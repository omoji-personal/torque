from __future__ import annotations
import asyncio
import os
import sys
import tempfile
import json
from pathlib import Path
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jsc_browser_tests.suite import discover_flows, resolve_write_gate, WriteGateError
from jsc_browser_tests.matrix import expand_cells, Cell

def test_discover_finds_flows_with_spec():
    flows = discover_flows()
    # smoke_login was migrated to have a spec — discovery includes library/ legacy + flows/
    names = {f.spec.name for f in flows if getattr(f, "spec", None)}
    assert "smoke_login" in names

class _OrgInfo:
    def __init__(self, t): self.detected_org_type = t

def test_sandbox_allowed():
    assert resolve_write_gate(_OrgInfo("sandbox"), has_token=False) is True

def test_production_blocked_without_token():
    try:
        resolve_write_gate(_OrgInfo("production"), has_token=False); assert False
    except WriteGateError:
        pass

def test_unknown_blocked_without_token_or_trust():
    try:
        resolve_write_gate(_OrgInfo("unknown"), has_token=False, trust_sandbox=False); assert False
    except WriteGateError:
        pass

def test_unknown_allowed_with_trust_escape():
    import pytest
    with pytest.raises(WriteGateError):
        resolve_write_gate(_OrgInfo("unknown"), has_token=False, trust_sandbox=True)

def test_none_orginfo_blocked():
    try:
        resolve_write_gate(None, has_token=False); assert False
    except WriteGateError:
        pass

class _Spec:
    def __init__(self, profiles, variations):
        self.profiles = profiles
        self.variations = variations

class _Var:
    def __init__(self, name, profile="admin"):
        self.name = name
        self.profile = profile

class _Flow:
    def __init__(self, spec):
        self.spec = spec
        self.name = "f"

def test_expand_cells_applicability():
    f = _Flow(_Spec(["admin", "standard"],
                    [_Var("happy_path", "admin"), _Var("platform_denied", "platform")]))
    cells = expand_cells([f], ["admin", "standard", "platform"])
    applicable = [(c.profile, c.variation.name) for c in cells if c.applicable]
    assert ("admin", "happy_path") in applicable
    assert ("standard", "happy_path") in applicable
    assert ("platform", "platform_denied") in applicable
    # the general happy_path does NOT run under the non-declared platform profile
    assert ("platform", "happy_path") not in applicable

def test_expand_cells_not_applicable():
    g = _Flow(_Spec(["admin"], [_Var("happy_path", "admin")]))
    cells = expand_cells([g], ["admin", "standard", "platform"])
    na_profiles = [c.profile for c in cells if not c.applicable]
    assert "standard" in na_profiles and "platform" in na_profiles
    assert any(c.profile == "admin" and c.applicable for c in cells)

def test_login_as_preflight_login_then_logout():
    import jsc_browser_tests.matrix as m
    from jsc_browser_tests.sf_client import FakeSfClient
    order = []
    async def fake_login(page, instance_url, uid, **kwargs): order.append(("login", uid))
    async def fake_logout(page, instance_url): order.append(("logout",))
    from jsc_browser_tests import auth
    orig_login, orig_logout, orig_observe, orig_auth_observe = m.login_as_user, auth.logout_as_user, m.observe_user_id, auth.observe_user_id
    identities = iter(["005admin", "005x1", "005x1", "005admin", "005x2", "005x2", "005admin"])
    async def fake_observe(page): return next(identities)
    m.observe_user_id = auth.observe_user_id = fake_observe
    m.login_as_user, auth.logout_as_user = fake_login, fake_logout
    try:
        seed = {"users": {"standard": {"user_id": "005x1"}, "platform": {"user_id": "005x2"}}}
        results = asyncio.run(m.login_as_preflight(object(), seed, FakeSfClient()))
    finally:
        m.login_as_user, auth.logout_as_user, m.observe_user_id, auth.observe_user_id = orig_login, orig_logout, orig_observe, orig_auth_observe
    assert results == {"standard": "PASS", "platform": "PASS"}
    assert order == [("login", "005x1"), ("logout",), ("login", "005x2"), ("logout",)]

