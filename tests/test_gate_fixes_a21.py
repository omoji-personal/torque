"""Gaps in the released connected-mode gate, found when the guarded-reads plan was
reviewed: a Torque command whose words the shell fills in at run time or that hides
behind `--`, a record read spelled in another word order or left incomplete,
record-returning commands classed as schema reads, Tooling rows and queries that
name people, and leaving connected mode without a person at a terminal."""
import argparse
from collections import namedtuple
from itertools import permutations
import os
from pathlib import Path

import pytest

from torque import cli, consent, connected_routes as routes, gate_connected as gc, workspace as ws
from torque.connected_routes import classify, rest_data_class
from torque.presence import Presence

Org = namedtuple("Org", "org_id_18 detected_org_type is_production instance_url", defaults=(None,))
ORGS = {"acme-prod": Org("00D000000000002AAA", "production", True, "https://acme.my.salesforce.com")}
YES = lambda: Presence(True, "")
NO = lambda: Presence(False, "not a terminal")
B = lambda cmd: [(r.kind, r.org, r.data) for r in classify("Bash", {"command": cmd})]
KINDS = lambda cmd, tool="Bash": [r.kind for r in classify(tool, {"command": cmd})]


@pytest.fixture
def w(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    root = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(root, "Acme")
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    letter = tmp_path / "a.pdf"
    letter.write_bytes(b"agreement")
    consent.record_consent(root, "Acme", "2026-09-30", letter, ["metadata"], ["acme-prod"], ["Contact"],
                           presence=YES, resolve=ORGS.get)
    consent.sign_off(root, "Acme", "Reviewer", presence=YES)
    return Path(os.path.realpath(root))


def run(root, command, mode=None, tool="Bash"):
    return gc.decide_connected(tool, {"command": command}, root, root, env={"TORQUE_CLIENT": "acme"},
                               permission_mode=mode, session_id="s1", tool_use_id="t1")


# ---- A1: a Torque command the shell completes at run time, or that hides behind `--`

RUN_TIME_BUILT = [
    "torque workspace ${x:-ai-access} full --path .",
    "torque ${y:-workspace} ai-access full --path .",
    "torque workspace ai-acces* full --path .",
    "torque workspace ai-acces? full --path .",
    "torque workspace {ai-access,x} full --path .",
    "torque approval ${v:-grant} req-000000000001",
    "torque client consent ${a:-record} --workspace . --client acme",
    "torque client ${c:-consent} record --workspace . --client acme",
    "python -m torque workspace ${x:-ai-access} full --path .",
    "command torque workspace ${x:-ai-access} full --path .",
    "torque workspace ai-access full --pa${t:-th} .",
    "torque change add-check --workspace . --client acme $EXTRA",
    "torque $ALL",
    # an option that takes no value does not shelter the next word
    "torque approval permissions --unattended ${x:---write} --workspace W",
    "torque approval permissions --unattended $X --workspace W",
    # an unquoted value splits into words when it holds a space
    "torque approval permissions --hook-python=$hook_arg --workspace W",
    "torque context --workspace=$HOME/w --client acme",
    "torque session add --workspace . --client acme --summary $TEXT --status executed",
    # a double-quoted variable is one word, but after a flag or in a bare place it can be an option
    'torque approval permissions --unattended "$X" --workspace W',
    'torque approval permissions "$X" --workspace W',
    'torque approval permissions "--$X" --workspace W',
    'torque approval permissions "--$X=1" --workspace W',
    'torque "$A" --workspace W --client acme',
    # PowerShell: the stop-parsing token and a splatted array
    "python -m torque --% workspace ai-access full --path .",
    "torque --% workspace ai-access full --path .",
    "torque @words",
    "torque workspace @words",
]


@pytest.mark.parametrize("command", RUN_TIME_BUILT)
def test_run_time_built_torque_command_is_refused(command):
    assert KINDS(command) == ["admin"], command


@pytest.mark.parametrize("command", [
    "torque -- workspace ai-access full --path .",
    "torque workspace -- ai-access full --path .",
    "torque workspace ai-access -- full --path .",
    "python -m torque -- workspace ai-access full --path .",
    "command torque -- workspace ai-access full --path .",
    "torque -- approval grant req-000000000001",
    "torque client -- consent record --workspace . --client acme",
    "torque -- context --workspace . --client acme",
])
def test_double_dash_does_not_hide_a_torque_command(command):
    # Torque's parser drops `--` and still reads the words after it as the command.
    assert KINDS(command) == ["admin"], command


@pytest.mark.parametrize("command", [
    'torque session add --workspace . --client acme --summary "$TEXT" --status executed',
    'torque session add --workspace . --client acme --summary="$TEXT" --status executed',
    'torque session add --workspace . --client acme "--summary=$TEXT" --status executed',
    "torque session add --workspace . --client acme --summary 'costs $5 {roughly} [draft] *' --status executed",
    'torque session add --workspace . --client acme --summary "a [draft] of {this}?" --status executed',
    'torque session add --workspace . --client acme --summary "$(cat notes.txt)" --status executed',
    'torque session add --workspace . --client acme --summary costs\\ \\$5 --status executed',
    'torque context --workspace "$HOME/w" --client acme',
    'torque context --workspace="$HOME/w" --client acme',
    "torque context --workspace . --client acme",
    "torque change list --workspace . --client acme --json",
    "torque workflows show qa",
    'torque client add "Acme [EMEA]" --workspace W',
    "torque client add 'What? {Now}' --workspace W",
    'torque workspace init "C:/Work/Clients [test]" --name Test',
])
def test_values_quoted_text_and_plain_commands_are_not_refused(command):
    assert "admin" not in KINDS(command) and "unverifiable" not in KINDS(command), command


def test_approval_request_tail_is_not_read_as_command_words():
    command = ('torque approval request --workspace . --client acme --org acme-prod --change c1 -- '
               'sf data update record -s Account -i 001x -v "Name=$X" -o acme-prod $MORE')
    assert KINDS(command) == ["local"]
    assert KINDS("torque launch --workspace . --client acme -- --model $M") == ["admin"]


def test_value_options_are_the_ones_torques_own_parser_gives_one_value():
    takes_one, other = set(), set()

    def walk(parser):
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for child in action.choices.values():
                    walk(child)
            else:
                one = action.nargs is None and type(action) in (argparse._StoreAction, argparse._AppendAction)
                (takes_one if one else other).update(action.option_strings)
    walk(cli.build_parser())
    assert routes.TORQUE_VALUE_OPTIONS == takes_one - other
    assert routes.TORQUE_DELEGATED == set(cli.DELEGATES) | set(cli.PUBLIC_ROUTES)


@pytest.mark.parametrize("command,kinds", [
    # a write keeps its one route: it needs an approval for this exact text
    ("torque browser multiprofile visit --target-org acme-prod $MORE", ["browser_write"]),
    ("torque recover show abc --org acme-prod $MORE", ["read", "unverifiable"]),
    ("torque logs analyze *", ["local", "unverifiable"]),
    ("torque logs analyze --dir=* x", ["local", "unverifiable"]),
    ("torque logs analyze debug/*.log", ["local"]),
    ('torque logs analyze "$FILE"', ["local", "unverifiable"]),
    ('torque logs analyze --file "$FILE"', ["local"]),
    ("torque browser multiprofile visit --target-org acme-prod -- --headed", ["browser_write"]),
    ("sf data query -q 'SELECT Id FROM Account' -o acme-prod $MORE", ["read", "unverifiable"]),
    ("sf data query -q 'SELECT Id FROM Account' -o acme-prod {-o,other}", ["read", "unverifiable"]),
    ("sf sobject describe -s Account -o acme-prod `cat more`", None),
    ("sf project retrieve start -m ApexClass:Acct* -o acme-prod", ["read"]),
    ("sf project retrieve start -d force-app/main/* -o acme-prod", ["read"]),
    ('sf data query -q "$Q" -o acme-prod', ["read"]),
    ('sf data query -q "SELECT Id FROM Account WHERE Name = \'$N\'" -o acme-prod', ["read"]),
    # a double-quoted variable that stands alone can be a whole option: --target-org=other
    ('sf data query -q x -o acme-prod "$MORE"', ["read", "unverifiable"]),
    ('sf sobject describe "$MORE" -s Account -o acme-prod', ["admin"]),        # among the command words
    ('sf sobject describe -s Account "$MORE" -o acme-prod', ["read", "unverifiable"]),
    ('sf sobject describe -s Account -o acme-prod "`cat more`"', ["read", "unverifiable"]),
    ('sf api request rest "/services/data/v60.0/sobjects/$NAME/describe" -o acme-prod', ["org_write"]),
    ('torque recover show abc --org acme-prod "$MORE"', ["read", "unverifiable"]),
    ("sf data query -q 'SELECT Id FROM Account WHERE Name LIKE {x} [y] *' -o acme-prod", ["read"]),
    ("sf project deploy start -d force-app -o acme-prod $MORE", ["org_write"]),
])
def test_words_the_shell_adds_later_are_asked_about(command, kinds):
    # The route the gate read still has to pass (consent, approval); the consultant is asked too.
    found = KINDS(command)
    assert found == kinds if kinds else "unverifiable" in found, command


def test_an_added_org_cannot_ride_on_a_consented_read(w):
    assert run(w, "sf sobject describe -s Account -o acme-prod").action == "allow"
    assert run(w, "sf sobject describe -s Account -o acme-prod $MORE").action == "ask"
    assert run(w, "sf sobject describe -s Account -o acme-prod $MORE", "bypassPermissions").action == "deny"
    assert run(w, "sf data query -q 'SELECT Id FROM Account' -o acme-prod $MORE").action == "deny"  # no records


@pytest.mark.parametrize("command", [
    "python -m torque (Get-Content words.txt)",
    "torque (Get-Content words.txt)",
    "torque workspace (echo ai-access) full --path .",
    "torque work`space ai-access full --path .",
    "torque workspace ai-access full --path .",
    "python -m torque --% workspace ai-access full --path .",
    "torque $words",
    "torque @words",
    "& torque workspace ai-access full --path .",
])
def test_powershell_spellings_of_an_owner_command_are_refused(w, command):
    assert "admin" in KINDS(command, "PowerShell"), command
    for mode in (None, "bypassPermissions"):
        assert run(w, command, mode, tool="PowerShell").action == "deny", command


def test_a_read_inside_a_powershell_string_meets_the_session_and_the_consent(w):
    inner = ("powershell -NoProfile -Command 'Remove-Item Env:TORQUE_CLIENT; "
             "sf data query -q \"SELECT Email FROM Contact\" -o acme-prod'")
    assert run(w, inner, tool="PowerShell").action == "deny"            # no record data in this consent
    other = ("powershell -NoProfile -Command 'Remove-Item Env:TORQUE_CLIENT; "
             "torque recover show --workspace C:/Work/W2 --client other --org acme-prod'")
    decision = run(w, other, tool="PowerShell")
    assert decision.action == "deny" and "bound to acme" in decision.reason


@pytest.mark.parametrize("mode", [None, "default", "bypassPermissions", "auto"])
@pytest.mark.parametrize("command", [
    "torque workspace `echo ai-access` full --path .",
    "torque workspace $(echo ai-access) full --path .",
    "torque `echo workspace ai-access full` --path .",
    "torque context --workspace . --client acme `echo --json`",
])
def test_command_substitution_in_a_torque_command_is_refused(w, command, mode):
    # The gate splits the line at a backtick or a parenthesis; the word before it is
    # marked, so the Torque command is known to get an argument at run time.
    assert "admin" in KINDS(command)
    assert run(w, command, mode).action == "deny"


def test_a_command_substitution_that_only_holds_a_command_is_read_as_before():
    assert KINDS("x=`sf sobject describe -s Account -o acme-prod`") == ["local", "read"]
    assert KINDS("echo $(sf sobject describe -s Account -o acme-prod)") == ["local", "read"]
    assert set(KINDS("echo `torque context --workspace . --client acme`")) == {"local"}


SWITCH_OFF = [
    "torque workspace ai-access full --path .",
    "torque workspace ai-access build-only --path .",
    "torque workspace ${x:-ai-access} full --path .",
    "torque ${y:-workspace} ai-access full --path .",
    "torque workspace ai-acces* full --path .",
    "torque -- workspace ai-access full --path .",
    "torque workspace -- ai-access full --path .",
    "python -m torque -- workspace ai-access full --path .",
    "x='full --path .'; torque workspace ai-access $x",
    'torque workspace "$A" full --path .',
]


@pytest.mark.parametrize("mode", [None, "default", "bypassPermissions", "auto"])
@pytest.mark.parametrize("command", SWITCH_OFF)
def test_session_cannot_switch_the_mode_off(w, command, mode):
    assert run(w, command, mode).action == "deny", command


@pytest.mark.parametrize("mode", ["full", "build-only"])
def test_leaving_connected_mode_needs_a_person_at_a_terminal(w, mode):
    with pytest.raises(ws.WorkspaceError, match="left by the owner at a real terminal"):
        ws.set_ai_access(w, mode, presence=NO)
    with pytest.raises(ws.WorkspaceError, match="real terminal"):
        ws.set_ai_access(w, mode)                      # the test runner is not a terminal
    assert ws.load_workspace(w)[1]["ai_access"] == "connected"
    ws.set_ai_access(w, mode, presence=YES)
    assert ws.load_workspace(w)[1]["ai_access"] == mode
    ws.set_ai_access(w, "full" if mode == "build-only" else "build-only")     # not connected: as before
    assert cli.main(["workspace", "ai-access", "build-only", "--path", str(w)]) == 0


def test_the_command_line_cannot_leave_connected_mode_without_a_terminal(w, capsys):
    assert cli.main(["workspace", "ai-access", "full", "--path", str(w)]) == 2
    assert cli.main(["--", "workspace", "ai-access", "full", "--path", str(w)]) == 2
    assert "real terminal" in capsys.readouterr().err
    assert ws.load_workspace(w)[1]["ai_access"] == "connected"


# ---- A2: a record or log read in another word order, in the colon spelling, or left incomplete

READS = [(data, name) for data, name in routes.SF_READ_NAMES if name != ("apex", "tail", "log")]


@pytest.mark.parametrize("data,name", READS)
def test_a_read_is_the_same_read_in_any_word_order(data, name):
    for order in permutations(name):
        assert B(f"sf {' '.join(order)} -o acme-prod") == [("read", "acme-prod", data)], order
        assert B(f"sf {' '.join(order)}") == [("no_org", None, data)], order
    assert B(f"sf {':'.join(name)} -o acme-prod") == [("read", "acme-prod", data)]
    assert B(f"sf {':'.join(reversed(name))} -o acme-prod") == [("read", "acme-prod", data)]
    assert B(f"sf force:{':'.join(name)} -o acme-prod") == [("read", "acme-prod", data)]


@pytest.mark.parametrize("command", [
    "sf data import resume -o acme-prod", "sf data import tree -o acme-prod", "sf data update record -o acme-prod",
    "sf data delete record -o acme-prod", "sf data create record -o acme-prod", "sf data upsert bulk -o acme-prod",
    "sf data delete resume -o acme-prod", "sf data update resume -o acme-prod", "sf apex run -o acme-prod",
    "sf apex tail log -o acme-prod", "sf log tail apex -o acme-prod", "sf data:delete:record -o acme-prod",
    "sf org create user -o acme-prod", "sf record update data -o acme-prod",
])
def test_a_write_that_shares_words_with_a_read_stays_a_write(command):
    assert B(command) == [("org_write", "acme-prod", None)], command


@pytest.mark.parametrize("command,full", [
    ("sf query -o acme-prod -q 'SELECT Email FROM Contact LIMIT 1' --json", "data query"),
    ("sf data -o acme-prod", "data query"), ("sf search -o acme-prod", "data search"),
    ("sf record get -o acme-prod", "data get record"), ("sf export -o acme-prod", "data export"),
    ("sf results -o acme-prod", "data bulk results"), ("sf resume -o acme-prod", "data"),
    ("sf log -o acme-prod", "apex"), ("sf apex log -o acme-prod", "apex"), ("sf tail -o acme-prod", "apex tail log"),
    ("sf users -o acme-prod", "org list users"), ("sf fromorg -o acme-prod", "cmdt generate fromorg"),
    ("sf cmdt generate -o acme-prod", "cmdt generate fromorg"), ("sf data:export -o acme-prod x", "data export"),
    ("sf query", "data query"),
])
def test_an_incomplete_read_name_is_refused(w, command, full):
    [route] = classify("Bash", {"command": command})
    assert route.kind == "admin" and "incomplete command name" in route.detail and full in route.detail, command
    assert run(w, command).action == "deny"


def test_a_reordered_read_needs_the_consent_its_plain_spelling_needs(w):
    for command in ("sf query data -q 'SELECT Id FROM Account' -o acme-prod",
                    "sf data:query -q 'SELECT Id FROM Account' -o acme-prod",
                    "sf log get apex -o acme-prod", "sf users list org -o acme-prod"):
        decision = run(w, command)
        assert decision.action == "deny" and "does not cover" in decision.reason, command


# ---- A3: commands that return records, classed as schema reads until now

@pytest.mark.parametrize("command,expected", [
    ("sf cmdt generate fromorg --sobject Client_Data__c --dev-name Dump -o acme-prod", ("read", "acme-prod", "records")),
    ("sf org list users -o acme-prod --json", ("read", "acme-prod", "records")),
    ("sf org list metadata -m ApexClass -o acme-prod", ("read", "acme-prod", None)),
    ("sf org list metadata-types -o acme-prod", ("read", "acme-prod", None)),
    ("sf cmdt generate records --csv x.csv --type-name T", ("local", None, None)),
])
def test_record_returning_commands_need_record_consent(command, expected):
    assert B(command) == [expected]


def test_metadata_consent_does_not_cover_the_orgs_people_or_an_objects_rows(w):
    for command in ("sf org list users -o acme-prod", "sf cmdt generate fromorg --sobject A__c --dev-name D -o acme-prod"):
        decision = run(w, command)
        assert decision.action == "deny" and "record data" in decision.reason
    assert run(w, "sf org list metadata -m ApexClass -o acme-prod").action == "allow"


# ---- A4: the Tooling API

Q = "/services/data/v66.0/tooling/query/?q="


@pytest.mark.parametrize("soql", [
    "SELECT Id, Name, Body FROM ApexClass",
    "SELECT+Id,+DeveloperName,+TableEnumOrId+FROM+CustomField+WHERE+TableEnumOrId+=+'Account'",
    "SELECT Id, Metadata FROM Flow WHERE Definition.DeveloperName = 'My_Flow' AND Status = 'Active'",
    "SELECT QualifiedApiName, DataType, EntityDefinition.QualifiedApiName FROM FieldDefinition "
    "WHERE EntityDefinition.QualifiedApiName = 'User'",
    "select id from validationrule where entitydefinition.developername = 'Contact' limit 10",
    "SELECT Name, PermissionsModifyAllData, PermissionsManageUsers FROM PermissionSet WHERE IsOwnedByProfile = false",
    "SELECT COUNT() FROM ApexTrigger",
    "SELECT Id FROM Profile WHERE Name = 'O''Brien & Co'".replace("&", "and"),
    "SELECT%20Id%20FROM%20Layout",
])
def test_tooling_schema_queries_are_schema(soql):
    assert rest_data_class(Q + soql) is None, soql
    assert rest_data_class("/services/data/v66.0/tooling/query?q=" + soql) is None


@pytest.mark.parametrize("soql", [
    "SELECT Id, Name, Username, Email FROM User",
    "SELECT Id FROM TraceFlag", "SELECT Id FROM ApexExecutionOverlayResult", "SELECT Id FROM PermissionSetAssignment",
    "SELECT Message, StackTrace FROM ApexTestResult", "SELECT Id FROM SomethingNew",
    "SELECT CreatedById FROM CustomObject LIMIT 1", "SELECT CreatedBy.Name FROM CustomObject",
    "SELECT Id, LastModifiedBy.Email FROM ApexClass", "SELECT LastModifiedById FROM Flow",
    "SELECT Id FROM ApexClass WHERE CreatedBy.Name LIKE 'J%'", "SELECT Id FROM ApexClass ORDER BY LastModifiedBy.Name",
    "SELECT Id FROM ApexClass WHERE CreatedById = '005000000000001'",
    "SELECT LastModifiedBy FROM FlowDefinitionView", "SELECT Owner.Name FROM StaticResource",
    "SELECT Definition.LastModifiedBy.Name FROM Flow", "SELECT User.Name FROM Profile", "SELECT UserLicenseId FROM Profile",
    "SELECT RunningUser FROM Flow", "SELECT Id, (SELECT Assignee.Name FROM Assignments) FROM PermissionSet",
    "SELECT FIELDS(ALL) FROM ApexClass LIMIT 1", "SELECT FIELDS (STANDARD) FROM ApexClass",
    "SELECT TYPEOF Owner WHEN User THEN Name END FROM ApexClass",
    "SELECT Id FROM ApexClass WHERE Id IN (SELECT ApexClassOrTriggerId FROM ApexCodeCoverage)",
    "SELECT Id FROM ApexClass, User", "SELECT Id FROM ApexClass&q=SELECT Name FROM User",
    "SELECT Id FROM ApexClass WHERE Name = 'x", "FIND {x}", "", "SELECT FROM ApexClass",
    "SELECT%20Name%20FROM%20%55ser", "SELECT+Id+FROM+ApexClass%26q%3DSELECT+Name+FROM+User",
    "SELECT Name FROM ApexClass WHERE Name = 'a' OR CreatedBy.Name = 'b'",
])
def test_tooling_queries_that_reach_people_or_data_are_record_reads(soql):
    assert rest_data_class(Q + soql) == "records", soql


@pytest.mark.parametrize("path,expected", [
    ("/services/data/v66.0/tooling/sobjects", None), ("/services/data/v66.0/tooling/sobjects/", None),
    ("/services/data/v66.0/tooling/sobjects/ApexClass/describe", None),
    ("/services/data/v66.0/tooling/describe", None),
    ("/services/data/v66.0/tooling/sobjects/User/005000000000001AAA", "records"),
    ("/services/data/v66.0/tooling/sobjects/ApexClass/01p000000000001AAA", "records"),
    ("/services/data/v66.0/tooling/sobjects/%55ser/005000000000001AAA", "records"),
    ("/services/data/v66.0/tooling/query", "records"), ("/services/data/v66.0/tooling/query/01g000000000001-2000", "records"),
    ("/services/data/v66.0/tooling/queryAll/?q=SELECT Id FROM ApexClass", "records"),
    ("/services/data/v66.0/tooling/query/?x=1&q=SELECT Id FROM ApexClass", "records"),
    ("/services/data/v66.0/tooling/query/?q=SELECT Id FROM ApexClass&x=1", "records"),
    ("/services/data/v66.0/tooling/search/?q=FIND {x}", "records"),
    ("/services/data/v66.0/tooling/query/?q=SELECT Id FROM ApexLog", "debug_logs"),
    ("/services/data/v66.0/sobjects/Account/describe", None), ("/services/data/v66.0/limits", None),
    ("/services/data/v66.0/sobjects/Account/describe/layouts/012000000000001", None),
    # the client folds dot segments away before sending: these reach a row, not a describe
    ("/services/data/v66.0/sobjects/User/describe/../005000000000001", "records"),
    ("/services/data/v66.0/sobjects/User/describe/%2e%2e/005000000000001", "records"),
    ("/services/data/v66.0/sobjects/User/describe/%252e%252e/005000000000001", "records"),
    ("/services/data/v66.0/tooling/sobjects/ApexClass/describe/../../User/005000000000001", "records"),
    ("/services/data/v66.0/limits/../query?q=SELECT Name FROM Contact", "records"),
    ("/services/data/v66.0/sobjects/./User/005000000000001/../describe", "records"),
    ("/services/data/v66.0/sobjects\\User\\005000000000001/describe", "records"),
    ("/services/data/v66.0/sobjects/User/005000000000001#/describe", "records"),
    ("/services/data/v66.0/sobjects/Account/001000000000001", "records"),
    ("/services/data/v66.0/query?q=SELECT Id FROM ApexClass", "records"),
])
def test_tooling_rows_and_other_paths(path, expected):
    assert rest_data_class(path) == expected, path


def test_a_tooling_row_or_an_author_field_needs_record_consent(w):
    row = "sf api request rest /services/data/v66.0/tooling/sobjects/User/005000000000001AAA --target-org acme-prod"
    author = ("sf api request rest '/services/data/v66.0/tooling/query/?q=SELECT+CreatedById+FROM+CustomObject+LIMIT+1' "
              "--target-org acme-prod")
    schema = ("sf api request rest '/services/data/v66.0/tooling/query/?q=SELECT+Id,Name+FROM+ApexClass' "
              "--target-org acme-prod")
    for command in (row, author):
        decision = run(w, command)
        assert decision.action == "deny" and "record data" in decision.reason, command
    assert run(w, schema).action == "allow"


# ---- what the gate reads must be what the shell runs

@pytest.mark.parametrize("command", [
    # a quoted operator character is a value, so the flag after it is still there
    'torque approval permissions --hook-python ">" --write --workspace W',
    "torque approval permissions --hook-python '>>' --write --workspace W",
    'torque approval permissions --hook-python "<" --write --workspace W',
    'torque approval permissions --hook-python \\> --write --workspace W',
    'torque approval permissions --hook-python ";" --write --workspace W',
    'torque approval permissions --hook-python "|" --write --workspace W',
    'torque approval permissions --hook-python "&" --write --workspace W',
    "torque approval permissions --hook-python '(' --write --workspace W",
    # a backslash at a line end joins the lines
    "torque work\\\nspace ai-access full --path .",
    "torque workspace ai-\\\naccess full --path .",
    "tor\\\nque workspace ai-access full --path .",
    "torque approval permissions --wri\\\nte --workspace W",
    "torque workspace ai-access \\\n  full --path .",
    'torque "work\\\nspace" ai-access full --path .',
])
def test_quoted_operators_and_line_continuations_do_not_hide_an_owner_command(w, command):
    assert KINDS(command) == ["admin"], command
    assert run(w, command, "bypassPermissions").action == "deny"


@pytest.mark.parametrize("command,expected", [
    ('sf da\\\nta query -q "SELECT Email FROM Contact LIMIT 1" -o acme-prod --json', [("read", "acme-prod", "records")]),
    ("sf data query \\\n  -q 'SELECT Id FROM Account' \\\n  -o acme-prod", [("read", "acme-prod", "records")]),
    ("sf sobject describe -s Account \\\n -o acme-prod", [("read", "acme-prod", None)]),
    ("sf data query -q 'SELECT Id FROM Account WHERE Name > \\'a\\'' -o acme-prod", None),
    ('sf data query -q "SELECT Id FROM Account WHERE A > 1 AND (B < 2)" -o acme-prod', [("read", "acme-prod", "records")]),
    ("echo 'a;b' ; sf sobject describe -s Account -o acme-prod", [("local", None, None), ("read", "acme-prod", None)]),
    ("echo ';' sf org display -o acme-prod", [("local", None, None)]),          # echo's arguments, not a command
    ("echo x ; sf org display -o acme-prod", [("local", None, None), ("credential", None, None)]),
])
def test_line_continuations_and_quoted_operators_in_sf_commands(command, expected):
    if expected is not None:
        assert B(command) == expected, command


def test_a_backslash_before_cr_lf_is_read_both_ways(w):
    # Bash on Linux and macOS: the backslash makes the CR an ordinary character, and the LF still ends
    # the command. Git Bash on Windows drops the CR and joins the lines. Both readings have to pass.
    hidden = "echo ok \\\r\nsf data query -q 'SELECT Email FROM Contact' -o acme-prod"
    assert ("read", "acme-prod", "records") in B(hidden), B(hidden)             # the first reading
    assert run(w, hidden, "dontAsk").action == "deny"
    owner = "echo ok \\\r\ntorque workspace ai-access full --path ."
    assert "admin" in KINDS(owner) and run(w, owner).action == "deny"
    joined = "sf data \\\r\n query -q x -o acme-prod"
    assert ("read", "acme-prod", "records") in B(joined), B(joined)             # the second reading
    assert "no_org" in KINDS(joined) and run(w, joined, "dontAsk").action == "deny"
    split_owner = "torque workspace \\\r\n ai-access full --path ."
    assert "admin" in KINDS(split_owner) and run(w, split_owner).action == "deny"
    assert not routes.is_simple("torque x \\\r\n --y") and routes.is_simple("torque x \\\n --y")


def test_single_quotes_keep_a_backslash_newline():
    [word] = [w for w in routes._tokens("echo 'a\\\nb'") if w != "echo"]
    assert word == "a\\\nb"


@pytest.mark.parametrize("command", [
    # the path or the query is finished by the shell: it is not the one the gate read
    'suffix=/../003000000000001AAA; sf api request rest "/services/data/v66.0/sobjects/Contact/describe$suffix" -o acme-prod',
    'sf api request rest "/services/data/v66.0/sobjects/Contact/describe$suffix" --target-org acme-prod',
    "sf api request rest /services/data/v66.0/sobjects/Contact/describe$suffix --target-org acme-prod",
    'fields=CreatedBy.Name; sf api request rest "/services/data/v66.0/tooling/query/?q=SELECT+$fields+FROM+ApexClass" -o acme-prod',
    'sf api request rest "/services/data/v66.0/tooling/query/?q=SELECT+Id+FROM+`echo User`" -o acme-prod',
    'sf api request rest "$PATH_TO_ANYTHING" -o acme-prod',
    'sf api request rest /services/data/v66.0/limits -X "$METHOD" -o acme-prod',
    'sf api request rest /services/data/v66.0/sobjects/{Contact,describe} -o acme-prod',
    'sf api request rest /services/data/v66.0/sobjects/Contact/* -o acme-prod',
])
def test_a_rest_request_the_shell_fills_in_is_not_a_schema_read(w, command):
    found = B(command)
    assert not any(kind == "read" for kind, _, _ in found), found
    assert any(kind in ("org_write", "admin") for kind, _, _ in found), found
    assert run(w, command).action == "deny"                 # no approval; and never a silent schema read
    assert run(w, command, "bypassPermissions").action == "deny"


@pytest.mark.parametrize("command,kinds,action", [
    ('X=1 sf data query -o acme-prod -q "SELECT Email FROM Contact LIMIT 1" --json',
     [("read", "acme-prod", "records"), ("unverifiable", "acme-prod", None)], "deny"),
    ('export X=1; sf data query -o acme-prod -q "SELECT Email FROM Contact LIMIT 1" --json',
     [("local", None, None), ("read", "acme-prod", "records"), ("unverifiable", "acme-prod", None)], "deny"),
    ("X=1 sf apex get log -o acme-prod --log-id 07L000000000001AAA",
     [("read", "acme-prod", "debug_logs"), ("unverifiable", "acme-prod", None)], "deny"),
    ("env X=1 sf org list users -o acme-prod",
     [("read", "acme-prod", "records"), ("unverifiable", "acme-prod", None)], "deny"),
    ("X=1 sf sobject describe -s Account -o acme-prod",
     [("read", "acme-prod", None), ("unverifiable", "acme-prod", None)], "ask"),
    ("export X=1; sf sobject describe -s Account -o acme-prod",
     [("local", None, None), ("read", "acme-prod", None), ("unverifiable", "acme-prod", None)], "ask"),
    ("X=1 sf data delete record -s Account -i 001 -o acme-prod", [("unverifiable", "acme-prod", None)], "ask"),
])
def test_a_variable_keeps_the_route_and_its_data_class(w, command, kinds, action):
    # The consultant is asked because of the variable; the consent's classes and the
    # approval a write needs still apply.
    assert B(command) == kinds
    decision = run(w, command)
    assert decision.action == action, decision.reason
    assert run(w, command, "bypassPermissions").action == "deny"


@pytest.mark.parametrize("command,kind", [
    ("powershell -Command 'torque workspace ai-access full'", "admin"),
    ("pwsh -c 'torque workspace ai-access full --path .'", "admin"),
    ("powershell.exe -NoProfile -Command 'torque approval grant req-000000000001'", "admin"),
    ("pwsh -c 'sf org display -o acme-prod'", "credential"),
])
def test_a_powershell_command_string_in_a_bash_call_is_read(w, command, kind):
    assert kind in KINDS(command), KINDS(command)
    assert run(w, command).action == "deny"


def test_a_read_in_a_powershell_string_in_a_bash_call_meets_the_consent(w):
    command = "pwsh -c 'sf data query -q \"SELECT Email FROM Contact\" -o acme-prod'"
    assert ("read", "acme-prod", "records") in B(command)
    assert run(w, command).action == "deny"
    assert run(w, "pwsh -c 'sf sobject describe -s Account -o acme-prod'").action == "ask"


# ---- how a word was written

def test_words_remember_their_quoting():
    words = routes._tokens("""a $x "$y" '$z' \\$w "a*b" c* '{d}' {e} "`f`" g\\ h "i\\$j" """)
    assert [str(word) for word in words] == ["a", "$x", "$y", "$z", "$w", "a*b", "c*", "{d}", "{e}", "`f`", "g h",
                                             "i\\$j"]
    loose = [bool(routes._LOOSE_CHARS & set(routes._raw(word))) for word in words]
    assert loose == [False, True, False, False, False, False, True, False, True, False, False, False]
    assert [routes._filled(word) for word in words] == [False, False, True, False, False, False, False, False, False,
                                                        True, False, False]
    assert routes._tokens("a 'b") is None
    # a word the gate did not split itself is read as unquoted
    assert routes._adds_words(["$x"]) and not routes._adds_words(["x", "a*b"]) and routes._adds_words(["*"])
    plain = "sf data query -q 'SELECT Id FROM Account' -o acme-prod"
    assert all(type(r.org) is not str or r.org == "acme-prod" for r in classify("Bash", {"command": plain}))


# ---- round 4: comments, attached options, REST options, a legacy name, ANSI-C quoting

@pytest.mark.parametrize("command,kinds,action", [
    # Bash and PowerShell drop the words after an unquoted #; cmd.exe runs them. Both readings must pass.
    ('sf data query -q "SELECT Name, Email FROM Contact LIMIT 10" --json # -o acme-prod',
     [("no_org", None, "records"), ("read", "acme-prod", "records")], "deny"),
    ("sf sobject describe -s Account # -o acme-prod", [("no_org", None, None), ("read", "acme-prod", None)], "deny"),
    ("sf project deploy start -o acme-prod --source-dir force-app # --dry-run",
     [("org_write", "acme-prod", None), ("check_only", "acme-prod", None)], "deny"),
    ("torque deploy --target-org acme-prod --source-dir force-app # --dry-run",
     [("org_write", "acme-prod", None), ("check_only", "acme-prod", None)], "deny"),
    ("torque approval permissions --workspace W # x\n--write", None, None),
    ("sf sobject describe -s Account -o acme-prod # look at the fields", [("read", "acme-prod", None)], "allow"),
    ("sf sobject describe -s Account -o acme-prod '# not a comment'", [("read", "acme-prod", None)], "allow"),
    ("sf sobject describe -s Account -o acme-prod a#b", [("read", "acme-prod", None)], "allow"),
    ("sf sobject describe -s Account -o acme-prod \\# x", [("read", "acme-prod", None)], "allow"),
])
def test_a_comment_cannot_carry_the_words_the_gate_reads(w, command, kinds, action):
    if kinds is not None:
        assert B(command) == kinds, command
        assert run(w, command).action == action


@pytest.mark.parametrize("command", [
    "sf api request rest /services/data/v66.0/sobjects/Account/001000000000001AAA -XDELETE -o acme-prod",
    "sf api request rest /services/data/v66.0/sobjects/Account/001000000000001AAA -X=DELETE -o acme-prod",
    "sf api request rest /services/data/v66.0/limits -bx -o acme-prod",
    "sf data query -q x -o acme-prod -oother",
    "sf data query -q x -o acme-prod -o=other",
    "sf data query -q x -o acme-prod -to other",
    "sf sobject describe -sAccount -o acme-prod",
    "sf data delete record -s Account -i 001 -o acme-prod -oother",
    "torque browser multiprofile visit --target-org acme-prod -oother",
    "torque recover show abc --org acme-prod -oother",
])
def test_an_option_with_its_value_attached_is_refused(w, command):
    assert KINDS(command) == ["admin"], command
    assert run(w, command).action == "deny"


@pytest.mark.parametrize("command,kind", [
    ("sf api request rest /services/data/v66.0/limits -o acme-prod", "read"),
    ("sf api request rest /services/data/v66.0/limits -X GET -o acme-prod", "read"),
    ("sf api request rest /services/data/v66.0/limits --method=get --target-org=acme-prod", "read"),
    ("sf api request rest /services/data/v66.0/limits -H 'Accept: application/json' -i --json -o acme-prod", "read"),
    ("sf api request rest /services/data/v66.0/limits --api-version 66.0 -S out.json -o acme-prod", "read"),
    ("sf api request rest /services/data/v66.0/limits -X DELETE -o acme-prod", "org_write"),
    ("sf api request rest /services/data/v66.0/limits --method=PATCH -o acme-prod", "org_write"),
    ("sf api request rest /services/data/v66.0/limits --method -o acme-prod", "org_write"),
    ("sf api request rest /services/data/v66.0/limits -X -o acme-prod", None),
    ("sf api request rest /services/data/v66.0/limits --body x -o acme-prod", "org_write"),
    ("sf api request rest /services/data/v66.0/limits -b x -o acme-prod", "org_write"),
    ("sf api request rest /services/data/v66.0/limits --file r.json -o acme-prod", "org_write"),
    ("sf api request rest /services/data/v66.0/limits -f r.json -o acme-prod", "org_write"),
    ("sf api request rest /services/data/v66.0/limits --flags-dir d -o acme-prod", "org_write"),
    ("sf api request rest /services/data/v66.0/limits --some-new-option -o acme-prod", "org_write"),
    ("sf api request rest /services/data/v66.0/limits --json=1 -o acme-prod", "org_write"),
    ("sf api request rest /services/data/v66.0/limits -o acme-prod -X", "org_write"),
])
def test_a_rest_request_is_a_read_only_with_options_the_gate_knows(command, kind):
    found = KINDS(command)
    assert "read" not in found if kind is None else found == [kind], (command, found)


def test_an_unquoted_rest_url_is_read_as_written():
    assert B("sf api request rest /services/data/v66.0/query?q=SELECT+Name+FROM+Contact -o acme-prod") == [
        ("read", "acme-prod", "records")]
    assert B("sf api request rest /services/data/v66.0/tooling/query?q=SELECT+Name+FROM+ApexClass -o acme-prod") == [
        ("read", "acme-prod", None)]
    assert B("sf api request rest /services/data/v66.0/tooling/query?q=SELECT+CreatedBy.Name+FROM+ApexClass "
             "-o acme-prod") == [("read", "acme-prod", "records")]


@pytest.mark.parametrize("command,expected", [
    ('sf force data soql query -q "SELECT Email FROM Contact" -o acme-prod', [("read", "acme-prod", "records")]),
    ("sf data soql query -q x -o acme-prod", [("read", "acme-prod", "records")]),
    ("sf query soql data -q x -o acme-prod", [("read", "acme-prod", "records")]),
    ("sf force:data:soql:query -q x -o acme-prod", [("read", "acme-prod", "records")]),
    ("sf force apex log get -o acme-prod", [("read", "acme-prod", "debug_logs")]),
    ("sf force data record get -s Contact -i 003 -o acme-prod", [("read", "acme-prod", "records")]),
    ("sf force data tree export -q x -o acme-prod", [("read", "acme-prod", "records")]),
])
def test_legacy_read_names_as_separate_words(command, expected):
    assert B(command) == expected, command


def test_an_incomplete_legacy_read_name_is_refused():
    for command in ("sf soql -q x -o acme-prod", "sf force data soql -q x -o acme-prod", "sf soql query -o acme-prod"):
        assert KINDS(command) == ["admin"], command


@pytest.mark.parametrize("command", [
    # inside double quotes $'...' is not ANSI-C quoting: Bash passes it as it stands
    "torque approval permissions --hook-python \"$'\\x22;echo \\x22'\" --write --workspace W",
    "torque approval permissions --hook-python \"$'\\x27\" --write --workspace W",
    # outside quotes it is, and the decoded word is what runs
    "torque $'work\\x73pace' ai-access full --path .",
    "torque workspace $'ai-acce\\x73\\x73' full --path .",
    "torque approval permissions $'--wri\\x74e' --workspace W",
    "torque approval permissions --workspace W $'\\x2d\\x2dwrite'",
    "torque workspace $\"ai-access\" full --path .",
])
def test_ansi_c_quoting_is_decoded_where_the_shell_decodes_it(w, command):
    assert "admin" in KINDS(command), (command, KINDS(command))
    assert run(w, command, "bypassPermissions").action == "deny"


def test_ansi_c_words_are_plain_text():
    words = routes._tokens("a $'b c' $'d\\x24e' $'f\\'g' \"$'h'\" $'' $'i;j' $\"k\"")
    # $"k" is a string the shell may translate: it keeps its `$`, a word the shell builds
    assert [str(word) for word in words] == ["a", "b c", "d$e", "f'g", "$'h'", "", "i;j", "$k"]
    assert routes._adds_words(words[-1:]) and not routes._adds_words(words[1:4])
    assert not routes._LOOSE_CHARS & set(routes._raw(words[2])) and routes._filled(words[4])
    assert len(routes._split("echo $'a;b' c")[0]) == 1          # the decoded ; is text, not a separator


def test_env_chdir_is_asked_about(w):
    assert KINDS("env -C ../other sf sobject describe -s Account -o acme-prod") == ["unverifiable", "read"]
    assert KINDS("env --chdir=../other sf sobject describe -s Account -o acme-prod") == ["unverifiable", "read"]
    assert run(w, "env -C ../other sf sobject describe -s Account -o acme-prod").action == "ask"
    assert KINDS("env X=1 sf sobject describe -s Account -o acme-prod") == ["read", "unverifiable"]


# ---- round 5: a comment and a here-document are read as Bash reads them

QUERY = 'sf data query -o acme-prod -q "SELECT Name, Email FROM Contact LIMIT 10" --json'
DEPLOY = "sf project deploy start -o acme-prod --source-dir force-app"


@pytest.mark.parametrize("command,kind,action", [
    # a backslash at the end of a comment does not continue it: the next line runs
    ("echo ok # note \\\n" + QUERY, "read", "deny"),
    (": # note \\\n" + DEPLOY, "org_write", "deny"),
    ("true # x \\\ntorque workspace ai-access full --path .", "admin", "deny"),
    ("echo ok # note \\\r\n" + QUERY, "read", "deny"),
    ("echo ok #\\\n" + QUERY, "read", "deny"),
    # a quote inside a comment is not a quote: it cannot pair with one on a later line
    ("echo a # it's\n" + DEPLOY + " # don't", "org_write", "deny"),
    ("echo a # it's\nsf data delete record -s Account -i 001 -o acme-prod # don't", "org_write", "deny"),
    ('echo a # say "x\n' + QUERY + ' # "y', "read", "deny"),
    ("echo a # it's\ntorque workspace ai-access full --path . # that's all", "admin", "deny"),
    # nor is one inside the body of a here-document
    ("cat <<EOF\nit's a body\nEOF\n" + DEPLOY + " # don't", "org_write", "deny"),
    ("cat <<'EOF'\n\"open\nEOF\n" + QUERY + ' # "', "read", "deny"),
    ("cat <<-EOF\n\tit's\n\tEOF\ntorque workspace ai-access full --path . # x'", "admin", "deny"),
])
def test_a_comment_or_a_body_cannot_hide_the_next_line(w, command, kind, action):
    found = KINDS(command)
    assert kind in found, (command, found)
    assert run(w, command).action == action
    assert run(w, command, "bypassPermissions").action == "deny"


def test_a_line_that_is_only_a_comment_has_no_route(w):
    describe = "sf sobject describe -s Account -o acme-prod"
    assert B("# list the fields\n" + describe) == [("read", "acme-prod", None)]
    assert B("  # indented\n\t# and tabbed\n" + describe + "\n# done") == [("read", "acme-prod", None)]
    assert run(w, "# list the fields\n" + describe).action == "allow"
    assert KINDS("# sf org display -o acme-prod") == ["local"]                 # nothing runs
    assert "credential" in KINDS("# a note\nsf org display -o acme-prod")      # the next line does
    assert set(KINDS("echo a # sf org display -o acme-prod")) == {"local"}     # words of echo, in any shell


def test_what_follows_a_comment_mark_is_still_split_for_cmd(w):
    # cmd.exe has no # comment: `&` there starts another command, so the gate keeps reading it.
    assert "credential" in KINDS("echo a # x & sf org display -o acme-prod")
    assert "org_write" in KINDS("echo a # x ; sf data delete record -s A -i 001 -o acme-prod")


@pytest.mark.parametrize("command,expected", [
    ("echo ok \\\n" + DEPLOY, "org_write"),
    ("echo ok \\\r\n" + DEPLOY, "org_write"),
    ("echo ok \\\ntorque workspace ai-access full --path .", "admin"),
    ("echo ok \\\n" + QUERY, "read"),
])
def test_powershell_does_not_join_lines_at_a_backslash(w, command, expected):
    assert expected in KINDS(command, "PowerShell"), KINDS(command, "PowerShell")
    assert run(w, command, tool="PowerShell").action == "deny"
    # in Bash a backslash before LF joins the lines (one echo); before CR LF it joins nothing
    assert (expected in KINDS(command)) == ("\r" in command)


def test_words_in_comments_and_bodies_keep_their_text():
    words = [str(word) for word in routes._tokens("a # it's \"x\" \\ y\nb")]
    assert words == ["a", "#", "it's", '"x"', "\\", "y", "\n", "b"]
    assert [str(word) for word in routes._tokens("a#b 'c #d' \\#e $# ${#f}")] == ["a#b", "c #d", "#e", "$#", "${#f}"]
    body = [str(word) for word in routes._tokens("cat <<E\nit's \"a\"\nE\nb 'c d'")]
    assert body == ["cat", "<<", "E", "\n", "it's", '"a"', "\n", "E", "\n", "b", "c d"]


@pytest.mark.parametrize("path,expected", [
    # the debug-log class comes from the endpoint or from what the query reads, not from a word in it
    ("/services/data/v66.0/query?q=SELECT+Email+FROM+Contact+WHERE+LastName+!=+'ApexLog'", "records"),
    ("/services/data/v66.0/query?q=SELECT+Email+FROM+Contact+WHERE+ApexLog__c+=+null", "records"),
    ("/services/data/v66.0/tooling/query/?q=SELECT+Email+FROM+User+WHERE+Name+!=+'ApexLog'", "records"),
    ("/services/data/v66.0/tooling/query/?q=SELECT+Name+FROM+ApexClass+WHERE+Name+=+'ApexLog'", None),
    ("/services/data/v66.0/query?q=SELECT+Id,+(SELECT+Email+FROM+Contacts)+FROM+ApexLog", "records"),
    ("/services/data/v66.0/query?q=SELECT+Id+FROM+ApexLog&x=1", "records"),
    ("/services/data/v66.0/sobjects/Contact/003000000000001AAA?fields=ApexLog", "records"),
    ("/services/data/v66.0/query?q=SELECT+Id,+LogLength+FROM+ApexLog", "debug_logs"),
    ("/services/data/v66.0/tooling/query/?q=SELECT+Id+FROM+Apex%4Cog", "debug_logs"),
    ("/services/data/v66.0/sobjects/ApexLog/07L000000000001AAA/Body", "debug_logs"),
    ("/services/data/v66.0/tooling/sobjects/ApexLog/07L000000000001AAA", "debug_logs"),
    ("/services/data/v66.0/sobjects/ApexLog", "debug_logs"),
])
def test_the_debug_log_class_comes_from_what_is_read(path, expected):
    assert rest_data_class(path) == expected, path


def test_debug_log_consent_does_not_cover_a_record_query(w, tmp_path):
    letter = tmp_path / "b.pdf"
    letter.write_bytes(b"agreement")
    consent.record_consent(w, "Acme", "2026-09-30", letter, ["metadata", "debug_logs"], ["acme-prod"], ["Contact"],
                           presence=YES, resolve=ORGS.get)
    consent.sign_off(w, "Acme", "Reviewer", presence=YES)
    sneaky = ("sf api request rest \"/services/data/v66.0/query?q=SELECT+Email+FROM+Contact+WHERE+LastName+!=+"
              "'ApexLog'\" -o acme-prod")
    decision = run(w, sneaky)
    assert decision.action == "deny" and "record data" in decision.reason
    logs = "sf api request rest '/services/data/v66.0/query?q=SELECT+Id+FROM+ApexLog' -o acme-prod"
    assert run(w, logs).action == "allow"


@pytest.mark.parametrize("command", [
    # a substitution inside double quotes has quotes of its own: the words after it are still this command's
    'torque approval permissions --hook-python "$(printf "x;echo ")" --write --workspace .',
    'torque approval permissions --hook-python "$(printf "x" | tr "a" "b"; echo ")")" --write --workspace .',
    'torque approval permissions --hook-python "a $(echo "$(echo "x;y")") b" --write --workspace .',
    'torque approval permissions --hook-python "`printf "x;echo "`" --write --workspace .',
    'torque approval permissions --hook-python="$(printf "x;echo ")" --write --workspace .',
    'torque workspace ai-access "$(printf "full;echo ")" --path .',
])
def test_a_substitution_in_double_quotes_does_not_end_the_word(w, command):
    assert "admin" in KINDS(command), (command, KINDS(command))
    assert run(w, command, "bypassPermissions").action == "deny"


def test_a_substitution_in_double_quotes_is_one_filled_word():
    words = routes._tokens('a "$(printf "x;y" | tr "x" "z")" b "c `echo "d e"` f" g')
    assert [str(word) for word in words] == ["a", '$(printf "x;y" | tr "x" "z")', "b", 'c `echo "d e"` f', "g"]
    assert routes._filled(words[1]) and routes._filled(words[3]) and not routes._LOOSE_CHARS & set(routes._raw(words[1]))
    assert KINDS('torque session add --workspace . --client acme --summary "$(printf "a; b")" --status executed') == [
        "local"]
    unclosed = routes._tokens('a "$(printf "x')                     # a substitution that does not close
    assert unclosed is None or any(routes._LOOSE_CHARS & set(routes._raw(word)) for word in unclosed)
    assert KINDS('torque approval permissions --hook-python "$(printf "x --write --workspace .') != ["local"]


@pytest.mark.parametrize("text,decoded", [
    ("a\\x41b", "aAb"), ("\\101\\60", "A0"), ("\\u41", "A"), ("\\u71uery", "query"), ("\\U00000041", "A"),
    ("\\x4", "\x04"), ("a\\0b", "a"), ("ai-access\\0ignored", "ai-access"), ("\\x00tail", ""), ("\\cA", "\x01"),
    ("\\q", "\\q"), ("\\xZ", "\\xZ"), ("\\t\\n\\\\\\'\\\"", "\t\n\\'\""), ("\\e[0m", "\x1b[0m"), ("plain", "plain"),
    ("end\\", "end\\"),
])
def test_ansi_c_escapes_as_bash_reads_them(text, decoded):
    assert routes._ansi_c(text) == decoded


def test_ansi_c_spellings_of_a_command_are_the_command(w):
    assert KINDS("torque workspace $'ai-access\\0ignored' full --path .") == ["admin"]
    assert B("sf data $'\\u71uery' -q 'SELECT Email FROM Contact' -o acme-prod") == [("read", "acme-prod", "records")]
    assert B("sf $'d\\x61ta' $'qu\\145ry' -q x -o acme-prod") == [("read", "acme-prod", "records")]


# ---- round 6: what a substitution runs is read with the substitution's own quotes

NESTED_QUERY = 'sf data query -q "SELECT Name FROM Contact" -o acme-prod'


@pytest.mark.parametrize("command,kind", [
    (f'echo "$({NESTED_QUERY})"', "read"),
    (f'echo "$(echo ")" ; {NESTED_QUERY})"', "read"),
    (f'echo "$(echo ")" && {NESTED_QUERY})"', "read"),
    (f"echo \"$(echo ')' ; {NESTED_QUERY})\"", "read"),
    (f'echo "$(echo \\) ; {NESTED_QUERY})"', "read"),
    (f'x="$(echo "(" ; {NESTED_QUERY})"', "read"),
    (f'echo "`echo ")" ; {NESTED_QUERY}`"', "read"),
    (f'echo "$(echo "$(echo ")" ; {NESTED_QUERY})")"', "read"),
    (f'torque session add --workspace . --client acme --summary "$(echo ")" ; {NESTED_QUERY})" --status executed', "read"),
    ('echo "$(echo ")" ; torque workspace ai-access full --path .)"', "admin"),
    ('echo "$(echo ")" ; sf project deploy start -o acme-prod -d force-app)"', "org_write"),
    ('echo "$(echo ")" ; sf org display -o acme-prod)"', "credential"),
    (f'echo "$(echo ")" ; {NESTED_QUERY}', "read"),                    # one that does not close
])
def test_a_command_inside_a_substitution_is_read_whatever_its_quotes(w, command, kind):
    assert kind in KINDS(command), (command, KINDS(command))
    assert run(w, command).action == "deny"


def test_substitutions_are_found_with_their_own_quotes():
    found = routes._substitutions('a "$(b ")" ; c "$(d)")" e `f ")"` \'$(g)\' \\$(h) $(i')
    assert found == ['b ")" ; c "$(d)"', 'f ")"', "i"]


# ---- round 6, second pass: where a substitution ends, PowerShell's escapes, a REST URL after options

@pytest.mark.parametrize("command", [
    f'echo "$(case x in x) {NESTED_QUERY};; esac)"',
    f'echo "$(case x in (x) {NESTED_QUERY};; *) true;; esac)"',
    f'echo "$( (case x in x) {NESTED_QUERY};; esac) )"',
    f'echo "$(echo ok # )\n{NESTED_QUERY}\n)"',
    f'echo "$(echo ok # it\'s )\n{NESTED_QUERY}\n)"',
    f'echo "$(cat <<E\n)\nE\n{NESTED_QUERY})"',
    f"echo $(echo $')' ; {NESTED_QUERY})",
    f"echo $(echo $'\\'' ; {NESTED_QUERY})",
    f'echo "$(echo ${{x//)/y}} ; {NESTED_QUERY})"',
    f"cat <<E\nit's a body\nE\necho \"$(echo \\) ; {NESTED_QUERY})\"",
    f"cat <<E\nit's a body\nE\n# c\necho \"$(echo ok # it's )\n{NESTED_QUERY}\n)\"",
    f"cat <<E\n$({NESTED_QUERY})\nE",
])
def test_no_shell_construct_hides_a_command_inside_a_substitution(w, command):
    assert ("read", "acme-prod", "records") in B(command), (command, B(command))
    assert run(w, command).action == "deny"


@pytest.mark.parametrize("command", [
    'torque approval permissions --hook-python "$(case x in x) echo "a;b" ;; esac)" --write --workspace .',
    'torque approval permissions --hook-python "$(echo ok # )\necho "a;b"\n)" --write --workspace .',
    'torque approval permissions --hook-python "$(cat <<E\n)\nE\necho "a;b")" --write --workspace .',
])
def test_the_words_after_such_a_substitution_are_still_the_commands(w, command):
    assert "admin" in KINDS(command), (command, KINDS(command))


def test_the_net_under_the_substitution_reader():
    tails = routes._substitution_tails('a "$(b ")" ; c "$(d)")" e `f` g')
    assert tails[0].startswith('b ")" ; c') and any(tail.startswith("d)") for tail in tails)
    assert any(tail.startswith("f ; ") for tail in tails)               # a backtick span, its end made a separator
    # prose is not read as a command: single quotes, a comment, an escaped mark
    assert routes._substitution_starts("echo 'the `Name` field; ran sf data query' # $(x) \\$(y)") == []
    # (the older, looser scan still asks about a backticked word in single quotes, as it did before)
    assert set(KINDS("torque session add --workspace . --client acme --summary 'fixed the `Name` field; ran "
                     "sf data query -o acme-prod' --status executed")) <= {"local", "unverifiable"}
    assert [mark for _, mark in routes._substitution_starts("echo `date` and `whoami`")] == ["`", "`"]   # the opening ones
    assert set(KINDS('torque session add --workspace . --client acme --summary "$(cat notes.txt)" --status executed'
                     )) == {"local"}


@pytest.mark.parametrize("command", [
    'torque approval permissions --hook-python "a`"; echo `"b" --write --workspace .',
    'torque approval permissions --hook-python "a""; echo ""b" --write --workspace .',
    "torque approval permissions --hook-python 'a''; echo ''b' --write --workspace .",
    'torque workspace ai-access "full`"; echo `"x" --path .',
])
def test_powershell_escaped_quotes_do_not_end_a_string(w, command):
    assert "admin" in KINDS(command, "PowerShell"), (command, KINDS(command, "PowerShell"))
    assert run(w, command, tool="PowerShell").action == "deny"


def test_powershell_escapes_written_as_bash_writes_them():
    convert = routes._powershell_escapes
    assert convert('a "b`"c" d') == 'a "b\\"c"${native} d'        # the mark: see the native-argument tests below
    assert convert('a "b""c" d') == 'a "b\\"c"${native} d'
    assert convert("a 'b''c' d") == "a 'b'\\''c' d"
    assert convert("a `\n b") == "a  b" and convert("C:\\Work\\x") == "C:\\\\Work\\\\x"
    assert convert('a "`$x" `;b') == 'a "\\$x" \\;b'


def test_a_write_only_a_powershell_reading_finds_still_needs_its_approval(w):
    hidden = 'echo "x`""; sf data delete record -s Account -i 001 -o acme-prod'
    assert "org_write" in KINDS(hidden, "PowerShell")
    assert run(w, hidden, tool="PowerShell").action == "deny"
    plain = "sf data delete record -s Account -i 001 -o acme-prod"
    assert KINDS(plain, "PowerShell").count("org_write") == 1           # not counted twice


@pytest.mark.parametrize("command,expected", [
    ("sf api request rest -o acme-prod /services/data/v66.0/tooling/sobjects/ApexLog/07L000000000001AAA/Body",
     ("read", "acme-prod", "debug_logs")),
    ("sf api request rest -o acme-prod /services/data/v66.0/sobjects/Account/describe", ("read", "acme-prod", None)),
    ("sf api request rest --target-org=acme-prod -X GET /services/data/v66.0/limits --json", ("read", "acme-prod", None)),
    ("sf api request rest -H 'Accept: x' /services/data/v66.0/sobjects/Contact/003000000000001 -o acme-prod",
     ("read", "acme-prod", "records")),
    ("sf api request rest -o acme-prod", ("org_write", "acme-prod", None)),                      # no URL
    ("sf api request rest /services/data/v66.0/limits /services/data/v66.0/query -o acme-prod",
     ("org_write", "acme-prod", None)),                                                          # two
])
def test_the_url_of_a_rest_request_is_found_wherever_it_stands(command, expected):
    assert B(command) == [expected], command


def test_metadata_consent_covers_a_describe_written_after_its_options(w):
    assert run(w, "sf api request rest -o acme-prod /services/data/v66.0/sobjects/Account/describe").action == "allow"
    assert run(w, "sf api request rest -o acme-prod /services/data/v66.0/sobjects/Account/001").action == "deny"


# ---- round 7: PowerShell's here-strings, block comments, assignments, and the same readings in a Bash call

PS_QUERY = 'sf data query -q "SELECT Email FROM Contact" -o acme-prod'


@pytest.mark.parametrize("command", [
    f"echo @\"\n\" '\n\"@\n{PS_QUERY}\n#'",                       # a here-string holding quotes
    f"echo @'\n\" '\n'@\n{PS_QUERY}",
    f"$t = @\"\nit's\n\"@\n{PS_QUERY} # don't",
    f"$t = @\"\n$({PS_QUERY})\n\"@",                              # a subexpression in an expandable here-string runs
    f"<# ' #> {PS_QUERY} #'",                                     # a block comment holding a quote
    f"<#\nit's\n#>\n{PS_QUERY}",
    f"$rows = {PS_QUERY}",                                        # an assignment runs the command on its right
    f"[string]$rows = {PS_QUERY}",
    f"echo 'it''s' ; echo ok # '\n\niex '{PS_QUERY}' # c",
    f'echo "a`"" ; {PS_QUERY}',
    f"<# ' #> echo ok #'\n<# c #>\n<# ' #> {PS_QUERY} #'\n# c",  # a quote in a line comment opens nothing
    f"# it's\n<# x #> {PS_QUERY}",
    f"echo a # don't\n<# \" #> {PS_QUERY} # \"",
])
def test_powershell_quoting_forms_do_not_hide_a_read(w, command):
    assert ("read", "acme-prod", "records") in B_PS(command), (command, B_PS(command))
    assert run(w, command, tool="PowerShell").action == "deny"
    assert run(w, command, "bypassPermissions", tool="PowerShell").action == "deny"


def B_PS(command):
    return [(r.kind, r.org, r.data) for r in classify("PowerShell", {"command": command})]


@pytest.mark.parametrize("command", [
    "echo @\"\n\" '\n\"@\ntorque workspace ai-access full --path .\n#'",
    "<# ' #> torque approval grant req-000000000001 #'",
    "$r = torque workspace ai-access full --path .",
])
def test_powershell_quoting_forms_do_not_hide_an_owner_command(w, command):
    assert "admin" in KINDS(command, "PowerShell")
    assert run(w, command, tool="PowerShell").action == "deny"


def test_a_powershell_here_string_is_one_quoted_string():
    convert = routes._powershell_escapes
    assert convert("echo @'\nit's \"x\n'@ ; b") == "echo 'it'\\''s \"x'${native} ; b"
    assert convert('echo @"\nsay "hi" `$x\n"@ ; b') == 'echo "say \\"hi\\" \\$x"${native} ; b'
    assert convert("echo @'\nit's x\n'@ ; b") == "echo 'it'\\''s x' ; b"
    assert convert("a <# it's #> b") == "a   b" and convert("a <# never closed") == "a  "
    assert convert("a # it's\n<# ' #> b #'") == "a # it's\n  b #'"       # a line comment is copied as it stands
    assert convert("echo a#'b' ; c") == "echo a#'b' ; c"                 # `#` inside a word starts no comment
    assert routes._PS_ASSIGNMENT.sub(r"\1", "$x = sf a; [int]$n += sf b\n$env:Y = sf c") == "sf a; sf b\nsf c"
    assert routes._PS_ASSIGNMENT.sub(r"\1", "if ($x -eq 1) { sf a }; $y == 2") == "if ($x -eq 1) { sf a }; $y == 2"


@pytest.mark.parametrize("command,kind", [
    ('powershell -Command \'torque approval permissions --hook-python "a`"; echo `"b" --write --workspace .\'', "admin"),
    ("pwsh -c \"echo @'\n' x\n'@\ntorque workspace ai-access full --path .\"", "admin"),
    (f"powershell -NoProfile -Command '$rows = {PS_QUERY}'".replace('"SELECT Email FROM Contact"', "x"), "read"),
    ("powershell 'torque workspace ai-access full --path .'", "admin"),
])
def test_powershell_in_a_bash_call_gets_the_same_readings(w, command, kind):
    assert kind in KINDS(command), (command, KINDS(command))
    assert run(w, command).action == "deny"


# ---- round 8: the characters PowerShell reads as quotes and spaces, its stop-parsing token, the strings
# ---- Bash has and PowerShell does not, what can stand before a command, and the second net

@pytest.mark.parametrize("command", [
    f"echo \u201c ' \u201d ; {PS_QUERY} #'",                       # curly double quotes are quotes
    f"echo \u2018 \" \u2019 ; {PS_QUERY} #\"",                     # so are curly single quotes
    f"echo \u201c ' \" ; {PS_QUERY} #'",                           # and they pair with the ASCII ones
    f"echo \u201e ' \u201c ; {PS_QUERY} #'",
    f"echo @\u201c\n ' \n\u201d@\n{PS_QUERY} #'",
    PS_QUERY.replace("sf data query ", "sf\u00a0data\u00a0query\u00a0"),   # a no-break space is a space
    PS_QUERY.replace("sf ", "sf\u3000"),
    f"echo a\u00a0;\u00a0{PS_QUERY}",
    f"echo --% ' | {PS_QUERY} #'",                                 # the stop-parsing token ends at a pipe
    f"echo a --% | {PS_QUERY}",
    f"echo $'a\\' ; {PS_QUERY} #'",                               # no ANSI-C string in PowerShell
    f"echo $\"a\\\" ; {PS_QUERY} #\"",
    f"echo ${{a'b}} ; {PS_QUERY} #'",                              # a variable name may hold a quote
    f"echo \"${{a\"b}}\" ; {PS_QUERY} #\"",
    f"& {{ return {PS_QUERY} }}",                                  # return, throw and exit run what follows
    f"try {{ throw {PS_QUERY} }} catch {{ }}",
    f"exit {PS_QUERY}",
    f". {PS_QUERY}",                                               # a dot runs a command in this scope
    f"foreach ($row in {PS_QUERY}) {{ }}",
    f"@{{rows = {PS_QUERY}}}",                                     # a value in a hash literal
    f"@{{'rows' = {PS_QUERY}; n = 1}}",
    f"$a, $b = {PS_QUERY}",
    f"${{rows}} = {PS_QUERY}",
    f"$a = $b = {PS_QUERY}",
    f"$rows = . {PS_QUERY}",
    f"if ($true) {{{PS_QUERY}}}",                                  # a brace needs no space
    f"&{{{PS_QUERY}}}",
    f"1 | % {{{PS_QUERY}}}",
])
def test_powershell_forms_bash_does_not_have_do_not_hide_a_read(w, command):
    assert ("read", "acme-prod", "records") in B_PS(command), (command, B_PS(command))
    assert run(w, command, tool="PowerShell").action == "deny"
    assert run(w, command, "bypassPermissions", tool="PowerShell").action == "deny"


@pytest.mark.parametrize("command", [
    "echo \u201c ' \u201d ; torque workspace ai-access full --path . #'",
    "echo --% ' | torque approval grant req-000000000001 #'",
    "echo $'a\\' ; torque workspace ai-access full --path . #'",
    "& { return torque workspace ai-access full --path . }",
    "@{r = torque approval grant req-000000000001}",
    "if ($true) {torque workspace guarded-reads on --path .}",
    "torque\u00a0workspace\u00a0ai-access\u00a0full\u00a0--path\u00a0.",
])
def test_powershell_forms_bash_does_not_have_do_not_hide_an_owner_command(w, command):
    assert "admin" in KINDS(command, "PowerShell"), (command, KINDS(command, "PowerShell"))
    assert run(w, command, tool="PowerShell").action == "deny"


def test_powershell_text_rewritten_for_these_forms():
    convert = routes._powershell_escapes
    assert convert("echo --% ' | sf a") == "echo --% ''\\''' | sf a"
    assert convert("echo --% a b\nsf c") == "echo --% 'a b' \nsf c"
    assert convert("echo x--% ' ; a") == "echo x--% ' ; a"                # only as a word of its own
    assert convert("echo ${a'b} \"${c\"d}\"") == 'echo ${v} "${v}"${native}'
    assert convert("echo $'a' $\"b\"") == "echo \\$'a' \\$\"b\""
    assert convert("&{sf a}", braces=True) == "& ; sf a ; " and convert("&{sf a}") == "&{sf a}"
    assert convert("echo -q x, -o, B ,C a,b") == "echo -q x -o B  C a,b"    # a comma with a space beside it
    assert convert("echo a,\n  b ; echo 'c, d'") == "echo a b ; echo 'c, d'"
    assert convert("echo '{' \"}\"", braces=True) == "echo '{' \"}\""        # not inside quotes
    assert "\u201c ' \u201d \u2018\u00a0\u2014".translate(routes._PS_CHARACTERS) == "\" ' \" ' -"
    plain = routes._powershell_statements
    assert plain("return sf a; throw sf b\nexit sf c") == "sf a; sf b\nsf c"
    assert plain("$a, $b = sf a; ${x} = sf b; $c = $d = sf c") == "sf a; sf b; sf c"
    assert plain("foreach ($x in sf a) { . sf b }") == "foreach (sf a) { sf b }"
    assert plain("@{k = sf a; 'q' = sf b}") == "@{sf a; sf b}"
    assert plain("returned sf a; $x -eq sf b; echo a = b") == "returned sf a; $x -eq sf b; echo a = b"


@pytest.mark.parametrize("command", ["return $x", "exit 1", "exit $LASTEXITCODE", 'echo "a" ; return'])
def test_ordinary_powershell_statements_stay_local(command):
    assert set(KINDS(command, "PowerShell")) == {"local"}, KINDS(command, "PowerShell")


def test_text_that_names_a_command_in_powershell_quoting_is_asked_about(w):
    named = 'echo "sf data query -q x -o acme-prod"'
    # plain words and plain quotes: PowerShell and Bash read them alike, and nothing is added
    assert KINDS(named, "PowerShell") == ["local"]
    assert run(w, named, tool="PowerShell").action == "allow"
    # with quoting only PowerShell has, the gate cannot be sure the text is only text
    for command in (named + " # note", named + " ; echo `n",
                    named + ' ; echo ""', "echo \u2018sf data query -q x -o acme-prod\u2019"):
        assert "unverifiable" in KINDS(command, "PowerShell"), (command, KINDS(command, "PowerShell"))
        assert run(w, command, tool="PowerShell").action == "ask"
        assert run(w, command, "bypassPermissions", tool="PowerShell").action == "deny"
    # after a word that begins with a quoted string, `#` starts a comment for PowerShell; the gate
    # also reads the line with the comment's words, so the command named there has to pass as itself
    after = 'echo "a"#\' ; sf data query -q x -o acme-prod #\''
    assert {"read", "unverifiable"} & set(KINDS(after, "PowerShell")), KINDS(after, "PowerShell")
    assert run(w, after, tool="PowerShell").action in ("ask", "deny")
    # a command the readings did find is not asked about again
    found = "sf sobject describe -s Account -o acme-prod # fields"
    assert KINDS(found, "PowerShell") == ["read"] and run(w, found, tool="PowerShell").action == "allow"
    # and a write keeps its one route: it needs an approval of this exact text
    write = 'sf project deploy start -o acme-prod ; echo "sf data query -q x -o acme-prod" # note'
    assert "unverifiable" not in KINDS(write, "PowerShell") and run(w, write, tool="PowerShell").action == "deny"


# ---- round 9: what a hash literal or a script block holds under a local command, quotes inside ${...},
# ---- the ways PowerShell adds words to a command, two writes on one PowerShell line, the call operator

@pytest.mark.parametrize("command", [
    f"echo @{{a = {PS_QUERY}}}",                                   # a hash literal's value runs
    f"echo @{{'a' = 1; b = {PS_QUERY}}}",
    f"Write-Output @{{ a = @{{ b = {PS_QUERY} }} }}",
    f"echo @{{\n  a = {PS_QUERY}\n}}",
    f"1 | sort {{ {PS_QUERY} }}",                                  # a script block handed to a cmdlet runs
    f"'a' | cp -Destination {{ {PS_QUERY} }}",
])
def test_what_a_powershell_hash_literal_or_block_holds_is_read(w, command):
    assert ("read", "acme-prod", "records") in B_PS(command), (command, B_PS(command))
    assert run(w, command, tool="PowerShell").action == "deny"
    assert run(w, command, "dontAsk", tool="PowerShell").action == "deny"


def test_an_owner_command_in_a_powershell_hash_literal_is_refused(w):
    for command in ("echo @{a = torque workspace ai-access full --path .}",
                    "echo @{a = sf apex get log -o acme-prod}"):
        assert {"admin", "read"} & set(KINDS(command, "PowerShell")), KINDS(command, "PowerShell")
        assert run(w, command, tool="PowerShell").action == "deny"


@pytest.mark.parametrize("command", [
    'echo "${x:-"\'"}"; ' + NESTED_QUERY + " #'",                  # the word in the braces has quotes of its own
    'echo "${x:-\'}\'}" ; ' + NESTED_QUERY + " #'",                # a single quote is plain there
    'echo "${x#\'}"\'}" ; ' + NESTED_QUERY + " #'",                # and quotes after a pattern operator
    "echo ${x:-'}'} ; " + NESTED_QUERY,
    'echo ${x:-"}"} ; ' + NESTED_QUERY,
    'echo "${x:-${y:-"\'"}}" ; ' + NESTED_QUERY + " #'",
    'echo "$(echo "${x:-"\'"}" ; ' + NESTED_QUERY + ')" #\'',
    'echo "${x:-"$(' + NESTED_QUERY + ')"}"',                      # a substitution inside it runs
    'x="${y:-"\'"}"; ' + NESTED_QUERY + " #'",
])
def test_quotes_inside_a_parameter_expansion_do_not_hide_a_command(w, command):
    assert ("read", "acme-prod", "records") in B(command), (command, B(command))
    assert run(w, command).action == "deny"
    assert run(w, command, "dontAsk").action == "deny"


def test_a_parameter_expansion_is_read_to_its_own_end():
    end = routes._parameter_end
    for text, quoted in (('${x:-"\'"}', True), ("${x:-'}'}", False), ('${x:-"}"}', False), ("${x#'}'}", True),
                         ("${x:-$(echo '}')}", True), ("${x:-${y:-\"}\"}}", True), ("${x:-\\}}", True),
                         ("${x:-`echo }`}", True), ("${x:-$'\\'}'}", False)):
        assert end(text + " rest", 0, quoted) == len(text), (text, end(text + " rest", 0, quoted))
    assert end("${x:-'}'}", 0, True) == len("${x:-'}")            # in double quotes a single quote is plain
    assert end('${x:-"never closed', 0, True) is None
    # the masked line keeps such an expansion in its word, and its substitutions are still found
    assert [str(word) for word in routes._tokens('echo "${x:-"a b"}" c')] == ["echo", '${x:-"a b"}', "c"]
    assert [str(word) for word in routes._tokens('echo ${x:-"a b"} c')] == ["echo", '${x:-"a b"}', "c"]
    assert routes._substitutions('echo "${x:-"$(a b)"}"') == ["a b"]
    assert KINDS('torque workspace "${x:-"ai-access"}" full --path .') == ["admin"]
    assert KINDS('torque workspace ${x:-"ai-access"} full --path .') == ["admin"]


@pytest.mark.parametrize("command", [
    "set MORE '-o','other'; " + PS_QUERY + " @MORE",               # splatting adds the items of $MORE as words
    PS_QUERY + " --% -o other",                                    # what follows --% reaches sf as it stands
    PS_QUERY + " ('-o','other')",
    PS_QUERY + " @('-o','other')",
    "sf data query -o acme-prod -q x, -o, other",                  # a comma with a space beside it separates words
    "sf data query -o acme-prod -q x,\n -o,\n other",
])
def test_words_powershell_adds_to_a_read_are_asked_about_or_refused(w, command):
    kinds = set(KINDS(command, "PowerShell"))
    assert kinds & {"unverifiable", "no_org"}, (command, KINDS(command, "PowerShell"))
    assert run(w, command, tool="PowerShell").action in ("ask", "deny")
    assert run(w, command, "dontAsk", tool="PowerShell").action == "deny"


def test_a_splatted_read_with_record_consent_is_still_asked_about(w):
    letter = w / "b.pdf"
    letter.write_bytes(b"agreement")
    consent.record_consent(w, "Acme", "2026-10-01", letter, ["metadata", "records"], ["acme-prod"], ["Contact"],
                           presence=YES, resolve=ORGS.get)
    consent.sign_off(w, "Acme", "Reviewer", presence=YES)
    assert run(w, PS_QUERY, tool="PowerShell").action == "allow"
    for command in (PS_QUERY + " @MORE", PS_QUERY + " @MORE", "sf data query -q x -o acme-prod @MORE"):
        for tool in ("PowerShell", "Bash"):
            assert run(w, command, tool=tool).action == "ask", (tool, command)
            assert run(w, command, "dontAsk", tool=tool).action == "deny"
    assert run(w, "sf data query -o acme-prod -q x, -o, other", tool="PowerShell").action == "deny"   # two orgs


UPDATE = "sf data update record -o acme-prod -s Account -i 001000000000001AAA -v"


def test_two_writes_on_a_powershell_line_are_not_one_approved_command(w):
    # for Bash, $'Name=A\' ; ... #' is one string; PowerShell has no such string and runs both commands
    other = UPDATE + " $'Name=A\\' ; sf data delete record -o acme-dev -s Account -i 001000000000002AAA #'"
    assert B_PS(other).count(("org_write", "acme-prod", None)) == 1
    assert ("org_write", "acme-dev", None) in B_PS(other)
    same = UPDATE + " $'Name=A\\' ; sf data delete record -o acme-prod -s Account -i 001000000000002AAA #'"
    assert [route for route in B_PS(same) if route[0] == "org_write"] == [("org_write", "acme-prod", None)]
    assert routes.is_simple(same) and not routes.is_simple(same, "PowerShell")
    for command in (other, same):
        decision = run(w, command, tool="PowerShell")
        assert decision.action == "deny" and "one approved write command on its own" in decision.reason


def test_one_write_read_twice_is_still_one_write():
    for command in (DEPLOY, DEPLOY + "\\main", "sf project deploy start -o acme-prod --source-dir 'C:\\my app\\src'"):
        assert [route for route in B_PS(command) if route[0] == "org_write"] == [("org_write", "acme-prod", None)]
        assert routes.is_simple(command, "PowerShell")


def test_a_write_beside_something_the_gate_cannot_check_is_refused_not_asked(w):
    # the plain reading sees a word `$result`; the PowerShell reading sees the write
    command = "$result = " + DEPLOY
    assert {"org_write", "unverifiable"} <= set(KINDS(command, "PowerShell"))
    decision = run(w, command, tool="PowerShell")
    assert decision.action == "deny" and "on its own" in decision.reason


def test_a_filtered_advisory_count_is_a_record_read(w):
    plain = "torque advisory impact --target-org acme-prod --sobject Contact"
    assert B(plain) == [("read", "acme-prod", None)] and run(w, plain).action == "allow"
    for where in ('--where "Birthdate = 2000-01-01"', "--where=Email=x", "--where \"Name = 'A'\""):
        for name in ("impact", "receipt"):
            command = f"torque advisory {name} --target-org acme-prod --sobject Contact {where}"
            assert B(command) == [("read", "acme-prod", "records")], (command, B(command))
            decision = run(w, command)
            assert decision.action == "deny" and "record data" in decision.reason


@pytest.mark.parametrize("command", [
    "torque advisory impact --target-org acme-prod --sobject Contact --target other",     # the last one given wins
    "torque advisory impact --target-org acme-prod --sobject Contact --t other",
    "torque advisory impact --target-org acme-prod --sobject Contact --target-or=other",
    "torque advisory impact --sobject Contact --targ acme-prod",
    "torque advisory impact --target-org acme-prod --sobject Contact --wh x",
    "torque logs list --target-org acme-prod --targetuser other",
    "torque deploy start --target-org acme-prod --or other",
    "torque deploy start --target-org acme-prod --dry",
    "torque approval permissions --hook-python x --wri --workspace .",
    "torque session add --workspace . --client acme --cli other --summary x --status executed",
    "torque session add --work elsewhere --client acme --summary x --status executed",
    "torque approval request --workspace . --client acme --target-org acme-prod --capture-before-rec Contact:003",
    "torque guarded counts --workspace . --client acme --target acme-prod --object Contact",
    "torque browser open --target-org acme-prod --head",
    "python -m torque advisory impact --target-org acme-prod --sobject Contact --target other",
    "jsc revert preview --target-org acme-prod --target other",
])
def test_an_option_name_cut_short_is_refused(w, command):
    assert KINDS(command) == ["admin"], (command, KINDS(command))
    decision = run(w, command)
    assert decision.action == "deny" and "cut short" in decision.reason
    assert KINDS(command, "PowerShell") == ["admin"]


def test_option_names_written_in_full_are_not_cut_short():
    # every long option Torque's own parsers define is read as itself
    import re as _re
    root = Path(routes.__file__).resolve().parents[2]
    names = set()
    for folder in (root / "src" / "torque", root / "packages"):
        for path in folder.rglob("*.py"):
            if "tests" in path.parts:
                continue
            names.update(_re.findall(r"""add_argument\([^)]*?["'](--[A-Za-z][A-Za-z0-9-]+)["']""",
                                     path.read_text(encoding="utf-8", errors="replace")))
    assert len(names) > 100
    assert [name for name in sorted(names) if routes._cut_short([name])] == []
    assert routes._cut_short(["--target-org", "A", "--org-data", "x", "--record-id", "1", "--capture-before", "y"]) == ""
    assert routes._cut_short(["--summary=--target x"]) == "" and routes._cut_short(["--target-o=x"]) == "--target-o"
    for command in ("torque advisory impact --target-org acme-prod --sobject Contact",
                    "torque session add --workspace . --client acme --summary x --status executed"):
        assert "admin" not in KINDS(command)


def test_the_powershell_call_operator_is_not_a_chain(w):
    assert routes.is_simple('& "C:/Program Files/Torque/torque.exe" guarded counts --workspace . --client acme',
                            "PowerShell")
    assert routes.is_simple("& sf project deploy start -o acme-prod", "PowerShell")
    assert not routes.is_simple("& sf project deploy start -o acme-prod", "Bash")
    for command in ("& torque x ; torque y", "& torque x & torque y", "torque x | & torque y", "& torque x > out.txt",
                    "& { torque x }", "& torque x `\n y", "torque x; & torque y"):
        assert not routes.is_simple(command, "PowerShell"), command


# ---- round 10: what Windows PowerShell hands a native program, a comma array, any here-document
# ---- delimiter, a continued line as one command

LOGS = "torque logs --target-org acme-prod --since"


@pytest.mark.parametrize("command", [
    LOGS + " '2026-01-01T00:00:00Z',--target-org, other --json",       # one comma with a space: the whole list is an array
    LOGS + " x, --target-org, other",
    LOGS + " x ,--target-org ,other",
    LOGS + " x,\n--target-org,\nother",
    LOGS + " 'Birthdate = 2000-01-01\" --target-org other'",            # a double quote inside an argument ends it there
    LOGS + " '2026-01-01T00:00:00Z\n\" --target-org other'",
    LOGS + " \"a`\" --target-org other\"",
    LOGS + " \"a\"\" --target-org other\"",
    LOGS + " 'a\\\" --target-org other'",
    LOGS + " @'\na\" --target-org other\n'@",
    LOGS + " 'a b\\' --target-org other",                               # a backslash before the closing quote swallows words
    "$w = 'a\" --target-org other'; " + LOGS + " \"x $w\"",             # so can the value of a variable in a string
    "$w = 'x'; " + LOGS + " \"$w\"",
    LOGS + " \"a $('b') c\"",
    LOGS + " 'a&b'",                                                    # a .cmd launcher reads these in a word without spaces
    "torque advisory impact --target-org acme-prod --sobject Contact --where 'Birthdate = 2000-01-01\" --target-org other'",
    "sf apex get log --target-org acme-prod --log-id 'a b\" --target-org other'",
])
def test_words_windows_powershell_hands_on_differently_are_not_read_as_written(w, command):
    kinds = set(KINDS(command, "PowerShell"))
    assert kinds & {"admin", "no_org", "unverifiable"}, (command, KINDS(command, "PowerShell"))
    assert run(w, command, tool="PowerShell").action in ("ask", "deny")
    assert run(w, command, "dontAsk", tool="PowerShell").action == "deny"
    assert "${native}" not in " ".join(route.detail for route in classify("PowerShell", {"command": command}))


def test_a_powershell_comma_array_does_not_hide_an_owner_command(w):
    for command in ("torque workspace ai-access,full, --path .", "torque workspace, ai-access, full --path .",
                    "torque approval, grant req-000000000001"):
        assert "admin" in KINDS(command, "PowerShell"), (command, KINDS(command, "PowerShell"))
        assert run(w, command, tool="PowerShell").action == "deny"


def test_plain_powershell_arguments_are_read_as_written(w):
    for command in ("sf sobject describe -s Account -o acme-prod", "sf sobject describe -s 'Account' -o \"acme-prod\"",
                    "sf project retrieve start -m ApexClass:A,ApexClass:B -o acme-prod",
                    "sf project retrieve start -m 'ApexClass:A' -o acme-prod --output-dir 'C:\\my folder\\x'",
                    "sf sobject describe -s Account -o acme-prod --json | ConvertFrom-Json"):
        assert "read" in KINDS(command, "PowerShell") and not {"admin", "no_org"} & set(KINDS(command, "PowerShell"))
    assert KINDS("sf sobject describe -s 'Account' -o acme-prod", "PowerShell") == ["read"]
    assert KINDS("sf project retrieve start -m ApexClass:A,ApexClass:B -o acme-prod", "PowerShell") == ["read"]
    # a variable inside double quotes is one word for Bash, whatever it holds; not for a
    # native program started by Windows PowerShell
    query = "sf data query -q \"SELECT Id FROM Account WHERE Id = '$ID'\" -o acme-prod"
    assert KINDS(query) == ["read"] and set(KINDS(query, "PowerShell")) == {"read", "unverifiable"}
    note = 'torque session add --workspace . --client acme --summary "$TEXT" --status executed'
    assert "admin" not in KINDS(note) and "admin" in KINDS(note, "PowerShell")


@pytest.mark.parametrize("delimiter,closing", [
    ("\\!", "!"), ("~", "~"), ("@EOF@", "@EOF@"), ("$x", "$x"), ("'E F'", "E F"), ('E"O"F', "EOF"), ("\\E", "E"),
    ("E-1", "E-1"), ("-E", "E"), (" 'a b'", "a b"), ("E%", "E%"), ("a/b", "a/b"), ("'it''s'", "its"), ("{E}", "{E}"),
])
def test_any_here_document_delimiter_is_read(w, delimiter, closing):
    command = f"cat <<{delimiter}\necho '\n{closing}\n{NESTED_QUERY}\n#'"
    assert ("read", "acme-prod", "records") in B(command), (command, B(command))
    assert run(w, command, "dontAsk").action == "deny"


def test_a_here_document_delimiter_is_the_word_with_its_quotes_removed():
    read = routes._heredoc
    for text, expected in (("<<E", ("E", False)), ("<<-E", ("E", True)), ("<< 'E F' x", ("E F", False)),
                           ('<<"E"', ("E", False)), ("<<\\!", ("!", False)), ('<<E"O"F;', ("EOF", False)),
                           ("<<@E@ x", ("@E@", False)), ("<<$x", ("$x", False)), ("<<\n", None), ("<< ;", None)):
        assert read(text, 0) == expected, text


def test_under_a_here_document_each_line_is_also_read_on_its_own(w):
    # a delimiter that can never close its body (it holds a line end): Bash warns and reads on
    command = "cat <<'a\nb'\necho '\n" + NESTED_QUERY + "\n#'"
    assert ("read", "acme-prod", "records") in B(command), B(command)


@pytest.mark.parametrize("command", [
    "torque logs --target-org '' other", 'torque logs --target-org "" other', "torque logs --target-org= --since x",
    "torque advisory impact --sobject Contact --target-org ''", "torque deploy start --target-org '' acme-prod",
    "torque revert preview --target-org '' acme-prod", "sf data query -q x -o ''", "sf data query -q x --target-org=",
])
def test_an_org_option_with_an_empty_value_names_no_org(w, command):
    # Bash hands the empty word on; Windows PowerShell drops it, so the next word would be the org
    for tool in ("Bash", "PowerShell"):
        assert "no_org" in KINDS(command, tool), (tool, command, KINDS(command, tool))
        assert run(w, command, tool=tool).action == "deny"
    convert = routes._powershell_escapes
    assert convert("a '' b \"\" c ''x d'' ('')") == "a  b  c '' x d'' ()"
    assert ("read", "other", "debug_logs") in B_PS("torque logs --target-org '' other")


@pytest.mark.parametrize("key", ["1", "-1", "0x1", "1.5", "1e2", "[int]1", "[int] 1", "'a b'", "$k", "a.b", "\"x\""])
def test_a_powershell_hash_literal_value_is_read_whatever_its_key(w, key):
    command = f"echo @{{{key}={PS_QUERY}}}"
    assert ("read", "acme-prod", "records") in B_PS(command), (command, B_PS(command))
    assert run(w, command, "dontAsk", tool="PowerShell").action == "deny"
    spaced = f"Write-Output @{{ a = 1; {key} = {PS_QUERY} }}"
    assert ("read", "acme-prod", "records") in B_PS(spaced), (spaced, B_PS(spaced))
    assert "admin" in KINDS(f"echo @{{{key}=torque workspace ai-access full --path .}}", "PowerShell")


@pytest.mark.parametrize("command", [
    # a string inside a subexpression inside a string: the inner quote opens a string, it ends nothing
    f"echo \"$( \"<#\" ; echo ok )\" ; echo @{{(1+1)={PS_QUERY}}}",
    f"echo \"$( \"a\" )\" <# x #> ; {PS_QUERY}",
    f"echo \"a $( \"b\" + (1) ) c\" ; {PS_QUERY} # \"",
    # after --%, a pipe inside double quotes does not end what is passed on
    f"echo --% \" | echo ok #\" ; 1 | % {{{PS_QUERY}}}",
    f"echo --% \" | echo ok #\" ; 1 | sort {{ {PS_QUERY} }}",
    f"echo --% \"a | b\" c | {PS_QUERY}",
    f"echo --% \" | {PS_QUERY} #\"",                   # PowerShell runs nothing here; the cautious reading stays
])
def test_powershell_nesting_found_by_running_every_pair_of_forms(w, command):
    assert ("read", "acme-prod", "records") in B_PS(command), (command, B_PS(command))
    assert run(w, command, "dontAsk", tool="PowerShell").action == "deny"


def test_the_powershell_rewriting_follows_a_subexpression_in_a_string():
    convert = routes._powershell_escapes
    assert convert('echo "$( "x y" ; a )" ; b <# c #> d') == 'echo "$( "x y" ; a )"${native} ; b   d'
    assert convert('echo "a $( (1) + "x)" ) b" ; c') == 'echo "a $( (1) + "x)" ) b"${native} ; c'
    # what follows --% runs to a pipe outside double quotes; the text after the first pipe is read once more
    assert convert('echo --% " | a #" ; 1 | b') == "echo --% '\" | a #\" ; 1' | b\n a #\" ; 1 | b"
    assert convert("echo --% a | b") == "echo --% 'a' | b"


def test_an_equals_sign_outside_a_hash_literal_is_left_alone(w):
    convert = routes._powershell_escapes
    assert convert("echo @{1=sf a; b = 2} c=d", braces=True) == "echo @ ; 1 ; sf a; 'b'  ;  2 ;  c=d"
    assert convert("echo @{a = sf x --target-org=dev}", braces=True) == "echo @ ;  'a'  ;  sf x --target-org=dev ; "
    assert convert("if ($a) { sf x -v Name=A } ; @{ k = { sf y -v N=B } }", braces=True) == \
        "if ($a)  ;  sf x -v Name=A  ;  ; @ ;  'k'  ;   ;  sf y -v N=B  ;   ; "
    for command in ("sf sobject describe -s Account --target-org=acme-prod",
                    "if ($true) { sf sobject describe -s Account --target-org=acme-prod }"):
        assert "read" in KINDS(command, "PowerShell") and "no_org" not in KINDS(command, "PowerShell"), command


def test_a_cmd_launcher_reads_an_sf_argument_again(w, monkeypatch):
    read = "sf apex get log --target-org acme-prod --log-id "
    monkeypatch.setattr(routes, "_WINDOWS", True)
    monkeypatch.setenv("TQ_SET", "x --target-org other")
    monkeypatch.delenv("TQ_UNSET", raising=False)
    for tool in ("Bash", "PowerShell"):
        # %NAME% as a word of its own, or in a word without spaces: its value's spaces split it
        for value in ("%TQ_SET%", "%TQ_UNSET%", "'%TQ_UNSET%'", "a%TQ_UNSET%b", "'a&b'", "'a|b'", "'a^b'", "'a>b'",
                      "'a\"b'", "'a b\" --target-org other'", "\"like '%TQ_SET%'\"", "\"%tq_set% x\""):
            kinds = KINDS(read + value, tool)
            assert "read" in kinds and "unverifiable" in kinds, (tool, value, kinds)
            assert run(w, read + value, "dontAsk", tool=tool).action == "deny"
        # inside a longer quoted argument a name that is not set stays as it is written
        for value in ("\"like '%TQ_UNSET%'\"", "'50% off'", "'a b&c'", "x"):
            assert KINDS(read + value, tool) == ["read"], (tool, value, KINDS(read + value, tool))
    # a write keeps its one route, and Torque's own launcher is a program, not a .cmd
    assert KINDS("sf project deploy start -o acme-prod --source-dir %TQ_SET%") == ["org_write"]
    assert KINDS("torque logs --target-org acme-prod --since %TQ_SET%") == ["read"]
    monkeypatch.setattr(routes, "_WINDOWS", False)
    assert KINDS(read + "%TQ_SET%") == ["read"]


@pytest.mark.parametrize("command", [
    f"echo @{{1=$1={PS_QUERY}}}",                                  # a variable named by a number
    f"echo @{{1=$PSVersionTable.'PSVersion' = {PS_QUERY}}}",       # a quoted member
    f"echo @{{1=$PSVersionTable['a]b'] = {PS_QUERY}}}",            # an index that holds a bracket
    f"echo @{{1=$a=$b=$c=$d=$e=$f=$g={PS_QUERY}}}",                # as many assignments as one likes
    f"$a=$b=$c=$d=$e=$f=$g=$h=$i=$j={PS_QUERY}",
    f"$1 = {PS_QUERY}", f"$x.'y z' = {PS_QUERY}", f"$x['a]b'] = {PS_QUERY}", f"$x[\"k\"].y = {PS_QUERY}",
    f"[string[]]$x = {PS_QUERY}", f"$global:x += {PS_QUERY}", f"$x, $y['a,b'] = {PS_QUERY}",
    f"echo @{{a = 1; b = $x.'q' = {PS_QUERY}}}", f"if ($r = {PS_QUERY}) {{ }}",
])
def test_a_powershell_assignment_is_read_whatever_its_target_looks_like(w, command):
    assert ("read", "acme-prod", "records") in B_PS(command), (command, B_PS(command))
    assert run(w, command, "dontAsk", tool="PowerShell").action == "deny"
    owner = command.replace(PS_QUERY, "torque workspace ai-access full --path .")
    assert "admin" in KINDS(owner, "PowerShell"), (owner, KINDS(owner, "PowerShell"))


@pytest.mark.parametrize("glued", [
    '"x"--target-org other', "'x'--target-org other", '"x"-o other', '"a b"--target-org other',
    "'x'\"\"--target-org other", "'x''y'--target-org other", '"x"y"z" --target-org other',
])
def test_a_powershell_word_that_begins_with_a_quoted_string_ends_at_its_quote(w, glued):
    # `"x"--target-org other` is three words for PowerShell (a function and a program both get them);
    # Bash would read one
    for command in (LOGS + " " + glued, "sf apex get log --target-org acme-prod --log-id " + glued):
        assert "no_org" in KINDS(command, "PowerShell"), (command, KINDS(command, "PowerShell"))
        assert run(w, command, tool="PowerShell").action == "deny"
    convert = routes._powershell_escapes
    assert convert('a "x"y \'x\'y "x"y"z" x"y"z --o="x"y "x" y "x";z') == \
        'a "x" y \'x\' y "x" y"z" x"y"z --o="x"y "x" y "x";z'
    assert convert("a 'x'\"y\" 'x''y'z \"x\",y (\"x\")y") == "a 'x' \"y\" 'x'\\''y' z \"x\",y (\"x\")y"
    assert convert('echo "x"#c') == 'echo "x" #c'                      # and what follows can be a comment
    # one word that only holds a quoted string stays one word, and a plain read
    assert KINDS("sf apex get log --target-org acme-prod --log-id x\"y\"z", "PowerShell") == ["read"]
    assert KINDS("sf apex get log --target-org acme-prod --log-id \"x\"", "PowerShell") == ["read"]
    assert "admin" in KINDS('torque workspace "ai-access"full --path .', "PowerShell")
    assert "admin" in KINDS('torque "workspace"ai-access full --path .', "PowerShell")


def test_any_powershell_variable_in_a_quoted_argument_is_a_value_not_on_the_line(w):
    # `$1` is a variable like any other: its value can hold the quote that ends the argument
    for name in ("$1", "$x", "$_", "$?", "$^", "$$", "${a b}", "$env:X", "$($x)"):
        query = f"sf data query -o acme-prod -q \"{name}\""
        assert set(KINDS(query, "PowerShell")) == {"read", "unverifiable"}, (query, KINDS(query, "PowerShell"))
        owner = f"torque approval permissions --workspace . --hook-python \"{name}\""
        assert "admin" in KINDS(owner, "PowerShell"), (owner, KINDS(owner, "PowerShell"))
    staged = ("echo @{1=$1='SELECT Email FROM Contact\" --target-org other'}; "
              "sf data query -o acme-prod -q \"$1\"")
    assert "unverifiable" in KINDS(staged, "PowerShell") and run(w, staged, "dontAsk", tool="PowerShell").action == "deny"
    assert KINDS("sf data query -o acme-prod -q \"costs $ 5\"", "PowerShell") == ["read"]     # a `$` that begins nothing


def test_exec_with_a_process_name_runs_the_command_after_it(w):
    command = 'exec -a echo sf data query -q "SELECT Email FROM Contact" -o acme-prod'
    assert ("read", "acme-prod", "records") in B(command), B(command)
    assert run(w, command, "dontAsk").action == "deny"
    assert ("read", "acme-prod", "records") in B('exec -cla x sf data query -q "SELECT Email FROM Contact" -o acme-prod')
    assert "admin" in KINDS("exec -a echo torque workspace ai-access full --path .")
    assert B("exec -c sf sobject describe -s Account -o acme-prod") == [("read", "acme-prod", None)]


def test_the_second_net_runs_on_any_powershell_line_that_is_not_plain(w):
    named = "sf data query -q x -o acme-prod"
    # a statement form no reading knows (made up here): the text still names a command, so it is asked about
    for command in (f"echo zz=[{named}", f"echo (zz {named}", f"echo $zz {named}", f"echo zz, {named}",
                    f"echo zz | zz {named}", f"echo @zz {named}"):
        kinds = KINDS(command, "PowerShell")
        assert {"read", "unverifiable", "no_org", "admin"} & set(kinds), (command, kinds)
        assert run(w, command, "dontAsk", tool="PowerShell").action == "deny"
    # plain words and plain quotes stay as they were
    assert KINDS(f'echo "{named}"', "PowerShell") == ["local"]
    assert set(KINDS(f"echo zz ; echo '{named}'", "PowerShell")) == {"local"}
    assert routes._powershell_statements("$a=$b=$c=$d=$e=$f=$g=$h=sf x") == "sf x"


def test_a_string_the_shell_may_translate_is_a_word_built_at_run_time(w):
    # Bash's $"..." is looked up in a message catalogue: the text on the line may not be the text that runs
    command = "sf $\"sobject\" $\"describe\" -q 'SELECT Email FROM Contact' -o acme-prod"
    assert ("read", "acme-prod", None) not in B(command) or "unverifiable" in KINDS(command), B(command)
    assert run(w, command, "dontAsk").action == "deny"
    assert KINDS("torque $\"workspace\" ai-access full --path .") == ["admin"]
    plain = "sf sobject describe -s Account -o acme-prod"
    assert B(plain) == [("read", "acme-prod", None)]


def test_a_continued_line_is_one_command():
    simple = routes.is_simple
    assert simple("torque guarded counts --workspace . --client acme --target-org A \\\n    --object Opportunity")
    assert simple("torque guarded counts --workspace . --client acme\n") and simple("  torque x --where 'a\nb'  ")
    for command in ("torque x \\\r\n --y", "torque x\ntorque y", "torque x # c \\\ntorque y", "cd elsewhere # \\\ntorque guarded counts",
                    "torque x <<E\nbody\nE", "torque x ; torque y", "torque x \\\n ; torque y"):
        assert not simple(command), command


# ---- round 13: backslashes between backticks, an escaped dollar in PowerShell, what braces hold under a
# ---- local command, an assignment inside the line, a value the shell fills in for an option the gate decides by

BT_QUERY = "sf data query -q 'SELECT Email FROM Contact' -o acme-prod"


@pytest.mark.parametrize("command", [
    'echo `echo \\\\"x; ' + BT_QUERY + ' #\\\\"`',                  # \\" becomes \", a quote that opens nothing
    "echo `echo \\\\'x; " + BT_QUERY + " #\\\\'`",
    'echo "`echo \\\\"x; ' + BT_QUERY + ' #\\\\"`"',
    'echo `echo \\`echo \\\\\\\\"x; ' + BT_QUERY + ' #\\\\\\\\"\\``',     # one backtick pair inside another
    'x=`echo \\\\"x; ' + BT_QUERY + ' #\\\\"`',
    "echo `echo \\$(" + BT_QUERY + ")`",
])
def test_bash_takes_a_backslash_off_between_backticks_before_it_reads_the_command(w, command):
    assert ("read", "acme-prod", "records") in B(command), (command, B(command))
    assert run(w, command, "dontAsk").action == "deny"
    owner = command.replace(BT_QUERY, "torque workspace ai-access full --path .")
    assert "admin" in KINDS(owner), (owner, KINDS(owner))


def test_the_readings_of_a_backtick_substitution():
    read = routes._backtick_readings
    assert read("echo a") == ["echo a"]
    assert read('echo \\\\"x; y #\\\\"') == ['echo \\\\"x; y #\\\\"', 'echo \\"x; y #\\"']
    assert read('echo \\"x\\" \\$a \\`b\\`') == ['echo \\"x\\" \\$a \\`b\\`', 'echo \\"x\\" $a `b`', 'echo "x" $a `b`']
    # `$(...)` takes nothing off, and a plain backtick substitution is read as before
    assert B('echo $(echo \\\\"x; ' + BT_QUERY + ' #\\\\")') == [("local", None, None), ("local", None, None)]
    assert B("echo `hostname`") == [("local", None, None), ("local", None, None)]


def test_an_escaped_dollar_in_a_powershell_string_is_plain_text(w):
    summary = 'torque session add --workspace . --client acme --summary "Paid `$5" --status executed'
    assert KINDS(summary, "PowerShell") == ["local"], KINDS(summary, "PowerShell")
    assert KINDS("echo `$5 ; echo \"a `$b\"", "PowerShell") == ["local", "local"]
    # a dollar that is not escaped still is a variable, and an escaped backtick before one escapes nothing
    assert "admin" in KINDS(summary.replace("`$5", "$5"), "PowerShell")
    assert "admin" in KINDS(summary.replace("`$5", "``$5"), "PowerShell")
    assert "unverifiable" in KINDS('sf data query -o acme-prod -q "a ``$x"', "PowerShell")
    # and the parentheses after an escaped dollar are still code outside a string
    hidden = 'echo `$(sf data query -q "SELECT Email FROM Contact" -o acme-prod)'
    assert ("read", "acme-prod", "records") in B_PS(hidden), B_PS(hidden)


@pytest.mark.parametrize("command", [
    "echo @{1=python x.py}", "echo @{1=x.cmd}", "echo @{1=./x.ps1}", "echo @{1=. './x.ps1'}",
    "echo @{a='b'; c=node x.js}", "echo @{a=1\n b=npx thing}", "echo @{1=@{2=python x.py}}",
    "echo 1 | select @{n='x';e={x.cmd}}", "echo 1 | select @{n='x';e={x.cmd echo }}", "echo @{1=7z a b}",
    "echo @{1=Set-Item env:TQ_X q}", "echo @{1=return python x.py}", "ls @{1=Get-Content x}",
])
def test_a_command_inside_powershell_braces_is_asked_about_as_on_a_line_of_its_own(w, command):
    # PowerShell runs the value of a hash literal's entry before the command that gets the literal starts
    assert "unverifiable" in KINDS(command, "PowerShell"), (command, KINDS(command, "PowerShell"))
    assert run(w, command, "dontAsk", tool="PowerShell").action == "deny"
    # beside a write it is one more statement than the approved command
    write = command + "; sf data update record -o acme-prod -s Account -i 001000000000001AAA -v \"Name=x\""
    assert "unverifiable" in KINDS(write, "PowerShell")


@pytest.mark.parametrize("command", [
    "echo @{name='text'; count=5; on=$true; kind=[int]1; ratio=1.5; size=10mb; neg=-1}", "echo @{n='x'}",
    "echo 1 | select @{n='x';e={$_.Name}}", "echo @{1=hostname}", "echo @{1=echo hi}", "echo @{ name = 'a b' }",
    "echo @{a=@{b='c'}}", "echo @{a=1\n b='x'}", "echo @{a=$x.y; b=\"z\"}", "echo @{a=.5; b=0x1F; c=1e3}",
])
def test_text_numbers_and_variables_inside_powershell_braces_stay_local(command):
    assert set(KINDS(command, "PowerShell")) == {"local"}, (command, KINDS(command, "PowerShell"))


def test_a_bare_hash_key_is_written_as_text():
    convert = routes._powershell_escapes
    assert convert("echo @{name=1; b_2 = x}", True, True) == "echo @ ;  'name'  ; 1; 'b_2'  ;  x ; "
    assert convert("echo @{1=a; 'k'=b; $k=c}", True, True) == "echo @ ; 1 ; a; 'k'  ; b; $k ; c ; "
    assert convert("echo @{name=1}") == "echo @{name=1}"                 # only where braces end statements
    seen: list = []
    convert("echo @{1=$env:TQ_X='q'}; $a = 1; [int]$b = 2; echo c=d", True, True, seen)
    assert len(seen) == 3 and seen[0].endswith("$env:TQ_X")


@pytest.mark.parametrize("setter", [
    "echo @{1=$env:TQ_X='q'}", "echo @{a=1; b=${env:TQ_X}='q'}", "echo @{1=$function:sf={ other }}",
    "echo @{1=$alias:sf='other'}", "echo 1 | select @{n='x';e={$env:TQ_X='q'}}", "echo @{1=[int]$x=1}",
    "echo @{1=$x+='q'}", "echo @{a=@{b=$env:TQ_X='q'}}",
])
def test_an_assignment_inside_a_powershell_line_is_asked_about(w, monkeypatch, setter):
    # set this way, a variable reaches the commands after it: a .cmd launcher fills in %NAME% from it, and
    # `$function:sf` or `$alias:sf` replaces the command itself
    monkeypatch.delenv("TQ_X", raising=False)
    command = setter + '; sf sobject describe -o acme-prod -s "Contact%TQ_X% "'
    kinds = KINDS(command, "PowerShell")
    assert "unverifiable" in kinds and "read" in kinds, (command, kinds)
    assert run(w, command, "dontAsk", tool="PowerShell").action == "deny"
    assert KINDS('sf sobject describe -o acme-prod -s "Contact%TQ_X% "', "PowerShell") == ["read"]
    # an equals sign that assigns nothing is left alone
    assert KINDS("sf data create record -o acme-prod -s Account -v Name=x --json=true", "PowerShell") == ["org_write"]
    assert KINDS("echo a=b ; echo 'c = d'", "PowerShell") == ["local", "local"]


@pytest.mark.parametrize("command", [
    "cp env:SystemRoot env:TQ_X", "cp function:prompt function:sf", "cd alias:; cp iex echo", "rm env:HOME",
    "mv -Path:env:A env:B", "cp Environment::A Environment::B", "cp 'env:A' \"env:B\"", "cd Env:\\",
    "cp Microsoft.PowerShell.Core\\Environment::A x", "ls variable:",
])
def test_a_powershell_drive_of_variables_functions_or_aliases_is_asked_about(w, command):
    # commands the gate takes for local change what these hold: a copy sets a variable or replaces a command
    assert "unverifiable" in KINDS(command, "PowerShell"), (command, KINDS(command, "PowerShell"))
    query = command + '; sf sobject describe -o acme-prod -s Contact'
    assert run(w, query, "dontAsk", tool="PowerShell").action == "deny"


def test_reading_a_variable_or_writing_about_a_drive_is_not_naming_one():
    for command in ("echo $env:PATH", "echo \"$env:TEMP\\x\"", "cd C:\\Projects", "echo 'see function: x'",
                    'torque session add --workspace . --client acme --summary "env: updated the alias: list"',
                    "sf data query -o acme-prod -q \"SELECT Id FROM Account WHERE Name = 'alias:x'\""):
        assert "unverifiable" not in KINDS(command, "PowerShell"), (command, KINDS(command, "PowerShell"))
    assert routes._powershell_drive("cp env:A env:B") == "env:A" and routes._powershell_drive("echo $env:A") == ""


@pytest.mark.parametrize("command", [
    'torque context --workspace . --client "$acme"', 'torque context --workspace . --client="$acme"',
    'torque logs --client $acme --target-org acme-prod --since 1h', 'torque logs --target-org acme-* --since 1h',
    'torque context --workspace . --initiative "$x"', 'torque context --workspace . --initiative="$x"',
    'torque context --workspace . --client "ac$me"', 'torque context --workspace . --client "`echo acme`"',
    'torque logs --target-org "$ORG" --since 1h', 'torque logs --org="$ORG" --since 1h',
])
def test_a_value_the_shell_fills_in_for_an_option_the_gate_decides_by_is_refused(w, command):
    # a session bound to acme would read `--client "$acme"` as acme and run it for whatever the variable holds
    assert "admin" in KINDS(command) and "local" not in KINDS(command), (command, KINDS(command))
    assert run(w, command).action == "deny"


def test_values_written_out_and_filled_values_of_other_options_are_as_before(w):
    assert KINDS("torque context --workspace . --client acme") == ["local"]
    assert KINDS("torque context --workspace . --client '$acme'") == ["local"]       # single quotes: plain text
    assert KINDS('torque session add --workspace . --client acme --summary "$TEXT" --status executed') == ["local"]
    assert run(w, 'torque session add --workspace . --client acme --summary "$TEXT" --status executed').action == "allow"


# ---- round 14: the line and paragraph separators in PowerShell, a method call wherever it stands

@pytest.mark.parametrize("separator", ["\u2028", "\u2029"])
def test_the_line_and_paragraph_separators_separate_words_for_powershell(w, separator):
    logs = f"torque logs{separator}--target-org acme-prod"
    assert ("read", "acme-prod", "debug_logs") in B_PS(logs), B_PS(logs)
    assert run(w, logs, tool="PowerShell").action == "deny"             # this consent has no debug_logs class
    assert "admin" in KINDS(f"torque workspace{separator}ai-access full --path .", "PowerShell")
    query = f"sf data query{separator}-q \"SELECT Email FROM Contact\"{separator}-o acme-prod"
    assert ("read", "acme-prod", "records") in B_PS(query), B_PS(query)
    assert routes._powershell_texts(f"a{separator}b")[0] == "a b"
    # Bash takes neither for a space: there the word is one word
    assert KINDS(f"echo a{separator}b") == ["local"]


@pytest.mark.parametrize("command", [
    "echo @{1=$sb.Invoke()}", "echo @{1=$ExecutionContext.InvokeCommand.InvokeScript('hostname')}",
    "echo @{1=$ExecutionContext.InvokeCommand.InvokeScript((cat x.txt))}",
    "echo $ExecutionContext.InvokeCommand.InvokeScript((cat x.txt))", "echo $sb.Invoke()",
    "echo @{1=[scriptblock]::Create((cat x.txt)).Invoke()}", "echo 1 | select @{n='x';e={$_.Run()}}",
    "echo @{1=$x.'Invoke'()}", "echo @{1=$x.$name()}", "echo @{1=$x.('In'+'voke')()}", "echo @{1=(cat x.txt).Invoke()}",
    "echo @{1='text'.Invoke()}", "echo @{a=1; b=$h['k v'].Invoke()}", "ls | select @{n='x';e={[IO.File]::ReadAllText('x')}}",
])
def test_a_powershell_method_call_is_asked_about_wherever_it_stands(w, command):
    # on a line of its own `$sb.Invoke()` is asked about; inside braces, or as an argument, it runs the same
    assert "unverifiable" in KINDS(command, "PowerShell"), (command, KINDS(command, "PowerShell"))
    assert run(w, command, "dontAsk", tool="PowerShell").action == "deny"


def test_reading_a_property_is_not_a_method_call():
    for command in ("echo @{a=$x.Name; b=$y.Count}", "echo $x.Name", "echo 1 | select @{n='x';e={$_.Name}}",
                    "echo 'see foo.bar() for details'", "echo [math]::Pi", "echo $env:USERPROFILE.Length",
                    "echo 'a.b' ; echo c"):
        assert "unverifiable" not in KINDS(command, "PowerShell"), (command, KINDS(command, "PowerShell"))
    found = routes._powershell_hidden("echo $x.M() ; echo $y.Name ; echo (hostname)", 0)
    assert [route.detail for route in found] == ["$x.M(...) (a method call: the gate cannot tell what it runs)"]


# ---- round 15: a variable set for the commands after it without `export`, a name made to run another
# ---- program, a number that is a word and not a file descriptor, a method named by a variable

R15_QUERY = 'sf data query -q "SELECT Email FROM Contact" -o acme-prod'


@pytest.mark.parametrize("setter", [
    "HOME=/other; ", "PATH=/other:$PATH; ", "PATH=/other:$PATH\n", "HOME=/other && ", "read HOME <<< /other; ",
    "read -r HOME <<< /other; ", "printf -v HOME /other; ", "declare HOME=/other; ", "typeset HOME=/other; ",
    "unset HOME; ", "let PATH=1; ", "builtin read HOME <<< /other; ", "command declare HOME=/other; ",
    "x=1 HOME=/other; ", "NODE_OPTIONS=--require=./x.js; ", "SF_STATE_FOLDER=/other; ", "HTTPS_PROXY=http://proxy.example.org:8080; ",
    "set -k; ", "set -o keyword; ", "TQ_R15_SET=/other; ", "IFS=,; ", 'n=PATH; printf -v "$n" /other; ',
    "declare -n ref=PATH; ref=/other; ", 'read "$n" <<< /other; ', "printf -v $n /other; ",
])
def test_a_variable_the_next_commands_read_is_asked_about_without_export(w, monkeypatch, setter):
    # an assignment to a variable that is exported already changes the environment: `PATH=/x:$PATH; sf ...`
    # runs another sf, and `HOME=/x; sf ...` reads other aliases (checked in real Bash)
    monkeypatch.setenv("TQ_R15_SET", "here")
    command = setter + R15_QUERY
    kinds = KINDS(command)
    assert "read" in kinds and "unverifiable" in kinds, (command, kinds)
    assert run(w, command, "dontAsk").action == "deny"
    write = setter + UPDATE + ' "Name=x"'
    assert "org_write" not in KINDS(write) and "unverifiable" in KINDS(write), (write, KINDS(write))


def test_a_loop_variable_and_an_ordinary_variable_are_as_before(w, monkeypatch):
    monkeypatch.delenv("OUTPUT", raising=False)
    monkeypatch.delenv("o", raising=False)
    assert "unverifiable" in KINDS("for HOME in /other; do " + R15_QUERY + "; done")
    assert "unverifiable" in KINDS("select PATH in /other; do " + R15_QUERY + "; done")
    for command in ("x=5; " + R15_QUERY, "OUTPUT=out.json; " + R15_QUERY, "echo PATH is set; " + R15_QUERY,
                    "printf '%s\\n' hello; " + R15_QUERY, "read -r line < notes.txt; " + R15_QUERY,
                    "for o in Account Contact; do sf sobject describe -s \"$o\" -o acme-prod; done"):
        assert "unverifiable" not in KINDS(command), (command, KINDS(command))
    names = routes._assigned_names
    assert names(["HOME=/x", "A=1"]) == ["HOME", "A"] and names(["HOME=/x", "sf", "org"]) == []
    assert names(["read", "-r", "HOME"]) == ["HOME"] and names(["printf", "-v", "HOME", "PATH"]) == ["HOME"]
    assert names(["read", "-p", "PATH", "-r", "line"]) == ["line"] and names(["printf", "%s", "PATH"]) == []
    assert names(["for", "PATH", "in", "a"]) == ["PATH"] and names(["echo", "HOME=/x"]) == []
    assert names(["declare", "-i", "PATH+=1", "ARR[0]=5"]) == ["PATH", "$"]     # brackets: the shell may build it
    assert names(["declare", "-n", "ref=PATH"]) == ["ref", "PATH"]       # a name reference: ref is PATH from here on


@pytest.mark.parametrize("command", [
    "PS4=\\$\\(python\\ x.py\\); set -x; true", "PS4='$'\"(python x.py)\"; set -x; true", "export PS4=x; true",
    "read PS4 <<< x; true", "PROMPT_COMMAND='python x.py'; true", "BASH_ENV=./x.sh; true", "declare ENV=./x.sh",
    "hash -p ./x.sh sf; " + R15_QUERY, "builtin hash -p ./x.sh sf", "hash -dp ./x.sh sf",
    "wget --use-askpass=./x.sh http://example.org/", "wget -e use_askpass=./x.sh http://example.org/",
    "wget --execute use_askpass=./x.sh http://example.org/", "wget -qe use_askpass=./x.sh http://example.org/",
])
def test_what_makes_bash_or_a_local_program_run_another_program_is_asked_about(w, command):
    assert "unverifiable" in KINDS(command), (command, KINDS(command))
    assert run(w, command, "dontAsk").action == "deny"


def test_ordinary_uses_of_the_same_commands_stay_local():
    for command in ("hash -r", "hash", "hash sf", "wget http://example.org/x.zip", "wget -q -O x.zip http://example.org/x",
                    "set -x; true", "set -e; true", "PS5=x; true"):
        assert set(KINDS(command)) == {"local"}, (command, KINDS(command))


def test_a_number_is_a_file_descriptor_only_directly_before_a_redirection(w):
    # `--target-org 123 >&2` names the org 123; `2>&1` is a file descriptor and no word of the command
    assert B("torque logs --target-org 123 >&2") == [("read", "123", "debug_logs")]
    assert B("torque logs --target-org 5 >&2 acme-prod") == [("read", "5", "debug_logs")]
    assert run(w, "torque logs --target-org 5 >&2 acme-prod").action == "deny"
    assert B("sf sobject describe -s Account -o 123 > out.json") == [("read", "123", None)]
    for tail in (" 2>&1", " 2>/dev/null", " 2>> err.txt", " 1>out.json 2>&1", " 2>&1 | cat", " 10>x", " 0</dev/null"):
        found = B(R15_QUERY + tail)
        assert ("read", "acme-prod", "records") in found and "no_org" not in [kind for kind, _, _ in found], (tail, found)
    assert B(R15_QUERY + " 2>&1")[0] == ("read", "acme-prod", "records")
    assert routes._mask("a 2>&1 b 12 >x 3<y '4>z' 5") == routes._mask("a >&1 b 12 >x <y '4>z' 5")
    assert not routes.is_simple("torque x 2>&1") and routes.is_simple("torque x --since 2")


@pytest.mark.parametrize("command", [
    "echo $x.$env:NAME()", "echo @{1=$x.${name}()}", "echo 'abc'.$env:NAME()", "echo $x.$script:name()",
    "echo $x::$name()", "echo $x.$y.$z()", "echo @{1=[type]::$env:NAME()}",
])
def test_a_powershell_method_named_by_a_variable_is_a_method_call(w, command):
    assert "unverifiable" in KINDS(command, "PowerShell"), (command, KINDS(command, "PowerShell"))
    assert run(w, command, "dontAsk", tool="PowerShell").action == "deny"
