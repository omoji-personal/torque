"""Shared setup for the delegated-approver tests (POSIX only: tier 2)."""
from collections import namedtuple
import fnmatch
import hashlib
import io
import os
from pathlib import Path

from torque import approval, changes, consent, delegation, workspace as ws
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
    control_owner(monkeypatch)
    return root


_REAL_CONTROL_STAT = None


def control_owner(monkeypatch, owner=0, only=None):
    """R46: the control files (workspace.json, consent.json) and the folders holding
    them (the workspace root, clients/, clients/<slug>) must not belong to the
    approver account. A single-uid test owns everything as ME, which is also the
    approver, so this reports `owner` as their owner instead. The default 0 is the
    root-owned locked layout a real delegated grant needs (D1 + R46); ME + 1 is the
    consultant's own account (the a15 layout, for owner grants at the gate). With
    `only` (a file or folder name), only that one is reported as `owner` and the
    rest as root. Mode and file type always come from the real file or folder.

    Fix round 1: `approval.grant`/`_grant_delegated` now take their own call-scoped
    `control_stat` override instead of needing this monkeypatch (see
    `delegated_grant`, which reads whatever this function last set and passes it
    explicitly). This function still monkeypatches the process-global
    `approval._control_stat`, and stays the source of truth for that: it is the
    only seam `_problem` (the gate's own R46 check, run when a granted approval
    is consumed) has, since no real caller of the gate needs to fake ownership,
    only tests do, and many tests here set up one ownership scenario and then
    check both a grant and a `consume()` against it in the same test body, so
    splitting this into two unrelated mechanisms would cost more than it buys."""
    from types import SimpleNamespace
    global _REAL_CONTROL_STAT
    if _REAL_CONTROL_STAT is None:
        _REAL_CONTROL_STAT = approval._control_stat
    real = _REAL_CONTROL_STAT

    def fake(path, st=None):
        found = real(path, st)
        uid = owner if only is None or os.path.basename(str(path)) == only else 0
        return SimpleNamespace(st_mode=found.st_mode, st_uid=uid)
    monkeypatch.setattr(approval, "_control_stat", fake)


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


def flow_request(root, argv=None, org="acme-dev", cwd=None):
    project = Path(cwd) if cwd else Path(root)
    cid = changes.create_change(root, "Acme", "Flow fix", "Cases escalate", [], org)["id"]
    flows = project / "force-app" / "main" / "default" / "flows"
    flows.mkdir(parents=True, exist_ok=True)
    (flows / "Case_Escalation.flow-meta.xml").write_text("<Flow>v2</Flow>", encoding="utf-8")
    return approval.create_request(root, "Acme", cid, org, argv=list(argv or WRITE), resolve=ORGS.get,
                                   cwd=project)


def delegated_grant(root, req, **extra):
    """The delegated approver's grant of `req`, bound to what `approval show` reported.
    root_owner=FAKE_OWNER stands in for the separate workspace owner (R41) that a
    single-uid test process cannot have. control_stat (fix round 1) is read from
    `approval._control_stat` at call time, whatever `control_owner` most recently
    set it to (the default, root-owned layout from `delegated_workspace`, or a
    scenario a test reconfigured with a later `control_owner(...)` call): the
    grant path itself takes this as an explicit, call-scoped parameter now,
    rather than relying on `_grant_delegated`'s fallback to the global."""
    view = approval.request_view(root, "Acme", req["id"], resolve=ORGS.get)
    kwargs = {"delegated": True, "model_id": MODEL, "request_sha256": view["request_sha256"],
              "payload_digest": view["payload"]["digest"] or "none", "out": io.StringIO(),
              "resolve": ORGS.get, "root_owner": FAKE_OWNER, "control_stat": approval._control_stat, **CLEAN}
    kwargs.update(extra)
    return approval.grant(root, "Acme", req["id"], **kwargs)
