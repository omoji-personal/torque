#!/usr/bin/env python3
"""Ingest Salesforce's own documentation corpus, so entries can be written from it.

WHY THIS EXISTS
The catalogue's `documented` tier is only as good as what backs it, and until now the backing was
recall plus a guide name. Two of the three factual errors external review found in the catalogue
were written that way: correct-sounding, cited afterwards, wrong.

The obvious fix — fetch the page — does not work against `help.salesforce.com`, which serves an
LWR JavaScript shell; you get a loading spinner, not a document. Salesforce does publish a
machine-readable corpus for exactly this purpose at `developer.salesforce.com/docs/llms.txt`: an
index of documentation sets, each a plain-text `llms-<set>.txt`. First-party, so it outranks any
third-party mirror in the precedence chain, and stable enough that a content hash means something.

WHAT IS AUTOMATABLE, MEASURED
Two tiers, and the difference is not a design choice — it is what the server allows.
  Tier A, scripted: `llms.txt` and every `llms-<set>.txt`. These fetch fine unattended, and each
    one enumerates its leaf pages, so the corpus can be COUNTED even where it cannot be pulled.
  Tier B, assisted: the `.md` leaf pages the indexes point at. Those return HTTP 403 to a
    scripted client — tested with both a bare and a browser User-Agent — so they are fetched
    during a working session by a browser-grade client and land as cited entries.
Pretending Tier B is scripted would put a promise in this file that the network refuses to keep,
which is the failure mode this whole repo exists to avoid. `_leaves.json` is the work queue: every
leaf the indexes name, so what has NOT been read is as visible as what has.

WHAT IT DOES NOT DO
It does not decide anything. It fetches, hashes and records. Turning a document into a catalogue
entry is a judgement, and the catalogue's rules about how a claim is known apply unchanged —
`documented` still requires the page, and `verified-live` still requires a verifier that can
return False against a real org.

USAGE
  ingest.py --list                 what the index offers, and what is cached
  ingest.py --set platform         fetch one set
  ingest.py --priority             fetch the sets Torque's own surface touches
  ingest.py --verify               re-hash the cache; report drift without fetching
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CACHE = ROOT / "knowledge" / "salesforce-docs"
INDEX = CACHE / "_index.json"
LLMS_INDEX = "https://developer.salesforce.com/docs/llms.txt"

# The sets that touch what Torque adjudicates. Marketing, Commerce, Industries and the mobile
# SDKs are deliberately absent: Torque is an org-operations layer, and a corpus that grows past
# the surface it serves is weight without reach.
PRIORITY = ["platform", "salesforcedx", "security", "metadata-coverage",
            "connect-rest", "event-bus", "graphql-api", "code-analyzer", "dataloader"]

UA = "torque-docs-ingest (+https://github.com/omoji-personal/torque)"
TIMEOUT = 60

# TIER B — the full prose corpus, which the leaves cannot give us.
# A community mirror of Salesforce's documentation as markdown: 68 files, ~29 MB, served from
# raw.githubusercontent with no bot protection, so unlike the .md leaves it is genuinely
# scriptable. Pinned to a tag, because an unpinned mirror is a corpus that changes under a
# citation.
#
# THREE THINGS ABOUT IT THAT MATTER, and none is a detail:
#   1. It declares NO LICENCE. So it is fetched into a GITIGNORED cache and never committed —
#      Torque does not redistribute someone else's unlicensed copy of Salesforce's docs.
#   2. It is THIRD-PARTY. source_kind: mirror, which ranks BELOW developer.salesforce.com in the
#      precedence chain. A claim that matters gets confirmed against Salesforce's own site or a
#      live org before it becomes a catalogue entry.
#   3. It is a small project (single maintainer) and could go away. The tag and the content hash
#      are recorded so a citation stays checkable even if it does.
MIRROR_REPO = "damecek/salesforce-documentation-context"
MIRROR_TAG = "v1.3.0"
MIRROR_CACHE = ROOT / "local" / "salesforce-docs-mirror"      # gitignored, deliberately
GH_TREE = f"https://api.github.com/repos/{MIRROR_REPO}/git/trees/{MIRROR_TAG}?recursive=1"
GH_RAW = f"https://raw.githubusercontent.com/{MIRROR_REPO}/{MIRROR_TAG}/"


def mirror_list() -> list:
    """Every markdown document in the mirror, with its size. Cheap: one API call, no downloads."""
    tree = json.loads(_get(GH_TREE))
    if tree.get("truncated"):
        print("  WARNING: GitHub truncated the tree; the list below is incomplete")
    return [(x["path"], x.get("size", 0)) for x in tree.get("tree", []) if x["path"].endswith(".md")]


def mirror_fetch(paths: list) -> int:
    """Pull named documents into the gitignored cache, recording tag and hash for each."""
    MIRROR_CACHE.mkdir(parents=True, exist_ok=True)
    meta_path = MIRROR_CACHE / "_mirror-index.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    total = 0
    for rel in paths:
        try:
            body = _get(GH_RAW + rel)
        except Exception as e:
            print(f"  FAILED    {rel} ({type(e).__name__})")
            continue
        dest = MIRROR_CACHE / rel.replace("/", "__")
        dest.write_text(body)
        meta[rel] = {"repo": MIRROR_REPO, "tag": MIRROR_TAG,
                     "sha256": hashlib.sha256(body.encode()).hexdigest(),
                     "bytes": len(body.encode()), "fetched": date.today().isoformat(),
                     "source_kind": "mirror"}
        total += len(body.encode())
        print(f"  cached    {rel:58} {len(body.encode()):>9,} B")
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    return total


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def _load_index() -> dict:
    if INDEX.exists():
        try:
            return json.loads(INDEX.read_text())
        except Exception:
            return {}
    return {}


def _save_index(d: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(d, indent=2, sort_keys=True) + "\n")


def available() -> dict:
    """set-name -> url, from Salesforce's own index."""
    out = {}
    for m in re.finditer(r"\[([^\]]+)\]\((https://developer\.salesforce\.com/docs/llms-([a-z0-9-]+)\.txt)\)",
                         _get(LLMS_INDEX)):
        out[m.group(3)] = m.group(2)
    return out


