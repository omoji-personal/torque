"""Build-only mode blocks org access. A browser or desktop control tool acts on a
page or window the gate cannot see, so it is blocked outright, and an MCP server
under a neutral name is blocked when its arguments name a Salesforce host."""
import io
import json
from pathlib import Path
import sys

import pytest

from torque import connected_routes as cr, gate, gate_antigravity as agy

W = Path("/w")

BROWSER_AND_DESKTOP = [
    # Claude in Chrome: navigation, page reads, in-page JavaScript, clicks.
    ("mcp__claude-in-chrome__navigate", {"url": "https://example.com", "tabId": 1}),
    ("mcp__claude-in-chrome__read_page", {"tabId": 1}),
    ("mcp__claude-in-chrome__get_page_text", {"tabId": 1}),
    ("mcp__claude-in-chrome__javascript_tool", {"action": "javascript_exec", "text": "document.title", "tabId": 1}),
    ("mcp__claude-in-chrome__computer", {"action": "screenshot", "tabId": 1}),
    ("mcp__claude-in-chrome__computer", {"action": "left_click", "coordinate": [1, 2], "tabId": 1}),
    ("mcp__claude-in-chrome__form_input", {"ref": "a", "value": "b", "tabId": 1}),
    ("mcp__claude-in-chrome__tabs_context_mcp", {}),
    ("mcp__Claude_in_Chrome__navigate", {"url": "https://example.com"}),
    # Playwright, Puppeteer and devtools style servers.
    ("mcp__playwright__browser_navigate", {"url": "https://example.com"}),
    ("mcp__playwright__browser_snapshot", {}),
    ("mcp__playwright__browser_evaluate", {"function": "() => document.cookie"}),
    ("mcp__puppeteer__puppeteer_navigate", {"url": "https://example.com"}),
    ("mcp__puppeteer__puppeteer_evaluate", {"script": "1"}),
    ("mcp__chrome-devtools__navigate_page", {"url": "https://example.com"}),
    ("mcp__chrome-devtools__evaluate_script", {"function": "() => 1"}),
    ("mcp__browsermcp__browser_click", {"element": "x"}),
    ("mcp__selenium__click_element", {"by": "css", "value": "a"}),
    ("mcp__tools__browser_open", {"url": "https://example.com"}),
    # Desktop control and computer use.
    ("mcp__computer-use__screenshot", {}),
    ("mcp__computer-use__left_click", {"coordinate": [1, 2]}),
    ("mcp__computer-use__type", {"text": "sf org list"}),
    ("mcp__computer_use__key", {"text": "cmd+t"}),
    ("mcp__desktop__computer", {"action": "screenshot"}),
    ("mcp__desktop_commander__start_process", {"command": "ls project"}),
    ("mcp__applescript__run", {"source": "tell application \"Terminal\" to activate"}),
    # Antigravity's browser tools, as gate_antigravity names them.
    ("mcp__antigravity__open_browser_url", {"Url": "https://example.com"}),
    ("mcp__antigravity__read_browser_page", {"PageId": "1"}),
    ("mcp__antigravity__browser_get_dom", {"PageId": "1"}),
    ("mcp__antigravity__browser_click_element", {"PageId": "1", "Selector": "a"}),
    ("mcp__antigravity__browser_input", {"PageId": "1", "Text": "x"}),
    ("mcp__antigravity__browser_press_key", {"PageId": "1", "Key": "Enter"}),
    ("mcp__antigravity__browser_list_network_requests", {"PageId": "1"}),
    ("mcp__antigravity__click_browser_pixel", {"PageId": "1", "X": 1, "Y": 2}),
    ("mcp__antigravity__execute_browser_javascript", {"PageId": "1", "Script": "document.cookie"}),
    ("mcp__antigravity__capture_browser_screenshot", {"PageId": "1"}),
    ("mcp__antigravity__capture_browser_console_logs", {"PageId": "1"}),
    ("mcp__antigravity__list_browser_pages", {}),
]
STILL_ALLOWED = [
    # Plain web fetch and search, and tools that drive no browser, keep their behaviour.
    ("WebFetch", {"url": "https://example.com", "prompt": "summarise"}),
    ("WebSearch", {"query": "salesforce flow fault email"}),
    ("mcp__fetch__fetch", {"url": "https://example.com"}),
    ("mcp__github__list_issues", {"owner": "example", "repo": "example"}),
    ("mcp__antigravity__generate_image", {"Prompt": "a diagram"}),
    ("mcp__antigravity__delete_knowledge", {"Id": "1"}),
    ("mcp__antigravity__manage_inbox", {"Action": "list"}),
    ("mcp__antigravity__read_resource", {"ServerName": "notes", "Uri": "notes://today"}),
    ("mcp__commander__start_process", {"command": "ls project"}),
]


