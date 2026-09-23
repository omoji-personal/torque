"""Update packaged workflow defaults while preserving local customizations.

Per-workspace flock serializes cooperating updaters. Directory-fd operations
reject symlinks; publication is atomic per file, followed by an atomic manifest.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
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


def _valid_workflow(path: str) -> bool:
    return (isinstance(path, str) and path.endswith(".md") and "\\" not in path
            and all(part not in ("", ".", "..") for part in path.split("/"))
            and any(path.startswith(prefix + "/") for _, prefix in _GROUPS))


def _bundled() -> dict[str, tuple[bytes, str]]:
    data = resources.files("torque").joinpath("data")
    files = {}
    def visit(item, relative: str, source: str) -> None:
        if item.is_dir():
            for child in sorted(item.iterdir(), key=lambda entry: entry.name):
                if child.name in ("", ".", "..") or "/" in child.name or "\\" in child.name:
                    raise ws.WorkspaceError("invalid bundled workflow path")
                visit(child, relative + "/" + child.name, source + "/" + child.name)
        elif item.is_file() and item.name.endswith(".md"):
            if not _valid_workflow(relative):
                raise ws.WorkspaceError("bundled workflow is outside the managed locations")
            files[relative] = (item.read_text(encoding="utf-8").encode("utf-8"), source)
    for group, destination in _GROUPS:
        visit(data.joinpath(group), destination, "torque/data/" + group)
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
            raise ws.WorkspaceError(f"workflow path is not a regular file: {name}")
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
                    handle = open(lock_path, "rb")
                else:
                    try:
                        handle = open(lock_path, "xb")
                    except FileExistsError:
                        handle = open(lock_path, "r+b")
            except FileNotFoundError:
                if check:
                    yield
                    return
                raise ws.WorkspaceError("template lock disappeared during update; rerun after local filesystem changes finish") from None
            try:
                if lock_path.is_symlink() or not lock_path.is_file():
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
    if raw is None:
        return {"schema": _SCHEMA, "files": {}}
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("schema") != _SCHEMA or not isinstance(value.get("files"), dict):
            raise ValueError("invalid schema")
        for name, entry in value["files"].items():
            if (not _valid_workflow(name) or not isinstance(entry, dict)
                    or not isinstance(entry.get("sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
                    or not isinstance(entry.get("version"), str) or not entry["version"]):
                raise ValueError("invalid file entry")
        return value
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
                with open(temp_path, "xb") as stream:
                    stream.write(contents)
                    stream.flush()
                    os.fsync(stream.fileno())
                if _read_at(fd, name) != expected:
                    return False
                if expected is None:
                    try:
                        os.link(temp_path, target)
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
                    os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
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


def _open_root(root: Path):
    """Return an opaque container handle for `root`: a dir fd on POSIX, the
    Path itself on Windows (which has no dir_fd-relative open)."""
    if _WINDOWS:
        return root
    return os.open(root, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)


def _close_root(handle) -> None:
    if not _WINDOWS:
        os.close(handle)


def _run(workspace: Path, *, check: bool, initial: bool = False) -> dict:
    # Reject a symlink at the user-supplied root before load_workspace resolves it.
    if Path(workspace).expanduser().is_symlink():
        raise ws.WorkspaceError("workspace root must not be a symlink")
    root, _ = ws.load_workspace(workspace)
    bundle = _bundled()  # Read the whole installed bundle before any mutation.
    root_fd = _open_root(root)
    try:
        with _locked(root_fd, check):
            raw_manifest = _read(root_fd, MANIFEST)
            manifest = _load_manifest(raw_manifest)
            baseline = manifest["files"]
            updated = dict(baseline)
            actions = []
            for path, (contents, source) in sorted(bundle.items()):
                current = _read(root_fd, path)
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
                elif old and _hash(current) == old["sha256"]:
                    action["action"] = "update"
                else:
                    action.update(action="preserve", reason="modified_local" if old else "unmanaged_local",
                                  suggestion="Compare with packaged_source, merge desired changes locally, and rerun; differing local files are never automatically replaced.")
                if action["action"] in ("add", "update") and not check:
                    if _publish(root_fd, path, contents, current):
                        action["applied"] = True
                    else:
                        action.update(action="preserve", reason="changed_during_update",
                                      suggestion="The file changed during this run. Review it and rerun the update.")
                if action["action"] in ("add", "update", "adopt", "current"):
                    # Check mode builds a proposal only; actual publication is never claimed.
                    updated[path] = {"sha256": digest, "version": __version__}
                    if action["action"] == "adopt" and not check:
                        action["applied"] = True
                actions.append(action)
            for path in sorted(set(baseline) - set(bundle)):
                actions.append({"path": path, "action": "retired", "applied": False,
                                "suggestion": "No longer bundled; left untouched. Review or remove it locally if appropriate."})
            next_manifest = {"schema": _SCHEMA, "files": updated}
            encoded = (json.dumps(next_manifest, indent=2, sort_keys=True) + "\n").encode()
            changed_manifest = not check and encoded != raw_manifest
            if changed_manifest and not _publish(root_fd, MANIFEST, encoded, raw_manifest):
                raise ws.WorkspaceError("template manifest changed during update; completed file updates are preserved, and a retry will reconcile them")
            return {"schema": "torque.template-update/1", "workspace": str(root), "check": check,
                    "package_version": __version__, "manifest": str(root / MANIFEST),
                    "manifest_updated": changed_manifest, "actions": actions,
                    "counts": dict(Counter(item["action"] for item in actions)),
                    "conflicts": [item for item in actions if item["action"] == "preserve" and item.get("reason") != "missing"]}
    finally:
        _close_root(root_fd)


def record_initial_templates(root: Path) -> dict:
    """Record only byte-matching defaults after workspace materialization; no workflow writes."""
    return _run(root, check=False, initial=True)


def update_templates(workspace: Path, check: bool = False) -> dict:
    """Refresh unchanged defaults/add missing resources, or inspect without writing."""
    return _run(workspace, check=check)
