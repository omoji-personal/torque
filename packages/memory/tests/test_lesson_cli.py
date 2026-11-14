"""`torque lesson` as an agent drives it: every command takes --json, never prompts,
and exits with a code that says what happened."""
import io
import json

import pytest

from jsc_memory import cli, storage
from jsc_memory.lesson import Lesson


@pytest.fixture
def run(workspace, monkeypatch, capsys):
    """run("add", "text", client="alpha") -> (exit code, stdout, stderr)."""
    def call(*args, client="alpha", stdin=None):
        folder = workspace if client is None else workspace / (
            "initiatives" if client == "playbook" else "clients") / client
        monkeypatch.setenv("TORQUE_WORKSPACE", str(folder))
        if stdin is not None:
            monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
        try:
            code = cli.main(list(args))
        except SystemExit as exc:          # argparse: a command that was not understood
            code = exc.code
        captured = capsys.readouterr()
        return code, captured.out, captured.err
    return call


def test_add_then_find_show_list_and_search(run, workspace):
    code, out, _ = run("add", "--when", "you are about to deploy a new custom field", "--do",
                       "grant field permissions in a permission set in the same deploy", "--because",
                       "a field created through the Metadata API has no field access", "--source", "consultant",
                       "--command", "sf project deploy *", "--json")
    assert code == 0
    saved = json.loads(out)
    assert (saved["status"], saved["state"], saved["scope"]) == ("saved", "active", "client")
    assert saved["applies"]["types"] == ["CustomField", "PermissionSet"] and "types" in saved["inferred"]
    assert saved["path"].endswith(f"{saved['id']}.md")
    code, out, _ = run("for", "--type", "CustomField", "--json")
    found = json.loads(out)
    assert code == 0 and found["lessons"][0]["id"] == saved["id"]
    assert found["lessons"][0]["match"]["reasons"] == ["type CustomField"] and found["note"].startswith("Notes from")
    assert found["query"] == {"tasks": [], "types": ["CustomField"], "words": [], "command": False,
                              "error": False}
    code, out, _ = run("recall", "field", "access")
    assert code == 0 and f"[{saved['id']}] WHEN you are about to deploy a new custom field" in out
    code, out, _ = run("show", saved["id"], "--json")
    shown = json.loads(out)
    assert code == 0 and shown["id"] == saved["id"] and shown["counters"]["shown"] == 2
    assert shown["evidence"][0]["type"] == "consultant-said"
    code, out, _ = run("show", saved["id"])
    assert "WHEN  you are about to deploy" in out and "counters: shown 2, helpful 0, stale 0" in out
    assert f"file: {saved['path']}" in out
    code, out, _ = run("list", "--json")
    assert [item["id"] for item in json.loads(out)["lessons"]] == [saved["id"]]
    code, out, _ = run("search", "metadata", "API")
    assert code == 0 and saved["id"] in out and out.startswith("Lessons that mention metadata API: ")
    assert run("search", "nothing-like-this")[1].startswith("No lessons to list")
    # What a lesson applies to is searched too: its command pattern, its error matcher.
    assert saved["id"] in run("search", "sf project deploy")[1]


def test_several_words_are_one_text_and_stdin_is_read_only_when_asked(run):
    code, out, _ = run("capture", "always", "retrieve", "first")
    assert code == 0 and "Saved L-" in out and "Always retrieve first" in out
    assert "candidate is not shown at the point of use" in out
    # No text and no parts: an error at once, never a wait on standard input.
    code, _, err = run("add", stdin="this must not be read")
    assert code == 2 and "nothing to save" in err
    code, out, _ = run("add", "-", stdin="When: a file is piped in. Do: read it as the text.")
    assert code == 0 and "Read it as the text" in out
    assert run("add", "   ")[0] == 2
    assert len(storage.load(storage.scopes_from_env())) == 2


def test_a_lesson_needs_somewhere_to_live(run):
    code, _, err = run("add", "a lesson with no client", client=None)
    assert code == 2 and "no client or initiative is selected" in err and "--scope firm" in err
    code, out, _ = run("add", "retrieve before you change anything", "--scope", "firm", client=None, )
    assert code == 0 and "(firm, candidate)" in out
    code, out, _ = run("add", "an initiative lesson", client="playbook")
    assert code == 0 and "(initiative, candidate)" in out
    # The firm lesson is listed for a client; the initiative's is not.
    listed = run("list", client="beta")[1]
    assert "Retrieve before you change anything" in listed and "initiative lesson" not in listed


