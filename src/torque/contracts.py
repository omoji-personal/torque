"""Offline contracts a release can prove without a live org, runnable against the
installed package: `python -m torque.contracts delegated-org-refusal --json`.

delegated-org-refusal (spec requirement 4): a delegated (AI) grant is refused for
a production org and for an org whose kind is unknown, whether the live
resolution or the recorded consent says so. Each case builds its own throwaway
tier 2 workspace and calls the real grant code path in torque.approval, never a
re-implementation. The delegated approver's identity is proven the same way the
grant code already lets any caller prove it: delegation.set_delegate's
administrator bypass to name the delegate, and approval.grant's own per-call
overrides for R41 (root_owner) and R46 (control_stat), so the workspace's
control files and folders read as owned by someone other than the single OS
account a contract run has to work with. Both overrides are call-scoped
parameters, never a change to the running process's own state: this module
touches no process-global (fix round 1), so a concurrent caller in the same
process checking real ownership is never affected by what a contract run fakes
for itself. Org identity comes from an injectable resolver factory (`resolve`),
defaulting to this module's own synthetic one; the contract never touches $HOME
beyond the temporary workspace it creates and makes no network call."""
from __future__ import annotations

from collections import namedtuple
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

_Org = namedtuple("_Org", "org_id_18 detected_org_type is_production instance_url")
_ORG = "contract-org"
_ID = "00D00000000000CAAA"
_INSTANCE = "https://contract.develop.my.salesforce.com"


def _org(kind: str):
    """The contract's own default resolver factory: an alias resolver fixed to one
    org kind, never touching Salesforce. `delegated_org_refusal`'s `resolve`
    parameter takes the same shape (kind -> alias resolver) and defaults to this
    one; a test injects a different factory to prove the seam actually changes
    the outcome, rather than this module hardcoding its own answer."""
    return {_ORG: _Org(_ID, kind, kind != "developer", _INSTANCE)}.get


def _fake_control_stat(real, approver_uid: int):
    """R46: a delegated grant refuses when the workspace's control files
    (workspace.json, consent.json) or the folders holding them belong to the
    approver account. A contract run has no second OS account to actually chown
    those to, so this builds a one-call `control_stat` override (fix round 1:
    `approval.grant`'s own parameter for exactly this, threaded through to
    `_controls_problem`/`_control_problem`/`_folder_problem`) that reports every
    control path as owned by a different, fixed uid instead. Mode and file type
    still come from the real file or folder; only the owner is faked. This never
    touches `approval._control_stat` itself, so it cannot affect any other,
    concurrent caller in the same process checking real ownership through the
    untouched default."""
    fake_uid = approver_uid + 1

    def fake(path, st=None):
        found = real(path, st)
        return SimpleNamespace(st_mode=found.st_mode, st_uid=fake_uid)

    return fake


def _workspace(base: Path, consent_kind: str, resolve) -> Path:
    """A throwaway, tier 2, single-account workspace: one client, this account
    named as the (AI) delegated approver, active consent for `_ORG` recorded at
    `consent_kind`, and a real deployable component in place, so the grant this
    proves is asked of has real files to bind rather than a stand-in."""
    import pwd
    from . import consent, delegation, workspace as ws
    from .presence import Presence

    me = os.getuid()
    root = Path(os.path.realpath(ws.init_workspace(base, "Contract")))
    ws.add_client(root, "Contract Client")
    account = pwd.getpwuid(me).pw_name
    # geteuid=lambda: 0 takes set_delegate's administrator path, the same way a
    # real provisioning run (as root) would; a contract run has no second OS
    # account to delegate to instead.
    delegation.set_delegate(root, "approver", account, me, "ai", geteuid=lambda: 0)
    yes = lambda: Presence(True, "")
    ws.set_ai_access(root, "connected", approval="required", verify="owner-uid", approver_uid=me, presence=yes)
    letter = base.parent / f"agreement-{consent_kind}.txt"
    letter.write_text("synthetic contract agreement", encoding="utf-8")
    consent.record_consent(root, "Contract Client", "2026-01-01", letter, ["metadata"], [_ORG], [],
                           presence=yes, resolve=resolve(consent_kind))
    consent.sign_off(root, "Contract Client", "Contract reviewer", presence=yes)
    for name in ("requests", "granted", "consumed", "denied"):
        (root / "clients" / "contract-client" / "approvals" / name).mkdir(parents=True, exist_ok=True)
    project = root / "force-app" / "main" / "default" / "flows"
    project.mkdir(parents=True, exist_ok=True)
    (project / "Contract.flow-meta.xml").write_text("<Flow/>", encoding="utf-8")
    return root


