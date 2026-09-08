"""Salesforce Setup embeds confirmation tokens in encoded JavaScript links."""
import pytest
from jsc_browser_tests.diagnostics import redact

@pytest.mark.parametrize('key', ['_CONFIRMATIONTOKEN','confirmationToken','csrfToken','csrf_token','nonce','sid'])
@pytest.mark.parametrize('equals', ['=', '%3D', '%253D', '%25253D'])
def test_encoded_setup_credentials_never_survive_diagnostics(key, equals):
    text='javascript:srcUp(%27%2F005000000000001%3F'+key+equals+'private-synthetic-value%26isdtp%3Dp1%27);'
    assert 'private-synthetic-value' not in redact(text)
    assert '[REDACTED]' in redact(text)

@pytest.mark.parametrize('key', ['_CONFIRMATIONTOKEN','csrfToken','csrf_token','nonce'])
def test_structured_credential_fields_redacted(key):
    assert redact({key: 'private-synthetic-value'})[key]=='[REDACTED]'
