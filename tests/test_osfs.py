"""agentgraph.osfs: the file-safety layer, tested against both backends where a test applies to both.

The portable backend (what Windows uses) runs here on any system; the msvcrt byte lock is exercised through a
stand-in module built on fcntl's record locks (the real msvcrt call is the one thing only Windows can prove).
"""
import errno
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agentgraph import osfs

ROOT = Path(__file__).resolve().parent.parent
RDONLY_NOFOLLOW = os.O_RDONLY | osfs.O_NOFOLLOW
DIR_NOFOLLOW = os.O_RDONLY | osfs.O_DIRECTORY | osfs.O_NOFOLLOW
CREATE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | osfs.O_NOFOLLOW


def install_fake_msvcrt():
    """A stand-in for msvcrt.locking built on POSIX record locks: a byte lock at the descriptor's current position."""
    import fcntl

    def locking(fd, mode, nbytes):
        position = os.lseek(fd, 0, os.SEEK_CUR)
        locking.seen.append((mode, position, os.fstat(fd).st_size))
        try:
            if mode == 0:
                fcntl.lockf(fd, fcntl.LOCK_UN, nbytes, position, os.SEEK_SET)
            else:
                fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, nbytes, position, os.SEEK_SET)
        except OSError as exc:
            raise OSError(errno.EACCES, "locked") from exc

    locking.seen = []
    fake = SimpleNamespace(locking=locking, LK_NBLCK=2, LK_UNLCK=0)
    osfs._msvcrt = fake
    return fake


CHILD = """
import os, sys, time
if sys.argv[1] == "fake":
    from tests.test_osfs import install_fake_msvcrt
    install_fake_msvcrt()
from agentgraph import osfs
kind, path, action = sys.argv[2:5]
if kind == "dir":
    fd = osfs.open(path, os.O_RDONLY | osfs.O_DIRECTORY)
else:
    fd = osfs.open(path, os.O_RDWR | os.O_CREAT, 0o600)
if action == "hold":
    osfs.lock(fd)
    print("locked", flush=True)
    sys.stdin.readline()  # hold until the parent says so (or exits)
else:
    try:
        osfs.lock(fd, block=False)
    except BlockingIOError:
        print("busy")
    else:
        print("free")
osfs.close(fd)
"""


def child(mode, kind, path, action, **kwargs):
    env = {**os.environ, "AGENTGRAPH_OSFS": "portable" if mode in ("portable", "fake") else "native"}
    return subprocess.Popen([sys.executable, "-c", CHILD, mode, kind, str(path), action], cwd=ROOT, env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, **kwargs)


