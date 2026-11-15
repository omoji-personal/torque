"""Judge a lesson after it was used: helpful, stale, verified, retired, restored, edited.

Feedback is an event in the scope's log; a change to the lesson itself (verify, retire,
restore, edit) rewrites its one file under the scope's lock. A platform lesson ships
with Torque and is read-only here: it can be marked helpful or stale in this workspace
(a stale mark takes it out of this workspace's recall), never rewritten.
"""
from __future__ import annotations

from pathlib import Path

from . import lesson as record
from . import recall, scrubber, storage, vocab
from .intake import Refused, parse_evidence
from .lesson import Lesson, LessonError
from .storage import Scopes, Tally

# What `torque lesson edit --set` may change. The id, the scope and the creation date are
# fixed; evidence is added with `verify`, and the body is edited in the file.
EDITABLE = ("title", "when", "do", "because", "kind", "last_verified", "review_by",
            "supersedes", "promoted_to", *(f"applies.{name}" for name in record.APPLIES))
# State and source decide whether a lesson is shown at the point of use, so they change
# only through the commands that record why.
ELSEWHERE = {
    "state": "a candidate becomes active on a basis (`torque lesson verify ID --evidence ...`); a lesson leaves "
             "with `torque lesson retire ID --reason ...` and comes back with `torque lesson restore ID`",
    "source": "where a lesson comes from is fixed when it is saved; when the consultant confirms it, record that "
              "with `torque lesson verify ID --evidence consultant-said`"}


class ReadOnly(LessonError):
    """The lesson ships with Torque; a workspace cannot rewrite it."""


def find(scopes: Scopes, wanted: str) -> Lesson:
    """The one lesson an id names among everything this call may read."""
    return storage.resolve(recall.everything(scopes), wanted)


def tally_of(scopes: Scopes, item: Lesson) -> Tally:
    return storage.tally(storage.events_for(scopes)).get(item.id) or Tally()


def _owned(scopes: Scopes, item: Lesson, action: str) -> Path:
    """The folder of a lesson this workspace may rewrite."""
    folder = scopes.folder(item.scope)
    if item.scope == "platform" or folder is None:
        raise ReadOnly(f"{item.id} ships with Torque and cannot be {action} from a workspace. If it is wrong or out "
                       f"of date here, mark it: torque lesson stale {item.id} --reason \"...\" (it then stays out of "
                       "this workspace's recall), and report it to the Torque repository.")
    return folder


def _fresh(scopes: Scopes, item: Lesson) -> Lesson:
    """The lesson as its file says now, read again under the lock."""
    return storage.resolve(storage.read_folder(scopes.folder(item.scope), item.scope, scopes), item.id)


def _clean(scopes: Scopes, scope: str, text: str) -> str:
    """A reason or a note for the events log: scrubbed like a lesson of that scope."""
    firm = scope in ("firm", "platform")
    text, found = scrubber.scrub(record.one_line(text),
                                 firm_terms=scrubber.workspace_terms(scopes.workspace) if firm else None)
    if found.refused:
        raise Refused(found.refusal("firm" if firm else scope).replace("Not saved", "Not recorded"), found)
    return text


def _event(scopes: Scopes, item: Lesson, name: str, **fields) -> None:
    root = scopes.event_root(item.scope)
    if root is None:
        raise LessonError("feedback is kept in a workspace: add --workspace PATH.")
    storage.log(root, [{"event": name, "id": item.id, **{key: value for key, value in fields.items() if value}}],
                must=True)


def helpful(scopes: Scopes, wanted: str, note: str = "", session: str | None = None) -> Lesson:
    item = find(scopes, wanted)
    if item.state == "retired":
        raise LessonError(f"{item.id} is retired. If it helped after all, bring it back first: "
                          f"torque lesson restore {item.id}")
    _event(scopes, item, "helpful", note=_clean(scopes, item.scope, note), session=session)
    return item


def stale(scopes: Scopes, wanted: str, reason: str, superseded_by: str | None = None) -> tuple[Lesson, Lesson | None]:
    """Mark a lesson stale: it leaves default recall at once and stays out until it is
    verified, restored or retired. `superseded_by` names the lesson that replaces it."""
    item = find(scopes, wanted)
    if not record.one_line(reason):
        raise LessonError("say why it is stale: --reason TEXT (what changed, or what is wrong with it).")
    newer = find(scopes, superseded_by) if superseded_by else None
    if newer is not None and newer.id == item.id:
        raise LessonError("a lesson cannot supersede itself.")
    reason = _clean(scopes, item.scope, reason)
    if newer is not None and newer.scope != "platform" and item.id not in newer.supersedes:
        folder = scopes.folder(newer.scope)
        with storage.transaction(scopes.root(newer.scope)):
            newer = _fresh(scopes, newer)
            newer.supersedes.append(item.id)
            storage.save(newer, folder)
    _event(scopes, item, "stale", reason=reason, superseded_by=newer.id if newer else "")
    return item, newer


