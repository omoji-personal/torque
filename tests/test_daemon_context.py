"""Task D21 (spec requirement 15): the delegated grant and key work run from a
LaunchDaemon-like context: an explicit HOME, no controlling terminal, no GUI
session and no login keychain. This is how the test program's approver daemon
(account torque-approver, launchd system domain) runs
`torque approval grant --delegated`.

`approval show` needs no delegated-approver identity (it is a plain read: no
call in `request_view` reaches `agent_reason` or `delegation._delegated_proof`
at all), so it is exercised through the real CLI, `python -m torque.cli
approval show`, exactly as the daemon would run it. `approval grant
--delegated` and `approval launch-binding` do need that identity proof (R41:
the delegate must be a separate OS account from the one that owns the
workspace directory; R46: the workspace's control files and the folders
holding them must not belong to the approver account), and the CLI exposes no
flag for either seam. That is by design, not a gap: the real daemon's
`torque-approver` account genuinely is a second OS account, and its control
files genuinely are root-owned, so the real command needs no override. A
single-uid test process cannot create a second real OS account, and a
subprocess cannot inherit its parent's monkeypatch of the module-global
`approval._control_stat` (D8 fix round 1 made that seam call-scoped for
exactly this reason). So those two verbs run here through a small `python -c`
driver instead, the same pattern `tests/test_delegated_reads.py` established
for this identical problem: import `torque.approval` / `torque.launch`
directly inside a fresh subprocess and pass `root_owner=` / `control_stat=`,
the injectable test seams those functions already expose, rather than adding
any env-var hook to production code (D5 concern 3 / the D21 dispatch
carried this forward explicitly). `resolve` is left at its default (None) on
every call, so the org is still resolved the same way the CLI resolves it:
through the real `resolve_org` -> a real subprocess `sf` call -> the fake
`sf` on PATH below.

This whole file skips inside any AI session (ruling F34): the agent-session
check (`torque.presence.agent_reason`, walked by `delegation._delegated_proof`
before R41 or R46) refuses first, by process ancestry, no matter what
environment or seam a test supplies. `claude` is an ancestor of every process
a Claude Code session starts (confirmed empirically while writing this file:
even the `python -c` driver above, with `root_owner`/`control_stat` supplied
and a fully scrubbed environment, still only ever produces reason class
"agent-session" run from inside such a session). That is Claude never
approving its own writes, working as designed; a human or CI is the one who
observes this file's grant/launch-binding assertions actually pass. Run it
from a plain terminal, or let CI run it:

    ~/.venvs/torque-a16/bin/python -m pytest tests/test_daemon_context.py -v

or, offline-suite style:

    ~/.venvs/torque-a16/bin/python scripts/test-offline.py \
        --pytest-only tests/test_daemon_context.py -q
"""
import json
import os
import subprocess
import sys
import textwrap

import pytest

from delegated_helpers import MODEL, delegated_workspace, flow_request
from torque.presence import agent_reason

pytestmark = [pytest.mark.skipif(not hasattr(os, "getuid"), reason="tier 2 is POSIX only"),
              pytest.mark.skipif(bool(agent_reason()), reason="run outside an AI session (CI runs it)")]
FAKE_SF = textwrap.dedent("""\
    #!/bin/sh
    case "$*" in
      *"org display"*) echo '{"status":0,"result":{"id":"00D000000000003AAA","instanceUrl":"https://acme-dev.develop.my.salesforce.com","loginUrl":"https://login.salesforce.com"}}' ;;
      *"data query"*) echo '{"status":0,"result":{"done":true,"totalSize":1,"records":[{"Id":"00D000000000003AAA","IsSandbox":false,"OrganizationType":"Developer Edition"}]}}' ;;
      *) echo '{"status":1}'; exit 1 ;;
    esac
""")
FAKE_SECURITY = "#!/bin/sh\ntouch \"$MARKER\"\nexit 1\n"
# D21 addition, beyond the brief: a grant/launch-binding driver, run as its own
# subprocess (a genuinely separate process, not a function call in-process) so
# no terminal, no GUI session and no login keychain are exercised for real,
# not merely asserted. `root_owner`/`control_stat` are `approval.grant`'s and
# `launch.create_binding`'s own call-scoped test seams (see the module
# docstring). A `delegation.Refusal` is reported the same way the CLI reports
# one (F37: JSON on stdout, exit 3) so a driver call reads exactly like a real
# `torque approval grant --delegated --json` call would, including on the
# tier-1 fail-closed path below.
DRIVER = textwrap.dedent("""\
    import io, json, os, sys
    from torque import delegation
    action, root = sys.argv[1], sys.argv[2]

    class _FakeSt:
        def __init__(self, st):
            self.st_mode = st.st_mode
            self.st_uid = 0

    def _control_stat(path, st=None):
        return _FakeSt(st if st is not None else os.lstat(path))

    root_owner = lambda p: os.getuid() + 1
    try:
        if action == "grant":
            client, req_id, sha, digest, key, model = sys.argv[3:9]
            from torque import approval
            record = approval.grant(root, client, req_id, delegated=True, model_id=model, request_sha256=sha,
                                    payload_digest=digest, idempotency_key=key, out=io.StringIO(),
                                    root_owner=root_owner, control_stat=_control_stat)
        else:
            client, model = sys.argv[3:5]
            from torque import launch as launches
            record = launches.create_binding(root, client, model_id=model, root_owner=root_owner,
                                             control_stat=_control_stat)
    except delegation.Refusal as exc:
        print(json.dumps({"refused": True, "reason_class": exc.reason_class, "message": str(exc)}))
        sys.exit(3)
    print(json.dumps(record))
""")


