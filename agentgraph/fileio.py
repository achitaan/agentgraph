"""Who owns a folder, on POSIX and on native Windows.

Reading and writing files, locks and links are `osfs`'s job. What `osfs` cannot say is "is this folder mine":
on Windows `st_uid` is always 0, so a check with it passes for every folder. Here the Windows answer comes
from the folder's owner SID.
"""
import os

from . import osfs

WINDOWS = os.name == "nt"  # read at call time by the callers, so a test can switch the Windows branch on

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _create = _kernel.CreateFileW
    _create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    _create.restype = wintypes.HANDLE
    _close = _kernel.CloseHandle
    _close.argtypes = (wintypes.HANDLE,)
    _close.restype = wintypes.BOOL
    _process = _kernel.GetCurrentProcess
    _process.restype = wintypes.HANDLE
    _free = _kernel.LocalFree
    _free.argtypes = (ctypes.c_void_p,)
    _free.restype = ctypes.c_void_p

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

    _TOKEN_USER = 1   # TOKEN_INFORMATION_CLASS: the account running this process
    _TOKEN_OWNER = 4  # ... and the owner new objects get (the Administrators group in an elevated shell)


def _token_sid(token, kind):
    """The first SID in a token's TOKEN_USER or TOKEN_OWNER record (both start with the SID pointer)."""
    size = wintypes.DWORD()
    _tokeninfo(token, kind, None, 0, ctypes.byref(size))
    if not size.value:
        raise ctypes.WinError(ctypes.get_last_error())
    data = ctypes.create_string_buffer(size.value)
    if not _tokeninfo(token, kind, data, size, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    return ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0], data


def _owned_on_windows(path):
    # READ_CONTROL (to read the owner), shared with everyone, open existing, BACKUP_SEMANTICS (to open a folder)
    handle = _create(os.fspath(path), 0x20000, 7, None, 3, 0x02000000, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        error = ctypes.get_last_error()
        if error in (2, 3):
            raise FileNotFoundError(2, "directory not found", os.fspath(path))
        raise ctypes.WinError(error)
    try:
        owner = ctypes.c_void_p()
        security = ctypes.c_void_p()
        result = _security(handle, 1, 1, ctypes.byref(owner), None, None, None, ctypes.byref(security))
        if result:
            raise ctypes.WinError(result)
        try:
            if not owner.value:
                return False
            token = wintypes.HANDLE()
            if not _token(_process(), 8, ctypes.byref(token)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                for kind in (_TOKEN_USER, _TOKEN_OWNER):
                    sid, keep = _token_sid(token, kind)
                    if _equal(owner, sid):
                        return True
                return False
            finally:
                _close(token)
        finally:
            _free(security)
    finally:
        _close(handle)


def owned_by_me(path):
    """Whether this folder or file belongs to the user running the process (the owner SID on Windows)."""
    if WINDOWS:
        return _owned_on_windows(path)
    return os.stat(path).st_uid == osfs.getuid()
