# Maintainer-window checks. The mechanism shipped 2026-08-04 and was live for a day without
# these, which is not this repo's standard and was recorded as owed rather than pretended.
#
# Every check here builds a THROWAWAY anchor in a temp directory and points the gate at it with
# TORQUE_ANCHOR and TORQUE_AUDIT_LOG. None of them reads, writes, or depends on the operator's
# real anchor or their real grant — so they behave identically whether or not a window happens to
# be open while the suite runs, which matters because the suite is frequently run inside one.
import hashlib
import hmac
import json as _mj
import os as _mos
import shutil as _msh
import subprocess as _msp
import sys as _msys
import tempfile as _mtf
import time as _mtime
from pathlib import Path as _MP

_GATE = "hooks/prod_write_gate.py"
_DGATE = "hooks/destructive_data_gate.py"


class _Box:
    """A disposable trust anchor plus an audit log, and the ability to mint grants into it."""

    def __init__(self):
        self.dir = _MP(_mtf.mkdtemp(prefix="tq-maint-check-"))
        self.secret = _mos.urandom(32)
        (self.dir / "secret").write_bytes(self.secret)
        self.audit = self.dir / "audit.log"

    def env(self):
        return {**_mos.environ, "TORQUE_ANCHOR": str(self.dir),
                "TORQUE_AUDIT_LOG": str(self.audit)}

    def sign(self, payload):
        body = _mj.dumps(payload, sort_keys=True).encode()
        return hmac.new(self.secret, body, hashlib.sha256).hexdigest()

    def grant(self, tree=None, minutes=10, forge=False):
        g = {"tree": tree or str(ROOT.resolve()),
             "exp": int(_mtime.time()) + minutes * 60, "iat": int(_mtime.time())}
        g["sig"] = "0" * 64 if forge else self.sign(g)
        (self.dir / "maintainer.grant").write_text(_mj.dumps(g))

    def clear(self):
        (self.dir / "maintainer.grant").unlink(missing_ok=True)

    def edit(self, path, gate=_GATE):
        ev = {"tool_name": "Edit",
              "tool_input": {"file_path": str(path), "old_string": "a", "new_string": "b"}}
        return _msp.run([_msys.executable, str(ROOT / gate)], input=_mj.dumps(ev),
                        capture_output=True, text=True, cwd=ROOT, timeout=90,
                        env=self.env()).returncode

    def bash(self, command, gate=_DGATE):
        ev = {"tool_name": "Bash", "tool_input": {"command": command}}
        return _msp.run([_msys.executable, str(ROOT / gate)], input=_mj.dumps(ev),
                        capture_output=True, text=True, cwd=ROOT, timeout=90,
                        env=self.env()).returncode

    def audit_lines(self, decision):
        if not self.audit.exists():
            return []
        return [l for l in self.audit.read_text().splitlines() if f'"{decision}"' in l]

    def close(self):
        _msh.rmtree(self.dir, ignore_errors=True)


@check("maintainer_grant_is_operator_only", "static", catastrophe=True)
def _maintainer_grant_is_operator_only():
    """A maintainer window must be forgeable only by someone who can sign with the anchor secret.

    Four negative cases and one positive, and the negatives are the security property: a grant
    that is absent, expired, forged, or issued for a different tree must all leave the gate
    refusing exactly as it would with no window at all.

    THE POSITIVE CASE ASSERTS THE REASON, NOT THE EXIT CODE. This is the trap A1 fell into:
    shadow_cannot_escape_the_transaction passed for thirteen commits because its target refused
    everything for an unrelated reason, so the assertion never once exercised the guard it was
    named after. An exit 0 here could mean "the window worked" or "the path was never protected",
    and those are not the same fact. So the positive case additionally requires a MAINTAINER-EDIT
    record naming the file — the audit trail is the evidence that the grant path, specifically,
    is what allowed it.
    """
    box = _Box()
    try:
        # Deliberately NOT named `target`. run_profile calls a check as fn(target) when
        # "target" appears in fn.__code__.co_varnames — which includes LOCALS, not just
        # parameters. A local called `target` therefore changes the calling convention of the
        # function that declares it, and the check dies with a TypeError that reads like a
        # harness bug. Cost ten minutes; worth a comment.
        victim = ROOT / "hooks" / "lib.py"          # protected by basename, wherever it lives
        cases = []

        box.clear()
        cases.append(("no grant", box.edit(victim), 2))

        box.grant(minutes=-1)                        # exp already in the past
        cases.append(("expired grant", box.edit(victim), 2))

        box.grant(forge=True)
        cases.append(("forged signature", box.edit(victim), 2))

        box.grant(tree="/somewhere/else")
        cases.append(("grant for another tree", box.edit(victim), 2))

        for label, got, want in cases:
            if got != want:
                return Result("maintainer_grant_is_operator_only", FAIL,
                              f"{label}: gate exited {got}, expected {want} — a window that is "
                              f"absent, stale, forged or foreign must refuse exactly as no "
                              f"window does")

        # the one positive, asserted by REASON
        box.grant()
        before = len(box.audit_lines("MAINTAINER-EDIT"))
        rc = box.edit(victim)
        after = box.audit_lines("MAINTAINER-EDIT")
        if rc != 0:
            return Result("maintainer_grant_is_operator_only", FAIL,
                          f"a valid window did not permit editing {victim.name} (exit {rc})")
        if len(after) <= before:
            return Result("maintainer_grant_is_operator_only", FAIL,
                          "a valid window permitted the edit but wrote NO MAINTAINER-EDIT record "
                          "— the allow cannot be attributed to the grant, and an unaudited edit "
                          "to the enforcement layer is not one this tool authorizes")
        if victim.name not in after[-1]:
            return Result("maintainer_grant_is_operator_only", FAIL,
                          f"the audit record does not name the edited file: {after[-1][:110]}")

        # and an ordinary file is unaffected in both directions
        box.clear()
        if box.edit(ROOT / "README.md") != 0:
            return Result("maintainer_grant_is_operator_only", FAIL,
                          "an ordinary file was refused with no window — the guard has become a "
                          "wall rather than a boundary")
        return Result("maintainer_grant_is_operator_only", PASS,
                      f"{len(cases)} invalid window shape(s) refused; a valid one permits the "
                      f"edit AND records it as MAINTAINER-EDIT; ordinary files unaffected")
    finally:
        box.close()


