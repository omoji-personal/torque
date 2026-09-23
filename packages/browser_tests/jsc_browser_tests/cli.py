"""cli.py — `jsc-browser` and `jsc-multiprofile` entry points.

Subcommands:
  jsc-browser-tests browser <flow_name> --target-org <alias>
  jsc-browser-tests multiprofile <flow_name> --target-org <alias>
  jsc-browser-tests sanitize-replay <script_path>

Auth: sf CLI frontdoor URL or explicitly configured CDP session, with Login As support.
Output: <selected-client>/state/qa-tests/<org>/<iso-ts>-<flow>/manifest.json
"""

from __future__ import annotations
from jsc_common.workspace import state_dir

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from . import auth, runner
from .suite import run_suite
from .diagnostics import artifact_child, redact, exception_detail


def _load_seed(client_alias: str) -> dict | None:
    """Load test-users.json seed with full schema validation.

    Per audit codex-R3-P1-17: schema validation (load → validate_seed_schema →
    alias-match) MUST run before profile dispatch. Live SOQL profile
    verification is optional (gated on sf CLI auth) — see validate_user_via_soql.
    """
    qa_orch_dir = Path(__file__).resolve().parents[2] / "qa_orchestrator"
    if str(qa_orch_dir) not in sys.path:
        sys.path.insert(0, str(qa_orch_dir))
    try:
        from jsc_qa import seed_validator  # type: ignore
        seed_path = seed_validator.seed_path_for(client_alias)
        if not seed_path.exists():
            return None
        seed = seed_validator.load_seed(seed_path)
        seed_validator.validate_seed_schema(seed)
        # alias-match
        if seed.get("alias") != client_alias:
            print(
                f"error: seed alias {seed.get('alias')!r} != requested {client_alias!r}",
                file=sys.stderr,
            )
            return None
        return seed
    except ImportError:
        return None
    except Exception as e:
        print(f"error: seed validation failed: {exception_detail(e)}", file=sys.stderr)
        # Fail closed — refuse to run multiprofile with an invalid seed
        return None


def cmd_browser(args: argparse.Namespace) -> int:
    """Single-profile (admin) browser walkthrough of a library flow."""
    flow = _load_library_flow(args.flow_name)
    if flow is None:
        print(f"error: no library flow named {args.flow_name!r}", file=sys.stderr)
        print(f"  Available: {_list_library_flows()}", file=sys.stderr)
        return 1
    return _run_flow_with_profiles(flow, args.target_org, profiles=["admin"], headed=args.headed,
                                   allow_production_writes=getattr(args, "allow_production_writes", False))


def cmd_multiprofile(args: argparse.Namespace) -> int:
    """Multi-profile UAT: walk flow as admin + each profile in test-users.json seed."""
    flow = _load_library_flow(args.flow_name)
    if flow is None:
        print(f"error: no library flow named {args.flow_name!r}", file=sys.stderr)
        print(f"  Available: {_list_library_flows()}", file=sys.stderr)
        return 1

    seed = _load_seed(args.target_org)
    if seed is None:
        print(f"error: test-users.json seed not found for {args.target_org}", file=sys.stderr)
        print(f"  Create: <selected-client>/config/test-users.json (select with TORQUE_TEST_USERS)", file=sys.stderr)
        return 1

    profiles = list(seed["users"].keys())
    if "admin" not in profiles:
        profiles.insert(0, "admin")  # always start with admin

    return _run_flow_with_profiles(
        flow, args.target_org, profiles=profiles,
        seed=seed, headed=args.headed,
        max_concurrency=args.max_concurrency,
        allow_production_writes=getattr(args, "allow_production_writes", False),
    )


def cmd_sanitize_replay(args: argparse.Namespace) -> int:
    """Scan a replay script for forbidden patterns (per design-v4 Closure 4)."""
    p = Path(args.script_path)
    if not p.exists():
        print(f"error: file not found: {p}", file=sys.stderr)
        return 1
    content = p.read_text()
    violations = auth.scan_replay_script(content)
    if violations:
        print(f"REJECTED: {p} contains {len(violations)} forbidden pattern(s):")
        for v in violations:
            print(f"  - {v}")
        return 2
    print(f"CLEAN: {p} — no forbidden patterns detected.")
    return 0


