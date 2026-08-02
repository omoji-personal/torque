#!/usr/bin/env python3
"""Turn the cached documentation corpus into something a person can actually look things up in.

THE PROBLEM THIS SOLVES
Retrieval got us 29 MB of Salesforce documentation in ~466 KB slabs. That is not usable. Nobody
reads a 466 KB file to check whether a claim is true, and an entry written without checking is
how two of the catalogue's three known errors got written.

WHAT IT PRODUCES, AND THE ONE RULE THAT SHAPES IT
  knowledge/salesforce-docs/_sections.json   COMMITTED — an index, never the prose
  local/salesforce-docs-mirror/*.md          GITIGNORED — the prose

The split is not tidiness, it is the licence. The mirror declares none, so Torque does not
redistribute its text. What gets committed is what a library catalogue holds: which guide, which
version, which section, where it starts, how long it is, and the URL to go read it at the source.
That is enough to find a passage and cite it, and it carries no text that is not a heading.

Anyone cloning this repo gets the index immediately and runs `ingest.py --mirror-get` to obtain
the prose themselves, from the same pinned tag, at the same hashes.

USAGE
  extract.py --build                      (re)build the section index from whatever is cached
  extract.py --search "duplicate rules"   find sections; prints headings + citations
  extract.py --show <section-id>          print one section's text from the local cache
  extract.py --stats                      what is indexed, by guide
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
MIRROR = ROOT / "local" / "salesforce-docs-mirror"
INDEX = ROOT / "knowledge" / "salesforce-docs" / "_sections.json"

# Chunk at #### and deeper. Measured on the Apex guide: # is the document, ## and ### are part
# headings, #### is where a section starts being about ONE thing (57 of them per 466 KB slab).
# Coarser and a "section" spans unrelated material; finer and a lookup returns fragments.
SECTION_RE = re.compile(r"^(#{4,6})\s+(.+?)\s*$", re.M)
DOC_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.M)
SOURCE_RE = re.compile(r"^>\s*Source:\s*(\S+)", re.M)
VERSION_RE = re.compile(r"^Version\s+([\d.]+),\s*(.+?)\s*$", re.M)
UPDATED_RE = re.compile(r"^Last updated:\s*(.+?)\s*$", re.M)
# The PDF's table of contents survives conversion as dot-leader lines. It is pure noise and it
# is ~15% of some files, so it is dropped before indexing rather than polluting every search.
TOC_LINE = re.compile(r"\.\s*\.\s*\.\s*\.")

STOP = set("""a an the and or of to in for on is are be it this that with as by from at if not
you your we they can will may must into than then when where which who whom what how why do does
did done have has had using use used more most other some such no nor only own same so too very
s t just don should now about above after again all any because been before being below between
both during each few further here once during over under up out off""".split())


def _terms(text: str, k: int = 24) -> list:
    """A section's distinctive words — enough to find it, far short of reproducing it."""
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text.lower())
    counts = Counter(w for w in words if w not in STOP)
    return [w for w, _ in counts.most_common(k)]


def build() -> int:
    if not MIRROR.exists():
        print(f"no corpus cached at {MIRROR}\n"
              f"run: python3 scripts/salesforce-docs/ingest.py --mirror-get <path>")
        return 1
    sections, docs = [], {}
    for f in sorted(MIRROR.glob("*.md")):
        text = f.read_text(errors="replace")
        title = (DOC_TITLE_RE.search(text) or [None, f.stem])[1] if DOC_TITLE_RE.search(text) else f.stem
        src = (SOURCE_RE.search(text).group(1) if SOURCE_RE.search(text) else "")
        vm = VERSION_RE.search(text)
        docs[f.name] = {"guide": title, "source": src,
                        "version": vm.group(1) if vm else "",
                        "release": vm.group(2) if vm else "",
                        "updated": (UPDATED_RE.search(text).group(1) if UPDATED_RE.search(text) else ""),
                        "sha256": hashlib.sha256(text.encode()).hexdigest()[:16],
                        "bytes": len(text.encode())}
        marks = list(SECTION_RE.finditer(text))
        for i, m in enumerate(marks):
            start = m.end()
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            body = text[start:end]
            # drop table-of-contents sections; they match everything and mean nothing
            lines = [ln for ln in body.splitlines() if ln.strip()]
            if lines and sum(1 for ln in lines if TOC_LINE.search(ln)) > len(lines) * 0.3:
                continue
            if len(body.strip()) < 120:          # a heading with no substance under it
                continue
            sid = hashlib.sha256(f"{f.name}|{m.start()}|{m.group(2)}".encode()).hexdigest()[:12]
            sections.append({
                "id": sid, "doc": f.name, "guide": title,
                "level": len(m.group(1)), "heading": m.group(2)[:180],
                "start": start, "end": end, "chars": end - start,
                "terms": _terms(body),
                "urls": sorted(set(re.findall(r"https://developer\.salesforce\.com/\S+?(?=[\s)\]]|$)",
                                              body)))[:3],
            })
    # Continuation parts carry no `# Title` header — only part-01 does — so they fell back to
    # their filename and every citation from them named a slug instead of a guide. They are the
    # same document, so they inherit part-01's identity rather than inventing one.
    for name, meta in docs.items():
        if meta.get("version"):
            continue
        base = re.sub(r"-part-\d+\.md$", "", name)
        sib = next((m for n, m in docs.items()
                    if n != name and n.startswith(base) and m.get("version")), None)
        if sib:
            part = re.search(r"-part-(\d+)\.md$", name)
            meta["guide"] = f"{sib['guide']} (part {int(part.group(1))})" if part else sib["guide"]
            for k in ("version", "release", "updated", "source"):
                meta[k] = sib.get(k, "")
    for sec in sections:
        sec["guide"] = docs[sec["doc"]]["guide"]

    INDEX.parent.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps({"docs": docs, "sections": sections}, indent=1, sort_keys=True) + "\n")
    print(f"  {len(sections):,} sections from {len(docs)} document(s) -> {INDEX.relative_to(ROOT)}")
    print(f"  index is {INDEX.stat().st_size:,} B — headings and terms only, no prose")
    return 0


