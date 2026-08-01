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

META = {
    "/Title": "Torque — an AI operations workspace for Salesforce",
    "/Author": "Omid Mojtahedi",
    "/Subject": ("An AI agent that does real Salesforce work — and structurally cannot write "
                 "to production on its own."),
    "/Keywords": ("Salesforce, AI agents, Claude Code, agent safety, metadata deployment, "
                  "validation harness, adversarial testing"),
    "/Creator": "Torque — github.com/omoji-personal/torque",
}


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
    writer.add_metadata({**(reader.metadata or {}), **META})

    tmp = OUT.with_suffix(".pdf.tmp")               # write-then-rename: never leave a torn PDF
    with open(tmp, "wb") as fh:
        writer.write(fh)
    tmp.replace(OUT)
    print(f"  stamped: /Author={META['/Author']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
