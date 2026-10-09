"""Guarded reads: the lane commands, their own checks, the policy file, the
test-record registry and the record of what each call showed.

A lane command does not rely on the gate having seen the call. Before it calls
the org it checks the workspace's mode and setting, the client's consent and
that org's data classes, the policy (and the registry), and, inside an AI
session, the session's launch record. Then it checks the org's identity, reads
describe and the org's field classification, and only then reads data. It
prints only text built here from what guarded_core returns: a refusal is one
line that holds what the caller typed and names from describe, and any other
failure is one fixed line. See docs/guarded-reads.md."""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

from . import consent, guarded_core as core, workspace as ws

POLICY_FILE = "guarded-policy.json"
REGISTRY_FILE = "test-records.json"
REGISTRY_SCHEMA = "torque.test_records/1"
ACTIVITY_DIR = "guarded-activity"
ACTIVITY_SCHEMA = "torque.guarded_activity/1"
# The consent class each lane needs for the org it reads, besides `metadata`.
LANE_CLASS = {"counts": "counts", "fill": "counts", "config": "config_records", "record": "test_records",
              "related": "test_records", "org": None, "candidates": None}
SF_TIMEOUT = 120
FIELD_GROUP, FIELD_GROUP_CHARS = 100, 4000   # the fields one query carries: its text travels in a web address
MAX_REGISTERED = 2000
Error = core.GuardedError

OFF = ("guarded reads are off for this workspace. The owner turns them on at a real terminal with "
       "`torque workspace guarded-reads on --path W`")
MADE_UP = ("Registering a record says that it holds made-up data, and so do the records that feed its totals. "
           "Its values will be shown to the AI session.")


def setting_on(config) -> bool:
    return ws.guarded_reads_on(config)


# ---------------------------------------------------------------- files

def _folder(workspace, client) -> tuple[Path, dict, str, Path]:
    """(workspace root, its config, the client's slug, the client's folder)."""
    try:
        root, config = ws.load_workspace(workspace)
        slug = ws.slug_for(client)
        return root, config, slug, ws.load_client(root, slug)[0]
    except (ws.WorkspaceError, OSError, AttributeError, TypeError):
        raise Error("the workspace or the client could not be read") from None


def _approvals(folder: Path, name: str, create: bool = False) -> Path:
    base = ws._inside(folder, folder / "approvals")
    if create:
        base.mkdir(mode=0o700, exist_ok=True)
    return ws._inside(folder, base / name)


def _read(path: Path):
    """The file's JSON, or None when there is no file. Unreadable is an error."""
    if not path.exists() and not path.is_symlink():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Error(f"{path.name} could not be read; the consultant repairs or removes it") from None


def _write(path: Path, payload: dict) -> None:
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if path.exists():
        ws._atomic_replace_text(path, text)
    else:
        ws.atomic_write_new(path, text)


def policy_payload(folder: Path) -> dict:
    """The policy file as stored (checked), or a new empty one."""
    payload = _read(_approvals(folder, POLICY_FILE))
    if payload is None:
        return {"schema": core.POLICY_SCHEMA, "fields": {}, "objects": {}}
    core.parse_policy(payload)
    return payload


def load_policy(folder: Path) -> core.Policy:
    return core.parse_policy(_read(_approvals(folder, POLICY_FILE)))


def load_registry(folder: Path) -> list[dict]:
    """The registered test records. Anything malformed stops the test-record lane."""
    payload = _read(_approvals(folder, REGISTRY_FILE))
    if payload is None:
        return []
    bad = Error("the test-record registry is malformed; the consultant repairs it with `torque guarded test-records`")
    if not isinstance(payload, dict) or payload.get("schema") != REGISTRY_SCHEMA \
            or not isinstance(payload.get("records"), list) or len(payload["records"]) > MAX_REGISTERED:
        raise bad
    for entry in payload["records"]:
        try:
            ok = (isinstance(entry, dict) and isinstance(entry.get("alias"), str) and entry["alias"]
                  and isinstance(entry.get("org_id_18"), str) and entry["org_id_18"].isalnum()
                  and core.object_name(entry.get("object")) and core.record_id(entry.get("id")) == entry["id"])
        except Error:
            ok = False
        if not ok:
            raise bad
    return payload["records"]


def registered_ids(registry: list[dict], org_id: str, sobject: str | None = None) -> set[str]:
    return {e["id"] for e in registry if e["org_id_18"] == org_id
            and (sobject is None or e["object"].casefold() == sobject.casefold())}


# ---------------------------------------------------------------- the command's own checks

@dataclass
class Context:
    root: Path
    slug: str
    folder: Path
    alias: str
    org_id: str
    org_kind: str
    policy: core.Policy
    registry: list = dc_field(default_factory=list)


def _agent_reason(env) -> str:
    from . import presence
    return presence.agent_reason(env)


