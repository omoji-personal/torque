#!/usr/bin/env python3
"""Write the list of checks the gate may run against a live org.

The gate cannot import the harness — validate.py execs every plugin at import time, and a gate
that loads the thing it is gating is a gate with a recursion problem. So the declarations are
written out here and the gate reads a file.

That file is an input the gate consults to decide, which puts it in the same class as the
writable-org allowlist: it is in PROTECTED_BASENAMES and in lib.is_authorization_input, so no
maintainer window grants it either. Regenerating it is an operator action, exactly like
`sync-counts.py`, and `readonly_manifest_is_derived` fails the build when it drifts.

    python3 scripts/gen-readonly-manifest.py
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "harness" / "checks" / "read-only-checks.json"


def main():
    spec = importlib.util.spec_from_file_location("tv_manifest", ROOT / "harness" / "validate.py")
    v = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v)
    names = sorted(n for n, ro in v.READS_ONLY.items() if ro)
    OUT.write_text(json.dumps({
        "note": "Checks declared to make no org mutation, and therefore runnable by the agent "
                "against a live org via `validate.py --only <name> --target-org X`. Generated "
                "from the @check declarations; verified against source by "
                "readonly_manifest_is_derived. Editing this by hand widens what the agent may "
                "run, which is why no maintainer window grants it.",
        "checks": names}, indent=2) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(names)} read-only check(s)")
    for n in names:
        print(f"  {n}")


if __name__ == "__main__":
    sys.exit(main())
