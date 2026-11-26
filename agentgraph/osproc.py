"""Process primitives, one POSIX form and one Windows form each. Standard library only.

Every function takes `windows=None` (meaning: this machine) and, where it runs a program, `runner=None` (meaning
`subprocess.run`, looked up when called). A test passes `windows=True` and a fake runner and sees the exact command lines
the Windows branch builds, on any machine. Only the functions marked "Windows only" below make calls that exist on
Windows alone (`ctypes.windll`); they are small and take the library handle as an argument.

On POSIX every function makes the calls the callers made before this module existed, with the same arguments:
`start_new_session=True`, `os.killpg`, `os.kill`, `ps`, `sh -c`.

    group_kwargs()         Popen keywords that start the child in a process group of its own
    detached_kwargs()      the same, for a child that must outlive its parent (a controller, a supervisor)
    text_kwargs()          Popen/run keywords that make text mode UTF-8 where the platform default is not
    shell_command(cmd)     the argument for Popen that runs a command string through a shell
    shell_arguments(cmd)   (args, shell) for `subprocess.run(args, shell=shell)`
    resolve_command(name)  a bare command name as a path CreateProcess can start (`.cmd` shims)
    kill_group(pid, force) one step of stopping a child's whole group / tree
    stop_tree(pid)         polite, wait, then force
    stop_family(proc)      stop a Popen child and everything below it
    Job(proc)              Windows: a job object holding a child and all it starts, so one call ends them all (no-op elsewhere)
    children(pid)          pids of every descendant
    is_alive(pid)          is there a running process with this pid
    is_orphaned_leader()   a leader of its own group whose parent is gone
    list_processes(...)    [Proc(pid, ppid, command)] for the machine
    term_signals()         the signals that ask a process to stop, on this platform
    base_env_names()       variable names a stripped-down child environment needs on Windows

The shell, where the command string comes from a person (a loop's check, a review-copy command): POSIX runs `sh -c`.
Windows runs `sh -c` when an `sh` is on PATH (the person put Git's or MSYS's there on purpose, and the commands were
written for it), and otherwise `cmd /d /s /c "..."`: plain commands (`pytest -q && ruff check .`) mean the same in both,
but quoting and `$VAR` do not, so a check that needs sh syntax needs an sh on PATH. Nothing is looked for outside PATH.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from typing import NamedTuple

CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

# `taskkill` exit status for "not found": the root is not there, or (with /T) a member of the tree was gone when it came to it, which says
# nothing about the root. Anything else that is not 0 is a refusal (access denied, or "only /F will do").
TASKKILL_NOT_FOUND = 128
TASKKILL_TRIES = 4  # one `kill_group` step asks taskkill this often while the root still runs
# GetExitCodeProcess answers this while the process is still running.
STILL_ACTIVE = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ACCESS_DENIED = 5

POWERSHELL_LIST = ("[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; "
                   "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress")

_BASE_ENV_NAMES = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA",
                   "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "TEMP", "TMP", "USERNAME",
                   "USERDOMAIN", "COMPUTERNAME", "OS", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS")


class ShellRefused(OSError):
    """A command string that cannot be run faithfully by the shell on hand. An OSError, so a caller that already turns "the command could
    not be started" into a failed check shows this message and never counts it as a pass."""


class Proc(NamedTuple):
    pid: int
    ppid: int | None
    command: str


class ProcessListError(OSError):
    """The process list could not be read or made no sense. An OSError, so callers that already treat a failed `ps` as "cannot be
    told" treat this the same way: never as "no process is running"."""


def is_windows() -> bool:
    return os.name == "nt"


def _win(windows) -> bool:
    return is_windows() if windows is None else bool(windows)


def _run(runner):
    return subprocess.run if runner is None else runner


# ---- starting children ----

