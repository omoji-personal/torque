"""The test-user seed: role names are the consultant's own words, credential field names
are still refused, and a role may name an alias of its own (`cli_alias`).

Before, any key containing the letters "sid" was refused as a credential field, so roles
called inside_sales or president could not be used."""
import pytest

from jsc_qa import seed_validator as sv


def user(**extra):
    entry = {"username": "std.user@acme.example", "user_id": "005000000000002AAA",
             "expected_profile": "Standard User", "license": "Salesforce",
             "expected_permission_sets": [], "allowed_in_prod": False}
    entry.update(extra)
    return entry


def seed(users, **extra):
    return {"schema_version": 1, "org_id_18": "00D000000000001AAA", "alias": "acme-sbx", "users": users, **extra}


@pytest.mark.parametrize("role", ["inside_sales", "president", "residential_services", "outside_rep",
                                  "password_reset_desk", "standard"])
def test_role_names_are_not_read_as_credential_fields(role):
    sv.validate_seed_schema(seed({role: user()}))


@pytest.mark.parametrize("field", ["sid", "user_sid", "SID", "sessionSid", "session_id", "password",
                                   "access_token", "refreshToken", "frontdoor_url", "Cookie"])
def test_credential_field_names_are_still_refused_wherever_they_are(field):
    with pytest.raises(sv.SeedValidationError, match="forbidden field name"):
        sv.validate_seed_schema(seed({"standard": user(**{field: "x"})}))
    with pytest.raises(sv.SeedValidationError, match="forbidden field name"):
        sv.validate_seed_schema(seed({"standard": user()}, **{field: "x"}))
    with pytest.raises(sv.SeedValidationError, match="forbidden field name"):
        sv.validate_seed_schema(seed({"standard": user(notes={field: "x"})}))


@pytest.mark.parametrize("name,forbidden", [
    ("sid", True), ("user_sid", True), ("sid_value", True), ("sessionSid", True), ("SID", True), ("orgSID", True),
    ("inside", False), ("president", False), ("considered", False), ("residence", False), ("username", False),
    ("user_id", False), ("cli_alias", False), ("allowed_in_prod", False), ("outsideRep", False),
])
def test_sid_counts_only_as_a_whole_word(name, forbidden):
    assert sv.forbidden_field(name) is forbidden


def test_a_role_may_name_its_own_cli_alias():
    sv.validate_seed_schema(seed({"standard": user(cli_alias="acme-sbx-std"), "manager": user()}))


@pytest.mark.parametrize("value", ["", "two words", "-o", 7, None, ["acme-sbx-std"]])
def test_a_cli_alias_must_be_one_alias(value):
    with pytest.raises(sv.SeedValidationError, match="cli_alias must be one org alias"):
        sv.validate_seed_schema(seed({"standard": user(cli_alias=value)}))


def test_secret_shaped_values_are_still_refused():
    with pytest.raises(sv.SeedValidationError, match="secret-shaped value"):
        sv.validate_seed_schema(seed({"standard": user(notes="password: hunter2")}))
