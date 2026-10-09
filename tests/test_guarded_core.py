"""Guarded reads, the pure rules: what a caller can and cannot get a query or a
printed value for. Attack cases first."""
import pytest

try:
    from torque import guarded_core as core
except ImportError:  # run from the draft folder before the module is in the package
    import guarded_core as core

E = core.GuardedError


def fld(name, type_, **more):
    base = {"name": name, "type": type_, "label": more.pop("label", name), "groupable": type_ in ("picklist", "boolean"),
            "filterable": type_ not in ("textarea", "address", "location", "base64"), "aggregatable": type_ != "base64",
            "calculated": False, "calculatedFormula": None, "nameField": False, "encrypted": False,
            "compoundFieldName": None, "picklistValues": []}
    values = more.pop("values", None)
    if values:
        base["picklistValues"] = [{"value": v, "active": True} for v in values]
    base.update(more)
    return base


def describe(name="Opportunity", extra=(), **top):
    fields = [
        fld("Id", "id"), fld("Name", "string", nameField=True),
        fld("StageName", "picklist", values=["Prospecting", "Closed Won", "Women's Fund"]),
        fld("IsWon", "boolean"), fld("Amount", "currency"), fld("CloseDate", "date"),
        fld("CreatedDate", "datetime", groupable=False), fld("RecordTypeId", "reference"),
        fld("AccountId", "reference"), fld("Description", "textarea"), fld("Notes__c", "string"),
        fld("Email__c", "email"), fld("Phone__c", "phone"), fld("Tags__c", "multipicklist", values=["A", "B"]),
        fld("Gender__c", "picklist", values=["F", "M"]), fld("Birthdate__c", "date"),
        fld("Years__c", "double", calculated=True, calculatedFormula="YEAR(TODAY()) - YEAR(Birthdate__c)"),
        fld("Score__c", "double", calculated=True, calculatedFormula="Amount * 2 + 1.5"),
        fld("Copy__c", "double", calculated=True, calculatedFormula="Account.AnnualRevenue"),
        fld("Uses_User__c", "boolean", calculated=True, calculatedFormula="$User.Id = OwnerId"),
        fld("Total__c", "currency", calculated=True), fld("Hidden_Formula__c", "double", calculated=True),
        fld("Of_Email__c", "boolean", calculated=True, calculatedFormula='CONTAINS(Email__c, "x.org")'),
        fld("Of_Formula__c", "double", calculated=True, calculatedFormula="Score__c + 1"),
        fld("BillingAddress", "address"), fld("BillingCity", "string", compoundFieldName="BillingAddress"),
        fld("BillingLatitude", "double", compoundFieldName="BillingAddress"),
        fld("BillingStateCode", "picklist", values=["PA", "NJ"], compoundFieldName="BillingAddress"),
        fld("Fiscal", "string"), fld("FiscalYear", "int", compoundFieldName="Fiscal"),
        fld("Secret__c", "double", encrypted=True),
        fld("Classified__c", "picklist", values=["x"]), fld("Restricted__c", "boolean"),
        fld("Blob__c", "base64"),
        fld("Customer_Copy__c", "string", calculated=True, calculatedFormula="Account.Name"),
        fld("Alias_Copy__c", "string", calculated=True, calculatedFormula="Customer_Copy__c"),
        fld("Alias_Twice__c", "string", calculated=True, calculatedFormula='Alias_Copy__c & "x"'),
        fld("Lookup_Text__c", "string", calculated=True, calculatedFormula="AccountId"),
        fld("Session__c", "string", calculated=True, calculatedFormula="GETSESSIONID()"),
        fld("Loop_A__c", "double", calculated=True, calculatedFormula="Loop_B__c"),
        fld("Loop_B__c", "double", calculated=True, calculatedFormula="Loop_A__c"), *extra]
    payload = {"name": name, "queryable": True, "fields": fields,
               "recordTypeInfos": [{"developerName": "Donation", "name": "Donation", "master": False},
                                   {"developerName": "Grant", "name": "Major Grant", "master": False},
                                   {"developerName": "Master", "name": "Master", "master": True}]}
    payload.update(top)
    return core.parse_describe(payload)


FACTS = core.parse_facts([
    {"QualifiedApiName": "Total__c", "DataType": "Roll-Up Summary (SUM Payment)", "ComplianceGroup": None,
     "SecurityClassification": None},
    {"QualifiedApiName": "Classified__c", "DataType": "Picklist", "ComplianceGroup": "PII;GDPR",
     "SecurityClassification": None},
    {"QualifiedApiName": "Restricted__c", "DataType": "Checkbox", "ComplianceGroup": None,
     "SecurityClassification": "Restricted"},
    {"QualifiedApiName": "StageName", "DataType": "Picklist", "ComplianceGroup": None,
     "SecurityClassification": "Internal"}])
D = describe()


def policy(fields=None, objects=None, min_cell=5, classification="required"):
    return core.parse_policy({"schema": core.POLICY_SCHEMA, "fields": fields or {}, "objects": objects or {},
                              "counts_min_cell": min_cell, "classification": classification})


RELEASED = policy({"Opportunity.StageName": "exact", "Opportunity.IsWon": "exact", "Opportunity.Amount": "coarse",
                   "Opportunity.CloseDate": "coarse", "Opportunity.CreatedDate": "exact",
                   "Opportunity.RecordTypeId": "exact", "Opportunity.Score__c": "exact",
                   "Opportunity.Tags__c": "exact"})
NOTHING = policy()


# ---- releases

@pytest.mark.parametrize("name,level", [("StageName", "exact"), ("IsWon", "exact"), ("Amount", "coarse"),
                                        ("Amount", "exact"), ("CloseDate", "coarse"), ("CreatedDate", "exact"),
                                        ("RecordTypeId", "exact"), ("Score__c", "exact"), ("Total__c", "coarse"),
                                        ("FiscalYear", "exact"), ("Tags__c", "exact")])
def test_releasable(name, level):
    core.check_release(D, D.field(name), level, FACTS, acknowledged=False)


@pytest.mark.parametrize("name,why", [
    ("Name", "name"), ("Notes__c", "string"), ("Description", "textarea"), ("AccountId", "reference"),
    ("Id", "id"), ("Email__c", "email"), ("Phone__c", "phone"), ("BillingAddress", "address"),
    ("BillingCity", "address"), ("BillingLatitude", "address"), ("Secret__c", "encrypted"),
    ("Blob__c", "base64"), ("Classified__c", "compliance"), ("Restricted__c", "Restricted"),
    ("Copy__c", "another record"), ("Uses_User__c", "another record"), ("Hidden_Formula__c", "did not give"),
    ("Of_Email__c", "Email__c"), ("Of_Formula__c", "calculated")])
def test_never_releasable_even_acknowledged(name, why):
    with pytest.raises(E, match=why):
        core.check_release(D, D.field(name), "exact", FACTS, acknowledged=True)


@pytest.mark.parametrize("name", ["Gender__c", "Birthdate__c", "Years__c", "BillingStateCode"])
def test_sensitive_names_need_the_acknowledgement(name):
    with pytest.raises(E, match="acknowledge-sensitive"):
        core.check_release(D, D.field(name), "exact", FACTS, acknowledged=False)
    core.check_release(D, D.field(name), "exact", FACTS, acknowledged=True)


def test_sensitive_words_are_whole_words_only():
    quiet = describe(extra=[fld("BusinessName__c", "picklist", values=["a"]), fld("Adobe_Flag__c", "boolean"),
                            fld("Message__c", "boolean"), fld("Essex_Region__c", "boolean"),
                            fld("Stage_Group__c", "picklist", values=["a"], label="Stage group")])
    for name in ("BusinessName__c", "Adobe_Flag__c", "Message__c", "Essex_Region__c", "Stage_Group__c"):
        assert core.sensitive_word(quiet.field(name)) == ""
    loud = describe(extra=[fld("DOB__c", "date"), fld("ContactAge__c", "double"), fld("X__c", "boolean",
                                                                                      label="Health status")])
    assert [core.sensitive_word(loud.field(n)) for n in ("DOB__c", "ContactAge__c", "X__c")] == ["dob", "age",
                                                                                               "health"]


def test_coarse_is_for_dates_and_numbers():
    with pytest.raises(E, match="exact or not at all"):
        core.check_release(D, D.field("StageName"), "coarse", FACTS, acknowledged=True)


def test_roll_up_needs_the_orgs_word_for_it():
    # Without the org's field facts a calculated field with no formula text could be anything.
    with pytest.raises(E, match="did not give"):
        core.check_release(D, D.field("Total__c"), "exact", core.OrgFacts(True), acknowledged=True)


def test_a_release_the_org_no_longer_allows_counts_as_none():
    listed = policy({"Opportunity.Classified__c": "exact", "Opportunity.StageName": "exact",
                     "Opportunity.StageName2": "exact"})
    assert core.released(D, D.field("Classified__c"), listed, FACTS) is None
    assert core.released(D, D.field("StageName"), listed, FACTS) == "exact"
    assert core.released(D, D.field("IsWon"), listed, FACTS) is None