def _fake_bin(tmp_path):
    bin_dir = tmp_path / "bin"
    if not bin_dir.exists():
        bin_dir.mkdir()
        for name, body in (("sf", FAKE_SF), ("security", FAKE_SECURITY)):
            (bin_dir / name).write_text(body)
            (bin_dir / name).chmod(0o755)
    return bin_dir


def daemon_env(tmp_path):
    bin_dir = _fake_bin(tmp_path)
    home = tmp_path / "approver-home"
    home.mkdir(mode=0o700)
    return {"HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin", "SF_USE_GENERIC_UNIX_KEYCHAIN": "true",
            "MARKER": str(tmp_path / "keychain-touched"), "PYTHONPATH": os.pathsep.join(sys.path)}


def daemon_env_without_home(tmp_path):
    """D21 addition: the same daemon environment with no HOME key at all (not
    merely an empty HOME): a real LaunchDaemon's environment ordinarily has
    none. Used by the two checks below for item 5 ("nothing may ... depend on
    the user's HOME in the daemon path; fail closed with a clear message if
    HOME is unset")."""
    bin_dir = _fake_bin(tmp_path)
    return {"PATH": f"{bin_dir}:/usr/bin:/bin", "SF_USE_GENERIC_UNIX_KEYCHAIN": "true",
            "MARKER": str(tmp_path / "keychain-touched"), "PYTHONPATH": os.pathsep.join(sys.path)}


def torque(env, *args):
    return subprocess.run([sys.executable, "-m", "torque.cli", *args], env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=120, start_new_session=True)


def driver(env, *args):
    return subprocess.run([sys.executable, "-c", DRIVER, *args], env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=120, start_new_session=True)


def test_delegated_verbs_run_with_no_terminal_gui_or_keychain(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    env = daemon_env(tmp_path)
    base = ["--workspace", str(root), "--client", "Acme"]
    view = torque(env, "approval", "show", req["id"], *base, "--json")
    assert view.returncode == 0, view.stderr
    view = json.loads(view.stdout)
    granted = driver(env, "grant", str(root), "Acme", req["id"], view["request_sha256"],
                     view["payload"]["digest"], "r0b:daemon:1:abcdef01", MODEL)
    assert granted.returncode == 0, granted.stderr
    assert json.loads(granted.stdout)["approver_kind"] == "ai"
    binding = driver(env, "binding", str(root), "Acme", MODEL)
    assert binding.returncode == 0, binding.stderr
    assert json.loads(binding.stdout)["approver_kind"] == "ai"
    assert not (tmp_path / "keychain-touched").exists()
    assert list((tmp_path / "approver-home").rglob("*")) == []


def test_home_entirely_unset_does_not_block_the_delegated_grant(tmp_path, monkeypatch):
    """Item 5, first half: the delegated grant path has no reachable
    `Path.home()` / `expanduser("~")` call (the only one in `approval.py`,
    `key_path()`, is the tier 1 signing key path, refused long before any
    delegated call could reach it; see the next test). A daemon environment
    with literally no HOME key still grants."""
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    env = daemon_env_without_home(tmp_path)
    view = torque(env, "approval", "show", req["id"], "--workspace", str(root), "--client", "Acme", "--json")
    assert view.returncode == 0, view.stderr
    view = json.loads(view.stdout)
    granted = driver(env, "grant", str(root), "Acme", req["id"], view["request_sha256"],
                     view["payload"]["digest"], "r0b:daemon:2:bcdef012", MODEL)
    assert granted.returncode == 0, granted.stderr
    assert json.loads(granted.stdout)["approver_kind"] == "ai"
    assert not (tmp_path / "keychain-touched").exists()


def test_a_tier_1_workspace_fails_closed_without_touching_home_or_the_keychain(tmp_path, monkeypatch):
    """Item 5, second half: fail closed with a clear message if HOME is
    unset. A tier 1 (hmac) workspace's own key lives under `Path.home()`, so a
    daemon that somehow pointed at one needs a named, clean refusal, not a
    crash while probing a HOME that may not even exist for a system account.
    `_delegated_proof` checks tier 2 before any tier 1 code (`key_path`)
    could run: the refusal is "tier-2-required", not a HOME-related error, and
    it is reached with no HOME in the environment at all."""
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    config_path = root / "workspace.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["approval_verify"] = "hmac"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    env = daemon_env_without_home(tmp_path)
    view = torque(env, "approval", "show", req["id"], "--workspace", str(root), "--client", "Acme", "--json")
    assert view.returncode == 0, view.stderr
    view = json.loads(view.stdout)
    refused = driver(env, "grant", str(root), "Acme", req["id"], view["request_sha256"],
                     view["payload"]["digest"], "r0b:daemon:3:cdef0123", MODEL)
    assert refused.returncode == 3, refused.stdout + refused.stderr
    assert json.loads(refused.stdout) == {
        "refused": True, "reason_class": "tier-2-required",
        "message": "delegated steps need a connected workspace with tier 2 (owner-uid) approvals"}
    assert not (tmp_path / "keychain-touched").exists()
