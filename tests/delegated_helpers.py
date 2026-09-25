"""Shared setup for the delegated-approver tests (POSIX only: tier 2)."""
from collections import namedtuple
import fnmatch
import hashlib
import os
from pathlib import Path

from torque import consent, delegation, workspace as ws
from torque.presence import Presence

YES = lambda: Presence(True, "")
ME = os.getuid() if hasattr(os, "getuid") else -1
if hasattr(os, "getuid"):
    import pwd
    ACCOUNT = pwd.getpwuid(ME).pw_name
else:
    ACCOUNT = ""
MODEL = "reviewer-model-1"
# A fake workspace-directory owner distinct from ME/ACCOUNT, for the delegated
# path's R41 "delegate != workspace owner" check (delegation.delegated_actor's
# root_owner parameter, added in D1's fix round 1, after this task's Interfaces
# text was written; not overriding it here would trip that check in every
# single-uid test, since the fixture's real directory owner is ME, the test
# process's own uid, same as the delegate account named below).
FAKE_OWNER = lambda p: ME + 1
CLEAN = {"env": {}, "ancestors": lambda: []}
Org = namedtuple("Org", "org_id_18 detected_org_type is_production instance_url", defaults=(None,))
ORGS = {"acme-dev": Org("00D000000000003AAA", "developer", False, "https://acme-dev.develop.my.salesforce.com"),
        "acme-prod": Org("00D000000000002AAA", "production", True, "https://acme.my.salesforce.com")}
WRITE = ["sf", "project", "deploy", "start", "--metadata", "Flow:Case_Escalation", "--target-org", "acme-dev"]


def base_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(root, "Acme")
    return Path(os.path.realpath(root))


def delegated_workspace(tmp_path, monkeypatch, *, kind="ai", orgs=("acme-dev",)):
    """Connected, tier 2, this account as setup delegate and delegated approver.
    Names the delegates, but performs the actual setup steps on the owner
    (presence) path, not the delegated path: later tasks use this fixture to
    reach a ready, tier-2, named-delegate workspace quickly without also
    exercising the delegated setup path's own R41 owner check."""
    root = base_workspace(tmp_path, monkeypatch)
    delegation.set_delegate(root, "setup", ACCOUNT, ME, "ai", geteuid=lambda: 0)
    delegation.set_delegate(root, "approver", ACCOUNT, ME, kind, geteuid=lambda: 0)
    ws.set_ai_access(root, "connected", approval="required", verify="owner-uid", approver_uid=ME, presence=YES)
    letter = tmp_path / "agreement.pdf"
    letter.write_bytes(b"synthetic agreement")
    consent.record_consent(root, "Acme", "2026-09-30", letter, ["metadata", "records"], list(orgs), ["Contact"],
                           presence=YES, resolve=ORGS.get)
    consent.sign_off(root, "Acme", "Reviewer", presence=YES)
    for name in ("requests", "granted", "consumed", "denied"):
        (root / "clients" / "acme" / "approvals" / name).mkdir(parents=True, exist_ok=True)
    return root


def snapshot(root) -> dict:
    root = Path(root)
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def changed(before: dict, after: dict) -> set:
    return {name for name, digest in after.items() if before.get(name) != digest}


def within(names, patterns) -> bool:
    return all(any(fnmatch.fnmatch(n, p) for p in patterns) for n in names)


def as_agent(monkeypatch):
    """Make gate checks see a separate agent account (uid ME + 1)."""
    monkeypatch.setattr(os, "getuid", lambda: ME + 1)
