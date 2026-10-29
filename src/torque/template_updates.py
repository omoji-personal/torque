"""Update packaged workflow defaults while preserving local customizations.

Per-workspace flock serializes cooperating updaters. Directory-fd operations
reject symlinks; publication is atomic per file, followed by an atomic manifest.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import difflib
import errno
import hashlib
from importlib import resources
import json
import os
from pathlib import Path
import re
import stat
from uuid import uuid4

from . import __version__
from . import workspace as ws

MANIFEST = ".torque/templates.json"
_SCHEMA = "torque.templates/1"
_GROUPS = (("commands", ".claude/commands"), ("rules", ".claude/rules"),
           ("skills", ".claude/skills"), ("agents", ".claude/agents"),
           ("skills", ".agents/skills"))
# The Antigravity copies derived from the rules and worker roles (antigravity.py);
# recipes become skills under .agents/skills, listed above. Versions up to 2.0.0a19
# reject a manifest whose "files" names these two locations, so their entries are
# kept in a section of their own, which those versions ignore.
_DERIVED = (".agents/rules", ".agents/agents")
_DERIVED_SECTION = "antigravity_files"

# Windows lacks the openat()/linkat()/unlinkat() family Python exposes as the
# dir_fd parameter, and the O_DIRECTORY/O_NOFOLLOW flags do not exist there.
# The directory-fd-relative implementation below is POSIX only; on Windows a
# best-effort path-based implementation with explicit symlink rejection is
# used instead (a small TOCTOU window replaces the POSIX openat containment).
_WINDOWS = os.name == "nt"
if _WINDOWS:
    import msvcrt
else:
    import fcntl
    _NOFOLLOW = os.O_NOFOLLOW
    _DIRECTORY = os.O_DIRECTORY


def _hash(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def _lf(contents: bytes | None) -> bytes | None:
    """Local bytes with Windows line endings read as LF, as the packaged text is read
    (`_bundled`). A Git checkout with core.autocrlf or a Windows editor changes only the
    endings; that is not a local edit. Files are still written with LF."""
    return None if contents is None else re.sub(rb"\r\n?", b"\n", contents)


def _renames() -> dict[str, str]:
    """Claude Code copies that versions up to 2.0.0a21 installed under the name of a
    command Claude Code answers itself: old workspace path -> new one (torque-NAME)."""
    from . import hosts
    return {f".claude/commands/{name}.md": f".claude/commands/{hosts.recipe_name(hosts.CLAUDE, name)}.md"
            for name in hosts.CLAUDE.builtin_commands}


def _previous_copy(path: str) -> bytes | None:
    """What versions up to 2.0.0a21 installed at an old path of `_renames`: the
    packaged recipe as it is."""
    try:
        item = resources.files("torque").joinpath("data", "commands", path.rsplit("/", 1)[-1])
        return item.read_text(encoding="utf-8").encode("utf-8") if item.is_file() else None
    except (OSError, TypeError, ModuleNotFoundError):
        return None


def _valid_workflow(path: str) -> bool:
    return (isinstance(path, str) and path.endswith(".md") and "\\" not in path
            and all(part not in ("", ".", "..") for part in path.split("/"))
            and any(path.startswith(prefix + "/") for prefix in (*(p for _, p in _GROUPS), *_DERIVED)))


def _derived(path: str) -> bool:
    return any(path.startswith(prefix + "/") for prefix in _DERIVED)


def _bundled(mode: str = "full") -> dict[str, tuple[bytes, str]]:
    """Every packaged file a workspace in `mode` gets: the same set in every mode,
    plus the rule files that mode adds (workspace.mode_rules). Claude Code's copies
    are named and worded as workspace.claude_copy installs them."""
    data = resources.files("torque").joinpath("data")
    files = {}
    def visit(item, relative: str, source: str, group: str) -> None:
        if item.is_dir():
            for child in sorted(item.iterdir(), key=lambda entry: entry.name):
                if child.name in ("", ".", "..") or "/" in child.name or "\\" in child.name:
                    raise ws.WorkspaceError("invalid bundled workflow path")
                visit(child, relative + "/" + child.name, source + "/" + child.name, group)
        elif item.is_file() and item.name.endswith(".md"):
            text = item.read_text(encoding="utf-8")
            if relative.startswith(".claude/"):
                name, text = ws.claude_copy(group, item.name, text)
                relative = relative.rsplit("/", 1)[0] + "/" + name
            if not _valid_workflow(relative):
                raise ws.WorkspaceError("bundled workflow is outside the managed locations")
            files[relative] = (text.encode("utf-8"), source)
    for group, destination in _GROUPS:
        visit(data.joinpath(group), destination, "torque/data/" + group, group)
    from . import antigravity
    for relative, (text, source) in antigravity.surface(data).items():
        if not _valid_workflow(relative):
            raise ws.WorkspaceError("bundled workflow is outside the managed locations")
        # A packaged skill keeps its place over a recipe of the same name.
        files.setdefault(relative, (text.encode("utf-8"), source))
    for relative, (text, source) in ws.mode_rules(mode, data).items():
        if not _valid_workflow(relative):
            raise ws.WorkspaceError("bundled workflow is outside the managed locations")
        files[relative] = (text.encode("utf-8"), source)
    return files


def _unsafe(path: str, exc: OSError) -> None:
    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
        raise ws.WorkspaceError(f"workflow path must use real directories and regular files, not symlinks: {path}") from exc
    raise exc


@contextmanager
def _parent(root_fd, path: str, create: bool = False):
    if _WINDOWS:
        # root_fd is the workspace root Path (see _open_root). Walk it
        # component by component, refusing any symlinked directory.
        current = root_fd
        for part in path.split("/")[:-1]:
            current = current / part
            if current.is_symlink():
                raise ws.WorkspaceError(f"workflow path must use real directories and regular files, not symlinks: {path}")
            if not current.exists():
                if not create:
                    yield None
                    return
                try:
                    current.mkdir(mode=0o700)
                except FileExistsError:
                    pass
                if current.is_symlink():
                    raise ws.WorkspaceError(f"workflow path must use real directories and regular files, not symlinks: {path}")
            elif not current.is_dir():
                raise ws.WorkspaceError(f"workflow path must use real directories and regular files, not symlinks: {path}")
        yield current
        return
    fd = os.dup(root_fd)
    try:
        for part in path.split("/")[:-1]:
            try:
                next_fd = os.open(part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    yield None
                    return
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass  # Re-open with O_NOFOLLOW; a racing path is never trusted.
                try:
                    next_fd = os.open(part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=fd)
                except OSError as exc:
                    _unsafe(path, exc)
            except OSError as exc:
                _unsafe(path, exc)
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


def _read_at(parent_fd, name: str) -> bytes | None:
    if _WINDOWS:
        target = parent_fd / name
        if target.is_symlink():
            raise ws.WorkspaceError(f"workflow path must use real directories and regular files, not symlinks: {name}")
        if not target.exists():
            return None
        if not target.is_file():
            raise ws.WorkspaceError(f"workflow path is not a regular file: {name}")
        return target.read_bytes()
    try:
        fd = os.open(name, os.O_RDONLY | _NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        _unsafe(name, exc)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ws.WorkspaceError(f"workflow path is not a regular file: {name}")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            return stream.read()
    finally:
        os.close(fd)


def _read(root_fd: int, path: str) -> bytes | None:
    with _parent(root_fd, path) as fd:
        return None if fd is None else _read_at(fd, path.split("/")[-1])


@contextmanager
def _locked(root_fd, check: bool):
    with _parent(root_fd, ".torque/templates.lock", create=not check) as fd:
        if fd is None:
            yield
            return
        if _WINDOWS:
            lock_path = fd / "templates.lock"
            try:
                if check:
                    lock_fd = os.open(lock_path, os.O_RDONLY)
                else:
                    try:
                        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
                    except FileExistsError:
                        lock_fd = os.open(lock_path, os.O_RDWR)
                handle = os.fdopen(lock_fd, "r+b" if not check else "rb")
            except FileNotFoundError:
                if check:
                    yield
                    return
                raise ws.WorkspaceError("template lock disappeared during update; rerun after local filesystem changes finish") from None
            try:
                if lock_path.is_symlink():
                    raise ws.WorkspaceError("template lock must use a real regular file, not a symlink")
                if not lock_path.is_file():
                    raise ws.WorkspaceError("template lock is not a regular file")
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                yield
            finally:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
                handle.close()
            return
        flags = (os.O_RDONLY if check else os.O_RDWR) | _NOFOLLOW | os.O_NONBLOCK
        try:
            if check:
                lock = os.open("templates.lock", flags, dir_fd=fd)
            else:
                try:
                    # Separate exclusive creation from opening an existing inode.
                    # Concurrent O_CREAT|O_NOFOLLOW can return ENOENT on macOS.
                    lock = os.open("templates.lock", flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=fd)
                except FileExistsError:
                    lock = os.open("templates.lock", flags, dir_fd=fd)
        except FileNotFoundError:
            if check:
                yield
                return
            raise ws.WorkspaceError("template lock disappeared during update; rerun after local filesystem changes finish") from None
        except OSError as exc:
            _unsafe(".torque/templates.lock", exc)
        try:
            if not stat.S_ISREG(os.fstat(lock).st_mode):
                raise ws.WorkspaceError("template lock is not a regular file")
            fcntl.flock(lock, fcntl.LOCK_SH if check else fcntl.LOCK_EX)
            yield
        finally:
            os.close(lock)  # Releases flock; keep the lock inode for waiting processes.


def _load_manifest(raw: bytes | None) -> dict:
    """The manifest with every tracked file under "files", whichever section holds it."""
    if raw is None:
        return {"schema": _SCHEMA, "files": {}}
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("schema") != _SCHEMA or not isinstance(value.get("files"), dict):
            raise ValueError("invalid schema")
        derived = value.get(_DERIVED_SECTION, {})
        if not isinstance(derived, dict):
            raise ValueError("invalid schema")
        files = {**derived, **value["files"]}
        for name, entry in files.items():
            if (not _valid_workflow(name) or not isinstance(entry, dict)
                    or not isinstance(entry.get("sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
                    or not isinstance(entry.get("version"), str) or not entry["version"]):
                raise ValueError("invalid file entry")
        return {"schema": _SCHEMA, "files": files}
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ws.WorkspaceError("invalid .torque/templates.json; preserve it and repair the manifest before updating") from exc


def _publish(root_fd, path: str, contents: bytes, expected: bytes | None) -> bool:
    """Return False if local bytes appeared/changed before publication; never replace new files."""
    with _parent(root_fd, path, create=True) as fd:
        name = path.split("/")[-1]
        temporary = ".torque-update-" + uuid4().hex
        if _WINDOWS:
            target = fd / name
            temp_path = fd / temporary
            try:
                temp_fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(temp_fd, "wb") as stream:
                    stream.write(contents)
                    stream.flush()
                    os.fsync(stream.fileno())
                if _read_at(fd, name) != expected:
                    return False
                if expected is None:
                    try:
                        ws.publish_new(temp_path, target)
                    except FileExistsError:
                        return False
                else:
                    os.replace(temp_path, target)
                return True
            finally:
                try:
                    temp_path.unlink()
                except FileNotFoundError:
                    pass
        stream_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600, dir_fd=fd)
        try:
            with os.fdopen(stream_fd, "wb") as stream:
                stream.write(contents)
                stream.flush()
                os.fsync(stream.fileno())
            if _read_at(fd, name) != expected:
                return False
            if expected is None:
                try:
                    ws.publish_new(temporary, name, dir_fd=fd)
                except FileExistsError:
                    return False
            else:
                os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
            return True
        finally:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass


def _move(root_fd, old: str, new: str) -> bool:
    """Give the file at `old` the name `new` in the same folder, never replacing a
    file there. False when `new` appeared or `old` went away in the meantime."""
    with _parent(root_fd, old) as fd:
        if fd is None:
            return False
        old_name, new_name = old.split("/")[-1], new.split("/")[-1]
        if _WINDOWS:
            source = fd / old_name
            if source.is_symlink() or not source.is_file():
                return False
            try:
                ws.publish_new(source, fd / new_name)
            except (FileExistsError, FileNotFoundError):
                return False
            source.unlink(missing_ok=True)  # Already gone when publish_new renamed it.
            return True
        try:
            if not stat.S_ISREG(os.stat(old_name, dir_fd=fd, follow_symlinks=False).st_mode):
                return False
            ws.publish_new(old_name, new_name, dir_fd=fd)
        except (FileExistsError, FileNotFoundError):
            return False
        try:
            os.unlink(old_name, dir_fd=fd)
        except FileNotFoundError:
            pass
        os.fsync(fd)
        return True


def _remove(root_fd, path: str, expected: bytes) -> bool:
    """Remove the file at `path` while it still holds `expected`; False otherwise."""
    with _parent(root_fd, path) as fd:
        if fd is None:
            return False
        name = path.split("/")[-1]
        if _read_at(fd, name) != expected:
            return False
        if _WINDOWS:
            (fd / name).unlink(missing_ok=True)
            return True
        try:
            os.unlink(name, dir_fd=fd)
        except FileNotFoundError:
            pass
        os.fsync(fd)
        return True


def _open_root(root: Path):
    """Return an opaque container handle for `root`: a dir fd on POSIX, the
    Path itself on Windows (which has no dir_fd-relative open)."""
    if _WINDOWS:
        return root
    return os.open(root, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)


def _close_root(handle) -> None:
    if not _WINDOWS:
        os.close(handle)


_SUGGEST_MERGE = ("Edited here, and the packaged text changed since this file was recorded. Compare them with "
                  "`torque workspace upgrade PATH --diff FILE`, merge what you want, then keep your version with "
                  "`--accept FILE`, or delete the file to take the packaged copy. Local files are never replaced.")
_SUGGEST_UNMANAGED = ("A local file at a path Torque installs, which Torque never recorded. Compare it with "
                      "`torque workspace upgrade PATH --diff FILE`; keep it with `--accept FILE`, or delete it to "
                      "take the packaged copy.")
_CUSTOMIZED = "Kept as it is: the packaged text has not changed since this file was recorded."


def _plan_renames(root_fd, baseline: dict, updated: dict, check: bool):
    """Bring Claude Code copies kept under an old name (`_renames`) to the new one.
    An untouched packaged copy is removed once the new copy is in place (`stale`); a
    consultant's edited copy moves to the new name as it is, with its recorded base,
    and is never overwritten; when both names hold a file, both stay and the old one
    is reported. Returns (rows, {new path: bytes a check-mode move would leave}, stale)."""
    rows, moved, stale = [], {}, []
    for old, new in sorted(_renames().items()):
        raw = _read(root_fd, old)
        entry = baseline.get(old)
        if raw is None:
            updated.pop(old, None)
            continue
        row = {"path": old, "renamed_to": new, "applied": False}
        rows.append(row)
        if _lf(raw) == _previous_copy(old) or (entry and _hash(_lf(raw)) == entry["sha256"]):
            row.update(action="renamed", reason="packaged_default")
            updated.pop(old, None)
            stale.append((row, old, new, raw))
            continue
        existing = _read(root_fd, new)
        if existing is not None and existing != raw:
            row.update(action="preserve", reason="renamed_both_present",
                       suggestion=f"Claude Code answers this command itself, so the recipe is now {new}, which "
                                  "also holds a file. Merge your edits into it, then delete this one.")
            continue
        if check:
            if existing is None:
                moved[new] = raw
        elif _move(root_fd, old, new) if existing is None else _remove(root_fd, old, raw):
            # (An interrupted move can leave the same file under both names.)
            row["applied"] = True
        else:
            row.update(action="preserve", reason="changed_during_update",
                       suggestion="The file changed during this run. Review it and rerun the update.")
            continue
        row.update(action="renamed", reason="local_edit")
        if entry and new not in baseline:
            baseline[new] = updated[new] = entry
        updated.pop(old, None)
    return rows, moved, stale


_FENCE = re.compile(r"^\s*(```|~~~)")


def _imports_agents(text: str) -> bool:
    """The CLAUDE.md text imports AGENTS.md: an `@AGENTS.md` outside code spans and
    fenced blocks, where Claude Code reads no import."""
    fenced = False
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and re.search(r"(?:^|\s)@(?:\./)?AGENTS\.md(?=\s|$)", re.sub(r"`[^`]*`", "", line)):
            return True
    return False


def _claude_md(root_fd, check: bool) -> tuple[dict | None, str]:
    """The CLAUDE.md init wrote in versions up to 2.0.0a21 asked the model to read
    AGENTS.md, which it may not do; it is replaced while unchanged. An edited one is
    kept, with a note when it does not import AGENTS.md. Returns (row, note)."""
    raw = _read(root_fd, "CLAUDE.md")
    if raw is None:
        return None, ""
    text = _lf(raw).decode("utf-8", "replace")
    if text in ws.LEGACY_CLAUDE_NOTES:
        new = ws.CLAUDE_NOTES.encode("utf-8")
        row = {"path": "CLAUDE.md", "action": "update", "applied": False, "packaged_sha256": _hash(new),
               "packaged_source": "torque workspace init (CLAUDE.md)"}
        if not check:
            if _publish(root_fd, "CLAUDE.md", new, raw):
                row["applied"] = True
            else:
                row.update(action="preserve", reason="changed_during_update",
                           suggestion="The file changed during this run. Review it and rerun the update.")
        return row, ""
    if _imports_agents(text):
        return None, ""
    return None, ("CLAUDE.md does not import AGENTS.md. Claude Code reads only CLAUDE.md when both exist, so the "
                  "workspace instructions reach a session only if the model opens AGENTS.md. Add the line "
                  "@AGENTS.md at the top of CLAUDE.md; your own text can stay below it.")


def _workspace_relative(root: Path, spec) -> str:
    """A managed path as the consultant typed it, relative to the workspace with forward slashes."""
    text = str(spec).strip()
    path = Path(text).expanduser()
    if path.is_absolute():
        try:
            text = path.resolve().relative_to(root).as_posix()
        except ValueError:
            raise ws.WorkspaceError(f"{spec} is outside the workspace") from None
    text = text.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.rstrip("/")


def _accepted_paths(root: Path, specs, bundle: dict) -> set[str]:
    chosen: set[str] = set()
    for spec in specs:
        relative = _workspace_relative(root, spec)
        found = {path for path in bundle if relative and (path == relative or path.startswith(relative + "/"))}
        if not found:
            raise ws.WorkspaceError(f"{spec} names no managed packaged file; the upgrade report lists them")
        chosen |= found
    return chosen


def _run(workspace: Path, *, check: bool, initial: bool = False, only=None, accept=None) -> dict:
    """`only`, a set of workspace paths, limits the run to those files; every other
    manifest entry is carried over as it is. `accept`, paths or folders, records the
    packaged text as the base of each named file that differs locally and writes
    nothing else. A whole run (neither) also renames Claude Code copies (`_renames`)
    and replaces an unchanged CLAUDE.md from an earlier version."""
    # Reject a symlink at the user-supplied root before load_workspace resolves it.
    if Path(workspace).expanduser().is_symlink():
        raise ws.WorkspaceError("workspace root must not be a symlink")
    root, config = ws.load_workspace(workspace)
    # Read the whole installed bundle before any mutation.
    bundle = _bundled(ws.access_mode(config))
    # The rule files of a mode this workspace is not in.
    off_mode = ws.mode_rule_paths() - set(bundle)
    if accept is not None:
        only = _accepted_paths(root, accept, bundle)
    whole = not initial and only is None
    root_fd = _open_root(root)
    try:
        with _locked(root_fd, check):
            raw_manifest = _read(root_fd, MANIFEST)
            manifest = _load_manifest(raw_manifest)
            baseline = manifest["files"]
            updated = dict(baseline)
            actions, moved, stale = _plan_renames(root_fd, baseline, updated, check) if whole else ([], {}, [])
            for path, (contents, source) in sorted(bundle.items()):
                if only is not None and path not in only:
                    continue
                raw = moved[path] if path in moved else _read(root_fd, path)
                current = _lf(raw)
                digest = _hash(contents)
                old = baseline.get(path)
                action = {"path": path, "packaged_source": source, "packaged_sha256": digest,
                          "applied": False}
                if current == contents:
                    action["action"] = "current" if old == {"sha256": digest, "version": __version__} else "adopt"
                elif initial:
                    action.update(action="preserve", reason="missing" if current is None else "unmanaged_local")
                elif current is None:
                    action["action"] = "add"
                elif accept is not None:
                    action.update(action="accepted", reason="modified_local" if old else "unmanaged_local")
                elif old and _hash(current) == old["sha256"]:
                    action["action"] = "update"
                elif old and digest == old["sha256"]:
                    # Only this side changed: nothing to merge, so not a conflict.
                    action.update(action="customized", note=_CUSTOMIZED)
                else:
                    action.update(action="preserve", reason="modified_local" if old else "unmanaged_local",
                                  suggestion=_SUGGEST_MERGE if old else _SUGGEST_UNMANAGED)
                if action["action"] in ("add", "update") and not check and accept is None:
                    if _publish(root_fd, path, contents, raw):
                        action["applied"] = True
                    else:
                        action.update(action="preserve", reason="changed_during_update",
                                      suggestion="The file changed during this run. Review it and rerun the update.")
                if action["action"] in ("adopt", "current", "accepted") or (
                        action["action"] in ("add", "update") and accept is None):
                    # Check mode builds a proposal only; actual publication is never claimed.
                    updated[path] = {"sha256": digest, "version": __version__}
                    if action["action"] in ("adopt", "accepted") and not check:
                        action["applied"] = True
                actions.append(action)
            for row, old, new, raw in stale:
                if not check and _read(root_fd, new) is not None:
                    row["applied"] = _remove(root_fd, old, raw)
            renamed = set(_renames()) if whole else set()
            for path in sorted((set(baseline) | off_mode) - set(bundle) - renamed):
                if only is not None and path not in only:
                    continue
                if path in off_mode:
                    if _read(root_fd, path) is None:
                        # `workspace ai-access` removed it when the workspace left that mode.
                        updated.pop(path, None)
                        continue
                    actions.append({"path": path, "action": "retired", "applied": False,
                                    "suggestion": "This rule describes an AI access mode the workspace is not in; "
                                                  "left untouched. Remove it, or set the mode again with "
                                                  "torque workspace ai-access."})
                    continue
                actions.append({"path": path, "action": "retired", "applied": False,
                                "suggestion": "No longer bundled; left untouched. Review or remove it locally if appropriate."})
            notes = {}
            if whole:
                row, note = _claude_md(root_fd, check)
                if row:
                    actions.append(row)
                if note:
                    notes["claude_md"] = note
            next_manifest = {"schema": _SCHEMA, "files": {k: v for k, v in updated.items() if not _derived(k)}}
            derived = {k: v for k, v in updated.items() if _derived(k)}
            if derived:
                next_manifest[_DERIVED_SECTION] = derived
            encoded = (json.dumps(next_manifest, indent=2, sort_keys=True) + "\n").encode()
            changed_manifest = not check and encoded != raw_manifest
            if changed_manifest and not _publish(root_fd, MANIFEST, encoded, raw_manifest):
                raise ws.WorkspaceError("template manifest changed during update; completed file updates are preserved, and a retry will reconcile them")
            return {"schema": "torque.template-update/1", "workspace": str(root), "check": check,
                    "package_version": __version__, "manifest": str(root / MANIFEST),
                    "manifest_updated": changed_manifest, "actions": actions,
                    "counts": dict(Counter(item["action"] for item in actions)),
                    "conflicts": [item for item in actions if item["action"] == "preserve" and item.get("reason") != "missing"],
                    **notes}
    finally:
        _close_root(root_fd)


def record_initial_templates(root: Path) -> dict:
    """Record only byte-matching defaults after workspace materialization; no workflow writes."""
    return _run(root, check=False, initial=True)


def record_mode_rules(root: Path) -> dict | None:
    """After `workspace ai-access` wrote or removed a mode's rule files
    (workspace._mode_rules): record the ones that match the packaged text and
    forget the ones it removed, so a later update can tell an untouched rule from
    an edited one. Writes the manifest only, and only in a workspace that has one."""
    if not (Path(root) / MANIFEST).is_file():
        return None
    return _run(root, check=False, initial=True, only=ws.mode_rule_paths())


def update_templates(workspace: Path, check: bool = False) -> dict:
    """Refresh unchanged defaults/add missing resources, or inspect without writing."""
    return _run(workspace, check=check)


def accept_templates(workspace: Path, paths) -> dict:
    """Keep each named local file (or every managed file in a named folder) that
    differs from its packaged text, recording that packaged text as its base: the
    file stops being a conflict until the packaged text changes again. Writes only
    the manifest; no workflow file is changed."""
    return _run(workspace, check=False, accept=list(paths))


def template_diff(workspace: Path, path) -> str:
    """A unified diff from the packaged text of a managed file to the local file,
    line endings aside. Writes nothing."""
    if Path(workspace).expanduser().is_symlink():
        raise ws.WorkspaceError("workspace root must not be a symlink")
    root, config = ws.load_workspace(workspace)
    bundle = _bundled(ws.access_mode(config))
    relative = _workspace_relative(root, path)
    if relative not in bundle:
        raise ws.WorkspaceError(f"{path} is not a managed packaged file; the upgrade report lists them")
    contents, source = bundle[relative]
    root_fd = _open_root(root)
    try:
        raw = _read(root_fd, relative)
    finally:
        _close_root(root_fd)
    if raw is None:
        return f"{relative} does not exist here; `torque workspace upgrade` installs the packaged copy ({source}).\n"
    packaged = contents.decode("utf-8").splitlines(keepends=True)
    local = _lf(raw).decode("utf-8", "replace").splitlines(keepends=True)
    if local == packaged:
        endings = " apart from line endings" if raw != _lf(raw) else ""
        return f"No difference{endings} between {relative} and the packaged copy ({source}).\n"
    return "".join(difflib.unified_diff(packaged, local, fromfile=f"packaged {source}", tofile=f"local {relative}"))