class Both:
    """Tests that hold for either backend; `backend` is set by the subclass."""
    backend = None
    mode = "native"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(setattr, osfs, "_msvcrt", osfs._msvcrt)

    def folder(self, name="d"):
        path = self.root / name
        path.mkdir(exist_ok=True)
        return path

    # -- no-follow

    def test_a_symlink_to_a_file_is_refused_with_eloop(self):
        (self.root / "real.txt").write_text("x")
        os.symlink(self.root / "real.txt", self.root / "link.txt")
        with self.assertRaises(OSError) as raised:
            self.backend.open(self.root / "link.txt", RDONLY_NOFOLLOW)
        self.assertEqual(raised.exception.errno, errno.ELOOP)
        fd = self.backend.open(self.root / "link.txt", os.O_RDONLY)  # followed when no-follow is not asked for
        self.backend.close(fd)

    def test_a_symlink_to_a_folder_is_refused_when_opening_a_folder(self):
        self.folder("real")
        os.symlink(self.root / "real", self.root / "link")
        with self.assertRaises(NotADirectoryError) as raised:
            self.backend.open(self.root / "link", DIR_NOFOLLOW)
        self.assertEqual(raised.exception.errno, errno.ENOTDIR)
        self.backend.close(self.backend.open(self.root / "link", os.O_RDONLY | osfs.O_DIRECTORY))

    def test_a_symlink_directory_component_is_refused_through_a_handle(self):
        real = self.folder("real")
        (real / "inner").mkdir()
        os.symlink(real / "inner", self.root / "swapped")
        root = self.backend.open(self.root, os.O_RDONLY | osfs.O_DIRECTORY)
        try:
            with self.assertRaises(NotADirectoryError):
                self.backend.open("swapped", DIR_NOFOLLOW, dir_fd=root)
            (real / "f").write_text("x")
            os.symlink(real / "f", self.root / "flink")
            with self.assertRaises(OSError) as raised:
                self.backend.open("flink", RDONLY_NOFOLLOW, dir_fd=root)
            self.assertEqual(raised.exception.errno, errno.ELOOP)
        finally:
            self.backend.close(root)

    def test_creating_over_a_symlink_is_eexist(self):
        os.symlink(self.root / "nowhere", self.root / "dangling")
        with self.assertRaises(FileExistsError):
            self.backend.open(self.root / "dangling", CREATE, 0o600)
        self.assertFalse((self.root / "nowhere").exists())

    def test_missing_names_keep_their_errors(self):
        with self.assertRaises(FileNotFoundError):
            self.backend.open(self.root / "absent", RDONLY_NOFOLLOW)
        with self.assertRaises(FileNotFoundError):
            self.backend.open(self.root / "absent", DIR_NOFOLLOW)
        (self.root / "file").write_text("x")
        with self.assertRaises(NotADirectoryError):
            self.backend.open(self.root / "file", DIR_NOFOLLOW)

    # -- bytes

    def test_bytes_round_trip_exactly(self):
        data = b"a\r\nb\nc\x1ad\r\n\x00end\r"
        fd = self.backend.open(self.root / "b.bin", CREATE, 0o644)
        os.write(fd, data)
        self.backend.fsync(fd)
        os.close(fd)
        fd = self.backend.open(self.root / "b.bin", RDONLY_NOFOLLOW | osfs.O_NONBLOCK)
        try:
            self.assertEqual(os.read(fd, 100), data)
        finally:
            os.close(fd)
        self.assertEqual((self.root / "b.bin").read_bytes(), data)

    # -- folder handles

    def test_a_handle_works_for_every_operation_and_lists(self):
        handle = self.backend.open(self.root, os.O_RDONLY | osfs.O_DIRECTORY)
        try:
            self.assertTrue(stat.S_ISDIR(self.backend.fstat(handle).st_mode))
            self.backend.mkdir("sub", 0o755, dir_fd=handle)
            self.assertTrue(stat.S_ISDIR(self.backend.lstat("sub", dir_fd=handle).st_mode))
            self.assertTrue(stat.S_ISDIR(self.backend.stat("sub", dir_fd=handle, follow_symlinks=False).st_mode))
            fd = self.backend.open("one.txt", CREATE, 0o644, dir_fd=handle)
            os.write(fd, b"one")
            os.close(fd)
            self.backend.fsync(handle)
            self.assertEqual(sorted(self.backend.listdir(handle)), ["one.txt", "sub"])
            self.backend.link("one.txt", "two.txt", src_dir_fd=handle, dst_dir_fd=handle, follow_symlinks=False)
            with self.assertRaises(FileExistsError):
                self.backend.link("one.txt", "two.txt", src_dir_fd=handle, dst_dir_fd=handle, follow_symlinks=False)
            self.backend.unlink("one.txt", dir_fd=handle)
            self.backend.rename("two.txt", "three.txt", src_dir_fd=handle, dst_dir_fd=handle)
            fd = self.backend.open("four.txt", CREATE, 0o644, dir_fd=handle)
            os.write(fd, b"four")
            os.close(fd)
            self.backend.replace("four.txt", "three.txt", src_dir_fd=handle, dst_dir_fd=handle)
            self.assertEqual((self.root / "three.txt").read_bytes(), b"four")
            fd = self.backend.open("five.txt", CREATE, 0o644, dir_fd=handle)
            os.close(fd)
            with self.assertRaises(FileExistsError):
                self.backend.rename_new("five.txt", "three.txt", src_dir_fd=handle, dst_dir_fd=handle)
            self.assertTrue((self.root / "five.txt").exists())
            self.backend.rename_new("five.txt", "six.txt", src_dir_fd=handle, dst_dir_fd=handle)
            self.assertEqual(sorted(self.backend.listdir(handle)), ["six.txt", "sub", "three.txt"])
            self.backend.rmdir("sub", dir_fd=handle)
            self.backend.unlink("six.txt", dir_fd=handle)
            self.assertEqual(self.backend.listdir(handle), ["three.txt"])
        finally:
            self.backend.close(handle)

    def test_a_handle_inside_a_handle(self):
        outer = self.backend.open(self.root, os.O_RDONLY | osfs.O_DIRECTORY)
        try:
            self.backend.mkdir("inner", 0o755, dir_fd=outer)
            inner = self.backend.open("inner", DIR_NOFOLLOW, dir_fd=outer)
            try:
                fd = self.backend.open("f", CREATE, 0o644, dir_fd=inner)
                os.close(fd)
                self.assertEqual(self.backend.listdir(inner), ["f"])
                self.assertTrue((self.root / "inner" / "f").exists())
            finally:
                self.backend.close(inner)
        finally:
            self.backend.close(outer)

    def test_a_closed_handle_is_ebadf_and_closing_twice_too(self):
        if self.backend is osfs.POSIX:
            self.skipTest("a closed descriptor number may be reused by the system; only tokens are tested")
        handle = self.backend.open(self.root, os.O_RDONLY | osfs.O_DIRECTORY)
        self.assertTrue(self.backend.is_handle(handle))
        self.assertGreaterEqual(handle, 1 << 30)  # never a real descriptor
        self.backend.close(handle)
        self.assertFalse(self.backend.is_handle(handle))
        for call in (lambda: self.backend.listdir(handle), lambda: self.backend.fstat(handle),
                     lambda: self.backend.fsync(handle), lambda: self.backend.lock(handle),
                     lambda: self.backend.open("x", CREATE, 0o600, dir_fd=handle),
                     lambda: self.backend.unlink("x", dir_fd=handle),
                     lambda: self.backend.lstat("x", dir_fd=handle), lambda: self.backend.close(handle)):
            with self.assertRaises(OSError) as raised:
                call()
            self.assertEqual(raised.exception.errno, errno.EBADF)

    def test_a_handle_to_a_folder_that_was_replaced_is_refused(self):
        if self.backend is osfs.POSIX:
            self.skipTest("a descriptor keeps the folder it was opened on")
        folder = self.folder("held")
        handle = self.backend.open(folder, os.O_RDONLY | osfs.O_DIRECTORY)
        try:
            folder.rename(self.root / "moved")
            with self.assertRaises(OSError):  # gone
                self.backend.listdir(handle)
            folder.mkdir()  # another folder at the same path
            with self.assertRaises(OSError) as raised:
                self.backend.open("f", CREATE, 0o600, dir_fd=handle)
            self.assertEqual(raised.exception.errno, errno.ESTALE)
            self.assertEqual(os.listdir(folder), [])
        finally:
            self.backend.close(handle)

    # -- replace

    def test_replace_is_atomic_for_a_reader(self):
        target = self.root / "t.txt"
        target.write_bytes(b"A" * 4096)
        stop = threading.Event()
        seen = set()

        def read():
            while not stop.is_set():
                try:
                    seen.add(target.read_bytes())
                except FileNotFoundError:
                    seen.add(None)

        reader = threading.Thread(target=read)
        reader.start()
        try:
            for number in range(150):
                staged = self.root / "staged"
                staged.write_bytes((b"B" if number % 2 else b"A") * 4096)
                self.backend.replace(staged, target)
        finally:
            stop.set()
            reader.join()
        self.assertTrue(seen <= {b"A" * 4096, b"B" * 4096}, "a reader saw a missing or partial file")

    # -- locks

    def _exclusion(self, kind, path):
        holder = waiting = child(self.mode, kind, path, "hold")
        try:
            self.assertEqual(holder.stdout.readline().strip(), "locked")
            tries = child(self.mode, kind, path, "try")
            self.assertEqual(tries.communicate(timeout=60)[0].strip(), "busy")
            waiting = child(self.mode, kind, path, "hold")
            time.sleep(0.3)
            self.assertIsNone(waiting.poll())  # blocked behind the holder
            holder.stdin.write("go\n")
            holder.stdin.flush()
            self.assertEqual(waiting.stdout.readline().strip(), "locked")  # got it once the first one let go
            waiting.stdin.write("go\n")
            waiting.stdin.flush()
            self.assertEqual(waiting.wait(timeout=60), 0)
            self.assertEqual(holder.wait(timeout=60), 0)
        finally:
            for process in (holder, waiting):
                process.kill()
                process.wait()
                process.stdin.close()
                process.stdout.close()
        free = child(self.mode, kind, path, "try")
        self.assertEqual(free.communicate(timeout=60)[0].strip(), "free")

    def test_two_processes_exclude_each_other_on_a_file(self):
        self._exclusion("file", self.root / "lockfile")

    def test_two_processes_exclude_each_other_on_a_folder(self):
        self._exclusion("dir", self.folder())

    def test_a_lock_in_this_process_is_seen_by_another_and_dies_with_the_close(self):
        folder = self.folder()
        handle = self.backend.open(folder, os.O_RDONLY | osfs.O_DIRECTORY)
        self.backend.lock(handle)
        self.assertEqual(child(self.mode, "dir", folder, "try").communicate(timeout=60)[0].strip(), "busy")
        if self.mode != "fake":  # record locks (the stand-in) never conflict inside one process
            other = self.backend.open(folder, os.O_RDONLY | osfs.O_DIRECTORY)
            try:
                with self.assertRaises(BlockingIOError):
                    self.backend.lock(other, block=False)
            finally:
                self.backend.close(other)
        self.backend.close(handle)  # closing releases it
        self.assertEqual(child(self.mode, "dir", folder, "try").communicate(timeout=60)[0].strip(), "free")

    def test_unlock_releases(self):
        path = self.root / "lockfile"
        fd = self.backend.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            self.backend.lock(fd)
            self.assertEqual(child(self.mode, "file", path, "try").communicate(timeout=60)[0].strip(), "busy")
            self.backend.unlock(fd)
            self.assertEqual(child(self.mode, "file", path, "try").communicate(timeout=60)[0].strip(), "free")
        finally:
            self.backend.close(fd)

    def test_a_lock_on_a_file_never_covers_its_content(self):
        path = self.root / "data"
        path.write_bytes(b"hello")
        fd = self.backend.open(path, os.O_RDWR, 0o600)
        try:
            os.lseek(fd, 2, os.SEEK_SET)
            self.backend.lock(fd)
            self.assertEqual(os.lseek(fd, 0, os.SEEK_CUR), 2)  # the position is where it was
            self.assertEqual(path.read_bytes(), b"hello")
            self.backend.unlock(fd)
        finally:
            self.backend.close(fd)

    def test_owner_and_mode_calls(self):
        self.assertEqual(self.backend.getuid(), os.getuid())
        self.assertEqual(self.backend.geteuid(), os.geteuid())
        path = self.root / "m"
        path.write_text("x")
        self.backend.chmod(path, 0o640)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
        fd = self.backend.open(path, os.O_RDWR)
        try:
            self.backend.fchmod(fd, 0o600)
        finally:
            self.backend.close(fd)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


