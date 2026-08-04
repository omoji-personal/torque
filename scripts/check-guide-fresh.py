#!/usr/bin/env python3
"""Is the committed guide PDF stale relative to the HTML it is built from?

WHY THIS EXISTS. guide/Torque-Guide.pdf shipped publicly claiming 59 checks and 11 mutation
tests while the repo ran 72 and 15. It was 45 commits behind its own source. Nothing caught it:
claimed_counts scans guide/torque-guide.html and never the PDF, and named_mutators_exist — the
check written PRECISELY because a stale mutator transcript once shipped in the guide — also
scans the HTML only. So the checked surface was not the published surface, and the artifact a
reader downloads was the one artifact with no guard on it.

WHY IT COMPARES COMMITS RATHER THAN CONTENT. Reading numbers out of a PDF needs a text
extractor, which is a dependency CI would have to carry and which fails differently on every
platform. The question that actually matters is simpler and answerable from git alone: was the
PDF rebuilt after the last change to its source? If the HTML moved and the PDF did not, the PDF
is stale, whatever it happens to say.

This is a freshness guard, not a correctness guard. It cannot tell you the PDF's numbers are
right; it can tell you they were generated from the current source. The correctness half belongs
in claimed_counts, which needs the PDF added to its scan list — that file is protected, so it is
recorded here as owed rather than done.

Exit 0 fresh, 1 stale, 2 cannot tell.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = "guide/torque-guide.html"
PDF = "guide/Torque-Guide.pdf"


def last_commit(path):
    r = subprocess.run(["git", "log", "-1", "--format=%H %ct %s", "--", path],
                       capture_output=True, text=True, cwd=ROOT)
    line = r.stdout.strip()
    if not line:
        return None
    sha, ts, subject = line.split(" ", 2)
    return sha, int(ts), subject


def main():
    for rel in (SRC, PDF):
        if not (ROOT / rel).exists():
            print(f"cannot tell: {rel} is missing")
            return 2

    src, pdf = last_commit(SRC), last_commit(PDF)
    if not src or not pdf:
        print("cannot tell: no commit history for one of the files")
        return 2

    print(f"  source  {SRC}\n            {src[0][:9]}  {src[2][:64]}")
    print(f"  built   {PDF}\n            {pdf[0][:9]}  {pdf[2][:64]}")

    if pdf[1] >= src[1]:
        print("\nfresh: the PDF was committed no earlier than its source")
        return 0

    behind = subprocess.run(
        ["git", "rev-list", "--count", f"{pdf[0]}..HEAD", "--", SRC],
        capture_output=True, text=True, cwd=ROOT).stdout.strip() or "?"
    print(f"\nSTALE: {SRC} has changed in {behind} commit(s) since the PDF was last built.")
    print("The PDF is what a reader downloads. Rebuild it:  node guide/build-pdf.mjs")
    return 1


if __name__ == "__main__":
    sys.exit(main())