def fetch(name: str, url: str, idx: dict) -> tuple[str, int, bool]:
    """Fetch one set. Returns (status, bytes, changed). Never raises on a network failure —
    a corpus that cannot be refreshed must not take the whole run down with it."""
    try:
        body = _get(url)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        return f"FAILED ({type(e).__name__})", 0, False
    digest = hashlib.sha256(body.encode()).hexdigest()
    prev = (idx.get(name) or {}).get("sha256")
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / f"{name}.txt").write_text(body)
    idx[name] = {"url": url, "sha256": digest, "bytes": len(body.encode()),
                 "fetched": date.today().isoformat(), "source_kind": "official"}
    return ("unchanged" if prev == digest else ("updated" if prev else "new")), len(body.encode()), prev != digest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--priority", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--mirror-list", action="store_true",
                    help="list the third-party prose corpus without downloading it")
    ap.add_argument("--mirror-get", action="append", default=[],
                    help="cache one mirror document by path (gitignored, never committed)")
    a = ap.parse_args()
    idx = _load_index()

    if a.verify:
        if not idx:
            print("no corpus cached"); return 1
        bad = 0
        for name, meta in sorted(idx.items()):
            f = CACHE / f"{name}.txt"
            if not f.exists():
                print(f"  MISSING  {name}"); bad += 1; continue
            got = hashlib.sha256(f.read_text().encode()).hexdigest()
            if got != meta.get("sha256"):
                print(f"  DRIFTED  {name} — on disk does not match the recorded hash"); bad += 1
            else:
                print(f"  ok       {name:22} {meta['bytes']:>9,} B  fetched {meta['fetched']}")
        total = sum(m.get("bytes", 0) for m in idx.values())
        print(f"\n  {len(idx)} set(s), {total:,} B, {bad} problem(s)")
        return 1 if bad else 0

    if a.mirror_list:
        docs = mirror_list()
        print(f"  {MIRROR_REPO} @ {MIRROR_TAG} — {len(docs)} document(s), "
              f"{sum(n for _, n in docs):,} B  [third-party mirror, no licence declared]\n")
        for path, n in sorted(docs, key=lambda x: -x[1]):
            print(f"    {n:>10,} B  {path}")
        return 0

    if a.mirror_get:
        n = mirror_fetch(a.mirror_get)
        print(f"\n  {n:,} B cached under {MIRROR_CACHE} (gitignored; mirror, not authoritative)")
        return 0

    if a.list:
        av = available()
        print(f"  {len(av)} set(s) offered by Salesforce's index; {len(idx)} cached\n")
        for name in sorted(av):
            mark = "cached" if name in idx else ("PRIORITY" if name in PRIORITY else "")
            print(f"    {name:34} {mark}")
        missing = [p for p in PRIORITY if p not in av]
        if missing:
            print(f"\n  NOT OFFERED under these names: {missing}")
        return 0

    av = available()
    want = a.set or (PRIORITY if a.priority else [])
    if not want:
        ap.error("give --set NAME, --priority, --list or --verify")
    rc = 0
    for name in want:
        url = av.get(name)
        if not url:
            print(f"  SKIP     {name:22} not offered by the index under that name"); rc = 1; continue
        status, n, _ = fetch(name, url, idx)
        print(f"  {status:9} {name:22} {n:>9,} B")
        if status.startswith("FAILED"):
            rc = 1
    _save_index(idx)
    print(f"\n  cache: {sum(m.get('bytes', 0) for m in idx.values()):,} B across {len(idx)} set(s)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
