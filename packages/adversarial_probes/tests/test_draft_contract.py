"""Offline source/CLI regressions; no Salesforce or Apex execution."""
import contextlib
import io
from pathlib import Path
from unittest.mock import patch

import pytest

from jsc_probes.apex import analyze_class, generate_probes_for_class, MethodSig, parse_methods
from jsc_probes.cli import main, write_test_class


def source(body, modifiers="public"):
    return f"{modifiers} class Example {{ {body} }}"


def generate(body, modifiers="public"):
    return generate_probes_for_class(source(body, modifiers), "Example")


def test_own_static_and_instance_receiver():
    output = generate("public static String first(String x){return x;} public Integer second(Integer x){return x;}")
    assert "String actual = Example.first((String)null);" in output
    assert "Integer actual = new Example().second((Integer)null);" in output
    assert "new Example().first" not in output and "Example.second" not in output


def test_comments_strings_and_nested_scope():
    output = generate("/* public void fake(){} */ public String note='public void ghost(){}'; public class Inner { public void nested(){} } public void real(){}")
    assert "new Example().real();" in output
    for name in ("fake(", "ghost(", "nested("):
        assert name not in output
    assert "SCOPE: Nested" in output
    assert "testDraftNeedsReview" not in output


def test_uppercase_keywords_and_annotation_arguments():
    output = generate("@AuraEnabled(cacheable=true) PUBLIC STATIC STRING echo(STRING value){return value;}", "PUBLIC WITH SHARING")
    assert "STRING actual = Example.echo((STRING)null);" in output


def test_overload_null_arguments_remain_typed():
    output = generate("public static String f(String value){return value;} public static Integer f(Integer value){return value;}")
    assert "Example.f((String)null)" in output
    assert "Example.f((Integer)null)" in output
    assert "Example.f(null)" not in output
    assert output.count("System.assert(false, 'DRAFT:") == 2


@pytest.mark.parametrize("typename,empty", [
    ("list < account >", "new list<account>()"),
    ("SET < String >", "new SET<String>()"),
    ("Map<String,List<Decimal>>", "new Map<String,List<Decimal>>()"),
    ("Account[]", "new Account[]{}"),
])
def test_collection_types(typename, empty):
    output = generate(f"public static void f({typename} values, Boolean flag){{}}")
    assert f"Example.f({empty}, (Boolean)null);" in output
    assert output.count("System.assert(false, 'DRAFT:") == 2


@pytest.mark.parametrize("params", ["String a, List<Account broken", "String a,", "String a, Map<String> wrong", "void value", "String", "@Unknown String value", "List<List<String> value"])
def test_malformed_parameter_is_not_silently_dropped(params):
    output = generate(f"public static void f({params}){{}}")
    assert "UNSUPPORTED f(" in output
    assert "Example.f(" not in output
    assert "testDraftNeedsReview" in output
    with pytest.raises(ValueError):
        MethodSig("f", params, True).parameters()


@pytest.mark.parametrize("constructor", ["public Example(String x){}", "private Example(){}"])
def test_unsupported_constructor_keeps_static_methods(constructor):
    output = generate(constructor + " public String f(String x){return x;} public static String g(String x){return x;}")
    assert "new Example()" not in output
    assert "No verified public/global zero-argument" in output
    assert "Example.g((String)null)" in output


def test_explicit_public_default_constructor():
    assert "new Example().f();" in generate("public Example(){} public void f(){}")


def test_abstract_receiver_never_guessed():
    output = generate("public void f(){} public abstract void g(); public static void h(){}", "public abstract")
    assert "new Example()" not in output
    assert "Example.h();" in output
    assert "Abstract/declaration-only" in output


def test_inner_type_signature_is_visible_and_not_called():
    output = generate("public class Inner {} public static Inner f(Inner x){return x;}")
    assert "Nested type signature" in output
    assert "Example.f(" not in output
    assert "testDraftNeedsReview" in output


