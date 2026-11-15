"""Owner-only files on Windows, the counterpart of mode 0600 plus an owner check.

`create_private(path)` creates a new file that is protected from its first byte: its
security descriptor names the current user as owner and grants only that user, SYSTEM and
Administrators, with nothing inherited. `problem(path)` is the read side: it reads the
file's native owner and DACL and returns "owner" or "access" when the file is not private,
else "". No dependency beyond ctypes. Callers use these only when `os.name == "nt"`.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

SYSTEM_SID = "S-1-5-18"
ADMINISTRATORS_SID = "S-1-5-32-544"
OWNER_RIGHTS_SID = "S-1-3-4"
# Rights an allow entry may not give anyone else: file data, extended and basic attributes
# (FILE_READ_DATA ... FILE_WRITE_ATTRIBUTES), DELETE_CHILD, DELETE, WRITE_DAC, WRITE_OWNER,
# ACCESS_SYSTEM_SECURITY, MAXIMUM_ALLOWED and the generic rights.
LOOSE_MASK = 0x3F | 0x40 | 0x80 | 0x100 | 0x10000 | 0x40000 | 0x80000 | 0x3000000 | 0xF0000000
_ALLOWED, _ALLOWED_CALLBACK = 0, 9
_DENIED_TYPES = (1, 6, 10, 12)


def _api():
    import ctypes
    from ctypes import wintypes
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [wintypes.LPVOID]
    kernel.LocalFree.restype = wintypes.LPVOID
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
                                           ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), wintypes.LPVOID]
    advapi.GetSecurityDescriptorDacl.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.BOOL),
                                                 ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.BOOL)]
    advapi.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                             wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID]
    pointer = ctypes.POINTER(wintypes.LPVOID)
    advapi.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD, pointer, pointer,
                                             pointer, pointer, pointer]
    advapi.GetAclInformation.argtypes = [wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD]
    advapi.GetAce.argtypes = [wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID)]
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), wintypes.LPVOID]
    return ctypes, wintypes, advapi, kernel


def _sid_text(ctypes, wintypes, advapi, kernel, sid) -> str:
    text = wintypes.LPWSTR()
    if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return text.value
    finally:
        kernel.LocalFree(text)


def current_user_sid() -> str:
    """The string SID of the account running this process."""
    ctypes, wintypes, advapi, kernel = _api()
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        # TOKEN_USER starts with the SID pointer.
        return _sid_text(ctypes, wintypes, advapi, kernel, ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0])
    finally:
        kernel.CloseHandle(token)


def private_sddl(user_sid: str | None = None, *, owner: bool = False) -> str:
    """Protected DACL: full control for the current user, SYSTEM and Administrators.
    `owner` also names the user as the owner, for a file created with this descriptor."""
    user = user_sid or current_user_sid()
    return (f"O:{user}" if owner else "") + f"D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;{user})"


def create_private(path, sddl: str | None = None) -> int:
    """Create a new file (never an existing one) protected from its first byte and return
    a write-only file descriptor. The default descriptor makes the current user the owner
    and gives only that user, SYSTEM and Administrators access; `sddl` names another one."""
    import msvcrt

    ctypes, wintypes, advapi, kernel = _api()

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("descriptor", wintypes.LPVOID), ("inherit", wintypes.BOOL)]

    descriptor = wintypes.LPVOID()
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl or private_sddl(owner=True), 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(SecurityAttributes),
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    try:
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
        # GENERIC_WRITE, no sharing, CREATE_NEW, FILE_ATTRIBUTE_NORMAL
        handle = create(str(path), 0x40000000, 0, ctypes.byref(attributes), 1, 0x80, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
        except Exception:
            kernel.CloseHandle(handle)
            raise
    finally:
        kernel.LocalFree(descriptor)


def protect(path, sddl: str | None = None) -> None:
    """Replace an existing file's or folder's DACL with a protected one (the private one,
    or `sddl`). Tests use it to build loose descriptors; Torque creates files with
    `create_private` instead, so no file is ever open before it is protected."""
    ctypes, wintypes, advapi, kernel = _api()
    descriptor = wintypes.LPVOID()
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl or private_sddl(), 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), wintypes.LPVOID()
        if not advapi.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl),
                                                ctypes.byref(defaulted)):
            raise ctypes.WinError(ctypes.get_last_error())
        # SE_FILE_OBJECT, DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION
        code = advapi.SetNamedSecurityInfoW(str(path), 1, 0x4 | 0x80000000, None, None, dacl, None)
        if code:
            raise ctypes.WinError(code)
    finally:
        kernel.LocalFree(descriptor)


def _sddl(path) -> str:
    """The file's owner and DACL as SDDL text, for messages and tests."""
    ctypes, wintypes, advapi, kernel = _api()
    descriptor, text = wintypes.LPVOID(), wintypes.LPWSTR()
    code = advapi.GetNamedSecurityInfoW(str(path), 1, 0x1 | 0x4, None, None, None, None, ctypes.byref(descriptor))
    if code:
        raise ctypes.WinError(code)
    try:
        if not advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                descriptor, 1, 0x1 | 0x4, ctypes.byref(text), None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return text.value
        finally:
            kernel.LocalFree(text)
    finally:
        kernel.LocalFree(descriptor)


