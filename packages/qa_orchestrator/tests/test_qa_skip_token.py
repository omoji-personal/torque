"""test_qa_skip_token.py — COR-3 single-use atomic consume regression coverage.

Audit 2026-05-30 COR-3: validate_for_skip() consumed the token with a
check-then-unlink that swallowed FileNotFoundError, so two concurrent callers
could both pass validation and both return (True, ...) — a TOCTOU defeating the
documented "Single-use atomic consume" contract. The fix replaces the unlink
with an atomic os.rename claim; exactly one caller wins. These tests lock that in.
"""
import json
import os
import threading
import time
import tempfile
from pathlib import Path

from jsc_qa import qa_skip_token


def _mint_valid_token(tmpdir):
    """Mint a token whose operator/org/target match what validate_for_skip checks."""
    org = "00DPP0000004XYZAB1"
    target = "A3"
    path = Path(tmpdir) / ".qa_skip_token.json"
    qa_skip_token.mint(
        operation_type="skip_change_type",
        org_id_18=org,
        skip_target=target,
        reason="TEST-0001: regression for single-use atomic consume",
        operator=qa_skip_token._current_user_name(),
        target_path=path,
    )
    os.chmod(path, 0o600)
    return path, org, target


def test_consume_is_single_use_sequential():
    with tempfile.TemporaryDirectory() as td:
        path, org, target = _mint_valid_token(td)
        ok1, _ = qa_skip_token.validate_for_skip(org, target, target_path=path)
        ok2, msg2 = qa_skip_token.validate_for_skip(org, target, target_path=path)
        assert ok1 is True, "first consume must succeed"
        assert ok2 is False, f"second consume must fail (single-use); got {msg2!r}"
        assert not path.exists(), "token file must be gone after consume"


def test_consume_is_atomic_under_concurrency():
    """Exactly one of many concurrent validators may win the single-use token."""
    with tempfile.TemporaryDirectory() as td:
        path, org, target = _mint_valid_token(td)
        results = []
        lock = threading.Lock()
        start = threading.Barrier(8)

        def worker():
            start.wait()
            ok, _ = qa_skip_token.validate_for_skip(org, target, target_path=path)
            with lock:
                results.append(ok)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results.count(True) == 1, (
            f"exactly one concurrent consume must win; got {results.count(True)} "
            f"successes out of {len(results)}"
        )
        assert not path.exists(), "token file must be gone after the winning consume"
        # no leftover .consumed sentinels
        leftovers = list(Path(td).glob(".qa_skip_token.json.consumed.*"))
        assert leftovers == [], f"sentinel files must be cleaned up; found {leftovers}"


def _write_token(path, **overrides):
    """Hand-craft a token file (bypassing mint) to simulate a forged/edited token."""
    now = time.time()
    tok = {
        "schema_version": 1,
        "operation_type": "skip_change_type",
        "org_id_18": "00DPP0000004XYZAB1",
        "skip_target": "A3",
        "reason": "TEST-0001: forged-token regression",
        "issued_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now)),
        "expiry_iso": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now + 600)),
        "operator": qa_skip_token._current_user_name(),
    }
    tok.update(overrides)
    fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(tok).encode())
    finally:
        os.close(fd)


def test_unknown_operation_type_is_rejected():
    """QA-SKIP-UNKNOWN-OP (TAA 2026-05-31): a forged token with an unknown
    operation_type must NOT validate (it previously fell through the if/elif
    skip_target chain with no else and returned True after consuming)."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / ".qa_skip_token.json"
        _write_token(path, operation_type="bogus")
        ok, msg = qa_skip_token.validate_for_skip("00DPP0000004XYZAB1", "A3", target_path=path)
        assert ok is False, f"unknown op_type must be rejected; got {msg!r}"
        assert "operation_type" in msg
        assert path.exists(), "rejected token must NOT be consumed"


def test_over_ttl_token_is_rejected():
    """QA-SKIP-TTL-UNCAPPED (TAA 2026-05-31): a token whose TTL exceeds the
    per-op cap (skip_change_type = 3600s) must be rejected even if not expired."""
    now = time.time()
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / ".qa_skip_token.json"
        _write_token(
            path,
            operation_type="skip_change_type",
            issued_at_iso=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now)),
            expiry_iso=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now + 30 * 24 * 3600)),
        )
        ok, msg = qa_skip_token.validate_for_skip("00DPP0000004XYZAB1", "A3", target_path=path)
        assert ok is False, f"over-TTL token must be rejected; got {msg!r}"
        assert "TTL" in msg
        assert path.exists(), "rejected token must NOT be consumed"


if __name__ == "__main__":
    test_consume_is_single_use_sequential()
    test_consume_is_atomic_under_concurrency()
    test_unknown_operation_type_is_rejected()
    test_over_ttl_token_is_rejected()
    print("qa_skip_token single-use/atomic consume + forged-token tests passed")
