"""Promote, harvest, stats, the self-check, the doctor report and the one-time import."""
import json
import os
import time

import pytest

from jsc_memory import cli, feedback, intake, lesson as record, promote, recall, review, storage
from jsc_memory.lesson import LessonError

ORG_ID = "00D" + "5g000004XyZa" + "EAA"


@pytest.fixture
def run(workspace, monkeypatch, capsys):
    def call(*args, client="alpha"):
        folder = workspace if client is None else workspace / "clients" / client
        monkeypatch.setenv("TORQUE_WORKSPACE", str(folder))
        try:
            code = cli.main(list(args))
        except SystemExit as exc:
            code = exc.code
        captured = capsys.readouterr()
        return code, captured.out, captured.err
    return call


def lesson_files(workspace):
    return {str(path.relative_to(workspace)): path.read_bytes() for path in workspace.rglob("*")
            if path.is_file() and (path.name == "events.jsonl" or path.suffix == ".md" and ".torque" not in path.parts)}


def client_lesson(scopes):
    return intake.add(
        scopes, when=f"you deploy a flow for Alpha Nonprofit to alpha-dev ({ORG_ID})",
        do="read the active version back after the deploy", because="a deploy can leave the flow inactive",
        body="Seen at Alpha Nonprofit on the gift entry flow.", source="consultant",
        evidence=["reproduced:session 20261112T101500000000Z-abc123def456"]).lesson


def test_promote_to_firm_writes_a_proposal_and_changes_nothing(run, workspace, scopes):
    alpha = scopes("alpha")
    item = client_lesson(alpha)
    before = lesson_files(workspace)
    code, out, _ = run("promote", item.id, "--to", "firm", "--json")
    made = json.loads(out)
    assert code == 0 and lesson_files(workspace) == before and not (workspace / "lessons").exists()
    path = workspace / "clients" / "alpha" / ".torque" / "lessons" / "proposals" / f"promote-{item.id}-to-firm.md"
    assert made["proposal"] == str(path) and made["applies_itself"] is True
    assert made["replaced"]["client name"] == 2 and made["replaced"]["org alias"] == 1
    proposal = path.read_text(encoding="utf-8")
    for word in ("Alpha", "alpha-dev", ORG_ID[:10]):
        assert word not in proposal and word not in made["text"]
    assert "you deploy a flow for the client to the org (<org-id>)" in proposal and "scope: firm" in proposal
    assert "## To confirm" in proposal and "torque lesson promote --apply" in proposal
    assert f"lesson: {item.id}" in proposal and "to: firm" in proposal
    text = run("promote", item.id, "--to", "firm")[1]
    assert "Proposal written, nothing applied" in text and "On their word: torque lesson promote --apply" in text
    assert lesson_files(workspace) == before


def test_apply_writes_the_firm_copy_without_client_words_and_marks_the_source(run, workspace, scopes):
    alpha = scopes("alpha")
    item = client_lesson(alpha)
    made = promote.propose(alpha, item.id, "firm")
    code, out, _ = run("promote", "--apply", made["proposal"], "--json")
    done = json.loads(out)
    assert code == 0 and done["status"] == "applied" and done["source"] == item.id
    firm = storage.load(scopes(None))
    assert [copy.id for copy in firm] == [done["firm"]] and firm[0].id != item.id
    copy = firm[0]
    assert (copy.scope, copy.state, copy.source) == ("firm", "active", "consultant")
    text = copy.path.read_text(encoding="utf-8")
    for word in ("Alpha", "alpha", ORG_ID[:10]):
        assert word not in text
    assert copy.when == "you deploy a flow for the client to the org (<org-id>)"
    assert copy.body == "Seen at the client on the gift entry flow." and copy.evidence[0]["type"] == "reproduced"
    source = storage.load(alpha, only="client")[0]
    assert source.promoted_to == [f"firm:{copy.id}"] and "Alpha Nonprofit" in source.when     # the source keeps its words
    events = storage.read_events(alpha.engagement)
    assert (events[-1]["event"], events[-1]["id"], events[-1]["to"]) == ("promoted", item.id, f"firm:{copy.id}")
    # For this client the firm copy now answers; for another client it answers too; the source is shown no more.
    found = [hit.lesson.id for hit in recall.find(alpha, types=["Flow"], limit=None).hits if hit.lesson.scope != "platform"]
    assert found == [copy.id]
    assert copy.id in [hit.lesson.id for hit in recall.find(scopes("beta"), types=["Flow"], limit=None).hits]
    # The pair is one lesson in two forms: the listing says so and the review does not call it a duplicate.
    assert review.build(alpha)["duplicates"] == [] and " promoted " in run("list")[1]
    # Applying again is harmless, by path or by the proposal's name.
    for proposal in (made["proposal"], f"promote-{item.id}-to-firm.md"):
        code, out, _ = run("promote", "--apply", proposal)
        assert code == 0 and "Already applied" in out
    assert len(storage.load(scopes(None))) == 1
    # An apply that stopped between its two writes is finished by running it again: no second firm lesson.
    source = storage.load(alpha, only="client")[0]
    source.promoted_to = []
    storage.save(source, alpha.folder("client"))
    code, out, _ = run("promote", "--apply", made["proposal"], "--json")
    assert code == 0 and json.loads(out)["status"] == "applied"
    assert [one.id for one in storage.load(scopes(None))] == [copy.id]
    assert storage.load(alpha, only="client")[0].promoted_to == [f"firm:{copy.id}"]


