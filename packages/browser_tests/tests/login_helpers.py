"""Shared fakes for the `torque browser login` tests: a Salesforce CLI double and a
runner that starts the command in a thread and talks to its local address.

Everything is synthetic. The only network use is numeric loopback."""
from __future__ import annotations

import http.client
import io
import json
import threading
import time

from jsc_browser_tests import cli, login
from jsc_browser_tests.sf_client import SfError

MARKER = "SENTINEL7f3aNEVERPRINT"
ORG = "00D000000000001AAA"
OTHER_ORG = "00D000000000009AAA"
ADDRESS = f"https://acme--sbx.sandbox.my.salesforce.com/secur/frontdoor.jsp?otp={MARKER}&startURL=%2F"
NAVIGATION = {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document", "Sec-Fetch-Site": "none"}


class FakeCli:
    """Stands in for SfClient. `orgs` maps an alias to what the CLI would answer:
    org_id, is_sandbox, username, url, and optional identity_error / mint_error /
    mint_seconds (how long the sign-in request takes) / organization_type / user_lookup_id."""
    orgs: dict = {}
    calls: list = []

    def __init__(self, alias, *args, **kwargs):
        self.alias = self.target_org = alias

    @classmethod
    def reset(cls, **orgs):
        cls.orgs = orgs or {"acme-sbx": {}}
        cls.calls = []

    def _org(self) -> dict:
        if self.alias not in self.orgs:
            raise SfError("sf data query exit 1 (NamedOrgNotFoundError): No authorization information found",
                          "not_authorized")
        return {"org_id": ORG, "is_sandbox": True, "username": "admin@acme.example", "url": ADDRESS,
                **self.orgs[self.alias]}

    def org_identity(self) -> dict:
        self.calls.append(("identity", self.alias))
        org = self._org()
        if org.get("identity_error"):
            raise org["identity_error"]
        return {"org_id": org["org_id"], "is_sandbox": org["is_sandbox"],
                "organization_type": org.get("organization_type", "Enterprise Edition")}

    def org_frontdoor(self, target_org=None, *, path=None, api_version=None) -> dict:
        self.calls.append(("mint", self.alias, path, api_version))
        org = self._org()
        if org.get("mint_error"):
            raise org["mint_error"]
        time.sleep(org.get("mint_seconds", 0))
        return {"url": org["url"], "org_id": org.get("mint_org_id", org["org_id"]), "username": org["username"]}

    def user_by_username(self, username):
        self.calls.append(("user", self.alias, username))
        return {"user_id": self.orgs.get(self.alias, {}).get("user_lookup_id", "005000000000001AAA"), "is_active": True}

    def find_user(self, value):
        self.calls.append(("find_user", self.alias, value))
        return self._org().get("users", {}).get(value)

    @classmethod
    def mints(cls) -> int:
        return sum(call[0] == "mint" for call in cls.calls)


class Command:
    """Run `login ARGS` in a thread; `address` waits for the local address it prints."""

    def __init__(self, *argv, wait: bool = True):
        self.out, self.err, self.code = io.StringIO(), io.StringIO(), None
        args = cli.build_parser().parse_args(["login", *argv])
        self.as_json = "--json" in argv

        def target():
            self.code = login.cmd_login(args, self.out, self.err)
        self.thread = threading.Thread(target=target, daemon=True)
        self.thread.start()
        self.address = self._address() if wait else None

    def _address(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for line in self.out.getvalue().splitlines():
                if self.as_json and line.startswith("{"):
                    event = json.loads(line)
                    if event.get("event") == "waiting":
                        return event["address"]
                elif line.startswith("http://127.0.0.1:"):
                    return line.strip()
            if not self.thread.is_alive():
                return None
            time.sleep(0.02)
        return None

    @property
    def port(self) -> int:
        return int(self.address.split(":")[2].split("/")[0])

    @property
    def code_path(self) -> str:
        return "/" + self.address.rsplit("/", 1)[1]

    def get(self, path=None, headers=None, method="GET", host=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        send = dict(NAVIGATION if headers is None else headers)
        if host:
            send["Host"] = host
        connection.request(method, self.code_path if path is None else path, headers=send)
        response = connection.getresponse()
        answer = (response.status, dict(response.getheaders()), response.read().decode("utf-8", "replace"))
        connection.close()
        return answer

    def done(self) -> int:
        self.thread.join(20)
        assert not self.thread.is_alive(), "the command did not end"
        return self.code

    def events(self) -> list[dict]:
        return [json.loads(line) for line in self.out.getvalue().splitlines() if line.startswith("{")]

    def everything(self) -> str:
        return self.out.getvalue() + self.err.getvalue()
