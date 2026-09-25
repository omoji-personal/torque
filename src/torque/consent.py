"""The client's written agreement to connected (stage 2) work, recorded by the
consultant at a real terminal. In a connected workspace the gate refuses org
access for a client without an active, signed-off record."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from uuid import uuid4

from . import workspace as ws

SCHEMA = "torque.consent/1"
FILE = "consent.json"
EVIDENCE_DIR = "consent-evidence"
DATA_CLASSES = ("metadata", "records", "debug_logs", "local_artifacts")
STATUSES = ("pending", "active", "suspended")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


def _require_operator(presence, confirm=None) -> None:
    """A person at a real terminal outside the session, who types back a code (the
    code is skipped only when a caller injects its own presence check)."""
    injected = presence is not None
    if presence is None:
        from .presence import operator_present as presence
    check = presence()
    if not check.ok:
        raise ws.WorkspaceError(f"consent is recorded by the consultant at a real terminal: {check.reason}")
    if confirm is not None or not injected:
        if confirm is None:
            from .presence import confirm_code as confirm
        if not confirm():
            raise ws.WorkspaceError("the confirmation code did not match; nothing was changed")


def _actor(workspace, presence, confirm, delegated, model_id, env, ancestors, getuid, root_owner=None):
    """(the delegated actor or None). The presence path is a15's, unchanged: it
    calls `_require_operator` exactly as before. The delegated path proves the
    caller is the workspace's named setup delegate (`delegation.delegated_actor`,
    an OS-account proof, not presence) and needs tier 2 already active (the
    default `require_tier2=True`: consent steps assume `ws.set_ai_access` already
    switched the workspace to connected, tier 2). `root_owner` is an injectable
    override for tests that cannot create a second real OS account."""
    if delegated:
        from . import delegation
        return delegation.delegated_actor(workspace, "setup", model_id=model_id, getuid=getuid, env=env,
                                          ancestors=ancestors, root_owner=root_owner)
    _require_operator(presence, confirm)
    return None


def _path(workspace, client) -> tuple[Path, Path]:
    folder, _, _ = ws.load_client(workspace, client)
    return folder, ws._inside(folder, folder / FILE)


def load_consent(workspace, client) -> dict | None:
    _, path = _path(workspace, client)
    if not path.is_file():
        return None
    item = ws._read_json(path)
    if item.get("schema") != SCHEMA:
        raise ws.WorkspaceError(f"invalid consent record: {path}")
    return item


def _resolver():
    from jsc_revert.org_detect import resolve_org
    return resolve_org


def _user() -> str:
    from jsc_revert.intent_marker import _current_user_name
    return _current_user_name()


def _save(path: Path, item: dict, *, delegated: bool = False) -> None:
    text = json.dumps(item, indent=2, ensure_ascii=False) + "\n"
    if path.exists():
        ws._atomic_replace_text(path, text)
    else:
        ws._write_json(path, item)
    if os.name != "nt":
        # F12: the a15 owner path keeps 0600 (unchanged). A setup delegate's own
        # uid writes 0644 instead, so the different account the gate and hooks
        # later run as can read it; the harness chowns it to root afterward.
        path.chmod(0o644 if delegated else 0o600)


def record_consent(workspace, client, agreed_on: str, evidence, data_allowed: list[str], orgs: list[str],
                   suspend_contacts: list[str], presence=None, resolve=None, *, delegated: bool = False,
                   model_id: str | None = None, env=None, ancestors=None, getuid=None, root_owner=None) -> dict:
    """Record (or replace) the agreement. A new record is pending until a second
    reviewer signs off. `delegated=True`: the workspace's setup delegate is
    recording this in place of the consultant at a real terminal; the record
    gains `recorded_by_actor` (the delegate's identity and kind)."""
    actor = _actor(workspace, presence, None, delegated, model_id, env, ancestors, getuid, root_owner)
    if not isinstance(agreed_on, str) or not _DATE.fullmatch(agreed_on):
        raise ws.WorkspaceError("agreed_on must be YYYY-MM-DD")
    unknown = [c for c in data_allowed if c not in DATA_CLASSES]
    if unknown or not data_allowed:
        raise ws.WorkspaceError(f"unknown data class: {', '.join(unknown) or 'none given'} "
                                f"(choose from {', '.join(DATA_CLASSES)})")
    if not orgs:
        raise ws.WorkspaceError("list at least one approved org alias")
    resolve = resolve or _resolver()
    approved = []
    for alias in dict.fromkeys(orgs):
        info = resolve(alias)
        if info is None:
            raise ws.WorkspaceError(f"could not resolve org alias {alias!r}; authenticate it first")
        entry = {"alias": alias, "org_id_18": info.org_id_18, "kind": info.detected_org_type}
        instance = getattr(info, "instance_url", None)
        if isinstance(instance, str) and instance:
            entry["instance_url"] = instance
        approved.append(entry)
    folder, path = _path(workspace, client)
    source = Path(evidence).expanduser().resolve()
    if not source.is_file():
        raise ws.WorkspaceError(f"agreement file does not exist: {source}")
    target_dir = ws._inside(folder, folder / EVIDENCE_DIR)
    target_dir.mkdir(mode=0o700, exist_ok=True)
    target = ws._inside(folder, target_dir / (uuid4().hex + re.sub(r"[^A-Za-z0-9.]", "", source.suffix)[:16]))
    shutil.copyfile(source, target)
    if os.name != "nt":
        target.chmod(0o600)
    item = {"schema": SCHEMA, "client": ws.slug_for(client), "status": "pending", "agreed_on": agreed_on,
            "evidence": {"path": target.relative_to(folder).as_posix(),
                         "sha256": hashlib.sha256(target.read_bytes()).hexdigest()},
            "data_allowed": list(dict.fromkeys(data_allowed)), "approved_orgs": approved,
            "suspend_contacts": [c.strip() for c in suspend_contacts if c and c.strip()],
            "reviewer": None, "recorded_by": _user(), "recorded_at": ws._now()}
    if actor is not None:
        item["recorded_by"] = actor.account
        item["recorded_by_actor"] = actor.as_dict()
    _save(path, item, delegated=delegated)
    return item


def _update(workspace, client, presence, change, *, delegated: bool = False, model_id: str | None = None,
           env=None, ancestors=None, getuid=None, root_owner=None) -> dict:
    actor = _actor(workspace, presence, None, delegated, model_id, env, ancestors, getuid, root_owner)
    _, path = _path(workspace, client)
    item = load_consent(workspace, client)
    if item is None:
        raise ws.WorkspaceError("no consent record; run torque client consent record first")
    change(item, actor)
    _save(path, item, delegated=delegated)
    return item


def sign_off(workspace, client, reviewer: str, presence=None, *, delegated: bool = False,
            model_id: str | None = None, env=None, ancestors=None, getuid=None, root_owner=None) -> dict:
    """The second reviewer's sign-off. It activates a pending record; it does
    not lift a suspension (record the agreement again for that). `delegated=True`:
    the workspace's setup delegate signs off in place of the consultant; the
    reviewer entry gains `signed_off_by` (the delegate's identity and kind)."""
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ws.WorkspaceError("name the second reviewer")

    def apply(item, actor):
        item["reviewer"] = {"name": reviewer.strip(), "signed_off_at": ws._now(),
                            **({"signed_off_by": actor.as_dict()} if actor else {})}
        if item.get("status") == "pending":
            item["status"] = "active"
    return _update(workspace, client, presence, apply, delegated=delegated, model_id=model_id, env=env,
                   ancestors=ancestors, getuid=getuid, root_owner=root_owner)


def suspend(workspace, client, presence=None) -> dict:
    def apply(item, actor):
        item["status"] = "suspended"
        item["suspended_at"] = ws._now()
    return _update(workspace, client, presence, apply)


def _orgs(consent: dict | None) -> list[dict]:
    orgs = consent.get("approved_orgs") if isinstance(consent, dict) else None
    if not isinstance(orgs, list):
        return []
    return [o for o in orgs if isinstance(o, dict) and isinstance(o.get("alias"), str)
            and isinstance(o.get("org_id_18"), str) and isinstance(o.get("kind"), str)]


def _signed_off(reviewer) -> bool:
    if not isinstance(reviewer, dict) or not isinstance(reviewer.get("name"), str) or not reviewer["name"].strip():
        return False
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(reviewer.get("signed_off_at"))).utcoffset() is not None
    except ValueError:
        return False


