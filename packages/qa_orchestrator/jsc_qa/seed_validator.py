"""seed_validator.py — test-user seed file validation (select with TORQUE_TEST_USERS).

Closes Codex-R1-P1-1 (auth + test-user security contract).

Hard rules:
- Seed file MUST NOT contain: passwords, session_id, access_token, cookies,
  frontdoor URLs, refresh tokens
- File mode MUST be 0o600
- File MUST be owned by current uid
- For each user entry: validate via SOQL that User is Active, Profile matches
  expected_profile, License matches, expected_permission_sets are assigned,
  + refuse System Administrator profile / ModifyAllData / ViewAllData /
  ManageUsers / BulkApiHardDelete for non-admin users
- For production target: allowed_in_prod=true required
"""

from __future__ import annotations
from jsc_common.workspace import private_config

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


# Forbidden field names (case-insensitive substring match) — (select with TORQUE_TEST_USERS)
FORBIDDEN_FIELD_PATTERNS = re.compile(
    r"password|session_id|sessionid|access_token|accesstoken|"
    r"cookie|frontdoor|refresh_token|refreshtoken|sid|oauth_token|"
    r"bearer|authorization",
    re.IGNORECASE,
)

# Secret-VALUE detection. Audit 2026-05-30 P2: must NOT reuse
# FORBIDDEN_FIELD_PATTERNS on values — its bare tokens (`sid`, etc.) would
# false-positive on legitimate seed values ("Cassidy" contains "ssid",
# "president" contains "sid"). Instead match an embedded credential LABEL
# immediately followed by an assignment (`name=...` / `name: ...`) using word
# boundaries + distinctive multi-char labels only. Bare high-entropy values
# under innocent keys are intentionally NOT flagged (indistinguishable from
# data); the access-token / org-id SHAPE check covers the common tokens.
FORBIDDEN_VALUE_PATTERNS = re.compile(
    r"\b(password|secret|access[_-]?token|refresh[_-]?token|session[_-]?id|"
    r"oauth[_-]?token|frontdoor|api[_-]?key|private[_-]?key)\b\s*[:=]",
    re.IGNORECASE,
)

# Forbidden permissions for non-admin test users (refuse if assigned)
FORBIDDEN_NON_ADMIN_PERMS = (
    "ModifyAllData",
    "ViewAllData",
    "ManageUsers",
    "BulkApiHardDelete",
    "AuthorApex",
    "ManageProfilesPermissionsets",
)


class SeedValidationError(Exception):
    """Raised when seed file fails validation."""


# Salesforce Id shape: 15- or 18-char alphanumeric. Used to validate
# caller-supplied user_id BEFORE interpolating into SOQL (SEED-SOQL-INJECTION,
# full-repo TAA 2026-05-31). Seed files are operator-authored (low risk), but a
# user_id containing a quote would corrupt or inject the verification query.
_SF_ID_RE = re.compile(r"^[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$")


def _assert_valid_sf_id(user_id: str) -> None:
    if not isinstance(user_id, str) or not _SF_ID_RE.match(user_id):
        raise SeedValidationError(
            f"user_id {user_id!r} is not a valid 15-/18-char Salesforce Id "
            f"(refusing to interpolate into SOQL)"
        )


def seed_path_for(client_alias: str, repo_root: Path | None = None) -> Path:
    """Load exact selected-client configuration; never infer clients from org aliases."""
    if repo_root is not None:
        return Path(repo_root) / "config" / "test-users.json"
    return private_config("test-users.json", env="TORQUE_TEST_USERS")


def load_seed(path: Path) -> dict:
    """Load seed file with file-mode + ownership validation. Raises on violation."""
    if not path.exists():
        raise SeedValidationError(
            f"seed file not found: {path}\n"
            f"  create one per .claude/rules/qa-orchestration.md spec.\n"
            f"  Schema example in design-v4.md Closure 3."
        )
    st = path.stat()
    # Windows has no POSIX mode bits or getuid(); this hardening is POSIX-only.
    if os.name != "nt":
        if (st.st_mode & 0o777) != 0o600:
            raise SeedValidationError(
                f"seed file mode is {oct(st.st_mode & 0o777)}, required 0o600. "
                f"Run: chmod 600 {path}"
            )
        if st.st_uid != os.getuid():
            raise SeedValidationError(
                f"seed file owned by uid {st.st_uid}, current uid is {os.getuid()}. "
                f"Refusing to read another user's seed."
            )
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise SeedValidationError(f"seed file is not valid JSON: {e}")
    return data


