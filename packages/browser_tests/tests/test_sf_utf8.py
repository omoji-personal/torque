import json
import locale
import subprocess
import sys

import pytest

from jsc_browser_tests.sf_client import SfClient, SfError


@pytest.mark.parametrize("method,args", [("_run", ["data", "query"]), ("_run_raw", ["org", "display"])])
def test_browser_sf_uses_utf8_with_legacy_locale(monkeypatch, method, args):
    monkeypatch.setattr(locale, "getencoding", lambda: "cp1252", raising=False)
    monkeypatch.setattr(locale, "getpreferredencoding", lambda *a: "cp1252")
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "cp1252", raising=False)
    original = subprocess.run
    def run(command, **kwargs):
        return original([sys.executable, "-c", "import os; os.write(1, bytes.fromhex('7b2276616c7565223a22c3a9227d'))"], **kwargs)
    monkeypatch.setattr(subprocess, "run", run)
    assert getattr(SfClient("synthetic"), method)(args)["value"] == "\u00e9"


@pytest.mark.parametrize("method", ["_run", "_run_raw"])
def test_invalid_utf8_reports_incomplete_without_logging_credentials(monkeypatch, method):
    def invalid(*args, **kwargs):
        raise UnicodeDecodeError("utf-8", b'synthetic-secret-\xff', 17, 18, "invalid")
    monkeypatch.setattr(subprocess, "run", invalid)
    with pytest.raises(SfError, match="UTF-8") as raised:
        getattr(SfClient("synthetic"), method)(["data", "query"])
    assert "synthetic-secret" not in str(raised.value)


@pytest.mark.parametrize("output", ["not JSON", "[]", "null", '"a string"'])
def test_invalid_structured_output_is_an_incomplete_sf_operation(output):
    with pytest.raises(SfError):
        SfClient._decode(output, "synthetic")