def test_apply_refuses_text_that_still_identifies_a_client_and_needs_the_sources_client(run, workspace, scopes):
    alpha = scopes("alpha")
    item = client_lesson(alpha)
    made = promote.propose(alpha, item.id, "firm")
    path = made["proposal"]
    edited = open(path, encoding="utf-8").read().replace("for the client to the org", "for Alpha Nonprofit to the org")
    with open(path, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(edited)
    code, _, err = run("promote", "--apply", path)
    assert code == 4 and "identifies a client" in err and "Alpha" not in err
    assert storage.load(scopes(None)) == [] and storage.load(alpha, only="client")[0].promoted_to == []
    # A proposal is applied from the scope of its source lesson, never from another client's.
    with open(path, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(edited.replace("for Alpha Nonprofit to the org", "for the client to the org"))
    # Selected for another client, the proposal is not even read: it lies in this client's folder.
    code, out, err = run("promote", "--apply", path, client="beta")
    assert code == 2 and "another client" in err and "flow" not in (out + err).lower()
    copied = workspace / "promote-copy.md"
    copied.write_text(open(path, encoding="utf-8").read(), encoding="utf-8")
    assert run("promote", "--apply", str(copied), client="beta")[0] == 1      # outside it: beta has no such lesson
    assert run("promote", "--apply", path, client=None)[0] == 2
    assert run("promote", "--apply", str(workspace / "nothing.md"))[0] == 2
    # A lesson retired since the proposal was written is not promoted.
    assert run("retire", item.id, "--reason", "no longer how the client works")[0] == 0
    code, _, err = run("promote", "--apply", path)
    assert code == 2 and "is retired now" in err and storage.load(scopes(None)) == []
    assert run("restore", item.id)[0] == 0
    assert run("promote", "--apply", path)[0] == 0


FAKE_SECRET = "Fake" + "-Only-" + "1234"                 # made up; assembled so no tracked file holds the pair
EXTRA_KEYS = ("client_context: Alpha Nonprofit\n"
              f"secret_context: pass{'word'}={FAKE_SECRET}\n"
              "notes: {owner: alpha-dev, seen: [Alpha Nonprofit, 2026-11-14]}\n")


def _with_extra_keys(item, extra=EXTRA_KEYS):
    """The lesson's file with front-matter keys Torque does not know, added by a person."""
    text = item.path.read_text(encoding="utf-8").replace("\nsource:", "\n" + extra.rstrip("\n") + "\nsource:", 1)
    item.path.write_text(text, encoding="utf-8")


def _fenced(proposal_path):
    return promote._BLOCK.search(open(proposal_path, encoding="utf-8").read()).group(1)


def test_a_wider_scope_copy_leaves_out_the_keys_torque_does_not_know_and_names_them(run, workspace, scopes):
    alpha = scopes("alpha")
    item = client_lesson(alpha)
    _with_extra_keys(item)
    assert set(storage.load(alpha, only="client")[0].extra) == {"client_context", "secret_context", "notes"}
    text = run("promote", item.id, "--to", "firm")[1]
    assert "Left out: 3 front-matter key(s)" in text and FAKE_SECRET not in text and "Alpha" not in text
    code, out, err = run("promote", item.id, "--to", "firm", "--json")
    made = json.loads(out)
    assert code == 0 and made["left_out"] == ["client_context", "secret_context", "notes"]
    proposal = open(made["proposal"], encoding="utf-8").read()
    for word in ("Alpha", "alpha-dev", FAKE_SECRET):
        assert word not in proposal and word not in out + err
    assert "Left out: 3 front-matter key(s) Torque does not know (client_context, secret_context, notes)" in proposal
    assert "client_context" not in made["text"] and _fenced(made["proposal"]) == made["text"]
    # Apply writes the text the proposal shows, and nothing else.
    code, out, err = run("promote", "--apply", made["proposal"], "--json")
    assert code == 0, err
    firm = storage.load(scopes(None))[0]
    written = firm.path.read_text(encoding="utf-8")
    assert written == made["text"] + "\n" and firm.extra == {}
    for word in ("Alpha", "alpha-dev", FAKE_SECRET, "client_context", "secret_context"):
        assert word not in written
    # The other destinations leave the keys out the same way.
    for target in ("platform", "rule", "check"):
        made = json.loads(run("promote", item.id, "--to", target, "--json")[1]) if target != "platform" else \
            promote.propose(alpha, item.id, "platform")
        assert made["left_out"] == ["client_context", "secret_context", "notes"]
        assert "client_context" not in made["text"] and "Alpha" not in made["text"]


def test_a_proposal_lists_custom_names_for_the_person_to_confirm_and_refuses_none(run, scopes):
    alpha = scopes("alpha")
    item = intake.add(alpha, when="you change Gift_Batch__c or npsp__Batch__c in a flow",
                      do="retrieve Batch_Rule__mdt first", because="the rule decides the batch",
                      source="consultant").lesson
    code, out, _ = run("promote", item.id, "--to", "firm", "--json")
    made = json.loads(out)
    assert code == 0 and made["custom_names"] == ["Batch_Rule__mdt", "Gift_Batch__c", "npsp__Batch__c"]
    proposal = open(made["proposal"], encoding="utf-8").read()
    assert "Custom names in this text: Batch_Rule__mdt, Gift_Batch__c, npsp__Batch__c." in proposal
    assert "Custom names in the text: " in run("promote", item.id, "--to", "firm")[1]
    assert run("promote", "--apply", made["proposal"])[0] == 0                 # listed, not refused


@pytest.mark.parametrize("added,refused", [
    ("client_context: Alpha Nonprofit", "client name"),
    (f"secret_context: pass{'word'}={FAKE_SECRET}", "password or secret"),
    ("notes: {owner: alpha-dev}", "org alias"),
    ("alpha_nonprofit_note: seen there", "client slug"),
    (None, None),
])
def test_apply_scrubs_a_key_a_person_put_back_into_the_proposal_like_the_rest(run, workspace, scopes, added, refused):
    alpha = scopes("alpha")
    item = client_lesson(alpha)
    _with_extra_keys(item)
    made = promote.propose(alpha, item.id, "firm")
    path = made["proposal"]
    kept = "reviewed_by: the consultant"           # a key a person adds on purpose, naming no client
    line = added or kept
    body = open(path, encoding="utf-8").read().replace("\nsource:", f"\n{line}\nsource:", 1)
    open(path, "w", encoding="utf-8", newline="\n").write(body)
    code, out, err = run("promote", "--apply", path)
    if refused:
        assert code == 4 and refused in err and storage.load(scopes(None)) == []
        assert "Alpha" not in out + err and FAKE_SECRET not in out + err
    else:
        assert code == 0
        firm = storage.load(scopes(None))[0]
        assert firm.extra == {"reviewed_by": "the consultant"}
        shown = record.parse(_fenced(path))
        assert (firm.front_matter(), firm.body) == (shown.front_matter(), shown.body)


def test_keys_torque_does_not_know_are_scrubbed_when_a_lesson_is_saved_in_its_own_scope(workspace):
    from jsc_memory import scrubber
    from jsc_memory.lesson import Lesson
    terms = scrubber.workspace_terms(workspace)
    item = Lesson(id="L-20261114-0001", title="t", extra={"contact": "jane@example.org",
                                                         "nested": {"alpha-dev": ["Beta Land Trust", 5551234567]}})
    found = scrubber.scrub_lesson(item)
    assert item.extra["contact"] == "<email>" and found.refused == {}
    found = scrubber.scrub_lesson(item, firm_terms=terms)
    assert {"org alias", "client name"} <= set(found.refused)
    deep = value = {}
    for _ in range(12):
        value["next"] = {}
        value = value["next"]
    with pytest.raises(LessonError, match="nested too deeply"):
        scrubber.scrub_lesson(Lesson(id="L-20261114-0002", title="t", extra={"deep": deep}))


@pytest.mark.parametrize("target,expected", [
    ("rule", "- When you deploy a flow for the client to the org (<org-id>): read the active version back after "
             "the deploy. (a deploy can leave the flow inactive.)"),
    ("recipe", "Reason: a deploy can leave the flow inactive."),
    ("skill", "Check: <how to see it holds>."),
    ("check", "Detect: you deploy a flow for the client to the org (<org-id>)."),
    ("platform", "scope: platform"),
])
def test_the_other_destinations_get_a_proposal_a_person_applies(run, workspace, scopes, target, expected):
    alpha = scopes("alpha")
    item = client_lesson(alpha)
    before = lesson_files(workspace)
    code, out, _ = run("promote", item.id, "--to", target, "--name", "flows", "--json")
    made = json.loads(out)
    assert code == 0 and expected in made["text"] and made["applies_itself"] is False
    assert "Alpha" not in made["text"] and lesson_files(workspace) == before
    proposal = open(made["proposal"], encoding="utf-8").read()
    assert f"torque lesson edit {item.id} --set promoted_to={target}:flows" in proposal and "## To confirm" in proposal
    code, _, err = run("promote", "--apply", made["proposal"])
    assert code == 2 and "applied by a person" in err and lesson_files(workspace) == before
    # Recording it by hand takes the lesson out of recall: it lives on in its promoted form.
    assert run("edit", item.id, "--set", f"promoted_to={target}:flows")[0] == 0
    assert [hit.lesson.id for hit in recall.find(alpha, types=["Flow"], limit=None).hits
            if hit.lesson.scope != "platform"] == []


def test_what_cannot_be_promoted_says_why(run, scopes):
    alpha = scopes("alpha")
    waiting = intake.add(alpha, text="an unreviewed remark").lesson
    firm = intake.add(alpha, text="retrieve before you change anything", scope="firm", source="consultant").lesson
    assert "is candidate" in run("promote", waiting.id, "--to", "firm")[2]
    assert "already a firm lesson" in run("promote", firm.id, "--to", "firm")[2]
    assert "already a platform lesson" in run("promote", "A-deploy-order", "--to", "platform")[2]
    assert run("promote", firm.id, "--to", "platform")[0] == 0
    assert run("promote", firm.id)[0] == 2 and run("promote")[0] == 2
    assert run("promote", firm.id, "--to", "firm", "--apply", "x.md")[0] == 2
    assert run("promote", firm.id, "--to", "wiki")[0] == 2


def test_harvest_lists_what_changed_in_a_hosts_memory_and_copies_nothing(run, workspace, tmp_path):
    memory = tmp_path / "host-memory"
    memory.mkdir()
    (memory / "MEMORY.md").write_text("- [feedback_flows](feedback_flows.md) the index\n", encoding="utf-8")
    (memory / "feedback_flows.md").write_text(
        "---\nname: flows-by-metadata\ndescription: Build flows through metadata, not the UI\nmetadata:\n"
        "  type: feedback\n---\nThe rule.\n\n**Why:** it is reviewable.\n", encoding="utf-8")
    (memory / "reference_cli.md").write_text("# CLI notes after a refresh\n\nPass the API version.\n",
                                             encoding="utf-8")
    old = memory / "project_old.md"
    old.write_text("Old state.\n", encoding="utf-8")
    os.utime(old, (time.time() - 40 * 86400,) * 2)
    before = {path.name: path.read_bytes() for path in memory.iterdir()}

    code, out, _ = run("harvest", "--host", "claude", "--dir", str(memory), "--json", client=None)
    report = json.loads(out)
    assert code == 0 and report["found"] and report["copied"] == 0 and report["since"] is None
    rows = {row["name"]: row for row in report["files"]}
    assert set(rows) == {"flows-by-metadata", "reference_cli", "project_old"}             # the index is not a note
    assert rows["flows-by-metadata"]["type"] == "feedback"
    assert rows["flows-by-metadata"]["description"] == "Build flows through metadata, not the UI"
    assert (rows["reference_cli"]["type"], rows["reference_cli"]["description"]) == ("reference",
                                                                                   "CLI notes after a refresh")
    assert rows["reference_cli"]["path"] == str(memory / "reference_cli.md")
    assert {path.name: path.read_bytes() for path in memory.iterdir()} == before
    assert not (workspace / "lessons").exists() and not list(workspace.rglob("L-*.md"))
    state = json.loads((workspace / ".torque" / "lessons" / "harvest.json").read_text(encoding="utf-8"))
    assert state["claude"]["files"] == 3 and state["claude"]["dir"] == str(memory)

    # The next harvest lists only what changed since; --since looks further back.
    code, out, _ = run("harvest", "--host", "claude", "--dir", str(memory), client=None)
    assert code == 0 and out.startswith("0 file(s) changed in ")
    os.utime(memory / "reference_cli.md", (time.time() + 5,) * 2)
    again = json.loads(run("harvest", "--host", "claude", "--dir", str(memory), "--json", client=None)[1])
    assert [row["name"] for row in again["files"]] == ["reference_cli"]
    since = json.loads(run("harvest", "--host", "claude", "--dir", str(memory), "--since", "2020-01-01", "--json",
                           client=None)[1])
    assert len(since["files"]) == 3
    text = run("harvest", "--host", "claude", "--dir", str(memory), "--since", "2020-01-01", client=None)[1]
    assert "Nothing was copied." in text and "torque lesson add --from-file PATH" in text

    # A note enters a scope only when someone chooses it.
    code, out, _ = run("add", "--from-file", str(memory / "feedback_flows.md"), "--scope", "firm", "--json",
                       client=None)
    saved = json.loads(out)
    assert code == 0 and (saved["scope"], saved["state"]) == ("firm", "candidate")
    stored = storage.load(storage.scopes_for(workspace))[0]
    assert stored.title == "Build flows through metadata, not the UI" and stored.source == "harvested"
    assert stored.body.startswith("The rule.")


def test_harvest_never_fails_when_the_folder_is_missing_or_the_host_is_not_documented(run, workspace, tmp_path,
                                                                                    monkeypatch):
    code, out, _ = run("harvest", "--host", "claude", "--dir", str(tmp_path / "nowhere"), client=None)
    assert code == 0 and out.startswith("No memory folder at ") and "--dir PATH" in out
    assert not (workspace / ".torque" / "lessons" / "harvest.json").exists()
    code, out, _ = run("harvest", "--host", "antigravity", client=None)
    assert code == 0 and ".agents/rules/" in out and "--dir PATH" in out
    assert run("harvest", "--host", "notepad", client=None)[0] == 2
    assert run("harvest", "--host", "claude", "--dir", str(tmp_path), "--since", "yesterday", client=None)[0] == 2
    # The documented Claude Code layout: ~/.claude/projects/<workspace path with separators as dashes>/memory.
    home = tmp_path / "config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    folder, _ = intake.host_memory_dir("claude", workspace)
    assert folder.parent.parent == home / "projects" and folder.name == "memory"
    assert not any(character in folder.parent.name for character in "\\/:")
    folder.mkdir(parents=True)
    (folder / "feedback_one.md").write_text("One note.\n", encoding="utf-8")
    report = json.loads(run("harvest", "--host", "claude", "--json", client=None)[1])
    assert report["dir"] == str(folder) and [row["name"] for row in report["files"]] == ["feedback_one"]
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    assert intake.host_memory_dir("codex", workspace)[0] == tmp_path / "codex" / "memories_v2"


def test_harvest_says_when_codex_keeps_its_memory_in_a_database_and_does_not_open_it(run, tmp_path, monkeypatch):
    codex = tmp_path / "codex"
    codex.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex))
    assert run("harvest", "--host", "codex", client=None)[1].startswith("No memory folder at ")
    database = codex / "memories_1.sqlite"
    database.write_bytes(b"not a database the test lets anyone open")
    opened = []
    real_open = open
    monkeypatch.setattr("builtins.open", lambda path, *a, **k: (opened.append(str(path)), real_open(path, *a, **k))[1])
    code, out, err = run("harvest", "--host", "codex", "--json", client=None)
    report = json.loads(out)
    assert code == 0 and report["found"] is False and report["files"] == []
    assert report["note"].startswith("Codex keeps its memory here in a database file (memories_1.sqlite in ")
    assert "does not read" in report["note"] and "--dir PATH" in report["note"] and len(report["note"].splitlines()) == 1
    assert str(database) not in opened


