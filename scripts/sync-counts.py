#!/usr/bin/env python3
"""Re-derive every check count stated in the guide. Run after adding or removing a check.

`claimed_counts` fails the build when a stated number drifts, which is correct and has caught
several commits in this repo. But re-deriving the numbers by hand every time is friction, and
friction against a correctness check is how someone eventually decides the check is the problem.
This does it in one command, from the registry, so the documentation cannot be wrong and cannot
be tedious at the same time.
"""
import pathlib
import re
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import validate as v  # noqa: E402

counts = Counter(profile for _name, profile, _cat, _fn in v.REGISTRY)
total = {pr: sum(n for p, n in counts.items() if v.RANK[p] <= v.RANK[pr]) for pr in v.PROFILES}

guide = ROOT / "guide" / "torque-guide.html"
text = guide.read_text()
for pattern, replacement in (
    (r"<code>static</code> \(\d+ checks,", f"<code>static</code> ({total['static']} checks,"),
    (r"<code>capability</code> \(\d+ checks,",
     f"<code>capability</code> ({total['capability']} checks,"),
    (r"\d+ checks, live org", f"{total['release']} checks, live org"),
    (r"<td>\d+ checks,", f"<td>{total['release']} checks,"),
    # The bare parenthesised form drifted to (27) while every other figure stayed current,
    # because nothing here matched it.
    (r"<code>release</code> \(\d+\)", f"<code>release</code> ({total['release']})"),
):
    text = re.sub(pattern, replacement, text)
guide.write_text(text)

print(f"synced: static={total['static']} capability={total['capability']} "
      f"release={total['release']}")
