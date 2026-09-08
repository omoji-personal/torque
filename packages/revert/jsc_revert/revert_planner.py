"""revert_planner.py — generate revert command per snapshot operation_type.

Closes plan-v5 Closure 6 (revert_executor must transmit parent_snapshot_id).
For each op_type, returns the wrapper invocation that reverts the snapshot.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from . import manifest as mf, org_detect, revert_capabilities, stale_detector
from .update_fields import selected_before_values
from .metadata_scope import MetadataScopeError, recovery_files


def preview(snapshot_id: str, target_org: str) -> int:
    """Print revert plan for snapshot WITHOUT executing. Returns 0 / 1."""
    org = org_detect.resolve_org(target_org)
    if org is None:
        print(f"error: cannot resolve org {target_org!r}", file=sys.stderr)
        return 1
    try:
        snap_dir, m = mf.load_by_id(org.org_id_short, org.alias, snapshot_id)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if m.get("org", {}).get("org_id_18", "")[:15] != org.org_id_short:
        print("error: snapshot belongs to a different org than the explicit target", file=sys.stderr)
        return 1
    m = {**m, "org": {**m["org"], "alias": target_org}}

    print(f"\n=== Revert preview for snapshot {snapshot_id} ===")
    print(f"  operation_type:        {m['operation_type']}")
    print(f"  captured_at:           {m['captured_at']}")
    print(f"  org:                   {m['org']['alias']} ({m['org']['org_id_18']}, "
          f"type={org.detected_org_type}, IsSandbox={org.is_sandbox})")
    print(f"  snapshot_status:       {m.get('snapshot_status')}")
    print(f"  parent_snapshot_id:    {m.get('parent_snapshot_id') or 'none'}")
    print(f"  invoking_intent:       {m.get('invoking_intent', {}).get('reason', '')}")

    # Re-derive rather than trust the stored value. Snapshots written before
    # the capture-aware gate landed carry automatic_revertible: true for
    # deploys that captured nothing (Codex R2-IMP-05), and a stored `true` is
    # exactly the claim that must not be taken on faith. Recomputing from the
    # persisted payload is cheap and pure. If the two disagree, say so out loud
    # rather than silently substituting — a snapshot whose stored promise was
    # wrong is information the operator wants.
    stored = m.get("revert_capabilities", {}) or {}
    rc = revert_capabilities.effective_capabilities(m)
    if rc.get("automatic_revertible") != stored.get("automatic_revertible"):
        print(f"\n  ⚠️  stored revert_capabilities disagree with a fresh evaluation:")
        print(f"       stored: {stored.get('automatic_revertible')}   "
              f"recomputed: {rc.get('automatic_revertible')}")
        print(f"       Using the recomputed value. The snapshot predates the "
              f"capture-aware gate.")

    print(f"\n  automatic_revertible:  {rc.get('automatic_revertible')}")
    if rc.get("manual_recovery_path"):
        print(f"  manual_recovery_path:  {rc['manual_recovery_path'][:200]}...")
    if rc.get("side_effects_warning"):
        print(f"  side_effects_warning:")
        for w in rc["side_effects_warning"]:
            print(f"    - {w}")

    if rc.get("automatic_revertible") is False:
        print(f"\n  ⚠️  This operation is NOT automatically revertible.")
        print(f"     Use the manual recovery guidance above. `jsc revert exec` will refuse.")
        return 0

    # Build the revert command
    revert_cmd = build_revert_command(m, snap_dir)
    if revert_cmd is None:
        print(f"\n  No revert plan defined for operation_type {m['operation_type']}.")
        return 0

    print(f"\n  Revert command would be:")
    print(f"    {' '.join(revert_cmd)}")

    # Drift check for metadata operations
    if m["operation_type"] == "deploy_metadata":
        print(f"\n  Checking drift...")
        drift = stale_detector.classify_metadata_drift(snap_dir, m, target_org)
        if drift:
            summary = stale_detector.summary(drift)
            print(f"  Drift summary:")
            for state, count in summary.items():
                if count:
                    print(f"    {state}: {count}")
            if stale_detector.is_blocking(drift):
                print(f"  ⚠️  {summary['absent']} absent + {summary['renamed_unknown']} renamed_unknown components.")
                print(f"     Revert will REFUSE without --force.")
        else:
            print(f"  (no metadata components in snapshot to check)")

    return 0


def _jsc_bin() -> str:
    """Return this interpreter's sibling console script, or an empty fallback signal."""
    candidate = Path(sys.executable).parent / "jsc"
    if candidate.is_file():
        return str(candidate)
    return ""  # No arbitrary PATH executable fallback; use this interpreter below.


