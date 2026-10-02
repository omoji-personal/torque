"""Private publication must withstand collisions and directory replacement."""
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from jsc_revert import bundle


def test_predictable_old_temp_symlink_cannot_truncate_another_file(tmp_path):
    target = tmp_path / "manifest.json"
    victim = tmp_path / "unrelated.txt"
    victim.write_text("keep", encoding="utf-8")
    try:
        target.with_suffix(target.suffix + f".tmp.{os.getpid()}").symlink_to(victim)
    except OSError:
        pytest.skip("Symlink creation requires platform permission")
    bundle.atomic_write_text(target, "private")
    assert victim.read_text(encoding="utf-8") == "keep"
    assert target.read_text(encoding="utf-8") == "private"


def test_short_writes_are_completed(tmp_path, monkeypatch):
    original = os.write
    monkeypatch.setattr(os, "write", lambda fd, data: original(fd, data[:3]))
    target = tmp_path / "record.json"
    bundle.atomic_write_text(target, "caf\u00e9 " * 100)
    assert target.read_text(encoding="utf-8") == "caf\u00e9 " * 100
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600


def test_concurrent_publications_have_distinct_temporary_files(tmp_path):
    target = tmp_path / "record.json"
    values = [str(i) * 10000 for i in range(20)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda value: bundle.atomic_write_text(target, value), values))
    assert target.read_text(encoding="utf-8") in values
    assert sorted(p.name for p in tmp_path.iterdir()) == [target.name]


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory descriptors")
def test_parent_swap_does_not_redirect_publication(tmp_path, monkeypatch):
    parent, moved, outside = (tmp_path / name for name in ("parent", "moved", "outside"))
    parent.mkdir(); outside.mkdir()
    (outside / "record.json").write_text("keep", encoding="utf-8")
    original = os.replace
    def swap_then_replace(*args, **kwargs):
        parent.rename(moved)
        parent.symlink_to(outside, target_is_directory=True)
        return original(*args, **kwargs)
    monkeypatch.setattr(os, "replace", swap_then_replace)
    bundle.atomic_write_text(parent / "record.json", "private")
    assert (outside / "record.json").read_text(encoding="utf-8") == "keep"
    assert (moved / "record.json").read_text(encoding="utf-8") == "private"


def test_failed_publication_cleans_temp_and_keeps_previous_bytes(tmp_path, monkeypatch):
    target = tmp_path / "record.json"
    target.write_text("previous", encoding="utf-8")
    def fail(*args, **kwargs):
        raise OSError("publication unavailable")
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        bundle.atomic_write_text(target, "next")
    assert target.read_text(encoding="utf-8") == "previous"
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS fixed system aliases")
def test_macos_system_temp_alias_preserves_private_publication(tmp_path):
    physical = tmp_path.resolve() / "record.json"
    alias = type(tmp_path)("/") / physical.relative_to("/private")
    bundle.atomic_write_text(alias, "private")
    assert physical.read_text(encoding="utf-8") == "private"


@pytest.mark.skipif(os.name != "nt", reason="Windows protected DACL")
def test_windows_publication_has_protected_owner_system_acl(tmp_path):
    import ctypes
    from ctypes import wintypes
    path = tmp_path / "private.json"
    bundle.atomic_write_text(path, "private")
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    descriptor, text = wintypes.LPVOID(), wintypes.LPWSTR()
    get = advapi.GetNamedSecurityInfoW
    get.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
                   wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID,
                   ctypes.POINTER(wintypes.LPVOID)]
    convert = advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW
    convert.argtypes = [wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                       ctypes.POINTER(wintypes.LPWSTR), wintypes.LPVOID]
    kernel.LocalFree.argtypes = [wintypes.LPVOID]
    assert get(str(path), 1, 4, None, None, None, None, ctypes.byref(descriptor)) == 0
    try:
        assert convert(descriptor, 1, 4, ctypes.byref(text), None)
        try:
            assert text.value.startswith("D:P")
            assert "(A;;FA;;;OW)" in text.value
            assert "(A;;FA;;;SY)" in text.value
            assert text.value.count("(") == 2
        finally:
            kernel.LocalFree(text)
    finally:
        kernel.LocalFree(descriptor)


@pytest.mark.skipif(os.name != "nt", reason="Windows directory handle sharing")
def test_windows_parent_cannot_be_renamed_during_publication(tmp_path, monkeypatch):
    parent = tmp_path / "parent"
    parent.mkdir()
    original = os.replace
    def try_swap(*args, **kwargs):
        with pytest.raises(OSError):
            parent.rename(tmp_path / "moved")
        return original(*args, **kwargs)
    monkeypatch.setattr(os, "replace", try_swap)
    bundle.atomic_write_text(parent / "record.json", "private")
    assert (parent / "record.json").read_text(encoding="utf-8") == "private"
