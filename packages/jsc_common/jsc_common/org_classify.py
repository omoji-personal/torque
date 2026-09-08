"""Compatibility interface for authoritative org classification.

Both live policies use the resolved Organization properties. Legacy alias and
environment arguments remain accepted, but cannot establish nonproduction
status or override available authority. The retired string-only hook policy
never shells out and treats its unknown identity as production-like.
"""
from __future__ import annotations

import os

DEFAULT_PROD_ALIASES = []


def is_production_target(alias: str, *, policy: str, env=os.environ,
                         live_resolver=None, prod_aliases=None, prod_pattern=None) -> bool:
    """Return production-like True when authority is unavailable.

    An injected live_resolver returns True/False/None and must itself resolve
    actual org properties. It remains a seam for callers and offline tests.
    This result describes a target; it does not authorize any operation.
    """
    if policy == "hook_prod_like_on_unknown":
        return True
    if policy not in ("cli_live_first", "dispatcher_alias_first"):
        raise ValueError(f"unknown policy: {policy!r}")
    try:
        if live_resolver is not None:
            verdict = live_resolver(alias)
        elif env.get("JSC_VISION_SKIP_LIVE_ORG_DETECT") == "1":
            verdict = None  # Retained offline seam; unknown still cannot grant a bypass.
        else:
            from jsc_revert.org_detect import resolve_org
            info = resolve_org(alias, timeout_seconds=10)
            verdict = None if info is None else info.is_production
    except Exception:
        verdict = None
    return False if verdict is False else True


def _dispatcher_live_is_production(alias: str, env) -> bool | None:
    """Compatibility seam for callers needing an explicitly unknown result."""
    if env.get("JSC_VISION_SKIP_LIVE_ORG_DETECT") == "1":
        return None
    try:
        from jsc_revert.org_detect import resolve_org
        info = resolve_org(alias)
        return None if info is None else info.is_production
    except Exception:
        return None
