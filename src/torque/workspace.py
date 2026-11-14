"""Local workspace records. No credentials, org calls, global settings, or inferred QA."""
from __future__ import annotations

from contextlib import contextmanager
import errno
import hashlib
from importlib import resources
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shlex
import stat
import shutil
import tempfile
from datetime import datetime, timezone
from uuid import uuid4

PROFILES = ("generic", "solution-lead")
STATUSES = ("prepared", "executed", "verified", "incomplete", "accepted")
# What a check rested on (`session add --check`): what was sent read back, a script or
# test, a page rendered and looked at, the real entry point used live, or a review.
CHECK_KINDS = ("read-back", "scripted", "rendered", "live", "reviewed")
CHECK_FIELDS = ("kinds", "as_user", "org", "org_type", "not_covered")
FIELD_LIMIT = 1000
AI_ACCESS_MODES = ("full", "build-only", "connected")
APPROVAL_VALUES = ("required",)
APPROVAL_VERIFY = ("hmac", "owner-uid")
CONFIG = "workspace.json"
SCHEMA = "torque.workspace/1"
# The schema a workspace has while guarded reads are on (docs/guarded-reads.md). A
# Torque from before guarded reads does not know it and refuses to read or change
# the workspace: it cannot take the workspace out of connected mode, and its gate,
# which lacks the fixes guarded reads rely on, blocks every org call there.
SCHEMA_GUARDED = "torque.workspace/2"
GUARDED_READS = "guarded_reads"
MAINTENANCE_FLAG = ".torque/maintenance"
_SESSION_ID = re.compile(r"[0-9]{8}T[0-9]{12}Z-[a-f0-9]{12}\Z")
_CHANGE_ID = re.compile(r"chg-[a-f0-9]{12}\Z")


class WorkspaceError(ValueError):
    """An actionable local configuration or input error. `code` names the kind of
    mistake for an agent (`torque.error/1`, docs/engagement-records.md, "Errors"); `next_command` is the
    command that helps, when there is one."""

    def __init__(self, message: str = "", *, code: str = "invalid_input", next_command: str | None = None):
        super().__init__(message)
        self.code = code
        self.next_command = next_command


class BoundaryError(WorkspaceError):
    """A path or record outside the selected engagement: a symlink, a path outside
    the workspace, or a record that names another client or initiative. Readers that
    skip a damaged record (`problems`) still stop on this."""

    def __init__(self, message: str = "", *, code: str = "outside_engagement", next_command: str | None = None):
        super().__init__(message, code=code, next_command=next_command)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def slug_for(name: str) -> str:
    name = name.strip()
    if not name or "/" in name or "\\" in name or ".." in name:
        raise WorkspaceError("client name must be nonempty and cannot contain paths or '..'")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug or len(slug) > 80:
        raise WorkspaceError("client name must produce a slug of 1–80 letters, digits or hyphens")
    return slug


def _inside(root: Path, path: Path) -> Path:
    """Keep client-owned paths within their root; do not follow swapped symlinks."""
    root = root.resolve()
    try:
        parts = path.relative_to(root).parts
    except ValueError as exc:
        raise BoundaryError("path is outside the selected workspace") from exc
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise BoundaryError(f"workspace path must not be a symlink: {current}")
    return path


ENGAGEMENT_FOLDERS = ("clients", "initiatives")


def _case_insensitive(root: Path) -> bool:
    """True when root's filesystem ignores case, probed with an engagement folder
    that exists (so CLIENTS/beta is the same folder as clients/beta)."""
    for folder in ENGAGEMENT_FOLDERS:
        path = root / folder
        variant = root / folder.upper()
        try:
            if path.is_dir():
                return variant.exists() and os.path.samefile(path, variant)
        except OSError:
            return False
    return False


def foreign_engagement(own: Path, path: Path) -> bool:
    """path lies inside another client or initiative of own's workspace. On a
    case-insensitive filesystem, names are compared without case."""
    root = own.parent.parent
    fold = _case_insensitive(root)

    def key(p: Path) -> tuple[str, ...]:
        return tuple(os.path.normcase(part).casefold() for part in p.parts) if fold else p.parts

    own_key, path_key, root_key = key(own), key(path), key(root)
    if path_key[:len(own_key)] == own_key:
        return False
    return any(len(path_key) > len(root_key) + 1 and path_key[:len(root_key) + 1] == root_key + key(Path(folder))
               for folder in ENGAGEMENT_FOLDERS)


