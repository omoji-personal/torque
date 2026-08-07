#!/usr/bin/env python3
"""Harvest every command example Salesforce publishes, into a must-allow corpus.

WHY THIS EXISTS. gate_fixtures_r17.json is twenty hand-written allow cases, and the lesson that
produced it was that hand-imagined allows are a narrower distribution than real use: 193 fixtures
with 44 allows still missed a defect that denied 71% of six months of genuine work. The corrective
is commands nobody in this repository wrote.

The practitioner corpus is the sharp one and it cannot live here — it carries client names, org
Ids and query text. This is the OTHER kind: the `examples` field of every command the installed
Salesforce CLI ships. Nobody wrote them to pass or fail a gate, they are public documentation, and
they can therefore be committed and re-derived by anyone.

WHAT IT IS NOT. Documented usage is not real use. These are pristine, single-line and free of
client data, where real practitioner commands are overwhelmingly multi-line. A corpus of vendor
examples cannot answer the question must_allow_corpus_has_no_shape_denials asks, which is why
that check still reports NA until an operator points it at their own commands, and why the check
fed by this file carries a different name.

THE TEMPLATING MATTERS. oclif writes examples with `<%= config.bin %>` and `<%= command.id %>`
rather than literal text. Expanding those is not cosmetic: harvesting without it collected 9 of
734 commands, because almost every example began with the unexpanded token and did not look like
a Salesforce command at all.

    python3 scripts/harvest-vendor-corpus.py > harness/tests/vendor-corpus.txt
"""
import glob
import json
import os
import re
import sys

# TWO ROOTS, and missing the second one costs a third of the corpus. The core install lives under
# /usr/local/lib/sf; every plugin the user added themselves lives under ~/.local/share/sf. Reading
# only the first harvested 457 of 729 invocations — 107 command ids absent, concentrated in
# `agent`, `template` and `devops`, which are exactly the plugins someone installs deliberately.
# A corpus that silently drops a third of itself still reports a rate, for a sample nobody chose.
MANIFEST_ROOTS = (
    "/usr/local/lib/sf",
    os.path.expanduser("~/.local/share/sf"),
)


def expand(text, cid):
    """Resolve the oclif template tokens an example is written with."""
    text = re.sub(r"<%=\s*config\.bin\s*%>", "sf", text)
    text = re.sub(r"<%=\s*command\.id\s*%>", cid.replace(":", " "), text)
    return re.sub(r"<%=.*?%>", "", text).strip()


def main():
    seen, rows = set(), []
    manifests, unreadable = [], 0
    for root in MANIFEST_ROOTS:
        manifests.extend(glob.glob(root + "/**/oclif.manifest.json", recursive=True))
    for man in manifests:
        try:
            with open(man) as fh:
                doc = json.load(fh)
        except Exception:                                    # noqa: BLE001
            unreadable += 1        # counted and reported, never silently skipped
            continue
        for cid, meta in (doc.get("commands") or {}).items():
            for ex in (meta.get("examples") or []):
                body = ex.get("command") if isinstance(ex, dict) else ex
                if not isinstance(body, str):
                    continue
                for line in body.splitlines():
                    line = expand(line, cid)
                    # Prose lines surround the commands in oclif examples; keep only invocations.
                    if not line.startswith("sf ") or line in seen:
                        continue
                    seen.add(line)
                    rows.append(line)

    if not rows:
        print("harvested nothing — is the Salesforce CLI installed at the expected path?",
              file=sys.stderr)
        return 1
    print("# Command examples published by the Salesforce CLI, harvested from the oclif")
    print("# manifests of the installed plugins. Public documentation, no client data.")
    print("# Regenerate: python3 scripts/harvest-vendor-corpus.py")
    print(f"# harvested {len(rows)} invocations from {len(manifests)} manifest(s)"
          + (f"; {unreadable} manifest(s) UNREADABLE and contributed nothing" if unreadable else ""))
    print("# A snapshot: the set depends on which plugins are installed, so a regeneration on")
    print("# another machine will legitimately differ. The count above is the one to compare.")
    for line in rows:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
