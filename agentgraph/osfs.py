"""The file-safety layer: the calls the toolkit's writers need, with one behaviour on every system.

The writers open folders without following a symlink, work "inside" an opened folder (`dir_fd=`), lock a folder,
fsync it, and rename or link files into place. Windows has none of: opening a folder with `os.open`, `dir_fd`,
`O_NOFOLLOW`, `fcntl`, `fchmod`, a hard link on every file system. This module is the one place that knows.
Callers use `osfs.open(...)` where they had `os.open(...)` and so on; the names below are drop-in.

Two backends, same functions:

  POSIX     the calls themselves (`osfs.open is os.open`): the same system calls with the same flags as before.
  PORTABLE  plain Python that runs anywhere; used on Windows (`os.name == "nt"`) and wherever the environment
            variable AGENTGRAPH_OSFS=portable asks for it (how the whole test suite is run on a Mac against it).

The portable backend, in short:
  * A folder handle is a token (an int far above any real file descriptor) mapped to the folder's real path and
    its (device, inode) identity. Every `dir_fd=` operation takes the token, joins the name onto the path and
    first checks that the path is still the same folder (`OSError(ESTALE)` when it was replaced). `close`
    releases the token; a closed token is `OSError(EBADF)`, as a closed descriptor is.
  * No-follow is enforced by checks: before the open, the last component is lstat-ed and a symlink (on Windows,
    any reparse point except a cloud-files placeholder such as OneDrive's) is refused with the errno the POSIX
    open gives (ELOOP for a file; ENOTDIR when a folder was asked for); after the open, `fstat(fd)` must be the
    same file as `lstat(path)`. POSIX refuses only the last component, so the portable code checks only that.
  * `O_BINARY` is always added when it exists (no newline translation, no Ctrl-Z end of file).
  * A lock on a real file descriptor is a byte lock (msvcrt) on one byte far beyond the end of the file, since a
    Windows byte lock is mandatory and must never cover content; without msvcrt (the portable backend forced on a
    Mac) it is `fcntl.flock`. A lock on a folder token is a lock on a small file in a per-user lock folder, because
    Windows cannot lock a folder; closing the token releases it.
  * Replacing, renaming and deleting retry a few times on `PermissionError` (Windows refuses while another process
    has the file open, usually for a moment: a scanner, an indexer, a reader).

What is weaker on Windows, exactly:
  * a folder handle does not pin the folder: another process can move or delete it, and the next operation then
    fails with ESTALE or ENOENT (the check and the operation are not one atomic step, as they are with an fd);
  * the no-follow check and the open are two steps, so a link swapped in between is caught only by the
    after-the-open identity check, not refused by the system;
  * `fsync` of a folder is a no-op (the rename itself may be lost in a power cut; its content was synced);
  * `chmod` / `fchmod` do nothing (the real call would only set the read-only flag, which breaks replacing);
    a file's mode bits are not preserved; a Windows profile folder is already private to its user;
  * `getuid` / `geteuid` are 0, as `st_uid` is, so "owned by me" checks always pass.
"""
from __future__ import annotations

import errno
import hashlib
import itertools
import os
import stat as _stat
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None
try:  # Windows
    import msvcrt as _msvcrt
except ImportError:
    _msvcrt = None

_NT = os.name == "nt"

# Flags. Where the system lacks one, a private bit stands in for it: the portable backend strips it before the
# real open, and nothing else sees it.
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 1 << 24)
O_DIRECTORY = getattr(os, "O_DIRECTORY", 1 << 25)
O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)  # only keeps a FIFO from blocking an open; Windows has none
O_BINARY = getattr(os, "O_BINARY", 0)

