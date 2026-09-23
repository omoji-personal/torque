"""De-identified mode: keep an AI session away from client orgs and client context."""
from __future__ import annotations
import json
import re
import shlex
import sys
from pathlib import Path

SF_LOCAL = {("project", "generate"), ("lightning", "generate"), ("apex", "generate"),
            ("--version",), ("version",), ("help",), ("plugins",)}
TORQUE_ALLOWED = {"demo", "workflows", "doctor", "--version", "--help", "-h"}
PATH_TOOLS = {"Read": "file_path", "Edit": "file_path", "Write": "file_path",
              "MultiEdit": "file_path", "NotebookEdit": "notebook_path", "Grep": "path", "Glob": "path"}


def _segments(command: str):
    for part in re.split(r"&&|\|\||;|\||\n", command):
        try:
            toks = shlex.split(part, posix=True)
        except ValueError:
            toks = part.split()
        if toks:
            yield toks


def _block_command(toks: list[str]) -> str:
    head = re.split(r"[/\\]", toks[0])[-1].lower()
    head = re.sub(r"\.(cmd|exe|bat)$", "", head)
    if head in {"sf", "sfdx"}:
        words = tuple(t for t in toks[1:3] if not t.startswith("-") or t == "--version")
        if words[:2] in SF_LOCAL or words[:1] in SF_LOCAL:
            return ""
        return f"{head} {' '.join(toks[1:3])} can reach a Salesforce org"
    if head == "torque" and len(toks) > 1 and toks[1] not in TORQUE_ALLOWED:
        return f"torque {toks[1]} reads client context or an org"
    return ""


def decide(tool_name: str, tool_input: dict, workspace: Path, mode: str) -> tuple[bool, str]:
    if mode != "build-only":
        return True, ""
    if tool_name == "Bash":
        for toks in _segments(str(tool_input.get("command", ""))):
            reason = _block_command(toks)
            if reason:
                return False, f"De-identified mode: {reason}. Run it yourself outside the AI session."
        return True, ""
    key = PATH_TOOLS.get(tool_name)
    if key and tool_input.get(key):
        target = Path(str(tool_input[key]))
        clients = workspace / "clients"
        if target == clients or clients in target.parents:
            return False, "De-identified mode: client context stays out of the AI session."
        if tool_name != "Read" and target.name == "workspace.json":
            return False, "De-identified mode: only the owner changes workspace.json."
    return True, ""


def _workspace_mode(start: Path) -> tuple[Path, str]:
    for folder in [start, *start.parents]:
        config = folder / "workspace.json"
        if config.is_file():
            data = json.loads(config.read_text(encoding="utf-8"))
            return folder, data.get("ai_access", "full")
    return start, "full"


def main() -> int:
    event = json.load(sys.stdin)
    root, mode = _workspace_mode(Path(event.get("cwd") or ".").resolve())
    allowed, reason = decide(event.get("tool_name", ""), event.get("tool_input") or {}, root, mode)
    if not allowed:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
