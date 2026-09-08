"""smoke_login — minimal flow that just authenticates + lands on home page.

Used for self-test + integration verification. No business logic; just proves:
  - sf CLI auth works
  - Playwright launches Chromium
  - frontdoor URL + Lightning home loads
This built-in flow applies only to admin. A multi-profile invocation does not
turn it into a non-admin access test; other roles are NOT_APPLICABLE.
"""

from __future__ import annotations

import time

from ..runner import BaseFlow, StepResult, Variation
from ..flow_spec import FlowSpec


class SmokeLoginFlow(BaseFlow):
    name = "smoke_login"
    spec = FlowSpec(
        name="smoke_login", workflow="E1", profiles=["admin"], writes=False,
        variations=[Variation("happy", expect="success")],
    )
    variations = [
        Variation(name="land_on_home", profile="admin"),
    ]

    async def run(self, page, ctx, variation):
        run_dir = ctx.run_dir
        steps = []

        # Step 1: verify the page reached Lightning Experience
        t0 = time.monotonic()
        try:
            await page.wait_for_url("**/lightning/**", timeout=20000)
            steps.append(StepResult(
                step_name="lightning_url_reached",
                status="PASS", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail=f"current URL: {page.url}",
            ))
        except Exception as e:
            steps.append(StepResult(
                step_name="lightning_url_reached",
                status="FAIL", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail=f"never reached /lightning/: {e}; current URL: {page.url}",
            ))
            return steps

        # Step 2: take a screenshot of the home page
        t1 = time.monotonic()
        screenshot_path = run_dir / f"smoke_{variation.profile}_home.png"
        try:
            await page.screenshot(path=str(screenshot_path), full_page=False)
            steps.append(StepResult(
                step_name="screenshot_home",
                status="PASS", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t1,
                screenshot_path=str(screenshot_path),
            ))
        except Exception as e:
            steps.append(StepResult(
                step_name="screenshot_home",
                status="FAIL", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t1,
                detail=str(e),
            ))

        return steps


FLOW = SmokeLoginFlow()
