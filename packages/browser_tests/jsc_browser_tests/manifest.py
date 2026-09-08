"""Private run diagnostics with credential redaction and a bounded audit log.

Flow evidence can include client data. This is not a public export or a PII
anonymizer; keep manifests in the selected private workspace.
"""
from __future__ import annotations
from jsc_common.workspace import state_dir

import json
import os
from pathlib import Path
from dataclasses import asdict
from .diagnostics import redact

DEFAULT_AUDIT_LOG = None  # defaults resolved at call time

# Schema-bounded audit fields — anything else (incl. PII / raw payloads) is dropped.
_AUDIT_FIELDS = ("iso", "flow", "profile", "status", "score", "target_org", "action")


def write(path, cells, score, audit_entries, audit_log=None, extra=None) -> dict:
    """Write private run-manifest.json to `path` and append
    schema-bounded audit entries to the operator-private audit log (mode 0o600).
    `audit_log` overrides the default location (used by tests). Returns the manifest dict.
    """
    manifest = {
        "score": score,
        "cells": [
            {
                "flow": c.flow_name,
                "profile": c.profile,
                "status": c.overall_status,
                "error": c.error,
                "steps": [asdict(step) for step in c.steps],
                "side_effects": c.side_effects,
                "test_record_ids": _test_ids(c),
            }
            for c in cells
        ],
    }
    if extra:
        manifest.update(extra)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    manifest = redact(manifest)
    p.write_text(json.dumps(manifest, indent=2))
    p.chmod(0o600)
    _append_audit(audit_entries, Path(audit_log) if audit_log else state_dir("audit-logs") / "browser-suite.log")
    return manifest


def _test_ids(cell) -> list:
    rows = (cell.side_effects or {}).get("rows", [])
    return [r.get("Id") for r in rows if isinstance(r, dict) and r.get("Id")]


def _bounded(entry: dict) -> dict:
    return redact({k: entry.get(k) for k in _AUDIT_FIELDS if k in entry})


def _append_audit(audit_entries, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(log_path), os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    try:
        for e in audit_entries or []:
            os.write(fd, (json.dumps(_bounded(e)) + "\n").encode())
    finally:
        os.close(fd)
    os.chmod(log_path, 0o600)
