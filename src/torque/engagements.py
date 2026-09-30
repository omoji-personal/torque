"""Engagements: a client or an internal initiative, tracked the same way.

Clients keep their existing folder and records. Initiatives live in
initiatives/<slug>/ with a binding.json (identity, kind, repositories) and a
state/engagement.json (title, owner, lifecycle). Client-only powers (org,
consent, approvals, connected mode, verify-deploy) are refused for initiatives.
"""
from __future__ import annotations

from datetime import date
import getpass
import json
import re
import tempfile
from pathlib import Path
from uuid import uuid4

from . import workspace as ws

KINDS = ("client", "initiative")
FOLDERS = {"client": "clients", "initiative": "initiatives"}
LIFECYCLE = ("active", "paused", "closed", "archived")
_SHARED = frozenset({"sessions", "changes", "context", "handoff"})
CAPABILITIES = {
    "client": _SHARED | {"org", "consent", "approvals", "connected", "verify_deploy"},
    "initiative": _SHARED,
}
PRIVATE_RULE = "/initiatives/"
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def require(kind: str, capability: str) -> None:
    if kind not in CAPABILITIES:
        raise ws.WorkspaceError(f"unknown engagement kind: {kind}")
    if capability not in CAPABILITIES[kind]:
        raise ws.WorkspaceError(f"{capability.replace('_', ' ')} is for clients only; an {kind} cannot use it")


def _actor() -> str:
    try:
        return getpass.getuser()
    except Exception:  # No login name available (containers); record that honestly.
        return "unknown"


def ensure_private_rule(root: Path) -> None:
    """Keep initiatives out of git in a workspace whose .gitignore lists private paths."""
    ignore = ws._inside(root, root / ".gitignore")
    if not ignore.exists():
        return
    text = ignore.read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines()]
    if "*" in lines or PRIVATE_RULE in lines:
        return
    ws._atomic_replace_text(ignore, text.rstrip("\n") + "\n" + PRIVATE_RULE + "\n")


def add_initiative(workspace: str | Path, name: str, owner: str | None = None) -> Path:
    root, _ = ws.load_workspace(workspace)
    ws.require_writable(root)
    slug = ws.slug_for(name)
    if owner is not None and (not owner.strip() or any(c in owner for c in "\r\n\0")):
        raise ws.WorkspaceError("owner must be nonempty and on one line")
    ensure_private_rule(root)
    with ws._client_creation_lock(root):
        parent = ws._inside(root, root / "initiatives")
        parent.mkdir(mode=0o700, exist_ok=True)
        folder = ws._inside(root, parent / slug)
        if folder.exists():
            raise ws.WorkspaceError(f"initiative slug already exists: {slug}; no existing data was replaced")
        staging = ws._inside(root, root / ".torque" / "client-staging")
        staging.mkdir(mode=0o700, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=slug + "-", dir=staging) as temporary:
            pending = Path(temporary)
            for directory in ("sessions", "changes", "artifacts", "context", "config", "state"):
                (pending / directory).mkdir(mode=0o700)
            now = ws._now()
            ws._write_json(pending / "binding.json", {"schema": "torque.binding/1", "id": str(uuid4()),
                                                      "kind": "initiative", "slug": slug, "repositories": []})
            ws._write_json(pending / "state" / "engagement.json", {
                "schema": "torque.engagement/1", "name": name.strip(), "owner": owner.strip() if owner else None,
                "state": "active", "created_at": now,
                "history": [{"state": "active", "at": now, "by": _actor()}]})
            ws.atomic_write_new(pending / "context.md", f"# {name.strip()}\n\n"
                                "Record this initiative's goal, scope, decisions, owners and open questions "
                                "here. Keep credentials out of these notes.\n")
            if folder.exists():
                raise ws.WorkspaceError(f"initiative slug already exists: {slug}; no existing data was replaced")
            pending.rename(folder)
    return folder


def load_initiative(workspace: str | Path, name: str) -> tuple[Path, dict, dict]:
    root, firm = ws.load_workspace(workspace)
    slug = ws.slug_for(name)
    folder = ws._inside(root, root / "initiatives" / slug)
    binding = ws._read_json(ws._inside(root, folder / "binding.json"))
    state = ws._read_json(ws._inside(root, folder / "state" / "engagement.json"))
    if (binding.get("schema") != "torque.binding/1" or binding.get("kind") != "initiative"
            or binding.get("slug") != slug or not isinstance(binding.get("repositories"), list)):
        raise ws.WorkspaceError(f"invalid initiative binding: {folder / 'binding.json'}")
    if (state.get("schema") != "torque.engagement/1" or state.get("state") not in LIFECYCLE
            or not isinstance(state.get("name"), str) or not state["name"].strip()
            or not isinstance(state.get("history"), list)):
        raise ws.WorkspaceError(f"invalid initiative state: {folder / 'state' / 'engagement.json'}")
    config = {"kind": "initiative", "slug": slug, "name": state["name"], "id": binding.get("id"),
              "owner": state.get("owner"), "state": state["state"], "history": state["history"],
              "repositories": binding["repositories"], "created_at": state.get("created_at")}
    return folder, firm, config


def list_engagements(workspace: str | Path, kind: str | None = None) -> list[dict]:
    if kind is not None and kind not in KINDS:
        raise ws.WorkspaceError(f"unknown engagement kind: {kind}")
    root, _ = ws.load_workspace(workspace)
    rows = []
    if kind in (None, "client"):
        rows += [{"kind": "client", "slug": c["slug"], "name": c["name"], "state": "active", "owner": None}
                 for c in ws.list_clients(root)]
    if kind in (None, "initiative"):
        parent = ws._inside(root, root / "initiatives")
        if parent.is_dir():
            for child in sorted(parent.iterdir()):
                ws._inside(root, child)
                if child.is_dir() and (child / "binding.json").exists():
                    config = load_initiative(root, child.name)[2]
                    rows.append({"kind": "initiative", "slug": config["slug"], "name": config["name"],
                                 "state": config["state"], "owner": config["owner"]})
    return rows


def set_state(workspace: str | Path, name: str, state: str, *, reason: str | None = None,
              review_date: str | None = None, outcome: str | None = None) -> dict:
    if state not in LIFECYCLE:
        raise ws.WorkspaceError(f"unknown lifecycle state: {state}")
    if state == "paused" and not (reason and reason.strip()):
        raise ws.WorkspaceError("pausing needs a reason")
    if state == "closed" and not (outcome and outcome.strip()):
        raise ws.WorkspaceError("closing needs an outcome")
    if review_date:
        try:
            valid = bool(_ISO_DATE.fullmatch(review_date.strip())) and bool(date.fromisoformat(review_date.strip()))
        except ValueError:
            valid = False
        if not valid:
            raise ws.WorkspaceError("the review date must be an ISO 8601 date (YYYY-MM-DD)")
    folder, _, config = load_initiative(workspace, name)
    ws.require_writable(folder.parent.parent)
    path = folder / "state" / "engagement.json"
    record = ws._read_json(path)
    entry = {"state": state, "at": ws._now(), "by": _actor()}
    for key, value in (("reason", reason), ("review_date", review_date), ("outcome", outcome)):
        if value:
            entry[key] = value.strip()
    record["state"] = state
    record["history"] = [*record["history"], entry]
    ws._atomic_replace_text(path, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return load_initiative(workspace, name)[2]