_TOKEN_BASE = 1 << 30  # far above any real descriptor (the C runtime on Windows allows 8192)
_LOCK_OFFSET = 0x7FFFFFFE  # the one byte a Windows lock covers: beyond the end of any file this toolkit writes
_RETRY_DELAYS = (0.01, 0.02, 0.04, 0.08, 0.1, 0.1, 0.1)  # about half a second in all
_REPARSE = getattr(_stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_WINDOWS_FORBIDDEN = set('<>:"|?*')


def is_link(info):
    """Whether an lstat result is a symlink (on Windows also a junction or any other reparse point, but not a
    cloud-files placeholder: OneDrive's "online only" files are reparse points that are ordinary files)."""
    if _stat.S_ISLNK(info.st_mode):
        return True
    if getattr(info, "st_file_attributes", 0) & _REPARSE:
        return (getattr(info, "st_reparse_tag", 0) & 0xFFFF0FFF) != 0x9000001A
    return False


def _strerror(code):
    """The text of an error code; Windows has none for its ELOOP (WSAELOOP), so it gets one."""
    text = os.strerror(code)
    return "Too many levels of symbolic links" if code == errno.ELOOP and text.startswith("Unknown error") else text


def unsafe_name(name):
    """Whether a file name means something else on Windows: a drive or a data stream (`:`), a wildcard, a control
    character, a trailing dot or space, or a device name (`NUL`, `COM1`...) with or without an extension."""
    if any(c in _WINDOWS_FORBIDDEN or ord(c) < 32 for c in name):
        return True
    if name.endswith((".", " ")):
        return True
    stem = name.split(".", 1)[0].rstrip(" ").upper()
    return stem in {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}


# --- POSIX backend ----------------------------------------------------------------------------------


def _posix_lock(fd, *, shared=False, block=True):
    """flock the descriptor (a file or a folder); `block` False raises BlockingIOError instead of waiting."""
    how = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
    fcntl.flock(fd, how if block else how | fcntl.LOCK_NB)


def _posix_unlock(fd):
    fcntl.flock(fd, fcntl.LOCK_UN)


_LIBC = []  # [library or None], loaded on first use


def _libc():
    if not _LIBC:
        try:
            import ctypes
            import ctypes.util
            _LIBC.append(ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True))
        except (OSError, TypeError, ImportError):
            _LIBC.append(None)
    return _LIBC[0]


def _posix_rename_new(source, target, *, src_dir_fd=None, dst_dir_fd=None):
    """Rename `source` to `target`, refused (FileExistsError) when `target` exists, in one step. Never a hard link:
    OpenCode 2.3.2 on macOS did not see the later deletion of a file made with link() (the n84 proof)."""
    library = _libc()
    exclusive, flag = None, 0
    if library is not None:
        exclusive = getattr(library, "renameatx_np", None)  # macOS: RENAME_EXCL
        flag = 0x4
        if exclusive is None:
            exclusive, flag = getattr(library, "renameat2", None), 0x1  # Linux: RENAME_NOREPLACE
    if exclusive is None:
        try:
            os.lstat(target, dir_fd=dst_dir_fd)
        except FileNotFoundError:
            os.rename(source, target, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
            return
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), target)
    import ctypes
    here = -2 if sys.platform == "darwin" else -100  # AT_FDCWD
    result = exclusive(here if src_dir_fd is None else src_dir_fd, os.fsencode(source),
                       here if dst_dir_fd is None else dst_dir_fd, os.fsencode(target), flag)
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), target)


def _swap_function():
    """macOS renameatx_np(RENAME_SWAP) or Linux renameat2(RENAME_EXCHANGE), or None (Windows has no such call)."""
    library = _libc()
    if library is None:
        return None
    return getattr(library, "renameatx_np", None) or getattr(library, "renameat2", None)


def _posix_swap(folder, first, second):
    """Exchange two entries of the folder (a real descriptor) in one step; False when the system cannot."""
    function = _swap_function()
    if function is None:
        return False
    import ctypes
    if function(folder, os.fsencode(first), folder, os.fsencode(second), 0x2) != 0:
        error = ctypes.get_errno()
        if error in (errno.ENOTSUP, errno.EINVAL, errno.ENOSYS, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)):
            return False
        raise OSError(error, os.strerror(error), second)
    return True


def _no_handle(fd):
    return False


def _late(name):
    """The os function `name`, looked up at each call: the very same system call as `os.<name>`, but a test (or a
    caller) that replaces `os.<name>` still takes effect."""
    def call(*args, **kwargs):
        return getattr(os, name)(*args, **kwargs)
    call.__name__ = name
    return call


