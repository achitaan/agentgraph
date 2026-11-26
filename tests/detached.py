"""Detached controllers that a test leaves behind (`looprun.start` without `wait=True` starts a real one).

On Windows a process that is still running keeps its files open (the run log, the lock, the check's output), so the test's temporary
folder cannot be deleted under it. `reap_detached(self)` records the processes the test starts and, when the test is over and before
its temporary folder is removed (call it right after the folder is made: cleanups run last in, first out), stops those still
running and waits for them. Elsewhere it does nothing: a running process holds no file there."""
import subprocess
import time
from unittest import mock

from agentgraph import osproc


def _below(pid) -> list:
    """The pids below `pid` now (none when the list cannot be read)."""
    try:
        return osproc.descendants(pid, osproc.list_processes(windows=True))
    except Exception:
        return []


def _stop_all(pids) -> None:
    """Stop what is left of these processes and wait for them to go."""
    for pid in pids:
        if osproc.is_alive(pid):
            try:
                osproc.kill_group(pid, True)
            except (ProcessLookupError, PermissionError):
                pass
    deadline = time.monotonic() + 10
    while any(osproc.is_alive(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(0.1)


def reap_detached(case) -> list:
    started: list = []
    if not osproc.is_windows():
        return started
    real = subprocess.Popen

    class Recording(real):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            started.append(self)

    patch = mock.patch.object(subprocess, "Popen", Recording)
    patch.start()

    def reap():
        patch.stop()
        family = []
        for proc in started:
            if proc.poll() is None:
                family += _below(proc.pid)  # a shell's children (MSYS `sh -c` runs a `bash`) do not end with it, and they keep the files open
                try:
                    osproc.stop_family(proc)
                except Exception:
                    pass
        for proc in started:
            try:
                proc.wait(15)
            except Exception:
                pass
        _stop_all(family)
        time.sleep(0.2)  # the system lets go of the files a moment after the process has gone

    case.addCleanup(reap)
    return started
