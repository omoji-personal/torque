"""The capture-time scrub: credentials refused in every scope, personal data and record
Ids masked, client-identifying text refused in firm scope, and ordinary lesson text
left alone. Credential-shaped test values are assembled here so that no tracked file
holds one."""
import pytest

from jsc_memory import scrubber
from jsc_memory.lesson import Lesson

ORG_ID = "00D" + "5g000004XyZa" + "EAA"                      # 18 characters, made up
RECORD_ID = "001" + "A000001AbCdE" + "FGH"
TOKEN = ORG_ID[:15] + "!" + "AQEAQFakeSessionValue" + "x9Y8z7" * 6
HOST = "sample-nonprofit--uat.sandbox.my" + ".salesforce.com"
SECRETS = {
    "session token": f"the token was {TOKEN} in the log",
    "sign-in address (front door)": f"open https://{HOST}/secur/frontdoor.jsp?sid={TOKEN}&retURL=/",
    "sign-in address (one-time)": f"https://{HOST}/secur/frontdoor.jsp?otp=" + "Ab3dE6gH9jK2mN5p" + "&startURL=%2F",
    "sign-in address (auth url)": "force" + "://PlatformCLI::" + "5Aep" + "861" + "abcDEF123ghiJKL456" + "@" + HOST,
    "address with a token": "POST https://api.example.com/v1/sync?token=" + "f3a9c1e7b2d4068a" + " failed",
    "address with a password": "https://" + "deploy:Wint3r!Pass" + "@build.example.com/job",
    "authorization header": "Authorization: Bearer " + "eyJhbGciOi" + "abc123DEF456ghi789",
    "private key": "-----BEGIN " + "RSA PRIVATE KEY-----\nMIIEow...",
    "provider token": "export KEY=" + "gh" + "p_" + "a1B2c3D4e5F6g7H8i9J0" * 2,
    "password assignment": "the integration user's password: " + "Summer2026!",
    "client secret": "client_secret=" + "9F8e7D6c5B4a3210",
}


@pytest.mark.parametrize("name", sorted(SECRETS))
def test_a_credential_looking_value_refuses_the_text_and_is_never_echoed(name):
    text, found = scrubber.scrub(SECRETS[name])
    assert found.refused, name
    message = found.refusal("client")
    assert "credential-looking value" in message and "Remove the value" in message
    for secret in (TOKEN, "Summer2026", "Wint3r", "9F8e7D6c5B4a3210", "f3a9c1e7b2d4068a", "a1B2c3D4"):
        assert secret not in message


FAKE = "Fake" + "Only"                 # assembled, so no tracked line reads as a label followed by a value
LABELLED = {
    "prose, quoted, mixed": 'The password is "' + FAKE + '!Mix3d999"',
    "prose, quoted, lowercase": "the client secret was '" + FAKE.lower() + "letters'",
    "prose, quoted, digits": "The passcode is `" + "40" + "81 9921`",
    "prose, quoted, passphrase": 'the passphrase is "correct ' + 'horse battery staple"',
    "prose, for whom": "The password for the integration user is " + FAKE + "2026x",
    "prose, set to": "The API key was set to " + "a1b2" + "c3d4e5f6",
    "prose, session id": "the session id is " + "0f9e8d7c" + "6b5a4321",
    "prose, long letters": "the token is " + "qwertyuiop" + "asdfghjklzxcv",
    "assignment, lowercase": "pass" + "word=" + "correcthorse" + "batterystaple",
    "assignment, digits": "pass" + "code = " + "48" + "151623",
    "assignment, no space after colon": "pass" + "word:" + "onlyletters",
    "assignment, prefixed name": "db_pass" + "word=" + "plainword",
    "assignment, json": '{"api_' + 'key": "' + "abcd" + 'efgh"}',
    "assignment, flag": "sf org create user --pass" + "word " + "simpleword",
    "assignment, sid": "s" + "id=" + "letters" + "only",
}


@pytest.mark.parametrize("name", sorted(LABELLED))
def test_a_value_after_an_explicit_secret_label_is_refused_whatever_it_is_made_of(name):
    text = LABELLED[name]
    cleaned, found = scrubber.scrub(text)
    assert found.refused == {"password or secret": 1}, name
    message = found.refusal("client")
    assert "password or secret" in message
    value = text.split()[-1].strip("\"'`{}")
    assert value[2:] not in message


@pytest.mark.parametrize("text", [
    "The token is short-lived, so ask for a new one per run.",
    "The password is stored in the vault, never in the repository.",
    "The API key for the sync user is kept in a named credential.",
    "The session id is printed by `sf org display` on older CLIs.",
    "The token is valid for 2 hours.",
    "MALFORMED_QUERY, unexpected token: 'ALL' on a query that works from Apex.",
    "`force:user:password:generate` is the old name of the command.",
    "password=<value>, password=****, password=$SF_PASSWORD, token=SF_ACCESS_TOKEN, secret=%CLIENT_SECRET%",
    'The password is "<the one in the vault>".',
    'The token is "the long value the server returns after sign-in".',
    "Rotate the client secret; the passwords are reset monthly.",
])
def test_ordinary_prose_about_secrets_is_left_alone(text):
    cleaned, found = scrubber.scrub(text)
    assert found.refused == {} and cleaned == text