def test_a_file_in_another_clients_folder_is_not_read_whichever_option_names_it(run, workspace, tmp_path):
    theirs = workspace / "clients" / "beta" / "notes.md"
    theirs.write_text("---\nname: beta-note\ndescription: Beta gift entry detail\n---\nBeta keeps gifts in batches.\n",
                      encoding="utf-8")
    for option in ("--from-file", "--body-file"):
        code, out, err = run("add", "what the note says", option, str(theirs))
        assert code == 2 and "another client" in err and "batches" not in out + err and "gift entry" not in out + err
    code, out, err = run("harvest", "--host", "claude", "--dir", str(theirs.parent))
    assert code == 2 and "another client" in err and "beta-note" not in out + err
    code, out, err = run("for", "--path", str(theirs.parent), "--json")
    assert code == 0 and json.loads(out)["query"]["types"] == []
    assert storage.load(storage.scopes_for(workspace / "clients" / "alpha")) == []
    # Its own client reads it, and a file outside every client's folder is read for any client.
    assert run("add", "what the note says", "--from-file", str(theirs), client="beta")[0] == 0
    mine = tmp_path / "note.md"
    mine.write_text("A note about deploying flows.", encoding="utf-8")
    assert run("add", "what the note says", "--body-file", str(mine))[0] == 0


def test_refusals_say_the_class_never_the_value_and_exit_4(run, workspace):
    secret = "Wint3r" + "!Pass99"
    code, out, err = run("add", f"The integration user's password: {secret}", "--json")
    assert code == 4 and secret not in out + err
    assert json.loads(out)["refused"] and "credential-looking value" in err
    code, out, err = run("add", "At Alpha Nonprofit the gift batch failed", "--scope", "firm", "--json")
    assert code == 4 and "Alpha" not in out + err and "client name" in json.loads(out)["refused"]
    assert not (workspace / "lessons").exists() and not (workspace / "clients" / "alpha" / "lessons").exists()


def test_a_labelled_secret_in_prose_or_lowercase_is_refused_and_never_written(run, workspace):
    for secret, text in (("Mix3d" + "!Synthetic9", 'The password is "{}"'),
                         ("onlylower" + "caseletters", "pass" + "word={}")):
        code, out, err = run("add", text.format(secret), "--json")
        assert code == 4 and secret not in out + err and "password or secret" in err
    assert not any(path.is_file() for path in workspace.rglob("L-*.md"))


def test_every_command_takes_the_short_id_that_add_and_list_print(run):
    code, out, _ = run("add", "When: you deploy a flow. Do: read it back.", "--source", "consultant", "--json")
    saved = json.loads(out)
    short = saved["short_id"]
    assert saved["id"].endswith("-" + short) and len(short) == 8
    text = run("add", "When: you load data. Do: count rows first.", "--source", "consultant")[1]
    assert "  short id: " in text and "(every lesson command takes it)" in text
    assert "An id's last part is enough for every command" in run("list")[1]
    for command in (("show", short), ("helpful", short), ("verify", short), ("stale", short, "--reason", "x"),
                    ("restore", short), ("edit", short, "--set", "kind=works")):
        code, out, err = run(*command, "--json")
        assert code == 0 and json.loads(out)["id"] == saved["id"], (command, err)


def test_harvest_withholds_a_host_note_line_that_holds_a_credential(run, workspace, tmp_path):
    memory = tmp_path / "host-memory"
    memory.mkdir()
    secret = "Mix3d" + "!Synthetic9"
    (memory / "reference_login.md").write_text(f"The sandbox pass{'word'} is \"{secret}\"\n", encoding="utf-8")
    (memory / "feedback_mail.md").write_text("Write to jane.doe@example.org first\n", encoding="utf-8")
    code, out, err = run("harvest", "--host", "claude", "--dir", str(memory), "--json")
    rows = {row["name"]: row["description"] for row in json.loads(out)["files"]}
    assert code == 0 and secret not in out + err
    assert rows == {"reference_login": "(withheld: it holds a credential-looking value; open the file to read it)",
                    "feedback_mail": "Write to <email> first"}


def test_for_prints_one_line_and_exits_zero_when_nothing_matches(run):
    code, out, err = run("for", "quantum", "chromodynamics")
    assert (code, err) == (0, "") and out.strip() == (
        "No lesson matches quantum, chromodynamics (client alpha + firm + platform).")
    code, out, _ = run("for", "--command", "sf project deploy start --metadata Flow:Gift_Entry", "--limit", "2",
                       client=None)
    assert code == 0 and out.count("\n[") == 2 and "(firm + platform): 2 of " in out


def test_show_keeps_the_old_listing_names(run):
    run("add", "a candidate lesson")
    run("add", "an active lesson", "--source", "consultant")
    assert "A candidate lesson" in run("show", "pending")[1] and "An active lesson" not in run("show", "pending")[1]
    assert "An active lesson" in run("show", "active")[1]
    assert run("show", "archive")[1].startswith("No lessons to list")
    code, out, _ = run("show")
    assert code == 0 and "A candidate lesson" in out and "An active lesson" in out


def test_an_unknown_or_ambiguous_id_changes_nothing_and_says_so(run, scopes):
    folder = scopes("alpha").folder("client")
    for suffix in ("ab01", "ab02"):
        storage.save(Lesson(id=f"L-20200101-{suffix}", title=f"Lesson {suffix}", created="2020-01-01"), folder)
    code, out, err = run("show", "L-20991231-ffff", "--json")
    assert code == 1 and "no lesson" in err and json.loads(out)["exit_code"] == 1
    code, out, err = run("show", "L-20200101-ab", "--json")
    assert code == 3 and "matches 2 lessons" in err and len(json.loads(out)["candidates"]) == 2
    assert run("show", "L-")[0] == 3
    assert "Lesson ab02" in run("show", "L-20200101-ab02")[1] and "Lesson ab01" in run("show", "20200101-ab01")[1]


