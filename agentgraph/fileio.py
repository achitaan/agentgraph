"""Regular-file opens and exclusive file locks on POSIX and native Windows."""
import errno
import os
import stat
from contextlib import contextmanager, ExitStack
from pathlib import Path


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

    _advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    _security = _advapi.GetSecurityInfo
    _security.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                          ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
    _security.restype = wintypes.DWORD
    _token = _advapi.OpenProcessToken
    _token.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    _token.restype = wintypes.BOOL
    _tokeninfo = _advapi.GetTokenInformation
    _tokeninfo.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                           wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    _tokeninfo.restype = wintypes.BOOL
    _equal = _advapi.EqualSid
    _equal.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    _equal.restype = wintypes.BOOL
    _process = _kernel.GetCurrentProcess
    _process.restype = wintypes.HANDLE
    _free = _kernel.LocalFree
    _free.argtypes = (ctypes.c_void_p,)
    _free.restype = ctypes.c_void_p


def _owned(handle):
    """Compare the directory owner SID with the process user's SID."""
    owner = ctypes.c_void_p()
    security = ctypes.c_void_p()
    result = _security(handle, 1, 1, ctypes.byref(owner), None, None, None, ctypes.byref(security))
    if result:
        raise ctypes.WinError(result)
    try:
        token = wintypes.HANDLE()
        if not _token(_process(), 8, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            size = wintypes.DWORD()
            _tokeninfo(token, 1, None, 0, ctypes.byref(size))
            if not size.value:
                raise ctypes.WinError(ctypes.get_last_error())
            data = ctypes.create_string_buffer(size.value)
            if not _tokeninfo(token, 1, data, size, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            sid = ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0]
            return bool(owner.value and _equal(owner, sid))
        finally:
            _close(token)
    finally:
        _free(security)


@contextmanager
def windows_owned_directory(path):
    """Hold ordinary directory handles from the volume root to an owned directory.

    Deny delete sharing while held, preventing renames of the checked components.
    Refuse reparse points rather than traversing them.
    """
    if os.name != "nt":
        raise OSError("Windows directory guard is only available on Windows")
    path = Path(os.path.abspath(path))
    with ExitStack() as stack:
        for component in (*reversed(path.parents), path):
            # READ_CONTROL for owner inspection of the last component only.
            access = 0x80 | (0x20000 if component == path else 0)
            handle = _create(str(component), access, 3, None, 3, 0x02200000, None)
            if handle == ctypes.c_void_p(-1).value:
                error = ctypes.get_last_error()
                if error in (2, 3):
                    raise FileNotFoundError(errno.ENOENT, "directory not found", component)
                raise ctypes.WinError(error)
            stack.callback(_close, handle)
            info = _Attributes()
            if not _info(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not info.attributes & 0x10 or info.attributes & 0x400:
                raise OSError(errno.EINVAL, "not a plain directory", component)
        if not _owned(handle):
            raise PermissionError(errno.EACCES, "directory is not owned by the current user", path)
        yield path


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
