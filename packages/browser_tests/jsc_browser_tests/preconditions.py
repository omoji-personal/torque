"""preconditions.py — requires-token registry of checkable predicates."""
from __future__ import annotations
import os
import re


class UnknownToken(Exception):
    pass


def _npsp_installed(sf) -> tuple[bool, str]:
    # The InstalledSubscriberPackage SOQL is rejected on real orgs ("sObject type ...
    # is not supported"), so detection goes through `sf package installed list` via
    # SfClient.package_namespaces (closes the Task 1.10 LIVE-GAP). A lookup failure is
    # treated as "not detectable" (False) so the npsp_or_bucket_account OR still works.
    try:
        namespaces = sf.package_namespaces()
    except Exception as e:  # noqa: BLE001 - any CLI/parse failure → undetectable
        return False, f"npsp detection unavailable: {e}"
    ok = "npsp" in namespaces
    return ok, "npsp installed" if ok else "npsp not installed"


def _bucket_account_present(sf) -> tuple[bool, str]:
    rows = sf.query("SELECT Id FROM Account WHERE Name='Individual' LIMIT 1")
    ok = len(rows) > 0
    return ok, "bucket account present" if ok else "no Individual bucket account"


def _experience_cloud_enabled(sf) -> tuple[bool, str]:
    # When Experience Cloud is fully OFF, the Network object isn't queryable and the
    # SOQL errors ("sObject type 'Network' is not supported"). A predicate must never
    # raise — that would propagate past the precondition loop (which only catches
    # UnknownToken) and crash the cell as FAIL instead of SKIP. Treat a query error as
    # "not enabled" -> SKIP.
    try:
        rows = sf.query("SELECT Id FROM Network LIMIT 1")
    except Exception as e:  # noqa: BLE001
        return False, f"Experience Cloud not enabled (Network not queryable: {str(e)[:50]})"
    ok = len(rows) > 0
    return ok, "experience cloud enabled" if ok else "no Experience Cloud sites"


def _portal_community_user_present(sf) -> tuple[bool, str]:
    # The Pro Bono Portal flow (E7) is an EXTERNAL-facing accept-case action that must
    # run as a community/portal user — an internal/admin session has no "Accept Case"
    # control. A Network record alone (experience_cloud_enabled) is NOT sufficient; the
    # flow also needs an active portal user to impersonate. Absent one, SKIP (don't FAIL).
    try:
        rows = sf.query(
            "SELECT Id FROM User WHERE IsActive=true AND UserType IN "
            "('PowerCustomerSuccess','CustomerSuccess','CspLitePortal','PowerPartner') LIMIT 1")
    except Exception as e:  # noqa: BLE001 - a predicate must never raise -> treat as absent (SKIP)
        return False, f"could not confirm a portal/community user ({str(e)[:60]})"
    ok = len(rows) > 0
    return ok, "portal/community user present" if ok else "no active portal/community user to test as"


def _state_country_picklist(sf) -> tuple[bool, str]:
    # Heuristic: presence of MailingCountryCode field implies state/country picklists on.
    rows = sf.query("SELECT QualifiedApiName FROM FieldDefinition "
                    "WHERE EntityDefinition.QualifiedApiName='Contact' "
                    "AND QualifiedApiName='MailingCountryCode'")
    ok = len(rows) > 0
    return ok, "state/country picklists on" if ok else "state/country picklists off"


def _test_record_overlay(sf) -> tuple[bool, str]:
    # The suite's isolation + verification rest on the UNMANAGED Test_Record__c overlay
    # field being deployed and FLS-readable (it is NOT part of the JS01 managed package).
    # If the SOQL errors, the overlay is absent on this org and any "GREEN" would be
    # unsound -> the cell must SKIP rather than false-pass.
    try:
        object_name = os.environ.get("TORQUE_TEST_RECORD_OBJECT")
        field_name = os.environ.get("TORQUE_TEST_RECORD_FIELD", "Test_Record__c")
        if not object_name:
            return False, "Configure TORQUE_TEST_RECORD_OBJECT for this client flow"
        if not all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", v) for v in (object_name, field_name)):
            return False, "Invalid configured test-record API name"
        sf.query(f"SELECT {field_name} FROM {object_name} LIMIT 1")
        return True, "Test_Record__c overlay deployed + readable"
    except Exception as e:  # noqa: BLE001
        return False, f"Test_Record__c overlay missing (deploy standard-deployment overlay): {e}"


_PREDICATES = {
    "npsp_installed": _npsp_installed,
    "bucket_account_present": _bucket_account_present,
    "experience_cloud_enabled": _experience_cloud_enabled,
    "portal_community_user_present": _portal_community_user_present,
    "state_country_picklist": _state_country_picklist,
    "test_record_overlay": _test_record_overlay,
}
# Composites: explicit boolean semantics, never ad-hoc.
_COMPOSITES = {
    "npsp_or_bucket_account": ("OR", ["npsp_installed", "bucket_account_present"]),
}


def evaluate(token: str, sf) -> tuple[bool, str]:
    if token in _PREDICATES:
        return _PREDICATES[token](sf)
    if token in _COMPOSITES:
        op, parts = _COMPOSITES[token]
        results = [_PREDICATES[p](sf) for p in parts]
        if op == "OR":
            ok = any(r[0] for r in results)
        else:  # AND
            ok = all(r[0] for r in results)
        return ok, f"{op}({', '.join(f'{p}={r[0]}' for p, r in zip(parts, results))})"
    raise UnknownToken(f"unknown precondition token: {token!r}")