def test_unreadable_classification_stops_everything_unless_ignored():
    with pytest.raises(E, match="classification could not be read"):
        core.require_facts(RELEASED, core.parse_facts(None))
    with pytest.raises(E, match="classification could not be read"):
        core.require_facts(RELEASED, core.parse_facts([{"no": "name"}]))
    core.require_facts(policy(classification="ignore"), core.parse_facts(None))
    with pytest.raises(E, match="classification could not be read"):
        core.require_facts(RELEASED, core.parse_facts([]))  # an answer with no field at all is no answer
    core.require_facts(policy(classification="ignore"), core.parse_facts([]))


@pytest.mark.parametrize("payload", [
    {}, {"schema": "other"}, {"schema": core.POLICY_SCHEMA, "fields": []},
    {"schema": core.POLICY_SCHEMA, "fields": {"Opportunity.StageName": "clear"}},
    {"schema": core.POLICY_SCHEMA, "fields": {"StageName": "exact"}},
    {"schema": core.POLICY_SCHEMA, "fields": {"A.B.C": "exact"}},
    {"schema": core.POLICY_SCHEMA, "objects": ["Settings__c"]}, {"schema": core.POLICY_SCHEMA, "objects": {"a b": ["*"]}},
    {"schema": core.POLICY_SCHEMA, "objects": {"A__c": []}}, {"schema": core.POLICY_SCHEMA, "objects": {"A__c": "*"}},
    {"schema": core.POLICY_SCHEMA, "objects": {"A__c": ["*", "B__c"]}},
    {"schema": core.POLICY_SCHEMA, "objects": {"A__c": ["B C"]}}, {"schema": core.POLICY_SCHEMA, "objects": {"A__c": [1]}},
    {"schema": core.POLICY_SCHEMA, "counts_min_cell": 2},
    {"schema": core.POLICY_SCHEMA, "counts_min_cell": "5"},
    {"schema": core.POLICY_SCHEMA, "counts_min_cell": True},
    {"schema": core.POLICY_SCHEMA, "classification": "maybe"}, "text", 3])
def test_malformed_policy_is_refused_whole(payload):
    with pytest.raises(E, match="malformed"):
        core.parse_policy(payload)


def test_no_policy_file_releases_nothing():
    assert core.parse_policy(None) == core.Policy()
    assert core.released(D, D.field("StageName"), core.parse_policy(None), FACTS) is None


# ---- object release

def test_people_objects_are_never_configuration():
    for name in ("Contact", "Account", "User", "Opportunity", "Case", "CampaignMember", "EmailMessage"):
        with pytest.raises(E, match="people"):
            core.check_object_release(describe(name), ["StageName"], FACTS, records=True, sensitive=True)
    with pytest.raises(E, match="people"):
        core.check_object_release(describe("Custom__c", extra=[fld("IsPersonAccount", "boolean")]), ["*"], FACTS,
                                  records=True)


def test_object_release_names_the_fields_it_shows():
    release = core.check_object_release
    setting = describe("Settings__c", customSetting=True)
    mdt, handler = describe("Rule__mdt"), describe("Trigger_Handler__c", extra=[fld("Class__c", "string")])
    assert release(setting, [], FACTS) == ["*"] and release(setting, ["*"], FACTS) == ["*"]
    assert release(mdt, None, FACTS) == ["*"]
    assert release(setting, ["notes__c", "Name"], FACTS) == ["Notes__c", "Name"]
    with pytest.raises(E, match="name the fields"):                 # all fields: settings objects only
        release(handler, ["*"], FACTS, records=True)
    with pytest.raises(E, match="name 1 to"):
        release(handler, [], FACTS, records=True)
    with pytest.raises(E, match="acknowledge-records"):
        release(handler, ["Class__c"], FACTS)
    assert release(handler, ["class__c", "Name", "Description", "IsWon", "Class__c"], FACTS, records=True) == [
        "Class__c", "Name", "Description", "IsWon"]
    for name, why in (("Email__c", "email"), ("AccountId", "another record"), ("Secret__c", "encrypted"),
                      ("BillingCity", "address"), ("Classified__c", "compliance"), ("Total__c", "calculated"),
                      ("Customer_Copy__c", "calculated"), ("Alias_Copy__c", "calculated"), ("Blob__c", "base64")):
        with pytest.raises(E, match=why):
            release(handler, [name], FACTS, records=True, sensitive=True)
    with pytest.raises(E, match="acknowledge-sensitive"):
        release(handler, ["Gender__c"], FACTS, records=True)
    assert release(handler, ["Gender__c"], FACTS, records=True, sensitive=True) == ["Gender__c"]
    with pytest.raises(E, match="no field named"):
        release(handler, ["Missing__c"], FACTS, records=True)


# ---- filters

def one(text, pol=RELEASED):
    return core.parse_filter(text, D, pol, FACTS).soql()


@pytest.mark.parametrize("text,soql", [
    ("StageName = Closed Won", "StageName = 'Closed Won'"),
    ("StageName = 'closed won'", "StageName = 'Closed Won'"),
    ('StageName != "Women\'s Fund"', "StageName != 'Women\\'s Fund'"),
    ("stagename IN (Prospecting, 'Closed Won')", "StageName IN ('Prospecting', 'Closed Won')"),
    ("StageName not  in ('Prospecting')", "StageName NOT IN ('Prospecting')"),
    ("IsWon = TRUE", "IsWon = true"), ("IsWon != false", "IsWon != false"),
    ("Amount >= 5000", "Amount >= 5000"), ("Amount < 0.5", "Amount < 0.5"), ("Amount = 0", "Amount = 0"),
    ("CloseDate >= 2026-01-01", "CloseDate >= 2026-01-01"), ("CloseDate < 2026-03-01", "CloseDate < 2026-03-01"),
    ("CloseDate = this_year", "CloseDate = THIS_YEAR"), ("CloseDate > LAST_N_MONTHS:6", "CloseDate > LAST_N_MONTHS:6"),
    ("CreatedDate >= 2026-01-15", "CreatedDate >= 2026-01-15T00:00:00Z"),
    ("CreatedDate < 2026-01-15T10:30:00Z", "CreatedDate < 2026-01-15T10:30:00Z"),
    ("CreatedDate = TODAY", "CreatedDate = TODAY"),
    ("Score__c > 12.345", "Score__c > 12.345"),
    ("RecordType.DeveloperName = donation", "RecordType.DeveloperName = 'Donation'"),
    ("recordtype.name IN ('Major Grant', Donation)", "RecordType.Name IN ('Major Grant', 'Donation')"),
    ("RecordType.Name = null", "RecordTypeId = null"),
    ("Email__c = null", "Email__c = null"), ("Notes__c != NULL", "Notes__c != null"),
    ("BillingCity != null", "BillingCity != null"), ("Phone__c = null", "Phone__c = null"),
    ("Name = null", "Name = null"), ("AccountId != null", "AccountId != null")])
def test_accepted_filters(text, soql):
    assert one(text) == soql


@pytest.mark.parametrize("text,why", [
    ("Name = 'Jane Doe'", "not released"), ("Notes__c = x", "not released"), ("Email__c = 'a@example.org'", "not released"),
    ("AccountId = 001000000000001", "not released"), ("Id = 006000000000001", "not released"),
    ("Gender__c = F", "not released"), ("Birthdate__c >= 1984-01-01", "not released"),
    ("Gender__c != null", "the word 'gender'"), ("Birthdate__c = null", "the word 'birthdate'"),
    ("Years__c != null", "its formula uses Birthdate__c"), ("Classified__c = null", "compliance"),
    ("Restricted__c != null", "Restricted"),
    ("Description = null", "cannot be filtered"),
    ("StageName = Closed", "not a configured value"), ("StageName = ' OR Name != '", "not a configured value"),
    ("StageName = 'Closed Won' OR Name != ''", "not a configured value"),
    ("StageName LIKE 'C%'", "could not read"), ("StageName", "could not read"), ("= x", "could not read"),
    ("LOWER(StageName) = 'x'", "could not read"), ("StageName = ", "could not read|no value"),
    ("Account.Name = 'x'", "relationship paths"), ("Owner.Email != null", "relationship paths"),
    ("RecordType.Id = 'x'", "relationship paths"), ("RecordTypeId = '012000000000001'", "RecordType.DeveloperName"),
    ("RecordType.DeveloperName = Nope", "not a configured value"), ("RecordType.Name > Donation", "compared with"),
    ("IsWon = yes", "checkbox"), ("IsWon > true", "checkbox"), ("IsWon IN (true)", "checkbox"),
    ("Amount >= 75043", "two significant digits"), ("Amount = 12.5", "two significant digits"),
    ("Amount >= 1e3", "not a number"), ("Amount >= 5000 OR Amount < 0", "not a number"),
    ("Amount IN (1, 2)", "compared with"), ("Amount = abc", "not a number"),
    ("CloseDate >= 2026-01-15", "first day of a month"), ("CloseDate = 2026-01-01", "first day of a month"),
    ("CloseDate <= 2026-02-01", "first day of a month"), ("CloseDate = TODAY", "first day of a month"),
    ("CloseDate >= LAST_N_DAYS:3", "first day of a month"), ("CloseDate >= 2026-13-01", "not a date"),
    ("CloseDate >= yesterdayish", "not a date"), ("CloseDate = 2026-01-01T00:00:00Z", "not a date"),
    ("CreatedDate >= 2026-01-15T10:30:00", "not a date"), ("CreatedDate >= 2026-01-15 10:30:00Z", "not a date"),
    ("Tags__c = A", "tested for null only"), ("StageName > Prospecting", "compared with"),
    ("StageName = null AND 1=1", "not a configured value"), ("StageName IN ()", "empty value"),
    ("StageName IN ('Prospecting'", "quote|could not read|not a configured"),
    ("StageName IN ('Prospecting' x)", "could not read"), ("StageName = Closed\x00Won", "control character"),
    ("StageName = " + "x" * 500, "longer than"), ("Sta geName = x", "could not read"),
    ("StageNamе = x", "could not read"), ("Missing__c = null", "no field named")])