def verify(scopes: Scopes, wanted: str, evidence=()) -> tuple[Lesson, str]:
    """Record that a lesson was checked and holds. It clears a stale mark, dates the
    lesson, and admits a candidate that now has a basis. Returns (lesson, what happened)."""
    item = find(scopes, wanted)
    day = record.today()
    entries = parse_evidence(evidence, day)
    basis = next((entry["type"] for entry in entries), "")
    if item.scope == "platform":
        _event(scopes, item, "verified", basis=basis)
        return item, "recorded for this workspace; the lesson itself ships with Torque and was not changed"
    folder = _owned(scopes, item, "verified")
    with storage.transaction(scopes.root(item.scope)):
        item = _fresh(scopes, item)
        if item.state == "retired":
            raise LessonError(f"{item.id} is retired. Bring it back first: torque lesson restore {item.id}")
        was = "candidate" if record.unbacked_active(item) else item.state
        for entry in entries:
            if entry.get("ref"):
                entry["ref"] = _clean(scopes, item.scope, entry["ref"])
        if was == "candidate":
            types = {entry["type"] for entry in [*item.evidence, *entries]}
            if not (item.source == "consultant" or types & record.REVIEW_ADMITTING):
                raise LessonError(
                    f"{item.id} is a candidate, and a candidate becomes active on a basis. Say which: --evidence "
                    "reproduced[:REF] (you saw it happen again), official-doc:URL, second-occurrence[:REF], or "
                    "consultant-said (the consultant confirmed it).")
            item.state = "active"
        item.evidence += entries
        item.last_verified, item.review_by = day, record.later(day)
        storage.save(item, folder)
    _event(scopes, item, "verified", basis=basis)
    return item, "now active" if was == "candidate" else "verified"


def retire(scopes: Scopes, wanted: str, reason: str) -> Lesson:
    item = find(scopes, wanted)
    folder = _owned(scopes, item, "retired")
    if not record.one_line(reason):
        raise LessonError("say why it is retired: --reason TEXT.")
    reason = _clean(scopes, item.scope, reason)
    with storage.transaction(scopes.root(item.scope)):
        item = _fresh(scopes, item)
        item.state = "retired"
        storage.save(item, folder)
    _event(scopes, item, "retired", reason=reason)
    return item


def restore(scopes: Scopes, wanted: str) -> tuple[Lesson, str]:
    """Bring a lesson back: a retired one returns to the state its evidence supports,
    and a stale mark is cleared. Returns (lesson, what happened)."""
    item = find(scopes, wanted)
    marked = bool(tally_of(scopes, item).stale_mark)
    if item.state != "retired" and not marked:
        return item, "nothing to restore: it is neither retired nor marked stale"
    if item.state == "retired":
        folder = _owned(scopes, item, "restored")
        with storage.transaction(scopes.root(item.scope)):
            item = _fresh(scopes, item)
            types = {entry["type"] for entry in item.evidence}
            item.state = "active" if item.source == "consultant" or types & record.REVIEW_ADMITTING else "candidate"
            storage.save(item, folder)
    _event(scopes, item, "restored")
    return item, f"restored as {item.state}" + ("; the stale mark is cleared" if marked else "")


def edit(scopes: Scopes, wanted: str, assignments) -> tuple[Lesson, list[str]]:
    """Change fields of a lesson: `key=value` pairs, lists as comma-separated words
    (`applies.types=Flow,CustomField`). Returns (lesson, the keys changed)."""
    item = find(scopes, wanted)
    folder = _owned(scopes, item, "edited")
    changes: dict = {}
    for assignment in assignments or ():
        key, separator, value = str(assignment).partition("=")
        key = key.strip()
        if separator and key in ELSEWHERE:
            raise LessonError(f"`{key}` is not set here: {ELSEWHERE[key].replace(' ID', ' ' + item.id)}.")
        if not separator or key not in EDITABLE:
            raise LessonError(f"cannot set `{key or assignment}`. Use key=value with one of: {', '.join(EDITABLE)}. "
                              "For the body or the evidence, edit the file or use `verify --evidence`.")
        changes[key] = value.strip()
    if not changes:
        raise LessonError("nothing to change: give at least one --set key=value.")
    with storage.transaction(scopes.root(item.scope)):
        item = _fresh(scopes, item)
        data = item.front_matter()
        for key, value in changes.items():
            words = [word.strip() for word in value.split(",") if word.strip()]
            if key.startswith("applies."):
                name = key.split(".", 1)[1]
                spell = {"tasks": vocab.tag, "types": vocab.canonical_type}.get(name, record.one_line)
                data["applies"][name] = list(dict.fromkeys(spell(word) for word in words))
            else:
                data[key] = words if key in ("supersedes", "promoted_to") else value
        edited = record.from_front_matter(data, item.body, path=item.path)
        if not edited.title:
            raise LessonError("a lesson needs a title.")
        found = scrubber.scrub_lesson(
            edited, firm_terms=scrubber.workspace_terms(scopes.workspace) if edited.scope == "firm" else None)
        if found.refused:
            raise Refused(found.refusal(edited.scope).replace("Not saved", "Not changed"), found)
        storage.save(edited, folder)
    return edited, list(changes)
