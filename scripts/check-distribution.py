#!/usr/bin/env python3
"""Check a built wheel's installed surface and accidental private/source content."""
import argparse
import json
from pathlib import Path
import zipfile
import tarfile

REQUIRED = {
    "torque/cli.py", "torque/workspace.py", "torque/data/catalogue.json",
    "torque/changes.py", "torque/demo.py", "torque/template_updates.py",
    "jsc_common/workspace.py", "jsc_qa/data/qa-router.yaml",
    "jsc_revert/cli.py", "jsc_loganalyzer/cli.py", "jsc_memory/cli.py",
    "jsc_browser_tests/cli.py", "meeting_processor/cli.py", "jsc_probes/cli.py",
    "jsc_ai_prompt_regression/cli.py", "jsc_advisory/data/salesforce-platform.json",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--sdist", type=Path)
    args = parser.parse_args()
    with zipfile.ZipFile(args.wheel) as archive:
        names = set(archive.namelist())
        missing = REQUIRED - names
        if missing:
            raise SystemExit(f"Wheel missing required runtime files: {sorted(missing)}")
        forbidden = {"local", ".git", ".sf", ".sfdx", "node_modules", "clients", "tests", "__pycache__"}
        bad = [name for name in names if forbidden.intersection(Path(name).parts)]
        if bad:
            raise SystemExit(f"Private/development content in wheel: {sorted(bad)}")
        data = json.loads(archive.read("torque/data/catalogue.json"))
        rows = data if isinstance(data, list) else data.get("workflows", data.get("commands", []))
        if not rows:
            raise SystemExit("Empty workflow catalogue")
        # The source_command surface grows as new Torque commands are added (JSC
        # legacy mappings plus native additions like the T4 demo-breadth recipes), so
        # pinning an exact total needs a manual bump on every legitimate catalogue
        # change. Guard the real regression instead: every mapped source_command is
        # present and distinct, so no two catalogue entries silently collide on the
        # same legacy/CLI alias.
        mapped = [row["source_command"] for row in rows if row.get("source_command")]
        if not mapped:
            raise SystemExit("No source_command mappings found in the packaged catalogue")
        source_commands = set(mapped)
        if len(mapped) != len(source_commands):
            duplicates = sorted({name for name in source_commands if mapped.count(name) > 1})
            raise SystemExit(f"Duplicate source_command mappings in the catalogue: {duplicates}")
        for row in rows:
            if f"torque/data/commands/{row['name']}.md" not in names:
                raise SystemExit(f"Missing packaged recipe: {row['name']}")
        home_prefix = (Path.home().as_posix().rstrip("/") + "/").encode()
        home_paths = [n for n in names if n.endswith((".py", ".md", ".json", ".yaml"))
                      and home_prefix in archive.read(n)]
        if home_paths:
            raise SystemExit(f"Developer-local absolute paths in wheel: {home_paths}")
        print(f"Wheel surface verified: {len(names)} entries, {len(rows)} recipes, "
              f"{len(source_commands)} distinct source_command mappings.")
    if args.sdist:
        with tarfile.open(args.sdist) as archive:
            paths = [Path(member.name) for member in archive.getmembers()]
            forbidden = {"local", "work", ".git", ".sf", ".sfdx", "node_modules", ".venv", ".venv-continuation", "clients"}
            bad = [str(p) for p in paths if forbidden.intersection(p.parts) or p.name == "torque-marketing.md"]
            if bad:
                raise SystemExit(f"Private/generated source distribution content: {bad}")
            relative = {Path(*p.parts[1:]).as_posix() for p in paths}
            required = {"scripts/test-offline.py", "workflows/catalogue.json", "AGENTS.md", "pyproject.toml", "MANIFEST.in",
                        "SECURITY.md", "CONTRIBUTING.md", "CHANGELOG.md", "docs/installation.md"}
            if not required <= relative:
                raise SystemExit(f"Source distribution missing: {sorted(required - relative)}")
            print(f"Source distribution surface verified: {len(paths)} entries, source workflows and offline test harness included.")


if __name__ == "__main__":
    main()
