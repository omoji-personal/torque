"""sf_client.py — the single org-I/O seam.

Every `sf` CLI touch in the suite goes through SfClient (real) so --self-test
can inject FakeSfClient and run offline. No module under the suite may call
subprocess.run(['sf', ...]) directly.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile


class SfError(Exception):
    """Raised when an sf CLI invocation fails."""


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
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            for i, ch in enumerate(stdout):
                if ch in "{[":
                    try:
                        return json.loads(stdout[i:])
                    except json.JSONDecodeError:
                        break
            raise SfError(f"sf {label} returned non-JSON: {stdout[:200]}")

    def _run(self, args: list[str], timeout_s: int | None = None) -> dict:
        proc = subprocess.run(
            ["sf", *args, "--target-org", self.target_org, "--json"],
            capture_output=True, text=True, timeout=timeout_s or self.timeout_s,
            env=_sf_env(),
        )
        if proc.returncode != 0:
            raise SfError(f"sf {' '.join(args)} exit {proc.returncode}: {proc.stderr[:300]}")
        return self._decode(proc.stdout, ' '.join(args))

    def _run_raw(self, args: list[str], timeout_s: int | None = None) -> dict:
        """Run an explicit `sf` arg list (no auto target-org/json appended) with
        the same return-code + JSON-decode guards as _run."""
        proc = subprocess.run(
            ["sf", *args],
            capture_output=True, text=True, timeout=timeout_s or self.timeout_s,
            env=_sf_env(),
        )
        if proc.returncode != 0:
            raise SfError(f"sf {' '.join(args)} exit {proc.returncode}: {proc.stderr[:300]}")
        return self._decode(proc.stdout, ' '.join(args))

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

    def org_frontdoor_url(self, target_org: str | None = None) -> str:
        org = target_org or self.target_org
        data = self._run_raw(["org", "open", "--target-org", org, "--url-only", "--json"])
        return data.get("result", {}).get("url", "")

    def org_display(self, target_org: str | None = None) -> dict:
        org = target_org or self.target_org
        data = self._run_raw(["org", "display", "--target-org", org, "--json"])
        return data.get("result", {})

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
                 org_display: dict | None = None, packages: list[str] | None = None):
        self._query_results = query_results or {}
        self._frontdoor_url = frontdoor_url
        self._org_display = org_display or {"id": "00Dfake0000000000", "instanceUrl": "https://fake.my.salesforce.com"}
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

    def org_frontdoor_url(self, target_org: str | None = None) -> str:
        return self._frontdoor_url

    def org_display(self, target_org: str | None = None) -> dict:
        return self._org_display