def test_unreadable_files_are_named_and_never_break_a_command(run, workspace):
    run("add", "a good lesson", "--source", "consultant")
    folder = workspace / "clients" / "alpha" / "lessons"
    (folder / "handedited.md").write_text("---\nid: [broken\n---\n", encoding="utf-8")
    for args in (("list",), ("for", "good"), ("search", "good"), ("show",)):
        code, out, _ = run(*args)
        assert code == 0 and "good lesson" in out
        assert "clients/alpha/lessons/handedited.md" in out and "not valid YAML" in out
    assert json.loads(run("list", "--json")[1])["unreadable"][0]["path"] == "clients/alpha/lessons/handedited.md"
    assert run("add", "another lesson")[0] == 0


def test_a_command_that_is_not_understood_exits_2(run):
    assert run("frobnicate")[0] == 2
    assert run("for", "--limit", "many")[0] == 2
    assert run("add", "x", "--kind", "rant")[0] == 2
    assert run("for", "--typ", "Flow")[0] == 2            # option names are never abbreviated


def test_for_error_finds_what_fixed_an_error_before(run):
    code, out, _ = run("add", "--when", "a query fails on an unknown field name", "--do",
                       "look the name up with sf sobject describe and do not guess again", "--because",
                       "a second guess usually fails too", "--error", "No such column", "--source", "consultant",
                       "--json")
    saved = json.loads(out)
    assert code == 0 and saved["applies"]["errors"] == ["No such column"]
    error = "No such column 'Region_Code__c' on entity 'Account'"
    code, out, _ = run("for", "--error", error, "--json")
    found = json.loads(out)
    assert code == 0 and found["lessons"][0]["id"] == saved["id"]
    assert found["lessons"][0]["match"]["reasons"] == ["error"] and found["query"]["error"] is True
    code, out, _ = run("for", "--error", error)
    assert f"[{saved['id']}] WHEN a query fails on an unknown field name" in out and "Region_Code__c" not in out
    code, out, err = run("for", "--error", "An error nobody has met: 0x51")
    assert (code, err) == (0, "") and out.startswith("No lesson matches this error (client alpha + firm + platform).")
    assert "--learned" in out and out.count("\n") == 1
    code, out, _ = run("for", "--error", "UNABLE_TO_LOCK_ROW on 12 rows", "--limit", "1", client=None)
    assert code == 0 and "[A-unable-to-lock-row]" in out


def test_a_lock_held_by_another_command_exits_7_and_says_to_run_again(run, monkeypatch, scopes):
    from contextlib import contextmanager

    @contextmanager
    def held(root):
        raise storage.Busy("another Torque command is writing lessons here. Nothing was written; run the command again.")
        yield

    monkeypatch.setattr(storage, "transaction", held)
    code, out, err = run("add", "a lesson while the folder is locked", "--json")
    assert code == 7 and "run the command again" in err and json.loads(out)["exit_code"] == 7
    assert storage.load(scopes("alpha")) == []


def test_the_package_holds_only_what_has_a_caller():
    """The hook-driven half of the earlier package (session spool, trigger synthesis,
    ranker, index, injector, the framework-memory shim) had no caller and is gone; what
    the package says about itself names modules and a guide that exist."""
    import importlib
    import importlib.util
    import re
    from pathlib import Path
    import jsc_memory
    for gone in ("spool", "capture", "injector", "index", "ranker"):
        assert importlib.util.find_spec(f"jsc_memory.{gone}") is None, gone
    for name in ("load_framework_memory", "active_lessons_path", "index_path", "spool_dir", "write_review_candidate"):
        assert not hasattr(storage, name), name
    named = re.findall(r"^- `(\w+)`", jsc_memory.__doc__, re.M)
    assert set(named) == {"lesson", "storage", "scrubber", "vocab", "intake", "recall", "feedback", "review",
                          "promote", "cli"}
    for name in named:
        importlib.import_module(f"jsc_memory.{name}")
    package = Path(jsc_memory.__file__).resolve().parent
    assert sorted(path.stem for path in package.glob("*.py")) == sorted([*named, "__init__"])
    guide = package.parents[2] / "docs" / "lessons.md"
    assert "docs/lessons.md" in jsc_memory.__doc__ and (not (package.parents[2] / "pyproject.toml").is_file()
                                                        or guide.is_file())
    # Nothing printed needs more than ASCII, so a console that is not UTF-8 cannot break a command.
    for module in named:
        source = (package / f"{module}.py").read_text(encoding="utf-8")
        for call in re.findall(r"print\((.*)", source):
            assert call.isascii(), (module, call)
