#!/usr/bin/env python3
"""Stamp author metadata onto the built guide.

WHY: Chromium's PDF writer sets /Creator and /Author to "Chromium". A document sent to a
reader as a portfolio piece then shows "Chromium" as its author in every PDF viewer, in
Finder's Get Info, and in any document-management system that indexes it. The bytes were
right and the attribution was wrong.

Non-fatal by design: if pypdf is absent the guide still builds, because a metadata stamp is
polish and must never be able to break the deliverable.

    python3 guide/stamp-metadata.py [path/to.pdf]
"""
import sys
from pathlib import Path

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "Torque-Guide.pdf"

# These strings are governed by docs/DESCRIBING-TORQUE.md, and both of the first two used to
# violate it — in the metadata, which is the first thing a reader sees in their PDF viewer's
# title bar, before a single page renders.
#
# /Title said "an AI operations WORKSPACE for Salesforce". The canonical noun phrase is "an
# AI-agent operations LAYER for Salesforce", and the reasoning is written down: it is not a
# place, it sits between the agent and the org. Workspace was one of four competing noun phrases
# the describing document was written to end.
#
# /Subject led with the constraint: "...and structurally cannot write to production on its own."
# The framing rule says copy that leads with what the agent cannot do sells the constraint
# instead of the product, and reads as a smaller thing than it is. Safety is the enabler, stated
# second. So the subject now says what it makes possible.
META = {
    "/Title": "Torque — an AI-agent operations layer for Salesforce",
    "/Author": "Omid Mojtahedi",
    "/Subject": ("Let an AI agent do real Salesforce work on the orgs that matter. It knows the "
                 "platform, shows what an operation will set off before it runs, and verifies "
                 "its changes in the org rather than in a return code."),
    "/Keywords": ("Salesforce, AI agents, Agentforce, Claude Code, agent safety, MCP, "
                  "metadata deployment, validation harness, adversarial testing"),
    "/Creator": "Torque — github.com/omoji-personal/torque",
}


def source_sha():
    """SHA-256 of the HTML this PDF is rendered from, stamped into the PDF itself.

    The freshness guard first compared COMMIT ORDER — was the PDF committed no earlier than its
    source. That catches the ordinary mistake (edit the HTML, forget to rebuild) and nothing
    else: any change to the PDF satisfies it, so a touched-but-not-rebuilt PDF passes.

    A hash of the source, carried inside the artifact, is not satisfiable by accident. Either the
    PDF records the HTML it was built from or it does not, and scripts/check-guide-fresh.py can
    compare that to the HTML on disk without a text extractor, a PDF parser in CI, or any
    assumption about commits.
    """
    import hashlib
    src = OUT.parent / "torque-guide.html"
    if not src.exists():
        return None
    return hashlib.sha256(src.read_bytes()).hexdigest()


def main():
    if not OUT.exists():
        print(f"  ! no PDF at {OUT}", file=sys.stderr)
        return 1
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        print("  · pypdf not installed — PDF metadata left as Chromium's default "
              "(pip install pypdf to stamp it)", file=sys.stderr)
        return 0                                    # never fail the build over polish

    reader = PdfReader(str(OUT))
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)
    meta = {**(reader.metadata or {}), **META}
    sha = source_sha()
    if sha:
        meta["/TorqueSourceSHA256"] = sha           # custom key; ignored by every viewer
    writer.add_metadata(meta)

    tmp = OUT.with_suffix(".pdf.tmp")               # write-then-rename: never leave a torn PDF
    with open(tmp, "wb") as fh:
        writer.write(fh)
    tmp.replace(OUT)
    print(f"  stamped: /Author={META['/Author']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
