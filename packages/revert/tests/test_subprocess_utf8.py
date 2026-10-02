import json
import locale
import subprocess
import sys
from types import SimpleNamespace

import pytest

from jsc_revert.wrappers import _common as common


def test_sf_output_uses_utf8_even_with_legacy_locale(monkeypatch):
    monkeypatch.setattr(locale, "getencoding", lambda: "cp1252", raising=False)
    monkeypatch.setattr(locale, "getpreferredencoding", lambda *a: "cp1252")
    # Exercise the fallback even when the outer offline launcher sets PYTHONUTF8.
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "cp1252", raising=False)
    original = subprocess.run
    def run(command, **kwargs):
        return original([sys.executable, "-c", "import os; os.write(1, bytes.fromhex('7b2276616c7565223a22c3a9227d'))"], **kwargs)
    monkeypatch.setattr(common.subprocess, "run", run)
    code, output, _ = common.run_sf_subprocess(["synthetic"])
    assert code == 0
    assert json.loads(output)["value"] == "\u00e9"


def test_invalid_utf8_is_incomplete_and_raw_bytes_are_private(tmp_path, monkeypatch):
    ctx = common.WrapperContext("data_record_update", "synthetic", "synthetic")
    ctx.snap_dir = tmp_path
    ctx.manifest = {"phases": {"underlying_command": {"status": "complete"}}}
    monkeypatch.setattr(ctx, "ensure_ownership", lambda: None)
    monkeypatch.setattr(ctx, "save", lambda: None)
    token = common._ACTIVE_WRAPPER.set(ctx)
    def invalid(*args, **kwargs):
        raise UnicodeDecodeError("utf-8", b'raw-\xff', 4, 5, "invalid")
    monkeypatch.setattr(common.subprocess, "run", invalid)
    try:
        with pytest.raises(common.IncompleteSubprocessOutput):
            common.run_sf_subprocess(["synthetic"])
    finally:
        common._ACTIVE_WRAPPER.reset(token)
    assert ctx.manifest["snapshot_status"] == "partial"
    assert ctx.manifest["mutation_succeeded"] is True
    raw = next(tmp_path.glob("invalid-utf8-*.bin"))
    assert raw.read_bytes() == b'raw-\xff'
    import os
    assert os.name == "nt" or raw.stat().st_mode & 0o777 == 0o600
