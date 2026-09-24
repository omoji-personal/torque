import pytest

from torque.connected_routes import classify, is_simple

K = lambda tool, inp: [(r.kind, r.org) for r in classify(tool, inp)]
B = lambda cmd: K("Bash", {"command": cmd})


@pytest.mark.parametrize("cmd,expected", [
    ("sf data query -q 'SELECT Id FROM Account' -o acme-prod", [("read", "acme-prod")]),
    ("sf data query -q \"SELECT COUNT() FROM Account WHERE Name IN ('a;b')\" -o acme-prod", [("read", "acme-prod")]),
    ("sf project retrieve start -m Flow:X --target-org acme-sbx", [("read", "acme-sbx")]),
    ("sf project retrieve start -m Flow:X --target-org=acme-sbx", [("read", "acme-sbx")]),
    ("sf project deploy start -m Flow:X -o acme-prod", [("org_write", "acme-prod")]),
    ("sf project deploy start -m Flow:X -o acme-prod --dry-run", [("check_only", "acme-prod")]),
    ("sf project deploy validate -m Flow:X -o acme-prod", [("check_only", "acme-prod")]),
    ("sf apex run test -o acme-prod", [("check_only", "acme-prod")]),
    ("sf data update record -s Account -i 001x -v Name=A -o acme-prod", [("org_write", "acme-prod")]),
    ("sf apex run -f x.apex -o acme-sbx", [("org_write", "acme-sbx")]),
    ("sf apex tail log -o acme-sbx", [("org_write", "acme-sbx")]),
    ("sf org display --verbose -o acme-sbx", [("org_write", "acme-sbx")]),
    ("sf org auth show-access-token -o acme-sbx", [("org_write", "acme-sbx")]),
    ("sf some future-topic do-thing -o acme-prod", [("org_write", "acme-prod")]),
    ("sf api request rest /services/data -o acme-prod", [("read", "acme-prod")]),
    ("sf api request rest /services/data -X PATCH -o acme-prod", [("org_write", "acme-prod")]),
    ("sf api request graphql --body q.graphql -o acme-prod", [("org_write", "acme-prod")]),
    ("sf api request rest /services/apexrest/Refresh -o acme-prod", [("org_write", "acme-prod")]),
    ("sf api request rest --file req.json -o acme-prod", [("org_write", "acme-prod")]),
    ("torque approval request --org acme-prod --capture-before-record Account:001x --change c -- sf x",
     [("read", "acme-prod")]),
    ("torque approval request --org acme-prod --capture-before-metadata Flow:X --change c -- sf x",
     [("read", "acme-prod")]),
    ("sf data update record -s Account -i 001x -v Name=A", [("no_org", None)]),
    ("sf data query -q x -o acme-prod -o beta-prod", [("no_org", None)]),
    ("sf project generate --name demo", [("local", None)]),
    ("sf --version", [("local", None)]),
    ("sf", [("local", None)]),
    ("sf org list", [("local", None)]),
    ("sf org display", [("no_org", None)]),
    ("sf data query -q x", [("no_org", None)]),
    ("sf org login web -a new", [("unverifiable", None)]),
    ("sf plugins install something", [("unverifiable", None)]),
    ("sf alias set acme-prod=other@example.com", [("admin", None)]),
    ("sf config set target-org=acme-prod", [("admin", None)]),
    ("sfdx force:source:deploy -p x -u acme-prod", [("org_write", "acme-prod")]),
    ("sfdx force:data:soql:query -q x -u acme-prod", [("read", "acme-prod")]),
    ("torque deploy -o acme-prod --metadata Flow:X --workspace . --client acme", [("org_write", "acme-prod")]),
    ("torque deploy --dry-run -o acme-prod --metadata Flow:X", [("check_only", "acme-prod")]),
    ("torque recover preview snap-1 --workspace . --client acme", [("local", None)]),
    ("torque recover exec snap-1 --org acme-prod --workspace . --client acme", [("org_write", "acme-prod")]),
    ("torque recover --workspace . --client acme exec snap-1 --org acme-prod", [("org_write", "acme-prod")]),
    ("jsc deploy -o acme-prod -m Flow:X", [("org_write", "acme-prod")]),
    ("jsc revert-token grant --reason x", [("admin", None)]),
    ("python3 -m torque data update -o acme-prod", [("org_write", "acme-prod")]),
    ("python -m jsc_revert.cli deploy -o acme-prod", [("org_write", "acme-prod")]),
    ("torque approval grant req-1", [("admin", None)]),
    ("torque approval deny req-1 --reason no", [("admin", None)]),
    ("torque approval permissions --workspace . --write", [("admin", None)]),
    ("torque approval permissions --workspace .", [("local", None)]),
    ("torque approval request --org acme-prod -- sf project deploy start -o acme-prod", [("local", None)]),
    ("torque approval require --org acme-prod -- sf project deploy start -o acme-prod", [("local", None)]),
    ("torque workspace ai-access full", [("admin", None)]),
    ("torque launch --client acme", [("admin", None)]),
    ("torque client consent record --client acme", [("admin", None)]),
    ("torque client consent show --client acme", [("local", None)]),
    ("torque context --workspace . --client acme", [("local", None)]),
    ("torque logs --target-org acme-prod", [("read", "acme-prod")]),
    ("torque browser run -o acme-sbx", [("browser_write", "acme-sbx")]),
    ("python3 tools/fix.py", [("unverifiable", None)]),
    ("python3 -c 'print(1)'", [("unverifiable", None)]),
    ("./deploy.sh", [("unverifiable", None)]),
    ("pytest -q", [("unverifiable", None)]),
    ("npm run build", [("unverifiable", None)]),
    ("cci task run deploy --org prod", [("unverifiable", None)]),
    ("bash -c 'sf apex run -f x -o acme-prod'", [("unverifiable", None), ("org_write", "acme-prod")]),
    ("bash deploy.sh", [("unverifiable", None)]),
    ("echo 'sf apex run -o acme-prod' | bash", [("local", None), ("unverifiable", None)]),
    ("curl -X POST https://acme.my.salesforce.com/services/data", [("unverifiable", None)]),
    ("curl https://example.com", [("local", None)]),
    ("git status", [("local", None)]),
    ("git -c alias.x='!sf apex run -o acme-prod' x", [("unverifiable", None)]),
    ("grep -rn sf src/", [("local", None)]),
    ("echo $(sf apex run -f x -o acme-prod)", [("local", None), ("org_write", "acme-prod")]),
    ("echo \"$(sf apex run -f x -o acme-prod)\"", [("local", None), ("org_write", "acme-prod")]),
    ("echo `sf apex run -f x -o acme-prod`", [("local", None), ("org_write", "acme-prod")]),
    ("echo hi\nsf apex run -f x -o acme-prod", [("local", None), ("org_write", "acme-prod")]),
    ("echo a#b; sf apex run -f x -o acme-prod", [("local", None), ("org_write", "acme-prod")]),
    ("s''f apex run -f x -o acme-prod", [("org_write", "acme-prod")]),
    ("$'\\x73f' apex run -f x -o acme-prod", [("org_write", "acme-prod")]),
    ("X=sf; $X apex run -o acme-prod", [("local", None), ("unverifiable", None)]),
    ("nohup sf apex run -f x -o acme-prod", [("org_write", "acme-prod")]),
    ("time sf data query -q x -o acme-prod", [("read", "acme-prod")]),
    ("nice -n 5 sf apex run -o acme-prod", [("org_write", "acme-prod")]),
    ("timeout 60 sf apex run -o acme-prod", [("org_write", "acme-prod")]),
    ("env sf apex run -o acme-prod", [("org_write", "acme-prod")]),
    ("HOME=/tmp/x sf data query -q x -o acme-prod", [("unverifiable", "acme-prod")]),
    ("env HOME=/tmp/x sf apex run -o acme-prod", [("unverifiable", "acme-prod")]),
    ("xargs sf apex run -o acme-prod", [("unverifiable", None), ("org_write", "acme-prod")]),
    ("script -q /dev/null torque approval grant req-1", [("unverifiable", None), ("admin", None)]),
    ("find . -name x -exec sf apex run -o acme-prod \;", [("unverifiable", None), ("org_write", "acme-prod")]),
    ("awk 'BEGIN{system(\"sf apex run -o acme-prod\")}'", [("unverifiable", None)]),
    ("awk '{print $1}' file", [("local", None)]),
    ("for f in a b; do sf apex run -f $f -o acme-prod; done", [("local", None), ("org_write", "acme-prod")]),
    ("{ sf apex run -o acme-prod; }", [("org_write", "acme-prod")]),
    ("eval 'sf apex run -o acme-prod'", [("unverifiable", None), ("org_write", "acme-prod")]),
    ("osascript -e 'tell app \"Terminal\" to do script \"x\"'", [("unverifiable", None)]),
    ("sf apex run -o acme-prod 2>&1 > out.txt", [("org_write", "acme-prod")]),
])
def test_bash_routes(cmd, expected):
    assert B(cmd) == expected