def read_security(path) -> tuple[str, list[tuple[str, int, str]] | None]:
    """(owner SID, entries) from the file's native descriptor. `entries` is None for a null
    DACL (everyone has every right); otherwise one (kind, mask, sid) per entry, kind being
    "allow", "deny" or "other" (an entry type this reader does not understand)."""
    ctypes, wintypes, advapi, kernel = _api()
    owner, dacl, descriptor = wintypes.LPVOID(), wintypes.LPVOID(), wintypes.LPVOID()
    code = advapi.GetNamedSecurityInfoW(str(path), 1, 0x1 | 0x4, ctypes.byref(owner), None, ctypes.byref(dacl),
                                        None, ctypes.byref(descriptor))
    if code:
        raise ctypes.WinError(code)
    try:
        owner_sid = _sid_text(ctypes, wintypes, advapi, kernel, owner) if owner.value else ""
        if not dacl.value:
            return owner_sid, None
        info = (wintypes.DWORD * 3)()
        if not advapi.GetAclInformation(dacl, info, ctypes.sizeof(info), 2):
            raise ctypes.WinError(ctypes.get_last_error())
        entries = []
        for index in range(info[0]):
            ace = wintypes.LPVOID()
            if not advapi.GetAce(dacl, index, ctypes.byref(ace)):
                raise ctypes.WinError(ctypes.get_last_error())
            kind = ctypes.c_ubyte.from_address(ace.value).value
            if kind in (_ALLOWED, _ALLOWED_CALLBACK) or kind in (1, 10):
                mask = ctypes.c_uint32.from_address(ace.value + 4).value
                sid = _sid_text(ctypes, wintypes, advapi, kernel, ace.value + 8)
                entries.append(("allow" if kind in (_ALLOWED, _ALLOWED_CALLBACK) else "deny", mask, sid))
            elif kind in _DENIED_TYPES:
                entries.append(("deny", 0, ""))
            else:
                entries.append(("other", 0, ""))
        return owner_sid, entries
    finally:
        kernel.LocalFree(descriptor)


def acl_problem(owner_sid: str, entries, user_sid: str) -> str:
    """"owner" unless the current user owns the file; "access" for a null DACL, an entry
    type that is not understood, or an allow entry that gives anyone but the current user,
    SYSTEM and Administrators any right in LOOSE_MASK; else "". Pure, for tests."""
    if owner_sid != user_sid:
        return "owner"
    if entries is None:
        return "access"
    allowed = {user_sid, SYSTEM_SID, ADMINISTRATORS_SID, OWNER_RIGHTS_SID}
    for kind, mask, sid in entries:
        if kind == "other" or (kind == "allow" and sid not in allowed and mask & LOOSE_MASK):
            return "access"
    return ""


def problem(path) -> str:
    """"owner" when the current user does not own the file, "access" when its DACL lets
    anyone else read or change it, else ""."""
    owner, entries = read_security(path)
    return acl_problem(owner, entries, current_user_sid())


def remove_command(path) -> str:
    """`Remove-Item` for one path, written so PowerShell reads the path exactly as it is:
    in single quotes, where only a single quote (the curly ones too) is special, and doubled."""
    quoted = re.sub("['" + chr(0x2018) + chr(0x2019) + chr(0x201A) + chr(0x201B) + "]",
                    lambda found: found.group() * 2, str(path))
    return f"Remove-Item -LiteralPath '{quoted}'"


def refusal(path, label: str | None = None, *, key: bool = False) -> str:
    """The sentence a loose or foreign-owned file earns, naming the one PowerShell command
    that fixes it, or "" when the file is private. Re-protecting is no fix: others may
    already have read the file, so it is deleted and Torque writes a new private one."""
    detail = ""
    try:
        found = problem(path)
    except OSError as exc:
        # An access list that cannot be read cannot be shown private: refuse (fail closed).
        found = "unreadable"
        detail = f" ({exc.strerror or exc})"
    if not found:
        return ""
    name = label or str(path)
    why = {"owner": "is owned by another account",
           "unreadable": "has an access list that cannot be read" + detail,
           "access": "can be read or changed by accounts other than you, SYSTEM and Administrators"}[found]
    after = ("The key is created again at the next grant (grants signed with the old key stop working)"
             if key else "Request and grant the approval again")
    return f"{name} {why}. {after}. Delete it with: {remove_command(path)}"