def validate_seed_schema(data: dict) -> None:
    """Static schema validation. Refuses forbidden fields. Raises on violation."""
    if data.get("schema_version") != 1:
        raise SeedValidationError(
            f"unsupported schema_version: {data.get('schema_version')}"
        )
    for k in ("org_id_18", "alias", "users"):
        if k not in data:
            raise SeedValidationError(f"missing required top-level key: {k}")
    if not isinstance(data["users"], dict):
        raise SeedValidationError("users must be a dict mapping profile_role → user_entry")

    # Recursive scan for forbidden field names
    _scan_forbidden(data, path="")

    # Per-user schema
    for role, user in data["users"].items():
        if not isinstance(user, dict):
            raise SeedValidationError(f"users.{role} must be a dict")
        required_user_fields = (
            "username", "user_id", "expected_profile", "license",
            "expected_permission_sets", "allowed_in_prod",
        )
        for k in required_user_fields:
            if k not in user:
                raise SeedValidationError(
                    f"users.{role} missing required field: {k}"
                )
        if not isinstance(user["expected_permission_sets"], list):
            raise SeedValidationError(
                f"users.{role}.expected_permission_sets must be a list"
            )
        if not isinstance(user["allowed_in_prod"], bool):
            raise SeedValidationError(
                f"users.{role}.allowed_in_prod must be bool"
            )


