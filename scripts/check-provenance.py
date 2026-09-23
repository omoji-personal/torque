#!/usr/bin/env python3
"""Verify recorded inherited-package bytes without accessing the source employer repo."""
import hashlib
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "packages/provenance.json").read_text(encoding="utf-8"))
    problems = []
    seen = set()
    for entry in manifest["files"]:
        name = entry["path"]
        path = root / name
        if name in seen or Path(name).is_absolute() or ".." in Path(name).parts:
            problems.append(f"Invalid or repeated entry: {name}")
            continue
        seen.add(name)
        if path.is_symlink() or not path.is_file():
            problems.append(f"Missing or symlinked file: {name}")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry["destination_sha256"]:
            problems.append(f"Changed bytes: {name}")
        if "source_sha256" in entry and entry.get("adapted") != (digest != entry["source_sha256"]):
            problems.append(f"Incorrect adaptation flag: {name}")
    if problems:
        print("\n".join(problems))
        return 1
    print(f"Package provenance verified: {len(seen)} files; original source hashes retained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
