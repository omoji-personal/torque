"""test_seed_validator.py — P2 secret-value scan regression coverage.

Audit 2026-05-30 P2: _scan_forbidden() gated its secret-VALUE scan behind a
`len(v) > 20` heuristic and only flagged 00D/Bearer-prefixed values >100 chars,
so a short embedded secret (`session_id=ABC`, `api_key=xyz`) silently passed.
The fix scans all string values (length-independent) for an embedded credential
label via a word-boundaried FORBIDDEN_VALUE_PATTERNS, plus the access-token/org-id
shape — WITHOUT false-positiving on legitimate names (the field pattern's bare
`sid`/`pwd` tokens would have flagged "Cassidy"/"president").
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jsc_qa.seed_validator import (  # noqa: E402
    _assert_valid_sf_id,
    _scan_forbidden,
    validate_user_via_soql,
    SeedValidationError,
)


def _raises(value) -> bool:
    try:
        _scan_forbidden({"some_key": value}, "")
        return False
    except SeedValidationError:
        return True


class TestSecretValueScan(unittest.TestCase):
    def test_short_embedded_secrets_are_caught(self):
        # These are exactly the values the old len(v) > 20 gate let through.
        for v in ("session_id=ABC123", "api_key: xyz", "password=hunter2",
                  "private_key=abc", "access_token=z"):
            self.assertTrue(_raises(v), f"should flag embedded secret: {v!r}")

    def test_session_token_shapes_are_caught(self):
        # A Salesforce SESSION ID / access token (org-id prefix + '!' + session
        # material, or an over-long 00D string) must be flagged.
        self.assertTrue(_raises("00D5g0000004XYZ!AQ8AQw1234567890abcdef"))  # session id
        self.assertTrue(_raises("00Dxx0000001gPzEAM_EXTRA_SESSION_MATERIAL"))  # >18 chars
        self.assertTrue(_raises("Bearer eyJabc"))     # bearer token

    def test_bare_org_id_is_allowed(self):
        # The REQUIRED org_id_18 seed field is a bare 15-/18-char org id and must
        # NOT be flagged — the prior over-broad '00D'-prefix check rejected it and
        # so rejected every legitimate seed (regression 2026-05-30 → fixed
        # 2026-05-31). Both the 15-char short form and the 18-char form are clean.
        for v in ("00Dxx0000001gPz", "00DPP0000004XYZAB1"):
            self.assertFalse(_raises(v), f"bare org id must NOT be flagged: {v!r}")

    def test_forbidden_field_names_still_caught(self):
        # The key scan (FORBIDDEN_FIELD_PATTERNS) is unconditional and must still
        # fire on a forbidden field NAME regardless of its value.
        with self.assertRaises(SeedValidationError):
            _scan_forbidden({"password": "x"}, "")
        with self.assertRaises(SeedValidationError):
            _scan_forbidden({"session_id": "x"}, "")

    def test_legitimate_values_do_not_false_positive(self):
        # Names/usernames/profiles/licenses/ids that contain forbidden SUBSTRINGS
        # ('Cassidy' has 'ssid', 'president' has 'sid') must NOT be flagged.
        for v in ("Cassidy", "president@org.example", "qa.standard@sample-prod.example",
                  "Example_Standard_User", "Salesforce Platform",
                  "005XX000001abcd", "considering the outside resident"):
            self.assertFalse(_raises(v), f"must NOT flag legitimate value: {v!r}")


class TestSoqlIdValidation(unittest.TestCase):
    """SEED-SOQL-INJECTION (TAA 2026-05-31): user_id must be Salesforce-Id-shaped
    before it is interpolated into the verification SOQL."""

    def test_valid_ids_accepted(self):
        # 15-char and 18-char Salesforce Ids.
        for ok in ("0050a000001ABCD", "005XX000001abcd", "0050a000001ABCDAAA"):
            try:
                _assert_valid_sf_id(ok)
            except SeedValidationError:
                self.fail(f"valid id rejected: {ok!r}")

    def test_injection_shaped_ids_rejected(self):
        for bad in (
            "005' OR Id!=null--",          # quote-break / tautology
            "005XX'); DELETE User--",       # statement-injection attempt
            "005 XX0000",                   # space
            "0050a000001ABCDA",             # 16 chars (neither 15 nor 18)
            "",                              # empty
        ):
            with self.assertRaises(SeedValidationError, msg=f"should reject {bad!r}"):
                _assert_valid_sf_id(bad)

    def test_validate_user_via_soql_rejects_injection_before_any_query(self):
        # No live org needed: a malformed user_id must be rejected at the shape
        # gate, before any `sf data query` subprocess is attempted.
        ok, detail = validate_user_via_soql(
            target_org="sf-sandbox",
            user_id="005' OR Id!=null--",
            user_role="standard",
            expected_profile="Example_Standard_User",
            expected_license="Salesforce Platform",
            expected_permission_sets=[],
            is_production=False,
            allowed_in_prod=False,
        )
        self.assertFalse(ok)
        self.assertIn("not a valid", detail)

    def test_user_query_reads_license_via_profile_relationship(self):
        # Regression for the 2026-08-18 sample-prod finding: User has NO direct
        # UserLicense relationship — the query must traverse Profile.UserLicense
        # and the parser must read the nested path. The old `UserLicense.Name`
        # form was INVALID_FIELD SOQL, so live verification failed on every org.
        from unittest import mock
        import json as _json

        captured = {"queries": []}

        def fake_run(cmd, capture_output=True, text=True, timeout=30):
            q = cmd[cmd.index("--query") + 1]
            captured["queries"].append(q)
            if "PermissionSetAssignment" in q or "PermissionSet" in q:
                payload = {"result": {"records": []}}
            else:
                payload = {"result": {"records": [{
                    "Id": "005000000000001AAA",
                    "IsActive": True,
                    "UserType": "Standard",
                    "Profile": {"Name": "Example_Standard_User",
                                "UserLicense": {"Name": "Salesforce"}},
                }]}}
            m = mock.Mock()
            m.returncode = 0
            m.stdout = _json.dumps(payload)
            m.stderr = ""
            return m

        with mock.patch("jsc_qa.seed_validator.subprocess.run", side_effect=fake_run):
            ok, detail = validate_user_via_soql(
                target_org="sf-sandbox",
                user_id="005000000000001AAA",
                user_role="standard",
                expected_profile="Example_Standard_User",
                expected_license="Salesforce",
                expected_permission_sets=[],
                is_production=False,
                allowed_in_prod=False,
            )
        user_query = captured["queries"][0]
        self.assertIn("Profile.UserLicense.Name", user_query)
        self.assertNotIn(" UserLicense.Name", user_query.replace("Profile.UserLicense.Name", ""))
        self.assertTrue(ok, detail)

        # and a license MISMATCH must fail with the actual value named
        def fake_run_mismatch(cmd, capture_output=True, text=True, timeout=30):
            q = cmd[cmd.index("--query") + 1]
            if "PermissionSetAssignment" in q or "PermissionSet" in q:
                payload = {"result": {"records": []}}
            else:
                payload = {"result": {"records": [{
                    "Id": "005000000000001AAA",
                    "IsActive": True,
                    "UserType": "Standard",
                    "Profile": {"Name": "Example_Standard_User",
                                "UserLicense": {"Name": "Salesforce Platform"}},
                }]}}
            m = mock.Mock()
            m.returncode = 0
            m.stdout = _json.dumps(payload)
            m.stderr = ""
            return m

        with mock.patch("jsc_qa.seed_validator.subprocess.run", side_effect=fake_run_mismatch):
            ok, detail = validate_user_via_soql(
                target_org="sf-sandbox",
                user_id="005000000000001AAA",
                user_role="standard",
                expected_profile="Example_Standard_User",
                expected_license="Salesforce",
                expected_permission_sets=[],
                is_production=False,
                allowed_in_prod=False,
            )
        self.assertFalse(ok)
        self.assertIn("Salesforce Platform", detail)


if __name__ == "__main__":
    unittest.main()
