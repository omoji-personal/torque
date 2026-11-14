"""One set of adversarial fixtures, reused across every way text enters a lesson or is
printed from one: add, session add --learned, edit, feedback, evidence, harvest,
migrate, promote (proposal and apply) and review. None of them may appear in a
wider-scope file or in a command's normal output.

Fixtures: another client's body text, a labelled secret in prose and in a lowercase
assignment, a two-character client name, an org Id of each length without padding, and a
front-matter key Torque does not know that holds another client's name. Every value is
made up and assembled from pieces, so no tracked line holds a credential-shaped pair."""
import json
from pathlib import Path
import re

import pytest

from torque import cli, workspace as ws
from jsc_memory import lesson as record

FOREIGN = "Beta-only body " + "9q2w"


def _checksum(fifteen):
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
    return fifteen + "".join(alphabet[sum(1 << bit for bit, char in enumerate(fifteen[part * 5:part * 5 + 5])
                                          if char.isupper())] for part in range(3))


SECRET_PROSE = "Plainly" + "-Fake-42"
SECRET_LOWER = "onlylower" + "caseletters"
ORG_15 = "00D" + "8c012345678A"
ORG_18 = _checksum("00D" + "abcdefGHIJKL")
# name: (text carrying it, what must never be seen)
FIXTURES = {
    "secret in prose": ('The password is "' + SECRET_PROSE + '"', SECRET_PROSE),
    "secret, lowercase assignment": ("pass" + "code=" + SECRET_LOWER, SECRET_LOWER),
    "two-character client name": ("XY needs field permission grants", "XY"),
    "org Id, 15": (f"The full copy is {ORG_15}", ORG_15),
    "org Id, 18": (f"The full copy is {ORG_18}", ORG_18),
    "unknown key naming a client": ("client_context: Beta Land Trust", "Beta Land Trust"),
    "another client's body text": (None, FOREIGN),
}
SECRETS_AND_IDS = ["secret in prose", "secret, lowercase assignment", "org Id, 15", "org Id, 18"]
IDENTIFIERS = [*SECRETS_AND_IDS, "two-character client name"]
FLOW = "When: you deploy a flow. Do: read the active version back. Because: a deploy can leave it inactive."


def seen(value, text):
    return bool(re.search(r"(?<![A-Za-z0-9])XY(?![A-Za-z0-9])", text)) if value == "XY" else value in text


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    """The workspace every case starts from, built once (a copy per case)."""
    root = ws.init_workspace(tmp_path_factory.mktemp("template") / "ws", "Synthetic workspace")
    ws.add_client(root, "Alpha Nonprofit", "alpha-dev")
    ws.add_client(root, "Beta Land Trust")
    ws.add_client(root, "XY")
    beta = root / "clients" / "beta-land-trust"
    # Another client's words, where an alpha command might be pointed at them.
    (beta / "notes.md").write_text(f"# {FOREIGN}\n\n{FOREIGN} again.\n", encoding="utf-8")
    (beta / "lessons").mkdir()
    (beta / "lessons" / "L-20261114-beef0001.md").write_text(record.render(record.Lesson(
        id="L-20261114-beef0001", title=FOREIGN, scope="client", state="active", source="consultant",
        do=FOREIGN, created="2026-11-14")), encoding="utf-8")
    _v1_store(beta, FOREIGN)
    return root


