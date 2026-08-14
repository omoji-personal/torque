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

# The mutator count lives in validate.py and was stated in four documents; nothing re-derived
# it, so it stayed at 11 through two additions. Same treatment as the check counts.
mutators = int(re.search(r"TOTAL_MUTATORS = (\d+)",
                         (ROOT / "harness" / "validate.py").read_text()).group(1))
# These are exactly the files claimed_counts scans (harness/checks/check_kb.py:~1272) — keep
# the two lists identical, or the syncer fixes some and the check fails on the rest, which is
# how it found bin/torque-demo. The previous revision of this comment said ROADMAP.md was in
# the syncer only, pending B3; B3 landed, the checker scans it, and the asymmetry that
# remained was the OTHER way: bin/torque-week and guide/TORQUE-GUIDE.md were checked but not
# synced, so a check added there meant a manual edit the checker would find and nobody would
# automate — the exact friction this script exists to remove.
for rel in ("README.md", "guide/torque-guide.html", "bin/torque-demo", "bin/torque-init",
            "bin/torque-week", "ROADMAP.md", "guide/TORQUE-GUIDE.md"):
    f = ROOT / rel
    if not f.exists():
        continue
    body = re.sub(r"\d{1,3} (mutators|mutation tests)", lambda m: f"{mutators} {m.group(1)}",
                  f.read_text())
    # The per-profile breakdown, in the prose form the documents actually use:
    #   "72 checks (52 static, 69 capability, 72 release)"
    # claimed_counts verifies each of those names against its own cumulative total, and used to
    # catch drift here that this syncer could not repair — so every check added meant a manual
    # edit the checker would find and nobody would automate. Rewriting them closes that loop:
    # the checker and the rewriter now cover the same claim.
    body = re.sub(r"(\d{1,3})\s+(static|capability|release)\b",
                  lambda m: f"{total[m.group(2)]} {m.group(2)}", body)
    # and the headline total, which is the release-profile count
    body = re.sub(r"(\d{2,4}) checks \(", f"{total['release']} checks (", body)
    f.write_text(body)

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
      f"release={total['release']} mutators={mutators}")
