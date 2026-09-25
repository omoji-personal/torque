"""Approver-bound unattended launch: single-use launch bindings and launch records
(a16 requirement 8). POSIX only: tier 2."""
import json
import os
import stat
import time

import pytest

from delegated_helpers import (CLEAN, FAKE_OWNER, ME, MODEL, YES, as_agent, base_workspace, control_owner,
                               delegated_workspace)
from torque import approval, cli, cli_approval, delegation, launch, workspace as ws
from torque.presence import Presence

pytestmark = pytest.mark.skipif(not hasattr(os, "getuid"), reason="tier 2 is POSIX only")
PASS = ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
APPROVER = {"root_owner": FAKE_OWNER, **CLEAN}
START = "Fri Sep 25 10:00:00 2026"


@pytest.fixture
def env_guard(monkeypatch):
    for key in ("TORQUE_CLIENT", "TORQUE_WORKSPACE", "TORQUE_LAUNCH"):
        monkeypatch.setenv(key, "placeholder")
        monkeypatch.delenv(key)


def consumed(root):
    return root / "clients/acme/approvals/consumed"


def granted(root):
    return root / "clients/acme/approvals/granted"


def run_launch(root, binding, seen, monkeypatch):
    monkeypatch.chdir(root)

    def fake_exec(program, args):
        seen.update(program=program, args=args, launch=os.environ.get("TORQUE_LAUNCH"),
                    client=os.environ.get("TORQUE_CLIENT"), workspace=os.environ.get("TORQUE_WORKSPACE"))
    return cli_approval.launch(root, "Acme", PASS, execvp=fake_exec, delegated=True, binding=binding, **CLEAN)


def refusal(excinfo, reason_class):
    assert isinstance(excinfo.value, delegation.Refusal), excinfo.value
    assert excinfo.value.reason_class == reason_class, (excinfo.value.reason_class, str(excinfo.value))


def rewrite(path, **change):
    """Change a binding in place, keeping its owner (this account, the approver)."""
    record = json.loads(path.read_text())
    record.update(change)
    path.write_text(json.dumps(record))


# The brief's cases (adapted: root_owner for R41, CLEAN env, reason classes per F30).

def test_binding_launch_passes_claude_options_through(tmp_path, monkeypatch, env_guard):
    root = delegated_workspace(tmp_path, monkeypatch)
    binding = launch.create_binding(root, "Acme", model_id=MODEL, **APPROVER)
    as_agent(monkeypatch)
    seen = {}
    run_launch(root, binding["id"], seen, monkeypatch)
    assert seen["program"] == "claude" and seen["args"] == ["claude", *PASS]
    assert seen["launch"] == binding["id"] and seen["client"] == "acme"
    assert seen["workspace"] == str(root / "clients" / "acme")
    record = json.loads((consumed(root) / f"{binding['id']}.launch").read_text())
    assert (record["kind"], record["via"], record["binding_id"], record["nonce"]) == \
        ("ai", "binding", binding["id"], binding["nonce"])
    assert record["pid"] == os.getpid() and record["pid_started"]
    assert record["schema"] == launch.LAUNCH_SCHEMA and record["workspace"] == str(root)
    assert record["approver"] == {"approver": binding["approver"], "approver_uid": ME, "approver_kind": "ai",
                                  "approver_model": MODEL}
    assert (record["binding_created_at"], record["binding_expires_at"]) == \
        (binding["created_at"], binding["expires_at"])


def test_reused_and_missing_bindings_are_refused(tmp_path, monkeypatch, env_guard):
    root = delegated_workspace(tmp_path, monkeypatch)
    binding = launch.create_binding(root, "Acme", model_id=MODEL, **APPROVER)
    as_agent(monkeypatch)
    run_launch(root, binding["id"], {}, monkeypatch)
    with pytest.raises(ws.WorkspaceError, match="already used") as used:
        run_launch(root, binding["id"], {}, monkeypatch)
    refusal(used, "binding-used")
    with pytest.raises(ws.WorkspaceError, match="no launch binding") as missing:
        run_launch(root, "lnk-0123456789ab", {}, monkeypatch)
    refusal(missing, "binding-missing")