@pytest.mark.parametrize("cmd", [
    "sf data get record -s Account -i 001x -o acme-prod", "sf data search -q x -o acme-prod",
    "sf data export tree -q x -o acme-prod", "sf data export bulk -q x -o acme-prod",
    "sf sobject describe -s Account -o acme-prod", "sf sobject list -o acme-prod",
    "sf org display -o acme-prod", "sf org list limits -o acme-prod", "sf org list metadata -m Flow -o acme-prod",
    "sf org list metadata-types -o acme-prod", "sf org list users -o acme-prod",
    "sf org list sobject record-counts -s Account -o acme-prod",
    "sf project retrieve preview -o acme-prod", "sf project deploy report --job-id 0Af -o acme-prod",
    "sf project deploy preview -o acme-prod", "sf apex get log -i 07L -o acme-prod",
    "sf apex list log -o acme-prod", "sf apex get test -i 707 -o acme-prod", "sf flow get test -i 707 -o acme-prod",
    "sf logic get test -i 707 -o acme-prod", "sf package installed list -o acme-prod",
    "sf package install report -i 0Hf -o acme-prod", "sf package version list -v acme-prod",
    "sf community list template -o acme-prod",
])
def test_verified_read_commands(cmd):
    assert B(cmd) == [("read", "acme-prod")]