def _session_problem(root: Path, slug: str, env) -> str:
    """Inside an AI session a lane reads only for the client the session was launched
    for, in the workspace it was launched in. A session is known by its host's
    markers (missing launch variables there are a refusal, not a pass) or, on a
    host that sets none, by the launch variables themselves."""
    from . import launch
    env = os.environ if env is None else env
    if not _agent_reason(env) and not (env.get("TORQUE_LAUNCH") or env.get("TORQUE_CLIENT")):
        return ""
    record = launch.session_record(root, env)
    if record is None or record.get("client") != slug:
        return ("inside an AI session this command reads only for the client the session was launched for, "
                "in the workspace it was launched in")
    return ""


def open_lane(workspace, client, alias, lane: str, *, env=None, resolve=None, registry: bool = False) -> Context:
    """Every check a lane makes before it reads anything: the local ones first (no
    org call is made when one fails), then the org's identity."""
    root, config, slug, folder = _folder(workspace, client)
    if ws.access_mode(config) != "connected":
        raise Error("guarded reads work in a connected workspace only")
    if not setting_on(config):
        raise Error(OFF)
    if not isinstance(alias, str) or not alias or alias.startswith("-"):
        raise Error("name the org with --target-org ALIAS")
    try:
        item = consent.load_consent(root, slug)
    except ws.WorkspaceError:
        item = None
    if consent.consent_problems(item, client=slug):
        raise Error("this client's consent is not usable; see `torque client consent show`")
    if consent.approved_org(item, alias) is None:
        raise Error("that org is not in this client's consent")
    classes = consent.data_allowed(item, alias)
    need = LANE_CLASS[lane]
    if "metadata" not in classes or (need and need not in classes and "records" not in classes):
        raise Error(f"this client's consent does not cover {need or 'metadata'} for that org")
    policy = load_policy(folder)
    records = load_registry(folder) if registry else []
    problem = _session_problem(root, slug, env)
    if problem:
        raise Error(problem)
    from . import approval
    try:
        org_id, kind = approval._org_identity(item, alias, resolve)
    except ws.WorkspaceError:
        raise Error("the alias does not resolve now to the org this client's consent names") from None
    return Context(root, slug, folder, alias, org_id, kind, policy, records)


# ---------------------------------------------------------------- the org

def _sf(run, argv: list[str], what: str):
    """The `result` of one Salesforce CLI call. Nothing the CLI says is passed on."""
    if run is None:
        from jsc_common.tools import run
    failed = Error(f"the Salesforce CLI did not answer the {what}; nothing is shown")
    try:
        done = run(argv, capture_output=True, text=True, timeout=SF_TIMEOUT, stdin=subprocess.DEVNULL)
        data = json.loads(done.stdout or "")
    except (OSError, subprocess.SubprocessError, ValueError):
        raise failed from None
    if done.returncode != 0 or not isinstance(data, dict) or data.get("status") != 0 or "result" not in data:
        raise failed
    return data["result"]


def _describe(run, alias: str, name) -> core.Describe:
    name = core.object_name(name)
    return core.parse_describe(_sf(run, ["sf", "sobject", "describe", "--sobject", name, "--target-org", alias,
                                         "--json"], "describe"))


def _query(run, alias: str, soql: str, what: str) -> list:
    with tempfile.TemporaryDirectory(prefix="torque-guarded-") as tmp:
        path = Path(tmp) / "query.soql"
        path.write_text(soql, encoding="utf-8")
        result = _sf(run, ["sf", "data", "query", "--file", str(path), "--target-org", alias, "--json"], what)
    if not isinstance(result, dict) or result.get("done") is not True or not isinstance(result.get("records"), list):
        raise Error(f"the Salesforce CLI did not answer the {what}; nothing is shown")
    return result["records"]


def _field_groups(infos: list) -> list[list]:
    """The fields in groups one query can carry, `Id` first in each."""
    ident = [info for info in infos if info.name == "Id"]
    groups, current, size = [], [], 0
    for info in infos:
        if info.name == "Id":
            continue
        if current and (len(current) >= FIELD_GROUP or size + len(info.name) > FIELD_GROUP_CHARS):
            groups.append(ident + current)
            current, size = [], 0
        current.append(info)
        size += len(info.name) + 2
    return [*groups, ident + current]


def _rows(run, alias: str, infos: list, build, what: str) -> list[dict]:
    """Rows read in as many queries as the field list needs and joined on Id.
    `build(fields)` gives the query for one group of fields. Every query has to
    answer with the same rows as the first: a row that is missing later, or one
    that was not there before, stops the call."""
    merged: dict = {}
    odd = Error(f"the Salesforce CLI did not answer the {what} the same way twice; nothing is shown")
    for index, group in enumerate(_field_groups(infos)):
        seen = set()
        for row in _query(run, alias, build(group), what):
            key = row.get("Id") if isinstance(row, dict) else None
            if not isinstance(key, str) or key in seen or (index and key not in merged):
                raise odd
            seen.add(key)
            merged.setdefault(key, {}).update(row)
        if index and seen != set(merged):
            raise odd
    return list(merged.values())