@unittest.skipIf(osfs.POSIX is None, "no POSIX backend on this system")
class PosixBackendTests(Both, unittest.TestCase):
    backend = osfs.POSIX
    mode = "native"

    def test_it_calls_the_os_functions_at_each_call(self):
        for name in ("open", "close", "fsync", "fstat", "stat", "lstat", "listdir", "unlink", "rmdir", "mkdir",
                     "rename", "replace", "link", "chmod", "fchmod", "getuid", "geteuid"):
            seen = []
            with mock.patch.object(os, name, side_effect=lambda *a, **k: seen.append(name)):
                getattr(osfs.POSIX, name)()
            self.assertEqual(seen, [name], name)


class PortableBackendTests(Both, unittest.TestCase):
    backend = osfs.PORTABLE
    mode = "portable"

    def test_binary_mode_is_always_added(self):
        seen = []
        real = os.open

        def record(path, flags, *rest, **kwargs):
            seen.append(flags)
            return real(path, flags & ~(1 << 26), *rest, **kwargs)

        with mock.patch.object(osfs, "O_BINARY", 1 << 26), mock.patch.object(osfs.os, "open", record):
            fd = self.backend.open(self.root / "f", os.O_WRONLY | os.O_CREAT, 0o600)
            os.close(fd)
        self.assertTrue(seen and all(flags & (1 << 26) for flags in seen))

    def test_the_no_follow_and_directory_flags_never_reach_the_system(self):
        seen = []
        real = os.open

        def record(path, flags, *rest, **kwargs):
            seen.append(flags)
            return real(path, flags, *rest, **kwargs)

        (self.root / "f").write_text("x")
        with mock.patch.object(osfs.os, "open", record):
            os.close(self.backend.open(self.root / "f", RDONLY_NOFOLLOW))
        self.assertTrue(seen and not any(flags & osfs.O_NOFOLLOW for flags in seen))

    def test_a_link_swapped_in_after_the_open_is_refused_and_the_descriptor_closed(self):
        (self.root / "a").write_text("a")
        (self.root / "b").write_text("b")
        real = os.lstat
        calls = []

        def swapping(path, **kwargs):
            calls.append(path)
            if len(calls) > 1:  # the check after the open sees another file there
                return real(self.root / "b")
            return real(path, **kwargs)

        opened = []
        real_open = os.open

        def tracking(*args, **kwargs):
            opened.append(real_open(*args, **kwargs))
            return opened[-1]

        with mock.patch.object(osfs.os, "lstat", swapping), mock.patch.object(osfs.os, "open", tracking):
            with self.assertRaises(OSError) as raised:
                self.backend.open(self.root / "a", RDONLY_NOFOLLOW)
        self.assertEqual(raised.exception.errno, errno.ELOOP)
        with self.assertRaises(OSError):
            os.fstat(opened[0])

    def test_a_reparse_point_counts_as_a_link_but_a_cloud_placeholder_does_not(self):
        plain = SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x20, st_reparse_tag=0)
        junction = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x410, st_reparse_tag=0xA0000003)
        cloud = SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x420, st_reparse_tag=0x9000701A)
        unknown = SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x420, st_reparse_tag=0)
        symlink = SimpleNamespace(st_mode=stat.S_IFLNK)
        self.assertEqual([osfs.is_link(x) for x in (plain, junction, cloud, unknown, symlink)],
                         [False, True, False, True, True])

    def test_a_reparse_point_is_refused_like_a_symlink(self):
        (self.root / "f").write_text("x")
        real = os.lstat

        def reparse(path, **kwargs):
            info = real(path, **kwargs)
            fake = mock.MagicMock(wraps=info)
            fake.st_mode, fake.st_file_attributes, fake.st_reparse_tag = info.st_mode, 0x420, 0xA0000003
            return fake

        with mock.patch.object(osfs.os, "lstat", reparse):
            with self.assertRaises(OSError) as raised:
                self.backend.open(self.root / "f", RDONLY_NOFOLLOW)
        self.assertEqual(raised.exception.errno, errno.ELOOP)

    def test_replace_retries_on_permission_error_a_bounded_number_of_times(self):
        (self.root / "src").write_text("new")
        (self.root / "dst").write_text("old")
        real = os.replace
        calls = []

        def busy(*args, **kwargs):
            calls.append(1)
            if len(calls) < 4:
                raise PermissionError(errno.EACCES, "in use")
            return real(*args, **kwargs)

        with mock.patch.object(osfs.os, "replace", busy), mock.patch.object(osfs, "_RETRY_DELAYS", (0, 0, 0, 0, 0)):
            self.backend.replace(self.root / "src", self.root / "dst")
        self.assertEqual(len(calls), 4)
        self.assertEqual((self.root / "dst").read_text(), "new")
        calls.clear()

        def forever(*args, **kwargs):
            calls.append(1)
            raise PermissionError(errno.EACCES, "in use")

        (self.root / "src").write_text("again")
        with mock.patch.object(osfs.os, "replace", forever), mock.patch.object(osfs, "_RETRY_DELAYS", (0, 0, 0)):
            with self.assertRaises(PermissionError):
                self.backend.replace(self.root / "src", self.root / "dst")
        self.assertEqual(len(calls), 4)  # the tries, and one last that raises
        self.assertEqual((self.root / "dst").read_text(), "new")

    def test_unlink_retries_too_but_other_errors_do_not(self):
        (self.root / "f").write_text("x")
        real = os.unlink
        calls = []

        def busy(*args, **kwargs):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError(errno.EACCES, "in use")
            return real(*args, **kwargs)

        with mock.patch.object(osfs.os, "unlink", busy), mock.patch.object(osfs, "_RETRY_DELAYS", (0, 0, 0)):
            self.backend.unlink(self.root / "f")
        self.assertEqual(len(calls), 3)
        self.assertFalse((self.root / "f").exists())
        with self.assertRaises(FileNotFoundError):  # not a PermissionError: no retry, the error as it is
            self.backend.unlink(self.root / "f")

    def test_without_hard_links_link_copies_and_still_refuses_an_existing_name(self):
        (self.root / "src").write_bytes(b"a\r\nb\x1a")
        with mock.patch.object(osfs.os, "link", side_effect=OSError(errno.EPERM, "no links here")):
            self.backend.link(self.root / "src", self.root / "dst")
            self.assertEqual((self.root / "dst").read_bytes(), b"a\r\nb\x1a")
            with self.assertRaises(FileExistsError):
                self.backend.link(self.root / "src", self.root / "dst")
        self.assertEqual((self.root / "src").read_bytes(), b"a\r\nb\x1a")

    def test_on_windows_chmod_and_fchmod_change_nothing(self):
        path = self.root / "m"
        path.write_text("x")
        path.chmod(0o640)
        with mock.patch.object(osfs, "_NT", True):
            self.backend.chmod(path, 0o444)
            fd = self.backend.open(path, os.O_RDWR)
            try:
                self.backend.fchmod(fd, 0o444)
            finally:
                self.backend.close(fd)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)

    def test_on_windows_a_name_that_means_something_else_is_refused_through_a_handle(self):
        handle = self.backend.open(self.root, os.O_RDONLY | osfs.O_DIRECTORY)
        try:
            with mock.patch.object(osfs, "_NT", True):
                for name in ("a:b", "nul.md", "x.", "COM1", "a*b"):
                    with self.assertRaises(OSError) as raised:
                        self.backend.open(name, CREATE, 0o600, dir_fd=handle)
                    self.assertEqual(raised.exception.errno, errno.EINVAL, name)
                os.close(self.backend.open("ok-name.md", CREATE, 0o600, dir_fd=handle))
        finally:
            self.backend.close(handle)
        self.assertEqual(os.listdir(self.root), ["ok-name.md"])

    def test_on_windows_dot_and_dotdot_are_still_allowed_through_a_handle(self):
        (self.root / "sub").mkdir()
        handle = self.backend.open(self.root, os.O_RDONLY | osfs.O_DIRECTORY)
        try:
            with mock.patch.object(osfs, "_NT", True):
                self.assertTrue(stat.S_ISDIR(self.backend.stat(".", dir_fd=handle).st_mode))
                self.assertTrue(stat.S_ISDIR(self.backend.stat("sub/..", dir_fd=handle).st_mode))
        finally:
            self.backend.close(handle)

    def test_unsafe_names(self):
        self.assertTrue(all(osfs.unsafe_name(n) for n in ("a:b", "CON", "con.txt", "Nul.md", "aux", "lpt3.x", "a?", "a ",
                                                         "x\x01")))
        self.assertFalse(any(osfs.unsafe_name(n) for n in ("agent.md", ".agent.md.ab12.tmp", "console", "communal.md",
                                                          "com10", "2026-11-25-skill-files")))


