"""Guarded reads at the gate: how each `torque guarded` call is classed, and what the
connected-mode decision does with it."""
from collections import namedtuple
import os
from pathlib import Path

import pytest

from torque import consent, gate, gate_connected as gc, workspace as ws
from torque.connected_routes import classify
from torque.presence import Presence

Org = namedtuple("Org", "org_id_18 detected_org_type is_production instance_url", defaults=(None,))
ORGS = {"acme-prod": Org("00D000000000002AAA", "production", True, "https://acme.my.salesforce.com"),
        "acme-dev": Org("00D000000000003AAA", "sandbox", False, "https://acme--dev.sandbox.my.salesforce.com"),
        "acme-full": Org("00D000000000004AAA", "sandbox", False, "https://acme--full.sandbox.my.salesforce.com")}
YES = lambda: Presence(True, "")
C = "--workspace . --client acme"
T = C + " --target-org acme-prod"
B = lambda cmd, tool="Bash": [(r.kind, r.org, r.data) for r in classify(tool, {"command": cmd})]


@pytest.fixture
def w(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    root = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(root, "Acme")
    ws.add_client(root, "Other")
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    ws.set_guarded_reads(root, "on", presence=YES)
    letter = tmp_path / "a.pdf"
    letter.write_bytes(b"agreement")
    consent.record_consent(root, "Acme", "2026-09-30", letter, ["metadata"], list(ORGS), ["Contact"], presence=YES,
                           resolve=ORGS.get, org_data={"acme-prod": ["counts", "test_records"],
                                                       "acme-full": ["records"]})
    consent.sign_off(root, "Acme", "Reviewer", presence=YES)
    return Path(os.path.realpath(root))


def run(root, command, mode=None, tool="Bash", cwd=None, client="acme"):
    return gc.decide_connected(tool, {"command": command}, root, cwd or root, env={"TORQUE_CLIENT": client},
                               permission_mode=mode, session_id="s1", tool_use_id="t1")


@pytest.mark.parametrize("command,expected", [
    (f"torque guarded counts {T} --object Opportunity --group-by StageName", ("read", "acme-prod", "counts")),
    (f"torque guarded counts {T} --object Opportunity --where \"IsWon = true\"", ("read", "acme-prod", "counts")),
    (f"torque guarded fill {T} --object Contact --fields Email,Phone", ("read", "acme-prod", "counts")),
    (f"torque guarded config {T} --object Settings__c", ("read", "acme-prod", "config_records")),
    (f"torque guarded record {T} --object Account --id 001000000000001 --all", ("read", "acme-prod", "test_records")),
    (f"torque guarded related {T} --id 001000000000001 --child Opportunity.AccountId", ("read", "acme-prod",
                                                                                        "test_records")),
    (f"torque guarded org {T}", ("read", "acme-prod", None)),
    (f"torque guarded policy candidates {T} --object Opportunity", ("read", "acme-prod", None)),
    (f"python -m torque guarded counts {T} --object Opportunity", ("read", "acme-prod", "counts")),
    (f"torque guarded counts --target-org=acme-prod {C} --object Opportunity", ("read", "acme-prod", "counts")),
    (f"torque guarded policy show {C}", ("local", None, None)),
    (f"torque guarded test-records list {C}", ("local", None, None)),
    (f"torque guarded exposure {C} --since 2026-10-01", ("local", None, None)),
    ("torque guarded --help", ("local", None, None)),
    (f"torque guarded counts {C} --object Opportunity", ("no_org", None, "counts")),
    (f"torque guarded counts {T} --target-org acme-dev --object Opportunity", ("no_org", None, "counts")),
    (f"torque guarded org {C}", ("no_org", None, None)),
])
def test_routes(command, expected):
    assert B(command) == [expected], command


@pytest.mark.parametrize("command", [
    f"torque guarded policy release {T} --field Opportunity.StageName --as exact",
    f"torque guarded policy release-object {T} --object Settings__c",
    f"torque guarded policy unrelease {C} --field Opportunity.StageName",
    f"torque guarded policy unrelease-object {C} --object Settings__c",
    f"torque guarded policy set-min-cell {C} --value 3",
    f"torque guarded policy set-classification {C} --value ignore",
    f"torque guarded policy {C}",
    f"torque guarded test-records add {T} --object Account --id 001000000000001",
    f"torque guarded test-records remove {C} --id 001000000000001",
    f"torque guarded test-records {C}",
    "torque guarded", f"torque guarded rows {T} --object Contact", f"torque guarded --json counts {T}",
    "torque workspace guarded-reads on --path .", "torque workspace guarded-reads off --path .",
    # the org is named with --target-org and nothing else
    f"torque guarded counts {C} -o acme-prod --object Opportunity",
    f"torque guarded counts {C} --org acme-prod --object Opportunity",
    f"torque guarded counts {T} -o acme-dev --object Opportunity",
    f"torque guarded counts {T} --targetusername acme-dev --object Opportunity",
    # run-time words and `--`, as for every command of Torque's own
    f"torque guarded counts {T} --object $OBJ", f"torque guarded counts {T} --object Opportunity $MORE",
    f"torque guarded ${{x:-policy}} release {T}", f"torque guarded counts {T} --object Opp*",
    f"torque -- guarded policy release {T} --field Opportunity.StageName --as exact",
    f"torque guarded -- policy release {T}", f"torque guarded counts {T} --object Opportunity -- --json",
    f'torque guarded counts {T} --object Opportunity "$MORE"',
])
def test_owner_commands_and_odd_spellings_are_refused(w, command):
    assert [kind for kind, _, _ in B(command)] == ["admin"], command
    for mode in (None, "bypassPermissions"):
        assert run(w, command, mode).action == "deny"


def test_a_quoted_value_stays_a_value():
    assert B(f"torque guarded counts {T} --object Opportunity --where \"StageName = 'Closed Won'\"") == [
        ("read", "acme-prod", "counts")]
    assert B(f"torque guarded counts {T} --object Opportunity --where 'Amount >= 5000' --json") == [
        ("read", "acme-prod", "counts")]
    assert B(f'torque guarded counts {T} --object "$OBJ"') == [("read", "acme-prod", "counts")]


def test_the_decision(w):
    counts = "torque guarded counts --workspace . --client acme --object Opportunity --target-org "
    assert run(w, counts + "acme-prod").action == "allow"
    assert run(w, counts + "acme-prod", "bypassPermissions").action == "allow"       # a read: no prompt needed
    assert run(w, counts + "acme-full").action == "allow"                             # records implies the lanes
    denied = run(w, counts + "acme-dev")
    assert denied.action == "deny" and "does not cover counts" in denied.reason
    config = "torque guarded config --workspace . --client acme --object Settings__c --target-org "
    denied = run(w, config + "acme-prod")                                             # counts and test records only
    assert denied.action == "deny" and "configuration records" in denied.reason
    assert run(w, config + "acme-full").action == "allow"
    assert "not in acme's consent" in run(w, counts + "elsewhere").reason
    assert run(w, "torque guarded org --workspace . --client acme --target-org acme-dev").action == "allow"
    assert run(w, "torque guarded policy show --workspace . --client acme").action == "allow"


def test_the_setting_off_refuses_the_lanes(w):
    ws.set_guarded_reads(w, "off", presence=YES)
    denied = run(w, "torque guarded counts --workspace . --client acme --object Opportunity --target-org acme-prod")
    assert denied.action == "deny" and "guarded reads are off" in denied.reason
    # records consent does not turn the lanes on either: the switch is the workspace's
    denied = run(w, "torque guarded counts --workspace . --client acme --object Opportunity --target-org acme-full")
    assert denied.action == "deny" and "guarded reads are off" in denied.reason
    # the two guarded commands that read only metadata are behind the same switch
    for command in ("torque guarded org --workspace . --client acme --target-org acme-prod",
                    "torque guarded policy candidates --workspace . --client acme --target-org acme-prod --object Contact"):
        denied = run(w, command)
        assert denied.action == "deny" and "guarded reads are off" in denied.reason, command
    assert run(w, "torque guarded policy show --workspace . --client acme").action == "allow"       # local, no org


def test_the_mode_is_written_so_that_an_older_torque_reads_build_only(w):
    import json
    from pathlib import Path
    from torque import gate
    stored = lambda: json.loads((Path(w) / "workspace.json").read_text(encoding="utf-8"))
    # what a Torque from before guarded reads makes of the mode (its gate._resolve_ai_access): a
    # word it does not know is build-only, so its hook refuses org work there
    older = lambda value, approval: ("full" if value == "full" else
                                     "connected" if value == "connected" and approval == "required" else "build-only")
    on = stored()
    assert (on["ai_access"], on["schema"], on["guarded_reads"]) == ("connected-guarded", "torque.workspace/2", "on")
    assert older(on["ai_access"], on.get("approval")) == "build-only"
    assert gate._resolve_ai_access(on["ai_access"], on.get("approval")) == "connected"
    assert ws.access_mode(on) == "connected" and gate._workspace_mode(Path(w))[1] == "connected"
    assert ws.guarded_reads_on(on) and not ws.guarded_reads_on({**on, "ai_access": "connected"})
    # setting connected mode again keeps guarded reads on, and the way the mode is written
    ws.set_ai_access(w, "connected", approval="required", presence=YES)
    assert stored()["ai_access"] == "connected-guarded" and ws.guarded_reads_on(stored())
    ws.set_guarded_reads(w, "off", presence=YES)
    off = stored()
    assert (off["ai_access"], off["schema"]) == ("connected", "torque.workspace/1") and "guarded_reads" not in off
    assert older(off["ai_access"], off.get("approval")) == "connected"
    # leaving connected mode switches guarded reads off with it
    ws.set_guarded_reads(w, "on", presence=YES)
    ws.set_ai_access(w, "build-only", presence=YES)
    left = stored()
    assert (left["ai_access"], left["schema"]) == ("build-only", "torque.workspace/1") and "guarded_reads" not in left


def test_another_client_or_workspace_is_refused(w, tmp_path):
    other = "torque guarded counts --workspace . --client other --object Opportunity --target-org acme-prod"
    assert "bound to acme" in run(w, other).reason
    assert run(w, "torque guarded policy show --workspace . --client other").action == "deny"
    assert run(w, "torque guarded counts --workspace . --client acme --object Opportunity --target-org acme-prod",
               client="").action == "deny"                                            # no client bound
    second = ws.init_workspace(tmp_path / "second", "Second")
    elsewhere = f"torque guarded counts --workspace {Path(second).as_posix()} --client acme --object Opportunity " \
                "--target-org acme-prod"
    denied = run(w, elsewhere)
    assert denied.action == "deny" and "another workspace" in denied.reason
    assert run(w, "torque guarded counts --workspace .. --client acme --object Opportunity --target-org acme-prod",
               cwd=w / "clients").action == "allow"                                   # the same workspace, from below
    assert run(w, f"torque guarded policy show --workspace {Path(second).as_posix()} --client acme").action == "deny"


def test_metadata_must_be_among_the_orgs_classes(w, tmp_path):
    letter = tmp_path / "b.pdf"
    letter.write_bytes(b"agreement")
    item = consent.load_consent(w, "Acme")
    item["data_allowed"] = ["local_artifacts"]
    assert "a guarded data class needs metadata for the same org" in consent.consent_problems(item, client="acme")


def test_build_only_refuses_the_lanes(w):
    ws.set_ai_access(w, "build-only", presence=YES)
    ok, reason = gate._decide("Bash", {"command": "torque guarded counts --workspace . --client acme --object "
                                                  "Opportunity --target-org acme-prod"}, w, w)
    assert not ok and "Build-only" in reason


def test_powershell_reading_meets_the_binding(w):
    inner = ("powershell -NoProfile -Command 'Remove-Item Env:TORQUE_CLIENT; torque guarded record "
             "--workspace C:/Work/W2 --client other --target-org acme-prod --object Contact --id 003000000000001 "
             "--fields Name'")
    decision = run(w, inner, tool="PowerShell")
    assert decision.action == "deny" and "bound to acme" in decision.reason


# ---- round 4

@pytest.mark.parametrize("command", [
    "cd ../other && torque guarded counts --workspace . --client acme --object Opportunity --target-org acme-prod",
    "cd ../other; torque guarded record --workspace . --client acme --target-org acme-prod --object Account "
    "--id 001000000000001 --all",
    "pushd ../other\ntorque guarded counts --workspace . --client acme --object Opportunity --target-org acme-prod",
    "torque guarded counts --workspace . --client acme --object Opportunity --target-org acme-prod | head -5",
    "torque guarded counts --workspace . --client acme --object Opportunity --target-org acme-prod > out.txt",
])
def test_a_guarded_read_runs_on_its_own(w, command):
    decision = run(w, command)
    assert decision.action == "deny" and "on its own" in decision.reason, decision.reason


def test_a_guarded_read_in_another_folder_is_asked_about(w):
    command = ("env -C ../other torque guarded counts --workspace . --client acme --object Opportunity "
               "--target-org acme-prod")
    assert run(w, command).action == "ask"
    assert run(w, command, "bypassPermissions").action == "deny"


@pytest.mark.parametrize("command,why", [
    ("torque guarded config --workspace . --client acme --target-org acme-prod --object Settings__c # --help",
     "configuration records"),
    ("torque guarded counts --workspace . --client acme --target-org acme-dev --object Opportunity # -h", "counts"),
    ("torque guarded policy release --workspace . --client acme --target-org acme-prod --field Opportunity.StageName "
     "--as exact # --help", "only the consultant"),
    ("torque guarded test-records add --workspace . --client acme --target-org acme-prod --object Account "
     "--id 001000000000001 # -h", "only the consultant"),
    ("torque guarded counts --workspace ../second --client acme --target-org acme-prod --object Opportunity # --help",
     "another workspace"),
])
def test_help_behind_a_comment_does_not_make_a_guarded_call_local(w, command, why):
    decision = run(w, command)
    assert decision.action == "deny" and why in decision.reason, decision.reason


def test_help_itself_is_local(w):
    assert run(w, "torque guarded counts --help").action == "allow"
    assert run(w, "torque guarded --help").action == "allow"
    assert run(w, "torque guarded counts --workspace ../second --help").action == "deny"


def test_a_home_relative_workspace_is_the_same_workspace(w, monkeypatch):
    monkeypatch.setenv("HOME", str(w.parent))
    monkeypatch.setenv("USERPROFILE", str(w.parent))
    command = f"torque guarded counts --workspace '~/{w.name}' --client acme --object Opportunity --target-org acme-prod"
    assert run(w, command).action == "allow"
    assert run(w, command.replace(w.name, "elsewhere")).action == "deny"


@pytest.mark.parametrize("command", [
    "torque guarded policy show --workspace . --client acme # inspect the releases",
    "torque guarded test-records list --workspace . --client acme # which records",
    "torque guarded exposure --workspace . --client acme # what was shown",
    "torque guarded counts --workspace . --client acme --object Opportunity --target-org acme-prod # by stage next",
])
def test_a_plain_trailing_comment_changes_nothing(w, command):
    assert run(w, command).action == "allow", run(w, command).reason


def test_options_behind_a_comment_still_count(w):
    assert run(w, "torque guarded policy show --workspace . --client acme # --workspace ../second").action == "deny"
    assert run(w, "torque guarded counts --workspace . --client acme --object Opportunity # --target-org acme-prod"
               ).action == "deny"


@pytest.mark.parametrize("workspace", [
    '--workspace "$PWD/.."', '--workspace="$PWD/.."', '--workspace "$PWD"', '--workspace "`pwd`/.."',
    '--workspace "$(pwd)/.."', "--workspace ~+/..", "--workspace ~-", "--workspace ~1/x", "--workspace ~other/firm",
    "--workspace=~+/..",
])
def test_a_guarded_read_names_its_workspace_with_a_path_written_out(w, workspace):
    # the gate compares the path as written with the workspace the session works in: `"$PWD/.."`
    # and `~+/..` would read as this folder and run in its parent
    command = f"torque guarded counts {workspace} --client acme --target-org acme-prod --object Opportunity"
    assert [route[0] for route in B(command)] == ["admin"], (command, B(command))
    assert run(w, command).action == "deny"


def test_a_workspace_path_written_out_is_read_as_before(w):
    for workspace in ("--workspace .", "--workspace ~/firm", "--workspace=.", "--workspace './'"):
        command = f"torque guarded counts {workspace} --client acme --target-org acme-prod --object Opportunity"
        assert B(command) == [("read", "acme-prod", "counts")], (command, B(command))
    assert run(w, f"torque guarded counts {T} --object Opportunity").action == "allow"