def _case(name: str, consent_kind: str, live_kind: str, resolve) -> dict:
    """One refusal proof: build a workspace whose consent records `consent_kind`,
    ask the real delegated grant code (torque.approval.grant(delegated=True))
    for the org resolved live as `live_kind`, and report whether it refused and
    with which reason class. R41 (the delegate must be a separate account from
    the one that owns the workspace directory) and R46 (the control files and
    folders must not be the approver account's) are satisfied through the same
    per-call overrides the grant code already accepts for exactly this situation
    (root_owner, control_stat), never by changing what is checked, and never by
    mutating any state this call does not own."""
    from . import approval, changes
    from .delegation import Refusal

    with tempfile.TemporaryDirectory() as tmp:
        root = _workspace(Path(tmp) / "ws", consent_kind, resolve)
        me = os.getuid()
        cid = changes.create_change(root, "Contract Client", "Contract", "Refusal holds", [], _ORG)["id"]
        argv = ["sf", "project", "deploy", "start", "--metadata", "Flow:Contract", "--target-org", _ORG]
        req = approval.create_request(root, "Contract Client", cid, _ORG, argv=argv,
                                      resolve=resolve(consent_kind), cwd=root)
        view = approval.request_view(root, "Contract Client", req["id"], resolve=resolve(consent_kind))
        try:
            approval.grant(root, "Contract Client", req["id"], delegated=True, model_id="contract-model",
                           request_sha256=view["request_sha256"],
                           payload_digest=view["payload"]["digest"] or "none",
                           out=io.StringIO(), resolve=resolve(live_kind), env={}, ancestors=lambda: [],
                           root_owner=lambda p: me + 1,
                           control_stat=_fake_control_stat(approval._control_stat, me))
        except Refusal as exc:
            return {"case": name, "refused": True, "reason_class": exc.reason_class}
        return {"case": name, "refused": False, "reason_class": None}


def delegated_org_refusal(resolve=None) -> dict:
    """Proves spec requirement 4: a delegated grant is refused for a production
    org and for an org of unknown kind, whether that comes from live resolution
    or from what consent recorded. `resolve` injects the org-kind resolver
    factory (kind -> alias resolver) each case uses in place of this module's
    own default (`_org`); a test uses it to prove the injection seam is real,
    the CLI never sets it. On a platform with no numeric user IDs (Windows),
    tier 2 approvals do not exist, so the contract reports itself unsupported
    rather than refusing to run."""
    if not hasattr(os, "getuid"):
        return {"contract": "delegated-org-refusal", "supported": False, "passed": None, "cases": []}
    build = resolve or _org
    cases = [_case("live-production", "developer", "production", build),
             _case("live-unknown", "developer", "unknown", build),
             _case("consent-production", "production", "developer", build)]
    passed = all(c["refused"] and c["reason_class"] == "org-production-or-unknown" for c in cases)
    return {"contract": "delegated-org-refusal", "supported": True, "passed": passed, "cases": cases}


CONTRACTS = {"delegated-org-refusal": delegated_org_refusal}


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in CONTRACTS:
        print("usage: python -m torque.contracts {" + ",".join(CONTRACTS) + "} [--json]", file=sys.stderr)
        return 2
    result = CONTRACTS[args[0]]()
    if "--json" in args:
        print(json.dumps(result, indent=2))
    else:
        for case in result["cases"]:
            print(f"{case['case']}: {'refused' if case['refused'] else 'NOT REFUSED'} ({case['reason_class']})")
    return 0 if result["passed"] in (True, None) else 1


if __name__ == "__main__":
    raise SystemExit(main())
