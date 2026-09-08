"""revert_capabilities.py — closes plan-v5 Closure 1 + R5 TRIPLE-convergent fix.

Computes per-operation_type revertibility honesty:
  - automatic_revertible: True | False | "best-effort"  (TRISTATE per CR5-2)
  - manual_recovery_path: prose explaining recovery for non-automatic cases
  - side_effects_warning: list of caveats

Classification history (read BASE_REVERTIBILITY below for the CURRENT values —
this prose is history only):
  - Gemini-R4-P1-3: apex_run + data_*_delete --hard-delete + package_install
    are False (one-way operations).
  - data_record_create remains "best-effort": delete-by-id can be blocked by FK
    references, can cascade-delete records created later, and cannot unwind
    sharing/files/automation/async jobs (explicit FK warning attached).

NOTE (v7.17.0 honesty pass, Codex-R1-P1-08 — corrects the now-superseded R5
prose that previously sat here): the R5 round briefly classified data_bulk_delete
(soft) as True and the upsert/import family as best-effort. That was REVERTED:
the contract is automatic_revertible==True IFF revert_planner.build_revert_command
has an implemented case. Every op_type added in Phase I.4-extended-2 without a
planner case (data_record_upsert, data_*_import, data_bulk_update/upsert/delete,
org_assign_*, deploy_quick_promote) is now REVERTIBLE_FALSE with a
forensic-manual-recovery path — NOT True/best-effort. The stale-comment that
implied otherwise (RSC-P1-01) is removed; see BASE_REVERTIBILITY for ground truth.
"""

from __future__ import annotations

from typing import Any
import re
from .update_fields import requested_fields, selected_before_values


# Tristate enum values
REVERTIBLE_TRUE = True            # full automatic revert with high confidence
REVERTIBLE_FALSE = False          # one-way operation; manual recovery only
REVERTIBLE_BEST_EFFORT = "best-effort"  # auto attempts revert but FK/sharing/automation may block


# Per-operation_type baseline classification.
# Special cases (data_*_delete --hard-delete, etc.) handled in compute_revertibility.
#
# v7.17.0 (Codex-R1-P1-08) honesty fix: op_types added in Phase I.4-extended-2
# that DO NOT YET have a planner implementation in revert_planner.build_revert_command
# are classified REVERTIBLE_FALSE for v7.17.0 with a forensic-manual-recovery
# path. The wrapper still captures the before-state CSV/IDs needed for manual
# revert; the operator (or v7.17.1 planner extension) performs the revert.
# Re-promote individually as planner cases are implemented + tested.
PLANNER_SUPPORTED_OP_TYPES = frozenset({
    # Strictly: op_types for which revert_planner.build_revert_command returns
    # a non-None command (i.e., the planner can actually plan the revert).
    # Gemini-R2-D1 closure: do NOT list op_types whose planner case returns None.
    # data_record_delete (soft) needs an `sf data undelete` equivalent — the
    # planner explicitly returns None at line 129 ("Phase I.4-extended-D could
    # ship an undelete helper; for now defer"). data_record_delete is therefore
    # downgraded to REVERTIBLE_FALSE in BASE_REVERTIBILITY for v7.17.0.
    # `revert` falls through the planner's "Other ops" None path; conceptually
    # it can re-trigger the underlying deploy_metadata case, but the planner
    # doesn't currently do that — so don't claim it.
    "deploy_metadata",
    "data_record_update",
    "data_record_create",
    # B4: soft data_record_delete now has a planner case — `jsc data undelete`.
    # (hard delete still returns None; the soft/hard split is handled in
    # compute_revertibility's special-case block + the planner's delete_mode gate.)
    "data_record_delete",
})