def group_kwargs(windows=None) -> dict:
    """Popen keywords: the child leads a new process group (POSIX: a new session), so its whole tree can be stopped."""
    if _win(windows):
        return {"creationflags": CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def detached_kwargs(windows=None) -> dict:
    """Popen keywords for a child that outlives its parent and the terminal it was started from. Windows: its own group and a console
    of its own that has no window (so it ignores Ctrl-C and a closed terminal, and the programs it starts open no window of theirs)."""
    if _win(windows):
        return {"creationflags": CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW}
    return {"start_new_session": True}


def text_kwargs(windows=None) -> dict:
    """Keywords for text-mode Popen/run: nothing on POSIX (the locale decides, as before); UTF-8 on Windows, where it would be cp1252."""
    return {"encoding": "utf-8"} if _win(windows) else {}


def resolve_command(name: str, *, windows=None, which=None) -> str:
    """`name` as something Popen can start. POSIX: unchanged. Windows: the path `shutil.which` finds through PATH and PATHEXT, because
    CreateProcess appends only `.exe` and so does not find `opencode.cmd`, `npm.cmd` or `node.cmd` by their bare names."""
    if not _win(windows) or os.path.isabs(name):
        return name
    return (which or shutil.which)(name) or name


def shell_command(command: str, *, windows=None, which=None, posix_shell: str = "sh", comspec: str | None = None):
    """What to hand to Popen (without shell=True) to run a command string through a shell. POSIX: [posix_shell, "-c", command]. Windows:
    [<sh on PATH>, "-c", command] when there is one, else the string `<comspec> /d /s /c "<command>"` (a string, not a list: cmd strips
    the outer quotes itself and a list would be quoted the way CreateProcess programs read it, which cmd does not). A command with a
    newline on the cmd path raises ShellRefused (an OSError); the shell path is `comspec` quoted when it holds a space."""
    if not _win(windows):
        return [posix_shell, "-c", command]
    sh = (which or shutil.which)("sh")
    if sh:
        return [sh, "-c", command]
    if "\r" in command or "\n" in command:  # cmd runs only the first line of a multi-line string: the rest would be silently skipped
        raise ShellRefused("a multi-line command needs a sh on PATH (install Git for Windows) or one line joined with &&: "
                           "Windows cmd would run only its first line")
    shell = comspec or os.environ.get("COMSPEC") or "cmd.exe"
    if " " in shell and not shell.startswith('"'):
        shell = f'"{shell}"'
    return f'{shell} /d /s /c "{command}"'


def shell_arguments(command: str, *, windows=None, which=None):
    """(args, shell) for `subprocess.run(args, shell=shell)`. POSIX: (command, True), what `shell=True` always did. Windows: shell_command, False."""
    if not _win(windows):
        return command, True
    return shell_command(command, windows=True, which=which), False


def term_signals(windows=None) -> tuple:
    """The signals that ask a process to stop: SIGTERM and SIGHUP on POSIX; SIGTERM and SIGBREAK (Ctrl-Break) on Windows, which has no SIGHUP."""
    names = ("SIGTERM", "SIGBREAK") if _win(windows) else ("SIGTERM", "SIGHUP")
    return tuple(getattr(signal, name) for name in names if hasattr(signal, name))


def base_env_names(windows=None) -> tuple:
    """Names a stripped-down environment must keep on Windows for programs to start at all (nothing on POSIX)."""
    return _BASE_ENV_NAMES if _win(windows) else ()


# ---- stopping children ----

def kill_group(pid: int, force: bool = False, *, windows=None, runner=None, alive=None, sleep=time.sleep) -> None:
    """One step of stopping the group (tree) led by `pid`. POSIX: `os.killpg(pid, SIGKILL if force else SIGTERM)`, raising what it raises.
    Windows: `taskkill /T [/F] /PID pid`; a failure becomes ProcessLookupError (the root was not running) or PermissionError (refused, or
    still running after a few tries), so a caller that ignores those two for killpg ignores them here. Without /F, taskkill asks windowed
    programs to close and refuses a console program ("can only be terminated forcefully"); the forced step is the one that always works.
    taskkill answers 128 ("not found") also when only a MEMBER of the tree exited while it was walking the tree, and the root can have been
    stopped by that very call, so 128 is never taken at its word: the root itself is asked (`alive`, default `is_alive`, the process handle).
    A forced step's 128 with the root still running is tried again (a few tries, a short pause); a polite step is asked once (it cannot
    end a console program anyway, and the caller goes on to the forced step); with the root gone a 128 is a normal return when the root
    was running when this call began (the step ended it) and ProcessLookupError when it was not. A forced step that fails for another
    reason is tried once more too; when the second try finds the root gone it returns normally, which does not tell whether a member that
    refused to die is left (taskkill does not say which one)."""
    if not _win(windows):
        os.killpg(pid, signal.SIGKILL if force else signal.SIGTERM)
        return
    argv = ["taskkill", "/T", *(["/F"] if force else []), "/PID", str(int(pid))]
    check = alive or (lambda p: is_alive(p, windows=True, runner=runner))
    existed = check(pid)
    code = 0
    for attempt in range(TASKKILL_TRIES):
        code = _run(runner)(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        if code == 0:
            return
        if code == TASKKILL_NOT_FOUND and not check(pid):
            if existed or attempt:
                return  # the root was running and is not now: this step (or the one before it) stopped it
            raise ProcessLookupError(pid)
        if not force:
            tries = attempt + 1
            break  # a polite step that did not end it: asking again changes nothing, the forced step is next
        if attempt + 1 < TASKKILL_TRIES:
            sleep(0.2)  # a member of the tree exited while it was being stopped: look once more
    else:
        tries = TASKKILL_TRIES
    if code == TASKKILL_NOT_FOUND:
        raise PermissionError(f"taskkill could not stop {pid}: it still runs after {tries} {'try' if tries == 1 else 'tries'} (status {code})")
    raise PermissionError(f"taskkill refused to stop {pid} (status {code})")


def stop_tree(pid: int, *, grace: float = 2.0, windows=None, runner=None, alive=None, sleep=time.sleep, clock=time.monotonic) -> bool:
    """Stop the group led by `pid`: politely, wait up to `grace` seconds for it to go, then by force. True when nothing is left running."""
    check = alive or (lambda p: is_alive(p, windows=windows, runner=runner))
    for force in (False, True):
        try:
            kill_group(pid, force, windows=windows, runner=runner, alive=check, sleep=sleep)
        except (ProcessLookupError, PermissionError):
            pass
        deadline = clock() + grace
        while check(pid):
            if clock() >= deadline:
                break
            sleep(0.05)
        else:
            return True
    return not check(pid)


def stop_family(proc: subprocess.Popen, *, windows=None, runner=None, listing=None, alive=None) -> None:
    """Stop the process we started and its descendants (a standalone server is one), nothing else. POSIX: SIGTERM, then SIGKILL, to each
    pid found through `ps`, two seconds' wait on `proc` after each. Windows: `taskkill /T` then `taskkill /T /F` on proc.pid, which
    takes the tree with it, with the same waits."""
    if _win(windows):
        for force in (False, True):
            try:
                kill_group(proc.pid, force, windows=True, runner=runner, alive=alive)
            except ProcessLookupError:
                pass
            except PermissionError:
                if not force:
                    continue  # taskkill refused the polite step (a console program only ends by force): waiting two seconds would change nothing
            try:
                proc.wait(timeout=2)
                return
            except subprocess.TimeoutExpired:
                continue
        return
    family = [proc.pid, *(listing(proc.pid) if listing else children(proc.pid))]
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in family:
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            continue


PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001


class _JobApi:
    """Windows only: the few kernel32 calls a job object needs (`create()`, `open(pid)`, `assign(job, process)`, `terminate(job)`, `close(handle)`)."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self._lib = lib = ctypes.WinDLL("kernel32", use_last_error=True)
        lib.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        lib.CreateJobObjectW.restype = wintypes.HANDLE
        lib.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        lib.OpenProcess.restype = wintypes.HANDLE
        lib.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        lib.AssignProcessToJobObject.restype = wintypes.BOOL
        lib.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        lib.TerminateJobObject.restype = wintypes.BOOL
        lib.CloseHandle.argtypes = [wintypes.HANDLE]

    def create(self):
        return self._lib.CreateJobObjectW(None, None)

    def open(self, pid):
        return self._lib.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)

    def assign(self, job, process):
        return bool(self._lib.AssignProcessToJobObject(job, process))

    def terminate(self, job):
        return bool(self._lib.TerminateJobObject(job, 1))

    def close(self, handle):
        self._lib.CloseHandle(handle)


class Job:
    """Windows: a job object that holds one started program and every program it starts afterwards, MSYS ones too (`taskkill /T` follows parent ids
    and loses what an MSYS shell started). `adopt(proc)` right after Popen; `terminate()` ends them all at once and says whether it could; `close()`
    lets go of the handle (it does not end anything). Everywhere else, and where the job cannot be made, `adopt` and `terminate` do nothing and
    `terminate()` is False, so the caller falls back to `taskkill`. `api` is for tests."""

    def __init__(self, windows=None, api=None):
        self.api, self.handle, self.adopted = None, None, False
        if not _win(windows):
            return
        try:
            self.api = api if api is not None else _JobApi()
            self.handle = self.api.create() or None
        except (ImportError, AttributeError, OSError):
            self.api = self.handle = None

    def adopt(self, proc) -> bool:
        if not self.handle:
            return False
        process = None
        try:
            process = self.api.open(proc.pid)
            if process and self.api.assign(self.handle, process):
                self.adopted = True
                return True
        except OSError:
            pass
        finally:
            if process:
                self.api.close(process)
        self.close()  # nothing is in it: there is no job to end
        return False

    def terminate(self) -> bool:
        if not self.handle or not self.adopted:
            return False
        try:
            return bool(self.api.terminate(self.handle))
        except OSError:
            return False

    def close(self) -> None:
        handle, self.handle = self.handle, None
        if handle:
            try:
                self.api.close(handle)
            except OSError:
                pass


def descendants(pid: int, table: list[Proc]) -> list[int]:
    """Every pid below `pid` in a process table, children before their own children, as the `ps` walk always listed them."""
    below: dict[int, list[int]] = {}
    for row in table:
        if row.ppid is not None:
            below.setdefault(row.ppid, []).append(row.pid)
    found, stack = [], [pid]
    while stack:
        for child in below.get(stack.pop(), []):
            found.append(child)
            stack.append(child)
    return found


def children(pid: int, *, windows=None, runner=None) -> list[int]:
    """The pids of every descendant of `pid`. POSIX: `ps -A -o pid=,ppid=` (no timeout, a failing `ps` raises, as before)."""
    if _win(windows):
        table = list_processes(windows=True, runner=runner, timeout=15)
    else:
        table = list_processes(["-A", "-o", "pid=,ppid="], ("pid", "ppid"), windows=False, runner=runner, timeout=None, check=False)
    return descendants(pid, table)


# ---- looking at processes ----

def is_alive(pid: int, *, windows=None, runner=None, api=None) -> bool:
    """Is a process with this pid running? POSIX: `os.kill(pid, 0)`. Windows never reaches os.kill (signal 0 there is CTRL_C_EVENT): it
    asks the process handle (`OpenProcess` + `GetExitCodeProcess` through `api`) and, when ctypes cannot give a library, `tasklist`."""
    if not _win(windows):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    if pid is None or int(pid) <= 0:
        return False
    library = api if api is not None else _load_kernel32()
    if library is not None:
        return _handle_running(library, int(pid))
    return _tasklist_running(int(pid), runner)


def _handle_running(api, pid: int) -> bool:
    """Plain Python around a process API (`open(pid)`, `last_error()`, `exit_code(handle)`, `close(handle)`), so a fake one exercises it anywhere."""
    handle = api.open(pid)
    if not handle:
        return api.last_error() == ERROR_ACCESS_DENIED  # there, but not ours to ask
    try:
        code = api.exit_code(handle)
        return code is None or code == STILL_ACTIVE
    finally:
        api.close(handle)


def _tasklist_running(pid: int, runner) -> bool:
    try:
        done = _run(runner)(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=15)
    except (OSError, subprocess.SubprocessError):
        return True  # cannot tell: the safe answer for a caller that is about to leave a process alone is "alive"
    for line in (done.stdout or "").splitlines():
        cells = [cell.strip('"') for cell in line.strip().split('","')]
        if len(cells) > 1 and cells[1].strip('"') == str(pid):
            return True
    return False


class _Kernel32:
    """Windows only: the few kernel32 calls the alive check needs, with their argument types set."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self._ctypes, self._wintypes = ctypes, wintypes
        self._lib = ctypes.WinDLL("kernel32", use_last_error=True)
        self._lib.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._lib.OpenProcess.restype = wintypes.HANDLE
        self._lib.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self._lib.GetExitCodeProcess.restype = wintypes.BOOL
        self._lib.CloseHandle.argtypes = [wintypes.HANDLE]

    def open(self, pid):
        return self._lib.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)

    def last_error(self):
        return self._ctypes.get_last_error()

    def exit_code(self, handle):
        code = self._wintypes.DWORD(0)
        if not self._lib.GetExitCodeProcess(handle, self._ctypes.byref(code)):
            return None
        return code.value

    def close(self, handle):
        self._lib.CloseHandle(handle)


def _load_kernel32():
    """The process API, or None where ctypes has no Windows library (then `tasklist` answers)."""
    try:
        return _Kernel32()
    except (ImportError, AttributeError, OSError):
        return None


def list_processes(posix_args=None, fields=("pid", "ppid", "command"), *, windows=None, runner=None, timeout: float | None = 15,
                   check: bool = True) -> list[Proc]:
    """The processes of the machine as Proc(pid, ppid, command). POSIX: `ps <posix_args>` (the caller's own argument list, so each caller
    keeps the exact call it made before), whose columns are named by `fields` (a leading run of "pid" and "ppid" and then, optionally,
    "command"); a line whose number columns are not numbers is skipped, a missing column is None or "". Windows: PowerShell's
    Get-CimInstance Win32_Process as JSON. Raises ProcessListError (an OSError) when the list cannot be read; other OSError and
    SubprocessError pass through. Never answers an empty list for output it could not read."""
    if _win(windows):
        argv = ["powershell", "-NoProfile", "-Command", POWERSHELL_LIST]
        done = _run(runner)(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)
        if getattr(done, "returncode", 0) != 0:
            raise ProcessListError("the process list could not be read (powershell failed)")
        return parse_cim_json(done.stdout or "")
    kw: dict = {"capture_output": True, "text": True}
    if timeout is not None:
        kw["timeout"] = timeout
    if check:
        kw["check"] = True
    out = _run(runner)(["ps", *(posix_args or ["-axo", "pid=,ppid=,command="])], **kw).stdout
    return parse_ps(out, fields)


def parse_ps(text: str, fields=("pid", "ppid", "command")) -> list[Proc]:
    numbers = [f for f in fields if f in ("pid", "ppid")]
    wants_command = "command" in fields
    rows = []
    for line in (text or "").splitlines():
        parts = line.split(None, len(numbers) if wants_command else len(numbers) - 1)
        if len(parts) < len(numbers) or not all(part.isdigit() for part in parts[:len(numbers)]):
            continue
        values = dict(zip(numbers, (int(part) for part in parts[:len(numbers)])))
        command = parts[len(numbers)] if wants_command and len(parts) > len(numbers) else ""
        if not wants_command and len(parts) != len(numbers):
            continue
        rows.append(Proc(values["pid"], values.get("ppid"), command))
    return rows


def parse_cim_json(text: str) -> list[Proc]:
    """The JSON PowerShell prints for `Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress`: one object when
    one process matched, a list otherwise; CommandLine may be null (system processes). Entries that make no sense are dropped; text that
    is not that JSON at all raises ProcessListError."""
    text = (text or "").lstrip("\ufeff").strip()
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ProcessListError("the process list was not JSON") from exc
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ProcessListError("the process list was not a list")
    rows = []
    for item in data:
        if not isinstance(item, dict):
            continue
        pid, ppid, command = item.get("ProcessId"), item.get("ParentProcessId"), item.get("CommandLine")
        if isinstance(pid, bool) or not isinstance(pid, int):
            continue
        rows.append(Proc(pid, ppid if isinstance(ppid, int) and not isinstance(ppid, bool) else None, command if isinstance(command, str) else ""))
    return rows


def is_orphaned_leader(pid: int, parent: int, *, windows=None, alive=None) -> bool:
    """A process its starter left behind: it leads its own group (every child here is started that way) and nobody is its parent any more.
    POSIX: the parent is init (pid 1) and the pid is its own process group id. Windows has no re-parenting and no group id to read: the
    parent pid is still the old one, so it is an orphan when no process has that pid any more."""
    if not _win(windows):
        try:
            return parent == 1 and os.getpgid(pid) == pid
        except OSError:
            return False
    if not parent or parent <= 0:
        return False
    return not (alive or (lambda p: is_alive(p, windows=True)))(parent)


def split_executable(command_line: str) -> tuple[str, str] | None:
    """(executable, the rest) of a Windows command line, or None when it cannot be told: a path in double quotes, or a first word."""
    line = (command_line or "").strip()
    if not line:
        return None
    if line.startswith('"'):
        end = line.find('"', 1)
        if end < 0:
            return None
        return line[1:end], line[end + 1:].strip()
    head, _, rest = line.partition(" ")
    return head, rest.strip()
