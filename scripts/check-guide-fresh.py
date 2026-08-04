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
satisfiable by accident.

Commit order was briefly kept as a fallback for PDFs built before the stamp existed. That is
gone. It ran in CI on its first push — pypdf was not installed there — and reported STALE from
commit archaeology that was wrong about a repository where the strong check would have said
fresh. A guard that silently degrades to a weaker test is the defect class this repo keeps
finding, and a weaker test that is also wrong is worse than no test.

So: if the stamp cannot be read, this reports that it could not run, exit 2, and says why. A
check that cannot reach its subject is BLOCKED with a reason, never a verdict from something
else it happened to be able to measure.

This is a freshness guard, not a correctness guard. It cannot tell you the PDF's numbers are
right; it can tell you they were generated from the current source. The correctness half belongs
in claimed_counts, which needs the PDF added to its scan list — that file is protected, so it is
recorded here as owed rather than done.

Exit 0 fresh, 1 stale, 2 cannot tell.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = "guide/torque-guide.html"
PDF = "guide/Torque-Guide.pdf"


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
    if not stamped:
        print("BLOCKED: the PDF carries no readable source stamp.")
        print("  Either pypdf is not installed here, or the PDF predates the stamp.")
        print("  Not falling back to comparing commit order: that test is weaker, it is")
        print("  satisfied by any change to the PDF, and it reported a wrong answer the one")
        print("  time it ran. A check that cannot run says so.")
        print("  Fix:  pip install pypdf  &&  node guide/build-pdf.mjs")
        return 2

    print(f"  source sha  {actual[:16]}…")
    print(f"  PDF records {stamped[:16]}…")
    if stamped == actual:
        print("\nfresh: the PDF records the hash of the current source")
        return 0
    print(f"\nSTALE: the PDF was built from a different {SRC}.")
    print("Rebuild it:  node guide/build-pdf.mjs")
    return 1

if __name__ == "__main__":
    sys.exit(main())