FOREIGN = "Beta-only synthetic note 7f3k"          # text that lives only in another engagement's folder


def _plant_foreign_notes(workspace, tmp_path):
    """Notes in every kind of place a harvest must not open while alpha is selected: another
    client and an initiative of this workspace, and a client of a second workspace beside it
    (one registered, one whose registration is missing)."""
    for folder in (workspace / "clients" / "beta", workspace / "clients" / "beta" / "lessons",
                   workspace / "initiatives" / "playbook"):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "private-memory.md").write_text(f"# {FOREIGN}\n\nbody\n", encoding="utf-8")
    other = tmp_path / "ws-two"
    (other / "clients" / "gamma").mkdir(parents=True)
    (other / "clients" / "unregistered").mkdir(parents=True)
    (other / "workspace.json").write_text(json.dumps({"name": "Second practice"}), encoding="utf-8")
    (other / "clients" / "gamma" / "client.json").write_text(json.dumps({"name": "Gamma", "slug": "gamma"}),
                                                            encoding="utf-8")
    for folder in (other / "clients" / "gamma", other / "clients" / "unregistered"):
        (folder / "notes.md").write_text(f"{FOREIGN}\n", encoding="utf-8")
    (workspace / "clients" / "alpha" / "own-note.md").write_text("# Alpha's own synthetic note\n", encoding="utf-8")
    (tmp_path / "loose-note.md").write_text("# A note outside every engagement\n", encoding="utf-8")
    return other