@check("maintainer_grant_never_touches_orgs", "static", catastrophe=True)
def _maintainer_grant_never_touches_orgs():
    """A window unlocks Torque's own source. It must not unlock a single thing about an org.

    That split is the entire reason the mechanism is defensible: editing lib.py has never
    required writing to a customer's org, so the grant is consulted in handle_edit and nowhere
    else. This asserts the "nowhere else" half, which is the half that would rot silently — a
    future refactor could thread the grant into an org path and every other check here would
    still pass.
    """
    box = _Box()
    try:
        box.grant()                                  # a VALID window, open for all of these
        shapes = [
            ("bulk delete without an impact token",
             "sf data delete bulk --sobject Account --file ids.csv --target-org torque-not-an-org"),
            ("anonymous apex",
             "sf apex run --file /tmp/x.apex --target-org torque-not-an-org"),
            ("unscoped record delete",
             "sf data delete record --sobject Account --where \"Name != null\" "
             "--target-org torque-not-an-org"),
        ]
        for label, cmd in shapes:
            rc = box.bash(cmd)
            if rc != 2:
                return Result("maintainer_grant_never_touches_orgs", FAIL,
                              f"with a maintainer window open, {label} exited {rc} instead of "
                              f"being denied — the window has leaked into org authorization, "
                              f"which is the one thing it must never do")
        return Result("maintainer_grant_never_touches_orgs", PASS,
                      f"with a valid window open, {len(shapes)} org-touching shape(s) are still "
                      f"refused; the grant reaches artifacts and not orgs")
    finally:
        box.close()


@check("maintainer_grant_cannot_reach_the_anchor", "static", catastrophe=True)
def _maintainer_grant_cannot_reach_the_anchor():
    """The window must never cover the thing that issues windows.

    A grant that could rewrite the signing secret could sign itself a new grant, and the
    operator-presence proof behind it would become decorative. Same for the token store, and for
    the sf CLI auth store, which holds live access tokens and is not source at all.

    These are checked BEFORE the grant in handle_edit precisely so no window can reach them.
    """
    box = _Box()
    try:
        box.grant()                                  # valid window, deliberately
        targets = [("the signing secret", box.dir / "secret"),
                   ("the token store", box.dir / "tokens" / "x.token"),
                   ("the sf auth store", _MP.home() / ".sfdx" / "alias.json"),
                   ("the sf config store", _MP.home() / ".sf" / "config.json")]
        for label, path in targets:
            rc = box.edit(path)
            if rc != 2:
                return Result("maintainer_grant_cannot_reach_the_anchor", FAIL,
                              f"with a maintainer window open, {label} ({path}) was writable "
                              f"(exit {rc}) — a window that reaches the anchor can extend itself")
        return Result("maintainer_grant_cannot_reach_the_anchor", PASS,
                      f"{len(targets)} anchor/credential path(s) refused even with a valid "
                      f"window open; the grant is checked after them, never before")
    finally:
        box.close()
