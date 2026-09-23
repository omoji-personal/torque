"""suite.py — top-level suite: flow discovery now; full orchestration in Phase 3."""
from __future__ import annotations
import os
import importlib.util
import hashlib

import importlib
import pkgutil
from pathlib import Path
from .diagnostics import exception_detail, redact, artifact_child


def discover_flows() -> list:
    """Discover generic built-ins plus explicitly configured trusted Python flows.

    TORQUE_BROWSER_FLOWS is an os.pathsep-separated list of files/directories.
    Those files are executable operator configuration; none are auto-discovered
    from client documents or downloaded content.
    """
    from .library.smoke_login import FLOW
    found = [FLOW]
    for item in filter(None, os.environ.get("TORQUE_BROWSER_FLOWS", "").split(os.pathsep)):
        root = Path(item).expanduser().resolve()
        paths = sorted(root.rglob("*.py")) if root.is_dir() else [root]
        for path in paths:
            if path.name.startswith("_"):
                continue
            if not path.is_file():
                raise ValueError(f"Configured browser flow does not exist: {path}")
            name = "torque_client_flow_" + hashlib.sha256(str(path).encode()).hexdigest()[:16]
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:
                raise ValueError(f"Cannot load browser flow: {path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            flow = getattr(module, "FLOW", None)
            if flow is None or getattr(flow, "spec", None) is None:
                raise ValueError(f"Configured browser flow must export FLOW with a FlowSpec: {path}")
            if any(f.spec.name == flow.spec.name for f in found):
                raise ValueError(f"Duplicate browser flow name: {flow.spec.name}")
            found.append(flow)
    return found


class WriteGateError(Exception):
    """Raised when the target org is not safe to write TEST records to."""


def resolve_write_gate(org_info, has_token: bool = False, trust_sandbox: bool = False,
                       *, allow_production_writes: bool = False) -> bool:
    """Explicit run-level production test-data choice; never reads a global token."""
    if org_info is None:
        raise WriteGateError("Cannot identify target org; reconnect the explicitly selected org.")
    kind = getattr(org_info, "detected_org_type", "unknown")
    if kind in ("sandbox", "developer"):
        return True
    if kind == "production" and allow_production_writes:
        return True
    raise WriteGateError("Test records require an identified sandbox/developer org or --allow-production-writes for an identified production org.")


def _resolve_org_lazy(target_org):
    """Resolve org type via the (separate) jsc_revert package; None if unavailable/offline."""
    try:
        from jsc_revert.org_detect import resolve_org
        return resolve_org(target_org)
    except Exception:
        return None


async def _live_cell_executor(cell, config):
    """Default executor: reuse run_flow_variation (owns browser+auth+cell_runner)."""
    import dataclasses
    from pathlib import Path
    from .runner import run_flow_variation
    var = dataclasses.replace(cell.variation, profile=cell.profile)
    seed = config.get("seed") or {}
    test_user = None
    if cell.profile != "admin":
        test_user = (seed.get("users") or {}).get(cell.profile)
    return await run_flow_variation(
        cell.flow, var, config["target_org"], Path(config["run_dir"]),
        test_user=test_user, headed=config.get("headed", False),
        sf_client=config["sf"], values=config.get("values") or {}, runid=config.get("runid"),
    )


async def _live_preflight(config):
    """Login-As/Logout-As smoke for each declared non-admin user, once, before the matrix."""
    profiles = config.get("profiles") or ["admin"]
    seed = config.get("seed") or {}
    needed = [p for p in profiles if p != "admin"]
    if not needed:
        return {}
    missing = [p for p in needed if not (seed.get("users") or {}).get(p, {}).get("user_id")]
    if missing:
        return {**{p: "FAIL" for p in missing}, "preflight_error": "Missing test user for requested profile(s)"}
    from playwright.async_api import async_playwright
    from . import auth
    from .matrix import login_as_preflight
    sf = config["sf"]
    admin_auth = auth.get_admin_auth(sf, config["target_org"])
    async with async_playwright() as pw:
        sess = await auth.open_session(pw, admin_auth,
                                       cdp_endpoint=auth.cdp_endpoint_from_env(),
                                       headed=config.get("headed", False))
        try:
            selected_seed = {"users": {p: seed["users"][p] for p in needed}}
            return await login_as_preflight(sess.page, selected_seed, sf)
        finally:
            await auth.close_session(sess)


async def run_suite(config) -> int:
    """End-to-end: write-gate -> expand cells -> run each (via cell_executor) ->
    score -> manifest/report -> exit code. Returns the exit code.
    """
    from pathlib import Path
    from .matrix import expand_cells
    from . import report, manifest
    from .runner import FlowResult

    sf = config["sf"]
    target_org = config.get("target_org") or getattr(sf, "target_org", None)
    if not target_org:
        raise ValueError("An explicit target org is required")

    flows = config.get("flows")
    if flows is None:
        flows = discover_flows()
    if not flows:
        return 2  # no tests is not a successful suite
    requires_writes = any(getattr(f.spec, "writes", True) for f in flows)
    org_info = config.get("org_info")
    if requires_writes and "org_info" not in config:
        org_info = _resolve_org_lazy(target_org)
    try:
        write_gate_ok = (not requires_writes) or resolve_write_gate(org_info, allow_production_writes=config.get("allow_production_writes", False))
    except WriteGateError:
        write_gate_ok = False

    profiles = config.get("profiles") or ["admin"]
    results: list = []
    preflight_results: dict = {}
    survivors: dict = {}
    cleanup_status = "NOT_CHECKED" if requires_writes else "NOT_APPLICABLE"
    cleanup_detail = "No mutation declared" if not requires_writes else "Run did not reach cleanup verification"
    from .provisioning.fixtures import new_runid
    runid = config.get("runid") or new_runid()

    if write_gate_ok:
        from .provisioning.object_registry import load_registry
        from .provisioning.teardown import verify_zero_leak
        base = Path(config.get("run_dir") or ".")
        run_dir = artifact_child(base, runid)
        run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        cell_config = {**config, "run_dir": str(run_dir), "runid": runid}
        # Preserve the record-name prefix independently of the encoded artifact path.
        cells = expand_cells(flows, profiles)
        cell_config["profiles"] = list(dict.fromkeys(c.profile for c in cells if c.applicable))

        preflight = config.get("preflight", _live_preflight)
        try:
            preflight_results = await preflight(cell_config) or {}
        except Exception as e:
            preflight_results = {"preflight_error": exception_detail(e)}
        preflight_failed = bool(preflight_results.get("preflight_error")) or any(
            value != "PASS" for key, value in preflight_results.items() if key != "preflight_error")
        stopped = preflight_failed
        if preflight_failed:
            cleanup_status = "NOT_APPLICABLE"
            cleanup_detail = "No flow executed because browser-session preflight failed"

        executor = config.get("cell_executor") or _live_cell_executor
        for cell in cells:
            if not cell.applicable:
                not_applicable = FlowResult(flow_name=cell.flow.spec.name, profile=cell.profile,
                                            target_org=target_org, overall_status="NOT_APPLICABLE")
                results.append(not_applicable)
                if config.get("on_result"):
                    config["on_result"](not_applicable)
                continue
            if stopped:
                fr = FlowResult(flow_name=cell.flow.spec.name, profile=cell.profile,
                                target_org=target_org, overall_status="INCOMPLETE",
                                error="Not executed: browser preflight or session restoration failed")
            else:
                try:
                    fr = await executor(cell, cell_config)
                except Exception as exc:
                    fr = FlowResult(flow_name=cell.flow.spec.name, profile=cell.profile,
                                    target_org=target_org, overall_status="FAIL",
                                    error=exception_detail(exc))
            fr.side_effects = fr.side_effects or {}
            fr.side_effects["is_happy"] = (getattr(cell.variation, "expect", "success") == "success")
            fr.side_effects["is_crud"] = (cell.flow.spec.workflow == "CRUD")
            results.append(fr)
            stopped = stopped or bool(fr.side_effects.get("stop_suite"))
            if config.get("on_result"):
                config["on_result"](fr)

        # post-run zero-leak verification: TEST-<runid> carrier survivors == teardown leak
        try:
            carriers = [api for api, e in load_registry(config.get("registry_path")).items() if e.test_record_carrier]
            if carriers and not preflight_failed:
                survivors = verify_zero_leak(sf, runid, carriers)
                cleanup_status = "LEAK_OBSERVED" if survivors else "OBSERVED_CLEAR"
                cleanup_detail = f"Queried {len(carriers)} configured objects by this run's name prefix; no claim about objects outside that scope."
            elif requires_writes and not preflight_failed:
                cleanup_status = "NOT_CHECKED"
                cleanup_detail = "No test-record registry supplied for this mutating flow; cleanup has not been verified."
        except Exception as exc:
            cleanup_status = "UNKNOWN"
            cleanup_detail = f"Cleanup verification failed: {type(exc).__name__}"

    score = report.score_run(results)
    teardown_leak = (any((r.side_effects or {}).get("teardown_error") for r in results)
                     or bool(survivors)
                     or cleanup_status in ("UNKNOWN", "NOT_CHECKED"))

    mpath = config.get("manifest_path")
    if mpath:
        manifest.write(mpath, results, score, config.get("audit_entries") or [],
                       audit_log=config.get("audit_log"),
                       extra={"target_org": target_org, "runid": runid,
                              "preflight": redact(preflight_results), "leak_survivors": survivors,
                              "cleanup_status": cleanup_status, "cleanup_detail": cleanup_detail})
    md_path = config.get("report_md_path")
    if md_path:
        Path(md_path).write_text(report.render_md(results, score) + f"\nCleanup: {cleanup_status}. {cleanup_detail}\n", encoding="utf-8")

    return report.exit_code(score, write_gate_ok=write_gate_ok, teardown_leak=teardown_leak)
