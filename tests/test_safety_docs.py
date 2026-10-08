"""The rule file and the two mode documents say what the gate does for the routes
the safety fixes changed, and state their limits next to the claims."""
from collections import namedtuple
import os
from pathlib import Path

import pytest

from torque import connected_routes as cr, consent, gate, gate_connected as gc, workspace as ws
from torque.presence import Presence

REPO = Path(__file__).resolve().parents[1]
RULE = REPO / "src" / "torque" / "data" / "connected" / "production-approval.md"
Org = namedtuple("Org", "org_id_18 detected_org_type is_production instance_url", defaults=(None,))
ORGS = {"acme-sbx": Org("00D000000000001AAA", "sandbox", False, "https://acme--sbx.sandbox.my.salesforce.com")}
YES = lambda: Presence(True, "")


def flat(path):
    return " ".join(path.read_text(encoding="utf-8").split())


@pytest.fixture
def w(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    for name in cr.SF_CONTAINER_VARS:
        monkeypatch.delenv(name, raising=False)
    root = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(root, "Acme")
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    letter = tmp_path / "a.pdf"
    letter.write_bytes(b"agreement")
    consent.record_consent(root, "Acme", "2026-09-30", letter, ["metadata", "records"], ["acme-sbx"], ["Contact"],
                           presence=YES, resolve=ORGS.get)
    consent.sign_off(root, "Acme", "Reviewer", presence=YES)
    return Path(os.path.realpath(root))


def run(root, tool, inp):
    return gc.decide_connected(tool, inp, root, root, env={"TORQUE_CLIENT": "acme"})


def test_the_rule_file_no_longer_says_reading_pages_is_fine(w):
    text = flat(RULE)
    assert "Reading pages is fine" not in text
    assert "for reading a page as well as for changing one" in text
    assert "torque browser ... --target-org ALIAS" in text and "torque approval request --browser" in text
    # What it says is what the gate does: a page read through a browser tool is refused,
    # with or without a window, and Torque's own browser is the route that needs one.
    for tool, arguments in (("mcp__claude-in-chrome__read_page", {"tabId": 1}),
                            ("mcp__claude-in-chrome__get_page_text", {"tabId": 1}),
                            ("mcp__playwright__browser_snapshot", {}),
                            ("mcp__computer-use__screenshot", {})):
        assert run(w, tool, arguments).action == "deny", tool
    decision = run(w, "Bash", {"command": "torque browser run --target-org acme-sbx"})
    assert decision.action == "deny" and "browser window" in decision.reason


def test_the_rule_file_names_the_refused_commands_and_the_safe_query(w):
    text = flat(RULE)
    assert gc.ORG_IDENTITY_QUERY in text
    for command in ("sf org display", "sf org open --url-only", "sf org generate password", "sf org login",
                    "sf org list", "sf alias list"):
        assert f"`{command}`" in text, command
        assert run(w, "Bash", {"command": command + " -o acme-sbx"}).action == "deny", command
    # The rule file is what a connected workspace gets.
    assert (w / ".claude" / "rules" / "production-approval.md").read_text(encoding="utf-8") == RULE.read_text(
        encoding="utf-8")


def test_connected_doc_lists_what_is_refused_and_what_gets_past():
    text = flat(REPO / "docs" / "connected-approval.md")
    section = text.split("## Credential and org-listing commands", 1)[1].split("## Two approval tiers", 1)[0]
    assert gc.ORG_IDENTITY_QUERY in section and "must cover `records`" in section
    for phrase in ("`org display` and `org display user`, with any flags", "`--url-only`", "`--flags-dir`",
                   "`SF_CONTAINER_MODE`", "`org generate password`", "every `org login` variant",
                   "`auth list`, `alias list` and `env list`", "`force:org:display`", "`sf display org`",
                   "`sf.cmd`, `sf.exe` or `sf.ps1`", "`-EncodedCommand`", "it is not a sandbox",
                   "Known forms that are not refused", "is asked about, not refused",
                   "`Start-Process sf -ArgumentList ...`", "neutral name", "A shortened command"):
        assert phrase in section, phrase
    table = text.split("## What the session can do", 1)[1].split("## Setting it up", 1)[0]
    assert "| Credentials and org listings |" in table and "with or without an approval" in table
    # `sf org login` used to be listed as a program the gate asks about.
    asked = next(line for line in (REPO / "docs" / "connected-approval.md").read_text(encoding="utf-8").splitlines()
                 if line.startswith("| Programs the gate cannot check"))
    assert "sf org login" not in asked and "neutral name" in asked
    reads = text.split("`sf` commands that only read", 1)[1]
    assert "`org display [user]`" not in reads and "they are refused" in reads


def test_the_read_list_in_the_connected_doc_matches_the_route_table():
    reads = flat(REPO / "docs" / "connected-approval.md").split("`sf` commands that only read", 1)[1]
    listed = reads.split("`apex tail log` is not a read", 1)[0]
    for topic in cr.SF_READ:
        assert topic[-1] in listed and topic[0] in listed, topic
    assert ("org", "display") not in cr.SF_READ and ("org", "list") not in cr.SF_READ


def test_build_only_doc_states_the_new_blocks_and_their_limits():
    text = flat(REPO / "docs" / "ai-access.md")
    blocks = text.split("## What build-only blocks", 1)[1].split("## Which workspace governs", 1)[0]
    for word in ("chrome", "playwright", "puppeteer", "browser", "firefox", "safari", "webdriver", "selenium",
                 "desktop", "applescript", "automation"):
        assert f"`{word}`" in blocks, word
        assert gate.BROWSER_SERVER.search(word) or gate.DESKTOP_SERVER.search(word), word
    for suffix in gate.SF_HOST_SUFFIXES:
        assert f"`{suffix}`" in blocks, suffix
    for host in gate.SF_PUBLIC_HOSTS:
        assert f"`{host.removesuffix('.salesforce.com')}`" in blocks or f"`{host}`" in blocks, host
    assert "the gate cannot see which page or window such a tool acts on" in blocks
    assert "`open_browser_url`" in blocks and "`execute_browser_javascript`" in blocks
    assert "`WebFetch`, `WebSearch`" in blocks and "pass as before" in blocks
    limits = text.split("## What it cannot stop", 1)[1]
    assert "not a sandbox" in limits
    for phrase in ("neutral name", "has no list of allowed servers",
                   "A browser the gate does not see as a browser tool", "`open` or `start` with a URL",
                   "Desktop Commander"):
        assert phrase in limits, phrase
    assert "In build-only and connected mode the same check fails closed" in text
    assert "The `full`-mode check never blocks by failing" in text


@pytest.mark.parametrize("name", ["docs/ai-access.md", "docs/connected-approval.md",
                                  "src/torque/data/connected/production-approval.md"])
def test_no_em_dashes(name):
    assert "—" not in (REPO / name).read_text(encoding="utf-8"), name