def _load_library_flow(flow_name: str):
    """Find a flow by spec.name (or name) across all recursively-discovered suite flows."""
    from .suite import discover_flows
    for f in discover_flows():
        if getattr(f.spec, "name", None) == flow_name or getattr(f, "name", None) == flow_name:
            return f
    return None


def _list_library_flows() -> list[str]:
    from .suite import discover_flows
    return sorted({getattr(f.spec, "name", None) or getattr(f, "name", "?")
                   for f in discover_flows()})


def _run_flow_with_profiles(
    flow, target_org: str, profiles: list[str],
    seed: dict | None = None,
    headed: bool = False,
    max_concurrency: int = 1,
    allow_production_writes: bool = False,
) -> int:
    """Use the same applicability, preflight, cleanup and scorer as suite-run."""
    from .sf_client import SfClient
    from .provisioning.fixtures import new_runid
    if max_concurrency > 1:
        print("Browser profile runs are serial to preserve session identity", file=sys.stderr)
    runid = new_runid()
    run_dir = artifact_child(state_dir("qa-tests"), target_org, f"{runid}-{flow.name}")
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    print(f"Run dir: {run_dir}")
    results = []
    def record(result):
        results.append(result)
        _print_flow_result(result)
    config = {
        "sf": SfClient(target_org), "target_org": target_org,
        "flows": [flow], "profiles": profiles, "seed": seed or {},
        "headed": headed, "allow_production_writes": allow_production_writes,
        "runid": runid, "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "manifest.json"),
        "on_result": record,
    }
    code = asyncio.run(run_suite(config))
    print(f"\nSummary: {len(results)} matrix cells, "
          f"{sum(r.overall_status == 'FAIL' for r in results)} FAIL; exit code {code}")
    return code


def _print_flow_result(result: runner.FlowResult) -> None:
    icon = "✓" if result.overall_status == "PASS" else "✗"
    print(f"  {icon} {result.flow_name} / {result.profile}: {result.overall_status} "
          f"({result.duration_seconds:.1f}s)")
    if result.error:
        print(f"    ERROR: {redact(result.error)}")
    for step in result.steps:
        sicon = {"PASS": "✓", "FAIL": "✗", "SKIP": "·"}.get(step.status, "?")
        fidelity_tag = "" if step.fidelity == "USER_FIDELITY" else f" [{step.fidelity}]"
        print(f"      {sicon} {step.step_name}: {step.status}{fidelity_tag} ({step.duration_seconds:.1f}s)")
        if step.detail and step.status != "PASS":
            print(f"        {redact(step.detail)[:200]}")


def _result_to_dict(r: runner.FlowResult) -> dict:
    return redact({
        "flow": r.flow_name, "profile": r.profile, "target_org": r.target_org,
        "status": r.overall_status, "duration_seconds": r.duration_seconds,
        "error": r.error,
        "steps": [
            {"name": s.step_name, "status": s.status, "fidelity": s.fidelity,
             "duration_seconds": s.duration_seconds, "detail": s.detail,
             "screenshot": s.screenshot_path}
            for s in r.steps
        ],
        "side_effects": r.side_effects,
    })


def cmd_suite_run(args: argparse.Namespace) -> int:
    """Full gold-standard suite run against an org."""
    import asyncio
    from .sf_client import SfClient
    from .provisioning.fixtures import new_runid
    runid = new_runid()
    base = args.run_dir or str(artifact_child(state_dir("qa-tests"), args.target_org))
    config = {
        "sf": SfClient(args.target_org),
        "target_org": args.target_org,
        "profiles": args.profiles.split(",") if args.profiles else ["admin"],
        "manifest_path": args.manifest or str(artifact_child(Path(base), runid) / "manifest.json"),
        "runid": runid,
        "report_md_path": args.report,
        "run_dir": base,
        "headed": args.headed,
        "allow_production_writes": args.allow_production_writes,
        "seed": _load_seed(args.target_org) or {},
    }
    ec = asyncio.run(run_suite(config))
    print(f"suite exit code: {ec}")
    return ec


