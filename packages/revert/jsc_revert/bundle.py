"""bundle.py — storage primitives for snapshot bundles.

Provides:
  - default + override path resolution (JSC_REVERT_DIR)
  - org-keyed directory structure (<org_id_short>-<alias>/<iso-ts>-<hash>/)
  - file-mode discipline (dir 0o700, file 0o600)
  - atomic writes via temp + os.replace
"""

from __future__ import annotations
from jsc_common.workspace import state_dir

import json
import os
import sys
from contextlib import contextmanager
import time
import uuid
from pathlib import Path


DEFAULT_REVERT_DIR = None  # compatibility symbol; defaults resolve at call time


def revert_dir() -> Path:
    """Resolve the JSC_REVERT_DIR root, creating it if missing (mode 0o700)."""
    base = state_dir("revert", legacy_env="JSC_REVERT_DIR")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    return base


def org_dir(org_id_short: str, org_alias: str) -> Path:
    """Per-org directory: <revert-dir>/<org_id_short>-<alias>/

    org_id_short is the first 15 chars of the 18-char org id; alias is the
    sf CLI alias. Both safe for filesystem (alphanumeric + hyphen).
    """
    safe_alias = "".join(c if c.isalnum() or c == "-" else "_" for c in org_alias)
    safe_short = "".join(c if c.isalnum() else "_" for c in org_id_short)
    d = revert_dir() / f"{safe_short}-{safe_alias}"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def new_snapshot_dir(org_id_short: str, org_alias: str, snapshot_id: str | None = None) -> tuple[Path, str]:
    """Create a fresh snapshot bundle dir. Returns (path, snapshot_id)."""
    if snapshot_id is None:
        snapshot_id = uuid.uuid4().hex[:16]
    iso_ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    short_hash = snapshot_id[:8]
    snap_dir = org_dir(org_id_short, org_alias) / f"{iso_ts}-{short_hash}"
    snap_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return snap_dir, snapshot_id


@contextmanager
def _parent_directory(path: Path):
    """Pin every parent while publishing, refusing symlinks/reparse points.

    POSIX uses openat/renameat. Windows directory handles deny delete sharing,
    preventing renames while the pathname-based replacement is in progress.
    """
    parent = path.absolute().parent
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handles = []
        try:
            current = Path(parent.anchor)
            for part in parent.parts[1:]:
                current /= part
                current.mkdir(mode=0o700, exist_ok=True)
                # FILE_LIST_DIRECTORY: a handle with no data access is exempt
                # from share checks, so it would not block a rename.
                handle = create(str(current), 0x1, 3, None, 3, 0x02200000, None)
                if handle == wintypes.HANDLE(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
                handles.append(handle)
                if current.lstat().st_file_attributes & 0x400:
                    raise OSError("Private write parent is a reparse point")
            yield None
        finally:
            for handle in reversed(handles):
                kernel.CloseHandle(handle)
        return
    fd = os.open(parent.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parent.parts[1:]:
            if part == "..":
                raise ValueError("Private write path contains traversal")
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _windows_private_file(path: Path) -> int:
    """CREATE_NEW with a protected owner/System DACL, including on Python 3.10."""
    import ctypes
    from ctypes import wintypes
    import msvcrt
    class SecurityAttributes(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("descriptor", wintypes.LPVOID),
                    ("inherit", wintypes.BOOL)]
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), wintypes.LPVOID]
    descriptor = wintypes.LPVOID()
    if not convert("D:P(A;;FA;;;SY)(A;;FA;;;OW)", 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       ctypes.POINTER(SecurityAttributes), wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    kernel.LocalFree.argtypes = [wintypes.LPVOID]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    try:
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
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


def _windows_replace(source: Path, target: Path) -> None:
    """Windows denies a replace while another writer is replacing the same
    target (WinError 5/32); retry briefly, since the condition is transient."""
    for attempt in range(50):
        try:
            os.replace(source, target)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in (5, 32) or attempt == 49:
                raise
            time.sleep(0.01 * (attempt + 1))


def atomic_write_bytes(path: Path, content: bytes, mode: int = 0o600) -> None:
    """Publish complete private bytes from an exclusively created random file."""
    path = Path(path).absolute()
    # macOS exposes these fixed system aliases from its root-owned / directory.
    # Spell out their targets; never resolve operator-controlled path segments.
    if sys.platform == "darwin" and path.parts[1:2] in (("var",), ("tmp",)):
        path = Path("/private").joinpath(*path.parts[1:])
    with _parent_directory(path) as parent_fd:
        temp = f".{path.name}.{uuid.uuid4().hex}.tmp"
        if parent_fd is None:
            fd = _windows_private_file(path.parent / temp)
        else:
            fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                         mode & 0o600, dir_fd=parent_fd)
        try:
            try:
                remaining = memoryview(content)
                while remaining:
                    count = os.write(fd, remaining)
                    if count <= 0:
                        raise OSError("Private write made no progress")
                    remaining = remaining[count:]
                os.fsync(fd)
            finally:
                os.close(fd)
            if parent_fd is None:
                _windows_replace(path.parent / temp, path)
            else:
                os.replace(temp, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        finally:
            try:
                if parent_fd is None:
                    (path.parent / temp).unlink()
                else:
                    os.unlink(temp, dir_fd=parent_fd)
            except FileNotFoundError:
                pass


def atomic_write_text(path: Path, content: str, mode: int = 0o600) -> None:
    atomic_write_bytes(path, content.encode("utf-8"), mode)


def atomic_write_json(path: Path, data: dict, mode: int = 0o600) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=False), mode=mode)