def _load():
    if not INDEX.exists():
        print("no index; run --build"); sys.exit(1)
    return json.loads(INDEX.read_text())


def search(query: str, limit: int = 12) -> int:
    d = _load()
    q = [w for w in re.findall(r"[a-z_][a-z0-9_]{2,}", query.lower()) if w not in STOP]
    if not q:
        print("query has no searchable terms"); return 1
    import math
    # how many sections each query term appears in — a term in 3 sections is far more
    # discriminating than one in 200, and flat scoring cannot tell them apart
    n = len(d["sections"]) or 1
    df = {w: sum(1 for s in d["sections"]
                 if w in s["terms"] or w in s["heading"].lower()) for w in q}
    idf = {w: math.log((n + 1) / (df.get(w, 0) + 1)) + 0.1 for w in q}
    scored = []
    for s in d["sections"]:
        head = s["heading"].lower()
        hit = 0.0
        matched = 0
        for w in q:
            in_head, in_body = w in head, w in s["terms"]
            if in_head or in_body:
                matched += 1
                # the heading says what a section is ABOUT; the body only says what it mentions
                hit += idf[w] * (3.0 if in_head else 1.0)
        if not matched:
            continue
        # a section answering several of the asked-about terms beats one shouting a single term
        hit *= (matched / len(q)) ** 1.5
        scored.append((round(hit, 2), s))
    if not scored:
        print(f"  nothing indexed matches {query!r} — that is a corpus gap, record it")
        return 2
    scored.sort(key=lambda x: (-x[0], -x[1]["chars"]))
    print(f"  {len(scored)} section(s) match {query!r}; top {min(limit, len(scored))}\n")
    for score, s in scored[:limit]:
        doc = d["docs"].get(s["doc"], {})
        print(f"  [{s['id']}] {s['heading'][:88]}")
        print(f"        {doc.get('guide','?')[:52]} v{doc.get('version','?')} "
              f"({doc.get('release','?')}) · {s['chars']:,} chars · score {score}")
        if s["urls"]:
            print(f"        {s['urls'][0][:96]}")
    print(f"\n  read one:  extract.py --show <id>")
    return 0


def show(sid: str) -> int:
    d = _load()
    s = next((x for x in d["sections"] if x["id"] == sid), None)
    if not s:
        print(f"no section {sid!r}"); return 1
    f = MIRROR / s["doc"]
    if not f.exists():
        print(f"section is indexed but its document is not cached: {s['doc']}\n"
              f"run: ingest.py --mirror-get documentation/{s['doc'].split('__',1)[-1]}")
        return 1
    doc = d["docs"].get(s["doc"], {})
    print(f"# {s['heading']}\n")
    print(f"  {doc.get('guide','?')} v{doc.get('version','?')} ({doc.get('release','?')}), "
          f"updated {doc.get('updated','?')}")
    print(f"  {doc.get('source','')}")
    print(f"  THIRD-PARTY MIRROR — confirm anything load-bearing against developer.salesforce.com "
          f"or a live org\n" + "-" * 88)
    print(f.read_text(errors="replace")[s["start"]:s["end"]].strip())
    return 0


def stats() -> int:
    d = _load()
    print(f"  {len(d['sections']):,} sections from {len(d['docs'])} document(s)\n")
    by = Counter(s["doc"] for s in d["sections"])
    for name, n in by.most_common():
        doc = d["docs"][name]
        print(f"   {n:5} sections  v{doc.get('version','?'):5} {doc.get('guide','?')[:56]}")
    total = sum(s["chars"] for s in d["sections"])
    print(f"\n  {total:,} chars of indexed prose, held locally; index itself "
          f"{INDEX.stat().st_size:,} B")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--search")
    ap.add_argument("--show")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    if a.build:
        return build()
    if a.search:
        return search(a.search)
    if a.show:
        return show(a.show)
    if a.stats:
        return stats()
    ap.error("give --build, --search QUERY, --show ID or --stats")


if __name__ == "__main__":
    sys.exit(main())