BASE_REVERTIBILITY: dict[str, Any] = {
    # Metadata: clean revert via redeploy of before-state
    "deploy_metadata": REVERTIBLE_TRUE,
    # Single-record DML
    "data_record_update": REVERTIBLE_TRUE,
    "data_record_create": REVERTIBLE_BEST_EFFORT,  # FK references can block delete-by-id
    "data_record_upsert": REVERTIBLE_FALSE,        # v7.17.0: forensic-only; planner not extended yet
    "data_record_delete": REVERTIBLE_BEST_EFFORT,  # B4: soft delete → `jsc data undelete` planner case (best-effort: purge/Recycle-Bin window may have elapsed); hard: special case below
    "data_record_import": REVERTIBLE_FALSE,        # v7.17.0: forensic-only; planner not extended yet
    # Bulk DML
    "data_bulk_update": REVERTIBLE_FALSE,          # v7.17.0: before-CSV captured; planner not extended
    "data_bulk_upsert": REVERTIBLE_FALSE,          # v7.17.0: forensic-only; planner not extended
    "data_bulk_delete": REVERTIBLE_FALSE,          # v7.17.0: before-CSV captured; planner not extended (soft+hard both)
    "data_bulk_import": REVERTIBLE_FALSE,          # v7.17.0: forensic-only; planner not extended
    # Permission management
    "org_assign_permset": REVERTIBLE_FALSE,        # v7.17.0: PSA-ids captured; planner not extended
    "org_assign_permsetlicense": REVERTIBLE_FALSE, # v7.17.0: PSL-ids captured; planner not extended
    # Quick deploy (validation-promote) — no metadata-before bytes captured in v7.17.0
    "deploy_quick_promote": REVERTIBLE_FALSE,      # Codex-R1-P1-07
    # Package operations
    "package_install": REVERTIBLE_FALSE,           # uninstall is separate op + may not be available
    "package_uninstall": REVERTIBLE_FALSE,         # package code no longer available unless re-installable
    # Anonymous Apex — fundamentally NOT auto-revertible
    "apex_run": REVERTIBLE_FALSE,
    # Codex-R2-P2-02: revert is conceptually re-deploy of metadata-before, but
    # revert_planner.build_revert_command's "Other ops" fallthrough returns None
    # for operation_type='revert' (no explicit case). Downgrade to False until
    # the planner is extended to handle revert-of-revert (which would recurse
    # to the underlying deploy_metadata case).
    "revert": REVERTIBLE_FALSE,
}


