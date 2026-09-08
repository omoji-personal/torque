#!/usr/bin/env python3
"""Tests for revert_capabilities.py — closes plan-v5 Closure 1 + R5 fixes."""

from __future__ import annotations

import sys
import hashlib
import tempfile
from copy import deepcopy
from pathlib import Path

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from revert.jsc_revert.revert_capabilities import (
    REVERTIBLE_TRUE, REVERTIBLE_FALSE, REVERTIBLE_BEST_EFFORT,
    compute_revertibility, effective_capabilities,
)


def main() -> int:
    failures = 0
    tests = []

    def check(label: str, condition: bool):
        nonlocal failures
        tests.append((label, condition))
        if not condition:
            failures += 1

    # v7.17.0 Gemini-R2-D1 closure: data_bulk_delete + data_record_delete (soft)
    # downgraded to False because the planner has no `sf data undelete`
    # equivalent (Phase I.4-extended-D deferred). Captured forensic before-state
    # enables manual revert.
    r = compute_revertibility("data_bulk_delete", {"delete_mode": "soft"})
    check("F-RC-1 data_bulk_delete soft → False (v7.17.0 forensic-only)",
          r["automatic_revertible"] is REVERTIBLE_FALSE)
    check("F-RC-1b data_bulk_delete soft has manual_recovery_path",
          r["manual_recovery_path"] and "before_csv" in r["manual_recovery_path"])

    # ── Hard delete special cases ──
    r = compute_revertibility("data_bulk_delete", {"delete_mode": "hard"})
    check("F-RC-2 data_bulk_delete --hard → False",
          r["automatic_revertible"] is REVERTIBLE_FALSE)
    check("F-RC-2b data_bulk_delete --hard manual_recovery_path mentions Recycle Bin",
          "Recycle Bin" in (r["manual_recovery_path"] or ""))

    r = compute_revertibility("data_record_delete", {"delete_mode": "hard"})
    check("F-RC-3 data_record_delete --hard → False",
          r["automatic_revertible"] is REVERTIBLE_FALSE)

    # B4: soft data_record_delete is now BEST-EFFORT (planner has a
    # `jsc data undelete` case). The hard case (F-RC-3 above) stays False.
    r = compute_revertibility("data_record_delete", {"delete_mode": "soft"})
    check("F-RC-4 data_record_delete soft → best-effort (B4 undelete planner case)",
          r["automatic_revertible"] is REVERTIBLE_BEST_EFFORT)
    check("F-RC-4b data_record_delete soft mentions undelete in recovery path",
          "undelete" in (r["manual_recovery_path"] or "").lower())
    check("F-RC-4c data_record_delete soft has purge-window side_effects_warning",
          any("purge" in w.lower() and "recycle bin" in w.lower()
              for w in r["side_effects_warning"]))

    # ── apex_run is one-way (Gemini-R4-P1-3) ──
    r = compute_revertibility("apex_run", {})
    check("F-RC-5 apex_run → False (one-way)",
          r["automatic_revertible"] is REVERTIBLE_FALSE)
    check("F-RC-5b apex_run side_effects_warning is non-empty",
          len(r["side_effects_warning"]) > 0)
    check("F-RC-5c apex_run manual_recovery_path mentions debug log",
          "debug log" in (r["manual_recovery_path"] or "").lower())

    # ── package_install is one-way ──
    r = compute_revertibility("package_install", {})
    check("F-RC-6 package_install → False",
          r["automatic_revertible"] is REVERTIBLE_FALSE)

    # ── Best-effort tristate (CR5-2 / Codex-R5-P1-1 — FK cascade) ──
    r = compute_revertibility("data_record_create", {"record_id": "a00000000000001AAA"})
    check("F-RC-7 data_record_create → 'best-effort' (FK cascade caveat)",
          r["automatic_revertible"] == REVERTIBLE_BEST_EFFORT)
    check("F-RC-7b data_record_create warning mentions REFERENCE_FOUND",
          any("REFERENCE_FOUND" in w for w in r["side_effects_warning"]))

    # v7.17.0 Codex-R1-P1-08 closure: planner-unsupported op_types downgraded
    # to REVERTIBLE_FALSE (was best-effort in v7.14)
    r = compute_revertibility("data_record_upsert", {})
    check("F-RC-8 data_record_upsert → False (v7.17.0 forensic-only)",
          r["automatic_revertible"] is REVERTIBLE_FALSE)

    r = compute_revertibility("data_bulk_import", {})
    check("F-RC-9 data_bulk_import → False (v7.17.0 forensic-only)",
          r["automatic_revertible"] is REVERTIBLE_FALSE)

    # ── Clean True cases (only planner-supported op_types remain True) ──
    # Codex-R2-P2-02: revert downgraded — planner returns None for it
    #
    # deploy_metadata was in this loop and asserted True on an EMPTY payload.
    # That assertion encoded the P0: it required the classifier to promise
    # revertibility for a deploy that had captured nothing. Predicted by Codex
    # R2-IMP-05 ("tests require deploy_metadata to be true with an empty
    # payload") before the gate was written, and it duly failed. It is moved
    # below with the evidence it was missing, NOT deleted — a True case for
    # deploy_metadata still has to exist, it just has to earn it.
    for op in ("data_record_update",):
        r = compute_revertibility(op, {"fields_updated": ["Name"], "before_row": {"Name": "Original"}})
        check(f"F-RC-{op}-True {op} → True",
              r["automatic_revertible"] is REVERTIBLE_TRUE)

    # ── P0 regression: deploy_metadata is revertible ONLY with proven capture ──
    # Guard for the 2026-07-28 P0. wrappers/deploy.py pre-retrieves only for
    # --metadata, so --source-dir / --manifest deploys captured nothing while
    # BASE_REVERTIBILITY still promised REVERTIBLE_TRUE. The planner then found
    # no metadata-before directory and returned None, i.e. the operator learned
    # at revert time, on the org they had just changed.
    capture_tmp = tempfile.TemporaryDirectory()
    capture_root = Path(capture_tmp.name).resolve() / "metadata-before" / "classes"
    capture_root.mkdir(parents=True)
    capture_files = []
    for name, content in {
        "Foo.cls": "public class Foo {}\n",
        "Foo.cls-meta.xml": '<ApexClass xmlns="http://soap.sforce.com/2006/04/metadata"><apiVersion>67.0</apiVersion><status>Active</status></ApexClass>',
    }.items():
        path = capture_root / name
        path.write_text(content)
        capture_files.append({"type": "ApexClass", "fullName": "Foo",
                              "before_state": "present", "filePath": str(path),
                              "before_checksum": hashlib.sha256(path.read_bytes()).hexdigest()})
    _CAPTURED = {"selectors": {"metadata_args": ["ApexClass:Foo"]}, "files": capture_files}
    check("F-RC-deploy-captured deploy_metadata WITH captured files → True",
          compute_revertibility("deploy_metadata", _CAPTURED)["automatic_revertible"]
          is REVERTIBLE_TRUE)

    for label, payload in (
        ("source-dir", {"selectors": {"source_dirs": ["force-app"]}, "files": []}),
        ("manifest", {"selectors": {"manifest_paths": ["package.xml"]}, "files": []}),
        # A --metadata retrieve that came back EMPTY must also fail closed:
        # snapshot_pre cannot distinguish an empty result from a complete one
        # (Codex R2-IMP-03), so the selector is not evidence — the files are.
        ("metadata-but-empty-retrieve",
         {"selectors": {"metadata_args": ["ApexClass:Foo"]}, "files": []}),
        ("no-selectors", {"selectors": {}, "files": []}),
        ("legacy-empty-payload", {}),
    ):
        r = compute_revertibility("deploy_metadata", payload)
        check(f"F-RC-deploy-uncaptured-{label} deploy_metadata → False",
              r["automatic_revertible"] is REVERTIBLE_FALSE)
        check(f"F-RC-deploy-uncaptured-{label} names the cause",
              "before-state" in (r["manual_recovery_path"] or ""))
    r = compute_revertibility("revert", {})
    check("F-RC-revert-False revert → False (planner case missing)",
          r["automatic_revertible"] is REVERTIBLE_FALSE)
    check("F-RC-revert mentions metadata-before in recovery path",
          "metadata-before" in (r["manual_recovery_path"] or ""))

    # ── P0 regression: non-empty files is NOT proof of capture ────────────
    # Codex R3-P0-01, against the FIRST version of this gate. snapshot_pre emits
    # an entry for every component it was asked about, tagged present / absent /
    # retrieve_failed / unknown, and deploy.py copies them all into
    # payload["files"]. So "files is non-empty" was satisfied by an all-net-new
    # deploy or a wholly failed retrieve — neither of which leaves anything to
    # redeploy. Only a `present` entry carries checksum + filePath.
    def _dep(files):
        return {"selectors": {"metadata_args": ["ApexClass:Foo"]}, "files": files}

    for label, files in (
        ("absent-only", [{"type": "ApexClass", "fullName": "New", "before_state": "absent"}]),
        ("retrieve-failed-only",
         [{"type": "ApexClass", "fullName": "X", "before_state": "retrieve_failed"}]),
        ("unknown-only", [{"type": "ApexClass", "fullName": "X", "before_state": "unknown"}]),
        ("absent-plus-failed",
         [{"type": "ApexClass", "fullName": "A", "before_state": "absent"},
          {"type": "ApexClass", "fullName": "B", "before_state": "retrieve_failed"}]),
        ("files-not-a-list", "corrupt"),
        ("files-of-junk", ["not-a-dict", 42]),
    ):
        check(f"F-RC-deploy-nopresent-{label} deploy_metadata → False",
              compute_revertibility("deploy_metadata", _dep(files))["automatic_revertible"]
              is REVERTIBLE_FALSE)

    # A selected net-new component has no before-state. Do not promise a
    # complete automatic restore merely because another selected member exists.
    mixed = deepcopy(_CAPTURED)
    mixed["selectors"]["metadata_args"].append("ApexClass:New")
    mixed["files"].append({"type": "ApexClass", "fullName": "New", "before_state": "absent"})
    mixed_result = compute_revertibility("deploy_metadata", mixed)
    check("F-RC-deploy-mixed present+absent → False (incomplete selected capture)",
          mixed_result["automatic_revertible"] is REVERTIBLE_FALSE)
    check("F-RC-deploy-mixed explains manual recovery",
          bool(mixed_result["manual_recovery_path"]))

    # Preserve the capitalization case with matching selector and real capture.
    title_case = deepcopy(_CAPTURED)
    for item in title_case["files"]:
        item["before_state"] = "Present"
    check("F-RC-deploy-case 'Present' is honoured like 'present'",
          compute_revertibility("deploy_metadata", title_case)["automatic_revertible"]
          is REVERTIBLE_TRUE)

    # ── effective_capabilities must FAIL CLOSED, never fail open ──────────
    # Codex R3-P1-03: validate_envelope only checks that payload exists, so
    # selectors can be a list; compute_revertibility then raises, and the first
    # version returned the STORED block — handing back `true` for exactly the
    # malformed snapshot least deserving of trust.
    _MALFORMED = {
        "operation_type": "deploy_metadata",
        "payload": {"selectors": ["not", "a", "dict"], "files": []},
        "revert_capabilities": {"automatic_revertible": True},
    }
    check("F-RC-failclosed malformed selectors do NOT yield stored True",
          effective_capabilities(_MALFORMED)["automatic_revertible"] is not REVERTIBLE_TRUE)

    # ── P0 regression: a STALE stored `true` must not be trusted ──────────
    # The first pass at this fix hardened `revert preview` only. The executor
    # and the `revert-show` listing still read revert_capabilities straight off
    # the manifest, so a snapshot written before the capture-aware gate — which
    # carries automatic_revertible: true for a deploy that captured nothing —
    # would have sailed past the executor's refusal and RUN. Found by auditing
    # my own fix. All three consumers now share effective_capabilities().
    _STALE = {
        "operation_type": "deploy_metadata",
        "payload": {"selectors": {"source_dirs": ["force-app"]}, "files": []},
        "revert_capabilities": {"automatic_revertible": True,
                                "manual_recovery_path": "", "side_effects_warning": []},
    }
    check("F-RC-stale-manifest stored True is overridden by re-derivation",
          effective_capabilities(_STALE)["automatic_revertible"] is REVERTIBLE_FALSE)
    check("F-RC-stale-manifest re-derivation explains why",
          "before-state" in (effective_capabilities(_STALE)["manual_recovery_path"] or ""))

    _HONEST = {
        "operation_type": "deploy_metadata",
        # before_state is REQUIRED for this to count as capture. The first
        # version of this fixture omitted it and passed, because the gate then
        # only checked that files was non-empty (Codex R3-P0-01).
        "payload": deepcopy(_CAPTURED),
        "revert_capabilities": {"automatic_revertible": True},
    }
    check("F-RC-stale-manifest genuine capture still reports True",
          effective_capabilities(_HONEST)["automatic_revertible"] is REVERTIBLE_TRUE)

    # A malformed manifest must degrade without crashing — `revert-show`
    # iterates every bundle and must not die on one bad file — but it must
    # degrade CLOSED. The earlier version of this fixture asserted the stored
    # value was handed back, which is fail-OPEN and was the behaviour Codex
    # R3-P1-03 flagged. Returning a dict is the crash guarantee; returning
    # not-True is the safety guarantee, and both are asserted.
    _BAD = effective_capabilities({"revert_capabilities": {"automatic_revertible": "x"}})
    check("F-RC-stale-manifest malformed manifest degrades without crashing",
          isinstance(_BAD, dict))
    check("F-RC-stale-manifest malformed manifest degrades CLOSED, not open",
          _BAD.get("automatic_revertible") is not REVERTIBLE_TRUE)

    # ── Unknown operation_type → False fallback ──
    r = compute_revertibility("nuke_universe", {})
    check("F-RC-10 unknown operation_type → False",
          r["automatic_revertible"] is REVERTIBLE_FALSE)
    check("F-RC-10b unknown op manual_recovery_path mentions deferred to manual review",
          "manual" in (r["manual_recovery_path"] or "").lower())

    # ── 16 operation_types covered (per Gemini-R4-P3-1 count fix) ──
    expected_ops = {
        "deploy_metadata", "data_record_update", "data_record_create",
        "data_record_upsert", "data_record_delete", "data_record_import",
        "data_bulk_update", "data_bulk_upsert", "data_bulk_delete",
        "data_bulk_import", "org_assign_permset", "org_assign_permsetlicense",
        "package_install", "package_uninstall", "apex_run", "revert",
    }
    check("F-RC-11 16 operation_types defined",
          len(expected_ops) == 16)
    for op in expected_ops:
        r = compute_revertibility(op, {})
        check(f"F-RC-cov-{op} {op} returns valid tristate",
              r["automatic_revertible"] in (REVERTIBLE_TRUE, REVERTIBLE_FALSE, REVERTIBLE_BEST_EFFORT))

    capture_tmp.cleanup()

    for label, passed in tests:
        print(f"{'PASS' if passed else 'FAIL'}: {label}")

    if failures:
        print(f"\nrevert_capabilities self-test FAILED: {failures} fixture(s)", file=sys.stderr)
        return 1
    print(f"\nrevert_capabilities self-test PASSED ({len(tests)} fixtures)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
