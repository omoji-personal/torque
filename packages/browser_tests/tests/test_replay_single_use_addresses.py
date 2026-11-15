"""`torque browser sanitize-replay` and the redaction also know the single-use sign-in
address the Salesforce CLI issues today (frontdoor.jsp?otp=...) and the endpoint that
issues it. Before, only the older sid= form was caught. Nested percent-encoding is
decoded (a bounded number of times) before the scan."""
from urllib.parse import quote

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


def encoded(text: str, times: int) -> str:
    for _ in range(times):
        text = quote(text, safe="")
    return text


SINGLE_ACCESS = "https://acme.my.salesforce.com/services/oauth2/singleaccess?redirect_uri=%2Flightning"


@pytest.mark.parametrize("times", [2, 3, 4])
@pytest.mark.parametrize("address", [SINGLE_USE, SINGLE_ACCESS], ids=["frontdoor-otp", "singleaccess"])
def test_nested_percent_encoding_is_decoded_before_the_scan(address, times):
    script = f"from urllib.parse import unquote\nawait page.goto(unquote(unquote('{encoded(address, times)}')))\n"
    violations = auth.scan_replay_script(script)
    assert violations and f"after {times - 1} round(s) of percent-decoding" in " ".join(violations)
    assert CODE not in " ".join(violations)


def test_encoding_deeper_than_the_scan_decodes_is_not_called_clean():
    violations = auth.scan_replay_script(f"page.goto(decode('{encoded(SINGLE_USE, auth.DECODE_ROUNDS + 2)}'))")
    assert violations and "still percent-encoded" in violations[-1] and CODE not in " ".join(violations)


@pytest.mark.parametrize("script", [
    "await page.goto(instance_url + '/lightning/o/Account/list?filterName=Recent%20Items')",
    "label = 'Done 100%25'",
])
def test_ordinary_percent_encoding_stays_clean(script):
    assert auth.scan_replay_script(script) == []


def test_a_clean_result_says_what_the_scan_cannot_prove(tmp_path, capsys):
    script = tmp_path / "replay.py"
    script.write_text("await page.goto(instance_url + '/lightning/page/home')\n", encoding="utf-8")
    assert cli.main(["sanitize-replay", str(script)]) == 0
    printed = capsys.readouterr().out
    assert printed.startswith("CLEAN") and "cannot prove" in printed and "percent-decoding" in printed


@pytest.mark.parametrize("text", [
    f"timeout at {SINGLE_USE}",
    f"Invalid Page Redirection for startURL=%2Fhome&otp={CODE}",
    f"query was ?otp%3D{CODE}",
])
def test_redaction_removes_the_one_time_value_with_or_without_its_address(text):
    assert CODE not in redact(text)
    assert redact({"otp": CODE}) == {"otp": "[REDACTED]"}