class FakeMsvcrtTests(PortableBackendTests):
    """The portable backend again, locking through the msvcrt code path (stand-in module, see the module note)."""
    mode = "fake"

    def setUp(self):
        super().setUp()
        self.fake = install_fake_msvcrt()

    def test_the_byte_lock_is_one_byte_far_beyond_the_content_and_the_position_comes_back(self):
        path = self.root / "data"
        path.write_bytes(b"hello")
        fd = self.backend.open(path, os.O_RDWR, 0o600)
        try:
            os.lseek(fd, 3, os.SEEK_SET)
            self.backend.lock(fd)
            self.backend.unlock(fd)
            self.assertEqual(os.lseek(fd, 0, os.SEEK_CUR), 3)
        finally:
            self.backend.close(fd)
        (lock_mode, lock_at, size), (unlock_mode, unlock_at, _) = self.fake.locking.seen
        self.assertEqual((lock_mode, unlock_mode), (2, 0))
        self.assertEqual(lock_at, unlock_at)
        self.assertGreater(lock_at, 1 << 20)
        self.assertGreater(lock_at, size)

    def test_a_blocking_lock_keeps_trying_where_lk_lock_would_give_up(self):
        calls = []

        def busy_then_free(fd, mode, nbytes):
            calls.append(1)
            if len(calls) < 6:
                raise OSError(errno.EACCES, "locked")

        osfs._msvcrt.locking = busy_then_free
        path = self.root / "data"
        fd = self.backend.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            self.backend.lock(fd)  # blocks through five refusals
            self.assertEqual(len(calls), 6)
            calls.clear()
            osfs._msvcrt.locking = mock.Mock(side_effect=OSError(errno.EACCES, "locked"))
            with self.assertRaises(BlockingIOError):
                self.backend.lock(fd, block=False)
        finally:
            self.backend.close(fd)


class SelectionTests(unittest.TestCase):
    def name(self, value):
        env = {**os.environ}
        env.pop("AGENTGRAPH_OSFS", None)
        if value is not None:
            env["AGENTGRAPH_OSFS"] = value
        return subprocess.run([sys.executable, "-c", "from agentgraph import osfs; print(osfs.backend_name)"],
                              cwd=ROOT, env=env, capture_output=True, text=True).stdout.strip()

    def test_the_system_decides_unless_the_environment_forces_the_portable_backend(self):
        self.assertEqual(self.name(None), "posix" if os.name != "nt" else "portable")
        self.assertEqual(self.name("portable"), "portable")
        self.assertEqual(self.name("anything else"), "posix" if os.name != "nt" else "portable")

    def test_the_module_level_names_follow_the_chosen_backend(self):
        for name in ("open", "close", "lock", "replace", "link", "listdir"):
            self.assertEqual(getattr(osfs, name), getattr(osfs.BACKEND, name))


if __name__ == "__main__":
    unittest.main()
