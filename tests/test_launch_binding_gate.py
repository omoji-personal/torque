"""The gate binds a connected session to a client only from its launch record
(a16 requirement 9). POSIX only: tier 2 and the process checks."""
import io
import json
import os
import sys
import time

import pytest

from delegated_helpers import CLEAN, FAKE_OWNER, MODEL, as_agent, delegated_workspace, launched
from torque import approval, cli_approval, delegation, gate, launch

pytestmark = pytest.mark.skipif(not hasattr(os, "getuid"), reason="tier 2 is POSIX only")
READ = "sf data query -q 'SELECT Id FROM Account' -o acme-dev"
APPROVER = {"root_owner": FAKE_OWNER, **CLEAN}
LSTART = "%a %b %d %H:%M:%S %Y"


def hook(monkeypatch, capsys, root, command=READ):
    event = {"hook_event_name": "PreToolUse", "cwd": str(root), "session_id": "s1", "tool_use_id": "t1",
             "permission_mode": "default", "tool_name": "Bash", "tool_input": {"command": command}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    code = gate.main()
    return code, capsys.readouterr()


def consumed(root):
    return root / "clients/acme/approvals/consumed"


def granted(root):
    return root / "clients/acme/approvals/granted"


def started_at(monkeypatch, t):
    """This process "started" at epoch `t`, as `ps -o lstart=` prints it. The test
    process itself started long before any binding a test creates, so a
    binding-based launch (R48) needs a start time after the binding's creation."""
    text = time.strftime(LSTART, time.localtime(t))
    real = launch.process_start
    monkeypatch.setattr(launch, "process_start",
                        lambda pid, *, run=None: text if pid == os.getpid() else real(pid, run=run))
    return text


def claimed(monkeypatch, root, *, kind="ai", start=None):
    """An approver's binding claimed by this process as the agent account, with the
    session environment `torque launch --delegated` sets."""
    binding = launch.create_binding(root, "Acme", model_id=MODEL if kind == "ai" else None, **APPROVER)
    started_at(monkeypatch, time.time() if start is None else start)
    as_agent(monkeypatch)
    record = launch.claim_binding(root, "Acme", binding["id"], **CLEAN)
    monkeypatch.setenv("TORQUE_CLIENT", "acme")
    monkeypatch.setenv("TORQUE_LAUNCH", record["id"])
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return binding, record


def unbound(monkeypatch, capsys, root):
    code, out = hook(monkeypatch, capsys, root)
    return code == 2 and "no client is bound" in out.err


def rewrite(path, **change):
    record = json.loads(path.read_text())
    record.update(change)
    path.write_text(json.dumps(record))


# The brief's cases.

def test_torque_client_by_hand_does_not_bind(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("TORQUE_CLIENT", "acme")
    monkeypatch.delenv("TORQUE_LAUNCH", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    code, out = hook(monkeypatch, capsys, root)
    assert code == 2 and "no client is bound" in out.err


def test_a_launch_record_for_this_process_binds(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    launched(monkeypatch, root)
    assert hook(monkeypatch, capsys, root)[0] == 0


def test_a_record_for_another_process_or_start_time_does_not_bind(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    record = launched(monkeypatch, root)
    path = consumed(root) / f"{record['id']}.launch"
    for change in ({"pid": 999_999}, {"pid_started": "Thu Jan  1 00:00:00 1970"}):
        path.write_text(json.dumps({**record, **change}))
        assert unbound(monkeypatch, capsys, root)


def test_a_record_for_another_client_does_not_bind(tmp_path, monkeypatch, capsys):
    """Beta exists and holds a copy of Acme's record: the record's own client
    field refuses it, not a missing folder."""
    from torque import workspace as ws
    root = delegated_workspace(tmp_path, monkeypatch)
    record = launched(monkeypatch, root)
    ws.add_client(root, "Beta")
    beta = root / "clients/beta/approvals/consumed"
    beta.mkdir(parents=True)
    (beta / f"{record['id']}.launch").write_bytes((consumed(root) / f"{record['id']}.launch").read_bytes())
    monkeypatch.setenv("TORQUE_CLIENT", "beta")
    slug, why = launch.verify_launch(root, dict(os.environ))
    assert slug is None and "not for this client" in why
    assert unbound(monkeypatch, capsys, root)


def test_a_symlinked_record_does_not_bind(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    record = launched(monkeypatch, root)
    path = consumed(root) / f"{record['id']}.launch"
    elsewhere = tmp_path / "record.launch"
    path.rename(elsewhere)
    path.symlink_to(elsewhere)
    assert unbound(monkeypatch, capsys, root)
    path.unlink()
    elsewhere.rename(path)
    assert hook(monkeypatch, capsys, root)[0] == 0


def test_an_ai_record_needs_its_approver_binding(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    binding, _ = claimed(monkeypatch, root)
    assert hook(monkeypatch, capsys, root)[0] == 0
    (granted(root) / f"{binding['id']}.json").unlink()
    code, out = hook(monkeypatch, capsys, root)
    assert code == 2 and "no client is bound" in out.err


# bound_env and verify_launch directly.

def test_bound_env_keeps_the_client_only_when_the_record_verifies(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    record = launched(monkeypatch, root)
    env = {"TORQUE_CLIENT": "Acme", "TORQUE_LAUNCH": record["id"], "PATH": "/bin"}
    assert launch.verify_launch(root, env) == ("acme", "")
    assert launch.bound_env(env, root) == {**env, "TORQUE_CLIENT": "acme"}
    forged = {**env, "TORQUE_LAUNCH": "launch-0123456789ab"}
    assert launch.bound_env(forged, root) == {"TORQUE_LAUNCH": "launch-0123456789ab", "PATH": "/bin"}
    assert env["TORQUE_CLIENT"] == "Acme"  # a copy, never the caller's mapping


@pytest.mark.parametrize("launch_id", ["", "launch-0123", "../../x", "lnk-0123456789AB", "apr-0123456789ab",
                                       "launch-0123456789ab/../x"])
def test_malformed_launch_ids_do_not_bind(tmp_path, monkeypatch, launch_id):
    root = delegated_workspace(tmp_path, monkeypatch)
    launched(monkeypatch, root)
    slug, why = launch.verify_launch(root, {"TORQUE_CLIENT": "acme", "TORQUE_LAUNCH": launch_id})
    assert slug is None and why


@pytest.mark.parametrize("content", [b"", b"not json", b"[]", b"\xff\xfe", b'{"schema": "torque.launch/1"}'])
def test_unreadable_or_malformed_records_do_not_bind(tmp_path, monkeypatch, capsys, content):
    root = delegated_workspace(tmp_path, monkeypatch)
    record = launched(monkeypatch, root)
    (consumed(root) / f"{record['id']}.launch").write_bytes(content)
    assert unbound(monkeypatch, capsys, root)


def test_a_record_that_is_a_folder_does_not_bind(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    record = launched(monkeypatch, root)
    path = consumed(root) / f"{record['id']}.launch"
    path.unlink()
    path.mkdir()
    assert unbound(monkeypatch, capsys, root)


@pytest.mark.parametrize("change", [
    {"schema": "torque.launch/0"}, {"id": "launch-0123456789ab"}, {"client": "beta"}, {"workspace": "/elsewhere"},
    {"via": "binding"}, {"via": "probe"}, {"kind": "probe"}, {"kind": "ai"}, {"created_at": "yesterday"},
    {"created_at": None}, {"pid": "self"}, {"pid": True}, {"pid_started": None}])
def test_altered_human_records_do_not_bind(tmp_path, monkeypatch, capsys, change):
    root = delegated_workspace(tmp_path, monkeypatch)
    record = launched(monkeypatch, root)
    rewrite(consumed(root) / f"{record['id']}.launch", **change)
    assert unbound(monkeypatch, capsys, root)


def test_a_probe_record_binds(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    record = launch.write_launch_record(root, "Acme", "probe")
    monkeypatch.setenv("TORQUE_CLIENT", "acme")
    monkeypatch.setenv("TORQUE_LAUNCH", record["id"])
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    assert hook(monkeypatch, capsys, root)[0] == 0


def test_a_parent_process_record_binds_and_a_child_record_does_not(tmp_path, monkeypatch):
    """The record's process is this one or an ancestor (exec keeps the pid, and the
    hook runs as a child of the session); never a process below or beside it."""
    root = delegated_workspace(tmp_path, monkeypatch)
    parent = launch.write_launch_record(root, "Acme", "human", pid=os.getppid())
    env = {"TORQUE_CLIENT": "acme", "TORQUE_LAUNCH": parent["id"]}
    assert launch.verify_launch(root, env) == ("acme", "")
    import subprocess
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        record = launch.write_launch_record(root, "Acme", "human", pid=child.pid)
        slug, why = launch.verify_launch(root, {**env, "TORQUE_LAUNCH": record["id"]})
        assert slug is None and "another process" in why
    finally:
        child.kill()
        child.wait()


# F7: at most 8 `ps` calls.

class FakePs:
    def __init__(self, table):
        self.table, self.calls = table, 0

    def __call__(self, argv):
        self.calls += 1
        pid = int(argv[-1])
        if pid not in self.table:
            return ""
        ppid, begun = self.table[pid]
        return str(ppid) if "ppid=" in argv else begun


@pytest.mark.parametrize("target", [999, 1000, 1003, 1006, 1007, 5555])
def test_the_record_check_makes_at_most_max_ps_calls(tmp_path, monkeypatch, target):
    root = delegated_workspace(tmp_path, monkeypatch)
    record = launched(monkeypatch, root)
    rewrite(consumed(root) / f"{record['id']}.launch", pid=target, pid_started=f"start {target}")
    ps = FakePs({999: (1000, "start 999"), **{1000 + i: (1001 + i, f"start {1000 + i}") for i in range(20)}})
    slug, _ = launch.verify_launch(root, {"TORQUE_CLIENT": "acme", "TORQUE_LAUNCH": record["id"]},
                                   getpid=lambda: 999, getppid=lambda: 1000, run=ps)
    assert ps.calls <= launch.MAX_PS_CALLS
    assert (slug == "acme") == (target == 999 or 1000 <= target < 1000 + launch.MAX_ANCESTORS)


def test_the_ps_calls_stay_inside_the_gate_budget(monkeypatch):
    """Each `ps` call is charged to the gate's per-call budget and never waits past it."""
    seen = []
    monkeypatch.setattr(launch.subprocess, "run",
                        lambda argv, **kw: seen.append(kw["timeout"]) or type("R", (), {"stdout": "1"})())
    monkeypatch.setattr(gate, "_deadline", time.monotonic() + 0.5)
    assert launch._ps(["ps", "-o", "ppid=", "-p", "1"]) == "1"
    assert 0 < seen[-1] <= 0.5
    monkeypatch.setattr(gate, "_deadline", time.monotonic() - 1)
    with pytest.raises(gate.BudgetExceeded):
        launch._ps(["ps", "-o", "ppid=", "-p", "1"])
    monkeypatch.setattr(gate, "_deadline", None)
    launch._ps(["ps", "-o", "ppid=", "-p", "1"])
    assert seen[-1] == 2


def test_a_record_check_past_the_budget_blocks_the_call(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    record = launched(monkeypatch, root)
    rewrite(consumed(root) / f"{record['id']}.launch", pid=os.getppid())
    monkeypatch.setattr(gate, "GATE_TIME_BUDGET", 0.0)
    code, out = hook(monkeypatch, capsys, root)
    assert code == 2 and "budget" in out.err


# F8: the binding is re-checked whenever the record names one.

def test_a_human_delegate_binding_is_rechecked_too(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch, kind="human")
    binding, record = claimed(monkeypatch, root, kind="human")
    assert record["kind"] == "human" and record["via"] == "binding"
    assert hook(monkeypatch, capsys, root)[0] == 0
    (granted(root) / f"{binding['id']}.json").unlink()
    assert unbound(monkeypatch, capsys, root)


def test_a_record_naming_a_binding_is_rechecked_whatever_its_via(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    binding, record = claimed(monkeypatch, root)
    rewrite(consumed(root) / f"{record['id']}.launch", via="presence", kind="human")
    assert unbound(monkeypatch, capsys, root)


@pytest.mark.parametrize("change", [
    {"nonce": "0" * 32}, {"binding_id": "lnk-0123456789ab"}, {"binding_created_at": "2026-01-01T00:00:00+00:00"},
    {"binding_expires_at": "2099-01-01T00:00:00+00:00"}, {"kind": "human"}, {"via": "probe"},
    {"approver": {"approver": "someone", "approver_uid": 0, "approver_kind": "ai", "approver_model": MODEL}}])
def test_a_binding_record_that_disagrees_with_its_binding_does_not_bind(tmp_path, monkeypatch, capsys, change):
    root = delegated_workspace(tmp_path, monkeypatch)
    _, record = claimed(monkeypatch, root)
    rewrite(consumed(root) / f"{record['id']}.launch", **change)
    assert unbound(monkeypatch, capsys, root)


@pytest.mark.parametrize("change", [{"nonce": "f" * 32}, {"client": "beta"}, {"approver_model": "other"},
                                    {"approver_kind": "human"}, {"schema": "x"},
                                    {"created_at": "2099-01-01T00:00:00+00:00"}])
def test_an_altered_binding_does_not_bind(tmp_path, monkeypatch, capsys, change):
    root = delegated_workspace(tmp_path, monkeypatch)
    binding, _ = claimed(monkeypatch, root)
    rewrite(granted(root) / f"{binding['id']}.json", **change)
    assert unbound(monkeypatch, capsys, root)


def test_a_binding_others_can_write_or_a_linked_folder_does_not_bind(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    binding, _ = claimed(monkeypatch, root)
    path = granted(root) / f"{binding['id']}.json"
    path.chmod(0o666)
    assert unbound(monkeypatch, capsys, root)
    path.chmod(0o644)
    assert hook(monkeypatch, capsys, root)[0] == 0
    real = granted(root).rename(root / "clients/acme/approvals/granted-real")
    granted(root).symlink_to(real)
    assert unbound(monkeypatch, capsys, root)


def test_a_binding_record_binds_after_the_binding_expires(tmp_path, monkeypatch):
    """The binding's window limits the claim; a session started inside it stays bound."""
    root = delegated_workspace(tmp_path, monkeypatch)
    binding, record = claimed(monkeypatch, root)
    later = approval._epoch(binding["expires_at"]) + 3600
    monkeypatch.setattr(launch.time, "time", lambda: later)
    env = {"TORQUE_CLIENT": "acme", "TORQUE_LAUNCH": record["id"]}
    assert launch.verify_launch(root, env) == ("acme", "")


def test_a_binding_record_needs_the_workspace_delegated_approver(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    claimed(monkeypatch, root)
    config = json.loads((root / "workspace.json").read_text())
    config["delegates"].pop("approver")
    (root / "workspace.json").write_text(json.dumps(config))
    assert unbound(monkeypatch, capsys, root)


def test_a_binding_record_needs_r46_control_files(tmp_path, monkeypatch, capsys):
    from delegated_helpers import ME, control_owner
    root = delegated_workspace(tmp_path, monkeypatch)
    claimed(monkeypatch, root)
    control_owner(monkeypatch, owner=ME, only="consent.json")
    assert unbound(monkeypatch, capsys, root)


# R48: a session started before its binding cannot adopt it; late re-claims fail.

def test_a_process_started_before_the_binding_cannot_claim_it(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    claimed(monkeypatch, root, start=time.time() - approval.SKEW - 5)
    code, out = hook(monkeypatch, capsys, root)
    assert code == 2 and "no client is bound" in out.err


def test_a_process_started_just_inside_the_skew_binds(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    claimed(monkeypatch, root, start=time.time() - approval.SKEW + 5)
    assert hook(monkeypatch, capsys, root)[0] == 0


def test_the_real_test_process_is_older_than_a_new_binding(tmp_path, monkeypatch):
    """Unfaked: this pytest process started before the binding, so R48 refuses it
    whenever the process is more than SKEW older than the binding."""
    root = delegated_workspace(tmp_path, monkeypatch)
    binding = launch.create_binding(root, "Acme", model_id=MODEL, **APPROVER)
    as_agent(monkeypatch)
    record = launch.claim_binding(root, "Acme", binding["id"], **CLEAN)
    begun = launch._lstart_epoch(record["pid_started"])
    if begun >= approval._epoch(binding["created_at"]) - approval.SKEW:
        pytest.skip("this pytest process is not yet more than SKEW older than a new binding")
    slug, why = launch.verify_launch(root, {"TORQUE_CLIENT": "acme", "TORQUE_LAUNCH": record["id"]})
    assert slug is None and "before" in why


def test_a_late_reclaim_does_not_bind(tmp_path, monkeypatch, capsys):
    """Deleting the consumed marker and claiming again after the window: the record's
    created_at is past the binding's creation + TTL + SKEW."""
    root = delegated_workspace(tmp_path, monkeypatch)
    binding, record = claimed(monkeypatch, root)
    created = approval._epoch(binding["created_at"])
    path = consumed(root) / f"{record['id']}.launch"
    late = created + launch.BINDING_TTL_MAX + approval.SKEW + 1
    rewrite(path, created_at=approval._iso(late))
    assert unbound(monkeypatch, capsys, root)
    rewrite(path, created_at=approval._iso(approval._epoch(binding["expires_at"]) + approval.SKEW + 1))
    assert unbound(monkeypatch, capsys, root)
    rewrite(path, created_at=binding["expires_at"])
    assert hook(monkeypatch, capsys, root)[0] == 0


def test_a_process_started_after_the_binding_expired_does_not_bind(tmp_path, monkeypatch, capsys):
    """A forged record cannot hide a late claim behind an early created_at: the
    process start time comes from `ps`, not from the record."""
    root = delegated_workspace(tmp_path, monkeypatch)
    binding, record = claimed(monkeypatch, root)
    late = approval._epoch(binding["expires_at"]) + approval.SKEW + 5
    text = started_at(monkeypatch, late)
    rewrite(consumed(root) / f"{record['id']}.launch", pid_started=text)
    assert unbound(monkeypatch, capsys, root)


def test_an_unparseable_start_time_on_a_binding_record_does_not_bind(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    binding, record = claimed(monkeypatch, root)
    real = launch.process_start
    monkeypatch.setattr(launch, "process_start",
                        lambda pid, *, run=None: "soon" if pid == os.getpid() else real(pid, run=run))
    rewrite(consumed(root) / f"{record['id']}.launch", pid_started="soon")
    assert unbound(monkeypatch, capsys, root)


def test_lstart_parsing():
    t = time.mktime((2026, 9, 5, 10, 0, 0, 0, 0, -1))
    assert launch._lstart_epoch("Sat Sep  5 10:00:00 2026") == t
    assert launch._lstart_epoch("Sat Sep 5 10:00:00 2026") == t
    for bad in ("", "soon", None, 5, "Sat Sep 5 10:00:00"):
        assert launch._lstart_epoch(bad) is None


# R49: a delegated launch refuses permission-bypass passthrough flags.

REFUSED = [["--dangerously-skip-permissions"], ["--allow-dangerously-skip-permissions"],
           ["--permission-mode", "bypassPermissions"], ["--permission-mode=bypassPermissions"],
           ["--permission-mode", "BYPASSPERMISSIONS"], ["--settings", "s.json"], ["--settings=s.json"],
           ["--setting-sources", "user"], ["--setting-sources=user"], ["--allowedTools", "Bash"],
           ["--allowedTools=Bash"], ["--allowed-tools", "Bash"], ["--allowed-tools=Bash"],
           ["--disallowedTools", "Edit"], ["--disallowedTools=Edit"], ["--disallowed-tools", "Edit"],
           ["--disallowed-tools=Edit"], ["--add-dir", "/tmp"], ["--add-dir=/tmp"]]


@pytest.mark.parametrize("flags", REFUSED)
def test_delegated_launch_refuses_permission_bypass_flags(tmp_path, monkeypatch, flags):
    root = delegated_workspace(tmp_path, monkeypatch)
    binding = launch.create_binding(root, "Acme", model_id=MODEL, **APPROVER)
    as_agent(monkeypatch)
    monkeypatch.chdir(root)
    ran = []
    with pytest.raises(delegation.Refusal) as info:
        cli_approval.launch(root, "Acme", ["-p", *flags, "--verbose"], execvp=lambda *a: ran.append(a),
                            delegated=True, binding=binding["id"], **CLEAN)
    assert info.value.reason_class == "launch-flag-refused", str(info.value)
    assert not ran and not (consumed(root) / f"{binding['id']}.launch").exists()


HARNESS = ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--permission-mode", "default"]


@pytest.mark.parametrize("flags", [
    ["--bare"], ["--safe-mode"], ["--permission-prompt-tool", "mcp__x__y"], ["--mcp-config", "m.json"],
    ["--plugin-dir", "p"], ["--agents", "{}"], ["--worktree"], ["--some-future-flag"], ["-x"], ["-"],
    ["--permission-mode", "acceptEdits"], ["--permission-mode=acceptEdits"], ["--permission-mode=plan"],
    ["--permission-mode"], ["--verbose=true"], ["--", "--bare"], ["prompt text", "--", "-x"]])
def test_delegated_launch_refuses_anything_off_the_allowlist(tmp_path, monkeypatch, flags):
    assert launch.launch_flag_problem(["-p", *flags])
    root = delegated_workspace(tmp_path, monkeypatch)
    binding = launch.create_binding(root, "Acme", model_id=MODEL, **APPROVER)
    as_agent(monkeypatch)
    monkeypatch.chdir(root)
    ran = []
    with pytest.raises(delegation.Refusal) as info:
        cli_approval.launch(root, "Acme", ["-p", *flags], execvp=lambda *a: ran.append(a), delegated=True,
                            binding=binding["id"], **CLEAN)
    assert info.value.reason_class == "launch-flag-refused", str(info.value)
    assert not ran and not (consumed(root) / f"{binding['id']}.launch").exists()


@pytest.mark.parametrize("flags", [
    HARNESS, ["--print", "summarize this"], ["-p", "--permission-mode=default"], ["--include-partial-messages"],
    ["--replay-user-messages"], ["--model", "m"], ["--model=m"], ["--fallback-model", "m"], ["--effort", "high"],
    ["--append-system-prompt", "--be brief"], ["--max-budget-usd", "5"], ["--json-schema", "{}"],
    ["--session-id", "0f0e"], ["--name", "run"], ["--no-session-persistence"], ["-p", "--", "plain prompt"]])
def test_delegated_launch_passes_allowlisted_options(flags):
    assert launch.launch_flag_problem(flags) == ""


def test_the_harness_argv_launches(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    binding = launch.create_binding(root, "Acme", model_id=MODEL, **APPROVER)
    as_agent(monkeypatch)
    monkeypatch.chdir(root)
    seen = []
    cli_approval.launch(root, "Acme", HARNESS, execvp=lambda program, args: seen.append(args), delegated=True,
                        binding=binding["id"], **CLEAN)
    assert seen == [["claude", *HARNESS]]


def test_delegated_launch_refuses_claude_code_simple(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    binding = launch.create_binding(root, "Acme", model_id=MODEL, **APPROVER)
    as_agent(monkeypatch)
    monkeypatch.chdir(root)
    monkeypatch.setenv("CLAUDE_CODE_SIMPLE", "1")
    ran = []
    with pytest.raises(delegation.Refusal) as info:
        cli_approval.launch(root, "Acme", HARNESS, execvp=lambda *a: ran.append(a), delegated=True,
                            binding=binding["id"], **CLEAN)
    assert info.value.reason_class == "launch-flag-refused" and "CLAUDE_CODE_SIMPLE" in str(info.value)
    assert not ran and not (consumed(root) / f"{binding['id']}.launch").exists()


def test_delegated_launch_child_has_no_claude_code_simple(tmp_path, monkeypatch):
    """Removed from the child environment even if it appears after the check."""
    root = delegated_workspace(tmp_path, monkeypatch)
    binding = launch.create_binding(root, "Acme", model_id=MODEL, **APPROVER)
    as_agent(monkeypatch)
    monkeypatch.chdir(root)
    monkeypatch.delenv("CLAUDE_CODE_SIMPLE", raising=False)
    real = launch.claim_binding

    def claim(*a, **k):
        os.environ["CLAUDE_CODE_SIMPLE"] = "1"
        return real(*a, **k)
    monkeypatch.setattr(launch, "claim_binding", claim)
    seen = []
    cli_approval.launch(root, "Acme", HARNESS, execvp=lambda *a: seen.append(os.environ.get("CLAUDE_CODE_SIMPLE")),
                        delegated=True, binding=binding["id"], **CLEAN)
    assert seen == [None]


def test_the_agent_session_check_comes_before_the_flag_check(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    with pytest.raises(delegation.Refusal) as info:
        cli_approval.launch(root, "Acme", ["--dangerously-skip-permissions"], execvp=lambda *a: None,
                            delegated=True, binding="lnk-0123456789ab", env={"CLAUDECODE": "1"},
                            ancestors=lambda: [])
    assert info.value.reason_class == "agent-session"


def test_owner_launch_keeps_its_flags(tmp_path, monkeypatch):
    """R49 is for the delegated path only; the consultant's launch is unchanged (a15)."""
    from torque.presence import Presence
    root = delegated_workspace(tmp_path, monkeypatch)
    monkeypatch.chdir(root)
    seen = []
    cli_approval.launch(root, "Acme", ["--dangerously-skip-permissions"], execvp=lambda *a: seen.append(a),
                        presence=lambda: Presence(True, ""))
    assert seen and seen[0][1][-1] == "--dangerously-skip-permissions"
