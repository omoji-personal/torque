"""What `torque qa run` tells an agent about browser work names no host and no private
tool, and points at the sign-in that works: `torque browser login`.

Before, the texts printed a source-checkout command (PYTHONPATH=packages/...), told the
agent to open a "login URL" with one vendor's DevTools tools, and the router data told
it to install a server with another vendor's command."""
import re

import pytest

from jsc_qa import dispatcher, router

DESCRIPTION = "A3 Flow change: new screen flow on the Case page"
NOT_HOST_NEUTRAL = ("PYTHONPATH", "python3 -m", "login URL", "DevTools MCP", "mcp__", "claude", "Claude",
                    "feedback_browser_testing_tool")


@pytest.fixture(autouse=True)
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("TORQUE_WORKSPACE", str(tmp_path))


@pytest.mark.parametrize("surface", ["Funct-Pl", "Multi-Prof", "Funct-MCP"])
def test_browser_surfaces_point_at_torque_commands_and_the_front_door_sign_in(surface):
    result = dispatcher.DISPATCHERS[surface]("acme-sbx", DESCRIPTION)
    assert result.status == "MANUAL_REQUIRED"
    text = result.detail + " " + result.invocation_command
    for words in NOT_HOST_NEUTRAL:
        assert words not in text, words
    assert "torque browser login --target-org acme-sbx --workspace <workspace> --client <client>" in text \
        or "torque browser login --target-org acme-sbx --as-alias" in text
    assert "confirm the signed-in user" in text


def test_the_host_tab_steps_are_complete():
    text = dispatcher.dispatch_funct_mcp("acme-sbx", DESCRIPTION).detail
    assert re.search(r"1\. Run `torque browser login --target-org acme-sbx .*`; it prints a local address", text)
    assert "2. Navigate the tab to that address with the host's browser tool (do not fetch it)" in text
    assert f"3. Walk through: {DESCRIPTION}" in text and "4. Take screenshots" in text


def test_flow_commands_are_the_public_ones():
    assert "torque browser browser <flow> --target-org acme-sbx --workspace <workspace> --client <client>" \
        in dispatcher.dispatch_funct_pl("acme-sbx", DESCRIPTION).detail
    multi = dispatcher.dispatch_multi_prof("acme-sbx", DESCRIPTION).detail
    assert "torque browser multiprofile <flow> --target-org acme-sbx" in multi and "--as-user <username>" in multi


def test_the_router_data_is_host_neutral_and_names_no_retired_hook():
    text = router.DEFAULT_ROUTER_PATH.read_text(encoding="utf-8")
    for words in ("claude mcp add", "mcp__", "deploy_gate", "namespace_touch_gate", "knowledge_readonly_gate",
                  "revert_warning_pretool", "docs/superpowers"):
        assert words not in text, words
    data = router.load_router()          # still a valid router
    surfaces = data["surfaces"]
    assert "torque browser login" in surfaces["Funct-MCP"]["invocation"]
    assert "torque browser login" in surfaces["Funct-MCP"]["description"]
    assert "torque browser browser <flow>" in surfaces["Funct-Pl"]["invocation"]
    assert surfaces["Hook-Gate"]["status"] == "deferred", "kept so a router that still lists it loads"
    assert dispatcher.dispatch_surface("Hook-Gate", "acme-sbx", DESCRIPTION).status == "DEFERRED"
