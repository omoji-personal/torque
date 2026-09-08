"""Authoritative policy compatibility; alias-only trust cannot grant a bypass."""
import builtins
import subprocess

import pytest

from jsc_common.org_classify import DEFAULT_PROD_ALIASES, is_production_target


@pytest.fixture(autouse=True)
def no_real_sf(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected real subprocess")
    monkeypatch.setattr(subprocess, "run", forbidden)


@pytest.mark.parametrize("policy", ["cli_live_first", "dispatcher_alias_first"])
@pytest.mark.parametrize("verdict", [True, False, None])
def test_live_authority_wins_over_alias_and_environment(policy, verdict):
    env={"JSC_PROD_ALIASES":"client-prod", "JSC_PROD_ORG_PATTERN":"never-matches",
         "JSC_QA_TRUST_ALIAS_SANDBOX":"1", "JSC_ORG_TYPE":"sandbox"}
    assert is_production_target("client-prod", policy=policy, env=env,
                                live_resolver=lambda alias: verdict) is (verdict is not False)


@pytest.mark.parametrize("policy", ["cli_live_first", "dispatcher_alias_first"])
@pytest.mark.parametrize("alias", ["client-sandbox", "client-dev", "client-test", "unknown", ""])
def test_unresolvable_target_stays_production_like(policy, alias):
    assert is_production_target(alias, policy=policy,
        env={"JSC_QA_TRUST_ALIAS_SANDBOX":"1", "JSC_PROD_ORG_PATTERN":"nomatch"},
        live_resolver=lambda value:None) is True


@pytest.mark.parametrize("policy", ["cli_live_first", "dispatcher_alias_first"])
def test_developer_is_nonproduction_even_when_actual_is_sandbox_false(monkeypatch, policy):
    from jsc_revert import org_detect
    info=org_detect.OrgInfo("client-test", "00D000000000001EAA", "00D000000000001", False,
                           "https://example.invalid", "", "developer", "Developer Edition", "Organization query")
    monkeypatch.setattr(org_detect,"resolve_org",lambda *a,**k:info)
    assert is_production_target("client-test",policy=policy,env={}) is False


def test_string_only_policy_never_resolves_or_imports_revert(monkeypatch):
    original=builtins.__import__
    def imported(name,*args,**kwargs):
        if name.startswith("jsc_revert"):
            pytest.fail("retired string-only policy imported org resolver")
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,"__import__",imported)
    for alias in ("client-prod","client-test","unknown",""):
        assert is_production_target(alias,policy="hook_prod_like_on_unknown",env={},
                                    live_resolver=lambda alias:pytest.fail("unexpected resolution")) is True


def test_offline_skip_remains_unknown_and_cannot_trust_alias():
    assert is_production_target("client-test",policy="dispatcher_alias_first",
        env={"JSC_VISION_SKIP_LIVE_ORG_DETECT":"1","JSC_QA_TRUST_ALIAS_SANDBOX":"1"}) is True


def test_no_private_defaults_and_unknown_policy_is_error():
    assert DEFAULT_PROD_ALIASES==[]
    with pytest.raises(ValueError):
        is_production_target("client",policy="typo")
