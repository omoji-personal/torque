"""sf_client.py — the single org-I/O seam.

Every `sf` CLI touch in the suite goes through SfClient (real) so --self-test
can inject FakeSfClient and run offline. No module under the suite may call
subprocess.run(['sf', ...]) directly.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from .diagnostics import redact

try:  # the shared launcher finds sf where Windows installs it as a batch file
    from jsc_common.tools import run as run_tool
except ImportError:
    run_tool = subprocess.run

try:
    from jsc_common.soql import soql_literal
except ImportError:  # the same escapes, for a bare package checkout
    def soql_literal(value: str) -> str:
        escapes = {'\\': '\\\\', "'": "\\'", '\n': '\\n', '\r': '\\r', '\t': '\\t', '\b': '\\b', '\f': '\\f'}
        return ''.join(escapes.get(char, char) for char in value)


class SfError(Exception):
    """Raised when an sf CLI invocation fails. `kind` says why, so a caller can tell the
    user what to do next: "cli_missing", "not_authorized" (the alias has no authorization
    or it expired), "api_only" (the user cannot have a browser session) or "other".
    The message is always redacted."""

    def __init__(self, message, kind: str = "other"):
        super().__init__(redact(message))
        self.kind = kind


# How the CLI names a missing or expired authorization, and an API-only user at the
# single-use sign-in endpoint. Matched on the error's name and message together.
_NOT_AUTHORIZED = re.compile(
    r"NamedOrgNotFound|NoOrgFound|No authorization information|AuthInfoCreation|RefreshTokenAuth|"
    r"expired access/refresh token|invalid_grant|INVALID_SESSION_ID|Session expired or invalid|"
    r"NoAuthInfoFound|not been authenticated|inactive user|INVALID_LOGIN", re.IGNORECASE)
_API_ONLY = re.compile(r"Bad_OAuth_Token|API[ _]?Only", re.IGNORECASE)


def _failure(label: str, returncode: int, stdout: str, stderr: str) -> SfError:
    """The error for a failed call, classified from the CLI's own JSON error when it
    printed one. Only the error's name and a short redacted message are kept."""
    name = message = ""
    try:
        data = SfClient._decode(stdout or "", label)
        name = str(data.get("name") or "")
        message = str(data.get("message") or "")
        extra = json.dumps(data.get("data"), default=str) if data.get("data") is not None else ""
    except SfError:
        extra = ""
    seen = " ".join((name, message, extra, stderr or ""))
    kind = "api_only" if _API_ONLY.search(seen) else "not_authorized" if _NOT_AUTHORIZED.search(seen) else "other"
    detail = redact(message or stderr or "")[:300]
    return SfError(f"sf {label} exit {returncode}{' (' + name + ')' if name else ''}: {detail}", kind)


# Suppress the sf CLI "update available" notice so it doesn't pollute --json stdout.
def _sf_env():
    return {**os.environ, "SF_AUTOUPDATE_DISABLE": "true"}