def _row_id(row) -> str:
    """The 18-character id of a row the org returned, or '' when it has none."""
    try:
        return core.record_id(row.get("Id") if isinstance(row, dict) else None)
    except Error:
        return ""


def _facts(run, alias: str, describe: core.Describe, policy: core.Policy) -> core.OrgFacts:
    soql = ("SELECT QualifiedApiName, DataType, ComplianceGroup, SecurityClassification FROM FieldDefinition "
            f"WHERE EntityDefinition.QualifiedApiName = {core.soql_string(describe.name)}")
    try:
        records = _query(run, alias, soql, "field classification")
    except Error:
        records = None
    return core.require_facts(policy, core.parse_facts(records))


# ---------------------------------------------------------------- the record of each call

def _record(ctx: Context, lane: str, entry: dict) -> None:
    """One new file per call, written before anything is shown. No value from the org."""
    body = {"schema": ACTIVITY_SCHEMA, "at": ws._now(), "lane": lane, "org_alias": ctx.alias,
            "org_id_18": ctx.org_id, **entry}
    try:
        ws.require_writable(ctx.root)
        folder = _approvals(ctx.folder, ACTIVITY_DIR, create=True)
        folder.mkdir(mode=0o700, exist_ok=True)
        # The name sorts by time (as fine as the clock gives it), then by a random part.
        seconds, fraction = divmod(time.time_ns(), 10**9)
        name = f"{time.strftime('%Y%m%dT%H%M%S', time.gmtime(seconds))}-{fraction:09d}-{secrets.token_hex(4)}.json"
        fd = os.open(ws._inside(ctx.folder, folder / name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(body, indent=2, ensure_ascii=False) + "\n")
    except (OSError, ws.WorkspaceError):
        raise Error("the record of this call could not be written, so nothing is shown") from None


# ---------------------------------------------------------------- the lanes

def counts(workspace, client, alias, sobject, group_by=(), where=(), *, env=None, resolve=None, run=None) -> dict:
    ctx = open_lane(workspace, client, alias, "counts", env=env, resolve=resolve)
    describe = _describe(run, alias, sobject)
    facts = _facts(run, alias, describe, ctx.policy)
    dimensions = [core.parse_dimension(text, describe, ctx.policy, facts) for text in (group_by or ())]
    filters = core.parse_filters(where, describe, ctx.policy, facts)
    soql, aliases = core.build_counts_query(describe, dimensions, filters)
    shaped = core.shape_counts(dimensions, aliases, _query(run, alias, soql, "count"), ctx.policy)
    _record(ctx, "counts", {"object": describe.name, "dimensions": shaped["columns"],
                            "filters": [f.typed for f in filters], "groups_shown": len(shaped["rows"])})
    return {"lane": "counts", "org": alias, "object": describe.name, "filters": [f.typed for f in filters],
            "datetime_buckets": "UTC", **shaped}


def fill(workspace, client, alias, sobject, fields, where=(), *, env=None, resolve=None, run=None) -> dict:
    ctx = open_lane(workspace, client, alias, "fill", env=env, resolve=resolve)
    describe = _describe(run, alias, sobject)
    facts = _facts(run, alias, describe, ctx.policy)
    infos = core.parse_fill_fields(fields, describe, ctx.policy, facts)
    filters = core.parse_filters(where, describe, ctx.policy, facts)
    shaped = core.shape_fill(infos, _query(run, alias, core.build_fill_query(describe, infos, filters), "count"),
                             ctx.policy)
    counted = {row["field"]: row["filled"] is not None for row in shaped["rows"]}
    _record(ctx, "fill", {"object": describe.name,
                          "fields": {i.name: "shown" if counted.get(i.name) else "suppressed" for i in infos},
                          "filters": [f.typed for f in filters], "total_shown": shaped["total"] is not None})
    return {"lane": "fill", "org": alias, "object": describe.name, "filters": [f.typed for f in filters], **shaped}


def _how(shown) -> str:
    """Whether a field's value was shown or masked, by what the rules returned for it: a
    stored value that reads like a mask (`<set>`) was shown."""
    return "masked" if isinstance(shown, core.Mask) else "shown"


def config(workspace, client, alias, sobject, fields=(), limit=core.CONFIG_LIMIT_DEFAULT, *, env=None,
           resolve=None, run=None) -> dict:
    ctx = open_lane(workspace, client, alias, "config", env=env, resolve=resolve)
    describe = _describe(run, alias, sobject)
    facts = _facts(run, alias, describe, ctx.policy)
    names = [n for n in (fields or ()) if isinstance(n, str) and n.strip()]
    released = ctx.policy.objects.get(describe.name.casefold(), frozenset())
    if not names and released and core.ALL_FIELDS not in released:
        names = [info.name for info in describe.fields.values() if info.name.casefold() in released]
    infos = core.select_names(names, describe, default_all=True)
    found = _rows(run, alias, infos, lambda group: core.build_config_query(describe, ctx.policy, group, limit), "rows")
    rows = [core.show_config_row(describe, infos, record, ctx.policy, facts) for record in found]
    # What was shown is what the rows hold, not what the release would allow: a value the
    # rules could not read prints as a mask, and with no rows nothing was shown at all.
    shown = sorted(i.name for i in infos if any(_how(row[i.name]) == "shown" for row in rows))
    _record(ctx, "config", {"object": describe.name, "fields_shown": shown,
                            "fields_masked": sorted(i.name for i in infos if i.name not in shown) if rows else [],
                            "rows_shown": len(rows)})
    return {"lane": "config", "org": alias, "object": describe.name, "rows": rows, "limit": limit}


def _test_rows(ctx: Context, describe: core.Describe, facts, infos, records: list) -> list[dict]:
    known = registered_ids(ctx.registry, ctx.org_id)
    return [{info.name: core.show_test_value(describe, info, record.get(info.name) if isinstance(record, dict)
                                             else None, ctx.policy, facts, known) for info in infos}
            for record in records]


def _all_or_named(describe: core.Describe, fields, every: bool) -> list:
    names = [n for n in (fields or ()) if isinstance(n, str) and n.strip()]
    if every and names:
        raise Error("give --fields or --all, not both")
    return core.select_names(names, describe, default_all=every)


def record(workspace, client, alias, sobject, rid, fields=(), every=False, *, env=None, resolve=None,
           run=None) -> dict:
    ctx = open_lane(workspace, client, alias, "record", env=env, resolve=resolve, registry=True)
    rid = core.record_id(rid)
    describe = _describe(run, alias, sobject)
    if rid not in registered_ids(ctx.registry, ctx.org_id, describe.name):
        raise Error(f"that id is not a registered test record of {describe.name} in this org. The consultant "
                    "registers one with `torque guarded test-records add`")
    facts = _facts(run, alias, describe, ctx.policy)
    infos = _all_or_named(describe, fields, every)
    found = _rows(run, alias, infos, lambda group: core.build_record_query(describe, group, rid), "record")
    if len(found) != 1 or _row_id(found[0]) != rid:
        # Only the row that was asked for is shown, whatever the answer holds.
        raise Error("the registered record was not found in the org")
    row = _test_rows(ctx, describe, facts, infos, found)[0]
    _record(ctx, "record", {"object": describe.name, "id": rid,
                            "fields": {name: _how(value) for name, value in row.items()}})
    return {"lane": "record", "org": alias, "object": describe.name, "id": rid, "fields": row}


def related(workspace, client, alias, parent, child, fields=(), every=False, limit=core.CHILD_LIMIT_DEFAULT, *,
            env=None, resolve=None, run=None) -> dict:
    ctx = open_lane(workspace, client, alias, "related", env=env, resolve=resolve, registry=True)
    parent = core.record_id(parent)
    if parent not in registered_ids(ctx.registry, ctx.org_id):
        raise Error("that parent id is not a registered test record in this org")
    if type(limit) is not int or not 1 <= limit <= core.CHILD_LIMIT_MAX:
        raise Error(f"the limit is a whole number from 1 to {core.CHILD_LIMIT_MAX}")
    if not isinstance(child, str) or child.count(".") != 1:
        raise Error("name the children as Object.LookupField, for example Opportunity.AccountId")
    describe = _describe(run, alias, child.split(".")[0])
    lookup = core.parse_child(child, describe)
    facts = _facts(run, alias, describe, ctx.policy)
    infos = _all_or_named(describe, fields, every)
    ids = []
    for found in _query(run, alias, core.build_child_ids_query(describe, lookup, parent), "children"):
        try:
            ids.append(core.record_id(found.get("Id") if isinstance(found, dict) else None))
        except Error:
            raise Error("the Salesforce CLI did not answer the children; nothing is shown") from None
    mine = registered_ids(ctx.registry, ctx.org_id, describe.name)
    shown = [i for i in ids if i in mine][:limit]
    answered = _rows(run, alias, infos, lambda group: core.build_children_query(describe, group, shown),
                     "children") if shown else []
    if any(_row_id(row) not in shown for row in answered):
        raise Error("the Salesforce CLI did not answer the children; nothing is shown")
    rows = _test_rows(ctx, describe, facts, infos, answered)
    more = len(ids) - len(shown)
    printed = {i.name: "shown" if any(_how(row[i.name]) == "shown" for row in rows) else "masked" for i in infos}
    _record(ctx, "related", {"object": describe.name, "parent": parent, "lookup": lookup.name,
                             "fields": printed if rows else {}, "rows_shown": len(rows)})
    return {"lane": "related", "org": alias, "object": describe.name, "parent": parent, "rows": rows,
            "not_shown": f"more than {core.MAX_GROUPS}" if len(ids) > core.MAX_GROUPS else more}


def org_info(workspace, client, alias, *, env=None, resolve=None) -> dict:
    """The org an alias resolves to, so a session can check it without a record read."""
    ctx = open_lane(workspace, client, alias, "org", env=env, resolve=resolve)
    _record(ctx, "org", {})
    return {"org": alias, "org_id_18": ctx.org_id, "kind": ctx.org_kind}


def candidates(workspace, client, alias, sobject, *, env=None, resolve=None, run=None) -> dict:
    """Each field of an object: how it could be released, or why not. A metadata read."""
    ctx = open_lane(workspace, client, alias, "candidates", env=env, resolve=resolve)
    describe = _describe(run, alias, sobject)
    facts = _facts(run, alias, describe, ctx.policy)
    rows = []
    for info in describe.fields.values():
        reason = core.blocked_reason(describe, info, facts)
        need = "" if reason else core.acknowledgement_reason(describe, info)
        can = "no" if reason else ("exact or coarse" if info.type in core.COARSE_TYPES else "exact")
        rows.append({"field": info.name, "type": info.type, "release": can, "released": ctx.policy.level(describe, info),
                     "note": reason or (f"needs --acknowledge-sensitive: {need}" if need else "")})
    _record(ctx, "candidates", {"object": describe.name, "field_names_listed": len(rows)})
    return {"org": alias, "object": describe.name, "custom_setting": describe.custom_setting,
            "holds_people": core.holds_people(describe), "fields": rows}


# ---------------------------------------------------------------- the consultant's commands

def _operator(presence, confirm, what: str, statement: str = "", out=None) -> None:
    """A person at a real terminal, who types back a code after reading `statement`."""
    from . import presence as presence_module

    def ask() -> bool:
        if statement:
            (out or sys.stdout).write(statement + "\n")
        return (confirm or presence_module.confirm_code)()
    presence_module.require_presence(what, presence, ask if (confirm is not None or presence is None) else None,
                                     error=Error)


def _at_terminal(presence, what: str) -> None:
    """A person at a real terminal (no code yet: the code comes with the statement)."""
    if presence is None:
        from .presence import operator_present as presence
    check = presence()
    if not check.ok:
        raise Error(f"{what} at a real terminal: {check.reason}")


def _admin(workspace, client) -> tuple[Path, dict, str, Path]:
    root, config, slug, folder = _folder(workspace, client)
    try:
        ws.require_writable(root)
    except ws.WorkspaceError as exc:
        raise Error(str(exc)) from None
    return root, config, slug, folder


def _save_policy(folder: Path, payload: dict) -> core.Policy:
    policy = core.parse_policy(payload)
    _approvals(folder, POLICY_FILE, create=True)
    _write(_approvals(folder, POLICY_FILE), payload)
    return policy


def _drop(mapping: dict, key: str) -> bool:
    found = [k for k in mapping if k.casefold() == key.casefold()]
    for k in found:
        del mapping[k]
    return bool(found)


def policy_release(workspace, client, alias, target, level, *, sensitive=False, presence=None, confirm=None,
                   resolve=None, run=None, env=None) -> dict:
    """Release one field: its values may be shown for this client, exact or coarse."""
    _, _, _, folder = _admin(workspace, client)
    _operator(presence, confirm, "a field is released by the consultant")
    ctx = open_lane(workspace, client, alias, "candidates", env=env, resolve=resolve)
    if not isinstance(target, str) or target.count(".") != 1:
        raise Error("name the field as Object.Field")
    describe = _describe(run, alias, target.split(".")[0])
    info = describe.field(target.split(".")[1])
    core.check_release(describe, info, level, _facts(run, alias, describe, ctx.policy), acknowledged=sensitive)
    payload = policy_payload(folder)
    key = f"{describe.name}.{info.name}"
    _drop(payload.setdefault("fields", {}), key)
    payload["fields"][key] = level
    _save_policy(folder, payload)
    return {"released": key, "as": level}


def policy_release_object(workspace, client, alias, sobject, fields=(), *, records=False, sensitive=False,
                          presence=None, confirm=None, resolve=None, run=None, env=None) -> dict:
    """Release an object as configuration, with the fields whose values show."""
    _, _, _, folder = _admin(workspace, client)
    _operator(presence, confirm, "an object is released by the consultant")
    ctx = open_lane(workspace, client, alias, "candidates", env=env, resolve=resolve)
    describe = _describe(run, alias, sobject)
    names = core.check_object_release(describe, fields, _facts(run, alias, describe, ctx.policy), records=records,
                                      sensitive=sensitive)
    payload = policy_payload(folder)
    _drop(payload.setdefault("objects", {}), describe.name)
    payload["objects"][describe.name] = names
    _save_policy(folder, payload)
    return {"released_object": describe.name, "fields": names}


def policy_change(workspace, client, change, *, presence=None, confirm=None) -> dict:
    """A policy change that needs no org: `change(payload)` edits the stored policy."""
    _, _, _, folder = _admin(workspace, client)
    _operator(presence, confirm, "the guarded policy is changed by the consultant")
    payload = policy_payload(folder)
    result = change(payload)
    _save_policy(folder, payload)
    return result


def policy_unrelease(workspace, client, target, **kw) -> dict:
    def change(payload):
        if not _drop(payload.setdefault("fields", {}), str(target)):
            raise Error("that field is not released")
        return {"unreleased": str(target)}
    return policy_change(workspace, client, change, **kw)


def policy_unrelease_object(workspace, client, sobject, **kw) -> dict:
    def change(payload):
        if not _drop(payload.setdefault("objects", {}), str(sobject)):
            raise Error("that object is not released")
        return {"unreleased_object": str(sobject)}
    return policy_change(workspace, client, change, **kw)


def policy_set(workspace, client, key, value, **kw) -> dict:
    def change(payload):
        payload[key] = value
        return {key: value}
    return policy_change(workspace, client, change, **kw)


def policy_show(workspace, client) -> dict:
    return policy_payload(_folder(workspace, client)[3])


def _exists(run, alias: str, describe: core.Describe, rid: str) -> None:
    if not describe.queryable:
        raise Error(f"{describe.name} cannot be queried")
    soql = f"SELECT Id FROM {describe.name} WHERE Id = {core.soql_string(rid)} LIMIT 1"
    found = _query(run, alias, soql, "record")
    if len(found) != 1 or _row_id(found[0]) != rid:
        raise Error("no record with that id was found in the org")


def test_records_add(workspace, client, alias, sobject, rid, with_children=(), note="", *, presence=None,
                     confirm=None, resolve=None, run=None, env=None, out=None) -> dict:
    """Register one record, and with `with_children` each of its present children, as
    test records. Nothing is registered by relationship: a child added later is not."""
    out = out or sys.stdout
    _, _, _, folder = _admin(workspace, client)
    _at_terminal(presence, "a test record is registered by the consultant")       # before any org call
    ctx = open_lane(workspace, client, alias, "record", env=env, resolve=resolve, registry=True)
    rid = core.record_id(rid)
    describe = _describe(run, alias, sobject)
    statement = MADE_UP + (f" {describe.name} is an object that holds people's records."
                           if core.holds_people(describe) else "")
    _operator(presence, confirm, "a test record is registered by the consultant", statement, out)
    _exists(run, alias, describe, rid)
    new = [(describe.name, rid)]
    for text in with_children or ():
        if not isinstance(text, str) or text.count(".") != 1:
            raise Error("name the children as Object.LookupField, for example Opportunity.AccountId")
        child = _describe(run, alias, text.split(".")[0])
        lookup = core.parse_child(text, child)
        found = _query(run, alias, core.build_child_ids_query(child, lookup, rid), "children")
        if len(found) > core.MAX_GROUPS:
            raise Error(f"more than {core.MAX_GROUPS} children; register them one by one")
        try:
            ids = [core.record_id(item.get("Id") if isinstance(item, dict) else None) for item in found]
        except Error:       # the id came from the org: its text is not repeated
            raise Error("the Salesforce CLI did not answer the children; nothing was registered") from None
        _operator(presence, confirm, "children are registered by the consultant",
                  f"{len(ids)} {child.name} records point at this record now. Each becomes a test record: "
                  "they hold made-up data too.", out)
        new += [(child.name, i) for i in ids]
    payload = _read(_approvals(folder, REGISTRY_FILE)) or {"schema": REGISTRY_SCHEMA, "records": []}
    have = registered_ids(ctx.registry, ctx.org_id)
    who = consent._user()
    added = []
    for name, one in new:
        if one not in have:
            have.add(one)
            added.append(one)
            payload["records"].append({"alias": alias, "org_id_18": ctx.org_id, "object": name, "id": one,
                                       "note": str(note or "")[:200], "by": who, "at": ws._now()})
    if len(payload["records"]) > MAX_REGISTERED:
        raise Error(f"at most {MAX_REGISTERED} test records per client")
    _approvals(folder, REGISTRY_FILE, create=True)
    _write(_approvals(folder, REGISTRY_FILE), payload)
    return {"registered": added, "already": len(new) - len(added)}


def test_records_remove(workspace, client, rid, *, presence=None, confirm=None) -> dict:
    _, _, _, folder = _admin(workspace, client)
    _operator(presence, confirm, "a test record is removed by the consultant")
    rid = core.record_id(rid)
    records = load_registry(folder)
    kept = [e for e in records if e["id"] != rid]
    if len(kept) == len(records):
        raise Error("that id is not registered")
    _write(_approvals(folder, REGISTRY_FILE), {"schema": REGISTRY_SCHEMA, "records": kept})
    return {"removed": rid}


def test_records_list(workspace, client) -> list[dict]:
    return load_registry(_folder(workspace, client)[3])


def exposure(workspace, client, since: str | None = None) -> dict:
    """What the lanes have shown for this client, from the records of their calls."""
    folder = _approvals(_folder(workspace, client)[3], ACTIVITY_DIR)
    calls: dict = {}
    fields: dict = {}
    unreadable = 0
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            lane, name, at = entry["lane"], entry.get("object") or "", str(entry["at"])
        except (OSError, ValueError, KeyError, TypeError):
            unreadable += 1
            continue
        if since and at[:10] < since:
            continue
        key = f"{lane} {name}".strip()
        calls[key] = calls.get(key, 0) + 1
        if isinstance(entry.get("fields"), dict):
            shown = [n for n, how in entry["fields"].items() if how == "shown"]
        else:
            shown = entry.get("fields_shown") or entry.get("dimensions") or entry.get("fields") or []
        for one in shown if isinstance(shown, list) else []:
            fields.setdefault(name, set()).add(str(one))
    return {"calls": calls, "fields_shown": {name: sorted(names) for name, names in fields.items()},
            "unreadable_records": unreadable}


# ---------------------------------------------------------------- command line

def _lines(result) -> list[str]:
    """The text of a result. Every value in it came through guarded_core."""
    lane = result.get("lane")
    if lane == "counts":
        floor = f"groups smaller than {result['min_cell']} are not shown"
        if not result["columns"]:
            return [f"{result['object']}: {result['rows'][0]['count']} records" if result["rows"] else
                    f"{result['object']}: not shown", f"({floor})"]
        lines = ["\t".join([*result["columns"], "count"])]
        lines += ["\t".join([*map(str, row["values"]), str(row["count"])]) for row in result["rows"]]
        return [*lines, f"({floor}; date buckets of a date/time field are in UTC)"]
    if lane == "fill":
        floor = f"(counts where the filled or the blank part is smaller than {result['min_cell']} are not shown)"
        if result["total"] is None:
            return [f"{result['object']}: not shown", floor]
        lines = [f"{result['object']}: {result['total']} records"]
        lines += [f"{row['field']}\t" + ("not shown" if row["filled"] is None else f"{row['filled']} filled")
                  for row in result["rows"]]
        return [*lines, floor]
    if lane in ("config", "related"):
        lines = [json.dumps(row, ensure_ascii=False, sort_keys=False) for row in result["rows"]]
        if lane == "related" and result["not_shown"]:
            lines.append(f"({result['not_shown']} more children are not shown: not registered, or past the limit)")
        return lines or ["(no rows)"]
    if lane == "record":
        return [f"{name}\t{json.dumps(value, ensure_ascii=False)}" for name, value in result["fields"].items()]
    return [json.dumps(result, indent=2, ensure_ascii=False)]


def register(subparsers) -> None:
    """The `torque guarded` commands. The org is named with --target-org only, and no
    command takes a positional argument, so the gate and the parser read the same words."""
    guarded = subparsers.add_parser("guarded", help="guarded reads: counts, configuration rows and test records "
                                                    "for an org without record consent (off by default)")
    actions = guarded.add_subparsers(dest="action", required=True)

    def lane(name, text, org=True):
        parser = actions.add_parser(name, help=text)
        parser.add_argument("--workspace", required=True)
        parser.add_argument("--client", required=True)
        if org:
            parser.add_argument("--target-org", required=True, dest="target_org")
        parser.add_argument("--json", action="store_true")
        return parser

    p = lane("counts", "how many records, by released fields")
    p.add_argument("--object", required=True, dest="sobject")
    p.add_argument("--group-by", action="append", default=[], dest="group_by")
    p.add_argument("--where", action="append", default=[])
    p = lane("fill", "how many records have each field filled in")
    p.add_argument("--object", required=True, dest="sobject")
    p.add_argument("--fields", required=True)
    p.add_argument("--where", action="append", default=[])
    p = lane("config", "rows of an object released as configuration")
    p.add_argument("--object", required=True, dest="sobject")
    p.add_argument("--fields")
    p.add_argument("--limit", type=int, default=core.CONFIG_LIMIT_DEFAULT)
    p = lane("record", "one registered test record")
    p.add_argument("--object", required=True, dest="sobject")
    p.add_argument("--id", required=True, dest="rid")
    p.add_argument("--fields")
    p.add_argument("--all", action="store_true", dest="every")
    p = lane("related", "the registered children of a registered test record")
    p.add_argument("--id", required=True, dest="rid")
    p.add_argument("--child", required=True)
    p.add_argument("--fields")
    p.add_argument("--all", action="store_true", dest="every")
    p.add_argument("--limit", type=int, default=core.CHILD_LIMIT_DEFAULT)
    lane("org", "the org an alias resolves to")
    p = lane("exposure", "what the lanes have shown for this client", org=False)
    p.add_argument("--since", help="YYYY-MM-DD")

    policy = actions.add_parser("policy", help="consultant only, except show and candidates: what may be shown")
    verbs = policy.add_subparsers(dest="verb", required=True)

    def verb(name, text, org=False):
        parser = verbs.add_parser(name, help=text)
        parser.add_argument("--workspace", required=True)
        parser.add_argument("--client", required=True)
        if org:
            parser.add_argument("--target-org", required=True, dest="target_org")
        parser.add_argument("--json", action="store_true")
        return parser

    p = verb("release", "release a field's values", org=True)
    p.add_argument("--field", required=True, help="Object.Field")
    p.add_argument("--as", required=True, dest="level", choices=core.LEVELS)
    p.add_argument("--acknowledge-sensitive", action="store_true", dest="sensitive")
    p = verb("unrelease", "take a field's release back")
    p.add_argument("--field", required=True, help="Object.Field")
    p = verb("release-object", "release an object as configuration", org=True)
    p.add_argument("--object", required=True, dest="sobject")
    p.add_argument("--fields", help="the fields whose values show; all of them for a custom setting")
    p.add_argument("--acknowledge-records", action="store_true", dest="records")
    p.add_argument("--acknowledge-sensitive", action="store_true", dest="sensitive")
    p = verb("unrelease-object", "take an object's release back")
    p.add_argument("--object", required=True, dest="sobject")
    p = verb("set-min-cell", "the smallest group a count shows")
    p.add_argument("--value", required=True, type=int)
    p = verb("set-classification", "whether the org's field classification must be readable")
    p.add_argument("--value", required=True, choices=("required", "ignore"))
    verb("show", "the policy")
    p = verb("candidates", "each field of an object and how it could be released", org=True)
    p.add_argument("--object", required=True, dest="sobject")

    tests = actions.add_parser("test-records", help="consultant only, except list: the registered test records")
    verbs = tests.add_subparsers(dest="verb", required=True)
    p = verb("add", "register a record that holds made-up data", org=True)
    p.add_argument("--object", required=True, dest="sobject")
    p.add_argument("--id", required=True, dest="rid")
    p.add_argument("--with-children", action="append", default=[], dest="with_children", help="Object.LookupField")
    p.add_argument("--note", default="")
    p = verb("remove", "remove a registered record")
    p.add_argument("--id", required=True, dest="rid")
    verb("list", "the registered records")


def _names(text) -> list[str]:
    return [part.strip() for part in str(text or "").split(",") if part.strip()]


def _dispatch(a) -> object:
    w, c = a.workspace, a.client
    if a.action == "counts":
        return counts(w, c, a.target_org, a.sobject, a.group_by, a.where)
    if a.action == "fill":
        return fill(w, c, a.target_org, a.sobject, _names(a.fields), a.where)
    if a.action == "config":
        return config(w, c, a.target_org, a.sobject, _names(a.fields), a.limit)
    if a.action == "record":
        return record(w, c, a.target_org, a.sobject, a.rid, _names(a.fields), a.every)
    if a.action == "related":
        return related(w, c, a.target_org, a.rid, a.child, _names(a.fields), a.every, a.limit)
    if a.action == "org":
        return org_info(w, c, a.target_org)
    if a.action == "exposure":
        return exposure(w, c, a.since)
    if a.action == "policy":
        if a.verb == "release":
            return policy_release(w, c, a.target_org, a.field, a.level, sensitive=a.sensitive)
        if a.verb == "unrelease":
            return policy_unrelease(w, c, a.field)
        if a.verb == "release-object":
            return policy_release_object(w, c, a.target_org, a.sobject, _names(a.fields), records=a.records,
                                         sensitive=a.sensitive)
        if a.verb == "unrelease-object":
            return policy_unrelease_object(w, c, a.sobject)
        if a.verb == "set-min-cell":
            return policy_set(w, c, "counts_min_cell", a.value)
        if a.verb == "set-classification":
            return policy_set(w, c, "classification", a.value)
        if a.verb == "show":
            return policy_show(w, c)
        return candidates(w, c, a.target_org, a.sobject)
    if a.verb == "add":
        return test_records_add(w, c, a.target_org, a.sobject, a.rid, a.with_children, a.note)
    if a.verb == "remove":
        return test_records_remove(w, c, a.rid)
    return {"records": test_records_list(w, c)}


def run(parsed, out=None, err=None) -> int:
    """Run one `torque guarded` command. Nothing leaves this function but text it
    built: a refusal prints its one line, and any other failure prints the name of
    its exception class without its text, which could hold a value from the org."""
    out, err = out or sys.stdout, err or sys.stderr
    try:
        result = _dispatch(parsed)
        text = (json.dumps(result, indent=2, ensure_ascii=False) if getattr(parsed, "json", False)
                or not isinstance(result, dict) else "\n".join(_lines(result)))
    except Error as exc:
        err.write("torque guarded: " + " ".join(str(exc).split())[:600] + "\n")     # always one line
        return 2
    except Exception as exc:  # noqa: BLE001 - the text of an unexpected error is never printed
        err.write(f"torque guarded: guarded read failed ({type(exc).__name__})\n")
        return 2
    out.write(text + "\n")
    return 0