def test_refused_filters(text, why):
    with pytest.raises(E, match=why):
        one(text)


def test_a_released_sensitive_field_can_be_tested_for_null():
    released = policy({"Opportunity.Gender__c": "exact", "Opportunity.Birthdate__c": "coarse"})
    assert one("Gender__c != null", released) == "Gender__c != null"
    assert one("Birthdate__c = null", released) == "Birthdate__c = null"
    with pytest.raises(E, match="compliance"):          # the org's own classification cannot be released
        one("Classified__c = null", policy({"Opportunity.Classified__c": "exact"}))


def test_nothing_released_means_only_null_tests():
    assert one("StageName = null", NOTHING) == "StageName = null"
    for text in ("StageName = Closed Won", "IsWon = true", "Amount >= 5000", "CloseDate >= 2026-01-01",
                 "RecordType.DeveloperName = Donation", "RecordType.Name = null"):
        with pytest.raises(E, match="not released"):
            one(text, NOTHING)


def test_exact_release_takes_fine_values():
    exact = policy({"Opportunity.Amount": "exact", "Opportunity.CloseDate": "exact"})
    assert one("Amount >= 75043.21", exact) == "Amount >= 75043.21"
    assert one("CloseDate = 2026-01-15", exact) == "CloseDate = 2026-01-15"


def test_filter_limits():
    with pytest.raises(E, match="at most 6"):
        core.parse_filters(["IsWon = true"] * 7, D, RELEASED, FACTS)
    with pytest.raises(E, match="at most 20"):
        one("StageName IN (" + ", ".join(["Prospecting"] * 21) + ")")
    assert core.parse_filters(None, D, RELEASED, FACTS) == []
    with pytest.raises(E):
        core.parse_filter(None, D, RELEASED, FACTS)


@pytest.mark.parametrize("text,ok", [("75000", True), ("1200", True), ("0.5", True), ("0", True), ("-30", True),
                                     ("0.05", True), ("10", True), ("75043", False), ("125", False),
                                     ("12.5", False), ("100.50", False), ("0.125", False), ("1000001", False)])
def test_coarse_number(text, ok):
    assert core.coarse_number(text) is ok


# ---- counts

def dims(*texts, pol=RELEASED):
    return [core.parse_dimension(t, D, pol, FACTS) for t in texts]


def test_counts_queries():
    q = lambda texts, filters=(): core.build_counts_query(D, dims(*texts), core.parse_filters(filters, D, RELEASED,
                                                                                               FACTS))
    assert q([]) == ("SELECT COUNT(Id) n FROM Opportunity", [])
    assert q(["StageName"], ["IsWon = true"]) == (
        "SELECT StageName d0, COUNT(Id) n FROM Opportunity WHERE IsWon = true GROUP BY StageName LIMIT 201",
        [["d0"]])
    assert q(["stagename", "CloseDate:Month", "RecordType.DeveloperName"]) == (
        "SELECT StageName d0, CALENDAR_YEAR(CloseDate) d1, CALENDAR_MONTH(CloseDate) d2, RecordType.DeveloperName d3,"
        " COUNT(Id) n FROM Opportunity GROUP BY StageName, CALENDAR_YEAR(CloseDate), CALENDAR_MONTH(CloseDate),"
        " RecordType.DeveloperName LIMIT 201", [["d0"], ["d1", "d2"], ["d3"]])
    assert q(["CreatedDate:year", "IsWon"])[0].startswith("SELECT CALENDAR_YEAR(CreatedDate) d0, IsWon d1,")


@pytest.mark.parametrize("text,why", [
    ("Name", "not released"), ("Notes__c", "not released"), ("AccountId", "not released"),
    ("Gender__c", "not released"), ("Email__c", "not released"), ("Amount", "cannot be grouped"),
    ("Tags__c", "cannot be grouped"), ("CloseDate", "year, quarter or month"), ("CloseDate:day", "year, quarter"),
    ("CloseDate:", "year, quarter"), ("StageName:year", "dates only"), ("Account.Type", "relationship paths"),
    ("CALENDAR_YEAR(CloseDate)", "not a field name"), ("StageName, Name", "not a field name"),
    ("RecordTypeId", "cannot be grouped"), ("Missing__c", "no field named"), ("x" * 300, "too long")])
def test_refused_dimensions(text, why):
    with pytest.raises(E, match=why):
        dims(text)


def test_dimension_limits_and_unreleased():
    with pytest.raises(E, match="at most 3"):
        core.build_counts_query(D, dims("StageName", "IsWon", "CloseDate:year", "CreatedDate:year"), [])
    with pytest.raises(E, match="twice"):
        core.build_counts_query(D, dims("StageName", "stagename"), [])
    for text in ("StageName", "IsWon", "CloseDate:year", "RecordType.Name"):
        with pytest.raises(E, match="not released"):
            dims(text, pol=NOTHING)
    with pytest.raises(E, match="cannot be queried"):
        core.build_counts_query(describe(queryable=False), [], [])


def rec(n, **values):
    return {"attributes": {"type": "AggregateResult"}, "n": n, **values}


def test_small_cells_are_absent_and_look_like_nothing():
    dimension = dims("StageName")
    shaped = lambda records, pol=RELEASED: core.shape_counts(dimension, [["d0"]], records, pol)
    out = shaped([rec(5, d0="Closed Won"), rec(4, d0="Prospecting"), rec(1, d0="Women's Fund")])
    assert out["rows"] == [{"values": ["Closed Won"], "count": 5}]
    assert "Prospecting" not in repr(out) and "Women" not in repr(out)
    assert shaped([rec(4, d0="Prospecting")]) == shaped([])                  # small and empty look the same
    assert core.shape_counts([], [], [rec(4)], RELEASED) == core.shape_counts([], [], [rec(0)], RELEASED)
    assert core.shape_counts([], [], [rec(315)], RELEASED)["rows"] == [{"values": [], "count": 315}]
    assert shaped([rec(4, d0="Prospecting")], policy({"Opportunity.StageName": "exact"}, min_cell=3))["rows"] == [
        {"values": ["Prospecting"], "count": 4}]


def test_unconfigured_values_never_print():
    dimension = dims("StageName", "RecordType.Name", "IsWon")
    out = core.shape_counts(dimension, [["d0"], ["d1"], ["d2"]], [
        rec(6, d0="jane.doe@example.org", d1="Major Grant", d2=True),
        rec(7, d0="Jane Doe", d1="Major Grant", d2=True),
        rec(9, d0="Closed Won", d1="Somebody's Name", d2=False),
        rec(8, d0=None, d1=None, d2=None)], RELEASED)
    text = repr(out)
    assert "jane" not in text.casefold() and "Somebody" not in text
    assert {"values": ["<other>", "Major Grant", "true"], "count": 13} in out["rows"]     # merged, then counted
    assert {"values": ["Closed Won", "<other>", "false"], "count": 9} in out["rows"]
    assert {"values": ["<blank>", "<blank>", "<blank>"], "count": 8} in out["rows"]


def test_date_buckets_print_year_with_month_or_quarter():
    out = core.shape_counts(dims("CloseDate:month", "CloseDate:quarter", "CloseDate:year"),
                            [["d0", "d1"], ["d2", "d3"], ["d4"]],
                            [rec(9, d0=2026, d1=3, d2=2026, d3=1, d4=2026), rec(9, d0=2025, d1=13, d2="x", d3=1,
                                                                                d4=20260)], RELEASED)
    assert out["rows"][0]["values"] in (["2026-03", "2026-Q1", "2026"], ["<other>", "<other>", "<other>"])
    assert {tuple(r["values"]) for r in out["rows"]} == {("2026-03", "2026-Q1", "2026"),
                                                         ("<other>", "<other>", "<other>")}


