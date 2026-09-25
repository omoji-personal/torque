import json
import os
import subprocess
import sys

import pytest

from torque import approval, contracts

pytestmark = pytest.mark.skipif(not hasattr(os, "getuid"), reason="tier 2 is POSIX only")


def test_contract_refuses_production_and_unknown():
    result = contracts.delegated_org_refusal()
    assert result["supported"] and result["passed"]
    assert [c["case"] for c in result["cases"]] == ["live-production", "live-unknown", "consent-production"]
    assert all(c["refused"] and c["reason_class"] == "org-production-or-unknown" for c in result["cases"])


def test_contract_runs_as_a_module_from_the_installed_package(tmp_path):
    run = subprocess.run([sys.executable, "-m", "torque.contracts", "delegated-org-refusal", "--json"],
                         cwd=tmp_path, capture_output=True, text=True, timeout=120,
                         env={**os.environ, "HOME": str(tmp_path)})
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["passed"] is True


def test_injected_resolver_changes_the_outcome():
    """F14: the resolver is genuinely injectable, not just an unused parameter. A
    factory that resolves every kind as a nonproduction org (sandbox) flips every
    case to not-refused, proving delegated_org_refusal actually calls what it is
    given rather than its own hardcoded _org."""
    def all_sandbox(kind):
        return lambda alias: contracts._Org(contracts._ID, "sandbox", False, contracts._INSTANCE)

    result = contracts.delegated_org_refusal(resolve=all_sandbox)
    assert result["supported"] and result["passed"] is False
    assert [c["case"] for c in result["cases"]] == ["live-production", "live-unknown", "consent-production"]
    assert all(not c["refused"] and c["reason_class"] is None for c in result["cases"])


def test_contract_reports_unsupported_without_numeric_uids(monkeypatch):
    monkeypatch.delattr(os, "getuid", raising=False)
    assert contracts.delegated_org_refusal() == {"contract": "delegated-org-refusal", "supported": False,
                                                  "passed": None, "cases": []}


def test_cli_text_output_names_each_case(capsys):
    assert contracts.main(["delegated-org-refusal"]) == 0
    out = capsys.readouterr().out
    for case in ("live-production", "live-unknown", "consent-production"):
        assert f"{case}: refused (org-production-or-unknown)" in out


def test_cli_unknown_contract_exits_2(capsys):
    assert contracts.main(["not-a-real-contract"]) == 2
    assert "usage:" in capsys.readouterr().err


def test_contract_never_mutates_control_stat_even_mid_call(monkeypatch):
    """Fix round 1, Important: the contract must satisfy R46 with a call-scoped
    control_stat override, never by swapping the process-global
    approval._control_stat, which could mask a real R46 violation for a
    concurrent caller elsewhere in the same process. A before/after identity
    check alone would miss a mutation that is restored before this function
    returns, so this spies on every moment the R46 folder check actually runs
    (inside each case's grant call) and asserts the global was untouched at each
    one, not only at the very end."""
    real = approval._control_stat
    seen = []
    original = approval._folder_problem

    def spy(*args, **kwargs):
        seen.append(approval._control_stat is real)
        return original(*args, **kwargs)

    monkeypatch.setattr(approval, "_folder_problem", spy)
    result = contracts.delegated_org_refusal()
    assert result["passed"] is True
    assert seen, "the R46 folder check must have run at least once per case"
    assert all(seen), "approval._control_stat must never change while a grant is in progress"


def test_cli_exit_1_and_not_refused_text_when_a_case_passes_through(monkeypatch, capsys):
    """Minor: main()'s exit-1 branch and its "NOT REFUSED" text line, driven
    through main() itself with a resolver injected that makes every case resolve
    as a nonproduction org, so none of them is refused."""
    def all_sandbox(kind):
        return lambda alias: contracts._Org(contracts._ID, "sandbox", False, contracts._INSTANCE)

    monkeypatch.setitem(contracts.CONTRACTS, "delegated-org-refusal",
                        lambda: contracts.delegated_org_refusal(resolve=all_sandbox))
    assert contracts.main(["delegated-org-refusal"]) == 1
    out = capsys.readouterr().out
    for case in ("live-production", "live-unknown", "consent-production"):
        assert f"{case}: NOT REFUSED (None)" in out


def test_cli_unsupported_path_through_main(monkeypatch, capsys):
    """Minor: the unsupported (no numeric user IDs) path through main() itself,
    text and --json, exits 0 either way (passed is None, never False)."""
    monkeypatch.delattr(os, "getuid", raising=False)
    assert contracts.main(["delegated-org-refusal"]) == 0
    assert capsys.readouterr().out == ""
    assert contracts.main(["delegated-org-refusal", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {"contract": "delegated-org-refusal", "supported": False, "passed": None, "cases": []}