def _forensic_recovery_path(operation_type: str) -> str:
    """v7.17.0 manual-recovery prose for op_types whose planner is not yet implemented.

    Captured forensic state lives in the snapshot bundle; refer the operator to the
    specific payload key(s) needed for manual revert.
    """
    paths = {
        "data_record_upsert": (
            "Snapshot captured payload.before_row + before_record_id (if existed). "
            "If upsert_mode=='update': manually `sf data update record --record-id <before_record_id> "
            "--values <reapply before_row>`. If upsert_mode=='insert': manually "
            "`sf data delete record --record-id <after_record_id>`."
        ),
        "data_record_import": (
            "Snapshot captured payload.inserted_record_ids if visible from sf import tree output. "
            "Manual revert: `sf data delete record --record-id <id>` for each, in reverse-FK order."
        ),
        "data_bulk_update": (
            "Snapshot captured payload.before_csv. Manual revert: "
            "`sf data update bulk --file <before_csv> --sobject <obj>`."
        ),
        "data_bulk_upsert": (
            "Snapshot captured payload.before_csv (existing rows BEFORE the upsert). "
            "Manual revert is split: (a) for rows that existed before, reapply before_csv via "
            "`sf data update bulk`; (b) for rows newly INSERTED by the upsert, query post-state "
            "by external-id then `sf data delete bulk` those IDs."
        ),
        "data_bulk_delete": (
            "Snapshot captured payload.before_csv (the full row content of all deleted records). "
            "Manual revert: `sf data import bulk --file <before_csv> --sobject <obj>` — original "
            "IDs and CreatedById/CreatedDate audit fields cannot be exactly preserved."
        ),
        "data_bulk_import": (
            "Snapshot captured payload.input_snapshot_path (copy of the import CSV), "
            "payload.bulk_job_id, payload.after_success_csv (from `sf data bulk results`), "
            "AND payload.inserted_record_ids (extracted from the success CSV). "
            "Manual revert: build a delete file from payload.inserted_record_ids and "
            "`sf data delete bulk --file <delete.csv> --sobject <obj>`."
        ),
        "org_assign_permset": (
            "Snapshot captured payload.created_psa_ids. Manual revert: `sf data delete record "
            "--sobject PermissionSetAssignment --record-id <id>` for each captured PSA id."
        ),
        "org_assign_permsetlicense": (
            "Snapshot captured payload.created_psl_ids. Manual revert: `sf data delete record "
            "--sobject PermissionSetLicenseAssign --record-id <id>` for each captured PSL id."
        ),
        "revert": (
            "Revert is conceptually a re-deploy of the parent snapshot's "
            "metadata-before bytes. v7.17.0 planner does not implement "
            "revert-of-revert (the 'Other ops' fallthrough returns None for "
            "operation_type='revert'). Manual recovery: load the revert "
            "snapshot's manifest, find parent_snapshot_id, and `jsc deploy "
            "--source-dir <parent>/metadata-before` directly."
        ),
        "data_record_delete": (
            "Soft-deleted record sits in Recycle Bin and is theoretically "
            "undeleteable, but v7.17.0 ships no `sf data undelete` equivalent "
            "in the planner. Manual revert: `sf apex run` with anonymous Apex "
            "`undelete [SELECT Id FROM <obj> WHERE Id='<record_id>' ALL ROWS];`. "
            "v7.17.1 candidate: ship undelete helper in revert_planner."
        ),
        "deploy_quick_promote": (
            "Quick-deploy promotion captured only the validation job's expected component "
            "list (payload.expected_components) — not metadata-before bytes. Manual revert: "
            "(a) review expected_components for the impacted metadata types; (b) retrieve "
            "the prior production state from an earlier snapshot or git tag; (c) `jsc deploy` "
            "that earlier state. Recommended pre-promotion practice: run `jsc deploy --dry-run` "
            "against the validation job's component manifest BEFORE promotion to create a "
            "metadata-before snapshot that v7.17.0's normal revert planner can use."
        ),
    }
    return paths.get(operation_type, (
        f"No automatic revert path defined for {operation_type!r} in v7.17.0. "
        f"Snapshot retained for forensic review under JSC_REVERT_DIR."
    ))


def _has_restorable_before_state(payload: dict) -> bool:
    """True only if at least one component has bytes that could be redeployed.

    A non-empty payload["files"] is NOT sufficient, which is the hole Codex
    found in the first version of this gate. snapshot_pre emits an entry for
    EVERY component it was asked about, tagged `present | absent |
    retrieve_failed | unknown`, and wrappers/deploy.py copies all of them into
    payload["files"]. So an all-net-new deploy (every component `absent`) or a
    wholly failed retrieve produces a long files list containing nothing that
    can be restored — and `checksum` / `filePath` are populated only when the
    state is `present`.

    Case-insensitive on purpose: snapshot_pre writes lowercase `present`, but
    the states originate in `sf` JSON where capitalisation has changed across
    CLI versions, and a gate that silently flips open on a capitalisation
    change is not a gate.
    """
    files = payload.get("files")
    if not isinstance(files, list):
        return False
    return any(
        isinstance(f, dict) and str(f.get("before_state", "")).strip().lower() == "present"
        for f in files
    )