@pytest.mark.parametrize("tool,arguments", BROWSER_AND_DESKTOP)
def test_build_only_blocks_browser_and_desktop_control_tools(tool, arguments):
    allowed, reason = gate.decide(tool, arguments, W, "build-only")
    assert not allowed
    assert reason.startswith("Build-only mode: browser and desktop control tools are blocked")
    assert "cannot see which page or window" in reason and "org or client page" in reason
    assert "web fetch or web search" in reason
    assert gate.decide(tool, arguments, W, "full") == (True, "")


@pytest.mark.parametrize("tool,arguments", STILL_ALLOWED)
def test_web_fetch_search_and_other_tools_keep_their_behaviour(tool, arguments):
    assert gate.decide(tool, arguments, W, "build-only") == (True, "")


@pytest.mark.parametrize("tool,arguments", BROWSER_AND_DESKTOP)
def test_both_modes_read_one_classification(tool, arguments):
    # What build-only blocks here is exactly what connected mode's classifier calls
    # a browser read, a browser change or desktop control.
    assert gate._mcp_surface(tool) in ("browser", "desktop")
    assert cr.classify_mcp(tool, arguments).kind in ("browser_read", "browser_write", "admin")


def test_tools_that_are_not_browser_or_desktop_have_no_surface():
    for tool, _ in STILL_ALLOWED:
        assert gate._mcp_surface(tool) == "", tool
    assert gate._mcp_surface("mcp__filesystem__read_file") == ""
    assert gate._mcp_surface("Read") == ""


# --- Through the Antigravity adapter, in Antigravity's own tool names ---

@pytest.fixture
def root(tmp_path, monkeypatch):
    base = tmp_path / "firm"
    for folder in ("clients/acme", "src", ".agents", ".claude"):
        (base / folder).mkdir(parents=True)
    (base / "workspace.json").write_text(json.dumps(
        {"schema": "torque.workspace/1", "name": "Synthetic", "profile": "generic", "ai_access": "build-only"}),
        encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "")
    return base


def answer(monkeypatch, capsys, root, name, args):
    data = {"toolCall": {"name": name, "args": args}, "workspacePaths": [root.as_posix()], "conversationId": "c1"}
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(data).encode("utf-8")), encoding="utf-8"))
    assert agy.main() == 0
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("name,args", [
    ("open_browser_url", {"Url": "https://example.com"}),
    ("open_browser_url", {"Url": "https://example.my.salesforce.com/lightning/setup/SetupOneHome/home"}),
    ("read_browser_page", {"PageId": "1"}),
    ("browser_get_dom", {"PageId": "1"}),
    ("browser_click_element", {"PageId": "1", "Selector": "a"}),
    ("click_browser_pixel", {"PageId": "1", "X": 1, "Y": 2}),
    ("execute_browser_javascript", {"PageId": "1", "Script": "document.cookie"}),
    ("capture_browser_screenshot", {"PageId": "1"}),
    ("list_browser_pages", {}),
])
def test_antigravity_browser_tools_are_denied_in_build_only(root, monkeypatch, capsys, name, args):
    reply = answer(monkeypatch, capsys, root, name, args)
    assert reply["decision"] == "deny"
    assert reply["reason"].startswith("Build-only mode: browser and desktop control tools are blocked")


def test_antigravity_web_reads_and_other_tools_are_still_handed_to_its_own_flow(root, monkeypatch, capsys):
    for name, args in (("read_url_content", {"Url": "https://example.com"}), ("search_web", {"Query": "x"}),
                       ("generate_image", {"Prompt": "a diagram"})):
        assert answer(monkeypatch, capsys, root, name, args)["decision"] == "ask", name


def test_every_antigravity_browser_tool_is_covered():
    browser = {name for name in agy.SCANNED_TOOLS if gate._mcp_surface("mcp__antigravity__" + name)}
    assert browser == {name for name in agy.SCANNED_TOOLS if "browser" in name}
    assert agy.SCANNED_TOOLS - browser == {"delete_knowledge", "generate_image", "manage_inbox", "read_resource"}


# --- An MCP server under a neutral name ---