@pytest.fixture
def world(template, tmp_path, monkeypatch, capsys):
    for name in ("TORQUE_WORKSPACE", "JSC_ROOT", "TORQUE_CLIENT", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(name, raising=False)
    import shutil
    root = Path(shutil.copytree(template, tmp_path / "ws"))
    alpha = root / "clients" / "alpha-nonprofit"
    beta = root / "clients" / "beta-land-trust"
    seeds = {path for path in root.rglob("*") if path.is_file()}
    outputs = []

    def run(*args, client="alpha-nonprofit"):
        scope = ["--workspace", str(root)] + (["--client", client] if client else [])
        try:
            code = cli.main([*args, *scope])
        except SystemExit as exc:
            code = exc.code
        captured = capsys.readouterr()
        outputs.append(captured.out + captured.err)
        return code, captured.out

    def hand_written(folder, scope, **fields):
        """A lesson file as a person may write it, bypassing capture."""
        identifier = f"L-20261114-{len(list(folder.glob('*.md'))) + 1:08x}" if folder.is_dir() else "L-20261114-00000001"
        item = record.Lesson(id=identifier, scope=scope, source="consultant",
                             created=record.today(), **{"title": "a lesson", "state": "active", **fields})
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{identifier}.md"
        path.write_text(record.render(item), encoding="utf-8")
        seeds.add(path)
        return identifier, path

    class World:
        pass
    world = World()
    world.root, world.alpha, world.beta, world.run, world.outputs = root, alpha, beta, run, outputs
    world.hand_written, world.seeds = hand_written, seeds
    return world


def _v1_store(engagement, text):
    folder = engagement / "state" / "memory" / "lessons"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "active.json").write_text(json.dumps([{
        "id": "0a6c725697531234", "title": "[explicit] " + text[:40], "short": text, "full_text": text,
        "captured_at": 1794667931, "last_seen": 1794667931, "state": "active"}]), encoding="utf-8")


def _firm_lesson(world):
    code, out = world.run("lesson", "add", FLOW, "--scope", "firm", "--source", "consultant", "--json")
    assert code == 0
    return json.loads(out)["id"]


# Each path takes the world and a fixture's text, and runs what a consultant or an agent would.
def path_add(world, text):
    world.run("lesson", "add", text, "--source", "consultant")
    world.run("lesson", "add", text, "--scope", "firm", "--source", "consultant")


def path_add_from_another_clients_file(world, text):
    for option in ("--from-file", "--body-file"):
        code, _ = world.run("lesson", "add", FLOW, option, str(world.beta / "notes.md"))
        assert code == 2


def path_learned(world, text):
    for scope in ("client", "firm"):
        world.run("session", "add", "--summary", "Synthetic session", "--status", "executed", "--learned", text,
                  "--learned-scope", scope, "--learned-from", "consultant")


def path_edit(world, text):
    identifier = _firm_lesson(world)
    if text.startswith("client_context:"):
        path = world.root / "lessons" / f"{identifier}.md"
        path.write_text(path.read_text(encoding="utf-8").replace("\nsource:", f"\n{text}\nsource:", 1),
                        encoding="utf-8")
        world.seeds.add(path)
        code, _ = world.run("lesson", "edit", identifier, "--set", "do=read the version back")
        assert code == 4
        return
    world.run("lesson", "edit", identifier, "--set", f"do={text}")
    world.run("lesson", "edit", identifier, "--set", f"title={text}")


def path_feedback(world, text):
    identifier = _firm_lesson(world)
    world.run("lesson", "stale", identifier, "--reason", text)
    world.run("lesson", "helpful", identifier, "--note", text)
    world.run("lesson", "retire", identifier, "--reason", text)


def path_evidence(world, text):
    world.run("lesson", "add", FLOW, "--scope", "firm", "--source", "consultant", "--evidence", f"reproduced:{text}")
    identifier = _firm_lesson(world)
    world.run("lesson", "verify", identifier, "--evidence", f"reproduced:{text}")


def path_harvest(world, text):
    notes = world.root / "host-notes"
    notes.mkdir(exist_ok=True)
    if text:
        (notes / "reference_one.md").write_text(f"{text}\n\nmore\n", encoding="utf-8")
        world.seeds.add(notes / "reference_one.md")
    for host in ("claude", "codex"):
        world.run("lesson", "harvest", "--host", host, "--dir", str(world.root), "--since", "2020-01-01", "--json")
        world.run("lesson", "harvest", "--host", host, "--dir", str(world.root), "--since", "2020-01-01")