def compute_revertibility(operation_type: str, payload: dict) -> dict[str, Any]:
    """Returns {automatic_revertible, manual_recovery_path, side_effects_warning}.

    automatic_revertible is TRISTATE:
      - True: full automatic revert
      - False: one-way operation; manual_recovery_path explains
      - "best-effort": auto attempts revert; FK / sharing / automation may block
    """
    if operation_type == "data_record_create":
        record_id = payload.get("record_id")
        if not isinstance(record_id, str) or not re.fullmatch(r"[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?", record_id):
            return {
                "automatic_revertible": REVERTIBLE_FALSE,
                "manual_recovery_path": (
                    "No valid created record ID was captured. Review the saved command result "
                    "and identify the exact created record before attempting recovery. "
                    "Creation may have succeeded; do not repeat creation to recover missing evidence."
                ),
                "side_effects_warning": ["Deletion recovery requires the exact created record ID; missing evidence does not mean no record was created."],
            }

    if operation_type == "org_assign_permset" and not payload.get("created_psa_ids"):
        return {
            "automatic_revertible": REVERTIBLE_FALSE,
            "manual_recovery_path": (
                "The command response did not capture PermissionSetAssignment IDs. "
                "Review the saved result and identify the exact assignments created by this "
                "operation before manually removing any assignment. Assignment creation may "
                "have succeeded; absent IDs are not evidence that nothing changed."
            ),
            "side_effects_warning": ["Automatic assignment undo is not implemented; exact created-assignment IDs are unavailable."],
        }

    if operation_type == "data_record_update":
        selected = selected_before_values(payload)
        if not selected or all(value is None for value in selected.values()) or any(
            "'" in str(value) for value in selected.values() if value is not None
        ):
            return {
                "automatic_revertible": REVERTIBLE_FALSE,
                "manual_recovery_path": (
                    "The original update field scope or representable before-values are missing. "
                    "Review the original --values and payload.before_row, then restore only those "
                    "explicitly updated fields. Do not submit the entire captured record. "
                    "Null and quoted values may require another Salesforce update method."
                ),
                "side_effects_warning": ["A full captured row is forensic context, not an editable restore payload."],
            }
        warnings = [
            "Only explicitly updated fields are restored; unrelated fields and automation side effects are not undone.",
            "Restoring these fields replaces their current values, including any later edits to the same fields.",
        ]
        partial_null = any(value is None for value in selected.values())
        if partial_null:
            warnings.append("Fields changed from null are not automatically restored to null; restore them manually.")
        return {
            "automatic_revertible": REVERTIBLE_BEST_EFFORT if partial_null else REVERTIBLE_TRUE,
            "manual_recovery_path": "Restore the explicitly updated null before-values manually after the non-null fields." if partial_null else None,
            "side_effects_warning": warnings,
        }

    # ── Special case: deploy_metadata WITHOUT proven capture ──────────────
    # BASE_REVERTIBILITY promises deploy_metadata is revertible. That promise is
    # only good if a before-state was actually captured, and it frequently is
    # not: wrappers/deploy.py pre-retrieves ONLY when --metadata is passed, so
    # --source-dir and --manifest deploys — the most common forms — reach here
    # having captured nothing. The planner then finds no metadata-before
    # directory and returns None, so the operator learns the truth at revert
    # time, on whatever org they just changed.
    #
    # Conservative by construction (Codex R2-IMP-03): payload["files"] being
    # non-empty is treated as the ONLY evidence of capture. It is not proof of
    # COMPLETE capture — snapshot_pre classifies just what the retrieve returned
    # and never compares against the resolved target set — so this gate is
    # necessary, not sufficient. Completeness is deferred work (plan-v3 V3-2).
    # Over-reporting False costs a manual recovery path; over-reporting True
    # costs the change. Those are not symmetric.
    if operation_type == "deploy_metadata" and not _has_restorable_before_state(payload):
        sel = payload.get("selectors") if isinstance(payload.get("selectors"), dict) else {}
        used = [n for n, k in (("--metadata", "metadata_args"),
                               ("--manifest", "manifest_paths"),
                               ("--source-dir", "source_dirs"),
                               ("--pre-destructive-changes", "destructive_manifest_paths"))
                if sel.get(k)]
        # WHY the before-state is missing decides what the operator should do,
        # and there are two different reasons. Until 2026-07-29 this emitted one
        # canned string that assumed the selector was at fault, which produced
        # the self-contradiction "the retrieve runs only for --metadata
        # selectors; this deploy used --metadata" on every net-new deploy —
        # confusing guidance at exactly the moment it is being read for help.
        # Tolerate the same corrupt shapes _has_restorable_before_state does:
        # payload["files"] may not be a list at all, and its entries may not be
        # dicts. The existing fixtures feed both ("corrupt", ["not-a-dict", 42])
        # and caught the first version of this branch, which assumed dicts and
        # raised AttributeError — turning a guidance-message improvement into a
        # crash on the exact malformed input the gate exists to survive.
        raw = payload.get("files")
        files = [f for f in raw if isinstance(f, dict)] if isinstance(raw, list) else []
        absent = [f for f in files if f.get("before_state") == "absent"]
        net_new = bool(files) and len(absent) == len(files)

        if net_new:
            names = ", ".join(f.get("fullName", "?") for f in absent[:4])
            why = (
                "Every component in this deploy was ABSENT from the org "
                f"beforehand ({names}{'…' if len(absent) > 4 else ''}), so it "
                "CREATED them and there is no prior state to restore. Reverting "
                "a create means DELETING, which this planner does not do — it "
                "emits no destructiveChanges (plan-v3 V3-2 item 5). To undo it, "
                "delete the components yourself via a destructiveChanges.xml "
                "deploy, having confirmed nothing else now depends on them.")
        elif "--metadata" in used:
            why = (
                "This deploy DID use --metadata, so the selector is not the "
                "problem — the pre-snapshot retrieve returned nothing usable. "
                "Check phases.pre_snapshot.error and metadata-before/ in the "
                "snapshot bundle; a failed retrieve and an empty org are "
                "indistinguishable downstream, so this fails closed.")
        else:
            why = (
                "The pre-snapshot retrieve runs only for --metadata selectors; "
                "this deploy used "
                + (", ".join(used) if used else "no recognised selector")
                + ". To get automatic revert on a future deploy, express it "
                "with --metadata.")

        return {
            "automatic_revertible": REVERTIBLE_FALSE,
            "manual_recovery_path": (
                "No before-state was captured for this deploy, so there is "
                "nothing to redeploy and `jsc revert exec` will refuse. "
                + why
                + " Recover manually: identify the changed components from "
                "payload.selectors and the deploy result, retrieve their current "
                "state from a comparable org, and redeploy."
            ),
            "side_effects_warning": [
                "Snapshot is FORENSIC ONLY — it records what was deployed, not how to undo it",
                "Net-new components would not be removed by a revert even if before-state existed "
                "(planner emits no destructiveChanges — plan-v3 V3-2)",
            ],
        }

    if operation_type == "deploy_metadata":
        from .metadata_scope import MetadataScopeError, selected_records
        try:
            selected_records(payload)
        except MetadataScopeError as exc:
            return {
                "automatic_revertible": REVERTIBLE_FALSE,
                "manual_recovery_path": f"Exact metadata recovery scope unavailable: {exc}",
                "side_effects_warning": [
                    "Retrieved parent containers are context, not implicitly selected for recovery.",
                    "Review the original selectors and captured files; an incomplete capture cannot undo all components.",
                ],
            }

    # ── Special case: hard-delete (overrides the True default) ────────────
    if operation_type in ("data_record_delete", "data_bulk_delete"):
        if payload.get("delete_mode") == "hard":
            return {
                "automatic_revertible": REVERTIBLE_FALSE,
                "manual_recovery_path": (
                    "Hard-deleted records bypass Recycle Bin. Snapshot persisted "
                    "before-state CSV at payload.before_csv_path. Restore via "
                    "`sf data import bulk` from the before-state, accepting that "
                    "original Salesforce IDs, audit fields (CreatedById, "
                    "CreatedDate, LastModifiedDate), sharing rules, child "
                    "relationships, attachments/files, and any automation "
                    "side-effects from the original creation cannot be exactly "
                    "reproduced."
                ),
                "side_effects_warning": [
                    "Original record IDs lost — any external system holding old IDs breaks",
                    "Audit fields reset to import time",
                    "Child records (attachments, history, etc.) NOT restored",
                ],
            }

    # ── Special case: SOFT data_record_delete (B4 — undelete planner case) ─
    # A soft delete sits in the Recycle Bin and can be undeleted via the
    # `jsc data undelete` wrapper. Best-effort because the Recycle Bin is not
    # permanent: a hard purge or the 15-day retention window elapsing makes the
    # record unrecoverable. (data_bulk_delete soft stays FALSE — no bulk
    # undelete planner case.)
    if operation_type == "data_record_delete" and payload.get("delete_mode") == "soft":
        return {
            "automatic_revertible": REVERTIBLE_BEST_EFFORT,
            "manual_recovery_path": (
                "Best-effort automatic revert: `jsc data undelete` runs anonymous "
                "Apex `undelete [SELECT Id FROM <obj> WHERE Id = :rid ALL ROWS]` to "
                "restore the soft-deleted record from the Recycle Bin. If the "
                "undelete fails (record purged from the Recycle Bin, or the 15-day "
                "retention window elapsed), the record is unrecoverable from the "
                "snapshot — re-create it from payload.before_row instead, accepting "
                "that the original Salesforce ID and audit fields cannot be "
                "exactly reproduced."
            ),
            "side_effects_warning": [
                "undelete fails if the record was purged from the Recycle Bin or "
                "the 15-day retention window elapsed",
                "Cascade-undelete restores child records that were cascade-deleted "
                "with the parent — but only those still in the Recycle Bin",
                "Sharing recalculation runs on undelete; manual shares created "
                "before the delete are NOT restored",
            ],
        }

    # ── Special case: apex_run ────────────────────────────────────────────
    if operation_type == "apex_run":
        return {
            "automatic_revertible": REVERTIBLE_FALSE,
            "manual_recovery_path": (
                "Apex anonymous execution can perform arbitrary DML. Snapshot "
                "captured: input apex code (apex_input_path), debug log id "
                "(debug_log_id), declared touched objects + best-effort inferred "
                "objects from log (touched_objects_*), per-object row counts + "
                "max LastModifiedDate before AND after. To recover: review the "
                "debug log for actual DML operations performed, manually "
                "construct reversal Apex script. There is NO automatic apex "
                "revert path."
            ),
            "side_effects_warning": [
                "Inferred touched_objects is best-effort — DML on objects not "
                "appearing in the debug log will not be detected",
                "Row count delta does NOT identify which specific records changed",
                "Async operations triggered by Apex (queueables, future methods) "
                "may complete after snapshot capture",
            ],
        }

    # ── Special case: package_install ─────────────────────────────────────
    if operation_type == "package_install":
        return {
            "automatic_revertible": REVERTIBLE_FALSE,
            "manual_recovery_path": (
                "Package install captured: package_id, subscriber_package_version_id, "
                "install_request_id, install_status, packages_before_install. "
                "Reverting requires: (1) `sf package uninstall` is a separate "
                "operation that may not always be available (package may not "
                "support uninstall, or org may have data depending on package "
                "fields). (2) Even successful uninstall does NOT restore data "
                "in package fields, custom config that referenced package "
                "metadata, or any side-effects from package post-install scripts."
            ),
            "side_effects_warning": [
                "Uninstall may fail if org has data in package custom fields/objects",
                "Package metadata referenced by org config (FlexiPages, validation "
                "rules, automation) breaks if uninstalled",
                "No automatic data restoration for fields lost during uninstall",
            ],
        }

    # ── Default lookup ────────────────────────────────────────────────────
    classification = BASE_REVERTIBILITY.get(operation_type, REVERTIBLE_FALSE)

    if classification == REVERTIBLE_TRUE:
        warnings: list[str] = []
        # SEC-9 (audit 2026-05-30): data_record_update is fully revertible for
        # value->value changes, but a field the forward op changed null->value
        # is NOT automatically restored to null — revert_planner skips None
        # before-values (emitting a blank token like '#N/A' to
        # `sf data update record --values` is unsafe; the sf CLI writes it
        # literally). Surface the gap so the TRUE classification doesn't
        # overpromise.
        if operation_type == "data_record_update":
            warnings.append(
                "Fields the operation changed from null to a value are NOT "
                "automatically restored to null (value->value changes revert "
                "fully). To null such a field, restore it manually after the revert."
            )
        return {
            "automatic_revertible": REVERTIBLE_TRUE,
            "manual_recovery_path": None,
            "side_effects_warning": warnings,
        }

    if classification == REVERTIBLE_BEST_EFFORT:
        # FK / sharing / automation caveats for create/upsert/import paths
        return {
            "automatic_revertible": REVERTIBLE_BEST_EFFORT,
            "manual_recovery_path": (
                "Best-effort automatic revert: wrapper attempts delete-by-id of "
                "created records. Failures expected when:\n"
                "  - Foreign-key references exist (REFERENCE_FOUND blocks delete)\n"
                "  - Cascade-deletes propagate beyond the snapshotted records\n"
                "  - Sharing rules / files / attachments need separate handling\n"
                "  - Automation (triggers, flows) created downstream side-effects\n"
                "Manual recovery: review snapshot manifest payload.records.created "
                "for the specific record IDs; query for inbound FKs via Tooling "
                "EntityDefinition before invoking revert."
            ),
            "side_effects_warning": [
                "Created records may be referenced by records created in later operations — "
                "delete-by-id will fail with REFERENCE_FOUND",
                "Cascade-delete behavior depends on each FK relationship's cascade setting",
                "External system references to created IDs break on delete",
            ],
        }

    # REVERTIBLE_FALSE — distinguish v7.17.0 known-deferred from truly unknown
    if operation_type in BASE_REVERTIBILITY:
        return {
            "automatic_revertible": REVERTIBLE_FALSE,
            "manual_recovery_path": _forensic_recovery_path(operation_type),
            "side_effects_warning": [
                "v7.17.0: planner not yet extended for this operation_type. "
                "Snapshot retains forensic state; revert is manual.",
            ],
        }
    return {
        "automatic_revertible": REVERTIBLE_FALSE,
        "manual_recovery_path": (
            f"Unknown operation_type {operation_type!r}: no automatic revert "
            f"path defined. Manual recovery only — review snapshot manifest "
            f"for context."
        ),
        "side_effects_warning": [
            f"operation_type {operation_type!r} not in BASE_REVERTIBILITY; "
            f"capability classification deferred to manual review",
        ],
    }


