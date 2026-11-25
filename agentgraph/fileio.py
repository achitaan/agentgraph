"""Regular-file opens and exclusive file locks on POSIX and native Windows."""
import errno
import os
import stat


if os.name == "nt":
    import ctypes
    from ctypes import wintypes
    import msvcrt

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _create = _kernel.CreateFileW
    _create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    _create.restype = wintypes.HANDLE
    _info = _kernel.GetFileInformationByHandleEx
    _info.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    _info.restype = wintypes.BOOL
    _close = _kernel.CloseHandle
    _close.argtypes = (wintypes.HANDLE,)
    _close.restype = wintypes.BOOL

    class _Attributes(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]


def open_regular(path, flags, mode=0o600):
    """Open a regular file without following a final-component link or reparse point.

    Callers must independently establish that the parent directory is appropriate.
    The Windows implementation validates the opened handle, not a separate path lookup.
    """
    if os.name != "nt":
        fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, mode)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise OSError(errno.EINVAL, "not a regular file", path)
        return fd
    access = flags & (os.O_WRONLY | os.O_RDWR)
    desired = {os.O_RDONLY: 0x80000000, os.O_WRONLY: 0x40000000,
               os.O_RDWR: 0xC0000000}[access]
    if flags & os.O_TRUNC:
        raise ValueError("open_regular does not support truncation")
    disposition = 1 if flags & os.O_CREAT and flags & os.O_EXCL else 4 if flags & os.O_CREAT else 3
    # FILE_FLAG_OPEN_REPARSE_POINT prevents following a link at the leaf.
    handle = _create(os.fspath(path), desired, 7, None, disposition, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        if error in (2, 3):
            raise FileNotFoundError(errno.ENOENT, "file not found", path)
        if error in (80, 183):
            raise FileExistsError(errno.EEXIST, "file exists", path)
        raise ctypes.WinError(error)
    try:
        info = _Attributes()
        if not _info(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & (0x10 | 0x400):
            raise OSError(errno.EINVAL, "not a regular file or is a reparse point", path)
        fd = msvcrt.open_osfhandle(handle, access | os.O_BINARY)
        handle = None  # descriptor now owns the handle
        return fd
    finally:
        if handle is not None:
            _close(handle)


def lock_exclusive(fd):
    """Acquire an exclusive lock until fd is closed (including across processes)."""
    if os.name != "nt":
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)
    else:
        # The lock file is dedicated to locking; byte zero may lie beyond EOF.
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