def test_no_silent_eight_method_cap():
    output = generate(" ".join(f"public static void m{i}(String x){{}}" for i in range(12)))
    assert output.count("System.assert(false, 'DRAFT:") == 12
    assert "Example.m11((String)null)" in output


def test_never_invents_dml_queries_security_or_exception_success():
    output = generate("public static void f(List<Account> a){}")
    for phrase in ("insert ", "update ", "SELECT", "System.runAs", "catch", "System.debug", "Governor", "Bulk251"):
        assert phrase not in output
    assert "Test.startTest();" in output and "Test.stopTest();" in output
    assert output.count("System.assert(false, ") == 2


@pytest.mark.parametrize("value", ["public class Example { /* unterminated", "public class Example { String x='unterminated; }", "public class Example {} public class Other {}", "public interface Example {}", "public class Different {}"])
def test_unsafe_or_mismatched_source_rejected(value):
    with pytest.raises(ValueError):
        generate_probes_for_class(value, "Example")


def test_properties_and_initializer_calls_are_not_methods():
    analysis = analyze_class(source("public String label { get; set; } public String value = String.valueOf(4); public void real(){}"))
    assert [method.name for method in analysis.methods] == ["real"]


@pytest.mark.parametrize("value", ["@isTest private class Example {}", "@ISTEST(SeeAllData=false) private class Example {}", "public class Example { static testMethod void checks(){} }"])
def test_annotation_and_legacy_test_detection(value):
    assert analyze_class(value).is_test is True
    with pytest.raises(ValueError, match="already an Apex test"):
        generate_probes_for_class(value, "Example")


def invoke(argv):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return main(argv)


def make_source(tmp_path):
    target = tmp_path / "Example.cls"
    target.write_text(source("public static String f(String x){return x;}"))
    return target


@pytest.mark.parametrize("conflict", ["ExampleAdversarialTest.cls", "ExampleAdversarialTest.cls-meta.xml"])
def test_rerun_preserves_each_edited_file_and_no_half_pair(tmp_path, conflict):
    target = make_source(tmp_path)
    out = tmp_path / "output"
    out.mkdir()
    (out / conflict).write_text("retained business edit")
    assert invoke(["--target-class", str(target), "--output", str(out)]) == 2
    assert {p.name for p in out.iterdir()} == {conflict}
    assert (out / conflict).read_text() == "retained business edit"


def test_force_is_explicit_and_no_hidden_files_remain(tmp_path):
    target = make_source(tmp_path)
    out = tmp_path / "output"
    generated = write_test_class(target, out)
    generated.write_text("retained business edit")
    assert invoke(["--target-class", str(target), "--output", str(out), "--force"]) == 0
    assert "EDITABLE DRAFT" in generated.read_text()
    assert len(list(out.iterdir())) == 2


@pytest.mark.parametrize("force", [False, True])
def test_output_symlink_never_overwrites_target(tmp_path, force):
    target = make_source(tmp_path)
    retained = tmp_path / "retained.txt"
    retained.write_text("do not overwrite")
    output = tmp_path / "out"
    output.mkdir()
    (output / "ExampleAdversarialTest.cls").symlink_to(retained)
    with pytest.raises(ValueError, match="regular file"):
        write_test_class(target, output, force=force)
    assert retained.read_text() == "do not overwrite"


def test_second_publish_race_rolls_back_only_first_created_file(tmp_path):
    import os
    target = make_source(tmp_path)
    out = tmp_path / "out"
    original = os.link
    def racing_link(src, dst):
        if str(dst).endswith("-meta.xml"):
            Path(dst).write_text("concurrent user metadata")
        return original(src, dst)
    with patch("jsc_probes.cli.os.link", racing_link), pytest.raises(FileExistsError):
        write_test_class(target, out)
    assert not (out / "ExampleAdversarialTest.cls").exists()
    assert (out / "ExampleAdversarialTest.cls-meta.xml").read_text() == "concurrent user metadata"
    assert len(list(out.iterdir())) == 1


