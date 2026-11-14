"""`torque browser sanitize-replay` and the redaction also know the single-use sign-in
address the Salesforce CLI issues today (frontdoor.jsp?otp=...) and the endpoint that
issues it. Before, only the older sid= form was caught."""
import pytest

from jsc_browser_tests import auth, cli
from jsc_browser_tests.diagnostics import redact

CODE = "SENTINELonetimecode42"
SINGLE_USE = f"https://acme--sbx.sandbox.my.salesforce.com/secur/frontdoor.jsp?otp={CODE}&cshc=abc&startURL=%2F"


@pytest.mark.parametrize("script", [
    f'await page.goto("{SINGLE_USE}")',
    f"url = 'https://acme.my.salesforce.com/secur/frontdoor.jsp?startURL=%2Fhome&otp={CODE}'",
    f"https%3A%2F%2Facme.my.salesforce.com%2Fsecur%2Ffrontdoor.jsp%3Fotp%3D{CODE}",
    'requests.get(instance + "/services/oauth2/singleaccess", headers=headers)',
    "fetch(base + '/services/oauth2/singleaccess?redirect_uri=%2Flightning')",
])
def test_single_use_addresses_and_their_endpoint_are_flagged_without_echoing_them(script):
    violations = auth.scan_replay_script(script)
    assert violations, "a replay script that holds or mints a sign-in address is not clean"
    assert CODE not in " ".join(violations)


@pytest.mark.parametrize("script", [
    'await page.goto(instance_url + "/lightning/page/home")',
    "# sign in with: torque browser login --target-org ALIAS",
    "await page.fill('#otp_field', code)",
])
def test_ordinary_replay_lines_stay_clean(script):
    assert auth.scan_replay_script(script) == []


def test_the_command_rejects_such_a_script(tmp_path, capsys):
    script = tmp_path / "replay.py"
    script.write_text(f'await page.goto("{SINGLE_USE}")\n', encoding="utf-8")
    assert cli.main(["sanitize-replay", str(script)]) == 2
    printed = capsys.readouterr().out
    assert "REJECTED" in printed and CODE not in printed


@pytest.mark.parametrize("text", [
    f"timeout at {SINGLE_USE}",
    f"Invalid Page Redirection for startURL=%2Fhome&otp={CODE}",
    f"query was ?otp%3D{CODE}",
])
def test_redaction_removes_the_one_time_value_with_or_without_its_address(text):
    assert CODE not in redact(text)
    assert redact({"otp": CODE}) == {"otp": "[REDACTED]"}