@pytest.mark.parametrize("records", [None, "x", [1], [{"n": "5", "d0": "Closed Won"}], [{"d0": "Closed Won"}],
                                     [{"n": True, "d0": "Closed Won"}], [{"n": -1, "d0": "x"}], [{"n": 5}]])
def test_malformed_answers_print_nothing(records):
    with pytest.raises(E, match="expected form"):
        core.shape_counts(dims("StageName"), [["d0"]], records, RELEASED)


def test_too_many_groups():
    with pytest.raises(E, match="more than 200"):
        core.shape_counts(dims("StageName"), [["d0"]], [rec(9, d0="Closed Won")] * 201, RELEASED)


# ---- fill

def test_fill_query_and_shape():
    fields = core.parse_fill_fields(["Email__c", "notes__c", "Email__c"], D, NOTHING, FACTS)
    assert [f.name for f in fields] == ["Email__c", "Notes__c"]
    assert core.build_fill_query(D, fields, core.parse_filters(["IsWon = true"], D, RELEASED, FACTS)) == (
        "SELECT COUNT(Id) n, COUNT(Email__c) f0, COUNT(Notes__c) f1 FROM Opportunity WHERE IsWon = true")
    shape = lambda n, a, b: core.shape_fill(fields, [rec(n, f0=a, f1=b)], RELEASED)
    assert shape(100, 60, 0)["rows"] == [{"field": "Email__c", "filled": 60}, {"field": "Notes__c", "filled": 0}]
    assert shape(100, 100, 98)["rows"] == [{"field": "Email__c", "filled": 100}, {"field": "Notes__c",
                                                                                 "filled": None}]
    assert shape(100, 3, 95)["rows"] == [{"field": "Email__c", "filled": None}, {"field": "Notes__c", "filled": 95}]
    assert shape(4, 2, 2) == {"total": None, "rows": [], "min_cell": 5}
    assert shape(0, 0, 0) == shape(1, 1, 0) == shape(4, 4, 4)       # an empty object and a small one look the same
    for bad in ([], [rec(10, f0=11, f1=1)], [rec(10, f0="3", f1=1)], [rec(10, f0=3)], [rec("10", f0=3, f1=1)]):
        with pytest.raises(E, match="expected form"):
            core.shape_fill(fields, bad, RELEASED)
    with pytest.raises(E, match="cannot be counted"):
        core.parse_fill_fields(["Blob__c"], D, NOTHING, FACTS)
    with pytest.raises(E, match="1 to 10"):
        core.parse_fill_fields([], D, NOTHING, FACTS)
    for name, why in (("Gender__c", "gender"), ("Birthdate__c", "birthdate"), ("Classified__c", "compliance"),
                      ("Years__c", "Birthdate__c")):
        with pytest.raises(E, match=why):
            core.parse_fill_fields(["Email__c", name], D, NOTHING, FACTS)
    assert core.parse_fill_fields(["Gender__c"], D, policy({"Opportunity.Gender__c": "exact"}), FACTS)


# ---- configuration objects

def test_config_rows():
    cfg = describe("Trigger_Handler__c", extra=[fld("Class__c", "string"), fld("Owner_Email__c", "email")])
    released = policy(objects={"trigger_handler__c": ["class__c", "IsWon", "Owner_Email__c", "AccountId", "Copy__c"],
                               "Contact": ["*"]})
    fields = core.select_names(["Name", "Class__c", "Owner_Email__c", "AccountId", "Copy__c", "IsWon", "Notes__c",
                                "Total__c"], cfg, default_all=True)
    assert core.build_config_query(cfg, released, fields, 50) == (
        "SELECT Id, Name, Class__c, Owner_Email__c, AccountId, Copy__c, IsWon, Notes__c, Total__c "
        "FROM Trigger_Handler__c ORDER BY Id LIMIT 50")
    record = {"Id": "a01000000000001AAA", "Name": "Jane Doe", "Class__c": "X_TDTM",
              "Owner_Email__c": "jane@example.org", "AccountId": "001000000000001AAA", "Copy__c": 5, "IsWon": True,
              "Notes__c": "call Jane on 555-0100", "Total__c": 73482.19}
    row = core.show_config_row(cfg, fields, record, released, FACTS)
    # Only the fields the release names show; a name, free text and a roll-up it does not name do not, and a
    # hard type, a reference and a formula over a parent never do, even when a hand-edited policy names them.
    assert row == {"Id": "a01000000000001AAA", "Name": "<set>", "Class__c": "X_TDTM", "Owner_Email__c": "<set>",
                   "AccountId": "<set>", "Copy__c": "<set>", "IsWon": True, "Notes__c": "<set>", "Total__c": "<set>"}
    assert "Jane" not in repr(row) and "73482" not in repr(row)
    with pytest.raises(E, match="not released as a configuration object"):
        core.build_config_query(cfg, NOTHING, fields, 50)
    with pytest.raises(E, match="people"):
        core.build_config_query(describe("Contact"), released, core.select_names(["Name"], describe("Contact"),
                                                                                default_all=True), 50)
    for limit in (0, 201, "5", True):
        with pytest.raises(E, match="limit"):
            core.build_config_query(cfg, released, fields, limit)
    assert core.show_config_row(cfg, fields, None, released, FACTS)["Class__c"] is None
    assert core.show_config_row(cfg, fields, {"Id": {"x": 1}, "Class__c": [1]}, released, FACTS)["Id"] == "<unreadable>"
    assert core.show_config_row(cfg, fields, {"Class__c": [1]}, released, FACTS)["Class__c"] == "<unreadable>"


def test_all_fields_is_for_settings_objects_only():
    everything = policy(objects={"Settings__c": ["*"], "Rule__mdt": ["*"], "Trigger_Handler__c": ["*"]})
    record = {"Id": "a01000000000001AAA", "Name": "Default", "Notes__c": "x", "Email__c": "a@example.org",
              "AccountId": "001000000000001AAA", "Total__c": 5.0, "Score__c": 2.5}
    for name, setting in (("Settings__c", True), ("Rule__mdt", False)):
        cfg = describe(name, customSetting=setting)
        fields = core.select_names(["Name", "Notes__c", "Email__c", "AccountId", "Total__c", "Score__c"], cfg,
                                   default_all=True)
        assert core.show_config_row(cfg, fields, record, everything, FACTS) == {
            "Id": "a01000000000001AAA", "Name": "Default", "Notes__c": "x", "Email__c": "<set>",
            "AccountId": "<set>", "Total__c": "<set>", "Score__c": 2.5}
    handler = describe("Trigger_Handler__c")                 # a hand-edited "*" on an ordinary object shows nothing
    fields = core.select_names(["Name", "Notes__c"], handler, default_all=True)
    with pytest.raises(E, match="all its fields"):
        core.build_config_query(handler, everything, fields, 10)
    assert core.show_config_row(handler, fields, record, everything, FACTS) == {
        "Id": "a01000000000001AAA", "Name": "<set>", "Notes__c": "<set>"}
    no_longer = describe("Settings__c")                       # the object stopped being a custom setting
    assert core.show_config_row(no_longer, fields, record, everything, FACTS)["Name"] == "<set>"


# ---- test records

def test_test_record_values():
    registered = {"001000000000001AAA"}
    show = lambda name, value, pol=NOTHING: core.show_test_value(D, D.field(name), value, pol, FACTS, registered)
    assert show("Name", "Test Donor") == "Test Donor"
    assert show("Email__c", "test@example.org") == "test@example.org"       # a test record's own values are test data
    assert show("AccountId", "001000000000001AAA") == "001000000000001AAA"
    assert show("AccountId", "001000000000001") == "001000000000001AAA"
    assert show("AccountId", "001000000000002AAA") == "<unregistered 001>"
    assert show("AccountId", None) is None
    assert show("AccountId", "not an id") == "<unreadable>"
    assert show("Copy__c", 123456.0) == "<set>"                               # formula reading the parent Account
    assert show("Uses_User__c", True) == "<set>"
    assert show("Hidden_Formula__c", 3.0) == "<set>"
    assert show("Hidden_Formula__c", None) == "<blank>"
    assert show("Score__c", 12.5) == 12.5                                     # formula over its own fields
    assert show("Of_Formula__c", 13.5) == 13.5                                # and a formula over that one
    assert show("Total__c", 73482.19) == "<set>"                              # roll-up: the children's values
    assert show("Customer_Copy__c", "Real Customer Ltd") == "<set>"
    assert show("Alias_Copy__c", "Real Customer Ltd") == "<set>"              # a formula over a formula over a parent
    assert show("Alias_Twice__c", "Real Customer Ltdx") == "<set>"
    assert show("Lookup_Text__c", "001000000000002AAA") == "<set>"            # a lookup's id as text
    assert show("Session__c", "00D...!AQ4AQ") == "<set>"
    assert show("Loop_A__c", 1.0) == "<set>"                                  # no end to follow: not shown
    assert show("Total__c", 73482.19, policy({"Opportunity.Total__c": "exact"})) == 73482.19   # released: shown
    assert show("Amount", float("nan")) == "<unreadable>"
    assert show("BillingAddress", {"city": "Testville", "attributes": {"x": 1}, "geo": {"a": 1}}) == {
        "city": "Testville"}