class SfClient:
    """Real sf-CLI-backed client. Methods shell out to `sf`."""

    def __init__(self, target_org: str, timeout_s: int = 60):
        if not target_org or not isinstance(target_org, str):
            raise ValueError("An explicit target org is required")
        self.target_org = target_org
        self.timeout_s = timeout_s

    @staticmethod
    def _decode(stdout: str, label: str) -> dict:
        """Parse sf --json output, tolerating a leading CLI banner (some sf
        versions print an 'update available' notice to stdout before the JSON)."""
        data = None
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            for i, ch in enumerate(stdout):
                if ch in "{[":
                    try:
                        data = json.loads(stdout[i:])
                        break
                    except json.JSONDecodeError:
                        break
        if not isinstance(data, dict):
            raise SfError(f"sf {label} returned invalid structured output: {redact(stdout)[:200]}")
        return data

    def _run(self, args: list[str], timeout_s: int | None = None) -> dict:
        return self._run_raw([*args, "--target-org", self.target_org, "--json"], timeout_s)

    def _run_raw(self, args: list[str], timeout_s: int | None = None, env: dict | None = None) -> dict:
        """Run an explicit `sf` arg list (no auto target-org/json appended) with
        the same return-code + JSON-decode guards as _run. The arguments go to the
        CLI as a list through the shared launcher, never through a shell."""
        label = " ".join(args[:3])
        seconds = timeout_s or self.timeout_s
        try:
            proc = run_tool(
                ["sf", *args], capture_output=True, text=True, encoding="utf-8", errors="strict",
                timeout=seconds, env=env or _sf_env())
        except UnicodeDecodeError:
            raise SfError("sf output was not valid UTF-8; operation incomplete. Inspect the target before retrying a write.") from None
        except FileNotFoundError:
            raise SfError("the Salesforce CLI (sf) was not found on PATH", "cli_missing") from None
        except subprocess.TimeoutExpired:
            raise SfError(f"sf {label} did not answer within {seconds} seconds") from None
        except OSError as exc:
            # The exception's text can repeat the command line, so only its type is kept.
            raise SfError(f"sf {label} could not be started ({type(exc).__name__})") from None
        if proc.returncode != 0:
            raise _failure(label, proc.returncode, getattr(proc, "stdout", "") or "", getattr(proc, "stderr", "") or "")
        return self._decode(proc.stdout, label)

    def query(self, soql: str, all_rows: bool = False) -> list[dict]:
        args = ["data", "query", "--query", soql]
        if all_rows:
            args.append("--all-rows")  # surfaces recycle-bin rows for leak checks
        return self._run(args).get("result", {}).get("records", [])

    def apex_run(self, apex: str) -> dict:
        # sf apex run reads from --file; write to a temp file
        fd, path = tempfile.mkstemp(suffix=".apex")
        try:
            os.write(fd, apex.encode()); os.close(fd)
            return self._run(["apex", "run", "--file", path])
        finally:
            os.unlink(path)

    def data_delete(self, sobject: str, record_id: str) -> dict:
        return self._run(["data", "delete", "record", "--sobject", sobject,
                          "--record-id", record_id])

    def org_frontdoor(self, target_org: str | None = None, *, path: str | None = None,
                      api_version: str | None = None) -> dict:
        """Ask the CLI for a single-use sign-in address: {"url", "org_id", "username"}.

        The address signs a browser in as the alias's user. It is valid once and for
        about a minute, so ask at the moment of navigation and never keep, print or log
        it. The same answer names the org and the user it is for, which is why no
        `sf org display` call is needed (older CLIs print the access token there)."""
        org = target_org or self.target_org
        args = ["org", "open", "--target-org", org, "--url-only", "--json"]
        env = _sf_env()
        # The CLI reads this variable as --path; only an explicit path is used here.
        env.pop("FORCE_OPEN_URL", None)
        if path:
            args += ["--path", path]
        if api_version:
            # After a sandbox refresh the CLI's cached API version can be wrong; the
            # variable covers the connection it builds before it reads the option.
            args += ["--api-version", api_version]
            env["SF_ORG_API_VERSION"] = api_version
        result = self._run_raw(args, env=env).get("result")
        result = result if isinstance(result, dict) else {}
        return {"url": result.get("url") or "", "org_id": result.get("orgId") or "",
                "username": result.get("username") or ""}

    def org_frontdoor_url(self, target_org: str | None = None) -> str:
        return self.org_frontdoor(target_org)["url"]

    def org_identity(self) -> dict:
        """Which org the alias points to, by the query that prints no credential:
        {"org_id", "is_sandbox", "organization_type"}. is_sandbox is None when the org
        did not say."""
        rows = self.query("SELECT Id, IsSandbox, OrganizationType FROM Organization")
        row = rows[0] if rows and isinstance(rows[0], dict) else {}
        org_id = row.get("Id") if isinstance(row.get("Id"), str) else ""
        if not re.fullmatch(r"00D[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?", org_id):
            raise SfError(f"the Organization query for {self.target_org} returned no org ID")
        sandbox = row.get("IsSandbox")
        return {"org_id": org_id, "is_sandbox": sandbox if isinstance(sandbox, bool) else None,
                "organization_type": row.get("OrganizationType") if isinstance(row.get("OrganizationType"), str) else ""}

    def user_by_username(self, username: str) -> dict | None:
        """{"user_id", "is_active"} for a username in this org, or None when there is none."""
        rows = self.query(f"SELECT Id, IsActive FROM User WHERE Username = '{soql_literal(username)}'")
        row = rows[0] if rows and isinstance(rows[0], dict) else None
        if not row or not isinstance(row.get("Id"), str):
            return None
        return {"user_id": row["Id"], "is_active": row.get("IsActive") is not False}

    def package_namespaces(self, target_org: str | None = None) -> list[str]:
        """Installed package namespaces via the sanctioned `sf package installed list`.

        The `InstalledSubscriberPackage` SOQL is rejected on real orgs ("sObject type
        ... is not supported"), so npsp/managed-package detection MUST go through this.
        """
        org = target_org or self.target_org
        data = self._run_raw(["package", "installed", "list", "--target-org", org, "--json"])
        rows = data.get("result", []) or []
        return [r.get("SubscriberPackageNamespace") for r in rows
                if r.get("SubscriberPackageNamespace")]


class FakeSfClient:
    """Offline test double. Returns canned results; records mutations."""

    def __init__(self, *, query_results: dict[str, list[dict]] | None = None,
                 frontdoor_url: str = "https://fake.my.salesforce.com/secur/frontdoor.jsp?sid=FAKE",
                 org_id: str = "00Dfake00000000AAA", username: str = "admin@fake.example",
                 is_sandbox: bool | None = True, packages: list[str] | None = None):
        self._query_results = query_results or {}
        self._frontdoor_url = frontdoor_url
        self._org_id, self._username, self._is_sandbox = org_id, username, is_sandbox
        self._packages = packages or []
        self.apex_calls: list[str] = []
        self.deleted: list[tuple[str, str]] = []
        self.target_org = "sf-fake"

    def query(self, soql: str, all_rows: bool = False) -> list[dict]:
        return self._query_results.get(soql, [])

    def package_namespaces(self, target_org: str | None = None) -> list[str]:
        return list(self._packages)

    def apex_run(self, apex: str) -> dict:
        self.apex_calls.append(apex)
        return {"result": {"success": True}}

    def data_delete(self, sobject: str, record_id: str) -> dict:
        self.deleted.append((sobject, record_id))
        return {"result": {"success": True}}

    def org_frontdoor(self, target_org: str | None = None, *, path: str | None = None,
                      api_version: str | None = None) -> dict:
        return {"url": self._frontdoor_url, "org_id": self._org_id, "username": self._username}

    def org_frontdoor_url(self, target_org: str | None = None) -> str:
        return self._frontdoor_url

    def org_identity(self) -> dict:
        return {"org_id": self._org_id, "is_sandbox": self._is_sandbox, "organization_type": "Developer Edition"}

    def user_by_username(self, username: str) -> dict | None:
        return {"user_id": "005fake00000000AAA", "is_active": True} if username == self._username else None