@pytest.mark.parametrize("text", [
    "Never put a token in the address: no `token=` inside any endpoint string.",
    "Sign in through /secur/frontdoor.jsp?sid=<SESSION_ID> with a helper that never prints the address.",
    "The password: required for the integration user, and it is kept in the vault.",
    "Use `sf org login` again; set password=$SF_PASSWORD in the CI secret store.",
    "client_secret = <from the connected app>",
    "The Bearer token goes in a header, never in the address.",
    "Session 20261112T101500123456Z-abcdef123456 taught this.",
])
def test_talking_about_credentials_is_not_a_credential(text):
    cleaned, found = scrubber.scrub(text)
    assert found.refused == {} and cleaned == text


def test_personal_data_and_salesforce_ids_are_masked_in_every_scope():
    text = (f"Jane (jane.doe@example.org, 555-123-4567, (555) 987-6543, SSN 123-45-6789) owns {RECORD_ID} "
            f"in org {ORG_ID}; a0B5g00000AbCdE too.")
    cleaned, found = scrubber.scrub(text)
    assert cleaned == ("Jane (<email>, <phone>, <phone>, SSN <ssn>) owns <record-id> in org <org-id>; "
                       "<record-id> too.")
    assert found.refused == {}
    assert found.masked == {"email address": 1, "phone number": 2, "social security number": 1,
                            "record Id": 2, "org Id": 1}


@pytest.mark.parametrize("text", [
    "An autolaunchedFlow with an accountTeamMember and an approvalProcessId ran administratively.",
    "Verified on 11/14/2026 and 2026-11-14; the ratio was 3/4/10.",
    "Torque 2.0.0.21 and package 1.2.3.4 on API 62.0; CLI 2.136.8.",
    "Query FlowDefinitionView (ActiveVersionId, LatestVersionId) and CampaignMember.AccountId.",
    "FIELD_CUSTOM_VALIDATION_EXCEPTION on Gift_Batch__c; see commit 3f9c2e1a7b4d5e6f8091.",
    "Call 311 or 1-800 numbers? No: the limit is 50,000 rows and 10 000 000 bytes.",
    "LightningComponentBundle acknowledgement internationalization",
])
def test_ordinary_lesson_text_is_left_alone(text):
    cleaned, found = scrubber.scrub(text)
    assert cleaned == text and found.masked == {} and found.refused == {}


def test_two_or_more_finds_never_drop_the_text():
    text = f"Compare {RECORD_ID} with {RECORD_ID[:15]} and mail a@example.com or b@example.com about it."
    cleaned, found = scrubber.scrub(text)
    assert cleaned == "Compare <record-id> with <record-id> and mail <email> or <email> about it."
    assert found.masked == {"record Id": 2, "email address": 2}


def test_invisible_characters_are_removed():
    cleaned, found = scrubber.scrub("skip\u200b the\u202e guard\ufeff")
    assert cleaned == "skip the guard" and found.masked == {"invisible characters": 3}


def test_firm_scope_refuses_what_identifies_a_client_without_printing_it(workspace):
    terms = scrubber.workspace_terms(workspace)
    assert sorted({kind for kind, _ in terms}) == ["client name", "client slug", "org alias"]
    cases = {
        "client name": "At Alpha  Nonprofit the gift batch failed.",
        "client slug": "Copy the layout from the beta sandbox.",
        "org alias": "Deploy to alpha-dev first.",
        "org Id": f"The org {ORG_ID} is the full copy.",
        "org host name": f"Open https://{HOST}/lightning/setup/Flows/home",
    }
    for kind, text in cases.items():
        cleaned, found = scrubber.scrub(text, firm_terms=terms)
        assert kind in found.refused, kind
        message = found.refusal("firm")
        assert "identifies a client" in message and "--scope firm" in message and kind in message
        for word in ("Alpha", "beta sandbox", "alpha-dev", ORG_ID, HOST):
            assert word not in message
    # The same texts are fine for the client's own folder (Ids are masked there).
    for text in cases.values():
        assert scrubber.scrub(text)[1].refused == {}


@pytest.mark.parametrize("text", [
    "The alphabet soup of metadata types is not a client.",
    "Use MyDomainName.my.salesforce.com in the example, or login.salesforce.com and test.salesforce.com.",
    "A pre-release package must not be installed in production; betas cannot be upgraded.",
])
def test_firm_scope_leaves_generic_text_alone(workspace, text):
    cleaned, found = scrubber.scrub(text, firm_terms=scrubber.workspace_terms(workspace))
    assert found.refused == {} and cleaned == text


def test_generalizing_replaces_client_words_instead_of_refusing(workspace):
    text = f"At Alpha Nonprofit (alpha-dev, {ORG_ID}, https://{HOST}) the batch failed for jane@example.org."
    cleaned, found = scrubber.scrub(text, firm_terms=scrubber.workspace_terms(workspace), generalize=True)
    assert cleaned == "At the client (the org, <org-id>, https://<org-host>) the batch failed for <email>."
    assert found.refused == {}
    assert scrubber.scrub(cleaned, firm_terms=scrubber.workspace_terms(workspace))[1].refused == {}


def test_every_text_of_a_lesson_is_scrubbed(workspace):
    item = Lesson(id="L-20261114-0001", title="Mail jane@example.org", when=f"you see {RECORD_ID}", do="ask Beta",
                  because="x", body="call 555-123-4567")
    item.applies["keywords"] = ["jane@example.org"]
    item.evidence = [{"type": "session", "ref": f"record {RECORD_ID}"}]
    found = scrubber.scrub_lesson(item, firm_terms=scrubber.workspace_terms(workspace))
    assert (item.title, item.when, item.body) == ("Mail <email>", "you see <record-id>", "call <phone>")
    assert item.applies["keywords"] == ["<email>"] and item.evidence[0]["ref"] == "record <record-id>"
    assert "client slug" in found.refused