def test_record_and_children_queries():
    fields = core.select_names(["Name", "StageName"], D, default_all=False)
    assert core.build_record_query(D, fields, "006000000000001") == (
        "SELECT Id, Name, StageName FROM Opportunity WHERE Id = '006000000000001AAA' LIMIT 1")
    with pytest.raises(E, match="--fields"):
        core.select_names([], D, default_all=False)
    lookup = core.parse_child("Opportunity.accountid", D)
    assert core.build_child_ids_query(D, lookup, "001000000000001AAA") == (
        "SELECT Id FROM Opportunity WHERE AccountId = '001000000000001AAA' ORDER BY Id LIMIT 201")
    assert core.build_children_query(D, fields, ["006000000000001AAA", "006000000000002AAA"]) == (
        "SELECT Id, Name, StageName FROM Opportunity WHERE Id IN ('006000000000001AAA', '006000000000002AAA')")
    for bad in ("Opportunity", "Opportunity.Name", "Opportunity.Account.Id", "Opportunity.Missing__c", None):
        with pytest.raises(E):
            core.parse_child(bad, D)
    with pytest.raises(E, match="no registered children"):
        core.build_children_query(D, fields, [])


@pytest.mark.parametrize("value,full", [("001000000000001", "001000000000001AAA"),
                                        ("001000000000001AAA", "001000000000001AAA"),
                                        ("001000000000001aaa", "001000000000001AAA"),
                                        ("006aZ00000BD3zq", "006aZ00000BD3zqQAD")])
def test_record_id(value, full):
    assert core.record_id(value) == full


@pytest.mark.parametrize("value", ["", "001", "001000000000001AAB", "001000000000001' OR Id != '", None, 5,
                                   "0010000000000 1", "001000000000001AAAA"])
def test_not_a_record_id(value):
    with pytest.raises(E, match="not a record id"):
        core.record_id(value)


# ---- describe

def test_describe_reads_cautiously():
    for payload in (None, {}, {"name": "X"}, {"name": "X Y", "fields": []}, {"name": 5, "fields": []}):
        with pytest.raises(E, match="describe"):
            core.parse_describe(payload)
    stated = {"type": "string", "calculated": False, "encrypted": False, "compoundFieldName": None}
    odd = core.parse_describe({"name": "X__c", "fields": [{"name": "A__c", **stated}, {"name": "B C"}, "junk",
                                                          {"type": "x"}]})
    info = odd.field("a__c")
    assert (info.type, info.groupable, info.filterable, info.encrypted) == ("string", False, False, False)
    assert list(odd.fields) == ["a__c"] and not odd.queryable
    for name in ("A__c; DROP", "A__c)", "", None, "Ａ__c"):
        with pytest.raises(E):
            odd.field(name)
    with pytest.raises(E):
        core.object_name("Opportunity; SELECT")


# ---- whatever the caller types, the query is one of a small family

import random
import re

_NAME = r"[A-Za-z][A-Za-z0-9_]*"
_FIELD = rf"(?:{_NAME}|RecordType\.(?:DeveloperName|Name))"
_STRING = r"'(?:[^'\\\x00-\x1f]|\\['\\])*'"
_LITERAL = (rf"(?:null|true|false|-?\d+(?:\.\d+)?|\d{{4}}-\d{{2}}-\d{{2}}(?:T\d{{2}}:\d{{2}}:\d{{2}}Z)?"
            rf"|[A-Z_0-9]+(?::\d+)?|{_STRING})")
_CONDITION = rf"{_FIELD} (?:(?:=|!=|<=|>=|<|>) {_LITERAL}|(?:NOT IN|IN) \({_STRING}(?:, {_STRING})*\))"
_WHERE = rf"(?: WHERE {_CONDITION}(?: AND {_CONDITION})*)?"
_EXPRESSION = rf"(?:{_FIELD}|CALENDAR_(?:YEAR|QUARTER|MONTH)\({_NAME}\))"
COUNTS_GRAMMAR = re.compile(rf"SELECT (?:{_EXPRESSION} d\d, )*COUNT\(Id\) n FROM {_NAME}{_WHERE}"
                            rf"(?: GROUP BY {_EXPRESSION}(?:, {_EXPRESSION})* LIMIT 201)?\Z")
FILL_GRAMMAR = re.compile(rf"SELECT COUNT\(Id\) n(?:, COUNT\({_NAME}\) f\d)+ FROM {_NAME}{_WHERE}\Z")

PIECES = ["StageName", "IsWon", "Amount", "CloseDate", "CreatedDate", "Name", "Email__c", "Notes__c", "Tags__c",
          "RecordType.DeveloperName", "RecordType.Name", "Account.Name", "Score__c", "Gender__c", "Id",
          "=", "!=", "<", ">=", " IN ", " NOT IN ", " LIKE ", " OR ", " AND ", "null", "true", "false", "5000",
          "75043", "2026-01-01", "2026-01-15T10:30:00Z", "THIS_YEAR", "LAST_N_MONTHS:6", "Closed Won", "Prospecting",
          "Women's Fund", "Donation", "Major Grant", "'", '"', "\\", "(", ")", ",", ";", "--", "/*", "*/", " ", "\t",
          "\n", "\x00", "%", "_", "SELECT Id FROM Contact", "FIELDS(ALL)", "COUNT()", "LIMIT 1", "OFFSET 5",
          "GROUP BY Name", "WITH SECURITY_ENFORCED", "ALL ROWS", "FOR UPDATE", ":month", ":year", ":", "’",
          "е", "＇", "''", "\\'", "' OR Name != '", "') OR (Name != '", "1 OR 1=1", "TYPEOF", "toLabel(StageName)"]


def _hostile(rng):
    return "".join(rng.choice(PIECES) for _ in range(rng.randint(1, 7)))


def test_every_query_torque_builds_fits_the_grammar():
    rng = random.Random(20261008)
    everything = policy({"Opportunity.StageName": "exact", "Opportunity.IsWon": "exact", "Opportunity.Amount": "exact",
                         "Opportunity.CloseDate": "exact", "Opportunity.CreatedDate": "exact",
                         "Opportunity.RecordTypeId": "exact", "Opportunity.Score__c": "exact",
                         "Opportunity.Gender__c": "exact", "Opportunity.Tags__c": "exact"})
    built = 0
    for _ in range(6000):
        pol = rng.choice([everything, RELEASED, NOTHING])
        try:
            dimensions = [core.parse_dimension(_hostile(rng), D, pol, FACTS) for _ in range(rng.randint(0, 3))]
            filters = core.parse_filters([_hostile(rng) for _ in range(rng.randint(0, 3))], D, pol, FACTS)
            soql, _ = core.build_counts_query(D, dimensions, filters)
            assert COUNTS_GRAMMAR.match(soql), soql
            built += 1
            fields = core.parse_fill_fields([rng.choice(PIECES) for _ in range(rng.randint(1, 3))], D, pol, FACTS)
            fill = core.build_fill_query(D, fields, filters)
            assert FILL_GRAMMAR.match(fill), fill
        except core.GuardedError as exc:
            assert "\x00" not in str(exc) and "\n" not in str(exc) and len(str(exc)) < 400
    assert built > 300          # the generator does reach the builder, not only the refusals


def test_the_grammar_itself_refuses_the_obvious():
    for bad in ("SELECT COUNT(Id) n FROM Opportunity WHERE Name = 'a' OR Name != ''",
                "SELECT Name d0, COUNT(Id) n FROM Opportunity GROUP BY Name",
                "SELECT COUNT(Id) n FROM Opportunity WHERE StageName = 'a' LIMIT 1",
                "SELECT COUNT(Id) n FROM Opportunity, Contact",
                "SELECT COUNT(Id) n FROM Opportunity WHERE Id IN (SELECT Id FROM Contact)",
                "SELECT COUNT(Id) n FROM Opportunity WHERE StageName = 'a\\' OR Name != \\''x"):
        assert not COUNTS_GRAMMAR.match(bad) or bad.endswith("LIMIT 201"), bad


# ---- round 3: what a formula is made from, as far as it can be followed

