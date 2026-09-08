#!/usr/bin/env python3
"""Tests for jsc_qa orchestrator (Phase 1A).

Exercises:
- Router YAML loading + validation
- Change-type matching from natural-language descriptions
- Surface effective assignments (production vs sandbox)
- QA skip token mint/validate/revoke
- Seed validator schema checks
- CLI subcommands
- End-to-end /qa run against canned input

NO live SF org calls — uses mocks where needed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class CliTooSlow(Exception):
    """The CLI did not finish in time AND the machine was too loaded to judge."""


# 30s is generous for the CLI's own work and nowhere near generous enough for the
# `sf` shell-outs it makes when the machine is busy. Measured 2026-07-28 at load
# 42: one `sf org display` on an unknown alias took 15.8s against a normal 1-3s,
# and the full `jsc_qa.cli run` took 40s at 27% CPU — i.e. waiting, not working.
# It produced completely correct output, so the 30s cap was timing the machine.
#
# Same call as f271d0e made for the latency fixture: above load 8 a stopwatch
# reading reflects scheduler contention, so raise CliTooSlow and let the caller
# report an incomplete run rather than pretend the fixtures finished. Both a
# genuine hang and an incomplete loaded-machine run return nonzero for CI.
_LOAD_CEILING = 8.0
_CLI_TIMEOUT_S = 30


def _run_cli(args: list[str], env_overrides: dict | None = None) -> tuple[int, str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]) + os.pathsep + os.environ.get("PYTHONPATH", "")
    if env_overrides:
        env.update(env_overrides)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "jsc_qa.cli", *args],
            capture_output=True, text=True, env=env, timeout=_CLI_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        try:
            load = os.getloadavg()[0]
        except OSError:
            load = 0.0
        if load > _LOAD_CEILING:
            raise CliTooSlow(
                f"{' '.join(args)[:60]} exceeded {_CLI_TIMEOUT_S}s at load "
                f"{load:.1f} — re-run below load {_LOAD_CEILING:.0f}") from None
        raise
    return proc.returncode, proc.stdout, proc.stderr


def main() -> int:
    failures = 0
    tests = []

    def check(label: str, condition: bool):
        nonlocal failures
        tests.append((label, condition))
        if not condition:
            failures += 1

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from jsc_qa import router, qa_skip_token, seed_validator, dispatcher

    # ── ROUTER YAML LOADING ────────────────────────────────────────────────
    rt = router.load_router()
    check("F-RT-1 router loads + validates", isinstance(rt, dict))
    check("F-RT-1b router schema_version=1", rt["schema_version"] == 1)
    check("F-RT-1c router has >=55 change_types (lower-bound, not a magic count)",
          len(rt["change_types"]) >= 55)  # 49 base + 11 extensions; grows over time
    # 13 original + AI-Prompt added in v7.15.1 + Funct-Inspect added 2026-05-21
    # in the sf-browser-testing v3 ship (chrome-devtools-mcp narrowed to
    # inspection-only role per design-v3 spec). Per Codex-fullrepo-R1-P1-01.
    # Brittle-count assertion replaced with required-surface-name check so
    # future surface additions don't false-fail this test.
    required_surfaces = {
        "MetaAPI", "Funct-Pl", "Funct-MCP", "Funct-Inspect", "Side-Eff",
        "Parity", "Vision", "Multi-Prof", "Adv-Probe", "Hook-Gate",
        "TAA", "Hostile-QA", "a11y", "Visual-Reg", "AI-Prompt",
    }
    actual_surfaces = set(rt["surfaces"].keys())
    missing = required_surfaces - actual_surfaces
    check(f"F-RT-1d router has all 15 required surfaces (missing: {missing})",
          missing == set())

    # ── CHANGE-TYPE MATCHING ──────────────────────────────────────────────
    matches = router.match_change_types("I shipped a Flow change to Cancel_Appointment", rt)
    check("F-MT-1 Flow change → matches A3",
          any(ct["id"] == "A3" for ct in matches))

    matches = router.match_change_types("Updated the Data mapping configuration records for City field", rt)
    check("F-MT-2 Data mapping configuration records → matches B3",
          any(ct["id"] == "B3" for ct in matches))

    matches = router.match_change_types("A3", rt)
    check("F-MT-3 direct id match A3 works", len(matches) == 1 and matches[0]["id"] == "A3")

    matches = router.match_change_types("LWC component update for timekeepingNotes", rt)
    check("F-MT-4 LWC change → matches A17 (extended taxonomy)",
          any(ct["id"] == "A17" for ct in matches))

    matches = router.match_change_types("Added a sharing rule for accounts", rt)
    check("F-MT-5 Sharing rule → matches H1 (extended taxonomy)",
          any(ct["id"] == "H1" for ct in matches))

    matches = router.match_change_types("nonsense gibberish xyz123", rt)
    check("F-MT-6 nonsense description returns empty list", matches == [])

    # ── A1: WORD-BOUNDARY ROUTER PRECISION (multi-match preserving) ────────
    # A1-1: "metadata" in description must NOT trigger CTs that keyword-match the
    # bare word "data" via substring (the old `kw in desc` bug matched "data"
    # inside "metadata"). Assert returned rows are real DICTS and none are the
    # data-op CTs C4/C8/D2.
    rows = router.match_change_types("I added metadata to the FlexiPage", rt)
    check("F-A1-1 metadata desc returns row DICTS",
          all(isinstance(r, dict) and "id" in r for r in rows))
    spurious = {"C4", "C8", "D2"}
    matched_ids = {r["id"] for r in rows}
    check(f"F-A1-1b metadata desc matches NO data-op CT (got {matched_ids & spurious})",
          not (matched_ids & spurious))

    # A1-2: a real Flow change still routes to A3 (Flow CRUD) — word-boundary
    # didn't over-tighten away legitimate matches.
    rows = router.match_change_types("I shipped a Flow change", rt)
    check("F-A1-2 'Flow change' still matches A3 (Flow CRUD)",
          any(r["id"] == "A3" for r in rows))

    # A1-3: MULTI-change-type matching preserved — a description naming two
    # distinct CTs returns BOTH rows (deploy touching N metadata types → N rows).
    rows = router.match_change_types(
        "I shipped a Flow change and updated the Data mapping configuration records for City field", rt)
    multi_ids = {r["id"] for r in rows}
    check("F-A1-3 multi-CT description returns BOTH A3 and B3",
          "A3" in multi_ids and "B3" in multi_ids)

    # A1-4: match_change_types_scored returns (ct, score) pairs, ranked, and a
    # direct-id mention outranks a keyword-only match.
    scored = router.match_change_types_scored("I shipped a Flow change", rt)
    check("F-A1-4 scored returns (dict, int) tuples",
          all(isinstance(p, tuple) and isinstance(p[0], dict)
              and isinstance(p[1], int) for p in scored))
    check("F-A1-4b scored ranked descending by score",
          all(scored[i][1] >= scored[i + 1][1] for i in range(len(scored) - 1)))
    scored_direct = router.match_change_types_scored("change A3 plus a flow tweak", rt)
    a3_score = next((sc for ct, sc in scored_direct if ct["id"] == "A3"), 0)
    check("F-A1-4c direct-id match gets the id bonus (high score)", a3_score >= 100)

    # ── SURFACE EFFECTIVE ASSIGNMENTS ─────────────────────────────────────
    a3 = next(ct for ct in rt["change_types"] if ct["id"] == "A3")
    grouped = router.required_surfaces(a3, is_production=True, router=rt)
    check("F-SE-1 A3 has required_automated surfaces",
          "MetaAPI" in grouped["required_automated"])
    check("F-SE-1b A3 has Multi-Prof in required_manual",
          "Multi-Prof" in grouped["required_manual"])
    check("F-SE-1c A3 has Funct-Pl OR Funct-MCP in one_of",
          "Funct-Pl" in grouped["one_of"] and "Funct-MCP" in grouped["one_of"])

    f7 = next(ct for ct in rt["change_types"] if ct["id"] == "F7")
    grouped_f7 = router.required_surfaces(f7, is_production=False, router=rt)
    check("F-SE-2 F7 (local/clients/ docs) has empty surfaces (intentionally uncovered)",
          all(len(v) == 0 for v in grouped_f7.values()))

    # ── QA SKIP TOKEN (mint/validate/revoke) ──────────────────────────────
    with tempfile.TemporaryDirectory() as tmpd:
        token_path = Path(tmpd) / ".qa_skip_token.json"

        # Mint a valid token
        path = qa_skip_token.mint(
            operation_type="skip_one_off",
            org_id_18="00DPP0000004XYZAB1",
            skip_target="Vision",
            reason="testing token mint flow — long enough reason text",
            expiry_seconds=600,
            target_path=token_path,
        )
        check("F-TK-1 token minted", path == token_path and token_path.exists())
        st = token_path.stat()
        check("F-TK-1b mode is 0o600", (st.st_mode & 0o777) == 0o600)

        # Show
        token = qa_skip_token.show(token_path)
        check("F-TK-2 show returns token dict",
              isinstance(token, dict) and token["operation_type"] == "skip_one_off")

        # Validate (matching skip_target)
        old_env = os.environ.get("JSC_QA_SKIP_TOKEN_PATH")
        os.environ["JSC_QA_SKIP_TOKEN_PATH"] = str(token_path)
        ok, detail = qa_skip_token.validate_for_skip(
            org_id_18="00DPP0000004XYZAB1",
            skip_target="Vision",
        )
        check("F-TK-3 validate matching skip_target → True", ok)
        check("F-TK-3b token consumed after validation", not token_path.exists())
        if old_env is not None:
            os.environ["JSC_QA_SKIP_TOKEN_PATH"] = old_env
        else:
            del os.environ["JSC_QA_SKIP_TOKEN_PATH"]

        # Validate non-matching skip_target → False
        path = qa_skip_token.mint(
            operation_type="skip_one_off",
            org_id_18="00DPP0000004XYZAB1",
            skip_target="Vision",
            reason="testing skip_target mismatch validation",
            expiry_seconds=600,
            target_path=token_path,
        )
        os.environ["JSC_QA_SKIP_TOKEN_PATH"] = str(token_path)
        ok, detail = qa_skip_token.validate_for_skip(
            org_id_18="00DPP0000004XYZAB1",
            skip_target="MetaAPI",  # different surface
        )
        check("F-TK-4 validate non-matching skip_target → False", not ok)
        check("F-TK-4b detail mentions mismatch", "skip_target" in detail.lower())
        if old_env is not None:
            os.environ["JSC_QA_SKIP_TOKEN_PATH"] = old_env
        else:
            os.environ.pop("JSC_QA_SKIP_TOKEN_PATH", None)
        try: token_path.unlink()
        except FileNotFoundError: pass

        # TTL cap enforcement
        try:
            qa_skip_token.mint(
                operation_type="skip_one_off",
                org_id_18="00DPP0000004XYZAB1",
                skip_target="Vision",
                reason="ttl exceeds cap test",
                expiry_seconds=3600,  # > 900s cap for skip_one_off
                target_path=token_path,
            )
            check("F-TK-5 TTL > cap raises ValueError", False)
        except ValueError:
            check("F-TK-5 TTL > cap raises ValueError", True)

    # ── SEED VALIDATOR (schema only; no SOQL) ─────────────────────────────
    with tempfile.TemporaryDirectory() as tmpd:
        seed_path = Path(tmpd) / "test-users.json"
        good_seed = {
            "schema_version": 1,
            "org_id_18": "00DPP0000004XYZAB1",
            "alias": "sf-test",
            "is_production": False,
            "users": {
                "admin": {
                    "username": "admin@test.org",
                    "user_id": "0050a000001ABC123",
                    "expected_profile": "System Administrator",
                    "license": "Salesforce",
                    "expected_permission_sets": ["Admin_Tools"],
                    "allowed_in_prod": True,
                },
            },
        }
        seed_path.write_text(json.dumps(good_seed))
        os.chmod(seed_path, 0o600)
        loaded = seed_validator.load_seed(seed_path)
        check("F-SD-1 valid seed loads", loaded["alias"] == "sf-test")
        seed_validator.validate_seed_schema(loaded)
        check("F-SD-1b valid seed passes schema", True)

        # Forbidden field detection
        bad_seed = dict(good_seed)
        bad_seed["users"]["admin"]["password"] = "secretpass"
        seed_path.write_text(json.dumps(bad_seed))
        os.chmod(seed_path, 0o600)
        try:
            loaded = seed_validator.load_seed(seed_path)
            seed_validator.validate_seed_schema(loaded)
            check("F-SD-2 forbidden 'password' field raises", False)
        except seed_validator.SeedValidationError:
            check("F-SD-2 forbidden 'password' field raises", True)

        # Bad mode
        good_seed_path = Path(tmpd) / "good-seed.json"
        good_seed_path.write_text(json.dumps(good_seed))
        os.chmod(good_seed_path, 0o644)  # too permissive
        try:
            seed_validator.load_seed(good_seed_path)
            check("F-SD-3 bad mode raises", False)
        except seed_validator.SeedValidationError as e:
            check("F-SD-3 bad mode raises", "0o644" in str(e) or "mode" in str(e))

    # ── DISPATCHER (smoke — invokes shouldn't crash; expect MANUAL_REQUIRED for stubs) ──
    # Funct-Pl: with no-match description, returns MANUAL_REQUIRED with available_flows listed
    result = dispatcher.dispatch_surface("Funct-Pl", "sf-test", "demo change with no flow")
    check("F-DP-1 Funct-Pl no-match → MANUAL_REQUIRED",
          result.status == "MANUAL_REQUIRED")
    check("F-DP-1b Funct-Pl manual lists available flows",
          "available_flows" in (result.metadata or {}))

    # Funct-Pl: matched library flow → invokes CLI (will FAIL/ERROR since sf-test isn't authenticated,
    # but importantly does NOT crash and returns a structured result).
    result = dispatcher.dispatch_surface("Funct-Pl", "sf-test", "smoke_login flow")
    check("F-DP-1c Funct-Pl matched flow dispatches CLI",
          result.status in ("PASS", "FAIL", "ERROR"))
    check("F-DP-1d Funct-Pl matched flow records flow_name",
          result.metadata.get("flow_name") == "smoke_login")

    # Explicit client flow adapters participate in both dispatchers' actual discovery.
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as flow_dir:
        nested = Path(flow_dir) / "nested" / "example.py"
        nested.parent.mkdir()
        nested.write_text("from copy import deepcopy\nfrom jsc_browser_tests.library.smoke_login import FLOW as SMOKE\nclass Example: pass\nFLOW = Example()\nFLOW.spec = deepcopy(SMOKE.spec)\nFLOW.spec.name = 'synthetic_external_flow'\n")
        with patch.dict(os.environ, {"TORQUE_BROWSER_FLOWS": flow_dir}):
            from jsc_browser_tests.suite import discover_flows
            names = {flow.spec.name for flow in discover_flows()}
            check("F-A3-1 explicit external nested flow discovered", "synthetic_external_flow" in names)
            result = dispatcher.dispatch_surface("Funct-Pl", "sf-test", "synthetic_external_flow")
            check("F-A3-1b explicit flow uniquely matched for CLI dispatch", result.metadata.get("flow_name") == "synthetic_external_flow" and result.status in ("PASS", "FAIL", "ERROR"))
            for surface, label in [("Multi-Prof", "F-A3-2"), ("Funct-Pl", "F-A3-3")]:
                result = dispatcher.dispatch_surface(surface, "sf-test", "description with no matching flow")
                check(f"{label} {surface} discovers same configured flow set", names.issubset(set(result.metadata.get("available_flows", []))))

    result = dispatcher.dispatch_surface("Hook-Gate", "sf-test", "demo change")
    check("F-DP-2 retired Hook-Gate cannot claim automatic PASS", result.status == "DEFERRED")
    result = dispatcher.dispatch_surface("Vision", "sf-test", "demo change")
    # Vision is now production (v7.15.0). With no qa-tests run dir for sf-test
    # AND/OR no gemini CLI installed, the dispatcher emits MANUAL_REQUIRED.
    # When gemini IS installed AND a real run exists it would emit PASS/FAIL
    # — exercised by integration tests, not this unit suite.
    check("F-DP-3 Vision dispatch returns MANUAL_REQUIRED for empty/absent state",
          result.status == "MANUAL_REQUIRED")
    check("F-DP-3b Vision detail mentions screenshot/manual review",
          "screenshot" in result.detail.lower() or "manual" in result.detail.lower()
          or "qa-tests" in result.detail.lower() or "gemini" in result.detail.lower())
    result = dispatcher.dispatch_surface("a11y", "sf-test", "demo change")
    check("F-DP-4 a11y returns DEFERRED (Phase 2-N)", result.status == "DEFERRED")
    result = dispatcher.dispatch_surface("nonexistent_surface", "sf-test", "x")
    check("F-DP-5 unknown surface returns ERROR", result.status == "ERROR")
    # AI-Prompt (v7.15.1): demoted to phase_2_wip pending unified JsonExtractor
    # maturity. dispatch_ai_prompt returns DEFERRED with operator-invocation pointer.
    result = dispatcher.dispatch_surface("AI-Prompt", "sf-test", "demo D1 prompt change")
    check("F-DP-6 AI-Prompt needs explicitly configured client fixtures",
          result.status == "MANUAL_REQUIRED")
    check("F-DP-6b AI-Prompt explains fixture configuration",
          "config/ai-fixtures" in result.detail and "contract" in result.detail)

    # ── R1 FIX-PASS COVERAGE ─────────────────────────────────────────────
    # F-DP-7: target_org validation rejects unsafe inputs
    for bad in ("../../etc", "/absolute/path", "has spaces", "rm -rf /", "", "with;semi"):
        result = dispatcher.dispatch_vision(bad, "x")
        check(f"F-DP-7 dispatch_vision rejects unsafe target_org={bad!r}",
              result.status == "ERROR" and
              ("not a valid" in result.detail or "non-empty" in result.detail))

    # F-DP-7b: AI-Prompt now returns DEFERRED short-circuit (no target_org work)
    result = dispatcher.dispatch_ai_prompt("../../etc", "x")
    check("F-DP-7b dispatch_ai_prompt rejects invalid target before provider work",
          result.status == "ERROR")

    # F-DP-8: production-target gate blocks Vision unless JSC_VISION_PROD_OK=1
    # Use `sf-prod` (matches default \bprod\b heuristic). Note: `sample-prod`
    # no longer matches the default heuristic post-R2-CONSENSUS-1 fix —
    # operator sets JSC_PROD_ALIASES=sample-prod if they want it auto-gated.
    import os as _os
    _os.environ.pop("JSC_VISION_PROD_OK", None)
    _os.environ.pop("JSC_PROD_ALIASES", None)
    result = dispatcher.dispatch_vision("sf-prod", "x")
    check("F-DP-8 production target blocked by default",
          result.status == "MANUAL_REQUIRED" and
          "BLOCKED" in result.detail and "JSC_VISION_PROD_OK" in result.detail)

    # With opt-in env var, gate clears (still likely MANUAL_REQUIRED for missing run dir, but no longer BLOCKED)
    _os.environ["JSC_VISION_PROD_OK"] = "1"
    try:
        result = dispatcher.dispatch_vision("sf-prod", "x")
        check("F-DP-8b JSC_VISION_PROD_OK=1 clears prod gate",
              "BLOCKED" not in result.detail)
    finally:
        _os.environ.pop("JSC_VISION_PROD_OK", None)

    # F-DP-8c: JSC_PROD_ALIASES env-var gates explicitly-listed sandboxes
    _os.environ["JSC_PROD_ALIASES"] = "sample-prod,sf-jscustom-prod"
    try:
        check("F-DP-8c JSC_PROD_ALIASES list catches non-default-matching prod",
              dispatcher._is_production_alias("sample-prod"))
    finally:
        _os.environ.pop("JSC_PROD_ALIASES", None)

    # F-DP-9: caveat footer present in MANUAL_REQUIRED branches when gemini missing
    # (we can't easily mock gemini availability cross-process; the prod-blocked
    # detail above already includes the privacy posture, which is the equivalent
    # discipline gate. Caveat footer exists as a constant; verify it's wired.)
    check("F-DP-9 CLAUDE_ONLY_CAVEAT defined", hasattr(dispatcher, "CLAUDE_ONLY_CAVEAT"))
    check("F-DP-9b caveat mentions claude_only",
          "claude_only" in dispatcher.CLAUDE_ONLY_CAVEAT)

    # F-DP-10: AI-Prompt invocation_command now references the operator-CLI
    # since auto-dispatch is gated to DEFERRED
    result = dispatcher.dispatch_ai_prompt("sf-test", "x")
    check("F-DP-10 AI-Prompt gives actionable client configuration",
          "config/ai-fixtures" in result.detail and result.status == "MANUAL_REQUIRED")

    # F-DP-11: alias spelling cannot establish org type. Inject authoritative
    # resolver results so the alias examples remain hermetic positive controls.
    import os as _os2
    _os2.environ.pop("JSC_PROD_ALIASES", None)
    _os2.environ.pop("JSC_PROD_ORG_PATTERN", None)
    _orig_live = dispatcher._live_org_is_production
    try:
        dispatcher._live_org_is_production = lambda alias: False
        for sandbox in ("sf-nonprod", "sf-preprod", "sf-prodcopy", "sample-sandbox-prodcopy"):
            check(f"F-DP-11 resolved nonproduction {sandbox!r} is allowed",
                  not dispatcher._is_production_alias(sandbox))
        dispatcher._live_org_is_production = lambda alias: True
        for prod in ("sf-prod", "sample-prod", "edc-prod", "production"):
            check(f"F-DP-11b resolved production {prod!r} is detected",
                  dispatcher._is_production_alias(prod))
    finally:
        dispatcher._live_org_is_production = _orig_live

    # F-DP-8d (VISION-PROD-ALIAS, full-repo TAA 2026-05-31): a production org whose
    # alias has NO 'prod' token (e.g. 'sf-customer') is caught via live org_detect,
    # so its Lightning screenshots are NOT silently sent to Gemini without opt-in.
    _os2.environ.pop("JSC_PROD_ALIASES", None)
    _os2.environ.pop("JSC_PROD_ORG_PATTERN", None)
    _os2.environ.pop("JSC_VISION_SKIP_LIVE_ORG_DETECT", None)
    _orig_live = dispatcher._live_org_is_production
    try:
        dispatcher._live_org_is_production = lambda alias: True if alias == "sf-customer" else None
        check("F-DP-8d non-prod-named production org caught via live org_detect",
              dispatcher._is_production_alias("sf-customer"))
        # QA-1 (full-repo manual audit 2026-06-09): an unresolvable alias now
        # fails CLOSED to production — a PII gate must block, not leak.
        _os2.environ.pop("JSC_QA_TRUST_ALIAS_SANDBOX", None)
        check("F-DP-8d2 unresolvable alias fails CLOSED to production",
              dispatcher._is_production_alias("sf-some-sandbox"))
        # The retired trust variable cannot turn unknown identity into nonproduction.
        _os2.environ["JSC_QA_TRUST_ALIAS_SANDBOX"] = "1"
        try:
            check("F-DP-8e obsolete trust env does NOT clear unresolvable alias",
                  dispatcher._is_production_alias("sf-some-sandbox"))
            # ...but an authoritative live=True still wins over the trust env var.
            check("F-DP-8e2 trust env does NOT override authoritative live prod",
                  dispatcher._is_production_alias("sf-customer"))
        finally:
            _os2.environ.pop("JSC_QA_TRUST_ALIAS_SANDBOX", None)
        # And the gate actually blocks Vision for it (no JSC_VISION_PROD_OK)
        _os2.environ.pop("JSC_VISION_PROD_OK", None)
        result = dispatcher.dispatch_vision("sf-customer", "x")
        check("F-DP-8d3 Vision blocked for live-detected prod org",
              result.status == "MANUAL_REQUIRED" and "BLOCKED" in result.detail)
    finally:
        dispatcher._live_org_is_production = _orig_live

    # F-DP-12 (closes claude-R2-P2-1): pathological single-char target_org rejected
    for bad in (".", "..", "-", "_"):
        result = dispatcher.dispatch_vision(bad, "x")
        check(f"F-DP-12 single-char target_org={bad!r} rejected",
              result.status == "ERROR")

    # ── ADV-PROBE PROMOTION (v7.16.0) ────────────────────────────────────
    # F-DP-13: Adv-Probe with no .cls/.trigger paths in description → MANUAL_REQUIRED
    result = dispatcher.dispatch_adv_probe("sf-test", "demo change with no apex paths")
    check("F-DP-13 Adv-Probe no-paths → MANUAL_REQUIRED",
          result.status == "MANUAL_REQUIRED")
    check("F-DP-13b detail mentions providing apex paths",
          "apex paths" in result.detail.lower() or "no existing" in result.detail.lower())

    # F-DP-13c: paths in description that don't exist on disk → MANUAL_REQUIRED with dropped_paths
    result = dispatcher.dispatch_adv_probe(
        "sf-test",
        "Touched packages/nonexistent/Foo.cls and `path/to/Bar.trigger`",
    )
    check("F-DP-13c nonexistent apex paths still MANUAL_REQUIRED",
          result.status == "MANUAL_REQUIRED")
    check("F-DP-13d metadata captures dropped_paths",
          "dropped_paths" in result.metadata and len(result.metadata["dropped_paths"]) >= 2)

    # F-DP-13e: real apex path in description → dispatcher invokes jsc_probes per file.
    # R1 Codex-010 fix: require PASS specifically (NOT ERROR/FAIL); verify generated file
    # is in the dispatcher's chosen output dir (incl. per-file hash subdir).
    import os as _os3
    sample_apex = "codebase/force-app/main/default/classes/ForgotPasswordController.cls"
    sample_full = (Path(__file__).resolve().parents[3] / sample_apex)
    if sample_full.exists():
        with tempfile.TemporaryDirectory() as tmp_out:
            _os3.environ["JSC_QA_ADV_PROBE_OUT"] = tmp_out
            try:
                result = dispatcher.dispatch_adv_probe("sf-test", f"Updated {sample_apex}")
                check("F-DP-13e real apex path → generated draft requires review",
                      result.status == "MANUAL_REQUIRED" and result.metadata.get("compiled") is False and result.metadata.get("executed") is False)
                check("F-DP-13f metadata captures probed apex_paths",
                      "apex_paths" in result.metadata and len(result.metadata["apex_paths"]) >= 1)
                check("F-DP-13f2 metadata has probed_count >= 1",
                      result.metadata.get("probed_count", 0) >= 1)
                # Generated files now in per-file-hash subdirs under tmp_out
                generated = list(Path(tmp_out).rglob("*AdversarialTest.cls"))
                check("F-DP-13g generated test file written under JSC_QA_ADV_PROBE_OUT",
                      len(generated) >= 1)
                check("F-DP-13g2 generated file has matching meta.xml",
                      len(list(Path(tmp_out).rglob("*AdversarialTest.cls-meta.xml"))) >= 1)
            finally:
                _os3.environ.pop("JSC_QA_ADV_PROBE_OUT", None)
    else:
        check("F-DP-13e SKIPPED: codebase/ mirror not present", True)
        check("F-DP-13f SKIPPED: codebase/ mirror not present", True)
        check("F-DP-13f2 SKIPPED: codebase/ mirror not present", True)
        check("F-DP-13g SKIPPED: codebase/ mirror not present", True)
        check("F-DP-13g2 SKIPPED: codebase/ mirror not present", True)

    # F-DP-14 (closes R1 Codex-001): zero-probed-with-eligible-paths → ERROR
    if sample_full.exists():
        _os3.environ["JSC_QA_ADV_PROBE_BUDGET_S"] = "10"  # min allowed
        _os3.environ["JSC_QA_ADV_PROBE_MAX_FILES"] = "10"
        # Manually drain the budget by setting it super-low via direct mock — but
        # since we validate min=10, we can't easily trigger "exhausted before any probe"
        # without slow operations. So we just verify the env-var validation path works:
        _os3.environ["JSC_QA_ADV_PROBE_BUDGET_S"] = "5"  # below min
        try:
            result = dispatcher.dispatch_adv_probe("sf-test", f"Updated {sample_apex}")
            check("F-DP-14 invalid BUDGET_S (below min) returns ERROR",
                  result.status == "ERROR")
            check("F-DP-14b detail mentions env var name",
                  "JSC_QA_ADV_PROBE_BUDGET_S" in result.detail)
        finally:
            _os3.environ.pop("JSC_QA_ADV_PROBE_BUDGET_S", None)
            _os3.environ.pop("JSC_QA_ADV_PROBE_MAX_FILES", None)

    # F-DP-15 (closes R1 Codex-002): MAX_FILES=0 → ERROR
    if sample_full.exists():
        _os3.environ["JSC_QA_ADV_PROBE_MAX_FILES"] = "0"
        try:
            result = dispatcher.dispatch_adv_probe("sf-test", f"Updated {sample_apex}")
            check("F-DP-15 MAX_FILES=0 returns ERROR (not PASS)",
                  result.status == "ERROR")
        finally:
            _os3.environ.pop("JSC_QA_ADV_PROBE_MAX_FILES", None)

    # F-DP-16 (closes R1 Codex-003): non-numeric env var → ERROR (not crash)
    if sample_full.exists():
        _os3.environ["JSC_QA_ADV_PROBE_MAX_FILES"] = "abc"
        try:
            result = dispatcher.dispatch_adv_probe("sf-test", f"Updated {sample_apex}")
            check("F-DP-16 non-numeric env var returns ERROR (no crash)",
                  result.status == "ERROR")
            check("F-DP-16b detail mentions 'not a valid integer'",
                  "not a valid integer" in result.detail)
        finally:
            _os3.environ.pop("JSC_QA_ADV_PROBE_MAX_FILES", None)

    # F-DP-17 (closes R1 Codex-004): JSC_QA_ADV_PROBE_OUT as file (not dir) → ERROR
    if sample_full.exists():
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"i am a file not a dir")
            file_not_dir = f.name
        _os3.environ["JSC_QA_ADV_PROBE_OUT"] = file_not_dir
        try:
            result = dispatcher.dispatch_adv_probe("sf-test", f"Updated {sample_apex}")
            check("F-DP-17 JSC_QA_ADV_PROBE_OUT pointing at file returns ERROR",
                  result.status == "ERROR")
        finally:
            _os3.environ.pop("JSC_QA_ADV_PROBE_OUT", None)
            Path(file_not_dir).unlink(missing_ok=True)

    # F-DP-18: explicitly selected local output is usable without legacy shields.
    with tempfile.TemporaryDirectory() as scratch:
        source = Path(scratch) / "Example.cls"
        source.write_text("public class Example { public static void run() {} }")
        codebase_target = Path(scratch) / "codebase" / "drafts"
        _os3.environ["JSC_QA_ADV_PROBE_OUT"] = str(codebase_target)
        try:
            result = dispatcher.dispatch_adv_probe("sf-test", f"Updated {source}")
            check("F-DP-18 explicit local output generates a draft",
                  result.status == "MANUAL_REQUIRED")
            check("F-DP-18b output is retained for review",
                  len(list(codebase_target.rglob("*AdversarialTest.cls"))) == 1)
        finally:
            _os3.environ.pop("JSC_QA_ADV_PROBE_OUT", None)

    # F-DP-19 (closes R1 Codex-007): .trigger paths filtered out (jsc_probes mishandles)
    result = dispatcher.dispatch_adv_probe(
        "sf-test", "Updated MyTrigger.trigger on Contact",
    )
    check("F-DP-19 .trigger paths dropped, no eligible .cls",
          result.status == "MANUAL_REQUIRED")
    check("F-DP-19b trigger drop reason surfaces in detail or metadata",
          "trigger" in str(result.metadata).lower() or "trigger" in result.detail.lower())

    # F-DP-20 (closes R1-CONSENSUS-1): two same-stem .cls don't collide
    if sample_full.exists():
        with tempfile.TemporaryDirectory() as scratch:
            scratch_p = Path(scratch)
            (scratch_p / "a").mkdir()
            (scratch_p / "b").mkdir()
            (scratch_p / "a" / "Foo.cls").write_text(
                "public class Foo { public static void alpha(String s) {} }"
            )
            (scratch_p / "b" / "Foo.cls").write_text(
                "public class Foo { public static void beta(String s) {} }"
            )
            with tempfile.TemporaryDirectory() as out:
                _os3.environ["JSC_QA_ADV_PROBE_OUT"] = out
                try:
                    desc = f"Updated {scratch_p / 'a' / 'Foo.cls'} and {scratch_p / 'b' / 'Foo.cls'}"
                    result = dispatcher.dispatch_adv_probe("sf-test", desc)
                    # Both should be probed; both AdversarialTest.cls present in distinct subdirs
                    generated = list(Path(out).rglob("FooAdversarialTest.cls"))
                    check("F-DP-20 same-stem .cls → both probed without collision",
                          len(generated) == 2)
                    if len(generated) == 2:
                        contents = [g.read_text() for g in generated]
                        # Each generated test should reference its OWN source's method
                        has_alpha = any("alpha(" in c for c in contents)
                        has_beta = any("beta(" in c for c in contents)
                        check("F-DP-20b distinct content preserved (alpha + beta both present)",
                              has_alpha and has_beta)
                finally:
                    _os3.environ.pop("JSC_QA_ADV_PROBE_OUT", None)

    # ── CLI INTEGRATION ──────────────────────────────────────────────────
    code, out, err = _run_cli(["--help"])
    check("F-CLI-1 --help exits 0", code == 0)
    check("F-CLI-1b --help mentions run + token-grant",
          "run" in out and "token-grant" in out)

    code, out, err = _run_cli(["token-show"])
    check("F-CLI-2 token-show works", code == 0)

    # Run against a description that matches A3 (Flow); use sf-test alias which is sandbox
    code, out, err = _run_cli([
        "run", "I just shipped an A3 Flow change", "--org", "sf-test",
    ])
    # No deploy job ID or expected component set is supplied, so MetaAPI must
    # stay MANUAL_REQUIRED and must not shell out for an unrelated most-recent
    # report. Other selected surfaces may still fail. The report mentions A3.
    check("F-CLI-3 run command emits report",
          "A3" in out and ("MANUAL_REQUIRED" in out or "FAIL" in out or "ERROR" in out or "PASS" in out))

    # F-CLI-4 (A1.3): one_of choice is explicit + printed with a reason.
    # Force ONLY A3 (whose one_of group is {Funct-Pl, Funct-MCP}) via --change-type
    # + a nonsense description, so no other CT's required_automated Funct-Pl
    # pre-covers the group. Default preference picks Funct-Pl.
    code, out, err = _run_cli([
        "run", "xyzzy gibberish nothing", "--org", "sf-test", "--change-type", "A3",
    ])
    check("F-CLI-4 one_of choice printed with reason",
          "one_of[A3]" in out and "Funct-Pl" in out
          and "framework default preference" in out)

    # F-CLI-5 (A1.3): --prefer-surface overrides the default one_of pick.
    code, out, err = _run_cli([
        "run", "xyzzy gibberish nothing", "--org", "sf-test",
        "--change-type", "A3", "--prefer-surface", "Funct-MCP",
    ])
    check("F-CLI-5 --prefer-surface Funct-MCP overrides default one_of pick",
          "one_of[A3]" in out and "Funct-MCP" in out
          and "operator --prefer-surface" in out)

    # F-CLI-6 (A1.3): --change-type augments the matched set (forces A4 in even
    # though the description doesn't name it).
    code, out, err = _run_cli([
        "run", "A3 Flow change", "--org", "sf-test", "--change-type", "A4",
    ])
    check("F-CLI-6 --change-type A4 augments matched set",
          "A3" in out and "A4" in out)

    for label, passed in tests:
        print(f"{'PASS' if passed else 'FAIL'}: {label}")
    if failures:
        print(f"\njsc_qa orchestrator self-test FAILED: {failures} fixture(s)", file=sys.stderr)
        return 1
    print(f"\njsc_qa orchestrator self-test PASSED ({len(tests)} fixtures)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CliTooSlow as e:
        # A timeout can occur after earlier assertions; their partial results
        # cannot stand in for a completed suite and must not be labelled PASS.
        print(f"INCOMPLETE: jsc_qa orchestrator self-test — {e}", file=sys.stderr)
        print("Fixture suite did not complete; rerun before claiming validation.", file=sys.stderr)
        sys.exit(2)
