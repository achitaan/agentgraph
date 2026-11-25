import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agentgraph import fileio, spend


class FileIOTests(unittest.TestCase):
    def test_regular_file_and_exclusive_create(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "file"
            fd = fileio.open_regular(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            with os.fdopen(fd, "wb") as stream:
                stream.write(b"hello")
            with self.assertRaises(FileExistsError):
                fileio.open_regular(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            with os.fdopen(fileio.open_regular(path, os.O_RDONLY), "rb") as stream:
                self.assertEqual(stream.read(), b"hello")
            with self.assertRaises(OSError):
                fileio.open_regular(Path(temp), os.O_RDONLY)

    def test_spend_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertTrue(spend.set_limit(50, project=temp)["changed"])
            self.assertEqual(spend.show(temp), {"limit": 50, "warn": 45})
            self.assertTrue(spend.remove(project=temp)["changed"])
            self.assertEqual(spend.show(temp), {"limit": None, "warn": None})

    def test_cross_process_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "lock"
            fd = fileio.open_regular(path, os.O_RDWR | os.O_CREAT)
            fileio.lock_exclusive(fd)
            code = ("import os,sys; from agentgraph.fileio import open_regular,lock_exclusive;"
                    "fd=open_regular(sys.argv[1],os.O_RDWR); lock_exclusive(fd);"
                    "print('locked',flush=True);os.close(fd)")
            child = subprocess.Popen([sys.executable, "-c", code, str(path)], stdout=subprocess.PIPE)
            try:
                with self.assertRaises(subprocess.TimeoutExpired):
                    child.wait(timeout=0.5)
            finally:
                os.close(fd)
                try:
                    stdout, _ = child.communicate(timeout=10)
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.wait()
            self.assertEqual(child.returncode, 0)
            self.assertEqual(stdout.strip(), b"locked")