HIDDEN = describe(extra=[
    # Hidden_Source__c is not in describe: this login cannot see it, the formula still carries it
    fld("Alias_Hidden__c", "double", calculated=True, calculatedFormula="Hidden_Source__c"),
    fld("Alias_Hidden_Text__c", "string", calculated=True, calculatedFormula='Hidden_Source__c & ""'),
    fld("Over_Hidden__c", "double", calculated=True, calculatedFormula="Alias_Hidden__c + 1"),
    fld("Plain__c", "double", calculated=True,
        calculatedFormula='IF(ISBLANK(TEXT(StageName)), 1E5, /* Name, Hidden_Source__c */ Amount * 2.5e3) + LEN("Nobody__c")'),
    fld("Flag__c", "boolean", calculated=True, calculatedFormula="AND(IsWon, NOT(ISNULL(Amount)), TRUE, true, NULL = null)"),
    fld("Mirror__c", "string", calculated=True,
        calculatedFormula='IF(ISBLANK(TEXT(Classified__c)), "", TEXT(Classified__c))'),
    fld("Mirror_Flag__c", "boolean", calculated=True, calculatedFormula="NOT(ISBLANK(Mirror__c))"),
    fld("Level2__c", "double", calculated=True, calculatedFormula="Years__c + 1"),
    fld("Level3__c", "double", calculated=True, calculatedFormula="Level2__c * 2"),
    fld("City_Flag__c", "boolean", calculated=True, calculatedFormula="NOT(ISBLANK(BillingCity))"),
])


def test_formula_refs_tell_fields_from_functions_and_from_what_is_not_there():
    refs = lambda name: tuple([f.name for f in part] if part and hasattr(part[0], "name") else list(part)
                              for part in core.formula_refs(HIDDEN, HIDDEN.field(name)))
    assert refs("Plain__c") == (["StageName", "Amount"], [])          # functions, numbers, strings, comments: none
    assert refs("Flag__c") == (["IsWon", "Amount"], [])
    assert refs("Alias_Hidden__c") == ([], ["Hidden_Source__c"])
    assert refs("Over_Hidden__c") == (["Alias_Hidden__c"], [])
    assert core.self_contained(HIDDEN, HIDDEN.field("Plain__c")) and core.self_contained(HIDDEN, HIDDEN.field("Flag__c"))


def test_a_formula_over_a_field_describe_does_not_show():
    show = lambda name, value: core.show_test_value(HIDDEN, HIDDEN.field(name), value, NOTHING, FACTS, set())
    assert show("Alias_Hidden__c", 73482.19) == "<set>"
    assert show("Alias_Hidden_Text__c", "Real Customer Ltd") == "<set>"
    assert show("Over_Hidden__c", 73483.19) == "<set>"                 # and a formula over that one
    assert show("Plain__c", 5.0) == 5.0
    for name in ("Alias_Hidden__c", "Over_Hidden__c"):
        assert not core.self_contained(HIDDEN, HIDDEN.field(name))
        with pytest.raises(E, match="cannot be released"):
            core.check_release(HIDDEN, HIDDEN.field(name), "exact", FACTS, acknowledged=True)
        with pytest.raises(E, match="tested for null"):
            core.parse_filter(f"{name} != null", HIDDEN, NOTHING, FACTS)
        with pytest.raises(E, match="not counted"):
            core.parse_fill_fields([name], HIDDEN, NOTHING, FACTS)
    assert "not a field this login can see" in core.blocked_reason(HIDDEN, HIDDEN.field("Alias_Hidden__c"), FACTS)
    core.check_release(HIDDEN, HIDDEN.field("Plain__c"), "exact", FACTS, acknowledged=False)
    cfg = describe("Trigger_Handler__c", extra=[
        fld("Alias_Hidden__c", "double", calculated=True, calculatedFormula="Hidden_Source__c")])
    with pytest.raises(E, match="calculated"):
        core.check_object_release(cfg, ["Alias_Hidden__c"], FACTS, records=True)
    hand_edited = policy(objects={"Trigger_Handler__c": ["Alias_Hidden__c"]})
    fields = core.select_names(["Alias_Hidden__c"], cfg, default_all=False)
    assert core.show_config_row(cfg, fields, {"Id": "a01000000000001AAA", "Alias_Hidden__c": 73482.19}, hand_edited,
                                FACTS)["Alias_Hidden__c"] == "<set>"


@pytest.mark.parametrize("name,why", [
    ("Mirror__c", "its formula uses Classified__c"), ("Mirror_Flag__c", "its formula uses Mirror__c"),
    ("Level2__c", "its formula uses Years__c"), ("Level3__c", "its formula uses Level2__c"),
    ("Copy__c", "reads another record"), ("Hidden_Formula__c", "did not give its formula"),
    ("Loop_A__c", "its formula uses Loop_B__c"),
])
def test_is_it_filled_in_follows_what_a_formula_is_made_from(name, why):
    with pytest.raises(E, match=why):
        core.parse_filter(f"{name} != null", HIDDEN, NOTHING, FACTS)
    with pytest.raises(E, match=why):
        core.parse_fill_fields([name], HIDDEN, NOTHING, FACTS)


def test_presence_of_ordinary_and_derived_fields_is_still_answered():
    for name in ("Email__c", "Notes__c", "BillingCity", "Score__c", "Of_Formula__c", "Plain__c", "Total__c", "Flag__c"):
        assert core.presence_reason(HIDDEN, HIDDEN.field(name), NOTHING, FACTS) == "", name
    # a formula over an address part needs the acknowledgement to be released, and is not counted before
    assert core.presence_reason(HIDDEN, HIDDEN.field("City_Flag__c"), NOTHING, FACTS) == "its formula uses BillingCity"


def test_sensitive_formulas_need_the_acknowledgement_at_any_depth():
    for name in ("Level2__c", "Level3__c"):
        assert core.acknowledgement_reason(HIDDEN, HIDDEN.field(name))
    assert core.acknowledgement_reason(HIDDEN, HIDDEN.field("Plain__c")) == ""


def test_every_field_of_a_setting_still_masks_the_sensitive_ones():
    setting = describe("Settings__c", customSetting=True, extra=[
        fld("Salary__c", "currency"), fld("Batch_Size__c", "double"),
        fld("Salary_Band__c", "double", calculated=True, calculatedFormula="Salary__c / 1000"),
        fld("Band_Label__c", "double", calculated=True, calculatedFormula="Salary_Band__c + 0")])
    assert core.check_object_release(setting, [], FACTS) == ["*"]
    fields = core.select_names(["Salary__c", "Batch_Size__c", "Salary_Band__c", "Band_Label__c", "Gender__c"], setting,
                               default_all=False)
    record = {"Id": "a01000000000001AAA", "Salary__c": 73482.19, "Batch_Size__c": 200.0, "Salary_Band__c": 73.48,
              "Band_Label__c": 73.48, "Gender__c": "F"}
    everything = policy(objects={"Settings__c": ["*"]})
    assert core.show_config_row(setting, fields, record, everything, FACTS) == {
        "Id": "a01000000000001AAA", "Salary__c": "<set>", "Batch_Size__c": 200.0, "Salary_Band__c": "<set>",
        "Band_Label__c": "<set>", "Gender__c": "<set>"}
    with pytest.raises(E, match="acknowledge-sensitive"):
        core.check_object_release(setting, ["Salary__c"], FACTS)
    named = core.check_object_release(setting, ["Salary__c", "Batch_Size__c"], FACTS, sensitive=True)
    assert core.show_config_row(setting, fields, record, policy(objects={"Settings__c": named}), FACTS) == {
        "Id": "a01000000000001AAA", "Salary__c": 73482.19, "Batch_Size__c": 200.0, "Salary_Band__c": "<set>",
        "Band_Label__c": "<set>", "Gender__c": "<set>"}


def test_person_account_fields_are_read_on_contact():
    account = describe("Account", extra=[
        fld("IsPersonAccount", "boolean"), fld("Clearance__pc", "picklist", values=["A", "B"]),
        fld("PersonDoNotCall", "boolean"), fld("PersonLeadSource", "picklist", values=["Web"]),
        fld("Personal_Tier__c", "picklist", values=["Gold"]), fld("Rating", "picklist", values=["Hot"]),
        fld("Over_Person__c", "boolean", calculated=True, calculatedFormula='ISPICKVAL(Clearance__pc, "A")')])
    released = policy({"Account.Clearance__pc": "exact", "Account.Rating": "exact", "Account.Personal_Tier__c": "exact"})
    for name in ("Clearance__pc", "PersonDoNotCall", "PersonLeadSource"):
        info = account.field(name)
        assert core.person_account_field(account, info)
        with pytest.raises(E, match="person-account field"):
            core.check_release(account, info, "exact", FACTS, acknowledged=True)
        assert core.released(account, info, released, FACTS) is None            # a hand-edited release counts for nothing
        with pytest.raises(E, match="person-account field"):
            core.parse_filter(f"{name} != null", account, released, FACTS)
        with pytest.raises(E, match="person-account field"):
            core.parse_fill_fields([name], account, released, FACTS)
    with pytest.raises(E, match="not released"):
        core.parse_dimension("Clearance__pc", account, released, FACTS)
    with pytest.raises(E, match="its formula uses Clearance__pc"):
        core.parse_filter("Over_Person__c = null", account, released, FACTS)
    for name in ("Rating", "Personal_Tier__c"):                                 # the account's own fields are untouched
        assert not core.person_account_field(account, account.field(name))
        core.check_release(account, account.field(name), "exact", FACTS, acknowledged=False)
    assert not core.person_account_field(D, D.field("StageName"))               # no person accounts: nothing changes


