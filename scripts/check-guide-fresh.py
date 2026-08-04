#!/usr/bin/env python3
"""Is the committed guide PDF stale relative to the HTML it is built from?

WHY THIS EXISTS. guide/Torque-Guide.pdf shipped publicly claiming 59 checks and 11 mutation
tests while the repo ran 72 and 15. It was 45 commits behind its own source. Nothing caught it:
claimed_counts scans guide/torque-guide.html and never the PDF, and named_mutators_exist — the
check written PRECISELY because a stale mutator transcript once shipped in the guide — also
scans the HTML only. So the checked surface was not the published surface, and the artifact a
reader downloads was the one artifact with no guard on it.

HOW IT ANSWERS THE QUESTION. The build stamps a SHA-256 of the HTML into the PDF's own metadata,
so the artifact records which source produced it. This compares that to the HTML on disk. No text
extractor, no parsing of page content, no assumption about commit order.

The first version compared COMMIT ORDER — was the PDF committed no earlier than its source. That
catches the ordinary mistake (edit the HTML, forget to rebuild) and nothing else: ANY change to
the PDF satisfies it, so a touched-but-not-rebuilt file passes. A recorded source hash is not
satisfiable by accident. Commit order is kept as the fallback for PDFs built before the stamp
existed, and the output says which test it actually ran, because a guard that silently degrades
to the weaker check is the class of defect this repo keeps finding.

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


def stamped_source_sha():
    """The source hash the PDF records, or None if it carries none / cannot be read."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        meta = PdfReader(str(ROOT / PDF)).metadata or {}
        return meta.get("/TorqueSourceSHA256")
    except Exception:
        return None


def main():
    for rel in (SRC, PDF):
        if not (ROOT / rel).exists():
            print(f"cannot tell: {rel} is missing")
            return 2

    # Preferred test: does the PDF record the hash of the HTML that is on disk right now?
    import hashlib
    actual = hashlib.sha256((ROOT / SRC).read_bytes()).hexdigest()
    stamped = stamped_source_sha()
    if stamped:
        print(f"  source sha  {actual[:16]}…")
        print(f"  PDF records {stamped[:16]}…")
        if stamped == actual:
            print("\nfresh: the PDF records the hash of the current source")
            return 0
        print(f"\nSTALE: the PDF was built from a different {SRC}.")
        print("Rebuild it:  node guide/build-pdf.mjs")
        return 1
    print("  (PDF carries no source stamp — falling back to commit order, which is weaker: "
          "any change to the PDF satisfies it)")

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
