"""Resolve org identity from Salesforce's authoritative Organization record.

Aliases, login URLs and environment hints are not evidence of org type. A
Developer Edition is nonproduction, but its actual IsSandbox can be false.
This module remains importable with only the revert package on sys.path.
"""
from __future__ import annotations

import json
import re
import subprocess
from typing import NamedTuple


class OrgInfo(NamedTuple):
    alias: str
    org_id_18: str
    org_id_short: str
    is_sandbox: bool  # Actual Organization.IsSandbox, not a policy shortcut.
    instance_url: str
    login_url: str
    detected_org_type: str
    organization_type: str = ""
    identity_source: str = ""

    @property
    def is_nonproduction(self) -> bool:
        return self.detected_org_type in ("sandbox", "developer")

    @property
    def is_production(self) -> bool:
        return not self.is_nonproduction


NON_PROD_EDITIONS = {"Developer Edition"}
_ORG_ID = re.compile(r"00D[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?\Z")
_ORG_QUERY = "SELECT Id, IsSandbox, OrganizationType FROM Organization"


def _sf_result(command: list[str], timeout_seconds: int) -> dict | None:
    try:
        proc = subprocess.run(command, capture_output=True, text=True,
                              timeout=timeout_seconds)
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, OSError, ValueError, TypeError):
        return None
    if (not isinstance(data, dict) or type(data.get("status")) is not int
            or data["status"] != 0 or not isinstance(data.get("result"), dict)):
        return None
    return data["result"]


def _valid_id(value) -> bool:
    return isinstance(value, str) and bool(_ORG_ID.fullmatch(value))


def resolve_org(target_org: str, timeout_seconds: int = 10) -> OrgInfo | None:
    """Resolve the explicit target; unavailable or conflicting authority is unknown.

    Org display supplies the instance URL and authenticated org ID. The bounded
    Organization query supplies the actual org properties and must match that
    ID. Auth material from org display is never returned, printed or persisted.
    Unknown identity returns None before wrappers can submit their mutation.
    """
    if (not isinstance(target_org, str) or not target_org.strip()
            or target_org.startswith("-") or any(ord(c) < 32 for c in target_org)):
        return None
    display = _sf_result(["sf", "org", "display", "--target-org", target_org, "--json"],
                         timeout_seconds)
    if display is None or not _valid_id(display.get("id")):
        return None
    authority = _sf_result(["sf", "data", "query", "--target-org", target_org,
                            "--query", _ORG_QUERY, "--json"], timeout_seconds)
    if authority is None:
        return None
    rows = authority.get("records")
    if (authority.get("done") is not True or type(authority.get("totalSize")) is not int
            or authority["totalSize"] != 1 or not isinstance(rows, list)
            or len(rows) != 1 or not isinstance(rows[0], dict)):
        return None
    row = rows[0]
    org_id = row.get("Id")
    sandbox = row.get("IsSandbox")
    edition = row.get("OrganizationType")
    if (not _valid_id(org_id) or org_id[:15] != display["id"][:15]
            or type(sandbox) is not bool or not isinstance(edition, str)
            or not edition.strip()):
        return None
    instance_url = display.get("instanceUrl", "")
    login_url = display.get("loginUrl", "")
    if not isinstance(instance_url, str) or not isinstance(login_url, str):
        return None
    org_id_18 = org_id if len(org_id) == 18 else _pad_to_18(org_id)
    return OrgInfo(
        alias=target_org, org_id_18=org_id_18, org_id_short=org_id_18[:15],
        is_sandbox=sandbox, instance_url=instance_url, login_url=login_url,
        detected_org_type=classify_org_type(target_org, org_type=edition,
                                             is_sandbox=sandbox),
        organization_type=edition, identity_source="Organization query",
    )


def classify_org_type(alias: str, instance_url: str = "", login_url: str = "", *,
                      org_type: str | None = None, is_sandbox: bool | None = None) -> str:
    """Classify supplied authoritative properties; unknown is production-like.

    The positional arguments remain for compatibility. They never establish
    nonproduction status. Call resolve_org for a live classification.
    """
    if type(is_sandbox) is bool and is_sandbox:
        return "sandbox"
    if (is_sandbox is False and isinstance(org_type, str)
            and org_type in NON_PROD_EDITIONS):
        return "developer"
    return "production"


def is_production_org(alias: str, instance_url: str = "", login_url: str = "", *,
                      org_type: str | None = None, is_sandbox: bool | None = None) -> bool:
    return classify_org_type(alias, instance_url, login_url,
                             org_type=org_type, is_sandbox=is_sandbox) == "production"


def _pad_to_18(id15: str) -> str:
    """Append Salesforce's case checksum to a 15-character ID."""
    if len(id15) != 15:
        return id15
    suffix = ""
    for start in (0, 5, 10):
        bits = sum(1 << i for i, char in enumerate(id15[start:start + 5]) if char.isupper())
        suffix += "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"[bits]
    return id15 + suffix