ORG_HOSTS = [
    "https://example.my.salesforce.com", "https://example--uat.sandbox.my.salesforce.com/services/data/v60.0",
    "example.lightning.force.com", "https://example.my.site.com/portal", "https://example.my.salesforce-setup.com/",
    "https://example.cloudforce.com", "https://example--c.vf.force.com/apex/Page", "https://example.file.force.com/x",
    "https://na139.salesforce.com", "https://login.salesforce.com", "https://test.salesforce.com/services/oauth2/token",
    "login.salesforce.com", "HTTPS://EXAMPLE.MY.SALESFORCE.COM/", "https://example.my.salesforce.com.",
    "https://example%2Emy%2Esalesforce%2Ecom/", "https://example.my.salesforce-sites.com/",
    "https://example--ns.documentforce.com/", "https://example.database.com",
    "open https://example.my.salesforce.com/001 and read the page", "admin@example.my.salesforce.com",
]
OTHER_HOSTS = [
    "https://example.com", "https://developer.salesforce.com/docs/atlas.en-us.apexcode.meta",
    "https://help.salesforce.com/s/articleView?id=sf.flow.htm", "https://trailhead.salesforce.com/",
    "https://www.salesforce.com/products/", "salesforce.com", "someone@salesforce.com",
    "https://www.website.com/a", "https://workforce.com", "https://airforce.com/x", "https://notsalesforce.com/",
    "https://example.my.salesforce.com.example.org/x", "https://login.salesforce.com-example.org/",
    "https://salesforce.community/", "a note about force.community and my.site.common",
]


@pytest.mark.parametrize("text", ORG_HOSTS)
def test_a_neutral_mcp_server_is_blocked_when_an_argument_names_a_salesforce_host(text):
    for arguments in ({"url": text}, {"options": {"targets": [{"endpoint": text}]}}, {"q": "x", "args": [text]}):
        allowed, reason = gate.decide("mcp__crm__query", arguments, W, "build-only")
        assert not allowed and "names the Salesforce host" in reason and "org access" in reason, arguments
        assert gate.decide("mcp__crm__query", arguments, W, "full") == (True, "")


@pytest.mark.parametrize("text", OTHER_HOSTS)
def test_public_salesforce_sites_and_lookalike_hosts_are_not_org_access(text):
    assert gate.decide("mcp__fetch__fetch", {"url": text}, W, "build-only") == (True, "")
    assert gate._salesforce_host({"url": text}) == ""


def test_the_host_scan_reports_the_host_it_found():
    assert gate._salesforce_host({"a": ["x", {"b": "see https://Example.My.Salesforce.com/001"}]}) == (
        "example.my.salesforce.com")
    assert gate._salesforce_host({"a": 1, "b": None, "c": ["plain text"]}) == ""
    # A host used as an argument name counts too.
    call = {"headers": {"https://example.my.salesforce.com": "Bearer x"}}
    assert gate._salesforce_host(call) == "example.my.salesforce.com"
    assert not gate.decide("mcp__crm__query", call, W, "build-only")[0]


def test_a_server_name_holding_a_double_underscore_is_read_whole():
    assert gate._mcp_surface("mcp__plugin__chrome-devtools__click") == "browser"
    assert gate._mcp_surface("mcp__team__computer-use__key") == "desktop"
    assert not gate.decide("mcp__plugin__chrome-devtools__click", {"uid": "x"}, W, "build-only")[0]


def test_the_name_checks_still_apply_first():
    allowed, reason = gate.decide("mcp__salesforce__run_soql_query", {"query": "x"}, W, "build-only")
    assert not allowed and "looks like Salesforce org access" in reason


def test_a_resource_read_is_classified_by_the_server_its_arguments_name():
    for tool, arguments in (("ReadMcpResourceTool", {"server": "salesforce", "uri": "salesforce://record/001"}),
                            ("ReadMcpResourceTool", {"server": "sfdx-tools", "uri": "x://y"}),
                            ("mcp__antigravity__read_resource", {"ServerName": "salesforce", "Uri": "x://y"}),
                            ("ReadMcpResourceTool", {"server": "notes", "uri": "https://example.my.salesforce.com/"})):
        allowed, reason = gate.decide(tool, arguments, W, "build-only")
        assert not allowed and "Salesforce" in reason, (tool, arguments)
    chrome = {"server": "claude-in-chrome", "uri": "x://y"}
    allowed, reason = gate.decide("ReadMcpResourceTool", chrome, W, "build-only")
    assert not allowed and "browser and desktop control tools" in reason
    notes = {"server": "notes", "uri": "notes://today"}
    assert gate.decide("ReadMcpResourceTool", notes, W, "build-only") == (True, "")
    # A spare `server` argument does not hide the name the tool arrived under.
    assert not gate.decide("mcp__claude-in-chrome__read_page", {"server": "notes"}, W, "build-only")[0]


def test_the_remaining_gap_is_real_and_documented():
    # A neutral server whose arguments carry no Salesforce host passes: an alias, a
    # record ID and a SOQL string say nothing the gate reads.
    call = {"org": "example-prod", "soql": "SELECT Id FROM Account", "recordId": "001000000000001AAA"}
    assert gate.decide("mcp__crm__query", call, W, "build-only") == (True, "")
    text = " ".join((Path(__file__).resolve().parents[1] / "docs" / "ai-access.md").read_text(encoding="utf-8").split())
    limits = text.split("## What it cannot stop", 1)[1]
    assert "neutral name" in limits and "an org alias, a record ID or a SOQL string" in limits