# ---- round 4: the formula reader, and what a named configuration release must check

@pytest.mark.parametrize("formula", [
    '"/*" & Account.Name & "*/"',                     # a comment mark inside a string hides nothing
    "'/*' & Account.Name & '*/'",
    '/* " */ Account.Name /* " */',                   # nor does a quote inside a comment
    '"a" & Hidden_Source__c & "b"',
    '"/*" & Hidden_Source__c & "*/"',
    '"\\"/*" & Account.Name & "*/\\""',
    'Amount & "never closed',                         # what does not end cannot be followed
    "Amount /* never closed",
    '"/*" & $User.Id & "*/"',
])
def test_strings_and_comments_in_a_formula_do_not_hide_what_it_reads(formula):
    reader = describe(extra=[fld("Alias__c", "string", calculated=True, calculatedFormula=formula)])
    info = reader.field("Alias__c")
    assert not core.self_contained(reader, info), formula
    assert core.show_test_value(reader, info, "Real Customer Ltd", NOTHING, FACTS, set()) == "<set>"
    assert core.blocked_reason(reader, info, FACTS)
    assert core.presence_reason(reader, info, NOTHING, FACTS)


@pytest.mark.parametrize("formula,fields", [
    ('"Account.Name" & TEXT(Amount)', ["Amount"]), ('/* Account.Name */ Amount', ["Amount"]),
    ('"/* not a comment" & TEXT(Amount) & "*/"', ["Amount"]), ("Amount /* a */ + /* b */ Amount", ["Amount"]),
    ('IF(IsWon, "it\'s won", \'say "no"\')', ["IsWon"]),
])
def test_what_is_only_text_in_a_formula_is_not_read_as_a_field(formula, fields):
    reader = describe(extra=[fld("Calc__c", "string", calculated=True, calculatedFormula=formula)])
    found, unseen = core.formula_refs(reader, reader.field("Calc__c"))
    assert [f.name for f in found] == fields and unseen == []
    assert core.self_contained(reader, reader.field("Calc__c"))


def test_a_named_configuration_release_checks_what_a_formula_is_made_from():
    cfg = describe("Configuration__c", extra=[
        fld("Salary__c", "currency"), fld("Band__c", "currency", calculated=True, calculatedFormula="Salary__c + 0"),
        fld("Band_Label__c", "currency", calculated=True, calculatedFormula="Band__c * 1"),
        fld("Mailing__c", "address"),
        fld("Mailing__StateCode__s", "picklist", values=["CA"], compoundFieldName="Mailing__c"),
        fld("Mailing__City__s", "string", compoundFieldName="Mailing__c")])
    for name in ("Band__c", "Band_Label__c", "Salary__c"):
        with pytest.raises(E, match="acknowledge-sensitive"):
            core.check_object_release(cfg, [name], FACTS, records=True)
        assert core.check_object_release(cfg, [name], FACTS, records=True, sensitive=True) == [name]
    for name in ("Mailing__StateCode__s", "Mailing__City__s"):       # no row shows part of an address
        with pytest.raises(E, match="address"):
            core.check_object_release(cfg, [name], FACTS, records=True, sensitive=True)
        hand_edited = policy(objects={"Configuration__c": [name]})
        fields = core.select_names([name], cfg, default_all=False)
        assert core.show_config_row(cfg, fields, {"Id": "a01000000000001AAA", name: "CA"}, hand_edited, FACTS)[name] == "<set>"


def test_a_formula_is_no_way_around_what_a_configuration_row_never_shows():
    cfg = describe("Configuration__c", extra=[
        fld("Mailing__c", "address"), fld("Mailing__City__s", "string", compoundFieldName="Mailing__c"),
        fld("Mailing__StateCode__s", "picklist", values=["CA"], compoundFieldName="Mailing__c"),
        fld("City_Copy__c", "string", calculated=True, calculatedFormula='Mailing__City__s & ""'),
        fld("City_Again__c", "string", calculated=True, calculatedFormula="City_Copy__c"),
        fld("State_Copy__c", "string", calculated=True, calculatedFormula="TEXT(Mailing__StateCode__s)"),
        fld("Email_Copy__c", "string", calculated=True, calculatedFormula="Email__c"),
        fld("Classified_Copy__c", "string", calculated=True, calculatedFormula="TEXT(Classified__c)"),
        fld("Fine__c", "string", calculated=True, calculatedFormula='TEXT(Amount) & "x"')])
    for name in ("City_Copy__c", "City_Again__c", "State_Copy__c", "Email_Copy__c", "Classified_Copy__c"):
        with pytest.raises(E, match="cannot be shown"):
            core.check_object_release(cfg, [name], FACTS, records=True, sensitive=True)
        hand_edited = policy(objects={"Configuration__c": [name]})
        fields = core.select_names([name], cfg, default_all=False)
        row = core.show_config_row(cfg, fields, {"Id": "a01000000000001AAA", name: "Real City"}, hand_edited, FACTS)
        assert row[name] == "<set>", name
    assert core.check_object_release(cfg, ["Fine__c"], FACTS, records=True) == ["Fine__c"]


# ---- round 6: the org's classification answer is read strictly

def row(name, compliance=None, security=None, kind="Text"):
    return {"QualifiedApiName": name, "DataType": kind, "ComplianceGroup": compliance, "SecurityClassification": security}


@pytest.mark.parametrize("records", [
    [{"QualifiedApiName": "StageName"}],                                            # a key is missing
    [{**row("StageName"), "ComplianceGroup": ["PII"]}], [{**row("StageName"), "ComplianceGroup": 1}],
    [{**row("StageName"), "SecurityClassification": {"value": "Restricted"}}],
    [{**row("StageName"), "DataType": 5}], [row("StageName"), "junk"], [{**row("StageName"), "QualifiedApiName": None}],
    {"records": []}, "text", None,
])
def test_an_answer_this_code_does_not_understand_is_not_read_as_unclassified(records):
    facts = core.parse_facts(records)
    assert not facts.readable
    with pytest.raises(E, match="classification could not be read"):
        core.require_facts(RELEASED, facts)


def test_a_field_the_answer_has_no_row_for_is_kept_back_when_classification_is_required():
    listed = core.parse_facts([row("StageName"), row("IsWon"), row("BillingAddress", "PII"), row("Fiscal"),
                               row("Amount", None, "Internal")])
    strict = core.require_facts(RELEASED, listed)
    assert strict.strict and core.require_facts(policy(classification="ignore"), listed).strict is False
    assert core.hard_reason(D, D.field("StageName"), strict) == ""
    assert "no row" in core.hard_reason(D, D.field("CloseDate"), strict)             # not in the answer
    assert core.released(D, D.field("CloseDate"), RELEASED, strict) is None
    assert core.released(D, D.field("StageName"), RELEASED, strict) == "exact"
    with pytest.raises(E, match="no row"):
        core.parse_filter("CloseDate = null", D, RELEASED, strict)
    # a part of a compound field has no row of its own: the compound's row is its classification
    assert core.hard_reason(D, D.field("FiscalYear"), strict) == ""
    assert "compliance" in core.hard_reason(D, D.field("BillingStateCode"), strict)
    loose = core.require_facts(policy(classification="ignore"), listed)
    assert core.hard_reason(D, D.field("CloseDate"), loose) == ""
    assert "compliance" in core.hard_reason(D, D.field("BillingStateCode"), loose)   # a row that is there still counts


