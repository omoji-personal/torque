"""Targeted recheck (R2f): D2 resolver exceptions and N7 per-request authorization.
Written failing first."""
import asyncio
import time
from types import SimpleNamespace

import pytest

from torque import browser_guard as bg

PROD = bg.Guard(org_alias="acme-prod", org_id_18="00D000000000002AAA", host_key=("acme", "prod"))
SBX = bg.Guard(org_alias="acme-sbx", org_id_18="00D000000000001AAA", host_key=("acme--sbx", "sandbox"))


def excluded(guard):
    return [rule.split(" ", 1)[1] for rule in bg.resolver_rules(guard).split(", ") if rule.startswith("EXCLUDE ")]


def matches(pattern, host):
    import fnmatch
    return fnmatch.fnmatchcase(host, pattern)


@pytest.mark.parametrize("guard,other", [
    (PROD, "acme--sbx--c.sandbox.vf.force.com"), (PROD, "acme--sbx.sandbox.my.site.com"),
    (PROD, "acme--sbx--c.sandbox.file.force.com"), (PROD, "acme--sbx.sandbox.my.salesforce.com"),
    (PROD, "acme--uat--c.sandbox.vf.force.com"), (PROD, "acme--sbx--c.sandbox.documentforce.com"),
    (SBX, "acme--c.vf.force.com"), (SBX, "acme.my.site.com"), (SBX, "acme--uat--c.sandbox.vf.force.com"),
    (SBX, "acme--sbxother--c.sandbox.vf.force.com"), (SBX, "acme.my.salesforce.com"),
])
def test_d2_no_exception_reaches_another_org(guard, other):
    assert not any(matches(p, other) for p in excluded(guard)), (other, excluded(guard))


@pytest.mark.parametrize("guard,own", [
    (PROD, "acme.my.salesforce.com"), (PROD, "acme.lightning.force.com"), (PROD, "acme--c.vf.force.com"),
    (PROD, "acme.my.salesforce-setup.com"), (PROD, "acme.my.site.com"),
    (SBX, "acme--sbx.sandbox.my.salesforce.com"), (SBX, "acme--sbx.sandbox.lightning.force.com"),
    (SBX, "acme--sbx--c.sandbox.vf.force.com"), (SBX, "acme--sbx.sandbox.my.site.com"),
])
def test_d2_the_org_own_hosts_stay_reachable(guard, own):
    assert any(matches(p, own) for p in excluded(guard)), (own, excluded(guard))


def test_d2_exceptions_have_no_wildcards():
    for guard in (PROD, SBX):
        assert not [p for p in excluded(guard) if "*" in p]


class Route:
    # Torque sends each request itself (route.fetch) and fulfills the response.
    async def fetch(self, **kw):
        from types import SimpleNamespace
        return SimpleNamespace(status=200, headers={})

    async def fulfill(self, **kw):
        self.result = "continue"

    result = None

    async def continue_(self):
        self.result = "continue"

    async def abort(self, reason=None):
        self.result = "abort"


class Context:
    handler = None

    async def route(self, pattern, handler):
        self.handler = handler

    async def close(self):
        pass


def test_n7_a_write_right_after_suspension_is_refused():
    state = {"ok": True}
    guard = bg.Guard(org_alias="acme-sbx", org_id_18="00D000000000001AAA", host_key=("acme--sbx", "sandbox"),
                     expires_at=time.time() + 600, recheck=lambda: state["ok"])  # default recheck interval

    async def main():
        context = Context()
        await bg.install(context, guard)
        url = "https://acme--sbx.sandbox.lightning.force.com/aura"
        first, second = Route(), Route()
        await context.handler(first, SimpleNamespace(url=url, method="POST"))
        state["ok"] = False
        await context.handler(second, SimpleNamespace(url=url, method="POST"))
        return first.result, second.result
    assert asyncio.run(main()) == ("continue", "abort")


def test_n7_every_write_rechecks():
    calls = []
    guard = bg.Guard(org_alias="acme-sbx", org_id_18="00D000000000001AAA", host_key=("acme--sbx", "sandbox"),
                     expires_at=time.time() + 600, recheck=lambda: calls.append(1) or True)

    async def main():
        context = Context()
        await bg.install(context, guard)
        for _ in range(3):
            await context.handler(Route(), SimpleNamespace(url="https://acme--sbx.sandbox.lightning.force.com/a",
                                                           method="POST"))
    asyncio.run(main())
    assert len(calls) == 3
