"""Connected mode refuses, for the session and whatever its approvals, a command
that prints or takes a credential (`sf org display` prints an access token, `sf org
open --url-only` a login URL) and a command that lists every org the machine is
logged in to (`sf org list`). Neither is a read, a write an approval can cover, or a
question for the consultant."""
from collections import namedtuple
import io
import json
import os
from pathlib import Path

import pytest

from torque import approval, changes, consent, gate, gate_connected as gc, workspace as ws
from torque import connected_routes as cr
from torque.presence import Presence

Org = namedtuple("Org", "org_id_18 detected_org_type is_production instance_url", defaults=(None,))
ORGS = {"acme-prod": Org("00D000000000002AAA", "production", True, "https://acme.my.salesforce.com"),
        "acme-sbx": Org("00D000000000001AAA", "sandbox", False, "https://acme--sbx.sandbox.my.salesforce.com")}
YES = lambda: Presence(True, "")
QUERY = 'sf data query --target-org acme-prod -q "SELECT Id, Name, IsSandbox FROM Organization"'

K = lambda tool, inp: [(r.kind, r.org) for r in cr.classify(tool, inp)]
B = lambda cmd: K("Bash", {"command": cmd})
P = lambda cmd: K("PowerShell", {"command": cmd})


@pytest.fixture(autouse=True)
def no_container_mode(monkeypatch):
    # The classifier reads these from the environment the hook inherits.
    for name in cr.SF_CONTAINER_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def w(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    root = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(root, "Acme")
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    record(root, tmp_path, ["metadata", "records"])
    return Path(os.path.realpath(root))


def record(root, tmp_path, data):
    letter = tmp_path / "a.pdf"
    letter.write_bytes(b"agreement")
    consent.record_consent(root, "Acme", "2026-09-30", letter, data, ["acme-prod", "acme-sbx"], ["Contact"],
                           presence=YES, resolve=ORGS.get)
    consent.sign_off(root, "Acme", "Reviewer", presence=YES)


def run(root, tool, inp, env=None, mode=None):
    return gc.decide_connected(tool, inp, root, root, env={"TORQUE_CLIENT": "acme"} if env is None else env,
                               permission_mode=mode, session_id="s1", tool_use_id="t1")


DISPLAY = [
    "sf org display", "sf org display -o acme-prod", "sf org display --target-org acme-prod",
    "sf org display --target-org=acme-prod --json", "sf org display --verbose -o acme-prod",
    "sf org display -o acme-prod --verbose --json", "sf org display user -o acme-prod",
    "sf org display user --json", "sf.cmd org display -o acme-prod", "sf.exe org display -o acme-prod",
    "SF.CMD org display -o acme-prod", "/usr/local/bin/sf org display -o acme-prod",
    "sf force:org:display -u acme-prod", "sf force:org:display --verbose -u acme-prod",
    "sfdx force:org:display -u acme-prod", "sfdx force:org:display --targetusername acme-prod --verbose --json",
    "sfdx.cmd force:org:display -u acme-prod", "sfdx force:user:display -u acme-prod",
    # The colon spelling, and the words in another order, which the CLI also accepts.
    "sf org:display -o acme-prod", "sf org:display:user -o acme-prod", "sf display org -o acme-prod",
    "sf display user org -o acme-prod", "sf user org display -o acme-prod", "sf ORG DISPLAY -o acme-prod",
    "sf org display --help",
]
SIBLINGS = [
    # A login URL.
    "sf org open -o acme-prod --url-only", "sf org open --url-only", "sf org open -o acme-prod -r",
    "sf org open -ro acme-prod", "sf org open -o acme-prod --urlonly", "sf org open -o acme-prod --json",
    "sf org open -o acme-prod --path /lightning/setup/SetupOneHome/home -r", "sf open org -r -o acme-prod",
    "sf org:open -o acme-prod --url-only", "sf force:org:open -u acme-prod -r",
    "sfdx force:org:open -u acme-prod --urlonly", "sf org open agent --api-name Bot -o acme-prod --url-only",
    # Files in a flags folder can set --url-only; a word the shell builds could be it.
    "sf org open -o acme-prod --flags-dir flags", "sf org open -o acme-prod ${HOME:+--url-only}",
    "sf org open -o acme-prod $FLAG", "sf org open -o acme-prod --url-onl?", "sf org open -o acme-prod *",
    # A password.
    "sf org generate password -o acme-prod", "sf org generate password --on-behalf-of u@example.com -o acme-prod",
    "sf generate password org -o acme-prod", "sfdx force:user:password:generate -u acme-prod",
    # A login: it takes a credential, prints tokens with --json, and can point an alias at another org.
    "sf org login web -a acme-prod", "sf org login web --json", "sf org login jwt -o u@example.com -f key -i id",
    "sf org login sfdx-url --sfdx-url-file auth.txt", "sf org login access-token --instance-url https://example.org",
    "sf org login device", "sf web login org", "sf login org web", "sf org:login:web",
    "sf auth web login", "sf auth jwt grant", "sfdx auth:web:login -a acme-prod", "sfdx force:auth:jwt:grant -u x",
    "sfdx force:auth:sfdxurl:store -f auth.txt", "sfdx auth:accesstoken:store", "sfdx auth:device:login",
    "sf org auth show-access-token -o acme-prod", "sf org auth show-sfdx-auth-url -o acme-prod",
    "sf org auth show-user-password -o acme-prod",
]
ALL_ORGS = [
    "sf org list", "sf org list --json", "sf org list --all", "sf org list --verbose",
    "sf org list --all --verbose --json", "sf org list --clean", "sf org list auth", "sf org list auth --json", "sf org list -o acme-prod",
    "sf.cmd org list", "sf force:org:list", "sf force:org:list --all", "sfdx force:org:list",
    "sfdx force:org:list --all --verbose --json", "sfdx.cmd force:org:list", "sf org:list", "sf list org",
    "sf list auth org", "sf auth list", "sfdx force:auth:list", "sfdx auth:list --json", "sf alias list",
    "sf alias list --json", "sfdx force:alias:list", "sf env list",
]


@pytest.mark.parametrize("command", DISPLAY + SIBLINGS)
def test_a_command_that_prints_or_takes_a_credential_is_its_own_route(command):
    assert B(command) == [("credential", None)]


@pytest.mark.parametrize("command", ALL_ORGS)
def test_a_command_that_lists_every_org_is_its_own_route(command):
    assert B(command) == [("all_orgs", None)]


SHORTENED = [
    # The CLI completes a shortened command when only one command fits its words and
    # flags: `sf display --verbose -o ALIAS` runs `org display`.
    ("sf display --verbose -o acme-prod", "credential"), ("sf display -o acme-prod", "credential"),
    ("sf display user -o acme-prod", "credential"), ("sf user display -o acme-prod", "credential"),
    ("sf user -o acme-prod", "credential"), ("sf org --verbose -o acme-prod", "credential"),
    ("sf org", "credential"), ("sf password -o acme-prod", "credential"),
    ("sf generate password -o acme-prod", "credential"), ("sf password org -o acme-prod", "credential"),
    ("sf login", "credential"), ("sf login web", "credential"), ("sf web", "credential"),
    ("sf jwt -o u@example.com -f key -i id", "credential"), ("sf device login", "credential"),
    ("sf auth", "credential"), ("sf sfdxurl store -f auth.txt", "credential"),
    ("sf open -r -o acme-prod", "credential"), ("sf open --url-only -o acme-prod", "credential"),
    ("sf open agent --json -o acme-prod", "credential"),
    ("sf list", "all_orgs"), ("sf list --all --json", "all_orgs"), ("sf list auth", "all_orgs"),
    ("sf alias", "all_orgs"), ("sf env", "all_orgs"), ("sfdx list", "all_orgs"),
]


@pytest.mark.parametrize("command,kind", SHORTENED)
def test_a_shortened_command_is_refused_as_the_command_it_can_become(command, kind):
    assert B(command) == [(kind, None)]


def test_the_reason_for_a_shortened_command_names_the_full_one(w):
    route = cr.classify("Bash", {"command": "sf display --verbose -o acme-prod"})[0]
    assert route.detail == "sf display --verbose -o acme-prod (the CLI can complete this to sf org display user)"
    decision = run(w, "Bash", {"command": "sf display --verbose -o acme-prod"})
    assert decision.action == "deny" and "the CLI can complete this to sf org display user" in decision.reason
    assert "the CLI can complete this to sf org list auth" in run(w, "Bash", {"command": "sf list"}).reason


@pytest.mark.parametrize("command,expected", [
    # Words from no refused command, or one word too many, are an ordinary unknown command.
    ("sf open -o acme-prod", [("org_write", "acme-prod")]),
    # ... unless its words all come from a record or log read, which the CLI can complete it to.
    ("sf query -q x -o acme-prod", [("admin", "acme-prod")]),
    ("sf list users -o acme-prod", [("admin", "acme-prod")]),
    ("sf display limits -o acme-prod", [("org_write", "acme-prod")]),
    ("sf project generate --name demo", [("local", None)]),
    ("sf config list", [("local", None)]),
    ("sf sobject list -o acme-prod", [("read", "acme-prod")]),
    ("sf package installed list -o acme-prod", [("read", "acme-prod")]),
    ("sf org create user -o acme-prod", [("org_write", "acme-prod")]),
    ("sf org logout -o acme-prod", [("unverifiable", "acme-prod")]),
    ("sf", [("local", None)]),
    ("sf --version", [("local", None)]),
])
def test_other_short_or_unknown_commands_keep_their_routes(command, expected):
    assert B(command) == expected


@pytest.mark.parametrize("command,expected", [
    # The CLI wants the command first; the gate does not rely on that.
    ("sf --json org display -o acme-prod", [("credential", None)]),
    ("sf -o acme-prod --api-version 60.0 org display", [("credential", None)]),
    ("sf --json org list", [("all_orgs", None)]),
    ("sf --verbose org list", [("all_orgs", None)]),
    ("sf --json org login web", [("credential", None)]),
    ("sf --json org open -o acme-prod --url-only", [("credential", None)]),
    ("sf --json data query -q x", [("no_org", None)]),
    ("sf --json data query -q x -o acme-prod", [("org_write", "acme-prod")]),
    ("sf --json $X -o acme-prod", [("admin", "acme-prod")]),
    ("sf --version", [("local", None)]),
    ("sf --help", [("local", None)]),
    ("sf -h", [("local", None)]),
])
def test_a_flag_before_the_command_words_is_never_local_work(command, expected):
    # `sf --json org list` used to classify as local, and `sf --json org display -o ALIAS` as a write.
    assert B(command) == expected


@pytest.mark.parametrize("command", DISPLAY + SIBLINGS + ALL_ORGS)
def test_they_are_never_a_read_a_write_or_a_question(command):
    kinds = {route.kind for route in cr.classify("Bash", {"command": command})}
    assert not kinds & {"local", "read", "check_only", "org_write", "unverifiable"}, kinds


@pytest.mark.parametrize("command,expected", [
    ("sf org list limits -o acme-prod", [("read", "acme-prod")]),
    ("sf org list users -o acme-prod", [("read", "acme-prod")]),
    ("sf org list metadata -m Flow -o acme-prod", [("read", "acme-prod")]),
    ("sf org list metadata-types -o acme-prod", [("read", "acme-prod")]),
    ("sf org list sobject record-counts -s Account -o acme-prod", [("read", "acme-prod")]),
    # Without an org these read the default org: refused, where `org list ...` used to pass as local.
    ("sf org list limits", [("no_org", None)]),
    ("sf org list users", [("no_org", None)]),
    # Describing a command does not run it.
    ("sf help org display", [("local", None)]),
    ("sf which org list", [("local", None)]),
    # Opening a browser without printing the URL stays a write an approval can cover.
    ("sf org open -o acme-prod", [("org_write", "acme-prod")]),
    ("sf org open -o acme-prod --path '/lightning/o/Account/list?filterName=Recent'", [("org_write", "acme-prod")]),
    ("sf org open -o acme-prod -b firefox", [("org_write", "acme-prod")]),
])
def test_neighbours_keep_their_routes(command, expected):
    assert B(command) == expected


@pytest.mark.parametrize("command,expected", [
    ("env sf org display -o acme-prod", [("credential", None)]),
    ("nohup sf org list", [("all_orgs", None)]),
    ("time sf org display -o acme-prod", [("credential", None)]),
    # A variable set for the command turns a read into a question; it does not turn these into one.
    ("HOME=/tmp/x sf org display -o acme-prod", [("credential", None)]),
    ("env SF_X=1 sf org list", [("all_orgs", None)]),
    ("export SF_X=1; sf org display -o acme-prod", [("local", None), ("credential", None)]),
    ("xargs sf org display -o acme-prod", [("unverifiable", None), ("credential", None)]),
    ("bash -c 'sf org display -o acme-prod'", [("unverifiable", None), ("credential", None)]),
    ("eval 'sf org list --json'", [("unverifiable", None), ("all_orgs", None)]),
    ("echo $(sf org display -o acme-prod --json)", [("local", None), ("credential", None)]),
    ("echo `sf org list`", [("local", None), ("all_orgs", None)]),
    ("npx @salesforce/cli org display -o acme-prod", [("unverifiable", None), ("credential", None)]),
    ("node node_modules/@salesforce/cli/bin/run.js org list", [("unverifiable", None), ("all_orgs", None)]),
    ("cmd /c sf org display -o acme-prod", [("unverifiable", None), ("credential", None)]),
    ("sf data query -q x -o acme-prod && sf org display -o acme-prod", [("read", "acme-prod"), ("credential", None)]),
    ("sf org display -o acme-prod | jq .result.accessToken", [("credential", None), ("local", None)]),
])
def test_wrapped_and_chained_forms_keep_the_route(command, expected):
    assert B(command) == expected


def test_command_words_built_at_run_time_are_refused_not_approvable(w):
    # The shell picks the sf command after the gate decides, so none of these may
    # become a write that an approval covers (they used to).
    for command in ("sf org ${X:+display} -o acme-prod", "sf org $VERB -o acme-prod", "sf $A $B -o acme-prod",
                    "sf org {display,} -o acme-prod", "sf org d* -o acme-prod", "sf or? display -o acme-prod",
                    "sf org $(echo display) -o acme-prod", "sf 'org display' -o acme-prod"):
        kinds = [route.kind for route in cr.classify("Bash", {"command": command})]
        assert kinds[0] in ("admin", "credential", "all_orgs") and "org_write" not in kinds, (command, kinds)
        assert run(w, "Bash", {"command": command}).action == "deny", command


def test_a_variable_in_a_flag_value_keeps_the_commands_route():
    # A write needs an approval for this exact text, variable and all.
    assert B("for f in a b; do sf apex run -f $f -o acme-prod; done") == [
        ("local", None), ("org_write", "acme-prod")]
    assert B('for f in a b; do sf apex run -f "$f" -o acme-prod; done') == [("local", None), ("org_write", "acme-prod")]
    assert B("sf api request rest '/services/data/v60.0/query?q=SELECT+Id+FROM+Account' -o acme-prod") == [
        ("read", "acme-prod")]


POWERSHELL = [
    "sf.cmd org display --target-org acme-prod", "& sf.cmd org display -o acme-prod",
    r"& 'C:\Program Files\sf\bin\sf.cmd' org display -o acme-prod", r"C:\tools\sf.cmd org display -o acme-prod",
    "sf.ps1 org display -o acme-prod", "s`f.cmd org display -o acme-prod", "sf.cmd org display `\n -o acme-prod",
    "cmd /c sf org display -o acme-prod", "cmd /c 'sf.cmd org display -o acme-prod'",
    "iex 'sf.cmd org display -o acme-prod'", "Invoke-Expression 'sf org open -o acme-prod --url-only'",
    "powershell -Command 'sf org display -o acme-prod'", "pwsh -c 'sf.cmd org list'",
    "powershell -Command \"iex 'sf org list --json'\"", "& ([scriptblock]::Create('sf.cmd org display -o acme-prod'))",
    "Start-Job { sf.cmd org display -o acme-prod }", "sf.cmd org list", "sf.cmd force:org:list --all",
    "powershell -EncodedCommand " + __import__("base64").b64encode(
        "sf.cmd org display -o acme-prod".encode("utf-16-le")).decode(),
]


@pytest.mark.parametrize("command", POWERSHELL)
def test_powershell_forms_are_read_the_way_build_only_reads_them(command):
    assert {kind for kind, _ in P(command)} & {"credential", "all_orgs"}, P(command)


def test_the_gate_refuses_the_powershell_forms(w):
    for command in POWERSHELL:
        decision = run(w, "PowerShell", {"command": command})
        assert decision.action == "deny", command
        assert "credential" in decision.reason or "every org" in decision.reason, (command, decision.reason)


def test_powershell_reading_adds_refusals_only():
    # An ordinary PowerShell call keeps the routes it had: the extra reading never
    # adds a second write route (which would refuse an approved write).
    assert P(r"sf apex run -f scripts\x.apex -o acme-prod") == B(r"sf apex run -f scripts\x.apex -o acme-prod")
    assert P("sf.cmd data query -q x -o acme-prod") == [("read", "acme-prod")]
    assert P("git commit -m 'Describe sf org display usage'") == [("unverifiable", None)]


def test_the_gate_refuses_a_credential_command_and_names_the_safe_query(w):
    for command in DISPLAY + SIBLINGS:
        decision = run(w, "Bash", {"command": command})
        assert decision.action == "deny", command
        if set("*?") & set(command):
            continue  # a glob at the workspace root is refused by the path checks first
        assert "credential" in decision.reason and "with or without an approval" in decision.reason, command
        assert 'sf data query --target-org ALIAS -q "SELECT Id, Name, IsSandbox FROM Organization"' in decision.reason


def test_the_safe_query_it_names_is_an_allowed_read(w, tmp_path):
    assert run(w, "Bash", {"command": QUERY}).action == "allow"
    # It reads the Organization record, so the consent must cover record data.
    record(w, tmp_path, ["metadata"])
    decision = run(w, "Bash", {"command": QUERY})
    assert decision.action == "deny" and "record data" in decision.reason


def test_the_gate_refuses_a_listing_of_every_org(w):
    for command in ALL_ORGS:
        decision = run(w, "Bash", {"command": command})
        assert decision.action == "deny", command
        assert "lists every org" in decision.reason and "outside acme's consent" in decision.reason, command
        assert "torque client consent show" in decision.reason


def test_refused_in_every_permission_mode_and_without_a_binding(w):
    for mode in (None, "default", "acceptEdits", "plan", "bypassPermissions", "auto"):
        for command in ("sf org display -o acme-prod", "sf org open -o acme-prod -r", "sf org login web",
                        "sf org list"):
            assert run(w, "Bash", {"command": command}, mode=mode).action == "deny", (mode, command)
            unbound = run(w, "Bash", {"command": command}, env={}, mode=mode)
            assert unbound.action == "deny" and "no client is bound" not in unbound.reason, (mode, command)


def test_no_approval_can_be_requested_for_them(w):
    cid = changes.create_change(w, "Acme", "Check", "Know the org", [], "acme-prod")["id"]
    for command in ("sf org display -o acme-prod", "sf org display -o acme-prod --verbose",
                    "sf org open -o acme-prod --url-only", "sf org generate password -o acme-prod",
                    "sf org list -o acme-prod", "sfdx force:org:display -u acme-prod"):
        with pytest.raises(ws.WorkspaceError, match="exactly one write command"):
            approval.create_request(w, "Acme", cid, "acme-prod", argv=command.split(), resolve=ORGS.get,
                                    manual_recovery="Nothing changes in the org; there is nothing to recover.",
                                    cwd=w)


def test_an_approved_plain_open_does_not_cover_the_url_printing_form(w):
    cid = changes.create_change(w, "Acme", "Layout", "Open Setup", [], "acme-sbx")["id"]
    req = approval.create_request(w, "Acme", cid, "acme-sbx", argv="sf org open -o acme-sbx".split(),
                                  resolve=ORGS.get, cwd=w)
    approval.grant(w, "Acme", req["id"], presence=YES, confirm=lambda: True, out=io.StringIO(), resolve=ORGS.get)
    assert run(w, "Bash", {"command": "sf org open -o acme-sbx --url-only"}).action == "deny"
    assert run(w, "Bash", {"command": "sf org open -o acme-sbx --json"}).action == "deny"
    assert run(w, "Bash", {"command": "sf org open -o acme-sbx"}).action == "allow"


@pytest.mark.parametrize("name", cr.SF_CONTAINER_VARS)
@pytest.mark.parametrize("value", ["true", "1", "TRUE", "yes"])
def test_container_mode_in_the_session_makes_a_plain_open_print_its_url(monkeypatch, name, value):
    # In container mode the CLI prints the login URL instead of opening a browser.
    assert B("sf org open -o acme-sbx") == [("org_write", "acme-sbx")]
    monkeypatch.setenv(name, value)
    assert B("sf org open -o acme-sbx") == [("credential", None)]
    assert B("sfdx force:org:open -u acme-sbx") == [("credential", None)]
    # Other commands are not affected by it.
    assert B("sf data query -q x -o acme-sbx") == [("read", "acme-sbx")]
    monkeypatch.setenv(name, "false")
    assert B("sf org open -o acme-sbx") == [("org_write", "acme-sbx")]


def test_the_gate_refuses_a_plain_open_in_container_mode(w, monkeypatch):
    monkeypatch.setenv("SF_CONTAINER_MODE", "true")
    decision = run(w, "Bash", {"command": "sf org open -o acme-sbx"})
    assert decision.action == "deny" and "credential" in decision.reason


CONTAINER_SETTERS = [
    ("SF_CONTAINER_MODE=true sf org open -o acme-sbx", "Bash"),
    ("env SFDX_CONTAINER_MODE=true sf org open -o acme-sbx", "Bash"),
    ("export SF_CONTAINER_MODE=true; sf org open -o acme-sbx", "Bash"),
    ("export SF_CONTAINER_MODE=true", "Bash"),
    ("bash -c 'SF_CONTAINER_MODE=1 sf org open -o acme-sbx'", "Bash"),
    ("$env:SF_CONTAINER_MODE = 'true'; sf org open -o acme-sbx", "PowerShell"),
    ("$env:SFDX_CONTAINER_MODE='true'", "PowerShell"),
    ("cmd /c \"set SF_CONTAINER_MODE=true && sf org open -o acme-sbx\"", "PowerShell"),
]


def test_a_command_that_sets_container_mode_is_refused(w):
    # It used to be a question for the consultant (a variable assignment).
    for command, tool in CONTAINER_SETTERS:
        routes = cr.classify(tool, {"command": command})
        assert any(route.kind == "admin" and "print its login URL" in route.detail for route in routes), command
        assert run(w, tool, {"command": command}).action == "deny", command
    assert "print its login URL" in run(w, "Bash", {"command": "export SF_CONTAINER_MODE=true"}).reason
    assert run(w, "Bash", {"command": "echo $SF_CONTAINER_MODE"}).action == "allow"


def test_salesforce_mcp_tools_that_list_orgs_or_return_credentials(w):
    assert K("mcp__salesforce__list_all_orgs", {}) == [("all_orgs", None)]
    # An org argument the tool ignores does not make the listing a read of that org.
    assert K("mcp__salesforce__list_all_orgs", {"usernameOrAlias": "acme-prod"}) == [("all_orgs", None)]
    assert K("mcp__sf__list_orgs", {"org": "acme-prod"}) == [("all_orgs", None)]
    assert K("mcp__salesforce__get_access_token", {"usernameOrAlias": "acme-prod"}) == [("credential", None)]
    assert K("mcp__salesforce__display_org", {"usernameOrAlias": "acme-prod"}) == [("credential", None)]
    assert K("mcp__salesforce__reset_user_password", {"usernameOrAlias": "acme-prod"}) == [("credential", None)]
    assert K("mcp__shell__run", {"command": "sf org display -o acme-prod"}) == [("local", None), ("credential", None)]
    assert K("mcp__salesforce__run_soql_query", {"usernameOrAlias": "acme-prod", "query": "x"}) == [
        ("read", "acme-prod")]
    for tool in ("mcp__salesforce__list_all_orgs", "mcp__salesforce__get_access_token"):
        assert run(w, tool, {"usernameOrAlias": "acme-prod"}).action == "deny", tool
    assert run(w, "mcp__shell__run", {"command": "sf org list"}).action == "deny"


def _hook(monkeypatch, capsys, event):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    code = gate.main()
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_hook_end_to_end(w, monkeypatch, capsys):
    from torque import launch
    launched = launch.write_launch_record(w, "Acme", "human")
    monkeypatch.setenv("TORQUE_CLIENT", "acme")
    monkeypatch.setenv("TORQUE_LAUNCH", launched["id"])
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    base = {"hook_event_name": "PreToolUse", "cwd": str(w), "session_id": "s1", "tool_use_id": "t1",
            "permission_mode": "default"}
    for tool, command in (("Bash", "sf org display -o acme-sbx"), ("PowerShell", "iex 'sf org display -o acme-sbx'")):
        code, out, err = _hook(monkeypatch, capsys, {**base, "tool_name": tool, "tool_input": {"command": command}})
        assert code == 2 and out == "" and "SELECT Id, Name, IsSandbox FROM Organization" in err, (tool, err)
    code, out, err = _hook(monkeypatch, capsys, {**base, "tool_name": "Bash", "tool_input": {"command": "sf org list"}})
    assert code == 2 and out == "" and "lists every org" in err
    code, out, _ = _hook(monkeypatch, capsys, {**base, "tool_name": "Bash", "tool_input": {"command": QUERY}})
    assert code == 0 and out == ""


def test_a_metadata_read_can_stand_in_for_org_display_in_a_readiness_probe(w, tmp_path):
    # torque doctor's bound probes used `sf org display`; with metadata-only consent
    # this read is the allowed stand-in, and its other-org and default-org forms deny.
    record(w, tmp_path, ["metadata"])
    assert run(w, "Bash", {"command": "sf sobject list -o acme-sbx"}).action == "allow"
    assert run(w, "Bash", {"command": "sf sobject list -o doctor-probe-other"}).action == "deny"
    assert run(w, "Bash", {"command": "sf sobject list"}).action == "deny"


def test_an_mcp_server_under_a_neutral_name_is_a_question_when_it_names_a_salesforce_host(w):
    call = {"instance": "https://acme.my.salesforce.com", "soql": "SELECT Id FROM Account"}
    assert K("mcp__crm__query", call) == [("unverifiable", None)]
    assert run(w, "mcp__crm__query", call).action == "ask"
    assert run(w, "mcp__crm__query", call, mode="bypassPermissions").action == "deny"
    assert run(w, "mcp__fetch__fetch", {"url": "https://developer.salesforce.com/docs"}).action == "allow"
    # The remaining gap: no host in the arguments, nothing to see.
    assert run(w, "mcp__crm__query", {"org": "acme-prod", "soql": "SELECT Id FROM Account"}).action == "allow"


def test_antigravity_browser_tools_and_resource_reads_are_classified_like_the_rest(w):
    assert K("mcp__antigravity__open_browser_url", {"Url": "https://example.com"}) == [("browser_write", None)]
    assert K("mcp__antigravity__read_browser_page", {}) == [("browser_read", None)]
    assert K("mcp__antigravity__execute_browser_javascript", {}) == [("browser_write", None)]
    assert K("mcp__antigravity__generate_image", {"Prompt": "x"}) == [("local", None)]
    for tool in ("mcp__antigravity__open_browser_url", "mcp__antigravity__read_browser_page",
                 "mcp__antigravity__browser_click_element", "mcp__antigravity__capture_browser_screenshot"):
        assert run(w, tool, {}).action == "deny", tool
        assert run(w, tool, {}, env={}).action == "deny", tool
    assert run(w, "ReadMcpResourceTool", {"server": "salesforce", "uri": "salesforce://record/001"}).action == "deny"
    assert run(w, "ReadMcpResourceTool", {"server": "notes", "uri": "notes://today"}).action == "allow"
    # A spare `server` argument does not rename a browser tool.
    assert run(w, "mcp__claude-in-chrome__read_page", {"server": "notes"}).action == "deny"