def test_expired_binding_is_refused(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    old = launch.create_binding(root, "Acme", model_id=MODEL, now=time.time() - 3600, **APPROVER)
    as_agent(monkeypatch)
    with pytest.raises(ws.WorkspaceError, match="expired") as info:
        launch.claim_binding(root, "Acme", old["id"], **CLEAN)
    refusal(info, "binding-expired")
    assert not (consumed(root) / f"{old['id']}.launch").exists()


def test_binding_owned_by_another_account_is_refused(tmp_path, monkeypatch, env_guard):
    root = delegated_workspace(tmp_path, monkeypatch)
    binding = launch.create_binding(root, "Acme", model_id=MODEL, **APPROVER)
    config = json.loads((root / "workspace.json").read_text())
    config["approver_uid"] += 1
    config["delegates"]["approver"]["uid"] += 1
    (root / "workspace.json").write_text(json.dumps(config))
    with pytest.raises(ws.WorkspaceError, match="approver account") as info:
        launch.claim_binding(root, "Acme", binding["id"], **CLEAN)
    refusal(info, "binding-invalid")


def test_delegated_launch_refused_in_a_workspace_without_a_delegated_approver(tmp_path, monkeypatch, env_guard):
    root = base_workspace(tmp_path, monkeypatch)
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    with pytest.raises(delegation.Refusal) as tier1:
        cli_approval.launch(root, "Acme", [], execvp=lambda *a: None, delegated=True, binding="lnk-0123456789ab",
                            **CLEAN)
    refusal(tier1, "tier-2-required")
    ws.set_ai_access(root, "connected", approval="required", verify="owner-uid", approver_uid=ME + 7, presence=YES)
    with pytest.raises(delegation.Refusal) as unnamed:
        cli_approval.launch(root, "Acme", [], execvp=lambda *a: None, delegated=True, binding="lnk-0123456789ab",
                            **CLEAN)
    refusal(unnamed, "not-delegated")


def test_binding_and_launch_refused_inside_an_ai_session(tmp_path, monkeypatch, env_guard):
    root = delegated_workspace(tmp_path, monkeypatch)
    with pytest.raises(delegation.Refusal) as made:
        launch.create_binding(root, "Acme", model_id=MODEL, root_owner=FAKE_OWNER, env={"CLAUDECODE": "1"},
                              ancestors=lambda: [])
    refusal(made, "agent-session")
    binding = launch.create_binding(root, "Acme", model_id=MODEL, **APPROVER)
    as_agent(monkeypatch)
    with pytest.raises(delegation.Refusal) as started:
        cli_approval.launch(root, "Acme", [], execvp=lambda *a: None, delegated=True, binding=binding["id"],
                            env={}, ancestors=lambda: [(9, "claude")])
    refusal(started, "agent-session")
    with pytest.raises(delegation.Refusal) as claimed:
        launch.claim_binding(root, "Acme", binding["id"], env={"CLAUDE_CODE_ENTRYPOINT": "cli"},
                             ancestors=lambda: [])
    refusal(claimed, "agent-session")
    assert not (consumed(root) / f"{binding['id']}.launch").exists()


def test_owner_launch_keeps_presence_and_writes_a_record(tmp_path, monkeypatch, env_guard):
    root = delegated_workspace(tmp_path, monkeypatch)
    monkeypatch.chdir(root)
    with pytest.raises(ws.WorkspaceError, match="real terminal"):
        cli_approval.launch(root, "Acme", [], execvp=lambda *a: None, presence=lambda: Presence(False, "no"))
    assert os.environ.get("TORQUE_LAUNCH") is None
    cli_approval.launch(root, "Acme", [], execvp=lambda *a: None, presence=YES)
    record_id = os.environ["TORQUE_LAUNCH"]
    record = json.loads((consumed(root) / f"{record_id}.launch").read_text())
    assert record["kind"] == "human" and record["via"] == "presence" and record_id.startswith("launch-")
    assert launch.LAUNCH_ID.fullmatch(record_id) and record["pid"] == os.getpid()


def test_launch_binding_is_admin_for_the_agent():
    from torque import connected_routes as cr
    for command in ("torque approval launch-binding --workspace . --client acme --model-id m",
                    "torque launch --workspace . --client acme --delegated --binding lnk-0123456789ab -- -p"):
        assert [r.kind for r in cr.classify("Bash", {"command": command})] == ["admin"]


# Binding creation.

def test_binding_file_shape_mode_and_location(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    t = time.time()
    binding = launch.create_binding(root, "Acme", model_id=MODEL, minutes=15, now=t, **APPROVER)
    path = granted(root) / f"{binding['id']}.json"
    assert json.loads(path.read_text()) == binding
    assert stat.S_IMODE(path.lstat().st_mode) == 0o644
    assert set(binding) == {"schema", "id", "workspace", "client", "nonce", "created_at", "expires_at", "approver",
                            "approver_uid", "approver_kind", "approver_model"}
    assert binding["schema"] == launch.BINDING_SCHEMA and launch.LAUNCH_ID.fullmatch(binding["id"])
    assert binding["id"].startswith("lnk-") and len(binding["nonce"]) == 32
    assert (binding["client"], binding["workspace"], binding["approver_uid"]) == ("acme", str(root), ME)
    assert approval._epoch(binding["expires_at"]) - approval._epoch(binding["created_at"]) == 900
    assert not list(consumed(root).iterdir())
    assert approval.list_approvals(root, "Acme") == []


@pytest.mark.parametrize("minutes", [0, 16, -1, 1.5, "10", True])
def test_binding_length_is_1_to_15_minutes(tmp_path, monkeypatch, minutes):
    root = delegated_workspace(tmp_path, monkeypatch)
    with pytest.raises(ws.WorkspaceError, match="1 to 15 minutes"):
        launch.create_binding(root, "Acme", model_id=MODEL, minutes=minutes, **APPROVER)
    assert not list(granted(root).glob("lnk-*"))


def test_binding_needs_the_delegated_approver_and_its_model(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    with pytest.raises(delegation.Refusal) as other:
        launch.create_binding(root, "Acme", model_id=MODEL, getuid=lambda: ME + 1, **APPROVER)
    refusal(other, "not-delegated")
    with pytest.raises(delegation.Refusal) as no_model:
        launch.create_binding(root, "Acme", model_id=None, **APPROVER)
    refusal(no_model, "not-delegated")
    with pytest.raises(delegation.Refusal) as same_owner:  # R41: default root_owner is this account
        launch.create_binding(root, "Acme", model_id=MODEL, **CLEAN)
    refusal(same_owner, "not-delegated")
    assert not list(granted(root).glob("lnk-*"))


def test_binding_refused_when_control_files_are_the_approvers(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    binding = launch.create_binding(root, "Acme", model_id=MODEL, **APPROVER)
    control_owner(monkeypatch, owner=ME, only="consent.json")
    with pytest.raises(delegation.Refusal) as made:
        launch.create_binding(root, "Acme", model_id=MODEL, **APPROVER)
    refusal(made, "not-delegated")
    as_agent(monkeypatch)
    with pytest.raises(delegation.Refusal) as claimed:
        launch.claim_binding(root, "Acme", binding["id"], **CLEAN)
    refusal(claimed, "not-delegated")


def test_binding_refused_for_unusable_consent(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    item = json.loads((root / "clients/acme/consent.json").read_text())
    item["status"] = "suspended"
    (root / "clients/acme/consent.json").write_text(json.dumps(item))
    with pytest.raises(delegation.Refusal) as info:
        launch.create_binding(root, "Acme", model_id=MODEL, **APPROVER)
    refusal(info, "consent-unusable")


def test_binding_does_not_create_approval_folders(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    granted(root).rmdir()
    with pytest.raises(ws.WorkspaceError):
        launch.create_binding(root, "Acme", model_id=MODEL, **APPROVER)
    assert not granted(root).exists()


# Claim checks.

def test_launching_account_must_not_be_the_approver(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    binding = launch.create_binding(root, "Acme", model_id=MODEL, **APPROVER)
    with pytest.raises(delegation.Refusal, match="approver account") as info:
        launch.claim_binding(root, "Acme", binding["id"], **CLEAN)
    refusal(info, "not-delegated")


def test_tier1_claim_is_tier_2_required(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    binding = launch.create_binding(root, "Acme", model_id=MODEL, **APPROVER)
    config = json.loads((root / "workspace.json").read_text())
    config["approval_verify"] = "hmac"
    (root / "workspace.json").write_text(json.dumps(config))
    as_agent(monkeypatch)
    with pytest.raises(delegation.Refusal) as info:
        launch.claim_binding(root, "Acme", binding["id"], **CLEAN)
    refusal(info, "tier-2-required")


@pytest.mark.parametrize("change, words", [
    ({"client": "beta"}, "another workspace or client"),
    ({"workspace": "/elsewhere"}, "another workspace or client"),
    ({"schema": "torque.launch-binding/0"}, "malformed"),
    ({"nonce": "short"}, "malformed"),
    ({"approver": "someone-else"}, "approver"),
    ({"approver_kind": "human", "approver_model": None}, "approver"),
    ({"approver_model": "bad model id!"}, "approver"),
    ({"expires_at": "not a time"}, "malformed"),
])
def test_altered_bindings_are_refused(tmp_path, monkeypatch, change, words):
    root = delegated_workspace(tmp_path, monkeypatch)
    binding = launch.create_binding(root, "Acme", model_id=MODEL, **APPROVER)
    rewrite(granted(root) / f"{binding['id']}.json", **change)
    as_agent(monkeypatch)
    with pytest.raises(delegation.Refusal, match=words) as info:
        launch.claim_binding(root, "Acme", binding["id"], **CLEAN)
    refusal(info, "binding-invalid")
    assert not (consumed(root) / f"{binding['id']}.launch").exists()


def test_binding_longer_than_the_cap_is_refused(tmp_path, monkeypatch):
    """F22: the 900 s cap holds at claim too, not only at creation."""
    root = delegated_workspace(tmp_path, monkeypatch)
    t = time.time()
    binding = launch.create_binding(root, "Acme", model_id=MODEL, now=t, **APPROVER)
    path = granted(root) / f"{binding['id']}.json"
    rewrite(path, expires_at=approval._iso(t + launch.BINDING_TTL_MAX + approval.SKEW + 1))
    as_agent(monkeypatch)
    with pytest.raises(delegation.Refusal, match="longer than") as info:
        launch.claim_binding(root, "Acme", binding["id"], now=t, **CLEAN)
    refusal(info, "binding-invalid")
    rewrite(path, expires_at=approval._iso(t + launch.BINDING_TTL_MAX + approval.SKEW))
    assert launch.claim_binding(root, "Acme", binding["id"], now=t, **CLEAN)["binding_id"] == binding["id"]


def test_future_dated_binding_is_refused(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    binding = launch.create_binding(root, "Acme", model_id=MODEL, now=time.time() + 600, **APPROVER)
    as_agent(monkeypatch)
    with pytest.raises(delegation.Refusal, match="future") as info:
        launch.claim_binding(root, "Acme", binding["id"], **CLEAN)
    refusal(info, "binding-invalid")


def test_binding_expires_at_its_expiry(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    t = float(int(time.time()))  # whole seconds: the record's times are
    binding = launch.create_binding(root, "Acme", model_id=MODEL, minutes=1, now=t, **APPROVER)
    as_agent(monkeypatch)
    with pytest.raises(delegation.Refusal) as info:
        launch.claim_binding(root, "Acme", binding["id"], now=t + 61, **CLEAN)
    refusal(info, "binding-expired")
    assert launch.claim_binding(root, "Acme", binding["id"], now=t + 60, **CLEAN)["id"] == binding["id"]


def test_symlinked_or_writable_binding_is_refused(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    first = launch.create_binding(root, "Acme", model_id=MODEL, **APPROVER)
    second = launch.create_binding(root, "Acme", model_id=MODEL, **APPROVER)
    real = tmp_path / "planted.json"
    path = granted(root) / f"{first['id']}.json"
    real.write_text(path.read_text())
    path.unlink()
    path.symlink_to(real)
    (granted(root) / f"{second['id']}.json").chmod(0o666)
    as_agent(monkeypatch)
    for binding in (first, second):
        with pytest.raises(delegation.Refusal) as info:
            launch.claim_binding(root, "Acme", binding["id"], **CLEAN)
        refusal(info, "binding-invalid")


def test_binding_in_a_folder_others_can_write_is_refused(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    binding = launch.create_binding(root, "Acme", model_id=MODEL, **APPROVER)
    granted(root).chmod(0o777)
    as_agent(monkeypatch)
    try:
        with pytest.raises(delegation.Refusal, match="approver account") as info:
            launch.claim_binding(root, "Acme", binding["id"], **CLEAN)
        refusal(info, "binding-invalid")
    finally:
        granted(root).chmod(0o755)


@pytest.mark.parametrize("content", [b"not json", b"[1, 2]", b"\xff\xfe bad utf-8"])
def test_unreadable_binding_fails_closed(tmp_path, monkeypatch, content):
    root = delegated_workspace(tmp_path, monkeypatch)
    binding = launch.create_binding(root, "Acme", model_id=MODEL, **APPROVER)
    (granted(root) / f"{binding['id']}.json").write_bytes(content)
    as_agent(monkeypatch)
    with pytest.raises(delegation.Refusal, match="malformed") as info:
        launch.claim_binding(root, "Acme", binding["id"], **CLEAN)
    refusal(info, "binding-invalid")


@pytest.mark.parametrize("bad", ["lnk-0123", "apr-0123456789ab", "launch-0123456789ab", "../lnk-0123456789ab",
                                 None])
def test_binding_id_shape(tmp_path, monkeypatch, bad):
    root = delegated_workspace(tmp_path, monkeypatch)
    as_agent(monkeypatch)
    with pytest.raises(delegation.Refusal, match="lnk-") as info:
        launch.claim_binding(root, "Acme", bad, **CLEAN)
    refusal(info, "binding-invalid")


def test_human_delegate_binding_is_recorded_via_binding(tmp_path, monkeypatch):
    """F8: a human-kind approver's binding makes a kind human record that still
    says via binding and carries its binding_id, so the gate re-checks it."""
    root = delegated_workspace(tmp_path, monkeypatch, kind="human")
    binding = launch.create_binding(root, "Acme", model_id=None, **APPROVER)
    assert binding["approver_kind"] == "human" and binding["approver_model"] is None
    as_agent(monkeypatch)
    record = launch.claim_binding(root, "Acme", binding["id"], **CLEAN)
    assert (record["kind"], record["via"], record["binding_id"]) == ("human", "binding", binding["id"])


def test_unreadable_process_start_fails_closed(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    binding = launch.create_binding(root, "Acme", model_id=MODEL, **APPROVER)
    as_agent(monkeypatch)
    with pytest.raises(ws.WorkspaceError, match="start time"):
        launch.claim_binding(root, "Acme", binding["id"], starts=lambda pid: None, **CLEAN)
    with pytest.raises(ws.WorkspaceError, match="start time"):
        launch.write_launch_record(root, "Acme", "human", starts=lambda pid: None)
    assert not list(consumed(root).iterdir())
    record = launch.claim_binding(root, "Acme", binding["id"], pid=4242, starts=lambda pid: START, **CLEAN)
    assert (record["pid"], record["pid_started"]) == (4242, START)


# Launch records.

def test_launch_record_kinds_and_shape(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    human = launch.write_launch_record(root, "Acme", "human", pid=77, starts=lambda pid: START)
    probe = launch.write_launch_record(root, "Acme", "probe", starts=lambda pid: START)
    assert (human["kind"], human["via"], human["pid"]) == ("human", "presence", 77)
    assert (probe["kind"], probe["via"], probe["pid"]) == ("probe", "probe", os.getpid())
    assert human["id"].startswith("launch-") and probe["id"].startswith("probe-")
    for record in (human, probe):
        assert launch.LAUNCH_ID.fullmatch(record["id"])
        assert json.loads((consumed(root) / f"{record['id']}.launch").read_text()) == record
        assert set(record) == {"schema", "id", "kind", "via", "client", "workspace", "pid", "pid_started",
                               "created_at"}
    with pytest.raises(ws.WorkspaceError, match="human or probe"):
        launch.write_launch_record(root, "Acme", "ai")


def test_launch_record_creates_missing_folders_without_remoding(tmp_path, monkeypatch):
    """F1: the human and probe records create approvals/ folders only when absent."""
    root = base_workspace(tmp_path, monkeypatch)
    base = root / "clients/acme/approvals"
    assert not base.exists()
    record = launch.write_launch_record(root, "Acme", "probe", starts=lambda pid: START)
    assert (base / "consumed" / f"{record['id']}.launch").is_file()
    assert stat.S_IMODE((base / "consumed").stat().st_mode) == 0o700
    (base / "granted").chmod(0o755)
    launch.write_launch_record(root, "Acme", "human", starts=lambda pid: START)
    assert stat.S_IMODE((base / "granted").stat().st_mode) == 0o755


def test_claim_creates_only_the_consumed_folder(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    binding = launch.create_binding(root, "Acme", model_id=MODEL, **APPROVER)
    consumed(root).rmdir()
    (root / "clients/acme/approvals/requests").rmdir()
    as_agent(monkeypatch)
    launch.claim_binding(root, "Acme", binding["id"], **CLEAN)
    assert stat.S_IMODE(consumed(root).stat().st_mode) == 0o700
    assert not (root / "clients/acme/approvals/requests").exists()


def test_claim_of_a_missing_binding_creates_nothing(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    consumed(root).rmdir()
    as_agent(monkeypatch)
    with pytest.raises(delegation.Refusal):
        launch.claim_binding(root, "Acme", "lnk-0123456789ab", **CLEAN)
    assert not consumed(root).exists()


# Process ancestry (F7).

class FakePs:
    """A process table for `ps`: pid -> (ppid, lstart)."""

    def __init__(self, table):
        self.table, self.calls = table, 0

    def __call__(self, argv):
        self.calls += 1
        assert argv[0] == "ps"
        pid = int(argv[-1])
        if pid not in self.table:
            return ""
        ppid, started = self.table[pid]
        return str(ppid) if "ppid=" in argv else started


def chain(length, start=100):
    """Process 1000 whose parent is 1001, and so on up to `length` ancestors."""
    return {1000 + i: (1000 + i + 1, f"start {1000 + i}") for i in range(length + 1)}


def test_ancestor_walk_is_lazy_and_capped():
    ps = FakePs(chain(20))
    assert launch.ancestor_pids(getppid=lambda: 1000, run=ps) == list(range(1000, 1000 + launch.MAX_ANCESTORS))
    assert launch.MAX_ANCESTORS == 7 and ps.calls == launch.MAX_ANCESTORS - 1
    ps = FakePs(chain(20))
    assert launch.ancestor_pids(stop=1002, getppid=lambda: 1000, run=ps) == [1000, 1001, 1002]
    assert ps.calls == 2
    ps = FakePs({1000: (1, "x")})
    assert launch.ancestor_pids(getppid=lambda: 1000, run=ps) == [1000]


@pytest.mark.parametrize("target", [999, 1000, 1003, 1006, 1007, 1019, 5555])
def test_launch_process_check_makes_at_most_8_ps_calls(target):
    ps = FakePs({999: (1000, "start 999"), **chain(20)})
    problem = launch.launch_process_problem(target, f"start {target}", getpid=lambda: 999, getppid=lambda: 1000,
                                            run=ps)
    assert ps.calls <= 8
    within = target == 999 or 1000 <= target < 1000 + launch.MAX_ANCESTORS
    assert (problem == "") == within, problem


def test_launch_process_check_needs_the_same_start_time():
    ps = FakePs({999: (1000, "start 999"), **chain(3)})
    assert launch.launch_process_problem(1001, "start 1001", getpid=lambda: 999, getppid=lambda: 1000, run=ps) == ""
    assert "replaced" in launch.launch_process_problem(1001, "earlier", getpid=lambda: 999, getppid=lambda: 1000,
                                                       run=ps)
    assert launch.launch_process_problem(1001, None, getpid=lambda: 999, getppid=lambda: 1000, run=ps)
    assert launch.launch_process_problem("1001", "start 1001", getpid=lambda: 999, getppid=lambda: 1000, run=ps)


def test_process_start_reads_this_process():
    assert launch.process_start(os.getpid())
    assert launch.process_start(os.getpid(), run=lambda argv: "  Fri  Sep 25  10:00:00 2026\n") == START
    assert launch.process_start(4242, run=lambda argv: "") is None


# CLI.

def test_cli_launch_binding_refuses_inside_an_ai_session(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("CLAUDECODE", "1")
    code = cli.main(["approval", "launch-binding", "--workspace", str(root), "--client", "Acme",
                     "--model-id", MODEL, "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 3 and out["refused"] and out["reason_class"] == "agent-session"
    assert not list(granted(root).glob("lnk-*"))


def test_cli_launch_binding_prints_the_binding(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    made = {}

    def fake_create(workspace, client, **kwargs):
        made.update(workspace=workspace, client=client, **kwargs)
        return {"id": "lnk-0123456789ab", "client": "acme", "expires_at": "2026-09-25T10:10:00+00:00"}
    monkeypatch.setattr(launch, "create_binding", fake_create)
    assert cli.main(["approval", "launch-binding", "--workspace", str(root), "--client", "Acme", "--model-id",
                     MODEL, "--minutes", "5", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == "lnk-0123456789ab"
    assert (made["model_id"], made["minutes"]) == (MODEL, 5)
    assert cli.main(["approval", "launch-binding", "--workspace", str(root), "--client", "Acme", "--model-id",
                     MODEL]) == 0
    assert "torque launch" in capsys.readouterr().out and made["minutes"] == 10


def test_cli_launch_flag_usage(tmp_path, monkeypatch, capsys, env_guard):
    root = delegated_workspace(tmp_path, monkeypatch)
    base = ["launch", "--workspace", str(root), "--client", "Acme"]
    assert cli.main([*base, "--binding", "lnk-0123456789ab"]) == 2
    assert "--delegated" in capsys.readouterr().err
    assert cli.main([*base, "--delegated"]) == 2
    assert "--binding" in capsys.readouterr().err
    seen = {}
    monkeypatch.setattr(cli_approval, "launch", lambda *a, **k: seen.update(args=a, kwargs=k) or 0)
    assert cli.main([*base, "--delegated", "--binding", "lnk-0123456789ab", "--", *PASS]) == 0
    assert seen["args"][2] == PASS and seen["kwargs"] == {"delegated": True, "binding": "lnk-0123456789ab"}


def test_launch_binding_is_a_fixed_deny_rule():
    from torque import permissions
    assert "Bash(torque approval launch-binding:*)" in permissions.FIXED_DENY_RULES
    assert "Bash(torque launch:*)" in permissions.FIXED_DENY_RULES