def _jsc_command() -> list[str]:
    console = _jsc_bin()
    return [console] if console else [sys.executable, "-m", "jsc_revert.cli"]


def build_revert_command(manifest: dict, snap_dir: Path) -> list[str] | None:
    """Build the wrapper command to revert a snapshot. Returns None if no plan exists."""
    op = manifest["operation_type"]
    payload = manifest.get("payload", {})
    target_org = manifest["org"]["alias"]
    parent_snap_id = manifest["snapshot_id"]

    if op == "deploy_metadata":
        try:
            source_files = recovery_files(payload, snap_dir)
        except MetadataScopeError as exc:
            print(f"error: metadata recovery scope: {exc}", file=sys.stderr)
            return None
        if not source_files:
            return None
        return [*_jsc_command(), "deploy", "-o", target_org,
                *[arg for path in source_files for arg in ("--source-dir", str(path))],
                "--parent-snapshot-id", parent_snap_id,
                "--operation-type", "revert",
                "--invoking-intent", f"revert-of {parent_snap_id}"]

    if op == "data_record_update":
        if not payload.get("before_row") or not payload.get("record_id"):
            return None
        # A full before-image includes readonly/formula/system fields and unrelated
        # editable fields. Restore only the fields explicitly sent in the update.
        selected = selected_before_values(payload, manifest.get("wrapper_command"))
        if selected is None:
            return None
        kvs = []
        for k, v in selected.items():
            if v is None:
                # SEC-9: a field the forward op changed null->value is left at
                # its new value (NOT auto-restored to null). Emitting a blank
                # token like '#N/A' to `sf data update record --values` is
                # unsafe — the sf CLI would write the literal string and corrupt
                # the field. revert_capabilities surfaces this gap as a
                # side_effects_warning so the TRUE classification doesn't
                # overpromise. To null such a field, restore it manually.
                continue
            # REVERT-VALUES-AMBIGUOUS (full-repo TAA 2026-05-31): the sf CLI
            # `--values` parser splits pairs on spaces and each pair on the first
            # `=`. A raw `f"{k}={v}"` with a value containing a space or `=` (e.g.
            # 'Smith, John = lead', a multi-line note, an address) corrupts the
            # parse and writes WRONG data on revert. Per Salesforce docs, wrap a
            # value that has spaces/`=` in single quotes (Name='Acme Inc'). A value
            # that itself contains a single quote is not safely representable in
            # this flat format — rather than silently emit a corrupt token, bail to
            # None so /revert falls back to documented manual recovery (same
            # honesty discipline as the SEC-9 null case above).
            sv = str(v)
            if "'" in sv:
                return None  # unquotable → manual recovery only
            if sv == "" or any(c in sv for c in (" ", "\t", "\n", "=")):
                kvs.append(f"{k}='{sv}'")
            else:
                kvs.append(f"{k}={sv}")
        if not kvs:
            return None
        return [*_jsc_command(), "data", "update", "-o", target_org,
                "--sobject", payload["object_api_name"],
                "--record-id", payload["record_id"],
                "--values", " ".join(kvs)]

    if op == "data_record_create":
        if not payload.get("record_id"):
            return None  # never got an id, can't delete
        return [*_jsc_command(), "data", "delete", "-o", target_org,
                "--sobject", payload["object_api_name"],
                "--record-id", payload["record_id"]]

    if op == "data_record_delete":
        if payload.get("delete_mode") == "hard":
            return None  # not auto-revertible (bypassed Recycle Bin)
        if not payload.get("before_row") or not payload.get("record_id"):
            return None
        # Soft-deleted record: undelete via the dedicated `jsc data undelete`
        # wrapper (B4). There is no `sf data undelete record`; the wrapper runs
        # anonymous Apex `undelete [SELECT Id FROM <obj> WHERE Id = :rid ALL ROWS]`
        # through the same snapshot/lock path. Carry revert-chain args (mirrors
        # the deploy_metadata case) so the executor's forensic chain links back.
        return [*_jsc_command(), "data", "undelete", "-o", target_org,
                "--sobject", payload["object_api_name"],
                "--record-id", payload["record_id"],
                "--parent-snapshot-id", parent_snap_id,
                "--operation-type", "revert",
                "--invoking-intent", f"revert-of {parent_snap_id}"]

    # Other ops: no plan in this Phase I.4-extended scope
    return None
