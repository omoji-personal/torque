"""Promote a lesson to a stronger or wider form, through a proposal a person reads.

`propose` writes a proposal file: the exact text, the destination, what the scrub
replaced, and what a person must confirm. It applies nothing. `apply` performs the one
promotion that stays inside the workspace: a client's or an initiative's lesson to a
firm lesson (a copy without client words; the source is marked `promoted_to`). For a
rule, a skill, a recipe, a check or a platform lesson the proposal is the deliverable:
shared and shipped guidance is changed by a person, or by a pull request.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re

from . import lesson as record
from . import recall, review, scrubber, storage
from .intake import Refused, shipped_ids
from .lesson import Lesson, LessonError
from .storage import Scopes

TARGETS = ("firm", "platform", "rule", "skill", "recipe", "check")
_FENCE = "~~~~"
_BLOCK = re.compile(r"^~~~~lesson[ \t]*\n(.*?)\n~~~~[ \t]*$", re.S | re.M)
_DESTINATION = {
    "firm": "this workspace's firm lessons (`lessons/`), shown for every client and initiative",
    "platform": "the `lessons/` folder of the Torque repository, through a pull request (it then ships to everyone)",
    "rule": "a standing rule file{name}: one line that loads on every turn, so add it only with an equal removal "
            "or spare budget",
    "skill": "the gotchas of the skill{name}, loaded when that skill is used",
    "recipe": "a step of the recipe{name}, read when that recipe runs",
    "check": "a deterministic check{name} (a lint, a pre-deploy check or a test) that catches this without "
             "anyone remembering it",
}
_CONFIRM = {
    "firm": ["The text names no client, person, org or client-specific field.",
             "It holds outside the client it came from (a second case, or a reason to expect it everywhere).",
             "No firm lesson already says this (`torque lesson search WORDS`)."],
    "platform": ["The text names no client, person, org or firm practice.",
                 "It was shown outside any client org: an official document, or a reproduction in a scratch or "
                 "developer org. State that evidence honestly in the file.",
                 "A maintainer reviews the pull request; adding the file is not publishing it."],
    "rule": ["It is a default that applies to almost every task; otherwise prefer a recipe step or a check.",
             "The always-loaded text stays within its budget (remove a line for each line added)."],
    "skill": ["The skill is the one an agent loads for this kind of work.", "The entry carries its check."],
    "recipe": ["The step sits where the work happens, before the action it guards."],
    "check": ["A program can decide it from files or command output; write the test first from the original case."],
}


def _sentence(text: str) -> str:
    text = record.one_line(text)
    return text if not text or text.endswith((".", "!", "?")) else text + "."


def _text_for(item: Lesson, target: str) -> str:
    """The exact text proposed for a destination other than a lesson file."""
    when, do, why = item.when or item.title, item.do, item.because
    if target == "rule":
        return _sentence(f"- When {when}: {do or item.title}") + (f" ({_sentence(why)[:-1]}.)" if why else "")
    if target == "skill":
        return (f"- **{_sentence(item.title)}** When {_sentence(f'{when}: {do}' if do else when)}"
                + (f" Why: {_sentence(why)}" if why else "") + " Check: <how to see it holds>.")
    if target == "recipe":
        return _sentence(f"- When {when}: {do or item.title}") + (f" Reason: {_sentence(why)}" if why else "")
    types = ", ".join(item.applies.get("types", [])) or "any"
    commands = ", ".join(item.applies.get("commands", [])) or "none named"
    return "\n".join([f"Detect: {_sentence(when)}", f"Expect: {_sentence(do or item.title)}",
                      f"Reason: {_sentence(why) if why else 'not recorded.'}",
                      f"Metadata types: {types}. Commands: {commands}.",
                      "Report the finding with the fix; do not block unless the consultant asks for a gate."])


def propose(scopes: Scopes, wanted: str, target: str, name: str = "") -> dict:
    """Write the proposal for promoting a lesson. Returns what was written; changes no lesson."""
    if target not in TARGETS:
        raise LessonError(f"unknown destination `{target}`: use one of {', '.join(TARGETS)}.")
    item = storage.resolve(recall.everything(scopes), wanted)
    if item.state != "active":
        raise LessonError(f"{item.id} is {item.state}; promote an active lesson (review it first: torque lesson "
                          f"verify {item.id} --evidence ...).")
    if target in ("firm", "platform") and item.scope in (target, "platform"):
        raise LessonError(f"{item.id} is already a {item.scope} lesson.")
    folder = review.proposals_dir(scopes)
    if folder is None:
        raise LessonError("a proposal is written in a workspace: add --workspace PATH.")
    day = record.today()
    copy = replace(item, applies={key: list(values) for key, values in item.applies.items()},
                   evidence=[dict(entry) for entry in item.evidence], supersedes=[], promoted_to=[],
                   extra=dict(item.extra), path=None)
    found = scrubber.scrub_lesson(copy, firm_terms=scrubber.workspace_terms(scopes.workspace), generalize=True)
    if found.refused:
        raise Refused(found.refusal(target).replace("Not saved", "No proposal written"), found)
    if target in ("firm", "platform"):
        taken = storage.taken_ids(scopes.folder("firm")) | shipped_ids() | {item.id}
        copy.id, copy.scope, copy.created, copy.review_by = record.new_id(day, taken), target, day, record.later(day)
        text = record.render(copy).rstrip("\n")
    else:
        text = _text_for(copy, target)
    label = f" `{name}`" if name else ""
    replaced = ", ".join(f"{kind} ({count})" for kind, count in found.masked.items()) or "nothing"
    path = storage._private_dir(folder) / f"promote-{item.id}-to-{target}.md"
    apply_line = (f"On the consultant's word: `torque lesson promote --apply {path.name}` (from this scope). It "
                  "writes the firm lesson and marks the source; nothing else changes."
                  if target == "firm" else
                  "A person applies this, in the destination above. Afterwards record it, so the lesson is no "
                  f"longer shown twice: `torque lesson edit {item.id} --set promoted_to={target}:"
                  f"{name or 'NAME'}`")
    front = {"proposal": "promote", "lesson": item.id, "from": item.scope, "to": target, "name": name,
             "created": day, "replaced": dict(found.masked)}
    body = "\n".join([
        "---", *(f"{key}: {record._yaml(value)}" for key, value in front.items()), "---",
        f"# Proposal: promote {item.id} to {target}{label}", "",
        f"Source: {item.id} ({item.scope}): {copy.title}",
        f"Destination: {_DESTINATION[target].format(name=label)}.",
        f"The scrub replaced: {replaced}. It knows shapes and this workspace's client list, not every name: read "
        "the text.", "",
        "## Text", "", "Edit it here if it needs rewording; the text between the fences is what is applied.", "",
        f"{_FENCE}lesson", text, _FENCE, "",
        "## To confirm", "", *(f"- [ ] {line}" for line in _CONFIRM[target]), "",
        "## Apply", "", apply_line, ""])
    storage._atomic_write(path, body)
    return {"proposal": str(path), "lesson": item.id, "from": item.scope, "to": target, "name": name,
            "replaced": dict(found.masked), "text": text, "applies_itself": target == "firm"}


def _read(scopes: Scopes, proposal: str) -> tuple[Path, dict, str]:
    """(path, front matter, proposed text) of a proposal file, given by path or by its
    name in the selected scope's proposals folder."""
    path = Path(proposal).expanduser()
    folder = review.proposals_dir(scopes)
    if not path.is_file() and folder is not None and (folder / path.name).is_file():
        path = folder / path.name
    if scopes.foreign(path):
        raise LessonError(f"{proposal} lies in the folder of another client or initiative; select the one the "
                          "lesson comes from: --workspace PATH --client NAME.")
    try:
        front, rest = record.split_front_matter(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        raise LessonError(f"cannot read the proposal {proposal}. Write one with: torque lesson promote ID --to firm"
                          ) from None
    meta = record.load_yaml(front)
    block = _BLOCK.search(rest)
    if not isinstance(meta, dict) or meta.get("proposal") != "promote" or block is None:
        raise LessonError(f"{path.name} is not a promotion proposal written by `torque lesson promote`.")
    return path, meta, block.group(1)


def apply(scopes: Scopes, proposal: str) -> dict:
    """Apply a client-to-firm (or initiative-to-firm) proposal: write the firm lesson and
    mark the source. Any other proposal is applied by a person."""
    path, meta, text = _read(scopes, proposal)
    target, source_id = meta.get("to"), str(meta.get("lesson") or "")
    if target != "firm":
        raise LessonError(
            f"a proposal for a {target} is applied by a person, in the destination it names; Torque does not change "
            f"shared or shipped guidance. Afterwards record it: torque lesson edit {source_id} --set "
            f"promoted_to={target}:{meta.get('name') or 'NAME'}")
    if scopes.engagement is None or scopes.folder("firm") is None:
        raise LessonError("select the client or initiative the lesson comes from: --workspace PATH --client NAME.")
    copy = record.parse(text)
    if copy.scope != "firm":
        raise LessonError("the proposed text must say `scope: firm`.")
    source = storage.resolve(storage.load(scopes, only=scopes.kind), source_id)
    if f"firm:{copy.id}" in source.promoted_to:
        return {"firm": copy.id, "source": source.id, "status": "already applied", "path": None}
    if source.state != "active":
        raise LessonError(f"{source.id} is {source.state} now, and only an active lesson is promoted. Look at it "
                          f"first (torque lesson show {source.id}), or leave the proposal unapplied.")
    found = scrubber.scrub_lesson(copy, firm_terms=scrubber.workspace_terms(scopes.workspace))
    if found.refused:
        raise Refused(found.refusal("firm").replace("Not saved", "Not applied"), found)
    copy.state, copy.path = "active", None
    folder = scopes.folder("firm")
    with storage.transaction(scopes.workspace):
        existing = next((item for item in storage.read_folder(folder, "firm", scopes) if item.id == copy.id), None)
        # The same lesson already there: an earlier apply wrote it and stopped before marking the source.
        written = existing is not None and record.content_key(existing) == record.content_key(copy)
        if not written and (existing is not None or copy.id in storage.taken_ids(folder)):
            raise LessonError(f"a firm lesson {copy.id} already exists; write a fresh proposal.")
        firm_path = existing.path if written else storage.save(copy, folder)
    with storage.transaction(scopes.engagement):
        source = storage.resolve(storage.read_folder(scopes.folder(source.scope), source.scope, scopes), source.id)
        source.promoted_to.append(f"firm:{copy.id}")
        storage.save(source, scopes.folder(source.scope))
    storage.log(scopes.engagement, [{"event": "promoted", "id": source.id, "to": f"firm:{copy.id}"}])
    return {"firm": copy.id, "source": source.id, "status": "applied", "path": str(firm_path),
            "proposal": str(path), "masked": found.masked}
