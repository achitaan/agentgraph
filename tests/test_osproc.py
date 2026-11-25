"""agentgraph.osproc and agentgraph.winconsole: the POSIX forms for real, the Windows forms through fakes (a fake runner that records
the command lines, a fake process API, a fake console, a fake msvcrt), so the Windows branches run on any machine."""
import json
import os
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

from agentgraph import __main__ as cli
from agentgraph import osproc, winconsole


class FakeRunner:
    """Stands in for subprocess.run: records every call and answers from a list (or a function)."""

    def __init__(self, *answers):
        self.calls = []
        self.answers = list(answers)

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        answer = self.answers.pop(0) if self.answers else subprocess.CompletedProcess(argv, 0, "", "")
        if isinstance(answer, BaseException):
            raise answer
        return answer


def done(returncode=0, stdout=""):
    return subprocess.CompletedProcess([], returncode, stdout, "")


def no_kill(*args):
    raise AssertionError(f"os.kill{args} must not be reached on Windows")


class PosixFormTests(unittest.TestCase):
    def test_group_and_detached_and_text_kwargs_are_todays(self):
        self.assertEqual(osproc.group_kwargs(windows=False), {"start_new_session": True})
        self.assertEqual(osproc.detached_kwargs(windows=False), {"start_new_session": True})
        self.assertEqual(osproc.text_kwargs(windows=False), {})
        self.assertEqual(osproc.base_env_names(windows=False), ())
        self.assertEqual(osproc.term_signals(windows=False), (signal.SIGTERM, signal.SIGHUP))

    def test_shell_command_is_sh_dash_c(self):
        self.assertEqual(osproc.shell_command("echo hi", windows=False), ["sh", "-c", "echo hi"])
        self.assertEqual(osproc.shell_command("echo hi", windows=False, posix_shell="/bin/sh"), ["/bin/sh", "-c", "echo hi"])
        self.assertEqual(osproc.shell_arguments("echo hi", windows=False), ("echo hi", True))

    def test_resolve_command_is_unchanged(self):
        self.assertEqual(osproc.resolve_command("opencode", windows=False, which=lambda n: "/x/" + n), "opencode")

    def test_kill_group_and_stop_tree_on_a_real_child(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], **osproc.group_kwargs())
        try:
            self.assertTrue(osproc.is_alive(proc.pid))
            self.assertTrue(osproc.stop_tree(proc.pid, grace=0.2) or proc.poll() is not None)
            proc.wait(timeout=10)
            self.assertEqual(proc.returncode, -signal.SIGTERM)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        with self.assertRaises(ProcessLookupError):
            osproc.kill_group(proc.pid, force=True)

    def test_kill_group_force_sends_sigkill(self):
        proc = subprocess.Popen([sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"],
                                **osproc.group_kwargs())
        try:
            time.sleep(0.3)
            osproc.kill_group(proc.pid, force=True)
            proc.wait(timeout=10)
            self.assertEqual(proc.returncode, -signal.SIGKILL)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_is_alive_of_a_dead_pid_and_of_this_process(self):
        self.assertTrue(osproc.is_alive(os.getpid()))
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        self.assertFalse(osproc.is_alive(proc.pid))

    def test_list_processes_sees_this_process(self):
        table = osproc.list_processes(["-axo", "pid=,ppid=,command="], ("pid", "ppid", "command"))
        mine = [row for row in table if row.pid == os.getpid()]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0].ppid, os.getppid())
        self.assertTrue(mine[0].command)

    def test_children_finds_a_grandchild(self):
        code = "import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); time.sleep(30)"
        proc = subprocess.Popen([sys.executable, "-c", code], **osproc.group_kwargs())
        try:
            deadline = time.monotonic() + 10
            found = []
            while time.monotonic() < deadline and not found:
                time.sleep(0.2)
                found = osproc.children(proc.pid)
            self.assertTrue(found)
        finally:
            for pid in [proc.pid, *osproc.children(proc.pid)]:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            proc.wait()

    def test_parse_ps_skips_bad_lines(self):
        text = "  10     1 /bin/sh -c x\nnot a number 3 y\n 11 10\n 12 10 python -m a b\n"
        rows = osproc.parse_ps(text)
        self.assertEqual([(r.pid, r.ppid, r.command) for r in rows], [(10, 1, "/bin/sh -c x"), (11, 10, ""), (12, 10, "python -m a b")])
        rows = osproc.parse_ps(" 5 hello world\n", ("pid", "command"))
        self.assertEqual([(r.pid, r.ppid, r.command) for r in rows], [(5, None, "hello world")])

    def test_descendants_walk_children_before_their_children(self):
        table = [osproc.Proc(2, 1, ""), osproc.Proc(3, 2, ""), osproc.Proc(4, 1, ""), osproc.Proc(5, 3, "")]
        self.assertEqual(sorted(osproc.descendants(1, table)), [2, 3, 4, 5])
        self.assertEqual(osproc.descendants(3, table), [5])

    def test_orphan_rule_on_posix(self):
        self.assertFalse(osproc.is_orphaned_leader(os.getpid(), os.getppid(), windows=False))
        self.assertFalse(osproc.is_orphaned_leader(2 ** 30, 1, windows=False))  # no such process: getpgid fails


