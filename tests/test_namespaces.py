from torque.namespaces import find_namespaces


def test_metadata_args_and_manifest(tmp_path):
    manifest = tmp_path / "package.xml"
    manifest.write_text("<Package xmlns=\"http://soap.sforce.com/2006/04/metadata\"><types>"
                        "<members>npsp__Trigger_Handler__c</members>"
                        "<name>CustomObject</name></types></Package>", encoding="utf-8")
    argv = ["sf", "project", "deploy", "start", "-m", "CustomField:Account.npe01__Pref__c",
            "--manifest", str(manifest), "-o", "x"]
    assert find_namespaces(argv, tmp_path) == ["npe01", "npsp"]


def test_relative_manifest(tmp_path):
    (tmp_path / "package.xml").write_text("<Package><types><members>outfunds__Funding_Request__c</members>"
                                          "<name>CustomObject</name></types></Package>", encoding="utf-8")
    assert find_namespaces(["-x", "package.xml"], tmp_path) == ["outfunds"]


def test_extra_and_unrelated(tmp_path):
    assert find_namespaces(["-m", "CustomObject:abc__Thing__c"], tmp_path) == []
    assert find_namespaces(["-m", "CustomObject:abc__Thing__c"], tmp_path, ("abc",)) == ["abc"]
    assert find_namespaces(["-m", "CustomObject:Plain__c"], tmp_path) == []


def test_unreadable_manifest_is_ignored(tmp_path):
    assert find_namespaces(["--manifest", str(tmp_path / "missing.xml")], tmp_path) == []
    (tmp_path / "bad.xml").write_text("<Package>", encoding="utf-8")
    assert find_namespaces(["--manifest", "bad.xml"], tmp_path) == []
