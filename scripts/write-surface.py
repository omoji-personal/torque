#!/usr/bin/env python3
"""What can this session actually change? Ask before planning, not after.

WHY THIS EXISTS. On 2026-08-03 a session was handed a 25-item defect punch list, started at
item A1, and discovered by being refused that 19 of the 25 named files are behind the write
gate. The punch list had been written from a code survey, and a code survey does not ask which
files an agent may write. Roughly half a context window went on mapping a boundary that is
mechanically knowable in under a second.

The boundary is not a bug and this tool does not move it. Torque's gates deny agent edits to
hooks/, bin/, .claude/, harness/checks/, knowledge/, local/orgs/ and a list of protected
basenames, with no token path — deliberately, because the gate's own source is exactly what an
agent must not rewrite. What was missing was a way to KNOW that up front.

It asks lib and shellparse directly rather than restating their rules. A second copy of the
predicate would drift from the real one, and a drifted copy of a security boundary reads as
reassurance while being wrong — the failure mode this repo has now found in three guards that
shared an assumption rather than code. If the gate changes, this follows it. Read-only: it
opens nothing for writing and contacts no org.

    python3 scripts/write-surface.py                      # survey the tracked tree
    python3 scripts/write-surface.py PATH [PATH ...]      # check paths; exit 1 if any blocked
    python3 scripts/write-surface.py --plan DOC.md        # check every path a document names

--plan is the one that pays. Point it at a handoff or punch list BEFORE working it, and it
reports which items are reachable and which need operator-present issuance.

Exit: 0 all queried paths writable · 1 at least one blocked · 2 usage error.
"""
from __future__ import annotations
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))

try:
    import lib
    import shellparse
except Exception as e:                                  # pragma: no cover - environment problem
    print(f"cannot import the gate's own predicates ({e}).\n"
          "This tool refuses to guess: an answer derived from a reimplementation would be a "
          "second copy of a security boundary, which is worse than no answer.", file=sys.stderr)
    raise SystemExit(2)


def blocked_reason(path: str) -> str:
    """Empty string if the agent may write it, else the reason the gate would give.

    Mirrors prod_write_gate._protected_reason by CALLING the same predicates it calls.
    """
    try:
        resolved = str(Path(path).expanduser().resolve())
    except Exception:
        resolved = path
    if shellparse.anchor_ref(resolved):
        return "trust anchor (signing secret / tokens) — never grantable"
    if os.path.basename(resolved) in shellparse.PROTECTED_BASENAMES:
        return f"protected basename ({os.path.basename(resolved)}) — protected wherever it lives"
    if lib.is_protected_target(resolved):
        return "inside a protected directory"
    return ""


def tracked_files() -> list[str]:
    r = subprocess.run(["git", "ls-files"], capture_output=True, text=True, cwd=ROOT)
    return [l for l in r.stdout.split("\n") if l.strip()]


def report(paths, label) -> int:
    width = min(max((len(p) for p in paths), default=10), 58)
    blocked = []
    for p in paths:
        why = blocked_reason(str(ROOT / p) if not os.path.isabs(p) else p)
        if why:
            blocked.append(p)
        print(f"  {'BLOCKED' if why else 'writable':9} {p:<{width}}  {why}")
    print(f"\n{label}: {len(paths) - len(blocked)} writable, {len(blocked)} blocked")
    if blocked:
        print("\nBlocked paths need operator-present issuance — the operator applies the change\n"
              "outside the agent's tool surface. There is no token that unlocks an artifact edit;\n"
              "torque approve mints ORG operation tokens only. See docs/MAINTAINER-MODE.md for the\n"
              "designed way through.")
    return 1 if blocked else 0


def survey() -> int:
    groups: dict[str, list[int]] = {}
    for f in tracked_files():
        top = f.split("/")[0] + ("/" if "/" in f else "")
        why = blocked_reason(str(ROOT / f))
        g = groups.setdefault(top, [0, 0])
        g[1 if why else 0] += 1
    print("  writable  blocked  area")
    for area in sorted(groups):
        w, b = groups[area]
        print(f"  {w:>8}  {b:>7}  {area}")
    tw = sum(g[0] for g in groups.values())
    tb = sum(g[1] for g in groups.values())
    print(f"  {tw:>8}  {tb:>7}  TOTAL")
    print("\nAlso locked, and not a path question: an org-touching harness run.\n"
          "  python3 harness/validate.py --profile capability --target-org <org>\n"
          "is refused as a Salesforce operation via interpreter. Targeted read-only checks DO\n"
          "run: python3 harness/validate.py --only <check>. Ask the operator to run profile runs\n"
          "with the ! prefix so the output lands in the conversation.")
    return 0


# A path-shaped token: has a separator or a known extension, and no spaces.
_PATH_RE = re.compile(r"`([^`\s]+\.(?:py|md|json|yml|yaml|txt|html|sh|toml)|[^`\s]*/[^`\s]*)`")


def from_plan(doc: str) -> int:
    text = Path(doc).read_text()
    found, seen = [], set()
    for m in _PATH_RE.finditer(text):
        p = m.group(1).split(":")[0].rstrip("/.,")          # strip line refs like file.py:94
        if p in seen or not p or p.startswith(("http", "-")):
            continue
        if not (ROOT / p).exists():
            continue
        # Must actually live in THIS repo. Prose about paths elsewhere ("/Users/**/…" in the
        # C9 write-up) resolves to something real on disk and would otherwise be reported as
        # writable, which is true and useless — the reader would take it for a repo file.
        # Path.is_relative_to would read better and is 3.9+; the floor here is 3.8.
        if not str((ROOT / p).resolve()).startswith(str(ROOT) + os.sep):
            continue
        seen.add(p)
        found.append(p)
    if not found:
        print(f"no existing repo paths named in {doc}.\n"
              "Empty is not an answer here either: check the document really does name paths in\n"
              "backticks before concluding it has nothing to gate on.")
        return 2
    print(f"paths named by {doc}:\n")
    return report(found, "plan")


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if args and args[0] == "--plan":
        if len(args) < 2:
            print("usage: write-surface.py --plan DOC.md", file=sys.stderr)
            return 2
        return from_plan(args[1])
    if args:
        return report(args, "queried")
    return survey()


if __name__ == "__main__":
    raise SystemExit(main())
