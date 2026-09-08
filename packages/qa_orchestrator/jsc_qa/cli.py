"""cli.py — `jsc-qa` orchestrator entry point.

Subcommands:
  jsc-qa run <change-description> --org <alias>     # the main entry point
  jsc-qa-token grant --skip-target X --reason Y --org Z
  jsc-qa-token show
  jsc-qa-token revoke

Reads packages/qa_orchestrator/qa-router.yaml. Honors JSC_QA_SKIP_TOKEN_PATH.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import dispatcher, qa_skip_token, report, router

# B1: consolidated production/sandbox classification. Benign sys.path bootstrap
# so a bare `PYTHONPATH=packages/qa_orchestrator` invocation still finds
# packages/jsc_common. QA consumer keeps this best-effort try/except.
try:
    from jsc_common.org_classify import is_production_target
except ImportError:  # pragma: no cover - bootstrap for bare PYTHONPATH
    import os as _os
    import sys as _sys
    _common = _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
        "jsc_common",
    )
    if _common not in _sys.path:
        _sys.path.insert(0, _common)
    from jsc_common.org_classify import is_production_target


# Documented default preference order for resolving one_of surface groups
# (A1.3). Funct-Pl (scripted Playwright, CI-friendly, repeatable) is preferred
# over Funct-MCP (token-heavy turn-by-turn) when both are offered. Operators
# override per-run via --prefer-surface (action="append").
ONE_OF_DEFAULT_PREFERENCE = ["Funct-Pl", "Funct-MCP", "Funct-Inspect"]


def cmd_run(args: argparse.Namespace) -> int:
    """`jsc-qa run <change-description> --org <alias>` — main entry point."""
    try:
        rt = router.load_router()
    except (FileNotFoundError, router.RouterValidationError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # Scored, ranked match (A1.2/A1.3) — preserves MULTI-change-type matching.
    # A deploy that touches N metadata types routes to N CT rows; we proceed with
    # the FULL matched set (union of surfaces), never collapsing to one winner.
    ranked = router.match_change_types_scored(args.description, rt)
    matches = [ct for ct, _score in ranked]

    # Operator can force/augment explicit change-type ids via --change-type
    # (action="append"). These are added to the matched set even if the NL
    # description didn't surface them.
    forced_ct_ids = list(dict.fromkeys(args.change_type or []))  # de-dup, keep order
    if forced_ct_ids:
        existing_ids = {ct["id"] for ct in matches}
        by_id = {c["id"]: c for c in rt["change_types"]}
        unknown_forced = []
        for cid in forced_ct_ids:
            cid_norm = cid.strip().upper()
            if cid_norm not in by_id:
                unknown_forced.append(cid)
                continue
            if cid_norm not in existing_ids:
                matches.append(by_id[cid_norm])
                existing_ids.add(cid_norm)
        if unknown_forced:
            print(f"warning: --change-type ids not in qa-router.yaml (ignored): "
                  f"{unknown_forced}", file=sys.stderr)

    if not matches:
        print(
            f"error: no change-types matched description: {args.description!r}\n"
            f"  Try referencing a change-type id directly (e.g., 'A3 Flow change')\n"
            f"  Or force one with --change-type A3\n"
            f"  Or check qa-router.yaml for taxonomy.",
            file=sys.stderr,
        )
        return 1

    # When multiple CTs match, print the ranked list so the operator sees WHY
    # each one routed + PROCEED with the full set (A1.3).
    if len(matches) > 1:
        score_by_id = {ct["id"]: sc for ct, sc in ranked}
        print(f"Matched {len(matches)} change-type(s) for {args.description!r} "
              f"(proceeding with the union of all surfaces):")
        for ct in matches:
            sc = score_by_id.get(ct["id"])
            origin = "score=%d" % sc if sc is not None else "forced via --change-type"
            print(f"  - {ct['id']}: {ct.get('name', '')} ({origin})")

    # Resolve org type. We use a simple heuristic for Phase 1A; Phase 1B+ uses
    # org_detect.resolve_org() (sf CLI shell-out) for production-vs-sandbox detection.
    is_production = _is_production_alias(args.org)

    # Compute union of required surfaces across all matched change-types
    all_surfaces_to_dispatch = {}  # surface_name → 'required_automated' (priority)
    one_of_groups = []  # list of (change_type_id, [surface_names]) — operator picks 1
    for ct in matches:
        grouped = router.required_surfaces(ct, is_production=is_production, router=rt)
        for s in grouped["required_automated"]:
            all_surfaces_to_dispatch[s] = "required_automated"
        for s in grouped["required_manual"]:
            if s not in all_surfaces_to_dispatch:
                all_surfaces_to_dispatch[s] = "required_manual"
        if grouped["one_of"]:
            one_of_groups.append((ct["id"], grouped["one_of"]))

    # Resolve each one_of group via an explicit, documented PREFERENCE ORDER
    # (A1.3) instead of a blind group[0] pick:
    #   1. operator --prefer-surface list (first listed member that's in the group)
    #   2. a surface already scheduled to dispatch (avoid spinning up a 2nd UI lane)
    #   3. ONE_OF_DEFAULT_PREFERENCE (framework default ordering)
    #   4. fallback to the group's first member (back-compat)
    operator_prefs = list(dict.fromkeys(args.prefer_surface or []))
    for ct_id, group in one_of_groups:
        # Skip if any member of this group is already covered.
        if any(g in all_surfaces_to_dispatch for g in group):
            continue
        chosen = None
        reason = ""
        for pref in operator_prefs:
            if pref in group:
                chosen, reason = pref, "operator --prefer-surface"
                break
        if chosen is None:
            for pref in ONE_OF_DEFAULT_PREFERENCE:
                if pref in group:
                    chosen, reason = pref, "framework default preference"
                    break
        if chosen is None:
            chosen, reason = group[0], "group order fallback (no preference matched)"
        all_surfaces_to_dispatch[chosen] = "one_of"
        print(f"one_of[{ct_id}]: chose {chosen!r} from {group} ({reason})")

    # Normal QA runs never consult ambient/global skip tokens. Legacy token
    # commands remain explicitly callable for old automation integrations.
    skipped_via_token = []
    skipped_change_types = []
    surfaces_to_actually_dispatch = list(all_surfaces_to_dispatch)


    # Dispatch each surface
    surface_results = []
    deploy_job_id = getattr(args, "deploy_job_id", None)
    deploy_components = getattr(args, "deploy_components", None)
    deploy_manifest = getattr(args, "deploy_manifest", None)
    for surface_name in surfaces_to_actually_dispatch:
        result = dispatcher.dispatch_surface(
            surface_name,
            args.org,
            args.description,
            deploy_job_id=deploy_job_id,
            deploy_components=deploy_components,
            deploy_manifest=deploy_manifest,
        )
        surface_results.append(result)

    # Print report
    print(report.format_report(
        change_types=matches,
        target_org=args.org,
        is_production=is_production,
        surface_results=surface_results,
        skipped_via_token=skipped_via_token,
    ))

    return report.result_exit_code(surface_results, bool(skipped_via_token))


def cmd_token_grant(args: argparse.Namespace) -> int:
    # Per audit gemini-R4: refuse to mint with placeholder org_id. The cmd_run
    # path strictly ignores tokens with unresolved orgs, so a placeholder
    # token would be silently useless. Better to fail loud here.
    org_id_18 = _get_org_id_18(args.org)
    if org_id_18 is None:
        print(f"error: cannot resolve org id for {args.org!r}", file=sys.stderr)
        print(f"  is sf CLI authenticated to that alias? Try: sf org display --target-org {args.org}",
              file=sys.stderr)
        print(f"  to skip live detect and mint with a placeholder anyway, set JSC_QA_TOKEN_ALLOW_PLACEHOLDER=1",
              file=sys.stderr)
        import os
        if os.environ.get("JSC_QA_TOKEN_ALLOW_PLACEHOLDER") != "1":
            return 1
        org_id_18 = "00DPP0000000000XXX"
    try:
        path = qa_skip_token.mint(
            operation_type=args.operation_type,
            org_id_18=org_id_18,
            skip_target=args.skip_target,
            reason=args.reason,
            expiry_seconds=args.expires_in * 60 if args.expires_in else None,
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"QA skip token minted at: {path}")
    print(f"  operation_type: {args.operation_type}")
    print(f"  org_id_18:      {org_id_18}")
    print(f"  skip_target:    {args.skip_target}")
    print(f"  reason:         {args.reason}")
    print(f"  expires:        {args.expires_in or 'default'} minutes")
    return 0


def cmd_token_show(args: argparse.Namespace) -> int:
    import json
    token = qa_skip_token.show()
    if token is None:
        print("No QA skip token present.")
        return 0
    print(json.dumps(token, indent=2))
    return 0


def cmd_token_revoke(args: argparse.Namespace) -> int:
    deleted = qa_skip_token.revoke()
    if deleted:
        print("QA skip token revoked.")
    else:
        print("No token to revoke.")
    return 0


def _is_production_alias(alias: str) -> bool:
    """Determine if alias points to a production org.

    Thin wrapper delegating to jsc_common's consolidated classifier under the
    `cli_live_first` policy (B1). That policy mirrors this surface's LIVE-FIRST
    behavior EXACTLY: live org_detect.resolve_org FIRST → production iff not
    sandbox; on live-query failure, alias-substring heuristic (endswith
    -prod/-production; the DEFAULT_PROD_ALIASES default-prod literal; `(^|-)prod`;
    then sandbox hints → False); AMBIGUOUS + live-failed → FAIL CLOSED to
    production unless JSC_QA_TRUST_ALIAS_SANDBOX=1. JSC_PROD_ALIASES /
    JSC_PROD_ORG_PATTERN honored first.

    (Per audit codex-R2-P1-3 + codex-R3-P1-13: an operator with sf CLI not yet
    authenticated who runs /qa against an unknown alias gets production-tier
    handling — the safer default.)
    """
    return is_production_target(alias, policy="cli_live_first")


def _get_org_id_18(target_org: str) -> str | None:
    """Best-effort org_id_18 resolution via sf CLI."""
    import json
    import subprocess
    try:
        proc = subprocess.run(
            ["sf", "org", "display", "--target-org", target_org, "--json"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout)
        org_id = data.get("result", {}).get("id", "")
        return org_id if len(org_id) == 18 else None
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        return None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jsc-qa", description="JSC QA Orchestrator")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run QA per the change-type router matrix")
    run.add_argument("description", help="natural-language change description (will be matched against change-types)")
    run.add_argument("--org", required=True, help="target org alias")
    run.add_argument("--change-type", action="append", metavar="ID",
                     help="force/augment an explicit change-type id (e.g. A3); "
                          "repeatable. Added to the NL-matched set.")
    run.add_argument("--prefer-surface", action="append", metavar="SURFACE",
                     help="preferred surface for resolving one_of groups (e.g. "
                          "Funct-MCP); repeatable, first match wins.")
    run.add_argument(
        "--deploy-job-id", "--job-id",
        dest="deploy_job_id",
        metavar="0Af...",
        help=(
            "exact Metadata API deploy or validation request ID. Required for "
            "the MetaAPI surface to PASS; there is no most-recent fallback."
        ),
    )
    run.add_argument(
        "--deploy-component",
        dest="deploy_components",
        action="append",
        metavar="TYPE:FULL_NAME",
        help=(
            "exact metadata artifact expected in the deploy report, such as "
            "Flow:Client_Intake; repeat for every artifact MetaAPI must prove."
        ),
    )
    run.add_argument(
        "--deploy-manifest",
        metavar="PACKAGE.XML",
        help=(
            "local package.xml used for the deploy or dry-run; exact members "
            "are unioned with --deploy-component values and the bytes are "
            "SHA-256 fingerprinted."
        ),
    )
    run.set_defaults(func=cmd_run)

    grant = sub.add_parser("token-grant", help="mint QA skip TTL token")
    grant.add_argument("--org", required=True)
    grant.add_argument("--skip-target", required=True,
                       help="change_type id (e.g. 'A3') OR surface name (e.g. 'Vision') OR command_fingerprint")
    grant.add_argument("--reason", required=True)
    grant.add_argument("--operation-type", default="skip_one_off",
                       choices=["skip_one_off", "skip_change_type", "skip_surface"])
    grant.add_argument("--expires-in", type=int, help="TTL in minutes (capped per op type)")
    grant.set_defaults(func=cmd_token_grant)

    show = sub.add_parser("token-show", help="print current QA skip token")
    show.set_defaults(func=cmd_token_show)

    revoke = sub.add_parser("token-revoke", help="revoke current QA skip token")
    revoke.set_defaults(func=cmd_token_revoke)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