def path_migrate(world, text):
    if text:
        _v1_store(world.alpha, text)
        world.seeds |= set((world.alpha / "state").rglob("*.json"))
    world.run("lesson", "migrate", "--json")
    world.run("lesson", "migrate")


def path_promote(world, text):
    if text.startswith("client_context:"):
        identifier, path = world.hand_written(world.alpha / "lessons", "client", when="you deploy a flow",
                                              do="read it back")
        path.write_text(path.read_text(encoding="utf-8").replace("\nsource:", f"\n{text}\nsource:", 1),
                        encoding="utf-8")
    else:
        identifier, _ = world.hand_written(world.alpha / "lessons", "client", when="you deploy a flow", do=text)
    code, out = world.run("lesson", "promote", identifier, "--to", "firm", "--json")
    if code == 0:
        assert world.run("lesson", "promote", "--apply", json.loads(out)["proposal"])[0] == 0
    for target in ("rule", "platform"):
        world.run("lesson", "promote", identifier, "--to", target)


def path_review(world, text):
    if text and not seen("XY", text):
        world.hand_written(world.alpha / "lessons", "client", title=text, do=text, state="candidate")
    if text:
        world.hand_written(world.root / "lessons", "firm", title=text, do=text, state="candidate")
    world.run("lesson", "review")
    world.run("lesson", "review", "--json")
    world.run("lesson", "review", client=None)


PATHS = {
    "add": (path_add, SECRETS_AND_IDS),
    "add --scope firm": (lambda world, text: world.run("lesson", "add", text, "--scope", "firm", "--source",
                                                       "consultant"), IDENTIFIERS),
    "add from another client's file": (path_add_from_another_clients_file, ["another client's body text"]),
    "session add --learned": (path_learned, SECRETS_AND_IDS),
    "session add --learned firm": (lambda world, text: world.run(
        "session", "add", "--summary", "Synthetic session", "--status", "executed", "--learned", text,
        "--learned-scope", "firm", "--learned-from", "consultant"), IDENTIFIERS),
    "edit": (path_edit, [*IDENTIFIERS, "unknown key naming a client"]),
    "stale, helpful, retire": (path_feedback, IDENTIFIERS),
    "evidence": (path_evidence, IDENTIFIERS),
    "harvest": (path_harvest, [*SECRETS_AND_IDS, "another client's body text"]),
    "migrate": (path_migrate, [*SECRETS_AND_IDS, "another client's body text"]),
    "promote and apply": (path_promote, [*IDENTIFIERS, "unknown key naming a client"]),
    "review": (path_review, [*IDENTIFIERS, "another client's body text"]),
}
CASES = [pytest.param(path, fixture, id=f"{path}: {fixture}") for path, (_, fixtures) in PATHS.items()
         for fixture in fixtures]


@pytest.mark.parametrize("path,fixture", CASES)
def test_no_fixture_reaches_a_wider_scope_file_or_normal_output(world, path, fixture):
    text, value = FIXTURES[fixture]
    PATHS[path][0](world, text)
    printed = "\n".join(world.outputs)
    assert printed.strip(), "the path printed nothing: it did not run"
    assert not seen(value, printed), f"{fixture} printed by {path}"
    assert FOREIGN not in printed, f"another client's text printed by {path}"
    # Every file Torque wrote, in any scope: nothing carries the value (the seeded inputs aside).
    written = [item for item in world.root.rglob("*") if item.is_file() and item not in world.seeds]
    for item in written:
        content = item.read_text(encoding="utf-8", errors="replace")
        assert not seen(value, content), f"{fixture} written to {item.relative_to(world.root)} by {path}"
        assert FOREIGN not in content, f"another client's text written to {item.relative_to(world.root)} by {path}"


def test_every_path_and_every_fixture_is_covered():
    covered = {fixture for _, fixtures in PATHS.values() for fixture in fixtures}
    assert covered == set(FIXTURES)
    assert {"add", "edit", "harvest", "migrate", "promote and apply", "review"} <= set(PATHS)
