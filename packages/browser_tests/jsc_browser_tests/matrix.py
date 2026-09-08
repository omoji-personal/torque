"""matrix.py — expand flows into (flow, variation, profile) cells with an
applicability oracle. Serial scheduler only (parallelism deferred per spec §7.1).

Applicability rules:
  - A profile in flow.spec.profiles runs the flow's GENERAL variations
    (variations whose .profile is itself a declared profile).
  - A variation pinned to a profile NOT in spec.profiles (e.g. platform_denied)
    runs ONLY under that pinned profile.
  - A requested profile that is neither declared nor pinned by any variation
    yields a single NOT_APPLICABLE cell (variation=None), excluded from scoring.
"""
from __future__ import annotations

from dataclasses import dataclass

from .auth import login_as_user, observe_user_id, restore_original_user, same_user, AuthError
from .diagnostics import exception_detail


@dataclass
class Cell:
    flow: object
    variation: object  # None for a NOT_APPLICABLE profile cell
    profile: str
    applicable: bool


def expand_cells(flows, profiles) -> list[Cell]:
    cells: list[Cell] = []
    for flow in flows:
        declared = list(flow.spec.profiles)
        var_profiles = {v.profile for v in flow.spec.variations}
        for profile in profiles:
            if profile in declared:
                # general variations (pinned to a declared profile) run under this declared profile
                for v in flow.spec.variations:
                    if v.profile in declared:
                        cells.append(Cell(flow, v, profile, True))
            elif profile in var_profiles:
                # only variations pinned to this (non-declared) profile run here
                for v in flow.spec.variations:
                    if v.profile == profile:
                        cells.append(Cell(flow, v, profile, True))
            else:
                cells.append(Cell(flow, None, profile, False))
    return cells


async def login_as_preflight(page, seed, sf) -> dict:
    """Attempt Login-As + Logout-As once per declared non-admin user BEFORE the
    matrix runs. Any identity/restoration failure stops the preflight; subsequent
    cells must not run in an uncertain session.
    """
    display = sf.org_display()
    instance_url = display.get("instanceUrl", "")
    baseline = await observe_user_id(page)
    results: dict[str, str] = {}
    for role, user in (seed.get("users") or {}).items():
        if role == "admin":
            continue
        failure = None
        try:
            await login_as_user(page, instance_url, user.get("user_id"), org_id_18=display.get("id"))
            observed = await observe_user_id(page)
            if not same_user(observed, user.get("user_id")):
                raise AuthError("Login As did not establish the requested browser user")
            results[role] = "PASS"
        except Exception as exc:
            failure = exception_detail(exc)
        finally:
            try:
                restoration = await restore_original_user(page, instance_url, baseline)
                if restoration.get("action_warning"):
                    detail = "Logout As action reported an error, but the original browser user is now observed: " + restoration["action_warning"]
                    failure = (failure + "; " if failure else "") + detail
            except Exception as exc:
                failure = (failure + "; " if failure else "") + "Session restoration failed: " + exception_detail(exc)
        if failure:
            results[role] = "FAIL"
            results["preflight_error"] = failure
            break
    return results