def test_run_suite_produces_manifest_and_exit_zero():
    import asyncio
    from jsc_browser_tests.suite import run_suite
    from jsc_browser_tests.sf_client import FakeSfClient
    from jsc_browser_tests.runner import FlowResult
    from jsc_browser_tests.library.smoke_login import FLOW as SMOKE
    import copy
    CLOSE = copy.deepcopy(SMOKE)
    CLOSE.spec = copy.deepcopy(SMOKE.spec)
    CLOSE.spec.name = "synthetic_secondary"
    async def fake_exec(cell, config):
        return FlowResult(flow_name=cell.flow.spec.name, profile=cell.profile,
                          target_org="sf-fake", overall_status="PASS")
    d = tempfile.mkdtemp(prefix="jsc-suite-run-")
    mpath = os.path.join(d, "run-manifest.json")
    class _Org: detected_org_type = "sandbox"
    config = {"sf": FakeSfClient(), "org_info": _Org(), "flows": [SMOKE, CLOSE],
              "profiles": ["admin"], "manifest_path": mpath,
              "audit_log": os.path.join(d, "audit.log"), "run_dir": d,
              "cell_executor": fake_exec}
    ec = asyncio.run(run_suite(config))
    assert os.path.exists(mpath)
    assert ec == 0
    m = json.loads(open(mpath, encoding="utf-8").read())
    assert "score" in m and "cells" in m and len(m["cells"]) >= 2

def test_run_suite_blocks_on_failed_write_gate():
    import asyncio
    from jsc_browser_tests.suite import run_suite
    from jsc_browser_tests.sf_client import FakeSfClient
    d = tempfile.mkdtemp(prefix="jsc-suite-block-")
    mpath = os.path.join(d, "m.json")
    config = {"sf": FakeSfClient(), "org_info": None, "flows": [], "profiles": ["admin"],
              "manifest_path": mpath, "audit_log": os.path.join(d, "a.log"), "run_dir": d,
              "has_token": False, "trust_sandbox": False}
    ec = asyncio.run(run_suite(config))
    assert ec == 2  # empty suite cannot claim success

def test_cli_discovers_generic_smoke_only_by_default():
    from jsc_browser_tests.cli import _list_library_flows
    assert _list_library_flows() == ["smoke_login"]

def test_run_suite_runs_preflight_and_records_it():
    import asyncio, os, tempfile, json
    from jsc_browser_tests.suite import run_suite
    from jsc_browser_tests.sf_client import FakeSfClient
    from jsc_browser_tests.runner import FlowResult
    from jsc_browser_tests.library.smoke_login import FLOW as SMOKE
    ran = {}
    async def fake_pre(cfg):
        ran["yes"] = True
        return {"standard": "PASS"}
    async def fake_exec(cell, cfg):
        return FlowResult(flow_name=cell.flow.spec.name, profile=cell.profile,
                          target_org="sf-fake", overall_status="PASS")
    d = tempfile.mkdtemp(prefix="jsc-pre-")
    mpath = os.path.join(d, "m.json")
    class _O:
        detected_org_type = "sandbox"
    ec = asyncio.run(run_suite({
        "sf": FakeSfClient(), "org_info": _O(), "flows": [SMOKE],
        "profiles": ["admin", "standard"], "manifest_path": mpath,
        "audit_log": os.path.join(d, "a.log"), "run_dir": d, "runid": "TEST-pre1",
        "cell_executor": fake_exec, "preflight": fake_pre,
        "seed": {"users": {"standard": {"user_id": "005x"}}}}))
    assert ran.get("yes") is True
    m = json.loads(open(mpath, encoding="utf-8").read())
    assert m["preflight"] == {"standard": "PASS"}

def test_run_suite_detects_leak_survivor_exit_4():
    import asyncio, os, tempfile
    from jsc_browser_tests.suite import run_suite
    from jsc_browser_tests.sf_client import FakeSfClient
    from jsc_browser_tests.runner import FlowResult
    from jsc_browser_tests.library.smoke_login import FLOW as SMOKE
    async def fake_exec(cell, cfg):
        return FlowResult(flow_name=cell.flow.spec.name, profile=cell.profile,
                          target_org="sf-fake", overall_status="PASS")
    async def no_pre(cfg):
        return {}
    d = tempfile.mkdtemp(prefix="jsc-leak-")
    registry_path = Path(d) / "registry.yaml"
    registry_path.write_text("objects:\n  Demo__Intake__c:\n    test_record_carrier: true\n", encoding="utf-8")
    sf = FakeSfClient(query_results={
        "SELECT COUNT(Id) c FROM Demo__Intake__c WHERE Name LIKE 'TEST-leak1-%'": [{"c": 1}]})
    class _O:
        detected_org_type = "sandbox"
    ec = asyncio.run(run_suite({
        "sf": sf, "org_info": _O(), "flows": [SMOKE], "profiles": ["admin"],
        "manifest_path": os.path.join(d, "m.json"), "audit_log": os.path.join(d, "a.log"),
        "run_dir": d, "runid": "TEST-leak1", "registry_path": registry_path, "cell_executor": fake_exec, "preflight": no_pre}))
    assert ec == 4

def main() -> int:
    failures = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try: fn(); print(f"PASS: {name}")
            except AssertionError as e: failures += 1; print(f"FAIL: {name}: {e}")
    print(f"\n{'ALL PASS' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main())
