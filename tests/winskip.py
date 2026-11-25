"""Skip helpers for tests that need something native Windows lacks."""

import os
import signal
import tempfile
import unittest

WINDOWS = os.name == "nt"


def _can_symlink():
    with tempfile.TemporaryDirectory() as folder:
        try:
            os.symlink(os.path.join(folder, "target"), os.path.join(folder, "link"))
        except (OSError, NotImplementedError, AttributeError):
            return False
        return True


CAN_SYMLINK = _can_symlink()

posix_only = unittest.skipIf(WINDOWS, "needs POSIX (process groups, signals, modes or a pty)")
needs_symlinks = unittest.skipUnless(CAN_SYMLINK, "needs permission to create symlinks")
needs_fifo = unittest.skipUnless(hasattr(os, "mkfifo"), "needs os.mkfifo")
needs_fork = unittest.skipUnless(hasattr(os, "fork"), "needs os.fork")
needs_uid = unittest.skipUnless(hasattr(os, "getuid"), "needs os.getuid")
needs_sighup = unittest.skipUnless(hasattr(signal, "SIGHUP"), "needs signal.SIGHUP")
