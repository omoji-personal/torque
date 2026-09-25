"""Delegated approvers and setup delegates (tier 2 only).

A connected workspace may name, in workspace.json, a grant approver and a setup
delegate: OS accounts other than the AI session's, each recorded as a person
("human") or an automated reviewer ("ai"). A delegated step skips the terminal
presence check because the operating system proves who runs it: the caller's uid
must be the named delegate's, workspace.json must be owned by root or that
delegate and writable by no one else, and the caller must not be part of an AI
session. The consultant's own grants keep the presence check and typed code.
See docs/delegated-approver.md."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re

from . import workspace as ws

ROLES = ("approver", "setup")
KINDS = ("ai", "human")
MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}\Z")
# Files each delegated setup step may write, relative to the workspace (fnmatch
# patterns; "*" is one client slug or file name). docs/delegated-approver.md.
SETUP_WRITES = {
    "ai-access": ("workspace.json", ".claude/rules/production-approval.md"),
    "permissions": (".claude/settings.json", ".claude/torque-permissions.json"),
    "consent-record": ("clients/*/consent.json", "clients/*/consent-evidence/*"),
    "consent-sign-off": ("clients/*/consent.json",),
}


class Refusal(ws.WorkspaceError):
    """A refusal with a stable reason class an automated approver can act on."""

    def __init__(self, reason_class: str, message: str):
        super().__init__(message)
        self.reason_class = reason_class


@dataclass(frozen=True)
class Actor:
    kind: str
    account: str
    uid: int | None
    model: str | None
    via: str

    def as_dict(self) -> dict:
        return asdict(self)


def _account(uid: int | None) -> str:
    if uid is None:
        return os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"
    import pwd
    return pwd.getpwuid(uid).pw_name


def human_actor() -> Actor:
    uid = os.getuid() if hasattr(os, "getuid") else None
    return Actor("human", _account(uid), uid, None, "presence")


def delegate_for(config: dict, role: str) -> dict | None:
    delegates = config.get("delegates") if isinstance(config, dict) else None
    item = delegates.get(role) if isinstance(delegates, dict) else None
    if (not isinstance(item, dict) or item.get("kind") not in KINDS or type(item.get("uid")) is not int
            or not isinstance(item.get("account"), str) or not item["account"]):
        return None
    return {"account": item["account"], "uid": item["uid"], "kind": item["kind"]}


def delegated_tier2(config: dict) -> bool:
    """True when this workspace runs tier 2 (owner-uid) approvals and its named
    approver delegate is the workspace's approver account. The "tier 2 with a
    named-delegate approver" predicate (F26): the permissions profile, grant,
    denial, launch-binding and delegated-launch code share this instead of
    each repeating config.get("approval_verify") == "owner-uid" plus the
    matching delegate_for(config, "approver")."""
    approver = delegate_for(config, "approver")
    return (config.get("approval_verify") == "owner-uid" and approver is not None
            and approver["uid"] == config.get("approver_uid"))


def set_delegate(workspace, role, account, uid, kind, *, presence=None, confirm=None, geteuid=None,
                 lookup=None) -> Path:
    """Name a delegate. The owner at a real terminal, or an administrator (uid 0)
    provisioning the workspace, may do this; nothing else can."""
    if role not in ROLES:
        raise ws.WorkspaceError(f"unknown delegate role {role!r}; choose approver or setup")
    if kind not in KINDS:
        raise ws.WorkspaceError(f"unknown delegate kind {kind!r}; choose ai or human")
    if not hasattr(os, "getuid"):
        raise ws.WorkspaceError("delegates need tier 2 approvals, which need numeric account IDs (not Windows)")
    if lookup is None:
        import pwd
        lookup = lambda name: pwd.getpwnam(name).pw_uid
    try:
        real = lookup(account)
    except KeyError:
        raise ws.WorkspaceError(f"no OS account named {account!r}") from None
    if real != uid:
        raise ws.WorkspaceError(f"{account} has uid {real}, not {uid}")
    if (geteuid or os.geteuid)() == 0:
        actor = Actor("human", "root", 0, None, "administrator")
    else:
        from .presence import require_presence
        require_presence("delegates are named by the owner", presence=presence, confirm=confirm,
                         error=ws.WorkspaceError)
        actor = human_actor()
    root, config = ws.load_workspace(workspace)
    delegates = dict(config.get("delegates") or {})
    delegates[role] = {"account": account, "uid": uid, "kind": kind}
    config["delegates"] = delegates
    config["delegates_changed_at"] = ws._now()
    config["delegates_changed_by"] = actor.as_dict()
    ws._atomic_replace_text(ws._inside(root, root / ws.CONFIG),
                            json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    return root


def _model(kind: str, model_id) -> str | None:
    if kind == "ai":
        if not isinstance(model_id, str) or not MODEL_RE.fullmatch(model_id):
            raise Refusal("not-delegated", "an AI delegate names its model with --model-id "
                                           "(letters, digits and . _ : / @ + -)")
        return model_id
    if model_id is not None:
        raise Refusal("not-delegated", "--model-id applies only to an AI delegate")
    return None


def delegated_actor(workspace, role, *, model_id=None, require_tier2=True, getuid=None, env=None,
                    ancestors=None) -> Actor:
    """Prove the caller is this workspace's delegate for `role`, outside any AI
    session. Raises Refusal with a reason class otherwise."""
    from .presence import agent_reason
    if not hasattr(os, "getuid"):
        raise Refusal("tier-2-required", "delegated steps need tier 2 approvals (not available on Windows)")
    why = agent_reason(env, ancestors)
    if why:
        raise Refusal("agent-session", f"a delegated step never runs inside an AI session: {why}")
    root, config = ws.load_workspace(workspace)
    item = delegate_for(config, role)
    if item is None:
        raise Refusal("not-delegated", f"this workspace names no {role} delegate")
    uid = (getuid or os.getuid)()
    if uid != item["uid"]:
        raise Refusal("not-delegated", f"this account (uid {uid}) is not the workspace's {role} delegate "
                                       f"({item['account']}, uid {item['uid']})")
    path = root / ws.CONFIG
    st = path.lstat()
    if path.is_symlink() or st.st_uid not in (0, item["uid"]) or st.st_mode & 0o022:
        raise Refusal("not-delegated", "workspace.json must be owned by root or the delegate and writable by "
                                       "no one else")
    if require_tier2:
        from . import gate
        mode = gate._resolve_ai_access(config.get("ai_access"), config.get("approval"))
        if mode != "connected" or config.get("approval_verify") != "owner-uid":
            raise Refusal("tier-2-required", "delegated steps need a connected workspace with tier 2 "
                                             "(owner-uid) approvals")
        if role == "approver" and config.get("approver_uid") != item["uid"]:
            raise Refusal("not-delegated", "the delegated approver must be the workspace's approver account "
                                           "(approver_uid)")
    return Actor(item["kind"], item["account"], uid, _model(item["kind"], model_id), "delegate")
