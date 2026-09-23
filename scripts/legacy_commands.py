"""The retained legacy command names, read from docs/workflow-continuity.md.

The "Retained command map" table there is the single human-maintained list of the
original command names; the prose above it states how many there are. Both the
distribution check and the installed smoke test compare the catalogue's
source_command values to this set, so a dropped, renamed or newly invented legacy
mapping fails CI.
"""
from pathlib import Path
import re

DOC = Path(__file__).resolve().parents[1] / "docs" / "workflow-continuity.md"


def legacy_commands() -> set[str]:
    text = DOC.read_text(encoding="utf-8")
    stated = re.search(r"All \*\*(\d+) source command names\*\*", text)
    if not stated:
        raise SystemExit(f"{DOC.name}: stated legacy command count not found")
    table = text.split("## Retained command map", 1)[1].strip().split("\n\n", 1)[0]
    names: list[str] = []
    for line in table.splitlines()[2:]:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 2:
            names.extend(name.strip() for name in cells[1].split(",") if name.strip())
    if len(names) != len(set(names)) or len(names) != int(stated.group(1)):
        raise SystemExit(f"{DOC.name}: table lists {len(names)} names ({len(set(names))} distinct), "
                         f"prose states {stated.group(1)}")
    return set(names)


def check_source_commands(rows: list[dict]) -> set[str]:
    """Every legacy name is mapped exactly once, and nothing else is mapped."""
    mapped = [row["source_command"] for row in rows if row.get("source_command")]
    duplicates = sorted({name for name in mapped if mapped.count(name) > 1})
    if duplicates:
        raise SystemExit(f"Duplicate source_command mappings: {duplicates}")
    expected = legacy_commands()
    if set(mapped) != expected:
        raise SystemExit(f"source_command mismatch: missing {sorted(expected - set(mapped))}, "
                         f"not legacy {sorted(set(mapped) - expected)} (new additions use null)")
    return expected