def _link_folder(link, target):
    """A linked folder the way this system allows one: a symbolic link, or on Windows a
    junction (which needs no privilege). None when neither can be made."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return "symbolic link"
    except (OSError, NotImplementedError, AttributeError):
        pass
    if os.name == "nt":
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
            return "junction"
        except (ImportError, OSError, AttributeError):
            pass
    return None


@pytest.mark.parametrize("start", ["workspace root", "common parent"])
def test_a_harvest_that_starts_above_the_clients_never_opens_another_engagements_files(run, workspace, tmp_path,
                                                                                         start):
    _plant_foreign_notes(workspace, tmp_path)
    folder = workspace if start == "workspace root" else tmp_path
    code, out, err = run("harvest", "--host", "codex", "--dir", str(folder), "--json")
    report = json.loads(out)
    assert code == 0 and report["found"]
    assert FOREIGN not in out + err
    names = {row["description"] for row in report["files"]}
    assert "Alpha's own synthetic note" in names                         # the selected client's own note is listed
    assert ("A note outside every engagement" in names) == (start == "common parent")
    assert not any("beta" in row["path"] or "playbook" in row["path"] or "ws-two" + os.sep + "clients" in row["path"]
                   for row in report["files"])
    expected = 2 if start == "workspace root" else 4                    # beta, playbook (+ gamma, unregistered)
    assert report["skipped"]["other engagements"] == expected
    text = run("harvest", "--host", "codex", "--dir", str(folder), "--since", "2020-01-01")[1]
    assert FOREIGN not in text and f"Not read: {expected} folder(s) or file(s) of another client" in text


def test_a_harvest_never_follows_a_link_into_another_engagement(run, workspace, tmp_path):
    _plant_foreign_notes(workspace, tmp_path)
    memory = tmp_path / "host-memory"
    memory.mkdir()
    (memory / "feedback_one.md").write_text("# One ordinary note\n", encoding="utf-8")
    kind = _link_folder(memory / "linked", workspace / "clients" / "beta")
    if kind is None:
        pytest.skip("this system cannot make a linked folder (no symbolic link privilege, no junction)")
    # Whatever the walk does, the read rule alone already refuses the file behind the link.
    alpha = storage.scopes_for(workspace / "clients" / "alpha")
    assert alpha.foreign(memory / "linked" / "private-memory.md"), kind
    code, out, err = run("harvest", "--host", "claude", "--dir", str(memory), "--json")
    report = json.loads(out)
    assert code == 0 and [row["description"] for row in report["files"]] == ["One ordinary note"]
    assert FOREIGN not in out + err and report["skipped"]["links"] == 1, kind
    # A linked file is not opened either, where the system can make one.
    try:
        os.symlink(workspace / "clients" / "beta" / "private-memory.md", memory / "linked-file.md")
    except (OSError, NotImplementedError):
        return
    code, out, err = run("harvest", "--host", "claude", "--dir", str(memory), "--since", "2020-01-01", "--json")
    assert code == 0 and FOREIGN not in out + err and json.loads(out)["skipped"]["links"] == 2


def test_naming_another_engagements_folder_directly_is_refused(run, workspace, tmp_path):
    other = _plant_foreign_notes(workspace, tmp_path)
    for folder in (workspace / "clients" / "beta", workspace / "initiatives" / "playbook",
                   other / "clients" / "gamma", other / "clients" / "unregistered"):
        code, out, err = run("harvest", "--host", "codex", "--dir", str(folder))
        assert code == 2 and "another client or initiative" in err and FOREIGN not in out + err, folder
    assert not (workspace / ".torque" / "lessons" / "harvest.json").exists()


def test_stats_count_what_was_captured_shown_and_judged(run, scopes):
    alpha = scopes("alpha")
    said = intake.add(alpha, text="When: you deploy a flow. Do: read it back.", source="consultant").lesson
    intake.add(alpha, text="an agent remark about fields").lesson
    intake.add(alpha, text="retrieve before you change anything", scope="firm", source="consultant")
    recall.for_command("sf project deploy start --metadata Flow:X", scopes=alpha, via="deploy")
    recall.context_section(alpha, via="context")
    feedback.helpful(alpha, said.id)
    report = review.stats(alpha)
    assert report["captured"] == {"total": 3, "by_source": {"agent": 1, "consultant": 2},
                                  "by_scope": {"client": 2, "firm": 1}}
    assert report["states"] == {"active": 2, "candidate": 1}
    assert report["events"]["helpful"] == 1 and report["events"]["shown"] >= 2
    assert report["shown_by_surface"]["context"] == 1 and report["shown_by_surface"]["deploy"] >= 1
    assert report["candidates_waiting"] == 1 and report["oldest_candidate"]["days"] == 0
    assert len(report["never_shown"]) == 1                       # the firm lesson was never shown
    code, out, _ = run("stats")
    assert code == 0 and "Captured: 3 (by source: agent 1, consultant 2; by scope: client 2, firm 1)" in out
    assert "Candidates waiting: 1; the oldest is" in out and "Active and never shown: 1" in out
    later = json.loads(run("stats", "--since", "2099-01-01", "--json")[1])
    assert later["captured"]["total"] == 0 and later["events"]["shown"] == 0 and later["candidates_waiting"] == 1
    assert run("stats", "--since", "last week")[0] == 2
    assert run("stats", client=None)[0] == 0


def test_the_selfcheck_proves_the_wiring_and_touches_no_workspace(run, workspace, monkeypatch):
    before = sorted(str(path) for path in workspace.rglob("*"))
    code, out, _ = run("selfcheck", "--json")
    report = json.loads(out)
    assert code == 0 and report["passed"] is True
    assert [step["step"] for step in report["steps"]] == [
        "write a lesson", "recall by metadata type", "recall by command", "the context block",
        "the function other packages call", "showing it was recorded", "platform lessons are readable"]
    assert sorted(str(path) for path in workspace.rglob("*")) == before
    assert "Lessons self-check: passed" in run("selfcheck")[1]
    # A broken link in the loop is reported as a failure, with the step, and exit 6.
    monkeypatch.setattr(recall, "context_section", lambda scopes, **more: {"shown": []})
    code, out, _ = run("selfcheck")
    assert code == 6 and "FAILED" in out and "FAIL the context block" in out
    monkeypatch.setattr(storage, "save", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    report = review.selfcheck()
    assert report["passed"] is False and report["steps"][-1]["step"] == "run to the end"


def test_the_doctor_report_counts_states_names_bad_files_and_says_live_or_idle(scopes, workspace):
    alpha = scopes("alpha")
    report = review.health(alpha)
    assert report["loop"] == "idle" and report["firm"]["last_capture"] is None
    assert report["engagement"]["states"] == {"candidate": 0, "active": 0, "retired": 0}
    assert "Lessons loop: idle" in review.health_lines(report)[-1]
    intake.add(alpha, text="When: you deploy a flow. Do: read it back.", source="consultant")
    intake.add(alpha, text="an agent remark")
    intake.add(alpha, text="retrieve before you change anything", scope="firm", source="consultant")
    (alpha.folder("client") / "broken.md").write_text("not a lesson", encoding="utf-8")
    recall.context_section(alpha, via="context")
    report = review.health(alpha)
    assert report["loop"] == "live"
    assert report["engagement"]["states"] == {"candidate": 1, "active": 1, "retired": 0}
    assert report["engagement"]["last_capture"] == record.today() == report["engagement"]["last_shown"]
    assert [problem["path"] for problem in report["engagement"]["unreadable"]] == ["clients/alpha/lessons/broken.md"]
    assert report["firm"]["states"]["active"] == 1 and report["firm"]["last_shown"] is None
    assert report["platform"]["advisory"] >= 40
    lines = review.health_lines(report)
    assert lines[0].startswith("Lessons (firm): 0 candidate, 1 active, 0 retired; unreadable files 0; last saved ")
    assert lines[1].startswith("Lessons (client alpha): 1 candidate, 1 active, 0 retired; unreadable files 1")
    assert lines[-1] == "Lessons loop: live (a lesson was saved or shown in the last 30 days)"
    # Old activity only: idle again.
    stale = review.health(scopes("beta"))
    assert stale["engagement"]["states"]["active"] == 0 and stale["loop"] == "live"       # the firm's capture counts
    assert review.health(storage.Scopes())["firm"] is None


def v1_store(workspace, slug="alpha"):
    folder = workspace / "clients" / slug / "state" / "memory" / "lessons"
    (folder / "_review_queue").mkdir(parents=True)
    (folder / "_archive").mkdir()

    def old(identifier, text, state, **more):
        return {"id": identifier, "title": "[explicit] " + " ".join(text.split()[:8]), "short": text.split("\n")[0],
                "full_text": text, "confidence": "HIGHEST", "trigger": "explicit_lesson", "captured_at": 1794667931,
                "last_seen": 1794667931, "state": state, "helpful_count": 0, "stale_count": 0, **more}
    (folder / "_review_queue" / "1794667931-a5f651d444ee.json").write_text(json.dumps(old(
        "a5f651d444ee6978", "Flow formulas that reference a screen choice return the choice label.\nUse a decision.",
        "review_pending")), encoding="utf-8")
    (folder / "active.json").write_text(json.dumps([old(
        "0a6c725697530000", "When: you deploy a FlexiPage. Do: retrieve it first. Because: labels get lost.", "active",
        helpful_count=2), old("9557e450044a1111", "Mail jane.doe@example.org about record 001A000001AbCdEFGH",
                              "active")]), encoding="utf-8")
    (folder / "_archive" / "1794667931-61478dc32be7.json").write_text(json.dumps(old(
        "61478dc32be72222", "An old lesson that was archived", "archive", stale_count=2)), encoding="utf-8")
    (folder / "_review_queue" / "1794660000-handedited00.json").write_text("{broken", encoding="utf-8")
    (folder / "_review_queue" / "1794660002-oddtimes0000.json").write_text(json.dumps(old(
        "0dd71e5000000000", "A lesson whose times are not times", "active", captured_at=10 ** 18,
        last_seen="yesterday")), encoding="utf-8")
    (folder / "_review_queue" / "1794660001-secret.json").write_text(json.dumps(old(
        "5ec4e70000000000", "the password: " + "Wint3r" + "!Pass99 opens the org", "review_pending")),
        encoding="utf-8")
    return folder


def test_v1_json_lessons_import_once_and_the_old_files_stay(run, workspace, scopes):
    folder = v1_store(workspace)
    old_files = {path.name: path.read_bytes() for path in folder.rglob("*") if path.is_file()}
    code, out, _ = run("migrate", "--json")
    report = json.loads(out)
    assert code == 0 and len(report["imported"]) == 5 and report["already"] == 0
    assert sorted(problem["reason"] for problem in report["problems"]) == [
        "not imported: password or secret in its text", "not readable JSON"]
    lessons = {item.id: item for item in storage.load(scopes("alpha"))}
    # A record whose times cannot be read is dated today and has no day of last use.
    odd = lessons.pop(f"L-{record.today().replace('-', '')}-0dd71e5000000000")
    assert (odd.state, odd.created, odd.last_verified) == ("active", record.today(), "")
    assert set(lessons) == {"L-20261114-a5f651d444ee6978", "L-20261114-0a6c725697530000",
                            "L-20261114-9557e450044a1111", "L-20261114-61478dc32be72222"}
    pending = lessons["L-20261114-a5f651d444ee6978"]
    assert (pending.state, pending.source, pending.created) == ("candidate", "harvested", "2026-11-14")
    assert pending.title == "Flow formulas that reference a screen choice return the choice label"
    assert pending.body.endswith("Use a decision.") and pending.applies["types"] == ["Flow"]
    assert pending.evidence[0]["ref"] == "earlier store, lesson a5f651d444ee6978"
    active = lessons["L-20261114-0a6c725697530000"]
    assert (active.state, active.when, active.do) == ("active", "you deploy a FlexiPage", "retrieve it first")
    assert "marked helpful 2 times" in active.evidence[0]["ref"] and active.last_verified == "2026-11-14"
    assert lessons["L-20261114-9557e450044a1111"].body == "Mail <email> about record <record-id>"
    assert lessons["L-20261114-61478dc32be72222"].state == "retired"
    assert {path.name: path.read_bytes() for path in folder.rglob("*") if path.is_file()} == old_files
    # Idempotent: a second run imports nothing and changes no lesson file.
    files = lesson_files(workspace)
    code, out, _ = run("migrate")
    assert code == 0 and out.startswith("Imported 0 lesson(s) from the earlier store; 5 were already imported.")
    assert lesson_files(workspace) == files
    # Only the selected client is read; another client's old store is left alone.
    v1_store(workspace, "beta")
    assert storage.load(scopes("beta"), only="client") == []
    assert run("migrate", client=None)[0] == 2
    code, out, _ = run("migrate", client="beta")
    assert code == 0 and len(storage.load(scopes("beta"), only="client")) == 5