class WindowsFormTests(unittest.TestCase):
    def setUp(self):
        patch = mock.patch("os.kill", side_effect=no_kill)
        patch.start()
        self.addCleanup(patch.stop)

    def test_kwargs(self):
        self.assertEqual(osproc.group_kwargs(windows=True), {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP
                                                              if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP") else 0x200})
        flags = osproc.detached_kwargs(windows=True)["creationflags"]
        self.assertTrue(flags & 0x200 and flags & 0x08000000)
        self.assertEqual(osproc.text_kwargs(windows=True), {"encoding": "utf-8"})
        self.assertIn("SYSTEMROOT", osproc.base_env_names(windows=True))
        self.assertNotIn(signal.SIGHUP if hasattr(signal, "SIGHUP") else None, osproc.term_signals(windows=True))

    def test_kill_group_command_lines(self):
        run = FakeRunner()
        osproc.kill_group(42, windows=True, runner=run)
        osproc.kill_group(42, True, windows=True, runner=run)
        self.assertEqual([c[0] for c in run.calls], [["taskkill", "/T", "/PID", "42"], ["taskkill", "/T", "/F", "/PID", "42"]])

    def test_kill_group_status_maps_to_the_two_errors_callers_ignore(self):
        with self.assertRaises(ProcessLookupError):
            osproc.kill_group(42, windows=True, runner=FakeRunner(done(128)))
        with self.assertRaises(PermissionError):
            osproc.kill_group(42, windows=True, runner=FakeRunner(done(1)))

    def test_stop_tree_is_polite_then_forced(self):
        run = FakeRunner()
        states = iter([True, True, True, False])  # alive after the polite step until the deadline, then gone after force
        clock = iter(range(0, 100))
        result = osproc.stop_tree(7, grace=2, windows=True, runner=run, alive=lambda pid: next(states),
                                  sleep=lambda s: None, clock=lambda: next(clock))
        self.assertTrue(result)
        self.assertEqual([c[0] for c in run.calls], [["taskkill", "/T", "/PID", "7"], ["taskkill", "/T", "/F", "/PID", "7"]])

    def test_stop_tree_stops_after_the_polite_step_when_it_worked(self):
        run = FakeRunner()
        self.assertTrue(osproc.stop_tree(7, windows=True, runner=run, alive=lambda pid: False, sleep=lambda s: None))
        self.assertEqual(len(run.calls), 1)

    def test_stop_family_uses_taskkill_tree_and_waits(self):
        run = FakeRunner()
        proc = mock.Mock(pid=9)
        proc.wait.side_effect = [subprocess.TimeoutExpired("x", 2), 0]
        osproc.stop_family(proc, windows=True, runner=run)
        self.assertEqual([c[0] for c in run.calls], [["taskkill", "/T", "/PID", "9"], ["taskkill", "/T", "/F", "/PID", "9"]])

    def test_list_processes_command_line(self):
        run = FakeRunner(done(0, json.dumps([{"ProcessId": 4, "ParentProcessId": 0, "CommandLine": None}])))
        rows = osproc.list_processes(windows=True, runner=run)
        argv, kwargs = run.calls[0]
        self.assertEqual(argv[:3], ["powershell", "-NoProfile", "-Command"])
        self.assertTrue(argv[3].endswith("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"))
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(rows, [osproc.Proc(4, 0, "")])

    def test_parse_one_object_a_list_nulls_and_garbage(self):
        one = osproc.parse_cim_json('{"ProcessId":5,"ParentProcessId":1,"CommandLine":"C:\\\\x.exe serve --service"}')
        self.assertEqual(one, [osproc.Proc(5, 1, "C:\\x.exe serve --service")])
        many = osproc.parse_cim_json('\ufeff[{"ProcessId":5,"ParentProcessId":null,"CommandLine":null},{"ProcessId":"x"},7,{"ProcessId":6,"ParentProcessId":5,"CommandLine":"a b"}]')
        self.assertEqual(many, [osproc.Proc(5, None, ""), osproc.Proc(6, 5, "a b")])
        self.assertEqual(osproc.parse_cim_json("[]"), [])
        for bad in ("", "not json", "123", '"text"', "null"):
            with self.assertRaises(osproc.ProcessListError, msg=bad):
                osproc.parse_cim_json(bad)
        self.assertTrue(issubclass(osproc.ProcessListError, OSError))

    def test_powershell_failure_is_an_error_not_an_empty_list(self):
        with self.assertRaises(OSError):
            osproc.list_processes(windows=True, runner=FakeRunner(done(1, "")))
        with self.assertRaises(OSError):
            osproc.list_processes(windows=True, runner=FakeRunner(done(0, "garbage")))

    def test_children_on_windows_walks_the_cim_table(self):
        rows = [{"ProcessId": 2, "ParentProcessId": 1, "CommandLine": "a"}, {"ProcessId": 3, "ParentProcessId": 2, "CommandLine": None},
                {"ProcessId": 9, "ParentProcessId": 8, "CommandLine": "z"}]
        self.assertEqual(osproc.children(1, windows=True, runner=FakeRunner(done(0, json.dumps(rows)))), [2, 3])

    def test_is_alive_never_reaches_os_kill(self):
        class Api:
            def __init__(self, handle, code, error=0):
                self.handle, self.code, self.error, self.closed = handle, code, error, []

            def open(self, pid):
                return self.handle

            def last_error(self):
                return self.error

            def exit_code(self, handle):
                return self.code

            def close(self, handle):
                self.closed.append(handle)

        running = Api(11, osproc.STILL_ACTIVE)
        self.assertTrue(osproc.is_alive(5, windows=True, api=running))
        self.assertEqual(running.closed, [11])
        self.assertFalse(osproc.is_alive(5, windows=True, api=Api(11, 0)))
        self.assertFalse(osproc.is_alive(5, windows=True, api=Api(0, None, error=87)))  # invalid parameter: no such process
        self.assertTrue(osproc.is_alive(5, windows=True, api=Api(0, None, error=osproc.ERROR_ACCESS_DENIED)))
        self.assertFalse(osproc.is_alive(0, windows=True, api=running))

    def test_is_alive_through_tasklist_when_there_is_no_api(self):
        csv = '"python.exe","1234","Console","1","10,000 K"\n'
        run = FakeRunner(done(0, csv), done(0, 'INFO: No tasks are running which match the specified criteria.\n'), OSError("no tasklist"))
        with mock.patch.object(osproc, "_load_kernel32", return_value=None):
            self.assertTrue(osproc.is_alive(1234, windows=True, runner=run))
            self.assertFalse(osproc.is_alive(1234, windows=True, runner=run))
            self.assertTrue(osproc.is_alive(1234, windows=True, runner=run))  # cannot tell: alive
        self.assertEqual(run.calls[0][0], ["tasklist", "/FI", "PID eq 1234", "/FO", "CSV", "/NH"])

    def test_shell_choice(self):
        self.assertEqual(osproc.shell_command("echo hi", windows=True, which=lambda n: "C:\\Git\\usr\\bin\\sh.exe"),
                         ["C:\\Git\\usr\\bin\\sh.exe", "-c", "echo hi"])
        self.assertEqual(osproc.shell_command("echo hi", windows=True, which=lambda n: None, comspec="C:\\Windows\\cmd.exe"),
                         'C:\\Windows\\cmd.exe /d /s /c "echo hi"')
        self.assertEqual(osproc.shell_arguments("echo hi", windows=True, which=lambda n: None, ), (osproc.shell_command(
            "echo hi", windows=True, which=lambda n: None), False))

    def test_resolve_command_uses_which_and_keeps_absolute_paths(self):
        self.assertEqual(osproc.resolve_command("npm", windows=True, which=lambda n: "C:\\n\\npm.cmd"), "C:\\n\\npm.cmd")
        self.assertEqual(osproc.resolve_command("npm", windows=True, which=lambda n: None), "npm")
        self.assertEqual(osproc.resolve_command("/abs/tool", windows=True, which=lambda n: "other"), "/abs/tool")

    def test_orphan_is_a_parent_that_is_gone(self):
        self.assertTrue(osproc.is_orphaned_leader(5, 77, windows=True, alive=lambda pid: False))
        self.assertFalse(osproc.is_orphaned_leader(5, 77, windows=True, alive=lambda pid: True))
        self.assertFalse(osproc.is_orphaned_leader(5, 0, windows=True, alive=lambda pid: False))

    def test_split_executable(self):
        self.assertEqual(osproc.split_executable('"C:\\Program Files\\o\\opencode.exe" serve --service'),
                         ("C:\\Program Files\\o\\opencode.exe", "serve --service"))
        self.assertEqual(osproc.split_executable("C:\\o\\opencode.exe serve --service"), ("C:\\o\\opencode.exe", "serve --service"))
        self.assertIsNone(osproc.split_executable(""))
        self.assertIsNone(osproc.split_executable('"unterminated serve'))


class WindowsCallerTests(unittest.TestCase):
    def test_sidechat_service_executables_on_windows(self):
        from agentgraph import sidechat
        run = FakeRunner(done(0, json.dumps([
            {"ProcessId": 1, "ParentProcessId": 0, "CommandLine": None},
            {"ProcessId": 2, "ParentProcessId": 1, "CommandLine": "/opt/opencode serve --service"},
            {"ProcessId": 3, "ParentProcessId": 1, "CommandLine": "sh -c \"/opt/opencode serve --service\""},
        ])))
        self.assertEqual(sidechat.service_executables(run=run, windows=True), ["/opt/opencode"])
        self.assertIsNone(sidechat.service_executables(run=FakeRunner(done(1)), windows=True))
        self.assertEqual(sidechat.service_executables(run=FakeRunner(done(0, "[]")), windows=True), [])

    def test_bench_sandbox_note_is_windows_only(self):
        from agentgraph import bench
        self.assertIn("macOS-only", bench.sandbox_note(True, windows=True))
        self.assertIsNone(bench.sandbox_note(False, windows=True))
        self.assertIsNone(bench.sandbox_note(True, windows=False))

    def test_reviewcopy_environment_on_windows(self):
        from agentgraph import reviewcopy
        source = {"PATH": "C:\\bin", "SYSTEMROOT": "C:\\Windows", "USERPROFILE": "C:\\Users\\a", "API_KEY": "x", "OTHER": "y"}
        env = reviewcopy.reduced_environment("C:\\tmp\\w", source, windows=True)
        self.assertEqual(env["PATH"], "C:\\bin")
        self.assertEqual(env["SYSTEMROOT"], "C:\\Windows")
        self.assertEqual((env["TEMP"], env["TMP"], env["TMPDIR"]), ("C:\\tmp\\w",) * 3)
        self.assertNotIn("API_KEY", env)
        self.assertNotIn("OTHER", env)
        posix = reviewcopy.reduced_environment("/tmp/w", {"PATH": "/x", "SYSTEMROOT": "s"}, windows=False)
        self.assertEqual(posix, {"PATH": "/x", "TMPDIR": "/tmp/w"})


class FakeConsole:
    def __init__(self, mode=0x0003, page=437, vt_works=True):
        self.mode, self.page, self.vt_works = mode, page, vt_works
        self.log = []

    def std_handle(self, which):
        self.log.append(("handle", which))
        return 7

    def get_mode(self, handle):
        return self.mode

    def set_mode(self, handle, mode):
        self.log.append(("set_mode", mode))
        if self.vt_works:
            self.mode = mode
        return self.vt_works

    def get_code_page(self):
        return self.page

    def set_code_page(self, page):
        self.log.append(("set_page", page))
        self.page = page
        return True


class FakeMsvcrt:
    def __init__(self, text):
        self.queue = list(text)

    def kbhit(self):
        return bool(self.queue)

    def getwch(self):
        return self.queue.pop(0)


class WinConsoleTests(unittest.TestCase):
    def test_vt_and_utf8_are_switched_on_and_put_back(self):
        console = FakeConsole()
        state = winconsole.set_utf8_output(console, winconsole.enable_vt(console))
        self.assertEqual(console.mode, 0x0003 | 0x0004)
        self.assertEqual(console.page, 65001)
        winconsole.restore(console, state)
        self.assertEqual((console.mode, console.page), (0x0003, 437))
        winconsole.restore(console, state)  # twice is fine
        self.assertEqual(console.log.count(("set_mode", 0x0003)), 1)

    def test_nothing_to_change_when_already_on_or_when_it_fails(self):
        console = FakeConsole(mode=0x0007, page=65001)
        state = winconsole.set_utf8_output(console, winconsole.enable_vt(console))
        winconsole.restore(console, state)
        self.assertFalse([entry for entry in console.log if entry[0].startswith("set_")])
        failing = FakeConsole(vt_works=False)
        state = winconsole.enable_vt(failing)
        self.assertFalse(state.changed_mode)
        winconsole.enable_vt(None)

    def test_keys(self):
        self.assertEqual(winconsole.key_bytes(FakeMsvcrt("q")), b"q")
        self.assertEqual(winconsole.key_bytes(FakeMsvcrt("\xe0H\xe0P\xe0K\xe0M")), b"\x1b[A\x1b[B\x1b[D\x1b[C")
        self.assertEqual(winconsole.key_bytes(FakeMsvcrt("\x00H")), b"\x1b[A")
        self.assertEqual(winconsole.key_bytes(FakeMsvcrt("\x00\x0f")), b"\x1b[Z")
        self.assertEqual(winconsole.key_bytes(FakeMsvcrt("\xe0I\xe0Q\xe0G\xe0O")), b"\x1b[5~\x1b[6~\x1b[H\x1b[F")
        self.assertEqual(winconsole.key_bytes(FakeMsvcrt("\xe0\x99x")), b"x")  # an unknown two-step key is dropped
        self.assertEqual(winconsole.key_bytes(FakeMsvcrt("é")), "é".encode("utf-8"))
        self.assertEqual(winconsole.key_bytes(FakeMsvcrt("")), b"")

    def test_wait_for_keys_times_out_and_returns_input(self):
        ticks = iter([0.0, 0.5, 1.0, 1.5])
        self.assertIsNone(winconsole.wait_for_keys(FakeMsvcrt(""), 1.0, clock=lambda: next(ticks), sleep=lambda s: None))
        self.assertEqual(winconsole.wait_for_keys(FakeMsvcrt("j"), 1.0, clock=lambda: 0.0, sleep=lambda s: None), b"j")

    def test_utf8_streams_reconfigures_only_what_needs_it(self):
        class Stream:
            def __init__(self, encoding):
                self.encoding, self.calls = encoding, []

            def reconfigure(self, **kw):
                self.calls.append(kw)

        old, new = Stream("cp1252"), Stream("utf-8")
        winconsole.utf8_streams([old, new, object()])
        self.assertEqual(old.calls, [{"encoding": "utf-8", "errors": "replace"}])
        self.assertEqual(new.calls, [])


class WindowsTerminalTests(unittest.TestCase):
    def test_open_read_keys_close_on_a_pipe(self):
        read_end, write_end = os.pipe()
        console = FakeConsole()
        msvcrt = FakeMsvcrt("\xe0Hj")
        term = cli._Terminal(0, write_end, windows=True, console=console, msvcrt=msvcrt)
        try:
            term.open()
            self.assertEqual(console.mode & 0x0004, 0x0004)
            self.assertEqual(console.page, 65001)
            keys = term.read_keys(time.monotonic() + 1)
            self.assertTrue(keys)
            self.assertIsNone(term.read_keys(time.monotonic() + 0.05))
        finally:
            term.close()
            os.close(write_end)
        data = os.read(read_end, 65536)
        os.close(read_end)
        self.assertTrue(data.startswith(cli.ENTER_SCREEN if isinstance(cli.ENTER_SCREEN, bytes) else cli.ENTER_SCREEN.encode()))
        self.assertTrue(data.endswith(cli.LEAVE_SCREEN if isinstance(cli.LEAVE_SCREEN, bytes) else cli.LEAVE_SCREEN.encode()))
        self.assertEqual((console.mode, console.page), (0x0003, 437))

    def test_no_msvcrt_means_no_keys_but_the_view_still_draws(self):
        read_end, write_end = os.pipe()
        term = cli._Terminal(0, write_end, windows=True, console=FakeConsole(), msvcrt=None)
        with mock.patch.object(winconsole, "load_msvcrt", return_value=None):
            try:
                term.open()
                self.assertIsNone(term.in_fd)
            finally:
                term.close()
                os.close(write_end)
                os.close(read_end)


if __name__ == "__main__":
    unittest.main()