def _scan_forbidden(obj: Any, path: str) -> None:
    """Recursively scan for forbidden field names (passwords, tokens, etc.)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            sub_path = f"{path}.{k}" if path else k
            if FORBIDDEN_FIELD_PATTERNS.search(k):
                raise SeedValidationError(
                    f"forbidden field name '{k}' at {sub_path} — "
                    f"seed file MUST NOT contain credentials or tokens"
                )
            # Secret-VALUE scan, length-independent. Audit 2026-05-30 P2: the
            # previous `len(v) > 20` gate let short secrets through (a <20-char
            # `session_id=ABC`, an `api_key=xyz`, any non-00D/Bearer token).
            # Match an embedded credential label via FORBIDDEN_VALUE_PATTERNS plus
            # the access-token / org-id shape — without any length gate.
            if isinstance(v, str):
                if FORBIDDEN_VALUE_PATTERNS.search(v):
                    raise SeedValidationError(
                        f"forbidden secret-shaped value at {sub_path} "
                        f"(embedded credential label)"
                    )
                # Bearer-prefixed values are always tokens.
                if v.startswith("Bearer "):
                    raise SeedValidationError(
                        f"forbidden secret-shaped value at {sub_path} "
                        f"(bearer token)"
                    )
                # Salesforce SESSION ID / access token shape: org-id prefix + '!'
                # + session material (e.g. '00D5g0000004XYZ!AQ8AQ...'). The '!'
                # (or excess length) is the discriminator. A BARE 15-/18-char
                # alphanumeric org id (e.g. '00DPP0000004XYZAB1') is the REQUIRED,
                # non-secret `org_id_18` field and MUST be allowed — the prior
                # check rejected any '00D'-prefixed string and so rejected every
                # legitimate seed's own org_id_18, breaking /qa-multiprofile
                # entirely (regression introduced 2026-05-30; caught 2026-05-31).
                if v.startswith("00D") and ("!" in v or len(v) > 18):
                    raise SeedValidationError(
                        f"forbidden secret-shaped value at {sub_path} "
                        f"(Salesforce session id / access token)"
                    )
            _scan_forbidden(v, sub_path)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _scan_forbidden(v, f"{path}[{i}]")


def validate_user_via_soql(
    target_org: str,
    user_id: str,
    user_role: str,
    expected_profile: str,
    expected_license: str,
    expected_permission_sets: list[str],
    is_production: bool,
    allowed_in_prod: bool,
) -> tuple[bool, str]:
    """Validate user via live SOQL queries against target org.

    Returns (is_valid, detail).
    """
    if is_production and not allowed_in_prod:
        return False, (
            f"user {user_role!r} is NOT marked allowed_in_prod=true; "
            f"refusing to use against production target {target_org}"
        )

    # SEED-SOQL-INJECTION (full-repo TAA 2026-05-31): user_id is interpolated
    # into every verification query below. Validate the Salesforce-Id shape
    # before any interpolation so a quote/paren in user_id cannot corrupt or
    # inject SOQL. (target_org is already alias-shape-validated upstream by the
    # dispatcher's _validate_target_org.)
    try:
        _assert_valid_sf_id(user_id)
    except SeedValidationError as e:
        return False, str(e)

    # Query 1: User profile + license + active status
    # NOTE: the license lives on the PROFILE relationship — User has no direct
    # UserLicense lookup. The prior `UserLicense.Name` form was INVALID_FIELD
    # SOQL, so live verification failed for every org (surfaced 2026-08-18
    # during the first real production seed validation, sample-prod).
    cmd = ["sf", "data", "query", "--target-org", target_org,
           "--query",
           f"SELECT Id, IsActive, Profile.Name, UserType, "
           f"Profile.UserLicense.Name FROM User WHERE Id='{user_id}'",
           "--json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return False, f"sf data query failed: {e}"
    if proc.returncode != 0:
        return False, f"sf data query exit {proc.returncode}: {proc.stderr[:200]}"
    try:
        data = json.loads(proc.stdout)
        records = data.get("result", {}).get("records", [])
    except json.JSONDecodeError:
        return False, "sf data query returned non-JSON"
    if not records:
        return False, f"user_id {user_id} not found in target org"
    user = records[0]
    if not user.get("IsActive"):
        return False, f"user {user_id} is INACTIVE"
    actual_profile = user.get("Profile", {}).get("Name", "")
    if actual_profile != expected_profile:
        return False, (
            f"user {user_id} profile is {actual_profile!r}, "
            f"expected {expected_profile!r}"
        )
    actual_license = ((user.get("Profile") or {}).get("UserLicense") or {}).get("Name", "")
    if actual_license != expected_license:
        return False, (
            f"user {user_id} license is {actual_license!r}, "
            f"expected {expected_license!r}"
        )

    # Query 2: For non-admin users, refuse if profile is System Administrator
    # OR if dangerous permissions are assigned via PSAs
    if user_role != "admin":
        if actual_profile == "System Administrator":
            return False, (
                f"non-admin user {user_role!r} has System Administrator profile — "
                f"refusing (defeats multi-profile UAT purpose)"
            )

        # Check for dangerous permissions via PermissionSetAssignment + PermissionSet
        # We check via the PermissionsModifyAllData etc. fields on user-effective perms
        # (simpler: just check the PS names assigned vs forbidden list)
        cmd2 = ["sf", "data", "query", "--target-org", target_org,
                "--query",
                f"SELECT PermissionSet.Name FROM PermissionSetAssignment "
                f"WHERE AssigneeId='{user_id}'",
                "--json"]
        try:
            proc = subprocess.run(cmd2, capture_output=True, text=True, timeout=30)
            data = json.loads(proc.stdout)
            ps_names = [r["PermissionSet"]["Name"]
                        for r in data.get("result", {}).get("records", [])]
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError, KeyError):
            return False, "could not query PermissionSetAssignment"

        # Verify expected permission sets present
        missing = [ps for ps in expected_permission_sets if ps not in ps_names]
        if missing:
            return False, (
                f"user {user_role!r} missing expected permission sets: {missing}"
            )

        # Check for dangerous permissions via aggregate user-effective query.
        # SEED-PROFILE-PERMS (full-repo TAA 2026-05-31): the PermissionSetAssignment
        # subquery INCLUDES the user's profile-owned PermissionSet (every Profile
        # is backed by a PermissionSet with IsOwnedByProfile=true since API 39), so
        # a dangerous perm granted by the PROFILE is already covered here — the
        # earlier worry that "only assigned PSes are checked, not the profile" does
        # NOT hold for this join. The real gap was field-list DRIFT: the perm fields
        # queried here must stay in lockstep with FORBIDDEN_NON_ADMIN_PERMS, which
        # listed ManageProfilesPermissionsets but cmd3 did not query it. Now derived
        # from the single source of truth so they cannot drift apart again.
        perm_fields = [f"Permissions{p}" for p in FORBIDDEN_NON_ADMIN_PERMS]
        cmd3 = ["sf", "data", "query", "--target-org", target_org,
                "--query",
                f"SELECT {', '.join(perm_fields)} "
                f"FROM PermissionSet WHERE Id IN ("
                f"SELECT PermissionSetId FROM PermissionSetAssignment "
                f"WHERE AssigneeId='{user_id}')",
                "--json"]
        try:
            proc = subprocess.run(cmd3, capture_output=True, text=True, timeout=30)
            data = json.loads(proc.stdout)
            for ps in data.get("result", {}).get("records", []):
                for perm_field in perm_fields:
                    if ps.get(perm_field):
                        perm_name = perm_field.replace("Permissions", "")
                        return False, (
                            f"non-admin user {user_role!r} has dangerous "
                            f"permission {perm_name} via assigned PS or profile — refusing"
                        )
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            # Permission check failed; conservatively REFUSE
            return False, "could not validate dangerous permissions; failing closed"

    return True, f"user {user_role!r} validated for {target_org}"


def validate_seed_for_run(
    client_alias: str,
    target_org: str,
    is_production: bool,
    repo_root: Path | None = None,
) -> tuple[bool, dict, list[tuple[str, str]]]:
    """Full validation: load + schema + per-user SOQL.

    Returns: (all_valid, seed_data, per_user_results)
      where per_user_results is a list of (role, detail_string).
    """
    path = seed_path_for(client_alias, repo_root)
    seed = load_seed(path)
    validate_seed_schema(seed)

    # Verify alias matches
    if seed["alias"] != client_alias:
        raise SeedValidationError(
            f"seed alias {seed['alias']!r} != requested {client_alias!r}"
        )

    per_user_results = []
    all_valid = True
    for role, user in seed["users"].items():
        ok, detail = validate_user_via_soql(
            target_org=target_org,
            user_id=user["user_id"],
            user_role=role,
            expected_profile=user["expected_profile"],
            expected_license=user["license"],
            expected_permission_sets=user["expected_permission_sets"],
            is_production=is_production,
            allowed_in_prod=user["allowed_in_prod"],
        )
        per_user_results.append((role, detail))
        if not ok:
            all_valid = False

    return all_valid, seed, per_user_results
