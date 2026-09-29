# Engagements a18 (core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add internal initiatives beside clients as first-class engagements, with the protections the converged design requires for the first release, without changing any existing client behavior.

**Architecture:** A new focused module `src/torque/engagements.py` owns kinds, the capability table and initiatives (binding.json + state/engagement.json). Existing client code gains a `kind` keyword where records are generic (sessions, context, handoff, change records) and stays untouched where it is client-only (consent, approvals, launch, delegated routes). Write paths gain directory fsync, mode-preserving replace and a maintenance flag; the gate protects initiative bindings and future client control folders.

**Tech Stack:** Python 3.10+, argparse, pytest; no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-29-engagements-design.md` (release plan item a18).

## Global Constraints

- No default global CLI interception or approval tokens; build-only and opt-in connected mode behave exactly as before for clients.
- Existing client CLI and JSON contracts are unchanged (`--client` everywhere it works today; record keys `client`).
- v1 workspaces keep working with no migration; nothing in a18 rewrites an existing client folder.
- Initiatives can never use client-only capabilities: org, consent, approvals, connected mode, verify-deploy.
- Public repository: no firm, client or person names in code, tests or docs.
- Clean and simple: one new module; thin, targeted edits elsewhere; no new dependencies; no feature removed.
- Laptop layout: private modes (folders 0700, files 0600), as clients today. Shared-host permissions are a20 provisioning.
- Full suite stays green: `.venv-continuation/bin/python -m pytest -q` (3,002 tests collected at a17).

Rulings made while planning (spec is binding; these narrow it for a18):
- Commands: `torque initiative add|list|show|set-state` plus one cross-kind `torque engagement list`. A separate `engagement add` is not needed while `client add` and `initiative add` exist. Clients get lifecycle states in the a20 migration, so `set-state` is initiative-only in a18.
- Tolerant readers move to a19 with the board that consumes them (YAGNI for a18).
- Worktree copies: guarded folders that do not resolve under the workspace path keep the old `clients/<name>` mapping; initiatives in worktree copies are covered once a19 resolves real paths.

---

### Task 1: Durable publication (directory fsync, mode-preserving replace)

**Files:**
- Modify: `src/torque/workspace.py` (`atomic_write_new` lines 69-88, `_atomic_replace_text` lines 138-148)
- Test: `tests/test_publication.py` (new)

**Interfaces:**
- Produces: `ws._fsync_dir(directory: Path) -> None`; `ws.atomic_write_new(path, text)` and `ws._atomic_replace_text(path, text)` keep their signatures. `_atomic_replace_text` now keeps an existing file's mode and group.

- [ ] **Step 1: Write the failing tests**

```python
"""Publication durability: directory fsync and mode-preserving replace."""
import os
import stat
from pathlib import Path

import pytest

from torque import workspace as ws


def test_create_only_syncs_the_directory(tmp_path, monkeypatch):
    synced = []
    monkeypatch.setattr(ws, "_fsync_dir", lambda d: synced.append(Path(d)))
    ws.atomic_write_new(tmp_path / "a.txt", "one\n")
    assert synced == [tmp_path]


def test_replace_syncs_the_directory(tmp_path, monkeypatch):
    target = tmp_path / "a.txt"
    target.write_text("one\n")
    synced = []
    monkeypatch.setattr(ws, "_fsync_dir", lambda d: synced.append(Path(d)))
    ws._atomic_replace_text(target, "two\n")
    assert target.read_text() == "two\n" and synced == [tmp_path]


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_replace_keeps_the_existing_mode(tmp_path):
    target = tmp_path / "shared.json"
    target.write_text("{}\n")
    target.chmod(0o660)
    ws._atomic_replace_text(target, '{"a": 1}\n')
    assert stat.S_IMODE(target.stat().st_mode) == 0o660