def test_record_and_log_reads_are_marked():
    assert classify("Bash", {"command": "sf data query -q x -o p"})[0].data == "records"
    assert classify("Bash", {"command": "sf apex get log -i 1 -o p"})[0].data == "debug_logs"
    assert classify("Bash", {"command": "sf project retrieve start -m Flow:X -o p"})[0].data is None


def test_client_flag_is_reported():
    assert classify("Bash", {"command": "torque context --workspace . --client=beta"})[0].client == "beta"


def test_mcp_routes():
    assert K("mcp__salesforce__run_soql_query", {"usernameOrAlias": "acme-prod", "query": "x"}) == [("read", "acme-prod")]
    assert K("mcp__salesforce__deploy_metadata", {"usernameOrAlias": "acme-prod"}) == [("org_write", "acme-prod")]
    assert K("mcp__salesforce__mystery_tool", {"usernameOrAlias": "acme-prod"}) == [("org_write", "acme-prod")]
    assert K("mcp__salesforce__run_soql_query", {"query": "x"}) == [("no_org", None)]
    assert K("mcp__claude-in-chrome__computer", {"action": "left_click"}) == [("browser_write", None)]
    assert K("mcp__claude-in-chrome__computer", {"action": "screenshot"}) == [("read", None)]
    assert K("mcp__claude-in-chrome__form_input", {}) == [("browser_write", None)]
    assert K("mcp__claude-in-chrome__javascript_tool", {}) == [("browser_write", None)]
    assert K("mcp__claude-in-chrome__shortcuts_execute", {}) == [("browser_write", None)]
    assert K("mcp__claude-in-chrome__read_page", {}) == [("read", None)]
    assert K("mcp__claude-in-chrome__tabs_create_mcp", {}) == [("read", None)]
    assert K("mcp__chrome-devtools__navigate_page", {"url": "https://x"}) == [("read", None)]
    assert K("mcp__chrome-devtools__evaluate_script", {}) == [("browser_write", None)]
    assert K("mcp__computer-use__screenshot", {}) == [("read", None)]
    assert K("mcp__computer-use__type", {"text": "x"}) == [("admin", None)]
    assert K("mcp__shell__run", {"command": "sf apex run -o acme-prod"}) == [("local", None), ("org_write", "acme-prod")]
    assert K("Read", {"file_path": "/w/x"}) == [("local", None)]


def test_simple_commands():
    assert is_simple("sf project deploy start -m Flow:X -o acme-prod")
    assert is_simple("sf data update record -v \"Name='a b'\" -o acme-prod")
    for cmd in ("a && b", "a; b", "a | b", "echo $(x)", "a > f", "`x`", "a &", "a\nb", "a <(b)", "a 2>&1"):
        assert not is_simple(cmd), cmd
