#!/usr/bin/env python3
"""
Self-test for jsc_revert.mcp_capture — verifies Route B subprocess CLI
round-trips correctly per Codex-R5-P1-5 (post-capture failures must surface).

Run: python3 -m packages.revert.tests.test_mcp_capture
  OR: PYTHONPATH=. python3 packages/revert/tests/test_mcp_capture.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Allow running both as module and script
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _run(operation: str, payload: dict, env: dict | None = None) -> tuple[int, str, str]:
    """Invoke the CLI as a subprocess (matches how Node would invoke it).

    Codex-R6-P1-2 fix: use the EXACT command + PYTHONPATH that the JS bridge
    reference uses. Tests now exercise the production command shape, not a
    parallel `revert.jsc_revert.mcp_capture` shape that the bridge never invokes.
    """
    base_env = os.environ.copy()
    # Mimic the bridge's PYTHONPATH wiring
    base_env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]) + os.pathsep + os.environ.get("PYTHONPATH", "")  # packages/revert/
    if env:
        base_env.update(env)
    proc = subprocess.run(
        [sys.executable, "-m", "jsc_revert.mcp_capture",  # bare jsc_revert (matches bridge)
         "--operation", operation],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=base_env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _run_version() -> tuple[int, str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]) + os.pathsep + os.environ.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "jsc_revert.mcp_capture", "--version"],
        capture_output=True, text=True, env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def main() -> int:  # noqa: C901
    failures = 0
    tests = []

    # ── F-MC-1: --version ─────────────────────────────────────────────────
    code, out, err = _run_version()
    if code == 0 and "jsc_revert.mcp_capture" in out:
        tests.append(("F-MC-1 --version returns 0 + version string", True))
    else:
        tests.append((f"F-MC-1 --version (got exit {code}, out={out[:60]!r})", False))
        failures += 1

    with tempfile.TemporaryDirectory() as tmpd:
        env = {"JSC_REVERT_DIR": tmpd}

        # ── F-MC-2: apex_run_pre + post round-trip ────────────────────────
        code, out, err = _run("apex_run_pre", {
            "apex_code": "Account a = new Account(Name='Test'); insert a;",
            "target_org": "sample-prod",
            "touched_objects": ["Account"],
        }, env)
        if code != 0:
            tests.append((f"F-MC-2a apex_run_pre (exit {code}, err={err[:100]!r})", False))
            failures += 1
        else:
            try:
                pre = json.loads(out)
                assert "snapshot_id" in pre
                assert pre["status"] == "pre_only"
                assert Path(pre["manifest_dir"]).exists()
                manifest = json.loads((Path(pre["manifest_dir"]) / "manifest.json").read_text())
                assert manifest["operation_type"] == "apex_run"
                assert manifest["snapshot_status"] == "pre_only"
                assert manifest["payload"]["target_org"] == "sample-prod"
                assert "apex_input_sha256" in manifest["payload"]
                tests.append(("F-MC-2a apex_run_pre creates manifest", True))

                # Now post
                code, out, err = _run("apex_run_post", {
                    "snapshot_id": pre["snapshot_id"],
                    "target_org": "sample-prod",
                    "result": {"success": True, "compiled": True},
                }, env)
                if code != 0:
                    tests.append((f"F-MC-2b apex_run_post (exit {code}, err={err[:100]!r})", False))
                    failures += 1
                else:
                    post = json.loads(out)
                    assert post["snapshot_id"] == pre["snapshot_id"]
                    assert post["status"] == "complete"
                    finalized = json.loads((Path(pre["manifest_dir"]) / "manifest.json").read_text())
                    assert finalized["snapshot_status"] == "complete"
                    assert finalized["payload"]["result_summary"]["success"] is True
                    tests.append(("F-MC-2b apex_run_post finalizes snapshot to complete", True))
            except (json.JSONDecodeError, AssertionError, KeyError) as e:
                tests.append((f"F-MC-2 manifest validation failed: {e}", False))
                failures += 1

        # ── F-MC-3: apex_run_failed ───────────────────────────────────────
        code, out, _ = _run("apex_run_pre", {
            "apex_code": "delete [SELECT Id FROM Account];",
            "target_org": "sample-prod",
        }, env)
        pre = json.loads(out)
        code, out, err = _run("apex_run_failed", {
            "snapshot_id": pre["snapshot_id"],
            "target_org": "sample-prod",
            "error": "FIELD_INTEGRITY_EXCEPTION: governor limit",
        }, env)
        if code == 0:
            failed_manifest = json.loads((Path(pre["manifest_dir"]) / "manifest.json").read_text())
            if (failed_manifest["snapshot_status"] == "failed"
                and "FIELD_INTEGRITY_EXCEPTION" in failed_manifest["payload"]["error_message"]):
                tests.append(("F-MC-3 apex_run_failed marks snapshot failed + preserves error", True))
            else:
                tests.append(("F-MC-3 apex_run_failed manifest mismatch", False))
                failures += 1
        else:
            tests.append((f"F-MC-3 apex_run_failed (exit {code}, err={err[:100]!r})", False))
            failures += 1

        # ── F-MC-4: deploy_pre + post round-trip ──────────────────────────
        code, out, _ = _run("deploy_pre", {
            "target_org": "sample-prod",
            "metadata": ["Flow:Foo", "ApexClass:Bar"],
        }, env)
        pre = json.loads(out)
        code, _, _ = _run("deploy_post", {
            "snapshot_id": pre["snapshot_id"],
            "target_org": "sample-prod",
            "deploy_id": "0AfPP00000123456",
            "status": "Succeeded",
        }, env)
        if code == 0:
            m = json.loads((Path(pre["manifest_dir"]) / "manifest.json").read_text())
            if m["snapshot_status"] == "complete" and m["payload"]["deploy_id"] == "0AfPP00000123456":
                tests.append(("F-MC-4 deploy round-trip pre→post", True))
            else:
                tests.append(("F-MC-4 deploy manifest mismatch", False))
                failures += 1
        else:
            tests.append((f"F-MC-4 deploy_post (exit {code})", False))
            failures += 1

        # ── F-MC-5: missing target_org → exit 1 with stderr ───────────────
        code, _, err = _run("apex_run_pre", {"apex_code": "x"}, env)
        if code == 1 and "target_org" in err:
            tests.append(("F-MC-5 missing target_org → exit 1 with diagnostic", True))
        else:
            tests.append((f"F-MC-5 expected exit 1 for missing target_org; got {code}, err={err[:100]!r}", False))
            failures += 1

        # ── F-MC-6: invalid JSON on stdin → exit 1 ────────────────────────
        env_with_pp = {**os.environ, **env}
        env_with_pp["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]) + os.pathsep + os.environ.get("PYTHONPATH", "")
        proc = subprocess.run(
            [sys.executable, "-m", "jsc_revert.mcp_capture",
             "--operation", "apex_run_pre"],
            input="not-valid-json",
            capture_output=True, text=True,
            env=env_with_pp,
        )
        if proc.returncode == 1 and "JSON" in proc.stderr:
            tests.append(("F-MC-6 invalid stdin JSON → exit 1 with diagnostic", True))
        else:
            tests.append((f"F-MC-6 expected exit 1 for bad JSON; got {proc.returncode}", False))
            failures += 1

        # ── F-MC-7: unknown operation → argparse error → exit 2 ──────────
        env_for_7 = os.environ.copy()
        env_for_7["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]) + os.pathsep + os.environ.get("PYTHONPATH", "")
        proc = subprocess.run(
            [sys.executable, "-m", "jsc_revert.mcp_capture",
             "--operation", "nuke_everything"],
            input='{}', capture_output=True, text=True, env=env_for_7,
        )
        if proc.returncode == 2:  # argparse default exit for invalid choice
            tests.append(("F-MC-7 unknown operation → argparse exit 2", True))
        else:
            tests.append((f"F-MC-7 expected argparse exit 2; got {proc.returncode}", False))
            failures += 1

        # ── F-MC-8 (MCP-PATH-TRAVERSAL, TAA 2026-05-31): a caller-supplied
        # snapshot_id with path traversal must be REJECTED (exit 1) and must NOT
        # create anything outside JSC_REVERT_DIR. *_post / *_failed read
        # snapshot_id from the (Node bridge) payload, so this is the attack path.
        canary = Path(tmpd).parent / f"jsc_traversal_canary_{os.getpid()}"
        # ../<canary-name> from base/<org>/ would land in tmpd's parent.
        evil_id = f"../../{canary.name}"
        code, out, err = _run("apex_run_failed", {
            "snapshot_id": evil_id,
            "target_org": "sample-prod",
            "error": "x",
        }, env)
        escaped = canary.exists()
        if code == 1 and not escaped and ("snapshot_id" in err or "invalid" in err.lower()):
            tests.append(("F-MC-8 traversal snapshot_id rejected, no escape", True))
        else:
            tests.append((f"F-MC-8 traversal NOT contained (exit {code}, escaped={escaped}, err={err[:120]!r})", False))
            failures += 1
        if escaped:
            try:
                import shutil
                shutil.rmtree(canary, ignore_errors=True)
            except Exception:
                pass

    # Print summary
    for label, passed in tests:
        prefix = "PASS" if passed else "FAIL"
        print(f"{prefix}: {label}")

    if failures:
        print(f"\nmcp_capture self-test FAILED: {failures} fixture(s)", file=sys.stderr)
        return 1
    print("\nmcp_capture self-test PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