def test_create_only_still_refuses_an_existing_file(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("keep\n")
    with pytest.raises(ws.WorkspaceError, match="already exists"):
        ws.atomic_write_new(target, "replace\n")
    assert target.read_text() == "keep\n"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv-continuation/bin/python -m pytest tests/test_publication.py -v`
Expected: FAIL (`module 'torque.workspace' has no attribute '_fsync_dir'`; mode test fails with 0o600).

- [ ] **Step 3: Implement**

Add after `_read_json` in `workspace.py`:

```python
def _fsync_dir(directory: Path) -> None:
    """Make a rename or link in directory durable. Windows has no directory fsync."""
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
```

In `atomic_write_new`, after `os.link(temporary, path)` succeeds (inside the `try` that catches `FileExistsError`, after the link line) add `_fsync_dir(path.parent)`:

```python
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise WorkspaceError(f"file already exists; choose a new path: {path}") from exc
        _fsync_dir(path.parent)
```

Replace `_atomic_replace_text` with:

```python
def _atomic_replace_text(path: Path, text: str) -> None:
    """Replace an explicitly managed file without exposing a partial write. An
    existing file keeps its mode and group (a shared record stays shared)."""
    try:
        existing = os.stat(path)
    except FileNotFoundError:
        existing = None
    fd, temporary = tempfile.mkstemp(prefix=".torque-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if existing is not None and os.name != "nt":
            os.chmod(temporary, stat.S_IMODE(existing.st_mode))
            try:
                os.chown(temporary, -1, existing.st_gid)
            except PermissionError:
                pass
        os.replace(temporary, path)
        _fsync_dir(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)
```

- [ ] **Step 4: Run the tests and the workspace suites**

Run: `.venv-continuation/bin/python -m pytest tests/test_publication.py tests/test_workspace.py tests/test_template_updates.py tests/test_default_acl.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/torque/workspace.py tests/test_publication.py
git commit -m "feat(workspace): durable publication with directory fsync and mode-preserving replace"
```

---

### Task 2: Maintenance flag

**Files:**
- Modify: `src/torque/workspace.py` (`add_client`, `add_session`), `src/torque/changes.py` (`create_change`, `_append`)
- Test: `tests/test_maintenance.py` (new)

**Interfaces:**
- Produces: `ws.MAINTENANCE_FLAG = ".torque/maintenance"`; `ws.require_writable(root: Path) -> None` raises `WorkspaceError` while the flag exists. Every record writer calls it with the workspace root.

- [ ] **Step 1: Write the failing tests**

```python
"""A maintenance flag pauses every record write; reads keep working."""
import pytest

from torque import changes
from torque import workspace as ws


@pytest.fixture
def root(tmp_path):
    root = ws.init_workspace(tmp_path / "firm", "Synthetic firm")
    ws.add_client(root, "Alpha")
    return root


def pause(root):
    (root / ".torque").mkdir(exist_ok=True)
    (root / ws.MAINTENANCE_FLAG).write_text("migration\n")


def test_session_write_refused_during_maintenance(root):
    pause(root)
    with pytest.raises(ws.WorkspaceError, match="maintenance"):
        ws.add_session(root, "Alpha", "work", "prepared")
    assert ws.list_sessions(root, "Alpha") == []


def test_client_and_change_writes_refused_during_maintenance(root):
    item = changes.create_change(root, "Alpha", "Title", "Outcome")
    pause(root)
    with pytest.raises(ws.WorkspaceError, match="maintenance"):
        ws.add_client(root, "Beta")
    with pytest.raises(ws.WorkspaceError, match="maintenance"):
        changes.create_change(root, "Alpha", "Another", "Outcome")
    with pytest.raises(ws.WorkspaceError, match="maintenance"):
        changes.add_note(root, "Alpha", item["id"], "decided", "decision")


def test_writes_resume_when_the_flag_is_removed(root):
    pause(root)
    (root / ws.MAINTENANCE_FLAG).unlink()
    assert ws.add_session(root, "Alpha", "work", "prepared")["status"] == "prepared"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv-continuation/bin/python -m pytest tests/test_maintenance.py -v`
Expected: FAIL (`MAINTENANCE_FLAG` missing).

- [ ] **Step 3: Implement**

In `workspace.py` after `CONFIG = "workspace.json"`:

```python
MAINTENANCE_FLAG = ".torque/maintenance"
```

After `load_workspace`:

```python
def require_writable(root: Path) -> None:
    """Refuse record writes while an administrator holds the workspace in maintenance."""
    if (root / MAINTENANCE_FLAG).exists():
        raise WorkspaceError(f"workspace is in maintenance ({MAINTENANCE_FLAG} exists); "
                             "writes are paused until it is removed")
```

In `add_client`, after `root, _ = load_workspace(workspace)` add `require_writable(root)`.
In `add_session`, after the first line add `require_writable(client.parent.parent)`.
In `changes.create_change`, after `directory, config = _directory(workspace, client)` add `ws.require_writable(directory.parent.parent.parent)`.
In `changes._append`, as its first line add `ws.require_writable(_workspace_of(root))`.

- [ ] **Step 4: Run tests**

Run: `.venv-continuation/bin/python -m pytest tests/test_maintenance.py tests/test_workspace.py tests/test_changes.py tests/test_changes_approval.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/torque/workspace.py src/torque/changes.py tests/test_maintenance.py
git commit -m "feat(workspace): maintenance flag pauses record writes"
```

---

### Task 3: One engagement boundary helper

**Files:**
- Modify: `src/torque/workspace.py` (`add_session` evidence check line 563-565, `_session_evidence` line 620, `client_output_path` line 702), `src/torque/changes.py` (`_capture_file` lines 117-120)
- Test: `tests/test_engagement_boundary.py` (new)

**Interfaces:**
- Produces: `ws.ENGAGEMENT_FOLDERS = ("clients", "initiatives")`; `ws.foreign_engagement(own: Path, path: Path) -> bool`: True when `path` lies in another client or initiative of the same workspace (own is `<workspace>/<folder>/<slug>`).

- [ ] **Step 1: Write the failing tests**

```python
"""Evidence and exports never cross into another client or initiative."""
from pathlib import Path

from torque import workspace as ws


def test_foreign_engagement(tmp_path):
    root = tmp_path / "w"
    own = root / "clients" / "alpha"
    assert ws.foreign_engagement(own, root / "clients" / "beta" / "x.txt")
    assert ws.foreign_engagement(own, root / "initiatives" / "plan" / "x.txt")
    assert not ws.foreign_engagement(own, own / "artifacts" / "x.txt")
    assert not ws.foreign_engagement(own, root / "profile.md")
    assert not ws.foreign_engagement(own, tmp_path / "outside.txt")
    initiative = root / "initiatives" / "plan"
    assert ws.foreign_engagement(initiative, own / "x.txt")
    assert not ws.foreign_engagement(initiative, initiative / "context.md")


def test_client_session_evidence_from_an_initiative_is_refused(tmp_path):
    root = ws.init_workspace(tmp_path / "firm", "Synthetic firm")
    ws.add_client(root, "Alpha")
    other = root / "initiatives" / "plan"
    other.mkdir(parents=True)
    evidence = other / "notes.txt"
    evidence.write_text("internal\n")
    try:
        ws.add_session(root, "Alpha", "work", "prepared", evidence)
    except ws.WorkspaceError as exc:
        assert "different client" in str(exc)
    else:
        raise AssertionError("evidence from an initiative was accepted")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv-continuation/bin/python -m pytest tests/test_engagement_boundary.py -v`
Expected: FAIL (`foreign_engagement` missing; the second test records the session).

- [ ] **Step 3: Implement**

In `workspace.py` after `_inside`:

```python
ENGAGEMENT_FOLDERS = ("clients", "initiatives")


def foreign_engagement(own: Path, path: Path) -> bool:
    """path lies inside another client or initiative of own's workspace."""
    root = own.parent.parent
    if own == path or own in path.parents:
        return False
    return any((root / folder) in path.parents for folder in ENGAGEMENT_FOLDERS)
```

Replace the three checks (messages keep "different client" so existing tests hold):

```python
        if foreign_engagement(client, path):
            raise WorkspaceError("evidence belongs to a different client or initiative")
```

```python
        if foreign_engagement(client, resolved):
            raise WorkspaceError("session evidence belongs to a different client or initiative")
```

```python
    if foreign_engagement(client, resolved):
        raise WorkspaceError("handoff output belongs to a different client or initiative; choose this "
                             "engagement's directory or an explicit export outside clients/ and initiatives/")
```

In `changes._capture_file` replace the `client = root.parent.parent` ... `raise` block with:

```python
    # This change's engagement may use shared firm artifacts, but never another engagement.
    if ws.foreign_engagement(root.parent.parent, path):
        raise ws.WorkspaceError("evidence belongs to a different client or initiative")
```

- [ ] **Step 4: Run tests**

Run: `.venv-continuation/bin/python -m pytest tests/test_engagement_boundary.py tests/test_context_continuation.py tests/test_record_resilience.py tests/test_before_state.py tests/test_workspace.py tests/test_changes.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/torque/workspace.py src/torque/changes.py tests/test_engagement_boundary.py
git commit -m "feat(workspace): one boundary helper across clients and initiatives"
```

---

### Task 4: Initiatives and the capability table

**Files:**
- Create: `src/torque/engagements.py`
- Modify: `src/torque/workspace.py` (`init_workspace` private rules and AGENTS.md text)
- Test: `tests/test_engagements.py` (new)

**Interfaces:**
- Consumes: `ws.require_writable`, `ws._client_creation_lock`, `ws._write_json`, `ws.atomic_write_new`, `ws._atomic_replace_text`, `ws.slug_for`, `ws._inside`, `ws.load_workspace`, `ws.list_clients`.
- Produces:
  - `engagements.KINDS = ("client", "initiative")`, `FOLDERS = {"client": "clients", "initiative": "initiatives"}`, `LIFECYCLE = ("active", "paused", "closed", "archived")`, `CAPABILITIES: dict[str, frozenset[str]]`.
  - `require(kind: str, capability: str) -> None` (raises `ws.WorkspaceError`).
  - `add_initiative(workspace, name: str, owner: str | None = None) -> Path`
  - `load_initiative(workspace, name: str) -> tuple[Path, dict, dict]` returning (folder, workspace config, merged config with keys `kind`, `slug`, `name`, `id`, `owner`, `state`, `history`, `repositories`, `created_at`).
  - `list_engagements(workspace, kind: str | None = None) -> list[dict]` rows `{kind, slug, name, state, owner}`; clients report `state: "active"` and `owner: None` until the a20 migration.
  - `set_state(workspace, name: str, state: str, *, reason=None, review_date=None, outcome=None) -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
"""Initiatives: the same tracking as clients, never the client-only powers."""
import json

import pytest

from torque import engagements as eng
from torque import workspace as ws


@pytest.fixture
def root(tmp_path):
    root = ws.init_workspace(tmp_path / "firm", "Synthetic firm")
    ws.add_client(root, "Alpha")
    return root


def test_add_initiative_creates_binding_state_and_folders(root):
    folder = eng.add_initiative(root, "Secure Workspace", owner="lead")
    assert folder == root / "initiatives" / "secure-workspace"
    binding = json.loads((folder / "binding.json").read_text())
    assert binding["schema"] == "torque.binding/1" and binding["kind"] == "initiative"
    assert binding["slug"] == "secure-workspace" and binding["repositories"] == []
    state = json.loads((folder / "state" / "engagement.json").read_text())
    assert state["schema"] == "torque.engagement/1" and state["state"] == "active"
    assert state["name"] == "Secure Workspace" and state["owner"] == "lead"
    for name in ("sessions", "changes", "artifacts", "context", "config"):
        assert (folder / name).is_dir()
    assert (folder / "context.md").is_file()


def test_duplicate_initiative_is_refused(root):
    eng.add_initiative(root, "Plan")
    with pytest.raises(ws.WorkspaceError, match="already exists"):
        eng.add_initiative(root, "Plan")


def test_list_engagements_covers_both_kinds(root):
    eng.add_initiative(root, "Plan")
    rows = eng.list_engagements(root)
    assert {(r["kind"], r["slug"]) for r in rows} == {("client", "alpha"), ("initiative", "plan")}
    assert [r["slug"] for r in eng.list_engagements(root, "initiative")] == ["plan"]


def test_set_state_records_history_and_rules(root):
    eng.add_initiative(root, "Plan")
    with pytest.raises(ws.WorkspaceError, match="reason"):
        eng.set_state(root, "Plan", "paused")
    config = eng.set_state(root, "Plan", "paused", reason="waiting", review_date="2026-11-01")
    assert config["state"] == "paused" and config["history"][-1]["reason"] == "waiting"
    with pytest.raises(ws.WorkspaceError, match="outcome"):
        eng.set_state(root, "Plan", "closed")
    assert eng.set_state(root, "Plan", "closed", outcome="done")["state"] == "closed"


def test_capabilities(root):
    eng.require("client", "consent")
    eng.require("initiative", "sessions")
    for capability in ("org", "consent", "approvals", "connected", "verify_deploy"):
        with pytest.raises(ws.WorkspaceError, match="clients only"):
            eng.require("initiative", capability)


def test_private_rule_and_workspace_notes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text("node_modules/\n")
    root = ws.init_workspace(repo, "Synthetic firm")
    assert "/initiatives/" in (root / ".gitignore").read_text().splitlines()
    assert "initiatives/SLUG" in (root / "AGENTS.md").read_text()


def test_existing_workspace_gains_the_private_rule_on_first_initiative(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text("/clients/\n/workspace.json\n/profile.md\n/.torque/\n")
    root = ws.init_workspace(repo, "Synthetic firm")
    eng.add_initiative(root, "Plan")
    assert "/initiatives/" in (root / ".gitignore").read_text().splitlines()


def test_initiative_add_respects_maintenance(root):
    (root / ws.MAINTENANCE_FLAG).write_text("x\n")
    with pytest.raises(ws.WorkspaceError, match="maintenance"):
        eng.add_initiative(root, "Plan")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv-continuation/bin/python -m pytest tests/test_engagements.py -v`
Expected: FAIL (`No module named 'torque.engagements'`).

- [ ] **Step 3: Implement `src/torque/engagements.py`**

```python
"""Engagements: a client or an internal initiative, tracked the same way.

Clients keep their existing folder and records. Initiatives live in
initiatives/<slug>/ with a binding.json (identity, kind, repositories) and a
state/engagement.json (title, owner, lifecycle). Client-only powers (org,
consent, approvals, connected mode, verify-deploy) are refused for initiatives.
"""
from __future__ import annotations

import getpass
import json
import tempfile
from pathlib import Path
from uuid import uuid4

from . import workspace as ws

KINDS = ("client", "initiative")
FOLDERS = {"client": "clients", "initiative": "initiatives"}
LIFECYCLE = ("active", "paused", "closed", "archived")
_SHARED = frozenset({"sessions", "changes", "context", "handoff"})
CAPABILITIES = {
    "client": _SHARED | {"org", "consent", "approvals", "connected", "verify_deploy"},
    "initiative": _SHARED,
}
PRIVATE_RULE = "/initiatives/"


def require(kind: str, capability: str) -> None:
    if kind not in CAPABILITIES:
        raise ws.WorkspaceError(f"unknown engagement kind: {kind}")
    if capability not in CAPABILITIES[kind]:
        raise ws.WorkspaceError(f"{capability.replace('_', ' ')} is for clients only; an {kind} cannot use it")


def _actor() -> str:
    try:
        return getpass.getuser()
    except Exception:  # No login name available (containers); record that honestly.
        return "unknown"


def ensure_private_rule(root: Path) -> None:
    """Keep initiatives out of git in a workspace whose .gitignore lists private paths."""
    ignore = ws._inside(root, root / ".gitignore")
    if not ignore.exists():
        return
    text = ignore.read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines()]
    if "*" in lines or PRIVATE_RULE in lines:
        return
    ws._atomic_replace_text(ignore, text.rstrip("\n") + "\n" + PRIVATE_RULE + "\n")


def add_initiative(workspace: str | Path, name: str, owner: str | None = None) -> Path:
    root, _ = ws.load_workspace(workspace)
    ws.require_writable(root)
    slug = ws.slug_for(name)
    if owner is not None and (not owner.strip() or any(c in owner for c in "\r\n\0")):
        raise ws.WorkspaceError("owner must be nonempty and on one line")
    ensure_private_rule(root)
    with ws._client_creation_lock(root):
        parent = ws._inside(root, root / "initiatives")
        parent.mkdir(mode=0o700, exist_ok=True)
        folder = ws._inside(root, parent / slug)
        if folder.exists():
            raise ws.WorkspaceError(f"initiative slug already exists: {slug}; no existing data was replaced")
        staging = ws._inside(root, root / ".torque" / "client-staging")
        staging.mkdir(mode=0o700, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=slug + "-", dir=staging) as temporary:
            pending = Path(temporary)
            for directory in ("sessions", "changes", "artifacts", "context", "config", "state"):
                (pending / directory).mkdir(mode=0o700)
            now = ws._now()
            ws._write_json(pending / "binding.json", {"schema": "torque.binding/1", "id": str(uuid4()),
                                                      "kind": "initiative", "slug": slug, "repositories": []})
            ws._write_json(pending / "state" / "engagement.json", {
                "schema": "torque.engagement/1", "name": name.strip(), "owner": owner.strip() if owner else None,
                "state": "active", "created_at": now,
                "history": [{"state": "active", "at": now, "by": _actor()}]})
            ws.atomic_write_new(pending / "context.md", f"# {name.strip()}\n\n"
                                "Record this initiative's goal, scope, decisions, owners and open questions "
                                "here. Keep credentials out of these notes.\n")
            if folder.exists():
                raise ws.WorkspaceError(f"initiative slug already exists: {slug}; no existing data was replaced")
            pending.rename(folder)
    return folder


def load_initiative(workspace: str | Path, name: str) -> tuple[Path, dict, dict]:
    root, firm = ws.load_workspace(workspace)
    slug = ws.slug_for(name)
    folder = ws._inside(root, root / "initiatives" / slug)
    binding = ws._read_json(ws._inside(root, folder / "binding.json"))
    state = ws._read_json(ws._inside(root, folder / "state" / "engagement.json"))
    if (binding.get("schema") != "torque.binding/1" or binding.get("kind") != "initiative"
            or binding.get("slug") != slug or not isinstance(binding.get("repositories"), list)):
        raise ws.WorkspaceError(f"invalid initiative binding: {folder / 'binding.json'}")
    if (state.get("schema") != "torque.engagement/1" or state.get("state") not in LIFECYCLE
            or not isinstance(state.get("name"), str) or not state["name"].strip()
            or not isinstance(state.get("history"), list)):
        raise ws.WorkspaceError(f"invalid initiative state: {folder / 'state' / 'engagement.json'}")
    config = {"kind": "initiative", "slug": slug, "name": state["name"], "id": binding.get("id"),
              "owner": state.get("owner"), "state": state["state"], "history": state["history"],
              "repositories": binding["repositories"], "created_at": state.get("created_at")}
    return folder, firm, config


def list_engagements(workspace: str | Path, kind: str | None = None) -> list[dict]:
    if kind is not None and kind not in KINDS:
        raise ws.WorkspaceError(f"unknown engagement kind: {kind}")
    root, _ = ws.load_workspace(workspace)
    rows = []
    if kind in (None, "client"):
        rows += [{"kind": "client", "slug": c["slug"], "name": c["name"], "state": "active", "owner": None}
                 for c in ws.list_clients(root)]
    if kind in (None, "initiative"):
        parent = ws._inside(root, root / "initiatives")
        if parent.is_dir():
            for child in sorted(parent.iterdir()):
                ws._inside(root, child)
                if child.is_dir() and (child / "binding.json").exists():
                    config = load_initiative(root, child.name)[2]
                    rows.append({"kind": "initiative", "slug": config["slug"], "name": config["name"],
                                 "state": config["state"], "owner": config["owner"]})
    return rows


def set_state(workspace: str | Path, name: str, state: str, *, reason: str | None = None,
              review_date: str | None = None, outcome: str | None = None) -> dict:
    if state not in LIFECYCLE:
        raise ws.WorkspaceError(f"unknown lifecycle state: {state}")
    if state == "paused" and not (reason and reason.strip()):
        raise ws.WorkspaceError("pausing needs a reason")
    if state == "closed" and not (outcome and outcome.strip()):
        raise ws.WorkspaceError("closing needs an outcome")
    folder, _, config = load_initiative(workspace, name)
    ws.require_writable(folder.parent.parent)
    path = folder / "state" / "engagement.json"
    record = ws._read_json(path)
    entry = {"state": state, "at": ws._now(), "by": _actor()}
    for key, value in (("reason", reason), ("review_date", review_date), ("outcome", outcome)):
        if value:
            entry[key] = value.strip()
    record["state"] = state
    record["history"] = [*record["history"], entry]
    ws._atomic_replace_text(path, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return load_initiative(workspace, name)[2]
```

In `workspace.init_workspace`:
- change `private_rules = ["/clients/", "/workspace.json", "/profile.md", "/.torque/"]` to `private_rules = ["/clients/", "/initiatives/", "/workspace.json", "/profile.md", "/.torque/"]`;
- in `agent_notes`, after the sentence ending `stateful native workflows.` insert: `"Keep internal work (not for a client) in initiatives/SLUG/, created with `torque initiative add NAME --workspace .`, and select it with --initiative SLUG. "`.

- [ ] **Step 4: Run tests**

Run: `.venv-continuation/bin/python -m pytest tests/test_engagements.py tests/test_workspace.py tests/test_template_updates.py -q`
Expected: PASS. If a template test compares the generated AGENTS.md text or the appended ignore block exactly, update that expectation to the new text (the change is intended).

- [ ] **Step 5: Commit**

```bash
git add src/torque/engagements.py src/torque/workspace.py tests/test_engagements.py tests/
git commit -m "feat(engagements): initiatives with binding, lifecycle state and a capability table"
```

---

### Task 5: Sessions, context and handoff for either kind

**Files:**
- Modify: `src/torque/workspace.py` (`add_session`, `list_sessions`, `client_output_path`, `_change_context`, `get_context`, `render_handoff`; new `load_engagement`)
- Test: `tests/test_engagement_records.py` (new)

**Interfaces:**
- Consumes: `engagements.load_initiative`, `engagements.require`.
- Produces: `ws.load_engagement(workspace, name: str, kind: str = "client") -> tuple[Path, dict, dict]` (config always has `slug` and `name`); these gain `kind: str = "client"` as a keyword: `add_session`, `list_sessions`, `client_output_path`, `get_context`, `render_handoff`. Session records for an initiative carry `"initiative": slug` instead of `"client": slug`; archived initiatives refuse new sessions.

- [ ] **Step 1: Write the failing tests**

```python
"""Initiatives record sessions, context and handoffs exactly like clients."""
import json

import pytest

from torque import engagements as eng
from torque import workspace as ws


@pytest.fixture
def root(tmp_path):
    root = ws.init_workspace(tmp_path / "firm", "Synthetic firm")
    ws.add_client(root, "Alpha")
    eng.add_initiative(root, "Plan")
    return root


def test_initiative_sessions_are_kept_apart_from_clients(root):
    entry = ws.add_session(root, "Plan", "drafted the plan", "prepared", kind="initiative")
    assert entry["initiative"] == "plan" and "client" not in entry
    assert [e["id"] for e in ws.list_sessions(root, "Plan", kind="initiative")] == [entry["id"]]
    assert ws.list_sessions(root, "Alpha") == []


def test_context_and_handoff_for_an_initiative(root):
    ws.add_session(root, "Plan", "drafted the plan", "prepared", kind="initiative")
    context = ws.get_context(root, "Plan", kind="initiative")
    assert context["client"]["kind"] == "initiative" and context["client_root"].endswith("initiatives/plan")
    assert "drafted the plan" in ws.render_handoff(root, "Plan", kind="initiative")


def test_archived_initiative_refuses_new_sessions(root):
    eng.set_state(root, "Plan", "archived")
    with pytest.raises(ws.WorkspaceError, match="archived"):
        ws.add_session(root, "Plan", "late note", "prepared", kind="initiative")


def test_client_session_json_is_unchanged(root):
    entry = ws.add_session(root, "Alpha", "client work", "prepared")
    assert entry["client"] == "alpha" and "initiative" not in entry and entry["schema"] == "torque.session/1"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv-continuation/bin/python -m pytest tests/test_engagement_records.py -v`
Expected: FAIL (`unexpected keyword argument 'kind'`).

- [ ] **Step 3: Implement**

Add after `load_client` in `workspace.py`:

```python
def load_engagement(workspace: str | Path, name: str, kind: str = "client") -> tuple[Path, dict, dict]:
    """A client (existing records) or an initiative (engagements module)."""
    if kind == "client":
        return load_client(workspace, name)
    from .engagements import load_initiative, require
    require(kind, "sessions")
    return load_initiative(workspace, name)


def _writable_engagement(folder: Path, config: dict) -> None:
    require_writable(folder.parent.parent)
    if config.get("state") == "archived":
        raise WorkspaceError("this engagement is archived; reopen it before recording new work")
```

`add_session`: change signature to `def add_session(workspace, client_name, summary, status="prepared", evidence=None, *, kind: str = "client") -> dict:`; replace the first line with `client, _, config = load_engagement(workspace, client_name, kind)` then `_writable_engagement(client, config)` (this replaces the `require_writable` line added in Task 2); in the entry dict replace `"client": config["slug"],` with `kind: config["slug"],`.

`list_sessions`: add `*, kind: str = "client"`; first line `client, _, config = load_engagement(workspace, client_name, kind)`; in the validation replace `value.get("client") != config["slug"]` with `value.get(kind) != config["slug"]`.

`client_output_path`: add `*, kind: str = "client"`; first line `client, _, _ = load_engagement(workspace, client_name, kind)`.

`_change_context(workspace, client_name, client, kind="client")`: pass `kind=kind` to `list_changes` and `get_change` (added in Task 6), and in `show_command` replace `"--client", client.name` with `"--" + kind, client.name`.

`get_context`: add `*, kind: str = "client"`; use `load_engagement(workspace, client_name, kind)`, pass `kind` to `_change_context` and `list_sessions`.

`render_handoff`: add `*, kind: str = "client"`; use `load_engagement`, `list_sessions(..., kind=kind)`, `list_changes(..., kind=kind)` and `render_change(..., kind=kind)`; replace `f"Configured org: {client.get('org') or 'not specified'}"` with `f"Configured org: {client.get('org') or 'not specified'}" if kind == "client" else f"Initiative state: {client['state']}"`, and the empty message with `f"No session entries have been recorded for this {kind}."` (clients keep the exact existing sentence because `kind` is `client`).

Task 6 adds the `kind` keyword to the change functions; land Tasks 5 and 6 in one commit if a test run between them fails on the missing keyword.

- [ ] **Step 4: Run tests** (after Task 6 if needed)

Run: `.venv-continuation/bin/python -m pytest tests/test_engagement_records.py tests/test_workspace.py tests/test_context_continuation.py tests/test_record_resilience.py tests/test_practical_continuity.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/torque/workspace.py tests/test_engagement_records.py
git commit -m "feat(workspace): sessions, context and handoff for initiatives"
```

---

### Task 6: Change records for either kind; client-only operations refused

**Files:**
- Modify: `src/torque/changes.py` (`_directory`, `create_change`, `load_change`, `list_changes`, `_append`, `_events`, `add_note`, `add_check`, `verify_deploy`, `append_approval_event`, `get_change`, `render_change`)
- Test: `tests/test_engagement_changes.py` (new)

**Interfaces:**
- Consumes: `ws.load_engagement`, `engagements.require`.
- Produces: every public function above gains `*, kind: str = "client"`. Initiative change records and events carry `"initiative": slug` instead of `"client": slug`. `verify_deploy` and `append_approval_event` call `engagements.require(kind, ...)` and refuse initiatives.

- [ ] **Step 1: Write the failing tests**

```python
"""Initiatives keep change records; client-only operations are refused."""
import pytest

from torque import changes
from torque import engagements as eng
from torque import workspace as ws


@pytest.fixture
def root(tmp_path):
    root = ws.init_workspace(tmp_path / "firm", "Synthetic firm")
    ws.add_client(root, "Alpha")
    eng.add_initiative(root, "Plan")
    return root


def test_initiative_change_lifecycle(root):
    item = changes.create_change(root, "Plan", "Pilot VM", "Work stays off laptops", ["Controls pass"],
                                 kind="initiative")
    assert item["initiative"] == "plan" and "client" not in item
    changes.add_note(root, "Plan", item["id"], "use one team VM", "decision", kind="initiative")
    detail = changes.get_change(root, "Plan", item["id"], kind="initiative")
    assert detail["events"][-1]["initiative"] == "plan"
    assert [c["id"] for c in changes.list_changes(root, "Plan", kind="initiative")] == [item["id"]]
    assert changes.list_changes(root, "Alpha") == []
    assert "Pilot VM" in changes.render_change(root, "Plan", item["id"], kind="initiative")


def test_verify_deploy_and_approvals_are_client_only(root):
    item = changes.create_change(root, "Plan", "T", "O", kind="initiative")
    with pytest.raises(ws.WorkspaceError, match="clients only"):
        changes.verify_deploy(root, "Plan", item["id"], "some-org", "0Af000000000001AAA", [], None,
                              kind="initiative")
    with pytest.raises(ws.WorkspaceError, match="clients only"):
        changes.append_approval_event(root, "Plan", item["id"], "approval_request", {}, kind="initiative")


def test_a_client_change_is_unchanged(root):
    item = changes.create_change(root, "Alpha", "T", "O")
    assert item["client"] == "alpha" and "initiative" not in item
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv-continuation/bin/python -m pytest tests/test_engagement_changes.py -v`
Expected: FAIL (`unexpected keyword argument 'kind'`).

- [ ] **Step 3: Implement**

```python
def _directory(workspace: str | Path, client: str, kind: str = "client") -> tuple[Path, dict]:
    root, _, config = ws.load_engagement(workspace, client, kind)
    return ws._inside(root, root / "changes"), config


def _owner_key(record: dict) -> str:
    return "initiative" if "initiative" in record else "client"
```

- `create_change(..., org=None, *, kind="client")`: `directory, config = _directory(workspace, client, kind)`; in `record` replace `"client": config["slug"],` with `kind: config["slug"],`; keep the Task 2 `require_writable` line and add `if config.get("state") == "archived": raise ws.WorkspaceError("this engagement is archived; reopen it before recording new work")`.
- `load_change(workspace, client, identifier, *, kind="client")`: `_directory(workspace, client, kind)`; validation `record.get(kind) != config["slug"]`.
- `list_changes(workspace, client, *, kind="client")`: `_directory(workspace, client, kind)` and `load_change(workspace, client, p.name, kind=kind)`.
- `_append`: replace `"client": record["client"],` with `_owner_key(record): record[_owner_key(record)],`.
- `_events`: replace `event.get("client") != record["client"]` with `event.get(_owner_key(record)) != record[_owner_key(record)]`.
- `add_note`, `add_check`, `get_change`, `render_change`: add `*, kind="client"` and pass `kind=kind` to every `load_change`/`list_changes`/`_directory` call inside them.
- `verify_deploy(..., *, kind="client")` and `append_approval_event(..., *, kind="client")`: first line

```python
    from .engagements import require
    require(kind, "verify_deploy")   # in append_approval_event: require(kind, "approvals")
```

- [ ] **Step 4: Run tests**

Run: `.venv-continuation/bin/python -m pytest tests/test_engagement_changes.py tests/test_engagement_records.py tests/test_changes.py tests/test_changes_approval.py tests/test_context_continuation.py tests/test_approval.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/torque/changes.py src/torque/workspace.py tests/test_engagement_changes.py
git commit -m "feat(changes): change records for initiatives; client-only operations refused"
```

---

### Task 7: CLI

**Files:**
- Modify: `src/torque/cli.py` (`_client_args` line 50; `build_parser` initiative and engagement parsers; `main` dispatch for context, session, handoff; `_change` line 373)
- Test: `tests/test_engagement_cli.py` (new)

**Interfaces:**
- Consumes: Tasks 4-6.
- Produces: `--client X` or `--initiative X` (exactly one) on `context`, `session add|list`, `handoff`, `change *`; `torque initiative add NAME --workspace W [--owner O] [--json]`, `initiative list`, `initiative show NAME`, `initiative set-state NAME STATE [--reason] [--review-date] [--outcome]`; `torque engagement list --workspace W [--kind client|initiative] [--json]`. Helper `_scope(parsed) -> tuple[str, str]` returns `(name, kind)`.

- [ ] **Step 1: Write the failing tests**

```python
"""CLI: initiatives use the same commands as clients."""
import contextlib
import io
import json

from torque import cli
from torque import workspace as ws


def invoke(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(args))
        except SystemExit as exc:  # argparse usage errors exit instead of returning
            code = exc.code
    return code, out.getvalue(), err.getvalue()


def test_initiative_commands(tmp_path):
    root = str(ws.init_workspace(tmp_path / "firm", "Synthetic firm"))
    ws.add_client(root, "Alpha")
    assert invoke("initiative", "add", "Plan", "--workspace", root, "--owner", "lead")[0] == 0
    code, out, _ = invoke("engagement", "list", "--workspace", root, "--json")
    assert code == 0 and {r["kind"] for r in json.loads(out)} == {"client", "initiative"}
    assert invoke("session", "add", "--workspace", root, "--initiative", "Plan", "--summary", "s",
                  "--status", "prepared")[0] == 0
    code, out, _ = invoke("context", "--workspace", root, "--initiative", "Plan", "--json")
    assert code == 0 and json.loads(out)["client"]["kind"] == "initiative"
    assert invoke("initiative", "set-state", "Plan", "paused", "--workspace", root, "--reason", "wait")[0] == 0
    code, out, _ = invoke("initiative", "show", "Plan", "--workspace", root, "--json")
    assert json.loads(out)["state"] == "paused"


def test_client_and_initiative_are_exclusive(tmp_path):
    root = str(ws.init_workspace(tmp_path / "firm", "Synthetic firm"))
    code, _, err = invoke("context", "--workspace", root, "--client", "A", "--initiative", "B")
    assert code == 2 and "not allowed with" in err


def test_client_commands_are_unchanged(tmp_path):
    root = str(ws.init_workspace(tmp_path / "firm", "Synthetic firm"))
    ws.add_client(root, "Alpha")
    code, out, _ = invoke("session", "add", "--workspace", root, "--client", "Alpha", "--summary", "s",
                          "--status", "prepared", "--json")
    assert code == 0 and json.loads(out)["client"] == "alpha"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv-continuation/bin/python -m pytest tests/test_engagement_cli.py -v`
Expected: FAIL (`invalid choice: 'initiative'`).

- [ ] **Step 3: Implement**

Replace `_client_args` in `cli.py`:

```python
def _client_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", required=True, help="private workspace directory")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--client", help="explicit client name or slug")
    scope.add_argument("--initiative", help="explicit internal initiative name or slug")


def _scope(parsed: argparse.Namespace) -> tuple[str, str]:
    """(name, kind) for the engagement a command names."""
    if getattr(parsed, "initiative", None):
        return parsed.initiative, "initiative"
    return parsed.client, "client"
```

In `build_parser`, after the `client` parser block, add:

```python
    initiative = sub.add_parser("initiative", help="manage an internal initiative's private context")
    initiative_sub = initiative.add_subparsers(dest="action", required=True)
    i_add = initiative_sub.add_parser("add")
    i_add.add_argument("name")
    i_add.add_argument("--workspace", required=True)
    i_add.add_argument("--owner", help="accountable person")
    i_add.add_argument("--json", action="store_true")
    for action in ("list", "show", "set-state"):
        p = initiative_sub.add_parser(action)
        p.add_argument("--workspace", required=True)
        p.add_argument("--json", action="store_true")
        if action != "list":
            p.add_argument("name")
        if action == "set-state":
            p.add_argument("state", choices=("active", "paused", "closed", "archived"))
            p.add_argument("--reason")
            p.add_argument("--review-date")
            p.add_argument("--outcome")
    engagement = sub.add_parser("engagement", help="list clients and initiatives together")
    engagement_sub = engagement.add_subparsers(dest="action", required=True)
    e_list = engagement_sub.add_parser("list")
    e_list.add_argument("--workspace", required=True)
    e_list.add_argument("--kind", choices=("client", "initiative"))
    e_list.add_argument("--json", action="store_true")
```

In `main`, add before `elif parsed.command == "change":`:

```python
        elif parsed.command == "initiative":
            from . import engagements as eng
            if parsed.action == "add":
                path = eng.add_initiative(parsed.workspace, parsed.name, parsed.owner)
                _print_json({"initiative_root": str(path), "org_calls": False}) if parsed.json else print(path)
            elif parsed.action == "list":
                rows = eng.list_engagements(parsed.workspace, "initiative")
                if parsed.json:
                    _print_json(rows)
                else:
                    for row in rows:
                        print(f"{row['slug']}: {row['name']} ({row['state']}, owner: {row['owner'] or 'not set'})")
                    if not rows:
                        print("No initiatives yet. Use torque initiative add NAME --workspace PATH.")
            else:
                config = (eng.load_initiative(parsed.workspace, parsed.name)[2] if parsed.action == "show"
                          else eng.set_state(parsed.workspace, parsed.name, parsed.state, reason=parsed.reason,
                                             review_date=parsed.review_date, outcome=parsed.outcome))
                if parsed.json:
                    _print_json(config)
                else:
                    print(f"{config['slug']}: {config['name']} ({config['state']}, owner: {config['owner'] or 'not set'})")
        elif parsed.command == "engagement":
            from . import engagements as eng
            rows = eng.list_engagements(parsed.workspace, parsed.kind)
            if parsed.json:
                _print_json(rows)
            else:
                for row in rows:
                    print(f"{row['kind']:<10} {row['slug']}: {row['name']} ({row['state']})")
```

In `main`, wherever `context`, `session` and `handoff` call `ws.*` with `parsed.client`, use `name, kind = _scope(parsed)` and pass `name` plus `kind=kind` (for `get_context`, `add_session`, `list_sessions`, `render_handoff`, `client_output_path`). The text output line `print(f"Configured org: {context['client'].get('org') or 'not specified'}")` becomes conditional on `kind == "client"`.

In `_change`, set `name, kind = _scope(args)`, `common = (args.workspace, name)`, and pass `kind=kind` to every `changes.*` and `ws.client_output_path` call.

- [ ] **Step 4: Run tests**

Run: `.venv-continuation/bin/python -m pytest tests/test_engagement_cli.py tests/test_cli.py tests/test_cli_names.py tests/test_context_continuation.py -q`
Expected: PASS. If `test_cli_names.py` inventories subcommands, add `initiative` and `engagement` to its expected list.

- [ ] **Step 5: Commit**

```bash
git add src/torque/cli.py tests/test_engagement_cli.py tests/test_cli_names.py
git commit -m "feat(cli): --initiative scope, initiative commands and engagement list"
```

---

### Task 8: Gate protection for bindings and client control folders

**Files:**
- Modify: `src/torque/gate.py` (`_PROTECTED_NAMES` line 2846, `_protected_record` 2855, `_holds_records` 2865, `_decide` copies line 2373, `_CLIENT_SPEC` 1083 and `clients_index_count` 1182), `src/torque/gate_connected.py` (`_guarded` line 78)
- Test: `tests/test_engagement_gate.py` (new)

**Interfaces:**
- Produces: `gate._ENGAGEMENT_RECORDS = {"clients": (...), "initiatives": ("binding.json",)}`; `_protected_record` and `_holds_records` cover both folders; `gate_connected._guarded` includes `initiatives/`; `clients_index_count` counts tracked files under `clients/` and `initiatives/`.

- [ ] **Step 1: Write the failing tests**

```python
"""On a laptop the agent shares the owner's account, so the gate's matchers are
the protection for engagement bindings and client control records."""
from pathlib import Path

from torque import engagements as eng
from torque import gate
from torque import gate_connected
from torque import workspace as ws


def setup(tmp_path):
    root = ws.init_workspace(tmp_path / "firm", "Synthetic firm")
    ws.add_client(root, "Alpha")
    eng.add_initiative(root, "Plan")
    return root


def test_binding_file_is_protected_in_every_mode(tmp_path):
    root = setup(tmp_path)
    binding = root / "initiatives" / "plan" / "binding.json"
    assert gate._protected_record(binding)
    assert gate._approval_file_reason("Write", {"file_path": str(binding)}, root)
    assert gate._approval_file_reason("Edit", {"file_path": "initiatives/plan/binding.json"}, root)


def test_removing_or_moving_an_initiative_folder_is_refused(tmp_path):
    root = setup(tmp_path)
    for command in ("rm -rf initiatives/plan", "mv initiatives/plan /tmp/x", "rm initiatives/plan/binding.json",
                    "cd initiatives && rm -rf plan"):
        assert gate._approval_file_reason("Bash", {"command": command}, root), command


def test_state_and_records_stay_writable(tmp_path):
    root = setup(tmp_path)
    for rel in ("initiatives/plan/state/engagement.json", "initiatives/plan/context.md",
                "initiatives/plan/sessions/x.json"):
        assert not gate._approval_file_reason("Write", {"file_path": rel}, root), rel


def test_future_client_control_folders_are_protected(tmp_path):
    root = setup(tmp_path)
    for name in ("control/consent.json", "requests/r.json", "claims/c.consumed", "binding.json"):
        assert gate._protected_record(root / "clients" / "alpha" / name), name


def test_connected_guard_covers_initiatives(tmp_path):
    root = setup(tmp_path)
    guarded = gate_connected._guarded(root, "alpha")
    assert root / "initiatives" in guarded
    assert all(p.name != "alpha" for p in guarded)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv-continuation/bin/python -m pytest tests/test_engagement_gate.py -v`
Expected: FAIL (binding.json not protected; initiatives not guarded).

- [ ] **Step 3: Implement**

In `gate.py` replace `_PROTECTED_NAMES`, `_protected_record` and `_holds_records`:

```python
_PROTECTED_NAMES = ("consent.json", "consent-evidence", "approvals", "binding.json", "control", "requests", "claims")
_ENGAGEMENT_RECORDS = {"clients": _PROTECTED_NAMES, "initiatives": ("binding.json",)}


def _protected_record(path: Path) -> bool:
    """path is an engagement's binding, or a client's consent, consent evidence,
    approval, control, request or claim record (or inside one), in a Torque workspace."""
    parts = path.parts
    for index in range(len(parts) - 3, -1, -1):
        names = _ENGAGEMENT_RECORDS.get(parts[index])
        if names and parts[index + 2] in names:
            return _is_workspace_root(Path(*parts[:index]))
    return False


def _holds_records(path: Path) -> bool:
    """path is a folder that holds such records: an engagement folder, clients/ or
    initiatives/, or the workspace root."""
    try:
        if not path.is_dir():
            return False
        names = _ENGAGEMENT_RECORDS.get(path.parent.name)
        if names and _is_workspace_root(path.parent.parent):
            return any((path / name).exists() for name in names)
        for folder, names in _ENGAGEMENT_RECORDS.items():
            base = path if path.name == folder else path / folder
            if base.is_dir() and _is_workspace_root(base.parent) and any(
                    (child / name).exists() for child in base.iterdir() if child.is_dir() for name in names):
                return True
    except OSError:
        return True
    return False
```

In `_decide`, replace the copies line so guarded folders outside `clients/` map correctly:

```python
        copies = None if guarded is None else [copy / Path(folder).relative_to(workspace)
                                               if Path(folder).is_relative_to(workspace)
                                               else copy / "clients" / Path(folder).name for folder in guarded]
```

Replace `_CLIENT_SPEC` usage in `clients_index_count`:

```python
_CLIENT_SPEC = ":(icase)clients"
_INITIATIVE_SPEC = ":(icase)initiatives"
```

and `listed = _git_run(workspace, ["ls-files", "--", _CLIENT_SPEC, _INITIATIVE_SPEC])`.

In `gate_connected.py` replace `_guarded`:

```python
def _guarded(workspace: Path, bound: str | None) -> list[Path]:
    """Folders a client-bound connected session treats as other engagements'
    context: every other client and every internal initiative."""
    clients = workspace / "clients"
    initiatives = [workspace / "initiatives"] if (workspace / "initiatives").exists() else []
    if not bound:
        return [clients, *initiatives]
    try:
        return [p for p in clients.iterdir() if p.name != bound and (p.is_dir() or p.is_symlink())] + initiatives
    except OSError:
        return [clients, *initiatives]
```

- [ ] **Step 4: Run the gate suites**

Run: `.venv-continuation/bin/python -m pytest tests/test_engagement_gate.py tests/test_gate.py tests/test_gate_connected.py tests/test_gate_connected_paths.py tests/test_gate_alpha11.py tests/test_gate_alpha12.py tests/test_gate_alpha13.py tests/test_gate_alpha14.py tests/test_connected_mode.py tests/test_unattended_gate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/torque/gate.py src/torque/gate_connected.py tests/test_engagement_gate.py
git commit -m "feat(gate): protect engagement bindings and client control records; guard initiatives"
```

---

### Task 9: Documentation, version, full suite

**Files:**
- Modify: `README.md` (short "Clients and initiatives" section), `CHANGELOG.md` (new 2.0.0a18 entry), `pyproject.toml` and `src/torque/__init__.py` (version `2.0.0a18`)
- Test: full suite

- [ ] **Step 1: README section** (after the client usage section):

```markdown
### Clients and initiatives

Internal work that is not for a client (adopting a tool, a workspace project) is an
initiative: `torque initiative add NAME --workspace .` creates `initiatives/SLUG/`
with the same sessions, context, change records and handoff as a client. Use
`--initiative SLUG` wherever `--client` works for records. Initiatives never get
client-only powers (org, consent, approvals, connected mode, verify-deploy).
`torque engagement list --workspace .` shows both kinds.
```

- [ ] **Step 2: CHANGELOG entry** at the top:

```markdown
## 2.0.0a18 - clients and initiatives (not published to a package index)

- Initiatives: `torque initiative add|list|show|set-state`, `--initiative` on context, session, handoff and change commands, `torque engagement list`.
- One boundary helper keeps evidence and exports inside their own client or initiative.
- Durable publication: directory fsync; replacing a file keeps its mode and group.
- A maintenance flag (`.torque/maintenance`) pauses record writes.
- The gate protects initiative bindings and future client control, request and claim folders, including folder moves and removals, in every mode; connected sessions treat initiatives as other context.
- Client commands and records are unchanged.
```

- [ ] **Step 3: Version bump** in `pyproject.toml` (`version = "2.0.0a18"`) and `src/torque/__init__.py` (`__version__ = "2.0.0a18"`).

- [ ] **Step 4: Full suite and hygiene**

Run: `.venv-continuation/bin/python -m pytest -q`
Expected: all pass (3,002 existing tests plus the new ones).
Run: `.venv-continuation/bin/python -m pytest tests/test_public_hygiene.py -q`
Expected: PASS (no private names).

- [ ] **Step 5: Commit**

```bash
git add README.md CHANGELOG.md pyproject.toml src/torque/__init__.py
git commit -m "release: 2.0.0a18 clients and initiatives"
```