POSIX = None
if not _NT:
    POSIX = SimpleNamespace(
        name="posix", open=_late("open"), close=_late("close"), dup=_late("dup"), fsync=_late("fsync"), fstat=_late("fstat"),
        stat=_late("stat"), lstat=_late("lstat"), listdir=_late("listdir"), unlink=_late("unlink"),
        rmdir=_late("rmdir"), mkdir=_late("mkdir"), rename=_late("rename"), replace=_late("replace"),
        link=_late("link"), chmod=_late("chmod"), fchmod=_late("fchmod"), lock=_posix_lock, unlock=_posix_unlock,
        rename_new=_posix_rename_new, swap=_posix_swap, getuid=_late("getuid"), geteuid=_late("geteuid"), is_handle=_no_handle)


# --- the two Windows-only calls, kept tiny ------------------------------------------------------------


def _win_try_lock(fd):
    """Lock the byte at `_LOCK_OFFSET` of the file, without waiting; OSError when somebody holds it."""
    here = os.lseek(fd, 0, os.SEEK_CUR)
    os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)  # msvcrt.locking works at the current position
    try:
        _msvcrt.locking(fd, _msvcrt.LK_NBLCK, 1)
    finally:
        os.lseek(fd, here, os.SEEK_SET)


def _win_unlock(fd):
    here = os.lseek(fd, 0, os.SEEK_CUR)
    os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
    try:
        _msvcrt.locking(fd, _msvcrt.LK_UNLCK, 1)
    finally:
        os.lseek(fd, here, os.SEEK_SET)


# --- portable backend -------------------------------------------------------------------------------


_NO_LINKS = {code for code in (getattr(errno, name, None) for name in (
    "EPERM", "ENOTSUP", "EOPNOTSUPP", "ENOSYS", "EINVAL", "EMLINK", "EXDEV")) if code is not None}


def _dirs(src_dir, dst_dir):
    """The dir_fd keywords of a two-name call, only those that are set (Windows does not take them at all)."""
    extra = {}
    if src_dir is not None:
        extra["src_dir_fd"] = src_dir
    if dst_dir is not None:
        extra["dst_dir_fd"] = dst_dir
    return extra


def _retry(call):
    """Call, retrying a short while on PermissionError (Windows: the file is open elsewhere for a moment)."""
    for delay in _RETRY_DELAYS:
        try:
            return call()
        except PermissionError:
            time.sleep(delay)
    return call()


def _identity(info):
    return (info.st_dev, info.st_ino) if info.st_ino else None  # no inode number on some file systems


class _Folder:
    __slots__ = ("path", "ident", "lock_fd")

    def __init__(self, path, ident):
        self.path, self.ident, self.lock_fd = path, ident, None


def _private_folder(root):
    """Make `root` (a folder only this user may use) and check it is what it should be: a real folder, not a link,
    owned by this user and closed to everyone else. A folder somebody else made first in a shared place is refused."""
    os.makedirs(root, mode=0o700, exist_ok=True)
    info = os.lstat(root)
    if is_link(info) or not _stat.S_ISDIR(info.st_mode):
        raise PermissionError(errno.EACCES, "the lock folder is not a plain folder", root)
    owner = getattr(os, "getuid", None)
    if owner is not None and (info.st_uid != owner() or info.st_mode & 0o077):
        raise PermissionError(errno.EACCES, "the lock folder belongs to somebody else or is open to others", root)
    return root


def _lock_folder():
    """Where a folder's lock file lives. Every process of this user must find the same place, whatever TEMP or TMP
    it was given (the review copy runs commands with both changed): AGENTGRAPH_LOCKDIR when set; on Windows
    %LOCALAPPDATA%\\agentgraph\\locks (else the home folder, else the temp folder); elsewhere (the portable backend
    forced for tests) the user's runtime folder, else a folder named for the user id in the temp folder, which is
    checked to be this user's own and private."""
    root = os.environ.get("AGENTGRAPH_LOCKDIR")
    if root:
        os.makedirs(root, mode=0o700, exist_ok=True)
        return root
    if _NT:
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            home = os.path.expanduser("~")
            base = home if home and home != "~" else tempfile.gettempdir()
        root = os.path.join(base, "agentgraph", "locks")
        os.makedirs(root, exist_ok=True)  # a Windows profile folder is already private to its user
        return root
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isabs(runtime) and os.path.isdir(runtime):
        return _private_folder(os.path.join(runtime, "agentgraph-locks"))
    owner = getattr(os, "getuid", None)
    name = "agentgraph-locks-%d" % owner() if owner is not None else "agentgraph-locks"
    return _private_folder(os.path.join(tempfile.gettempdir(), name))