def consent_problems(consent: dict | None, client: str | None = None) -> list[str]:
    """Why this record does not permit org access; empty when it does. With `client`
    (a slug), the record must be that client's."""
    if consent is None:
        return ["no consent record"]
    problems = []
    if consent.get("schema") != SCHEMA:
        problems.append("the consent record has an unknown schema")
    if client is not None and consent.get("client") != client:
        problems.append("the consent record belongs to another client")
    status = consent.get("status")
    if status == "suspended":
        problems.append("consent is suspended")
    if not _signed_off(consent.get("reviewer")):
        problems.append("no second-reviewer sign-off")
    elif status != "active" and status != "suspended":
        problems.append(f"consent status is {status!r}")
    orgs = consent.get("approved_orgs")
    if not isinstance(orgs, list) or not orgs or len(_orgs(consent)) != len(orgs):
        problems.append("no approved orgs" if not orgs else "the approved org list is malformed")
    data = consent.get("data_allowed")
    if not isinstance(data, list) or any(c not in DATA_CLASSES for c in data):
        problems.append("the data classes are malformed")
    return problems


def approved_org(consent: dict | None, alias: str | None) -> dict | None:
    if not alias:
        return None
    return next((o for o in _orgs(consent) if o["alias"] == alias), None)


def data_allowed(consent: dict | None) -> set[str]:
    data = consent.get("data_allowed") if isinstance(consent, dict) else None
    return {c for c in data if c in DATA_CLASSES} if isinstance(data, list) else set()
