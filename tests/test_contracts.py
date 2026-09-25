import json
import os
import subprocess
import sys

import pytest

from torque import contracts

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