class _Portable:
    name = "portable"

    def __init__(self):
        self._table = {}
        self._guard = threading.Lock()
        self._numbers = itertools.count(_TOKEN_BASE)

    # -- handles

    def is_handle(self, fd):
        return fd in self._table

    def _folder(self, fd):
        folder = self._table.get(fd)
        if folder is None:
            raise OSError(errno.EBADF, "the folder handle is closed")
        return folder

    def _where(self, fd):
        """The folder's path, after checking that it is still the folder the handle was made for."""
        folder = self._folder(fd)
        stale = getattr(errno, "ESTALE", errno.EIO)
        try:
            info = os.stat(folder.path)
        except FileNotFoundError:
            raise OSError(stale, "the folder behind the handle is gone", folder.path) from None
        if not _stat.S_ISDIR(info.st_mode) or (folder.ident is not None and _identity(info) != folder.ident):
            raise OSError(stale, "the folder behind the handle was replaced", folder.path)
        return folder.path

    def _at(self, path, dir_fd):
        """(path to use, real dir_fd to pass on): a token's folder is joined on; a real descriptor goes through."""
        if dir_fd is None:
            return path, None  # as given (a Path stays a Path)
        path = os.fsdecode(os.fspath(path))
        if dir_fd not in self._table:
            if dir_fd >= _TOKEN_BASE:
                raise OSError(errno.EBADF, "the folder handle is closed")
            return path, dir_fd
        last = path.replace("\\", "/").rsplit("/", 1)[-1]  # not os.path.basename: ntpath reads `a:b` as a drive and `b`
        if _NT and unsafe_name(last) and last not in (".", "..") and not os.path.isabs(path):
            raise OSError(errno.EINVAL, "a name Windows would read as something else", path)
        return os.path.join(self._where(dir_fd), path), None

    def close(self, fd):
        with self._guard:
            folder = self._table.pop(fd, None)
        if folder is None:
            if fd >= _TOKEN_BASE:
                raise OSError(errno.EBADF, "the folder handle is closed")
            return os.close(fd)
        if folder.lock_fd is not None:
            os.close(folder.lock_fd)  # closing releases the lock
            folder.lock_fd = None

    def dup(self, fd):
        """Another handle for the same folder (a second one to close), or os.dup of a descriptor. The copy does
        not hold a lock the original holds."""
        folder = self._table.get(fd)
        if folder is None:
            if fd >= _TOKEN_BASE:
                raise OSError(errno.EBADF, "the folder handle is closed")
            return os.dup(fd)
        self._where(fd)
        with self._guard:
            token = next(self._numbers)
            self._table[token] = _Folder(folder.path, folder.ident)
        return token

    def _new_handle(self, path, nofollow=False):
        """A handle for the folder at `path`: its real path (links resolved) and identity, checked twice: the
        folder the path led to must be the folder at the real path (else ESTALE), and with `nofollow` the real path
        must not be a link or junction now (ELOOP; a link put in the place after the resolving is caught here).
        The folder is not held open: a later swap is caught by the same check on every operation, not prevented."""
        info = os.stat(path)
        if not _stat.S_ISDIR(info.st_mode):
            raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), path)
        real = os.path.realpath(path)
        folder = _Folder(real, _identity(info))
        after = os.lstat(real)
        if nofollow and is_link(after):
            raise OSError(errno.ELOOP, _strerror(errno.ELOOP), path)
        if folder.ident is not None and _identity(after) != folder.ident:
            raise OSError(getattr(errno, "ESTALE", errno.EIO), "the folder changed while it was opened", path)
        with self._guard:
            token = next(self._numbers)
            self._table[token] = folder
        return token

    # -- open

    def open(self, path, flags, mode=0o777, *, dir_fd=None):
        full, real_dir = self._at(path, dir_fd)
        wants_dir = bool(flags & O_DIRECTORY)
        nofollow = bool(flags & O_NOFOLLOW)
        creating = bool(flags & os.O_CREAT and flags & os.O_EXCL)  # an existing link is then EEXIST, as on POSIX
        if nofollow:
            try:
                before = os.lstat(full, dir_fd=real_dir)
            except FileNotFoundError:
                pass  # the open reports it, or makes it
            else:
                if is_link(before):
                    if creating:  # Windows would create the file the link points to: refuse before it does
                        raise FileExistsError(errno.EEXIST, "File exists", full)
                    code = errno.ENOTDIR if wants_dir else errno.ELOOP
                    raise OSError(code, _strerror(code), full)
        if wants_dir:
            if real_dir is not None:
                return os.open(full, flags, mode, dir_fd=real_dir)
            return self._new_handle(full, nofollow)
        fd = os.open(full, (flags & ~(O_NOFOLLOW | O_DIRECTORY)) | O_BINARY, mode, dir_fd=real_dir)
        if nofollow:
            try:
                after = os.lstat(full, dir_fd=real_dir)
                if is_link(after) or not os.path.samestat(os.fstat(fd), after):
                    raise OSError(errno.ELOOP, _strerror(errno.ELOOP), full)
            except BaseException:
                os.close(fd)
                raise
        return fd

    # -- what a descriptor takes

    def fsync(self, fd):
        if fd in self._table:
            self._where(fd)  # a folder's fsync does nothing here (see the module note)
            return
        os.fsync(fd)

    def fstat(self, fd):
        if fd in self._table:
            return os.stat(self._where(fd))
        return os.fstat(fd)

    def listdir(self, path="."):
        if isinstance(path, int):
            if path in self._table:
                return os.listdir(self._where(path))
            if path >= _TOKEN_BASE:
                raise OSError(errno.EBADF, "the folder handle is closed")
        return os.listdir(path)

    # -- what takes dir_fd

    def stat(self, path, *, dir_fd=None, follow_symlinks=True):
        full, real_dir = self._at(path, dir_fd)
        return os.stat(full, dir_fd=real_dir, follow_symlinks=follow_symlinks)

    def lstat(self, path, *, dir_fd=None):
        full, real_dir = self._at(path, dir_fd)
        return os.lstat(full, dir_fd=real_dir)

    def mkdir(self, path, mode=0o777, *, dir_fd=None):
        full, real_dir = self._at(path, dir_fd)
        os.mkdir(full, mode, dir_fd=real_dir)

    def rmdir(self, path, *, dir_fd=None):
        full, real_dir = self._at(path, dir_fd)
        _retry(lambda: os.rmdir(full, dir_fd=real_dir))

    def unlink(self, path, *, dir_fd=None):
        full, real_dir = self._at(path, dir_fd)
        _retry(lambda: os.unlink(full, dir_fd=real_dir))

    def rename(self, src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        """Plain rename. On Windows it refuses to replace an existing file (use `replace` to replace)."""
        a, a_dir = self._at(src, src_dir_fd)
        b, b_dir = self._at(dst, dst_dir_fd)
        _retry(lambda: os.rename(a, b, **_dirs(a_dir, b_dir)))

    def replace(self, src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        """Rename over an existing file in one step."""
        a, a_dir = self._at(src, src_dir_fd)
        b, b_dir = self._at(dst, dst_dir_fd)
        _retry(lambda: os.replace(a, b, **_dirs(a_dir, b_dir)))

    def rename_new(self, source, target, *, src_dir_fd=None, dst_dir_fd=None):
        a, a_dir = self._at(source, src_dir_fd)
        b, b_dir = self._at(target, dst_dir_fd)
        if _NT:  # rename onto an existing file fails there (FileExistsError), in one step
            return _retry(lambda: os.rename(a, b, **_dirs(a_dir, b_dir)))
        return _posix_rename_new(a, b, src_dir_fd=a_dir, dst_dir_fd=b_dir)

    def swap(self, folder, first, second):
        """Exchange two entries of a folder atomically; False where that cannot be done (Windows, or a file system
        without it): the caller then falls back to a checked replace. A folder handle is opened by its path for
        the call (not pinned), which only a system with the exchange call (not Windows) ever does."""
        if _NT or _swap_function() is None:
            return False
        if folder not in self._table:
            if folder >= _TOKEN_BASE:
                raise OSError(errno.EBADF, "the folder handle is closed")
            return _posix_swap(folder, first, second)
        fd = os.open(self._where(folder), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            return _posix_swap(fd, first, second)
        finally:
            os.close(fd)

    def link(self, src, dst, *, src_dir_fd=None, dst_dir_fd=None, follow_symlinks=True):
        """A hard link; FileExistsError when `dst` exists. Where the file system has no hard links (FAT, some
        network shares) `dst` is made by an exclusive create and a copy instead: its content then appears while
        it is written, not all at once."""
        a, a_dir = self._at(src, src_dir_fd)
        b, b_dir = self._at(dst, dst_dir_fd)
        extra = {} if _NT or follow_symlinks else {"follow_symlinks": False}
        try:
            os.link(a, b, **_dirs(a_dir, b_dir), **extra)
        except (FileExistsError, FileNotFoundError):
            raise
        except NotImplementedError:
            _copy_new(a, b, a_dir, b_dir)
        except OSError as exc:
            if exc.errno not in _NO_LINKS and getattr(exc, "winerror", None) != 1:
                raise  # a real failure (disk full, no permission), not "this file system has no hard links"
            _copy_new(a, b, a_dir, b_dir)

    # -- modes and owners

    def chmod(self, path, mode, *, dir_fd=None, follow_symlinks=True):
        if _NT:
            return  # would only toggle the read-only flag
        full, real_dir = self._at(path, dir_fd)
        os.chmod(full, mode, dir_fd=real_dir, follow_symlinks=follow_symlinks)

    def fchmod(self, fd, mode):
        if _NT or not hasattr(os, "fchmod") or fd in self._table:
            return
        os.fchmod(fd, mode)

    def getuid(self):
        return os.getuid() if hasattr(os, "getuid") else 0

    def geteuid(self):
        return os.geteuid() if hasattr(os, "geteuid") else 0

    # -- locks

    def lock(self, fd, *, shared=False, block=True):
        """Lock a file or a folder handle for this process; `block` False raises BlockingIOError instead of
        waiting. A shared lock is exclusive where the system has no shared byte lock (Windows)."""
        folder = self._table.get(fd)
        if folder is None:
            if fd >= _TOKEN_BASE:
                raise OSError(errno.EBADF, "the folder handle is closed")
            return self._lock_file(fd, shared, block)
        self._where(fd)
        if folder.lock_fd is not None:
            return  # this handle holds it already
        name = "%d-%d" % folder.ident if folder.ident else hashlib.sha256(os.path.normcase(folder.path).encode()).hexdigest()
        lock_fd = os.open(os.path.join(_lock_folder(), name + ".lock"), os.O_RDWR | os.O_CREAT | O_BINARY, 0o600)
        try:
            self._lock_file(lock_fd, shared, block)
        except BaseException:
            os.close(lock_fd)
            raise
        folder.lock_fd = lock_fd

    def unlock(self, fd):
        folder = self._table.get(fd)
        if folder is not None:
            if folder.lock_fd is not None:
                self._unlock_file(folder.lock_fd)
                os.close(folder.lock_fd)
                folder.lock_fd = None
            return
        self._unlock_file(fd)

    def _lock_file(self, fd, shared, block):
        if _msvcrt is not None:
            delay = 0.005
            while True:
                try:
                    _win_try_lock(fd)
                    return
                except OSError as exc:
                    if self._holds(fd):
                        return  # a repeat on the descriptor that holds it: flock accepts it, so does this
                    if not block:
                        raise BlockingIOError(errno.EAGAIN, "the lock is held by another process") from exc
                    time.sleep(delay)  # LK_LOCK would give up after about ten seconds; flock never does
                    delay = min(delay * 2, 0.1)
        if fcntl is None:
            raise OSError(errno.ENOSYS, "this system has no file locks")
        _posix_lock(fd, shared=shared, block=block)

    def _holds(self, fd):
        """Whether this very descriptor holds the byte lock already (a Windows lock is refused even to the handle
        that holds it, which would make a repeat wait forever). Only the holder can unlock it, so a successful
        unlock proves it; the lock is taken again at once. If another process wins that instant, the repeat waits
        its turn like a first lock. Nothing is remembered, so a descriptor closed without an unlock leaves no stale
        entry to skip a later real lock."""
        try:
            _win_unlock(fd)
        except OSError:
            return False
        try:
            _win_try_lock(fd)
        except OSError:
            return False
        return True

    def _unlock_file(self, fd):
        if _msvcrt is not None:
            _win_unlock(fd)
        elif fcntl is not None:
            _posix_unlock(fd)


def _copy_new(src, dst, src_dir, dst_dir):
    """The no-hard-link stand-in for link(): `dst` is made exclusively, then filled from `src`."""
    source = os.open(src, os.O_RDONLY | O_BINARY, dir_fd=src_dir)
    try:
        target = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_BINARY, 0o644, dir_fd=dst_dir)
        try:
            while True:
                chunk = os.read(source, 1 << 16)
                if not chunk:
                    break
                while chunk:
                    chunk = chunk[os.write(target, chunk):]
            os.fsync(target)
        except BaseException:
            os.close(target)
            target = None
            try:
                os.unlink(dst, dir_fd=dst_dir)
            except OSError:
                pass
            raise
        finally:
            if target is not None:
                os.close(target)
    finally:
        os.close(source)


PORTABLE = _Portable()


def _choose():
    forced = os.environ.get("AGENTGRAPH_OSFS", "").strip().lower()
    if forced == "portable" or POSIX is None:
        return PORTABLE
    return POSIX


BACKEND = _choose()
backend_name = BACKEND.name


def utf8_stdout(streams=None) -> bool:
    """Portable backend only (Windows): make stdout and stderr write UTF-8 and leave "\\n" alone, so text a command prints
    (a stored output, a graph's words, JSON with ensure_ascii=False) is the same bytes as on POSIX: no "\\r\\n" on a
    pipe or file, and no failure on a character the console or locale code page (cp1252) lacks. stderr keeps going
    on a character it cannot show (a backslash escape). The POSIX backend is left exactly as it is (returns False).
    A stream that cannot be reconfigured (replaced by a test, closed) is left alone."""
    if BACKEND.name != "portable":
        return False
    import sys
    for stream, errors in (streams if streams is not None else ((sys.stdout, "strict"), (sys.stderr, "backslashreplace"))):
        try:
            stream.reconfigure(encoding="utf-8", errors=errors, newline="\n")
        except (AttributeError, ValueError, OSError):
            pass
    return True

open = BACKEND.open  # (path, flags, mode=0o777, *, dir_fd=None) -> fd; a folder (O_DIRECTORY) gives a handle
dup = BACKEND.dup  # another descriptor or folder handle for the same thing
close = BACKEND.close  # a descriptor or a folder handle; releases a lock held through it
fsync = BACKEND.fsync  # a file; on a folder handle, nothing on Windows
fstat = BACKEND.fstat
stat = BACKEND.stat  # (path, *, dir_fd=None, follow_symlinks=True)
lstat = BACKEND.lstat  # (path, *, dir_fd=None)
listdir = BACKEND.listdir  # (path or folder handle)
unlink = BACKEND.unlink  # (path, *, dir_fd=None)
rmdir = BACKEND.rmdir
mkdir = BACKEND.mkdir  # (path, mode=0o777, *, dir_fd=None)
rename = BACKEND.rename  # (src, dst, *, src_dir_fd=None, dst_dir_fd=None); not over a file on Windows
replace = BACKEND.replace  # same arguments; replaces an existing file
swap = BACKEND.swap  # (folder, first, second) -> bool: atomic exchange of two names, False when not possible
CAN_SWAP = not _NT and _swap_function() is not None
rename_new = BACKEND.rename_new  # same arguments; FileExistsError when dst exists, never a hard link
link = BACKEND.link  # (src, dst, *, src_dir_fd=None, dst_dir_fd=None, follow_symlinks=True); copy where no links
chmod = BACKEND.chmod  # no-ops on Windows
fchmod = BACKEND.fchmod
lock = BACKEND.lock  # (fd_or_handle, *, shared=False, block=True); BlockingIOError when not blocking and held
unlock = BACKEND.unlock
getuid = BACKEND.getuid  # 0 on Windows, as st_uid is there
geteuid = BACKEND.geteuid
is_handle = BACKEND.is_handle  # whether an int is a folder handle (portable backend) rather than a descriptor