def cmd_suite_teardown(args: argparse.Namespace) -> int:
    """Best-effort leak report: count TEST-<runid> survivors across carrier objects."""
    from .sf_client import SfClient
    from .provisioning.teardown import verify_zero_leak
    from .provisioning.object_registry import load_registry
    sf = SfClient(args.target_org)
    objs = [api for api, e in load_registry().items() if e.test_record_carrier]
    if not objs:
        print("Cleanup NOT_CHECKED: no test-record carriers are configured", file=sys.stderr)
        return 4
    leaks = verify_zero_leak(sf, args.runid, objs)
    print(f"leaks: {leaks}")
    return 0 if not leaks else 4


def cmd_suite_selftest(args: argparse.Namespace) -> int:
    """Run the suite's offline test files; aggregate exit code (mutation catalog in Task 6.5)."""
    import subprocess
    from pathlib import Path
    tests_dir = Path(__file__).resolve().parents[1] / "tests"
    if not tests_dir.exists() or not list(tests_dir.glob("test_*.py")):
        print("Offline tests are source-development assets; run the repository validation harness.", file=sys.stderr)
        return 2
    failed = 0
    # BT-1 (full-repo manual audit 2026-06-09): every subprocess.run must pass a
    # timeout + TimeoutExpired handling per .claude/rules/verify-harness-patterns.md
    # ("Subprocess timeouts (mandatory)"). Without it, a single hung offline test
    # file (unguarded network/browser call, infinite loop) hangs the whole
    # self-test runner with no actionable error. These are offline unit tests
    # that finish in seconds; 300s is a generous hang-detector, not a budget.
    timeout_s = int(os.environ.get("JSC_BROWSER_SELFTEST_TIMEOUT_S", "300"))
    for t in sorted(tests_dir.glob("test_*.py")):
        try:
            rc = subprocess.run(
                [sys.executable, str(t)],
                env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
                timeout=timeout_s,
            ).returncode
        except subprocess.TimeoutExpired:
            failed += 1
            print(
                f"FAILED (timeout >{timeout_s}s): {t.name} — test file hung; "
                f"check for an unguarded network/browser call or infinite loop "
                f"(override the limit via JSC_BROWSER_SELFTEST_TIMEOUT_S)",
                file=sys.stderr,
            )
            continue
        if rc != 0:
            failed += 1
            print(f"FAILED: {t.name}", file=sys.stderr)
    print(f"self-test: {failed} file(s) failed")
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("browser", help="single-profile (admin) browser walkthrough")
    b.add_argument("flow_name")
    b.add_argument("--target-org", required=True)
    b.add_argument("--headed", action="store_true", help="visible browser window")
    b.add_argument("--allow-production-writes", action="store_true", help="Run test-record mutations on an explicitly selected production org")
    b.set_defaults(func=cmd_browser)

    m = sub.add_parser("multiprofile", help="walk flow as admin + each non-admin profile")
    m.add_argument("flow_name")
    m.add_argument("--target-org", required=True)
    m.add_argument("--headed", action="store_true")
    m.add_argument("--max-concurrency", type=int, default=1,
                   help="compatibility option; browser profile execution remains serial")
    m.add_argument("--allow-production-writes", action="store_true", help="Run test-record mutations on an explicitly selected production org")
    m.set_defaults(func=cmd_multiprofile)

    s = sub.add_parser("sanitize-replay", help="scan a replay script for forbidden patterns")
    s.add_argument("script_path")
    s.set_defaults(func=cmd_sanitize_replay)

    sr = sub.add_parser("suite-run", help="run the full gold-standard suite")
    sr.add_argument("--target-org", required=True)
    sr.add_argument("--profiles", default="admin", help="comma-separated profiles")
    sr.add_argument("--manifest")
    sr.add_argument("--report")
    sr.add_argument("--run-dir")
    sr.add_argument("--allow-production-writes", action="store_true", help="Explicitly run test-record mutations on a known production target; no global token")
    sr.add_argument("--headed", action="store_true")
    sr.set_defaults(func=cmd_suite_run)

    st = sub.add_parser("suite-teardown", help="report TEST-<runid> leak survivors")
    st.add_argument("--target-org", required=True)
    st.add_argument("--runid", required=True)
    st.set_defaults(func=cmd_suite_teardown)

    ss = sub.add_parser("suite-self-test", help="run the suite's offline test files")
    ss.set_defaults(func=cmd_suite_selftest)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
