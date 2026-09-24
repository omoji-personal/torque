"""Is a person at a real terminal, outside the agent session?

This checks the agent's tool surface, not an operating-system boundary: code the
agent writes and runs under the same account can fake a terminal. See
docs/connected-approval.md for the limits."""
from __future__ import annotations

from dataclasses import dataclass
import os
import secrets
import subprocess
import sys

# Claude Code sets these for every tool subprocess (verified, docs/connected-approval.md).
AGENT_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")
# A process whose command name contains one of these is an agent host.
AGENT_PROCESS_MARKERS = ("claude",)
# No 0/O or 1/I: the code is read off a screen and typed back.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6
MAX_ANCESTORS = 64


@dataclass(frozen=True)
class Presence:
    ok: bool
    reason: str


def _process_ancestors(limit: int = MAX_ANCESTORS) -> list[tuple[int, str]]:
    """(pid, command name) for each ancestor process, nearest first. The list
    ends with (-1, "<unknown>") when the ancestry could not be read."""
    chain: list[tuple[int, str]] = []
    pid = os.getppid()
    for _ in range(limit):
        if pid <= 1:
            return chain
        try:
            out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True,
                                 text=True, timeout=2).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return chain + [(-1, "<unknown>")]
        parts = out.split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            return chain + [(-1, "<unknown>")]
        chain.append((pid, parts[1]))
        pid = int(parts[0])
    return chain + [(-1, "<unknown>")]


def operator_present(env=None, stdin=None, stdout=None, ancestors=None) -> Presence:
    """All must hold: stdin and stdout are terminals; no agent environment marker
    is set; and (macOS, Linux) no ancestor process is the agent host. On Windows
    only the first two are checked, which is weaker."""
    env = os.environ if env is None else env
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    try:
        terminal = bool(stdin and stdout and stdin.isatty() and stdout.isatty())
    except (AttributeError, OSError, ValueError):
        terminal = False
    if not terminal:
        return Presence(False, "this needs a real terminal; run it yourself, not through the AI session")
    found = [name for name in AGENT_ENV if name in env]
    if found:
        return Presence(False, f"this looks like an agent session ({', '.join(found)} is set)")
    if os.name != "nt":
        for pid, command in (ancestors or _process_ancestors)():
            if pid == -1:
                return Presence(False, "could not read the process ancestry; refusing")
            name = os.path.basename(command.strip()).casefold()
            if any(marker in name for marker in AGENT_PROCESS_MARKERS):
                return Presence(False, f"an agent process ({name}) is an ancestor of this command")
    return Presence(True, "")


def confirm_code(stdin=None, stdout=None, choose=secrets.choice) -> bool:
    """Print a random code and require it typed back. Slows a reflexive yes;
    it is not authentication."""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    code = "".join(choose(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    stdout.write(f"Type this code to confirm: {code}\n> ")
    stdout.flush()
    typed = (stdin.readline() or "").strip().upper()
    return secrets.compare_digest(typed.encode(), code.encode())