def _read_json(path: Path, missing: str | WorkspaceError | None = None) -> dict:
    """The JSON object in `path`. With `missing`, a file that does not exist raises
    that message (or that error), so a typo or a wrong folder does not read as damaged data."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        if isinstance(missing, WorkspaceError):
            raise missing from exc
        raise WorkspaceError(missing or f"cannot read valid local configuration: {path}") from exc
    except (OSError, ValueError) as exc:
        raise WorkspaceError(f"cannot read valid local configuration: {path}") from exc
    if not isinstance(value, dict):
        raise WorkspaceError(f"expected a JSON object: {path}")
    return value


def _fsync_dir(directory: Path) -> None:
    """Make a rename or link in directory durable. Windows has no directory fsync."""
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# What os.link answers on a filesystem without hard links (Google Drive for
# desktop, exFAT, some network shares): Windows ERROR_INVALID_FUNCTION and
# ERROR_NOT_SUPPORTED; elsewhere the "not permitted" and "not supported" family.
_NO_LINK_WINERRORS = (1, 50)
_NO_LINK_ERRNOS = frozenset(getattr(errno, name) for name in
                            ("EPERM", "EACCES", "ENOTSUP", "EOPNOTSUPP", "ENOSYS", "EMLINK")
                            if hasattr(errno, name))


def links_unsupported(exc: OSError) -> bool:
    """True when os.link failed because the filesystem cannot make hard links."""
    return not isinstance(exc, FileExistsError) and (
        getattr(exc, "winerror", None) in _NO_LINK_WINERRORS or exc.errno in _NO_LINK_ERRNOS)


def publish_new(temporary, path, *, dir_fd: int | None = None) -> None:
    """Give the complete file `temporary` the name `path` in the same folder,
    never replacing an existing file: FileExistsError when `path` exists.

    A hard link does that in one step. Where the filesystem refuses hard links,
    Windows renames (its rename never replaces a file); elsewhere the name is
    claimed with an exclusive create and the complete file is moved onto that
    empty claim. `dir_fd` makes both names relative to an open folder (POSIX)."""
    at = {} if dir_fd is None else {"dir_fd": dir_fd}
    both = {} if dir_fd is None else {"src_dir_fd": dir_fd, "dst_dir_fd": dir_fd}
    try:
        os.link(temporary, path, **both, **({} if dir_fd is None else {"follow_symlinks": False}))
        return
    except OSError as exc:
        if not links_unsupported(exc):
            raise
    if os.name == "nt":
        os.rename(temporary, path)
        return
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, **at))
    try:
        os.replace(temporary, path, **both)
    except OSError:
        # Withdraw this call's own empty claim; anything else there is not ours.
        try:
            if os.stat(path, follow_symlinks=False, **at).st_size == 0:
                os.unlink(path, **at)
        except OSError:
            pass
        raise


# A word PowerShell, cmd.exe and a POSIX shell (Git Bash on Windows too) all read as itself.
_PLAIN_WORD = re.compile(r"[A-Za-z0-9_+=:./-]+")
# What double quotes do not protect in one of them: PowerShell expands `$` and a backtick and
# ends the string at any double quote (the curly ones too), cmd.exe expands `%`, and an
# interactive Bash expands `!`.
_NOT_IN_DOUBLE_QUOTES = frozenset('$`%"!“”„')
_SINGLE_QUOTES = re.compile("['‘’‚‛]")


def _windows_word(word: str) -> str:
    """One word for PowerShell and cmd.exe: as it is when neither reads anything in it, else
    in double quotes, which both read (with a backslash run before the closing quote doubled,
    as programs expect; Windows PowerShell hands a word on in quotes only when it holds a
    space, so a word ending in a backslash and holding none cannot be written that way). A
    word double quotes cannot carry goes in PowerShell's single quotes, each single quote
    doubled; only PowerShell reads that one."""
    if _PLAIN_WORD.fullmatch(word):
        return word
    if not _NOT_IN_DOUBLE_QUOTES & set(word) and (not word.endswith("\\") or re.search(r"\s", word)):
        return '"' + re.sub(r"(\\+)$", r"\1\1", word) + '"'
    return "'" + _SINGLE_QUOTES.sub(lambda found: found.group() * 2, word) + "'"


def command_text(argv: list[str], *, windows: bool | None = None) -> str:
    """A command line the user can paste into their own shell: on Windows (or with `windows`)
    one that PowerShell and cmd.exe read the same way (_windows_word), elsewhere POSIX
    quoting. subprocess.list2cmdline left a word without a space unquoted, so PowerShell
    read an apostrophe, a `;` or a `$` in a path as its own syntax."""
    if os.name == "nt" if windows is None else windows:
        return " ".join(_windows_word(word) for word in argv)
    return shlex.join(argv)


def create_private(path) -> int:
    """A new file for writing that no other account can reach, from its first byte: on
    Windows it is created with an owner-only security descriptor (the owner is the current
    user; nothing inherited), elsewhere with mode 0600. Returns a file descriptor."""
    if os.name == "nt":
        from jsc_common import winacl
        return winacl.create_private(path)
    return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)


def copy_private(source, target, *, follow_symlinks: bool = True) -> str:
    """Copy a file's content to a new `target` created private (create_private), so the
    content is never behind a handle that predates the protection."""
    with open(source, "rb") as origin, os.fdopen(create_private(target), "wb") as copy:
        shutil.copyfileobj(origin, copy)
    return str(target)


def _temporary_file(parent: Path, private: bool) -> tuple[int, str]:
    """A fresh temporary file in `parent`; with `private`, one made by create_private on
    Windows (elsewhere mkstemp's 0600 already is). Otherwise the folder's access applies."""
    if private and os.name == "nt":
        name = str(parent / (".torque-" + uuid4().hex))
        return create_private(name), name
    return tempfile.mkstemp(prefix=".torque-", dir=parent)


def atomic_write_new(path: Path, text: str, *, private: bool = False) -> None:
    """Publish one complete private file atomically, without replacing an existing file.
    `private`: on Windows the file is created with an owner-only descriptor (create_private);
    otherwise it inherits the folder's access."""
    if not path.parent.is_dir():
        raise WorkspaceError(f"output directory does not exist: {path.parent}")
    fd, temporary = _temporary_file(path.parent, private)
    try:
        # newline="\n": text is always \n-separated Python-source content;
        # without this, Windows would translate it to \r\n on write, and a
        # later byte-exact comparison against LF-only bundled content (see
        # template_updates.py) would see every materialized file as changed.
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            publish_new(temporary, path)
        except FileExistsError as exc:
            raise WorkspaceError(f"file already exists; choose a new path: {path}") from exc
        _fsync_dir(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


# V2 I6: the extended attribute holding a file's POSIX access list (Linux). It
# exists only when the file has more than the three mode-bit entries, for example
# when its folder carries a default entry for the approver account.
ACL_XATTR = "system.posix_acl_access"


def open_acl_mask(path) -> None:
    """V2 I6: on a file or folder with an extended POSIX access list, add group
    read (and search, for a folder) to its mode bits. With an access list, the
    group bits are the access-list mask, and a file created 0600 (a folder 0700)
    in a folder with a default entry gets an empty mask, which hides that entry.
    Adding the bits sets the mask; who may read is still decided by the entries
    the default access list gave the path (the owning group's entry included),
    never by the bits alone. No-op where `os.getxattr` does not exist (macOS,
    Windows), for a path without an extended access list, and for a link."""
    getxattr = getattr(os, "getxattr", None)
    if getxattr is None or os.name == "nt":
        return
    try:
        found = os.lstat(path)
        if stat.S_ISLNK(found.st_mode):
            return
        getxattr(path, ACL_XATTR, follow_symlinks=False)
    except OSError:
        return
    bits = 0o050 if stat.S_ISDIR(found.st_mode) else 0o040
    os.chmod(path, (found.st_mode & 0o7777) | bits)


def share_with_approver(workspace, *paths) -> None:
    """V2 I6: `open_acl_mask` on each path the delegated approver reads (the
    agent's requests and change records and the folders Torque creates for
    them), only in a delegated tier 2 workspace. A workspace without a delegated
    approver, macOS and Windows keep the private 0600 and 0700 modes."""
    if getattr(os, "getxattr", None) is None or os.name == "nt" or not paths:
        return
    from . import delegation
    try:
        config = load_workspace(workspace)[1]
    except (OSError, WorkspaceError):
        return
    if not delegation.delegated_tier2(config):
        return
    for path in paths:
        open_acl_mask(path)


def _atomic_replace_text(path: Path, text: str, *, keep_mode: bool = False, private: bool = False) -> None:
    """Replace an explicitly managed file without exposing a partial write.

    By default the replacement is a fresh, private file (mkstemp's 0600), the
    same result callers relied on before durability work touched this
    function: a caller that manages its own mode afterward (set_ai_access,
    _connected_rule) must not have that mode silently overridden by whatever
    the file happened to be before. Pass keep_mode=True only when an existing
    file's mode and group must survive the replace (a shared record stays
    shared)."""
    existing = None
    if keep_mode:
        try:
            existing = os.stat(path)
        except FileNotFoundError:
            existing = None
    fd, temporary = _temporary_file(path.parent, private and not keep_mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if existing is not None and os.name != "nt":
            os.chmod(temporary, stat.S_IMODE(existing.st_mode))
            try:
                os.chown(temporary, -1, existing.st_gid)
            except OSError:
                pass
        os.replace(temporary, path)
        _fsync_dir(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _write_json(path: Path, value: dict, *, private: bool = False) -> None:
    atomic_write_new(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n", private=private)


def _torque_project(path: Path) -> bool:
    """Recognize this project's explicit identity, including on Python 3.10."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    try:
        import tomllib
    except ModuleNotFoundError:
        # The shipped pyproject uses a simple [project].name assignment. On 3.10
        # avoid treating text inside a multiline TOML value as a declaration.
        if '\"\"\"' in text or "'''" in text:
            return False
        section = None
        for raw in text.splitlines():
            line = raw.strip()
            header = re.fullmatch(r"\[([^\[\]]+)\]\s*(?:#.*)?", line)
            if header:
                section = header[1].strip()
            elif section == "project":
                name = re.fullmatch(r'''name\s*=\s*(["'])([A-Za-z0-9_.-]+)\1\s*(?:#.*)?''', line)
                if name:
                    return re.sub(r"[-_.]+", "-", name[2]).lower() == "torque-salesforce"
        return False
    try:
        project = tomllib.loads(text).get("project", {})
        name = project.get("name") if isinstance(project, dict) else None
        return isinstance(name, str) and re.sub(r"[-_.]+", "-", name).lower() == "torque-salesforce"
    except ValueError:
        return False


def _checkout_containing(path: Path) -> Path | None:
    for ancestor in (path, *path.parents):
        if ((ancestor / "src/torque/__init__.py").is_file()
                and (ancestor / "src/torque/workspace.py").is_file()
                and _torque_project(ancestor / "pyproject.toml")):
            return ancestor
    return None


def _source_checkout() -> Path | None:
    return _checkout_containing(Path(__file__).resolve())


def claude_copy(group: str, name: str, text: str) -> tuple[str, str]:
    """The file name and text of Claude Code's copy of a packaged file in `group`
    (commands, rules, skills or agents). A recipe named like a command Claude Code
    answers itself (hosts.CLAUDE.builtin_commands) is installed as torque-NAME, and
    recipes, rules and worker roles name it that way. Skills are copied as they are."""
    from . import hosts
    if group == "skills":
        return name, text
    if group == "commands" and name.endswith(".md"):
        name = hosts.recipe_name(hosts.CLAUDE, name[:-len(".md")]) + ".md"
    return name, hosts.rename_mentions(hosts.CLAUDE, text)


def _materialize_workflows(root: Path) -> None:
    """Install packaged conversation files in this workspace, preserving existing local files."""
    try:
        data = resources.files("torque").joinpath("data")
    except (ModuleNotFoundError, TypeError):
        return

    def copy_tree(source, destination: Path, group: str, claude: bool) -> None:
        if not source.is_dir():
            return
        _inside(root, destination).mkdir(parents=True, exist_ok=True, mode=0o700)
        for item in source.iterdir():
            if item.is_dir():
                copy_tree(item, _inside(root, destination / item.name), group, claude)
            elif item.is_file() and item.name.endswith(".md"):
                name, text = item.name, item.read_text(encoding="utf-8")
                if claude:
                    name, text = claude_copy(group, name, text)
                target = _inside(root, destination / name)
                if not target.exists():
                    atomic_write_new(target, text)

    for group in ("commands", "rules", "skills", "agents"):
        copy_tree(data.joinpath(group), root / ".claude" / group, group, True)
    copy_tree(data.joinpath("skills"), root / ".agents" / "skills", "skills", False)
    # Antigravity reads .agents/ only: the same rules, recipes and worker roles in its format.
    from . import antigravity
    for relative, (text, _source) in antigravity.surface(data).items():
        target = _inside(root, root / relative)
        _inside(root, target.parent).mkdir(parents=True, exist_ok=True, mode=0o700)
        if not target.exists():
            atomic_write_new(target, text)


# The CLAUDE.md init writes. With both files present Claude Code reads CLAUDE.md only,
# so it imports AGENTS.md (code.claude.com/docs/en/memory); an import loads every
# session, a sentence asking the model to read the file does not.
CLAUDE_NOTES = "@AGENTS.md\n"
# What versions up to 2.0.0a21 wrote there; `workspace upgrade` replaces it while unchanged.
LEGACY_CLAUDE_NOTES = ("# Torque workspace\n\nRead AGENTS.md in this directory for the shared workflow, "
                       "then use `torque context --workspace . --client NAME` for the selected client.\n",)


def init_workspace(path: str | Path, name: str, profile: str = "generic") -> Path:
    if not name.strip():
        raise WorkspaceError("workspace name must be nonempty")
    if profile not in PROFILES:
        raise WorkspaceError(f"unknown profile: {profile}")
    target = Path(path).expanduser()
    if target.is_symlink():
        raise WorkspaceError("workspace root must not be a symlink")
    root = target.resolve()
    source = _source_checkout()
    if source is not None:
        source = source.resolve()
    if (source and (root == source or source in root.parents)) or _checkout_containing(root):
        raise WorkspaceError("choose a private workspace outside the Torque source checkout")
    if (root / CONFIG).exists():
        raise WorkspaceError(f"workspace already initialized: {root}")
    if root.exists() and not root.is_dir():
        raise WorkspaceError(f"workspace path is not a directory: {root}")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    clients = _inside(root, root / "clients")
    clients.mkdir(mode=0o700, exist_ok=True)
    firm = {"schema": SCHEMA, "name": name.strip(),
            "profile": profile, "created_at": _now()}
    focus_notes = ""
    if profile == "solution-lead":
        firm["role"] = "Salesforce Solution Lead"
        firm["delivery_focus"] = ["discovery", "business analysis", "solution design and configuration",
                                  "QA and UAT", "support", "training", "small project coordination"]
        focus_notes = (
            "## Delivery focus\n\nRole: Salesforce Solution Lead.\n\n"
            "Use this workspace for discovery and business analysis; solution design and "
            "Salesforce configuration; QA and UAT; support and diagnosis; client training; "
            "and coordination of small projects. Keep requirements, decisions, configuration "
            "changes, validation evidence and client-facing handoffs connected within each client.\n\n"
            "For discovery, capture the process, stakeholders, current pain points and acceptance "
            "criteria. For delivery, retain the proposed approach, actual changes and outstanding "
            "questions. For support, record reproducible symptoms and observed results. For "
            "training, preserve the intended audience and practical task steps. For coordination, "
            "record owners, agreed next actions and dependencies without inventing commitments.\n\n"
            "These are editable working defaults, not statements of the firm's approved policies.\n\n"
        )
    ignore = _inside(root, root / ".gitignore")
    if not ignore.exists():
        atomic_write_new(ignore, "# Private client data and artifacts\n*\n")
    else:
        original = ignore.read_text(encoding="utf-8")
        private_rules = ["/clients/", "/initiatives/", "/workspace.json", "/profile.md", "/.torque/"]
        if any(rule not in original.splitlines() for rule in private_rules):
            updated = original.rstrip("\n") + "\n\n# Torque private workspace\n" + "\n".join(private_rules) + "\n"
            _atomic_replace_text(ignore, updated)
    agent_notes = (
        "# Private Torque workspace\n\n"
        "This directory holds private employer and client work. Begin by reading profile.md. "
        "Select the client the user named; never infer another client or org. From this directory, "
        "use `torque context --workspace . --client NAME` to resume that client's notes and journal.\n\n"
        "Discover the working recipes with `torque workflows`, then read one with "
        "`torque workflows show NAME --workspace .`. An explicitly selected workspace uses the local "
        "copy its host runs when present (.claude/commands/NAME.md for Claude Code, "
        ".agents/skills/NAME/SKILL.md for Antigravity, torque-NAME for the recipes renamed below), "
        "then the packaged recipe written for that host. "
        "Without a selected workspace, show reads the packaged reference. These recipes support conversational work; "
        "use the user's actual authorization and existing Salesforce access for operations. "
        "Where Torque's gate hook is wired (`torque doctor --workspace .` says where), it stops tools from "
        "changing a client's consent, approval and control records in every mode; in build-only or connected "
        "mode it checks each tool call, and the rule file in the rules folder (`build-only.md` or "
        "`production-approval.md`) says what to expect. No approval tokens or global sf replacement are required. "
        "Claude Code answers /help, /context, /status, /security-review and /undo itself, so there those "
        "recipes are /torque-help, /torque-context, /torque-status, /torque-security-review and /torque-undo. "
        "Antigravity offers the same recipes as skills and slash commands from .agents/skills/; "
        "its own /help, /context and /undo keep those names, so there those three recipes are "
        "/torque-help, /torque-context and /torque-undo.\n\n"
        "Keep client files in clients/SLUG/. Use --workspace . --client SLUG when calling "
        "stateful native workflows. Keep internal work (not for a client) in initiatives/SLUG/, "
        "created with `torque initiative add NAME --workspace .`, and select it with --initiative SLUG. "
        "Keep credentials out of notes and session summaries. "
        "Record progress with `torque session add --workspace . --client NAME --summary TEXT "
        "--status prepared|executed|verified|incomplete`. Status is user-reported; distinguish "
        "actual evidence, assertions and unanswered questions. Use `torque handoff` to render "
        "the recorded history. Nothing here implies organizational approval or completed QA.\n"
    )
    for filename, content in {
        "AGENTS.md": agent_notes,
        "CLAUDE.md": CLAUDE_NOTES,
        "profile.md": f"# {name.strip()}\n\nProfile: {profile}\n\n"
                      + focus_notes + "Record the firm's actual delivery conventions, approved tools, artifact locations "
                      "and relevant policies here. This starter does not assume they are already agreed.\n",
    }.items():
        if not (root / filename).exists():
            atomic_write_new(root / filename, content)
    _materialize_workflows(root)
    # Publish the configuration last: an interrupted first-run can resume safely.
    _write_json(root / CONFIG, firm)
    from .template_updates import record_initial_templates
    record_initial_templates(root)
    return root


def _validate_config(config: dict, path: Path) -> dict:
    """The workspace.json schema check, factored out so a caller that already
    holds a parsed dict from its own protected read (delegation.py's
    single-descriptor read, avoiding a TOCTOU between reading and checking the
    file) can reuse this instead of a second, separately-worded copy."""
    if (not isinstance(config, dict) or config.get("schema") not in (SCHEMA, SCHEMA_GUARDED)
            or not isinstance(config.get("name"), str) or not config["name"].strip()
            or config.get("profile") not in PROFILES):
        raise WorkspaceError(f"invalid workspace configuration: {path}")
    return config


def load_workspace(path: str | Path) -> tuple[Path, dict]:
    root = Path(path).expanduser().resolve()
    config_path = _inside(root, root / CONFIG)
    if not config_path.exists() and not config_path.is_symlink():
        nearest = next((folder for folder in root.parents if (folder / CONFIG).is_file()), None)
        if nearest is not None:
            raise WorkspaceError(f"this folder is not a Torque workspace (no {CONFIG} here: {root}); the nearest "
                                 f"one is {nearest}. Name it with --workspace", code="not_a_workspace",
                                 next_command=command_text(["torque", "client", "list", "--workspace", str(nearest)]))
        hint = (" This is an engagement folder; select the workspace two folders up."
                if (root / "client.json").is_file() or (root / "binding.json").is_file() else "")
        raise WorkspaceError(f"not a Torque workspace: {root} has no workspace configuration ({CONFIG}).{hint} "
                             "Select the workspace folder, or create a workspace with "
                             "`torque workspace init PATH --name NAME`.", code="not_a_workspace",
                             next_command="torque workspace init PATH --name NAME")
    config = _validate_config(_read_json(config_path), config_path)
    _inside(root, root / "clients")
    return root, config


def require_writable(root: str | Path) -> None:
    """Refuse record writes while an administrator holds the workspace in maintenance.
    `root` is the workspace directory (a str or Path, as the writers receive it)."""
    if (Path(root).expanduser() / MAINTENANCE_FLAG).exists():
        raise WorkspaceError(f"workspace is in maintenance ({MAINTENANCE_FLAG} exists); "
                             "writes are paused until it is removed")


def require_package_writable(selected: str | Path | None = None) -> None:
    """Check maintenance before starting package work, including direct entry points.

    A selected package root can be the workspace, its client, or a legacy private
    folder. Check its ancestors and the working folder's ancestors. This is an
    admission check, not cancellation: already running work must retain outcomes.
    """
    selected = selected or os.environ.get("TORQUE_WORKSPACE") or os.environ.get("JSC_ROOT")
    paths = [Path.cwd()]
    if selected:
        paths.append(Path(selected).expanduser().resolve())
    for path in paths:
        for candidate in (path, *path.parents):
            require_writable(candidate)


def _owner_uid_supported() -> bool:
    """Tier 2 checks file ownership by numeric uid, which Windows does not have."""
    return hasattr(os, "getuid")


def _owner_at_terminal(presence, what: str) -> None:
    """Require the owner at a real terminal, who types back a code (the code is
    skipped only when a caller injects its own presence check)."""
    injected = presence is not None
    if presence is None:
        from .presence import operator_present as presence
    check = presence()
    if not check.ok:
        raise WorkspaceError(f"{what} by the owner at a real terminal: {check.reason}")
    if not injected:
        from .presence import confirm_code
        if not confirm_code():
            raise WorkspaceError("the confirmation code did not match; nothing was changed")


def set_ai_access(workspace: str | Path, mode: str, approval: str | None = None,
                  verify: str | None = None, approver_uid: int | None = None, presence=None, *,
                  delegated: bool = False, model_id: str | None = None, env=None, ancestors=None,
                  getuid=None, root_owner=None) -> Path:
    """Set the workspace ai_access mode. Ordinarily only the owner calls this, at a
    real terminal; an AI session has its own edits to workspace.json blocked by the
    gate. Connected mode needs approval="required" and a person at a real terminal
    (presence).

    A connected workspace's named setup delegate may instead switch to connected
    mode without a terminal (`delegated=True`): the caller's identity comes from
    `delegation.delegated_actor`, an OS-account proof, not from presence. A
    delegated switch always sets tier 2 (`verify="owner-uid"`); anything else
    refuses with reason class "tier-2-required". The delegate's identity is
    recorded as `config["ai_access_changed_by"]`. `root_owner`, like `env`,
    `ancestors` and `getuid`, is an injectable override of
    `delegation.delegated_actor`'s own default, for tests that cannot create a
    second real OS account."""
    require_writable(workspace)
    if mode not in AI_ACCESS_MODES:
        raise WorkspaceError(f"unknown ai_access mode: {mode}")
    if model_id is not None and not delegated:
        raise WorkspaceError("--model-id applies only to a delegated call (pass --delegated too)")
    if mode != "connected" and (approval is not None or verify is not None or approver_uid is not None):
        raise WorkspaceError("--approval, --verify and --approver-uid apply only to connected mode")
    if delegated and mode != "connected":
        raise WorkspaceError("a delegated setup only sets connected mode")
    actor = None
    if mode != "connected" and access_mode(load_workspace(workspace)[1]) == "connected":
        # Leaving connected mode takes the gate off the session's org access, so it
        # needs what entering it needs: a person at a real terminal, and the code.
        _owner_at_terminal(presence, "connected mode is left")
    if mode == "connected":
        if approval not in APPROVAL_VALUES:
            raise WorkspaceError("connected mode needs --approval required")
        verify = verify or "hmac"
        if verify not in APPROVAL_VERIFY:
            raise WorkspaceError(f"unknown approval verification: {verify}")
        if verify == "owner-uid" and not _owner_uid_supported():
            raise WorkspaceError("owner-uid (tier 2) approvals are not supported on this platform; use hmac")
        if verify == "owner-uid" and (type(approver_uid) is not int or approver_uid < 0):
            raise WorkspaceError("owner-uid verification needs --approver-uid, the approver account's numeric uid")
        if verify == "hmac" and approver_uid is not None:
            raise WorkspaceError("--approver-uid applies only to owner-uid verification")
        if delegated:
            from . import delegation
            if verify != "owner-uid":
                raise delegation.Refusal("tier-2-required", "a delegated setup uses tier 2: --verify owner-uid "
                                                            "--approver-uid UID")
            # The identity proof reads workspace.json once through the protected,
            # single-descriptor reader (delegation._read_protected_config) and
            # hands the config back, so the update below reuses it rather than a
            # second, separately timed, plain path-based reopen.
            actor, delegated_config, delegated_root = delegation._delegated_actor_and_config(
                workspace, "setup", model_id=model_id, require_tier2=False, getuid=getuid, env=env,
                ancestors=ancestors, root_owner=root_owner)
        else:
            _owner_at_terminal(presence, "connected mode is set")
    if delegated:
        root, config = delegated_root, delegated_config
        _inside(root, root / "clients")
    else:
        root, config = load_workspace(workspace)
    from . import gate
    if mode == "connected" and config.get("schema") == SCHEMA_GUARDED and config.get(GUARDED_READS) == "on":
        config["ai_access"] = gate.CONNECTED_GUARDED    # guarded reads stay on, and so does how the mode is written
    else:
        # Guarded reads belong to connected mode: leaving it switches them off.
        config["ai_access"] = mode
        if config.get("schema") == SCHEMA_GUARDED:
            config.pop(GUARDED_READS, None)
            config["schema"] = SCHEMA
    for key in ("approval", "approval_verify", "approver_uid"):
        config.pop(key, None)
    if mode == "connected":
        config["approval"] = approval
        config["approval_verify"] = verify
        if verify == "owner-uid":
            config["approver_uid"] = approver_uid
    config["ai_access_changed_at"] = _now()
    if mode == "connected" and delegated:
        config["ai_access_changed_by"] = actor.as_dict()
    else:
        config.pop("ai_access_changed_by", None)
    config_path = _inside(root, root / CONFIG)
    _atomic_replace_text(config_path, json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    if delegated and os.name != "nt":
        # F12: a file the setup delegate's own uid wrote defaults to 0600 (mkstemp),
        # unreadable to the different account the gate and hooks later run as.
        # 0644 lets that account read it; the harness chowns it to root afterward.
        config_path.chmod(0o644)
    _connected_rule(root, mode == "connected", delegated=delegated)
    if not delegated:
        # A delegated setup step writes only the files delegation.SETUP_WRITES lists;
        # there, `torque workspace upgrade` adds the Antigravity copy of the rule.
        _mode_rules(root, mode)
    return root


def guarded_reads_on(config) -> bool:
    """Guarded reads are on for this workspace.json: the setting, its schema and the
    way the mode is written all say so (a hand-edited setting in an old-schema file
    counts as off)."""
    from . import gate
    return (isinstance(config, dict) and config.get("schema") == SCHEMA_GUARDED
            and config.get(GUARDED_READS) == "on" and config.get("ai_access") == gate.CONNECTED_GUARDED)


def set_guarded_reads(workspace: str | Path, value: str, presence=None) -> Path:
    """Switch guarded reads on or off for a connected workspace: the owner, at a real
    terminal, with the confirmation code. On changes the workspace's schema and the
    way its mode is written (gate.CONNECTED_GUARDED). A Torque from before guarded
    reads then refuses to load the workspace file, and its hook, which reads the mode
    without loading the file, takes the word it does not know for build-only: it
    refuses org work there instead of applying its own older reading of connected mode."""
    from . import gate
    require_writable(workspace)
    if value not in ("on", "off"):
        raise WorkspaceError("guarded reads are on or off")
    _owner_at_terminal(presence, "guarded reads are switched")
    root, config = load_workspace(workspace)
    if value == "on":
        if access_mode(config) != "connected":
            raise WorkspaceError("guarded reads work in a connected workspace; set connected mode first")
        config[GUARDED_READS], config["schema"], config["ai_access"] = "on", SCHEMA_GUARDED, gate.CONNECTED_GUARDED
    else:
        config.pop(GUARDED_READS, None)
        config["schema"] = SCHEMA
        if config.get("ai_access") == gate.CONNECTED_GUARDED:
            config["ai_access"] = "connected"
    config["guarded_reads_changed_at"] = _now()
    _atomic_replace_text(_inside(root, root / CONFIG), json.dumps(config, indent=2, ensure_ascii=False) + "\n",
                         keep_mode=True)
    return root


CONNECTED_RULE = "production-approval.md"
BUILD_ONLY_RULE = "build-only.md"
# The rule file a mode adds to each host's playbook: mode -> (its name there, where
# its packaged text is under torque/data). Full mode adds none.
MODE_RULES = {"connected": (CONNECTED_RULE, ("connected", CONNECTED_RULE)),
              "build-only": (BUILD_ONLY_RULE, (BUILD_ONLY_RULE,))}
# Claude Code's copy of the connected rule keeps the life it had before the other
# mode rules existed: `_connected_rule` writes and removes it, and `torque
# workspace upgrade` does not track it.
_CONNECTED_RULE_PATH = ".claude/rules/" + CONNECTED_RULE


def connected_rule_stale(workspace: str | Path) -> bool:
    """Claude Code's copy of the connected rule differs from this version's text.
    `torque workspace upgrade` does not track that file, so it says this instead."""
    try:
        root, config = load_workspace(workspace)
        if access_mode(config) != "connected":
            return False
        packaged = resources.files("torque").joinpath("data", "connected", CONNECTED_RULE).read_text(encoding="utf-8")
        return (root / ".claude" / "rules" / CONNECTED_RULE).read_text(encoding="utf-8") != packaged
    except (OSError, ValueError, WorkspaceError):
        return False


def access_mode(config: dict) -> str:
    """The mode the gate enforces for this workspace.json: `full` when it names
    none, otherwise the gate's own reading of the value (what is neither full nor
    connected with required approval is build-only)."""
    from . import gate
    return gate._resolve_ai_access(config["ai_access"], config.get("approval")) if "ai_access" in config else "full"


def mode_rule_paths() -> frozenset[str]:
    """Every workspace path `mode_rules` can name, whatever the mode."""
    from . import hosts
    return frozenset(f"{host.playbook}/rules/{name}" for host in hosts.HOSTS
                     for name, _ in MODE_RULES.values()) - {_CONNECTED_RULE_PATH}


def mode_rules(mode: str, data=None) -> dict[str, tuple[str, str]]:
    """{workspace path: (text, packaged source)} for the rule files `mode` adds
    beside Claude Code's connected rule: the build-only rule in each host's
    playbook, and the Antigravity copy of the connected rule. Empty in full mode.
    `data` is the packaged data folder (torque/data)."""
    if mode not in MODE_RULES:
        return {}
    from . import antigravity, hosts
    name, packaged = MODE_RULES[mode]
    data = resources.files("torque").joinpath("data") if data is None else data
    text = data.joinpath(*packaged).read_text(encoding="utf-8")
    source = "torque/data/" + "/".join(packaged)
    out: dict[str, tuple[str, str]] = {}
    for host in hosts.HOSTS:
        relative = f"{host.playbook}/rules/{name}"
        if relative == _CONNECTED_RULE_PATH:
            continue
        if host is hosts.ANTIGRAVITY:
            # Antigravity discards a rule without its header (antigravity.rule).
            copy = antigravity.rule(name[:-len(".md")], text)
            if copy is not None:
                out[relative] = (copy, source + " (Antigravity copy)")
        else:
            out[relative] = (text, source)
    return out


def _mode_rules(root: Path, mode: str) -> None:
    """Write the rule files `mode` adds (`mode_rules`) and remove those of the
    other modes, as `_connected_rule` does for Claude Code's connected rule: a
    rule that says what a mode means stays in a playbook only while the workspace
    is in that mode. The rule tells the agent what to expect; the gate, not the
    rule, is what blocks a call."""
    wanted = mode_rules(mode)
    for relative in sorted(mode_rule_paths() - set(wanted)):
        _inside(root, root / relative).unlink(missing_ok=True)
    for relative, (text, _source) in wanted.items():
        target = _inside(root, root / relative)
        _inside(root, target.parent).mkdir(mode=0o700, parents=True, exist_ok=True)
        if target.exists():
            _atomic_replace_text(target, text)
        else:
            atomic_write_new(target, text)
    from .template_updates import record_mode_rules
    try:
        record_mode_rules(root)
    except (OSError, WorkspaceError):
        # The record is bookkeeping for `workspace upgrade`, which adopts an
        # unchanged rule and reports a damaged manifest itself; the mode is set.
        pass


def _connected_rule(root: Path, present: bool, *, delegated: bool = False) -> None:
    """Materialize the "propose, do not act" rule in a connected workspace, and
    remove it when the workspace leaves connected mode.

    On the delegated setup path (F12 fix round 1), the rule file relaxes to
    world-readable, the same reason workspace.json and consent.json do: the
    setup delegate's own uid wrote it, unreadable to the different account
    the gate and hooks later run as; the harness chowns it to root
    afterward. `.claude/rules` and `.claude` each relax only when this call
    is the one that created them (R55, spec requirement 22): a preexisting
    directory's mode, whichever step wrote it, is never weakened as a side
    effect of this one. The owner path is unchanged."""
    target = _inside(root, root / ".claude" / "rules" / CONNECTED_RULE)
    if not present:
        target.unlink(missing_ok=True)
        return
    text = resources.files("torque").joinpath("data", "connected", CONNECTED_RULE).read_text(encoding="utf-8")
    claude_dir = target.parent.parent
    claude_existed = claude_dir.exists()
    rules_existed = target.parent.exists()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if target.exists():
        _atomic_replace_text(target, text)
    else:
        atomic_write_new(target, text)
    if delegated and os.name != "nt":
        target.chmod(0o644)
        if not rules_existed:
            target.parent.chmod(0o755)
        if not claude_existed:
            claude_dir.chmod(0o755)


@contextmanager
def _client_creation_lock(root: Path):
    """Serialize complete client publication; an interrupted attempt holds no slug."""
    private = _inside(root, root / ".torque")
    private.mkdir(mode=0o700, exist_ok=True)
    path = _inside(root, private / "client-creation.lock")
    if os.name == "nt":
        # Windows has neither O_NOFOLLOW/O_NONBLOCK nor fcntl; best-effort
        # symlink check plus msvcrt advisory locking replaces them.
        import msvcrt
        if path.is_symlink():
            raise WorkspaceError("client creation lock must be a regular file")
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise WorkspaceError("client creation lock must be a regular file")
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            yield
        finally:
            try:
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            os.close(fd)
        return
    import fcntl
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise WorkspaceError("client creation lock must be a regular file")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def add_client(workspace: str | Path, name: str, org: str | None = None) -> Path:
    root, _ = load_workspace(workspace)
    require_writable(root)
    slug = slug_for(name)
    if org is not None and (not org.strip() or any(c in org for c in "\r\n\0")):
        raise WorkspaceError("org alias must be nonempty and on one line")
    with _client_creation_lock(root):
        # clients/ is git-ignored, so a fresh clone of a private workspace has none; create it as init does.
        parent = _inside(root, root / "clients")
        parent.mkdir(mode=0o700, exist_ok=True)
        client = _inside(root, parent / slug)
        if client.exists():
            raise WorkspaceError(f"client slug already exists: {slug}; no existing data was replaced")
        staging = _inside(root, root / ".torque" / "client-staging")
        staging.mkdir(mode=0o700, exist_ok=True)
        # A killed process can leave private staging behind, but not an occupied
        # client slug. A caught failure removes only this attempt's directory.
        with tempfile.TemporaryDirectory(prefix=slug + "-", dir=staging) as temporary:
            pending = Path(temporary)
            for directory in ("sessions", "artifacts", "context", "config"):
                (pending / directory).mkdir(mode=0o700)
            _write_json(pending / "client.json", {"schema": "torque.client/1", "name": name.strip(),
                                                  "slug": slug, "org": org.strip() if org else None,
                                                  "created_at": _now()})
            atomic_write_new(pending / "context.md", f"# {name.strip()}\n\n"
                             "Record this client's current scope, decisions, contacts, constraints and "
                             "open questions here. Keep credentials out of these notes.\n")
            _inside(root, client)
            if client.exists():
                raise WorkspaceError(f"client slug already exists: {slug}; no existing data was replaced")
            pending.rename(client)
    return client


def list_clients(workspace: str | Path) -> list[dict]:
    root, _ = load_workspace(workspace)
    directory = _inside(root, root / "clients")
    if not directory.is_dir():
        return []
    clients = []
    for child in sorted(directory.iterdir()):
        _inside(root, child)
        if child.is_dir() and (child / "client.json").exists():
            clients.append(load_client(root, child.name)[2])
    return clients


def load_client(workspace: str | Path, name: str) -> tuple[Path, dict, dict]:
    root, firm = load_workspace(workspace)
    slug = slug_for(name)
    client = _inside(root, root / "clients" / slug)
    path = _inside(root, client / "client.json")
    if not path.is_file() and not path.is_symlink():
        # Only this workspace's own client folders are named, by slug, and only in full mode:
        # in build-only and connected mode a session works for one client and is not told the others.
        parent = root / "clients"
        listing = command_text(["torque", "client", "list", "--workspace", str(root)])
        if access_mode(firm) == "full":
            known = sorted(child.name for child in parent.iterdir()
                           if child.is_dir() and (child / "client.json").is_file()) if parent.is_dir() else []
            clients = "clients: " + (", ".join(known) if known else "none yet") + ". Name one by its slug, list them"
        else:
            clients = "list its clients"
        raise WorkspaceError(f'no client "{name.strip()}" in this workspace ({root}); ' + clients
                             + " with " + listing + ", or create this one with "
                             + command_text(["torque", "client", "add", name.strip(), "--workspace", str(root)]) + ".",
                             code="unknown_client", next_command=listing)
    config = _read_json(path)
    if config.get("schema") != "torque.client/1" or config.get("slug") != slug:
        raise WorkspaceError(f"invalid client configuration: {client / 'client.json'}")
    if not isinstance(config.get("name"), str) or not config["name"].strip():
        raise WorkspaceError("client configuration has no name")
    return client, firm, config


def load_engagement(workspace: str | Path, name: str, kind: str = "client") -> tuple[Path, dict, dict]:
    """A client (existing records) or an initiative (engagements module)."""
    if kind == "client":
        return load_client(workspace, name)
    from .engagements import load_initiative, require
    require(kind, "sessions")
    return load_initiative(workspace, name)


ARCHIVED_REFUSAL = "this engagement is archived; reopen it before recording new work"


def require_engagement_writable(folder: Path) -> None:
    """Refuse a new record in an engagement folder (clients/<slug> or
    initiatives/<slug>) while the workspace is in maintenance or the engagement is
    archived. The lifecycle lives in state/engagement.json; clients have none."""
    require_writable(folder.parent.parent)
    state = folder / "state" / "engagement.json"
    if state.exists() and _read_json(_inside(folder, state)).get("state") == "archived":
        raise WorkspaceError(ARCHIVED_REFUSAL)


def add_session(workspace: str | Path, client_name: str, summary: str,
                status: str = "prepared", evidence: str | Path | None = None, *,
                kind: str = "client", learned=(), learned_scope: str = "client",
                learned_from: str = "agent", check=None, as_user: str | None = None,
                org: str | None = None, not_covered: str | None = None, accepted_by: str | None = None,
                corrects: str | None = None, change: str | None = None) -> dict:
    """Append a session record. Each text in `learned` is also saved as a lesson linked
    to the record (scope: the engagement, or the firm). The record is written first and
    stands whatever happens to a lesson: the returned entry then names the lessons saved
    (`lessons`) and those that were not, with the reason (`lesson_problems`).

    What a check rested on is kept in `check` (kind, as_user, org, org_type when the
    workspace knows the org, not_covered), who accepted the work in `accepted_by`, and
    the earlier record this one corrects in `corrects`; the earlier record is never
    rewritten (its correction is derived when records are read)."""
    client, _, config = load_engagement(workspace, client_name, kind)
    require_engagement_writable(client)
    if not summary.strip():
        raise WorkspaceError("session summary must be nonempty")
    if status not in STATUSES:
        raise WorkspaceError(f"unknown session status: {status}")
    if learned_scope not in ("client", "firm") or learned_from not in ("consultant", "agent"):
        raise WorkspaceError("a learned lesson's scope is client or firm, and it comes from the consultant or the agent")
    basis = {}
    kinds = [check] if isinstance(check, str) else list(check or [])
    for item in kinds:
        if item not in CHECK_KINDS:
            raise WorkspaceError(f"unknown check kind: {item}; choose from {', '.join(CHECK_KINDS)}")
    if kinds:
        basis["kinds"] = list(dict.fromkeys(kinds))
    for key, value, option in (("as_user", as_user, "--as-user"), ("org", org, "--in-org"),
                               ("not_covered", not_covered, "--not-covered")):
        if value is not None:
            basis[key] = _field_text(value, option)
    if "org" in basis:
        if any(c.isspace() for c in basis["org"]):
            raise WorkspaceError("--in-org takes one org alias, username or ID, without spaces")
        org_type = known_org_type(client, kind, basis["org"])
        if org_type:
            basis["org_type"] = org_type
    accepted = _field_text(accepted_by, "--accepted-by") if accepted_by is not None else None
    if status == "accepted" and not accepted:
        raise WorkspaceError("status accepted needs --accepted-by TEXT: who accepted the work (a lead, a tester, "
                             "the client, by role or name); an agent never accepts work alone")
    if corrects is not None:
        _require_session(client, config, kind, corrects)
    if change is not None:
        from .changes import load_change
        load_change(workspace, client_name, change, engagement_kind=kind)
    references = []
    for item in _evidence_arguments(evidence):
        reference = _evidence_reference(client, item)
        if all(reference["path"] != earlier["path"] for earlier in references):
            references.append(reference)
    # One file is stored as before, an object, so every version reads it; several are an
    # ordered list of the same objects (docs/engagement-records.md, "Session record").
    evidence_ref = None if not references else references[0] if len(references) == 1 else references
    at = _now()
    entry_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:12]
    entry = {"schema": "torque.session/1", "id": entry_id, "created_at": at,
             kind: config["slug"], "summary": summary.strip(), "status": status,
             "status_basis": "user_reported", "independently_verified": False,
             "evidence": evidence_ref}
    if basis:
        entry["check"] = basis
    if accepted:
        entry["accepted_by"] = accepted
    if corrects is not None:
        entry["corrects"] = corrects
    if change is not None:
        entry["change"] = change
    sessions = _inside(client, client / "sessions")
    sessions.mkdir(mode=0o700, exist_ok=True)
    _write_json(_inside(client, sessions / f"{entry_id}.json"), entry, private=True)
    if learned:
        try:
            from jsc_memory import intake
            saved, problems = intake.learned(_lesson_scopes(client, kind), learned, source=learned_from,
                                             scope="firm" if learned_scope == "firm" else None, session=entry_id)
        except Exception as exc:  # The record above is written; a lesson never undoes it.
            saved, problems = [], [{"learned": 0, "reason": f"lessons are unavailable ({type(exc).__name__})"}]
        entry = {**entry, "lessons": saved, "lesson_problems": problems}
    return entry


def _field_text(value: object, option: str) -> str:
    """One line of free text for a record field."""
    if not isinstance(value, str) or not value.strip() or any(c in value for c in "\r\n\0"):
        raise WorkspaceError(f"{option} must be nonempty and on one line")
    if len(value.strip()) > FIELD_LIMIT:
        raise WorkspaceError(f"{option} holds more than {FIELD_LIMIT:,} characters; put the detail in the summary")
    return value.strip()


def known_org_type(engagement: Path, kind: str, alias: str) -> str | None:
    """sandbox, developer, scratch or production when the workspace knows the org: the
    client's consent record names it with the type the Salesforce CLI reported when the
    consent was recorded. None when it does not (no org call is made)."""
    if kind != "client":
        return None
    try:
        from .consent import load_consent
        record = load_consent(engagement.parent.parent, engagement.name) or {}
        for org in record.get("approved_orgs") or []:
            if isinstance(org, dict) and org.get("alias") == alias and isinstance(org.get("kind"), str):
                return org["kind"]
    except (OSError, WorkspaceError, ValueError):
        return None
    return None


def _require_session(client: Path, config: dict, kind: str, identifier: object) -> None:
    """The session record `identifier` exists in this engagement and reads as valid.
    The refusal names no other record."""
    listing = command_text(["torque", "session", "list", "--workspace", str(client.parent.parent),
                            "--" + kind, config["slug"]])
    if not isinstance(identifier, str) or not _SESSION_ID.fullmatch(identifier):
        raise WorkspaceError(f"--corrects takes a session ID as `torque session add` printed it; list this "
                             f"{kind}'s records with {listing}")
    path = _inside(client, client / "sessions" / f"{identifier}.json")
    try:
        value = _read_json(path)
    except BoundaryError:
        raise
    except WorkspaceError:
        value = None
    if value is None or foreign_owner(value, kind, config["slug"]) or _session_problem(path, value, kind, config["slug"]):
        raise WorkspaceError(f"--corrects names no readable session record of this {kind}; list its records with "
                             f"{listing}")


def session_hints(entry: dict) -> list[str]:
    """What a new record leaves unsaid that the reader will want: a hint, never a refusal."""
    hints = []
    check = entry.get("check") or {}
    if entry.get("status") == "verified":
        unstated = [text for key, text in (("kinds", "how it was checked (--check " + "|".join(CHECK_KINDS) + ")"),
                                           ("as_user", "as whom (--as-user)"),
                                           ("org", "in which org (--in-org)"),
                                           ("not_covered", "what was not covered (--not-covered)"))
                    if not check.get(key)]
        if unstated:
            hints.append("verified, but not stated: " + "; ".join(unstated))
    return hints


def basis_text(entry: dict) -> str:
    """The check basis of a record on one line, or ''. Acceptance is what the record's
    author reported, never Torque's confirmation."""
    check = entry.get("check") or {}
    parts = []
    if check.get("kinds"):
        parts.append("check " + " + ".join(check["kinds"]))
    if check.get("as_user"):
        parts.append(f"as {check['as_user']}")
    if check.get("org"):
        parts.append(f"in {check['org']}" + (f" ({check['org_type']})" if check.get("org_type") else ""))
    if check.get("not_covered"):
        parts.append(f"not covered: {check['not_covered']}")
    if entry.get("accepted_by"):
        parts.append(f"accepted by {entry['accepted_by']} (reported)")
    return "; ".join(parts)


def session_link_lines(entry: dict) -> list[str]:
    """A record's basis and correction links, one line each, for context and handoff."""
    lines = []
    if basis_text(entry):
        lines.append("Basis: " + basis_text(entry))
    if entry.get("corrects"):
        lines.append(f"Corrects: {entry['corrects']}")
    if entry.get("corrected_by"):
        lines.append("CORRECTED by " + ", ".join(entry["corrected_by"]) + ": what this record says no longer "
                     "holds as written; read the correction")
    if entry.get("change"):
        lines.append(f"Change: {entry['change']}")
    return lines


def _evidence_arguments(evidence) -> list:
    """The evidence files a caller named: none, one path, or several in order."""
    if evidence is None:
        return []
    if isinstance(evidence, (str, os.PathLike)):
        return [evidence]
    return list(evidence)


def _evidence_reference(client: Path, evidence) -> dict:
    """The reference to one evidence file: its path and SHA-256 when recorded."""
    path = Path(evidence).expanduser().resolve()
    if not path.is_file():
        raise WorkspaceError(f"evidence is not a readable file: {path}")
    if foreign_engagement(client, path):
        raise WorkspaceError("evidence belongs to a different client or initiative")
    try:
        digest = _file_hash(path)
    except OSError as exc:
        raise WorkspaceError(f"cannot read evidence: {path}") from exc
    reference = {"path": str(path), "sha256": digest, "basis": EVIDENCE_BASIS}
    try:
        # Inside the workspace the reference is also kept relative to it, so it still
        # resolves after the workspace moves or is copied to another machine. The
        # absolute path stays for versions that read only that.
        reference["workspace_path"] = path.relative_to(client.parent.parent).as_posix()
    except ValueError:
        # Outside the workspace the absolute path is the reference, and it is read
        # only there: never matched with a copy inside the workspace.
        reference["scope"] = "external"
    return reference


def _lesson_scopes(engagement: Path, kind: str):
    """What the lessons commands may read and write for this engagement: its own
    lessons, the firm's and the platform's; never another engagement's folder."""
    from jsc_memory.storage import Scopes
    return Scopes(workspace=engagement.parent.parent, engagement=engagement, kind=kind)


def learned_lines(entry: dict) -> list[str]:
    """What `session add --learned` did with each lesson, for the terminal."""
    lines = []
    for item in entry.get("lessons", []):
        verb = {"saved": "saved", "duplicate": "already saved", "second-occurrence": "seen again"}[item["status"]]
        lines.append(f"Lesson {item['id']} {verb} ({item['scope']}, {item['state']}): {item['title']}")
        inferred = "; ".join(f"{name} {', '.join(values)}" for name, values in item.get("inferred", {}).items())
        if inferred:
            lines.append(f"  applies, inferred from the text: {inferred}. Correct with: torque lesson edit "
                         f"{item['id']} --set applies.types=...")
        if item["status"] == "saved" and item["state"] == "candidate":
            lines.append("  A candidate waits for review and is not shown at the point of use until then: "
                         "torque lesson review")
        lines += [f"  {note}" for note in item.get("notes", [])]
    lines += [f"Lesson {problem['learned']} not saved. {problem['reason']}" for problem in entry.get("lesson_problems", [])]
    return lines


def lessons_context(workspace: str | Path, engagement: Path, kind: str = "client", *,
                    shown_via: str | None = None) -> dict:
    """The lessons block of context and handoff: the engagement's active lessons that
    matter most, how many the firm and the platform hold, and the candidates waiting for
    review. With `shown_via`, the lessons listed are recorded as shown. A problem with
    the lessons is reported in the block and never stops the caller."""
    scope = ["--workspace", str(workspace), "--" + kind, engagement.name]
    commands = {"query": command_text(["torque", "lesson", "for", "TOPIC", *scope]),
                "review": command_text(["torque", "lesson", "review", *scope])}
    try:
        from jsc_memory import recall
        paused = (engagement.parent.parent / MAINTENANCE_FLAG).exists()
        section = recall.context_section(_lesson_scopes(engagement, kind), via=None if paused else shown_via)
    except Exception as exc:  # Lessons are an aid: context and handoff work without them.
        section = {"shown": [], "lines": [], "problem": f"lessons could not be read ({type(exc).__name__})"}
    return {**section, "commands": commands}


def lesson_lines(section: dict, kind: str = "client") -> list[str]:
    """The lessons block as text, for `torque context` and `torque handoff`."""
    if section.get("problem"):
        return [f"Lessons: {section['problem']}. Check with torque doctor."]
    counts = f"firm {section['firm_active']}, platform {section['platform_active']}"
    if section["lines"]:
        lines = [f"Lessons from earlier work: {len(section['shown'])} of {section['engagement_active']} for this "
                 f"{kind} ({counts})", *section["lines"], section["note"]]
    else:
        lines = [f"Lessons: none active for this {kind} yet ({counts})."]
    lines.append(f"For the ones that fit the task: {section['commands']['query']} "
                 "(or --task, --type, --command, --path; after a failure --error \"its first line\")")
    lines.append("Save what this session teaches with its record: torque session add ... --learned "
                 "\"When: ... Do: ... Because: ...\"")
    if section["candidates_waiting"]:
        lines.append(f"{section['candidates_waiting']} candidate lesson(s) wait for review: "
                     + section["commands"]["review"])
    unreadable = section.get("unreadable") or []
    if unreadable:
        lines.append(f"Skipped {len(unreadable)} lesson file(s) that could not be read: "
                     + "; ".join(f"{problem['path']} ({problem['reason']})" for problem in unreadable[:3])
                     + (" ..." if len(unreadable) > 3 else "") + ". Fix or remove them.")
    return lines


def _file_hash(path: Path) -> str:
    """Hash large local artifacts without loading the entire file into memory."""
    digest = hashlib.sha256()
    if os.name == "nt":
        # Windows has neither O_NOFOLLOW nor O_NONBLOCK; best-effort symlink
        # check first, then rely on the S_ISREG check below.
        if path.is_symlink():
            raise OSError("evidence is not a regular file")
        fd = os.open(path, os.O_RDONLY)
    else:
        # O_NONBLOCK prevents a replaced named pipe from hanging resumption.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("evidence is not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    finally:
        os.close(fd)
    return digest.hexdigest()


EVIDENCE_BASIS = "file reference recorded; contents not evaluated"


def _absolute_reference(text: str) -> bool:
    """An absolute path as macOS, Linux or Windows wrote it: a record copied from one
    system to another keeps a valid reference, even though it cannot open there."""
    return PurePosixPath(text).is_absolute() or PureWindowsPath(text).is_absolute()


def _relative_reference(text: object) -> bool:
    return (isinstance(text, str) and bool(text) and "\0" not in text and "\\" not in text
            and not text.startswith("/") and not PureWindowsPath(text).drive
            and all(part not in ("", ".", "..") for part in text.split("/")))


def _moved_reference(engagement: Path, stored: str) -> Path | None:
    """Where evidence an earlier version recorded only as an absolute path into this
    engagement's folder (.../clients/SLUG/REST) is now, after the workspace moved or
    was copied, Windows to macOS or back: REST under this engagement. None for a
    path that does not run through this engagement's own folder."""
    windows = bool(PureWindowsPath(stored).drive) or "\\" in stored
    parts = (PureWindowsPath if windows else PurePosixPath)(stored).parts
    fold = str.casefold if windows else str
    folder, slug = fold(engagement.parent.name), fold(engagement.name)
    for index in range(len(parts) - 3, -1, -1):
        if fold(parts[index]) == folder and fold(parts[index + 1]) == slug:
            rest = parts[index + 2:]
            return engagement.joinpath(*rest) if all(part not in ("", ".", "..") for part in rest) else None
    return None


def _check_evidence(client: Path, path: Path, sha256: str) -> tuple[str, str]:
    """(integrity, location) of the file at path; the location is workspace-relative
    inside the workspace and absolute outside it."""
    root = client.parent.parent
    try:
        location = path.relative_to(root).as_posix()
    except ValueError:
        location = str(path)
    try:
        resolved = path.resolve()
        if foreign_engagement(client, resolved):
            raise BoundaryError("session evidence belongs to a different client or initiative")
        if path != resolved:
            return "unavailable", location  # A saved canonical reference now traverses a symlink.
        return ("matches_reference" if _file_hash(path) == sha256 else "changed"), location
    except FileNotFoundError:
        return "missing", location
    except (OSError, RuntimeError):
        return "unavailable", location


def _workspace_reference(text: object) -> tuple[str, ...]:
    """The parts of a workspace-relative evidence reference (workspace_path). One that
    would leave the workspace (absolute, a drive, a `..` part) raises BoundaryError,
    which every reader refuses; any other malformed value raises WorkspaceError, which
    an informational reader skips as a damaged record."""
    if not isinstance(text, str) or not text or "\0" in text:
        raise WorkspaceError("invalid session evidence reference")
    if text.startswith(("/", "\\")) or PureWindowsPath(text).drive or ".." in re.split(r"[\\/]", text):
        raise BoundaryError("session evidence reference leaves the workspace")
    if not _relative_reference(text):
        raise WorkspaceError("invalid session evidence reference")
    return tuple(text.split("/"))


def _reference_shape(evidence: object) -> tuple[str, ...] | None:
    """Check one evidence reference without opening its file: the parts of its
    workspace-relative path, or None for an absolute-only reference. A reference that
    leaves the workspace raises BoundaryError, any other malformed one WorkspaceError."""
    # A reference that leaves the workspace stops the read before anything else is checked.
    relative = (_workspace_reference(evidence["workspace_path"])
                if isinstance(evidence, dict) and "workspace_path" in evidence else None)
    if (not isinstance(evidence, dict) or not isinstance(evidence.get("path"), str)
            or not evidence["path"] or "\0" in evidence["path"]
            or not _absolute_reference(evidence["path"])
            or not isinstance(evidence.get("sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", evidence["sha256"])
            or evidence.get("basis") != EVIDENCE_BASIS
            or ("scope" in evidence and (evidence["scope"] != "external" or relative is not None))):
        raise WorkspaceError("invalid session evidence reference")
    return relative


def _session_evidence(client: Path, evidence: object) -> tuple[str, str | None]:
    """(integrity, location) of a session's evidence reference. A reference inside the
    workspace is read relative to it (workspace_path), so it resolves after a move or a
    copy to another machine. One outside it ("scope": "external") is read at its
    absolute path only. A reference an earlier version wrote, which could not say which
    it was, is also looked for under this engagement when its path no longer opens."""
    if evidence is None:
        return "not_supplied", None
    relative = _reference_shape(evidence)
    if relative is not None:
        return _check_evidence(client, client.parent.parent.joinpath(*relative), evidence["sha256"])
    stored = Path(evidence["path"])
    if stored.is_absolute():
        found = _check_evidence(client, stored, evidence["sha256"])
        if found[0] != "missing":
            return found
    if evidence.get("scope") == "external":
        return "missing", evidence["path"]
    moved = _moved_reference(client, evidence["path"])
    if moved is not None:
        found = _check_evidence(client, moved, evidence["sha256"])
        if found[0] != "missing":
            return found
    return "missing", evidence["path"]


def _evidence_items(evidence: object) -> list:
    """The references in a session's `evidence`: none (null), one (an object, as every
    version writes one file) or several (a nonempty list of such objects, in order)."""
    if evidence is None:
        return []
    if isinstance(evidence, list):
        if not evidence or any(not isinstance(item, dict) for item in evidence):
            raise WorkspaceError("invalid session evidence reference")
        return evidence
    return [evidence]


def _session_evidence_all(client: Path, evidence: object) -> tuple[str, str | None, list[dict]]:
    """(integrity, location, items) of every evidence reference of a session. The
    integrity and location are the first file's when it is the only one; with several,
    the integrity is the first that does not match (missing, changed, unavailable) or
    matches_reference when all do. `items` holds each file: where it is now, its
    recorded SHA-256 and its integrity, in the order recorded."""
    items = []
    for reference in _evidence_items(evidence):
        integrity, location = _session_evidence(client, reference)
        items.append({"location": location, "sha256": reference["sha256"], "integrity": integrity,
                      "where": "outside this workspace" if _absolute_reference(location) else "in this workspace"})
    if not items:
        return "not_supplied", None, []
    overall = next((item["integrity"] for item in items if item["integrity"] != "matches_reference"),
                   "matches_reference")
    return overall, items[0]["location"], items


def _session_timestamp(value: object) -> bool:
    try:
        return isinstance(value, str) and datetime.fromisoformat(value).utcoffset() is not None
    except ValueError:
        return False


def foreign_owner(value: dict, kind: str, slug: str) -> bool:
    """A record names another client or initiative than the one it was read for."""
    return any(isinstance(value.get(key), str) and value[key] and (key != kind or value[key] != slug)
               for key in ("client", "initiative"))


def record_problem(engagement: Path, path: Path, reason: str) -> dict:
    """An entry for a reader's `problems` list: the file, relative to its engagement."""
    return {"path": path.relative_to(engagement).as_posix(), "reason": reason}


def _session_problem(path: Path, value: dict, kind: str, slug: str) -> str:
    """Why this is not a session record of the engagement, or ""."""
    if not _SESSION_ID.fullmatch(path.stem):
        return "its file name is not a session ID (a copy or a stray file?)"
    if value.get("id") != path.stem:
        return "its id does not match its file name"
    if (value.get("schema") != "torque.session/1" or value.get(kind) != slug
            or value.get("status") not in STATUSES or value.get("status_basis") != "user_reported"
            or value.get("independently_verified") is not False
            or not isinstance(value.get("summary"), str)
            or not value["summary"].strip() or not _session_timestamp(value.get("created_at"))):
        return "not a valid session record"
    check = value.get("check")
    if "check" in value and (not isinstance(check, dict) or not check or any(
            key not in CHECK_FIELDS or (key != "kinds" and (not isinstance(item, str) or not item.strip()))
            for key, item in check.items())
            or ("kinds" in check and (not isinstance(check["kinds"], list) or not check["kinds"]
                                      or any(kind not in CHECK_KINDS for kind in check["kinds"])))):
        return "its check basis is not valid"
    if "accepted_by" in value and (not isinstance(value["accepted_by"], str) or not value["accepted_by"].strip()):
        return "its accepted_by is not valid"
    if value["status"] == "accepted" and "accepted_by" not in value:
        return "it is accepted but names no one who accepted it"
    if "corrects" in value and (not isinstance(value["corrects"], str) or not _SESSION_ID.fullmatch(value["corrects"])
                                or value["corrects"] == value["id"]):
        return "its corrects link is not a session ID"
    if "change" in value and (not isinstance(value["change"], str) or not _CHANGE_ID.fullmatch(value["change"])):
        return "its change link is not a change ID"
    return ""


def list_sessions(workspace: str | Path, client_name: str, limit: int | None = 20, *,
                  kind: str = "client", problems: list | None = None, since: datetime | None = None) -> list[dict]:
    """The engagement's session records, newest first, at most `limit` of them. A
    record that fails its checks, its evidence reference included, stops the read,
    unless `problems` is a list: then it is skipped and named there, so one stray or
    damaged file does not hide the rest of the history; a skipped record is never read
    as valid. A record or evidence that belongs to another client or initiative, an
    evidence reference that leaves the workspace, and a symlink stop the read either
    way (BoundaryError). With `since` (an aware datetime), only records made at or
    after it. A record a later record corrects carries `corrected_by` (their IDs)."""
    client, entries = read_sessions(workspace, client_name, kind=kind, problems=problems)
    if since is not None:
        entries = [entry for entry in entries if datetime.fromisoformat(entry["created_at"]) >= since]
    return with_evidence(client, entries, limit, problems)


def with_evidence(client: Path, entries: list[dict], limit: int | None, problems: list | None) -> list[dict]:
    """The first `limit` of `entries` (all with None) whose evidence can be checked, each
    with its evidence integrity; one whose reference is damaged is skipped and named in
    `problems`, or stops the read without it."""
    selected = []
    for value in entries:
        if limit is not None and len(selected) >= limit:
            break
        path = client / "sessions" / f"{value['id']}.json"
        try:
            (value["evidence_integrity"], value["evidence_location"],
             value["evidence_items"]) = _session_evidence_all(client, value.get("evidence"))
        except BoundaryError as exc:
            raise BoundaryError(f"{exc}: {path}") from exc
        except WorkspaceError as exc:
            if problems is None:
                raise WorkspaceError(f"{exc}: {path}") from exc
            problems.append(record_problem(client, path, str(exc)))
            continue
        selected.append(value)
    return selected


def read_sessions(workspace: str | Path, client_name: str, *, kind: str = "client",
                  problems: list | None = None) -> tuple[Path, list[dict]]:
    """(engagement folder, every session record of it), newest first, checked as
    list_sessions describes but without opening evidence files. A record a later one
    corrects carries `corrected_by`, the IDs of the records that correct it."""
    client, _, config = load_engagement(workspace, client_name, kind)
    sessions = _inside(client, client / "sessions")
    if not sessions.exists():
        return client, []
    entries = []
    for path in sorted(sessions.glob("*.json"), reverse=True):
        try:
            value = _read_json(_inside(client, path))
        except BoundaryError:
            raise
        except WorkspaceError:
            value, reason = None, "not a readable JSON object"
        else:
            if foreign_owner(value, kind, config["slug"]):
                raise BoundaryError(f"session record belongs to a different client or initiative: {path}")
            evidence = value.get("evidence")
            for reference in evidence if isinstance(evidence, list) else [evidence]:
                if isinstance(reference, dict) and "workspace_path" in reference:
                    # A reference that leaves the workspace stops the read, however damaged the rest is.
                    try:
                        _workspace_reference(reference["workspace_path"])
                    except BoundaryError as exc:
                        raise BoundaryError(f"{exc}: {path}") from exc
                    except WorkspaceError:
                        pass  # Malformed: named below, with the record.
            reason = _session_problem(path, value, kind, config["slug"])
            if not reason and value.get("corrects") and value["corrects"] >= value["id"]:
                # Only an earlier record can be corrected; this also rules out a cycle.
                reason = "its corrects link names itself or a later record"
            if not reason:
                try:
                    for reference in _evidence_items(value.get("evidence")):
                        _reference_shape(reference)
                except BoundaryError as exc:
                    raise BoundaryError(f"{exc}: {path}") from exc
                except WorkspaceError as exc:
                    if problems is None:
                        raise WorkspaceError(f"{exc}: {path}") from exc
                    reason = str(exc)
        if reason:
            if problems is None:
                raise WorkspaceError(f"invalid session record: {path}")
            problems.append(record_problem(client, path, reason))
            continue
        entries.append(value)
    # A correction is a later record that names an earlier one; the earlier file is never
    # rewritten, so its correction is derived here.
    corrections: dict[str, list[str]] = {}
    for value in reversed(entries):
        if value.get("corrects"):
            corrections.setdefault(value["corrects"], []).append(value["id"])
    for value in entries:
        if value["id"] in corrections:
            value["corrected_by"] = corrections[value["id"]]
    return client, entries


def record_problem_lines(problems: list) -> list[str]:
    """Lines naming the records a reader skipped; none when it skipped nothing."""
    if not problems:
        return []
    return [f"Skipped {len(problems)} record(s) that could not be read, so the history shown is incomplete. "
            "Torque never rewrites a record: check each file, then move it out or delete it."] + [
        f"- {problem['path']}: {problem['reason']}" for problem in problems]


def _change_context(workspace: str | Path, client_name: str, client: Path, kind="client",
                    problems: list | None = None) -> list[dict]:
    """Bound event summaries without converting reported checks into observed acceptance.
    The show command names the workspace as the caller did (`.` stays `.`)."""
    from .changes import get_change, list_changes
    summaries = []
    limit = 5
    for record in list_changes(workspace, client_name, engagement_kind=kind, problems=problems):
        detail = get_change(workspace, client_name, record["id"], engagement_kind=kind, problems=problems)
        groups = {"decisions": "decision", "metadata_observations": "metadata_observation",
                  "next_steps": "next_step"}
        histories, totals = {}, {}
        for name, event_kind in groups.items():
            events = [event for event in detail["events"] if event["kind"] == event_kind]
            totals[name] = len(events)
            histories[name] = []
            for event in events[-limit:]:
                row = {key: event[key] for key in ("id", "created_at", "summary", "basis")}
                if event_kind == "metadata_observation":
                    row.update({key: event.get(key) for key in
                                ("target_org", "job_id", "result", "evidence_integrity", "manifest_integrity")})
                    row["business_acceptance_proven"] = False
                histories[name].append(row)
        truncated = {name: total > limit for name, total in totals.items()}
        summaries.append({**record, "criteria": detail["criteria"], "assessment": detail["assessment"],
                          **histories, "history_counts": totals, "history_limit_per_kind": limit,
                          "history_truncated": truncated,
                          "history_note": ("Only the latest five entries per history kind are shown; use show_command for full history."
                                           if any(truncated.values()) else "All decision, metadata and next-step history is shown."),
                          "show_command": command_text(["torque", "change", "show", record["id"],
                                                        "--workspace", str(workspace), "--" + kind, client.name])})
    return summaries


def client_output_path(workspace: str | Path, client_name: str, destination: str | Path, *,
                       kind: str = "client") -> Path:
    """Allow an explicit export, but never place this engagement's data under another
    client or initiative."""
    client, _, _ = load_engagement(workspace, client_name, kind)
    output = Path(destination).expanduser().absolute()
    resolved = output.resolve()
    if foreign_engagement(client, resolved):
        raise WorkspaceError("handoff output belongs to a different client or initiative; choose this "
                             "engagement's directory or an explicit export outside clients/ and initiatives/")
    return output


# The notes `context` and `handoff` show in full, in characters, across every notes file.
# Measured: a session summary has a median of 655 characters, so 20 records are about
# 13,000; 24,000 characters of notes keeps a whole resume near 40,000 characters (about
# 10,000 tokens), where two large notes files alone gave 131,489. `--max-chars` changes it.
NOTES_BUDGET = 24_000
NOTE_FILE_LIMIT = 65_536
SESSION_WINDOW = 20


def parse_since(text: str) -> datetime:
    """A `--since` value: a date (YYYY-MM-DD, from the start of that local day) or a time
    (YYYY-MM-DDTHH:MM[:SS], with an offset or Z, else local)."""
    value = text.strip() if isinstance(text, str) else ""
    try:
        moment = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value)
    except ValueError:
        raise WorkspaceError("--since takes a date such as 2026-11-01, or a time such as 2026-11-01T09:00") from None
    return moment if moment.utcoffset() is not None else moment.astimezone()


def _context_notes(client: Path, budget: int | None = NOTES_BUDGET) -> tuple[dict[str, str], list[dict]]:
    """The notes for resumption and handoff, and a list of every notes file with its size
    and whether it is shown in full, cut or not shown. In order (firm notes, client notes,
    then context/*.md by name) each file is shown while the total stays within `budget`
    characters; the file that does not fit is cut and says where to read the rest. With no
    budget, each file is shown up to NOTE_FILE_LIMIT characters."""
    root = client.parent.parent
    notes, files = {}, []
    candidates = [("Firm notes", root / "profile.md", root),
                  ("Client notes", client / "context.md", client)]
    context_dir = _inside(client, client / "context")
    if context_dir.is_dir():
        candidates.extend((f"Client context: {p.name}", p, client)
                          for p in sorted(context_dir.glob("*.md")))
    remaining = budget
    for title, path, owner in candidates:
        _inside(owner, path)
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as stream:
            text = stream.read(NOTE_FILE_LIMIT + 1)
        location = path.relative_to(root).as_posix()
        whole = text[:NOTE_FILE_LIMIT] + ("\n[truncated at 65,536 characters]" if len(text) > NOTE_FILE_LIMIT else "")
        state = "cut" if len(text) > NOTE_FILE_LIMIT else "full"
        if remaining is not None and len(whole) > remaining:
            def marker(shown: int) -> str:
                return f"\n[cut: the first {shown:,} characters are shown; read the rest in {location}]"
            room = remaining - len(marker(remaining))
            if room >= 200:
                whole, state = whole[:room] + marker(room), "cut"
            else:
                whole, state = None, "not shown"
        if whole is not None:
            notes[title] = whole
            if remaining is not None:
                remaining -= len(whole)
        files.append({"title": title, "path": location, "bytes": path.stat().st_size,
                      "shown_chars": len(whole or ""), "state": state})
    return notes, files


def note_file_lines(files: list[dict], budget: int | None) -> list[str]:
    """Every notes file with its size, the ones cut or not shown marked."""
    if not files:
        return []
    shown = sum(item["shown_chars"] for item in files)
    head = (f"Notes files ({len(files)}; {shown:,} characters shown"
            + (f", budget {budget:,}; change it with --max-chars N" if budget is not None else "") + "):")
    marks = {"full": "shown in full", "cut": "CUT", "not shown": "NOT SHOWN (over the budget)"}
    # A file cut at its own size limit (65,536 characters) is marked by that notice in its text.
    return [head] + [f"- {item['path']}: {item['bytes']:,} bytes, {marks[item['state']]}"
                     + (f" at {item['shown_chars']:,} characters" if item["state"] == "cut" else "")
                     for item in files]


def _standing(records: list[dict], shown: list[dict], more: str) -> dict:
    """Where things stand: the latest record, the open records (marked incomplete and not
    corrected) shown and not shown, and the corrections among the records shown."""
    ids = {entry["id"] for entry in shown}
    def open_record(entry):
        return entry["status"] == "incomplete" and not entry.get("corrected_by")
    older = [entry for entry in records if entry["id"] not in ids and open_record(entry)]
    dates = {entry["id"]: entry["created_at"] for entry in records}
    hidden = [entry for entry in records if entry.get("corrects") and entry["id"] not in ids]
    return {"latest": shown[0]["id"] if shown else None,
            "open": [entry["id"] for entry in shown if open_record(entry)],
            "open_not_shown": {"count": len(older), "oldest": older[-1]["created_at"] if older else None,
                               "command": more if older else None},
            "corrections": [{"id": entry["id"], "corrects": entry["corrects"],
                             "corrected_created_at": dates.get(entry["corrects"]),
                             "corrected_shown": entry["corrects"] in ids}
                            for entry in shown if entry.get("corrects")],
            "corrections_not_shown": {"count": len(hidden), "command": more if hidden else None}}


def standing_lines(context: dict) -> list[str]:
    """`Where things stand` as text: the latest record, open items, corrections."""
    state, sessions = context["where_things_stand"], context["sessions"]
    lines = ["Where things stand:"]
    latest = next((entry for entry in sessions if entry["id"] == state["latest"]), None)
    if latest is None:
        lines.append("- No session records yet.")
    else:
        lines.append(f"- Latest record {latest['id']} ({latest['created_at']}) [{latest['status']}, "
                     f"user-reported]: {latest['summary']}")
        lines += ["  " + line for line in session_link_lines(latest)]
        if latest.get("evidence_integrity") in ("missing", "changed", "unavailable"):
            lines.append(f"  Evidence: {latest['evidence_integrity']} since recording.")
    for identifier in state["open"]:
        entry = next(e for e in sessions if e["id"] == identifier)
        lines.append(f"- Open (incomplete): {entry['created_at']} {entry['id']}: {entry['summary'][:200]}"
                     + ("..." if len(entry["summary"]) > 200 else ""))
    older = state["open_not_shown"]
    if older["count"]:
        lines.append(f"- Open, not shown: {older['count']} older record(s) marked incomplete and not corrected "
                     f"(oldest {older['oldest']}); read them with {older['command']}")
    for change in context.get("changes", []):
        pending = change["assessment"]["not_yet_reported_pass"]
        if pending:
            lines.append(f"- Open in change {change['id']} ({change['title']}): not yet reported pass: "
                         + ", ".join(pending))
    for item in state["corrections"]:
        where = ("" if item["corrected_shown"] else
                 f" (made {item['corrected_created_at']}, not shown here)" if item["corrected_created_at"]
                 else " (not found among this engagement's records)")
        lines.append(f"- Correction: {item['id']} corrects {item['corrects']}{where}")
    hidden = state["corrections_not_shown"]
    if hidden["count"]:
        lines.append(f"- Corrections, not shown: {hidden['count']} older correction record(s); read them with "
                     f"{hidden['command']}")
    return lines


# The most characters `torque context` prints as text (about 10,000 tokens): where things
# stand, lessons, changes, history and notes together. --max-chars changes it.
CONTEXT_BUDGET = 40_000


def _change_lines(change: dict) -> list[str]:
    lines = [f"Change {change['id']}: {change['title']} — {change['outcome']}"]
    unresolved = [f"{c['id']} ({c['reported_result'].replace('_', ' ')})"
                  for c in change["criteria"] if c["reported_result"] != "pass" and not c.get("retired")]
    lines.append("Acceptance results are operator-reported; independent business acceptance is not established.")
    if unresolved:
        lines.append("Not yet reported pass: " + ", ".join(unresolved))
    elif not change["criteria"]:
        lines.append("No acceptance criteria recorded.")
    if change["assessment"]["evidence_problems"]:
        lines.append(f"Evidence problems: {change['assessment']['evidence_problems']} missing, changed or "
                     "unavailable captures.")
    lines += [f"Decision (reported): {d['summary']}" for d in change["decisions"]]
    lines += [f"Metadata {o['result']} · Org {o['target_org']} · Job {o['job_id']}: {o['summary']}"
              for o in change["metadata_observations"]]
    lines += [f"Next step (reported): {s['summary']}" for s in change["next_steps"]]
    if any(change["history_truncated"].values()):
        counts = ", ".join(f"{name}: {count}" for name, count in change["history_counts"].items())
        lines.append(f"{change['history_note']} Total entries — {counts}.")
    lines.append("Full change: " + change["show_command"])
    return lines


def _session_lines(entry: dict) -> list[str]:
    lines = [f"{entry['created_at']} [{entry['status']}, user-reported] {entry['summary']}",
             *session_link_lines(entry)]
    if entry.get("evidence_integrity") in ("missing", "changed", "unavailable"):
        lines.append(f"Evidence: {entry['evidence_integrity']} since recording.")
    return lines


def context_text(context: dict, kind: str, budget: int = CONTEXT_BUDGET, *, workspace: str | Path = ".") -> str:
    """`torque context` as text, at most `budget` characters. Where things stand and the
    lessons come first and always; then changes, history and notes share what is left
    (history at most half). Whatever does not fit is named in a last line, with the
    command that shows it. `context` holds the complete notes (get_context max_chars=None)."""
    if budget < 2_000:
        raise WorkspaceError("--max-chars must be at least 2,000 for the text (where things stand and the lessons "
                             "always print); with --json it may be smaller")
    label = "Initiative" if kind == "initiative" else "Client"
    top = [f"{context['workspace']['name']} / {context['client']['name']}",
           f"{label} directory: {context['client_root']}"]
    if kind == "client":
        top.append(f"Configured org: {context['client'].get('org') or 'not specified'}")
    top += [context["evidence_note"], *record_problem_lines(context["record_problems"]),
            "", *standing_lines(context), "", *lesson_lines(context["lessons"], kind)]
    text = "\n".join(top) + "\n"
    reserve = 700  # the line that names what was cut
    remaining = budget - len(text) - reserve
    cut: list[str] = []
    changes_text = ""
    for change in context.get("changes", []):
        block = "\n" + "\n".join(_change_lines(change)) + "\n"
        if len(changes_text) + len(block) > max(remaining // 4, 0):
            cut.append(f"changes ({len(context['changes'])} in all; each with its `Full change` command: "
                       f"torque change list)")
            break
        changes_text += block
    remaining -= len(changes_text)
    entries = context["sessions"][1:]
    history_cap = max(remaining // 2, 0)
    shown_history, history_text = 0, ""
    for entry in entries:
        block = "\n" + "\n".join(_session_lines(entry)) + "\n"
        if len(history_text) + len(block) > history_cap:
            break
        history_text += block
        shown_history += 1
    full = f"torque context --workspace {workspace} --{kind} {context['client']['slug']} --json"
    if shown_history < len(entries):
        cut.append(f"history ({shown_history + 1} of {context['sessions_returned']} records printed)")
    shown_records = shown_history + (1 if context["sessions"] else 0)
    history_head = (f"\nHistory: {shown_records} of {context['sessions_total']} session record(s) printed, newest "
                    "first, the latest one above"
                    + (f"; the rest: {context['more_command']}" if context["sessions_truncated"] else "") + ".\n")
    remaining -= len(history_text) + len(history_head)
    files = [dict(item) for item in context.get("notes_files", [])]
    titles = {item["title"]: item for item in files}
    listing_room = sum(len(line) + 1 for line in note_file_lines(files, budget)) + 200
    room = max(remaining - listing_room, 0)
    notes_text = ""
    for title, value in context.get("notes", {}).items():
        item = titles.get(title)
        block = f"\n{title}:\n{value}\n"
        if len(block) <= room:
            notes_text += block
            room -= len(block)
            if item:
                item["shown_chars"] = len(value)
            continue
        marker = f"\n[cut: the rest is in {item['path'] if item else title}]"
        keep = room - len(title) - len(marker) - 4
        if item:
            item["state"], item["shown_chars"] = ("cut", keep) if keep >= 200 else ("not shown", 0)
        if keep >= 200:
            notes_text += f"\n{title}:\n{value[:keep]}{marker}\n"
            room = 0
    notes_cut = [item["path"] for item in files if item["state"] != "full"]
    if notes_cut:
        cut.append("notes (" + ", ".join(notes_cut) + ")")
    listing = "\n".join(note_file_lines(files, budget))
    text += ("\n" + listing + "\n" if listing else "") + notes_text + changes_text + history_head + history_text
    if cut:
        line = (f"\nCut to fit {budget:,} characters: " + "; ".join(cut)
                + f". All of it: {full}, or a larger --max-chars.\n")
        text += line if len(line) <= reserve else line[:reserve - 5] + "...]\n"
    return text


def get_context(workspace: str | Path, client_name: str, *, kind: str = "client",
                shown_via: str | None = None, limit: int | None = SESSION_WINDOW,
                since: datetime | None = None, max_chars: int | None = None) -> dict:
    """The engagement's notes, lessons, changes and recent sessions, with where things
    stand first and what was left out counted. A stray or damaged record is skipped and
    named in record_problems instead of hiding the rest. With `shown_via` (the command
    printing the context), the lessons listed count as shown. `limit` None: every record."""
    client, firm, config = load_engagement(workspace, client_name, kind)
    if limit is not None and limit < 1:
        raise WorkspaceError("--limit must be 1 or more; use --all for every record")
    if max_chars is not None and max_chars < 1:
        raise WorkspaceError("--max-chars must be 1 or more")
    notes, note_files = _context_notes(client, max_chars)
    problems: list[dict] = []
    changes = _change_context(workspace, client_name, client, kind, problems)
    _, records = read_sessions(workspace, client_name, kind=kind, problems=problems)
    window = records if since is None else [e for e in records if datetime.fromisoformat(e["created_at"]) >= since]
    sessions = with_evidence(client, window, limit, problems)
    more = command_text(["torque", "context", "--workspace", str(workspace), "--" + kind, config["slug"], "--all"])
    truncated = len(sessions) < len(records)
    return {"workspace": firm, "client": config, "client_root": str(client),
            "where_things_stand": _standing(records, sessions, more),
            "lessons": lessons_context(workspace, client, kind, shown_via=shown_via),
            "changes": changes, "notes": notes, "notes_files": note_files, "notes_budget": max_chars,
            "sessions": sessions, "sessions_total": len(records), "sessions_returned": len(sessions),
            "sessions_truncated": truncated, "more_command": more if truncated else None,
            "record_problems": problems,
            "evidence_note": "Journal statuses are user-reported, not independent verification."}


HANDOFF_SCHEMA = "torque.handoff/1"
HANDOFF_STATEMENT = ("Statuses below were supplied by the user. This journal did not independently "
                     "verify execution or outcomes.")


def _session_view(entry: dict) -> dict:
    """A session record as a handoff carries it: no absolute path into the author's
    folders (evidence inside the workspace is named relative to it)."""
    view = {key: entry[key] for key in ("id", "created_at", "status", "summary")}
    for key in ("check", "accepted_by", "corrects", "corrected_by", "change"):
        if entry.get(key):
            view[key] = entry[key]
    view["evidence_integrity"] = entry.get("evidence_integrity", "not_supplied")
    view["evidence"] = [dict(item) for item in entry.get("evidence_items", [])]
    return view


def handoff_data(workspace: str | Path, client_name: str, *, kind: str = "client",
                 limit: int | None = SESSION_WINDOW, since: datetime | None = None,
                 change: str | None = None, everything: bool = False) -> dict:
    """The handoff for the task in hand: where things stand first, then lessons, notes,
    changes and the history, newest first. By default the 20 newest records and the
    notes within NOTES_BUDGET; `since` and `limit` narrow it, `change` keeps only that
    change and the sessions linked to it, `everything` is the full journal. A record that
    could not be read is named, so the document says it is incomplete."""
    client_root, firm, client = load_engagement(workspace, client_name, kind)
    if limit is not None and limit < 1:
        raise WorkspaceError("--limit must be 1 or more; use --all for every record")
    if everything:
        limit, since = None, None
    from .changes import get_change, list_changes, load_change
    problems: list[dict] = []
    if change is not None:
        load_change(workspace, client_name, change, engagement_kind=kind)
        change_ids = [change]
    else:
        change_ids = [item["id"] for item in list_changes(workspace, client_name, engagement_kind=kind,
                                                          problems=problems)]
    changes = []
    for identifier in change_ids:
        item = get_change(workspace, client_name, identifier, engagement_kind=kind, problems=problems)
        changes.append({"id": item["id"], "title": item["title"], "outcome": item["outcome"],
                        "criteria": [{key: c[key] for key in ("id", "text", "reported_result")} for c in item["criteria"]],
                        "assessment": item["assessment"], "next_steps": item["next_steps"]})
    notes, note_files = _context_notes(client_root, None if everything else NOTES_BUDGET)
    _, records = read_sessions(workspace, client_name, kind=kind, problems=problems)
    if change is not None:
        records = [entry for entry in records if entry.get("change") == change]
    window = records if since is None else [e for e in records if datetime.fromisoformat(e["created_at"]) >= since]
    sessions = [_session_view(entry) for entry in with_evidence(client_root, window, limit, problems)]
    # The commands name the workspace as `.`: a handoff does not carry the author's folder names.
    more = command_text(["torque", "handoff", "--workspace", ".", "--" + kind, client["slug"],
                         *(["--change", change] if change else []), "--all"])
    truncated = len(sessions) < len(records)
    return {"schema": HANDOFF_SCHEMA, "title": client["name"], kind: client["slug"],
            "workspace": firm["name"], "profile": firm["profile"],
            **({"configured_org": client.get("org")} if kind == "client" else {"initiative_state": client["state"]}),
            "statement": HANDOFF_STATEMENT,
            "window": {"limit": limit, "since": since.isoformat() if since else None, "change": change,
                       "all": everything},
            "where_things_stand": _standing(records, sessions, more),
            "sessions_total": len(records), "sessions_returned": len(sessions), "sessions_truncated": truncated,
            "more_command": more if truncated else None, "record_problems": problems,
            "lessons": lessons_context(".", client_root, kind),
            "notes_files": note_files, "notes_budget": None if everything else NOTES_BUDGET, "notes": notes,
            "changes": changes, "sessions": sessions}


def render_handoff_markdown(data: dict, workspace: str | Path, client_name: str, *, kind: str = "client") -> str:
    """The handoff as Markdown: current state first, history after."""
    lines = [f"# Handoff: {data['title']}", "", f"Workspace: {data['workspace']}", f"Profile: {data['profile']}",
             f"Configured org: {data.get('configured_org') or 'not specified'}" if kind == "client"
             else f"Initiative state: {data['initiative_state']}",
             "", data["statement"], ""]
    window = data["window"]
    scope = ("the full journal" if window["all"] else
             ", ".join(part for part in (f"change {window['change']}" if window["change"] else "",
                                         f"since {window['since']}" if window["since"] else "",
                                         f"the {window['limit']} newest records" if window["limit"] else "") if part))
    lines += [f"Shown: {scope}; {data['sessions_returned']} of {data['sessions_total']} session record(s)"
              + (f". The rest: `{data['more_command']}`" if data["sessions_truncated"] else "") + ".", ""]
    if data["record_problems"]:
        lines += ["## Records not shown", "", *record_problem_lines(data["record_problems"]), ""]
    lines += ["## Where things stand", "", *standing_lines(data)[1:], ""]
    lines += ["## Lessons", "", "```text", *lesson_lines(data["lessons"], kind), "```", ""]
    if data["notes"] or data["notes_files"]:
        lines += ["## Working context", "", *note_file_lines(data["notes_files"], data["notes_budget"]), ""]
        for title, contents in data["notes"].items():
            lines += [f"### {title}", "", contents.rstrip(), ""]
    from .changes import render_change
    for item in data["changes"]:
        lines += ["---", "", render_change(workspace, client_name, item["id"], engagement_kind=kind,
                                           problems=[]).rstrip(), ""]
    lines += ["## History", ""]
    if not data["sessions"]:
        lines.append(f"No session entries have been recorded for this {kind}"
                     + (" in this window." if data["sessions_total"] else "."))
    for entry in data["sessions"]:
        lines += [f"### {entry['created_at']} — {entry['status']} (user-reported)", "",
                  entry["summary"], "", f"Entry: {entry['id']}"]
        lines += session_link_lines(entry)
        # Where each file is now: relative to the workspace inside it.
        for item in entry["evidence"]:
            lines += [f"Evidence reference: {item['location']} ({item['where']})",
                      f"Recorded SHA-256: {item['sha256']}",
                      f"Evidence integrity: {item['integrity'].replace('_', ' ')}"]
        lines.append("The reference was hashed when recorded; its content was not assessed."
                     if entry["evidence"] else "Evidence reference: none supplied.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_handoff(workspace: str | Path, client_name: str, *, kind: str = "client", **window) -> str:
    """The engagement's handoff as Markdown (handoff_data takes the window options)."""
    return render_handoff_markdown(handoff_data(workspace, client_name, kind=kind, **window),
                                   workspace, client_name, kind=kind)


EXPORTS = "state/exports.json"


def _export_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def replace_output(path: Path, text: str, engagement: Path, *, force: bool = False) -> dict:
    """Write an export the user names again and again (a handoff). A new file is
    published whole. An existing file is replaced, through a temporary file and a
    rename, only when it is still exactly what Torque last wrote there (its SHA-256 is
    kept in the engagement's state/exports.json), or with `force`; its previous content
    is kept as PATH.bak. A file someone edited, or one Torque did not write, is left as
    it is and the new render is written to PATH.new. Returns where it was written
    (`written`), the backup (`backup`) and whether an edited file was kept (`kept`)."""
    for candidate in (path, path.with_name(path.name + ".bak"), path.with_name(path.name + ".new")):
        if candidate.is_symlink() or (candidate.exists() and not candidate.is_file()):
            raise WorkspaceError(f"{candidate} is not a plain file; name another output, or move it away")
    if not path.parent.is_dir():
        raise WorkspaceError(f"output directory does not exist: {path.parent}")
    record_path = _inside(engagement, engagement / EXPORTS)
    try:
        written = _read_json(record_path) if record_path.is_file() else {}
    except WorkspaceError:
        written = {}
    result = {"written": path, "backup": None, "kept": False}
    if path.exists():
        current = _file_hash(path)
        if not force and written.get(_export_key(path)) != current:
            target = path.with_name(path.name + ".new")
            _atomic_replace_text(target, text)
            return {"written": target, "backup": None, "kept": True}
        backup = path.with_name(path.name + ".bak")
        fd, temporary = _temporary_file(path.parent, False)
        try:
            with os.fdopen(fd, "wb") as stream, open(path, "rb") as source:
                shutil.copyfileobj(source, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, backup)
        finally:
            Path(temporary).unlink(missing_ok=True)
        _atomic_replace_text(path, text)
        result["backup"] = backup
    else:
        atomic_write_new(path, text)
    written[_export_key(path)] = _file_hash(path)
    record_path.parent.mkdir(mode=0o700, exist_ok=True)
    _atomic_replace_text(record_path, json.dumps(written, indent=2) + "\n", private=True)
    return result


def output_lines(result: dict) -> list[str]:
    """What an export did, for the terminal."""
    if result["kept"]:
        original = result["written"].with_name(result["written"].name[:-len(".new")])
        return [str(result["written"]),
                f"{original} was edited since Torque wrote it (or Torque did not write it), so it is kept as it "
                f"is; the new render is in {result['written']}. Compare them, or replace it with --force (the "
                "previous content is then kept as .bak)."]
    lines = [str(result["written"])]
    if result["backup"]:
        lines.append(f"Replaced the earlier file; its previous content is in {result['backup']}")
    return lines


def output_report(result: dict) -> dict:
    return {"output": str(result["written"]), "replaced": result["backup"] is not None,
            "backup": str(result["backup"]) if result["backup"] else None, "kept_edited_file": result["kept"]}
