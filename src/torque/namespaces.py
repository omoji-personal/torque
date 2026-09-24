"""Managed namespaces a deploy names, shown on the approval screen. A warning,
never a block: the reviewer decides whether touching a managed package's
components is intended."""
from __future__ import annotations

from pathlib import Path
import re
import xml.etree.ElementTree as ET

# Public nonprofit packages commonly installed in client orgs. A workspace adds
# more with "managed_namespaces" in workspace.json.
DEFAULT_MANAGED = ("npsp", "npe01", "npo02", "npe03", "npe4", "npe5", "outfunds", "pmdm", "sb")
_PREFIX = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9]{0,14})__(?=[A-Za-z])")
MANIFEST_FLAGS = ("--manifest", "-x")
MAX_MANIFEST_BYTES = 2 * 1024 * 1024


def _manifest_members(path: Path) -> list[str]:
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            return []
        root = ET.fromstring(path.read_bytes())
    except (OSError, ET.ParseError, ValueError):
        return []
    return [el.text or "" for el in root.iter() if el.tag.split("}")[-1] == "members"]


def find_namespaces(argv: list[str], cwd: Path, extra: tuple[str, ...] = ()) -> list[str]:
    """Sorted managed namespace prefixes named by the command or its manifest."""
    managed = {n.casefold() for n in (*DEFAULT_MANAGED, *extra) if isinstance(n, str)}
    from . import argv_flags
    texts = list(argv)
    for value in argv_flags.values(argv, MANIFEST_FLAGS):
        texts += _manifest_members(Path(cwd) / value)
    found = {m.group(1) for text in texts for m in _PREFIX.finditer(text)}
    return sorted(n for n in found if n.casefold() in managed)