def test_names_do_not_define_test_status(tmp_path):
    target = tmp_path / "TestimonialService.cls"
    target.write_text("public class TestimonialService { public void f(){} }")
    assert invoke(["--target-class", str(target), "--output", str(tmp_path / "out")]) == 0
    actual = tmp_path / "ExampleSpec.cls"
    actual.write_text("@isTest private class ExampleSpec {}")
    assert invoke(["--target-class", str(actual), "--output", str(tmp_path / "tests")]) == 2
    assert not (tmp_path / "tests").exists()


def test_source_api_version_and_explicit_override(tmp_path):
    target = make_source(tmp_path)
    target.with_name(target.name + "-meta.xml").write_text('<ApexClass xmlns="http://soap.sforce.com/2006/04/metadata"><apiVersion>65.0</apiVersion><status>Active</status></ApexClass>')
    out = write_test_class(target, tmp_path / "v65")
    assert "<apiVersion>65.0</apiVersion>" in out.with_name(out.name + "-meta.xml").read_text()
    out = write_test_class(target, tmp_path / "v66", api_version="66.0")
    assert "<apiVersion>66.0</apiVersion>" in out.with_name(out.name + "-meta.xml").read_text()


@pytest.mark.parametrize("version", ["0.0", "NaN", "<tag/>", "61", "-1.0"])
def test_invalid_api_version_no_output(tmp_path, version):
    target = make_source(tmp_path)
    with pytest.raises(ValueError):
        write_test_class(target, tmp_path / "output", api_version=version)
    assert not (tmp_path / "output").exists()


def test_malformed_metadata_no_output(tmp_path):
    target = make_source(tmp_path)
    target.with_name(target.name + "-meta.xml").write_text('<bad>')
    assert invoke(["--target-class", str(target), "--output", str(tmp_path / "output")]) == 2
    assert not (tmp_path / "output").exists()


def test_cli_bad_directory_is_a_normal_error(tmp_path):
    assert invoke(["--target-dir", str(tmp_path / "missing")]) == 2


def test_long_names_stay_readable_distinct_and_consistent(tmp_path):
    from jsc_probes.apex import test_class_name as generated_name
    names = ["ConsultingBusinessProcessingServiceA", "ConsultingBusinessProcessingServiceB"]
    generated = []
    for name in names:
        target = tmp_path / f"{name}.cls"
        target.write_text(f"public class {name} {{ public static String f(String x){{return x;}} }}")
        out = write_test_class(target, tmp_path / "out")
        assert out.stem == generated_name(name)
        assert len(out.stem) <= 40
        assert out.stem.startswith("Consulting") and out.stem.endswith("AdversarialTest")
        assert f"private class {out.stem}" in out.read_text()
        assert f"{name}.f((String)null)" in out.read_text()
        assert out.with_name(out.name + "-meta.xml").is_file()
        generated.append(out.stem)
    assert generated[0] != generated[1]


def test_source_name_limit_is_distinct_from_generated_limit():
    from jsc_probes.apex import test_class_name as generated_name
    assert len(generated_name("A" * 40)) == 40
    with pytest.raises(ValueError, match="Source Apex class name"):
        generated_name("A" * 41)


def test_nested_type_name_as_parameter_name_does_not_hide_method():
    output = generate("public class Inner {} public static String f(String Inner){return Inner;}")
    assert "Example.f((String)null)" in output


def test_deliberate_nested_scope_exclusion_is_informational_only():
    value = source("public class Inner { public void excluded(){} } public static String actual(String x){return x;}")
    analysis = analyze_class(value)
    assert analysis.diagnostics == []
    assert analysis.scope_notes and len(analysis.methods) == 1
    output = generate_probes_for_class(value, "Example")
    assert "testDraftNeedsReview" not in output
    assert output.count("System.assert(false, 'DRAFT:") == 1
