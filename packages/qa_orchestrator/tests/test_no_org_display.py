"""Neither the QA package nor the browser package runs the Salesforce CLI's org display
command. Torque's own rules tell an agent not to run it (CLI releases before 2.136.8
print the access token there) and name the Organization query instead; the packages now
follow the same rule.

Not covered here: jsc_revert.org_detect, which other packages share."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import jsc_browser_tests
import jsc_qa
from jsc_browser_tests.sf_client import SfClient
from jsc_qa import cli as qa_cli

ORG = "00D000000000001AAA"
PACKAGES = [Path(jsc_browser_tests.__file__).resolve().parent, Path(jsc_qa.__file__).resolve().parent]


def _command_words(tree):
    """Every list or tuple of string constants in a module: the shape of a CLI argument list."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)):
            yield [element.value if isinstance(element, ast.Constant) and isinstance(element.value, str) else None
                   for element in node.elts]


def test_no_argument_list_in_either_package_names_org_display():
    found = []
    for package in PACKAGES:
        for path in sorted(package.rglob("*.py")):
            for words in _command_words(ast.parse(path.read_text(encoding="utf-8"))):
                if any(pair == ("org", "display") for pair in zip(words, words[1:])):
                    found.append(path.name)
    assert found == []


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def run(argv, **options):
        seen.append(list(argv))
        answer = {"status": 0, "result": {"records": [{"Id": ORG, "IsSandbox": True, "OrganizationType": "X",
                                                        "Username": "admin@acme.example", "IsActive": True}],
                                          "url": "https://acme.my.salesforce.com/secur/frontdoor.jsp?otp=x",
                                          "orgId": ORG, "username": "admin@acme.example"}}
        return SimpleNamespace(returncode=0, stdout=json.dumps(answer), stderr="")
    monkeypatch.setattr("jsc_common.tools.run", run)
    monkeypatch.setattr("jsc_browser_tests.sf_client.run_tool", run)
    return seen


def test_the_qa_token_command_resolves_the_org_with_the_organization_query(calls):
    assert qa_cli._get_org_id_18("acme-sbx") == ORG
    assert calls == [["sf", "data", "query", "--query", "SELECT Id FROM Organization",
                      "--target-org", "acme-sbx", "--json"]]


def test_an_unresolved_org_is_none_not_an_error(monkeypatch):
    monkeypatch.setattr("jsc_common.tools.run", lambda argv, **options: SimpleNamespace(returncode=1, stdout="", stderr=""))
    assert qa_cli._get_org_id_18("acme-sbx") is None
    monkeypatch.setattr("jsc_common.tools.run", lambda argv, **options: SimpleNamespace(
        returncode=0, stdout=json.dumps({"result": {"records": []}}), stderr=""))
    assert qa_cli._get_org_id_18("acme-sbx") is None

    def missing(argv, **options):
        raise FileNotFoundError("sf")
    monkeypatch.setattr("jsc_common.tools.run", missing)
    assert qa_cli._get_org_id_18("acme-sbx") is None


def test_every_browser_client_call_avoids_org_display(calls):
    from jsc_browser_tests import auth
    client = SfClient("acme-sbx")
    admin = auth.get_admin_auth(client, "acme-sbx")
    assert (admin.org_id_18, admin.instance_url, admin.username) == (ORG, "https://acme.my.salesforce.com",
                                                                     "admin@acme.example")
    client.org_identity()
    client.find_user("admin@acme.example")
    client.query("SELECT Id FROM Account")
    assert not hasattr(client, "org_display")
    assert calls and all(argv[0] == "sf" and argv[1:3] != ["org", "display"] for argv in calls)
    assert len([argv for argv in calls if argv[1:3] == ["org", "open"]]) == 1, "one CLI call answers the sign-in"