def effective_capabilities(snap: dict) -> dict[str, Any]:
    """Capabilities for a LOADED snapshot, re-derived rather than trusted.

    Every consumer of a persisted manifest must go through this. A stored
    `automatic_revertible` is a claim made by whichever version of the code
    wrote it, and snapshots written before the capture-aware deploy gate carry
    `true` for deploys that captured nothing. Trusting that value is precisely
    the P0 this module was fixed for, and it is most dangerous in the executor,
    where it gates whether a revert actually runs.

    Falls back to the stored block if recomputation raises — an unreadable
    manifest should not crash `revert-show`. It returns the stored value
    unchanged when the two agree, so callers can compare identity to detect a
    stale snapshot.
    """
    # isinstance-guarded: a hand-edited or truncated manifest can carry a
    # non-dict here, and every caller immediately does .get() on the result.
    # Falling back to a raw stored value would turn a bad manifest into an
    # AttributeError in the middle of `revert-show` (Antigravity R3).
    stored = snap.get("revert_capabilities")
    stored = stored if isinstance(stored, dict) else {}
    try:
        payload = snap.get("payload") or {}
        if snap["operation_type"] == "data_record_update" and isinstance(payload, dict) and "fields_updated" not in payload:
            payload = {**payload, "fields_updated": requested_fields(payload, snap.get("wrapper_command"))}
        return compute_revertibility(snap["operation_type"], payload)
    except Exception as e:
        # FAIL CLOSED. The first version returned `stored` here, which is
        # fail-OPEN: validate_envelope only checks that `payload` exists, so a
        # schema-valid manifest whose `selectors` is a list makes
        # compute_revertibility raise — and the fallback then handed back a
        # stored `true` for precisely the malformed snapshot least deserving of
        # trust (Codex R3-P1-03). A safety gate that opens when it cannot
        # evaluate is not a safety gate.
        return {
            "automatic_revertible": REVERTIBLE_FALSE,
            "manual_recovery_path": (
                "Revertibility could not be evaluated for this snapshot "
                f"({type(e).__name__}: {str(e)[:120]}). The manifest is "
                "malformed or was written by an incompatible version. Treating "
                "it as NOT revertible; inspect the bundle by hand before acting."
            ),
            "side_effects_warning": [
                "Capability evaluation FAILED — this is not a positive finding of non-revertibility",
                f"stored value was {stored.get('automatic_revertible')!r} and is not being trusted",
            ],
        }