def test_a_type_the_rules_do_not_know_shows_no_value():
    # a well-formed type word that is none of Salesforce's field types the rules know: what it
    # means is unknown, so its values are kept back in every lane, like those of a hard type
    stated = {"calculated": False, "calculatedFormula": None, "encrypted": False, "filterable": True,
              "groupable": True, "aggregatable": True, "compoundFieldName": None}
    setting = core.parse_describe({"name": "Setting__c", "queryable": True, "customSetting": True, "fields": [
        {"name": "Reply_To__c", "type": "unknown", **stated}, {"name": "Mode__c", "type": "futuretype", **stated},
        {"name": "Plain__c", "type": "string", **stated}, {"name": "Flag__c", "type": "boolean", **stated}]})
    everything = core.Policy(objects={"setting__c": frozenset({"*"})})
    facts = core.OrgFacts(True, {name: ("", "", "Text") for name in ("reply_to__c", "mode__c", "plain__c", "flag__c")},
                          True)
    fields = [setting.field(name) for name in ("Reply_To__c", "Mode__c", "Plain__c", "Flag__c")]
    record = {"Reply_To__c": "person@example.com", "Mode__c": "x", "Plain__c": "kept", "Flag__c": True}
    assert core.show_config_row(setting, fields, record, everything, facts) == {
        "Reply_To__c": "<set>", "Mode__c": "<set>", "Plain__c": "kept", "Flag__c": True}
    for name in ("Reply_To__c", "Mode__c"):
        assert "do not know" in core.hard_reason(setting, setting.field(name), facts)
        with pytest.raises(E):
            core.parse_dimension(name, setting, NOTHING, facts)
    assert core.KNOWN_TYPES >= core.EXACT_TYPES | core.HARD_TYPES | {"string", "textarea", "reference", "id"}
    # the same in the lanes that need no release: a registered test record, a fill count, a null filter
    odd = setting.field("Reply_To__c")
    assert core.show_test_value(setting, odd, "SECRET", NOTHING, facts, set()) == "<set>"
    assert core.show_test_value(setting, setting.field("Plain__c"), "kept", NOTHING, facts, set()) == "kept"
    assert "do not know" in core.presence_reason(setting, odd, NOTHING, facts)
    with pytest.raises(E):
        core.parse_fill_fields(["Reply_To__c"], setting, NOTHING, facts)
    with pytest.raises(E):
        core.parse_filter("Reply_To__c != null", setting, NOTHING, facts)


def test_a_formula_made_from_a_type_the_rules_do_not_know_shows_no_value_either():
    stated = {"calculatedFormula": None, "encrypted": False, "filterable": True, "groupable": True, "aggregatable": True,
              "compoundFieldName": None}
    thing = core.parse_describe({"name": "Thing__c", "queryable": True, "fields": [
        {"name": "Hidden__c", "type": "futuretype", "calculated": False, **stated},
        {"name": "Mirror__c", "type": "string", "calculated": True, **{**stated, "calculatedFormula": "Hidden__c"}},
        {"name": "Again__c", "type": "string", "calculated": True, **{**stated, "calculatedFormula": "Mirror__c & 'x'"}},
        {"name": "Plain__c", "type": "string", "calculated": False, **stated},
        {"name": "Of_Plain__c", "type": "string", "calculated": True, **{**stated, "calculatedFormula": "Plain__c & 'x'"}}]})
    facts = core.OrgFacts(True, {name: ("", "", "Text") for name in thing.fields}, True)
    for name in ("Hidden__c", "Mirror__c", "Again__c"):
        assert core.show_test_value(thing, thing.field(name), "SECRET", NOTHING, facts, set()) == "<set>", name
        assert core.presence_reason(thing, thing.field(name), NOTHING, facts), name
        with pytest.raises(E):
            core.parse_fill_fields([name], thing, NOTHING, facts)
        with pytest.raises(E):
            core.parse_filter(f"{name} != null", thing, NOTHING, facts)
    assert "do not know" in core.presence_reason(thing, thing.field("Mirror__c"), NOTHING, facts)
    assert not core.self_contained(thing, thing.field("Mirror__c")) and core.self_contained(thing, thing.field("Of_Plain__c"))
    assert core.show_test_value(thing, thing.field("Of_Plain__c"), "kept", NOTHING, facts, set()) == "kept"
    assert core.parse_fill_fields(["Of_Plain__c"], thing, NOTHING, facts)


def test_a_part_of_a_compound_field_needs_its_parent_in_the_describe():
    # Salesforce lists the parent (an address, a name, Fiscal) in the same describe; a part is
    # protected by what the parent is, so a describe without the parent is not used
    stated = {"calculated": False, "calculatedFormula": None, "encrypted": False, "compoundFieldName": None}
    part = {**stated, "name": "Mailing__City__s", "type": "string", "compoundFieldName": "Mailing__c"}
    with pytest.raises(E) as refusal:
        core.parse_describe({"name": "Site__c", "queryable": True, "fields": [
            {"name": "Id", "type": "id", **stated}, part]})
    assert str(refusal.value) == "the org's describe for this object could not be read"
    whole = core.parse_describe({"name": "Site__c", "queryable": True, "fields": [
        {"name": "Id", "type": "id", **stated}, {"name": "Mailing__c", "type": "address", **stated}, part,
        {**stated, "name": "Name", "type": "string", "compoundFieldName": "Name"}]})
    assert "address" in core.hard_reason(whole, whole.field("Mailing__City__s"), core.OrgFacts())
    assert D.field("FiscalYear").compound == "Fiscal" and "fiscal" in D.fields


def test_a_describe_that_does_not_state_the_protective_facts_is_not_used():
    # the type as a plain word, whether the field is calculated (and from what), whether it is
    # encrypted, which compound field it is a part of: Salesforce states all of them for every
    # field, and the rules protect by them
    good = {"name": "Reply_To__c", "type": "email", "calculated": False, "calculatedFormula": None, "encrypted": False,
            "compoundFieldName": None}
    assert core.parse_describe({"name": "Setting__c", "fields": [good]}).field("Reply_To__c").type == "email"
    for change in ({"type": "ORG-SENTINEL-real.person@example.com"}, {"type": "one\ntwo"}, {"type": ["x"]},
                   {"type": None}, {"type": ""}, {"calculated": None}, {"calculated": "true"}, {"calculated": 1},
                   {"encrypted": None}, {"encrypted": "false"}, {"calculatedFormula": 5},
                   {"calculatedFormula": "Account.Name"},                # a formula on a field that says it has none
                   {"compoundFieldName": 5}, {"compoundFieldName": ""}, {"compoundFieldName": "a b"},
                   {"compoundFieldName": ["Mailing__c"]}, {"compoundFieldName": False},
                   "type", "calculated", "encrypted", "compoundFieldName"):      # the fact left out
        item = {k: v for k, v in good.items() if k != change} if isinstance(change, str) else {**good, **change}
        with pytest.raises(E) as refusal:
            core.parse_describe({"name": "Setting__c", "queryable": True, "customSetting": True, "fields": [item]})
        assert str(refusal.value) == "the org's describe for this object could not be read", change
    formula = {**good, "type": "string", "calculated": True, "calculatedFormula": "Account.Name"}
    assert core.parse_describe({"name": "Setting__c", "fields": [formula]}).field("Reply_To__c").calculated


def test_a_part_of_an_address_stays_protected_whatever_the_describe_says_about_its_parent():
    # the part of an address is kept back because of what its parent is; a describe that states the
    # parent as anything but a field's name (a number, nothing at all) is not used, so the part
    # never reads as an ordinary text field
    stated = {"calculated": False, "calculatedFormula": None, "encrypted": False, "filterable": True,
              "groupable": True, "aggregatable": True, "compoundFieldName": None}

    def site(parent):
        city = {**stated, "name": "Mailing__City__s", "type": "string"}
        if parent == "left out":
            del city["compoundFieldName"]
        else:
            city["compoundFieldName"] = parent
        return {"name": "Site__c", "queryable": True, "customSetting": True, "fields": [
            {**stated, "name": "Id", "type": "id"}, {**stated, "name": "Mailing__c", "type": "address"}, city]}

    for parent in (5, "left out", "", 0, True, ["Mailing__c"], "Mailing c"):
        with pytest.raises(E) as refusal:
            core.parse_describe(site(parent))
        assert str(refusal.value) == "the org's describe for this object could not be read", parent
    whole = core.parse_describe(site("Mailing__c"))
    everything = core.Policy(objects={"site__c": frozenset({"*"})})
    row = core.show_config_row(whole, [whole.field("Mailing__City__s")], {"Mailing__City__s": "VALUE"}, everything,
                               core.OrgFacts())
    assert row == {"Mailing__City__s": "<set>"}
    assert "address" in core.hard_reason(whole, whole.field("Mailing__City__s"), core.OrgFacts())


def test_a_field_listed_twice_is_an_answer_that_is_not_used():
    # Salesforce lists a field once. If an answer lists it twice (an email field, then the same name as
    # a checkbox), neither row can be taken for the truth: the describe, or the classification, is not used
    stated = {"calculated": False, "calculatedFormula": None, "encrypted": False, "compoundFieldName": None}
    for second in ("Reply_To__c", "reply_to__c", "REPLY_TO__C"):
        with pytest.raises(E) as refusal:
            core.parse_describe({"name": "Setting__c", "queryable": True, "customSetting": True, "fields": [
                {**stated, "name": "Reply_To__c", "type": "email"}, {**stated, "name": second, "type": "boolean"}]})
        assert str(refusal.value) == "the org's describe for this object could not be read"
    row = {"ComplianceGroup": None, "SecurityClassification": None, "DataType": "Text"}
    listed = [{**row, "QualifiedApiName": "Notes__c", "ComplianceGroup": "PII"}, {**row, "QualifiedApiName": "notes__c"}]
    assert not core.parse_facts(listed).readable
    assert core.parse_facts(listed[:1]).readable and core.parse_facts(listed[:1]).classified("Notes__c")
