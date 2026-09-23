"""
jsc-probes CLI entry point.

Adopted into JusticeserverClaude (JSC) 2026-05-04 from claudeblazer (Apache-2.0).
TAA Phase 5 P2-2.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path
import xml.etree.ElementTree as ET

from jsc_probes.apex import analyze_class, generate_probes_for_class, test_class_name

# Apex API version stamped into generated *AdversarialTest.cls-meta.xml files.
# Keep in step with the org's sourceApiVersion / sfdx-project.json. (Audit 2026-05-30 P2.)
DEFAULT_APEX_API_VERSION = "61.0"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate editable Apex null/empty test drafts. Draft assertions intentionally fail until replaced with business expectations; nothing is compiled or executed.",
    )
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--target-class", help="Path to a single .cls file")
    grp.add_argument("--target-dir", help="Directory of .cls files")
    parser.add_argument(
        "--output",
        help="Output dir for generated <Class>AdversarialTest.cls files (defaults to alongside input)",
    )
    parser.add_argument("--force", action="store_true", help="Explicitly replace existing generated class and metadata; default preserves edited files")
    parser.add_argument("--api-version", help="Generated class API version; defaults to source companion version, then 61.0")
    return parser.parse_args(argv)


def class_name_from_path(path: Path) -> str:
    return path.stem


def _api_version(class_path: Path, override: str | None) -> str:
    version = override
    companion = class_path.with_name(class_path.name + "-meta.xml")
    if version is None and companion.exists():
        raw = companion.read_bytes()
        if b"<!DOCTYPE" in raw.upper():
            raise ValueError("Source metadata must not contain a DOCTYPE")
        metadata = ET.fromstring(raw)
        ns = "{http://soap.sforce.com/2006/04/metadata}"
        versions = metadata.findall(ns + "apiVersion")
        if metadata.tag != ns + "ApexClass" or len(versions) != 1:
            raise ValueError("Source companion must have one ApexClass apiVersion")
        version = versions[0].text
        if version is None:
            raise ValueError("Source companion apiVersion is empty")
    version = DEFAULT_APEX_API_VERSION if version is None else version.strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+", version) or float(version) <= 0:
        raise ValueError("API version must be a positive decimal such as 61.0")
    return version


def _write_pair(files: dict[Path, str], force: bool) -> None:
    # Validate both before touching either. Exclusive links also detect an output
    # created between validation and publication, without following symlinks.
    for path in files:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError(f"Output is not a regular file: {path}")
        if path.exists() and not force:
            raise FileExistsError(f"Preserved existing output: {path}; choose a new output directory or explicitly use --force")
    staged = []
    published = []
    try:
        for path, contents in files.items():
            fd, temporary = tempfile.mkstemp(prefix=".probe-", dir=path.parent)
            temporary = Path(temporary)
            staged.append(temporary)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(contents)
                stream.flush()
                os.fsync(stream.fileno())
            if force:
                os.replace(temporary, path)
            else:
                os.link(temporary, path)
                published.append((path, temporary.stat()))
    except Exception:
        # On no-clobber publication failure, remove only files this call created
        # and only while they still refer to this call's exact inode.
        if not force:
            for path, original in published:
                if path.exists() and not path.is_symlink():
                    current = path.stat()
                    if (current.st_dev, current.st_ino) == (original.st_dev, original.st_ino):
                        path.unlink()
        raise
    finally:
        for temporary in staged:
            temporary.unlink(missing_ok=True)


def write_test_class(class_path: Path, output_dir: Path | None, *, force: bool = False, api_version: str | None = None) -> Path:
    source = class_path.read_text(encoding="utf-8")
    cname = analyze_class(source, class_name_from_path(class_path)).class_name
    test_source = generate_probes_for_class(source, cname)
    version = _api_version(class_path, api_version)
    out_dir = output_dir or class_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    test_name = test_class_name(cname)
    out_file = out_dir / f"{test_name}.cls"
    meta_file = out_dir / f"{test_name}.cls-meta.xml"
    metadata = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<ApexClass xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        f"    <apiVersion>{version}</apiVersion>\n"
        "    <status>Active</status>\n"
        "</ApexClass>\n"
    )
    _write_pair({out_file: test_source, meta_file: metadata}, force)
    return out_file


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    output_dir = Path(args.output) if args.output else None

    targets: list[Path] = []
    if args.target_class:
        target_path = Path(args.target_class)
        # R1 Codex-008: validate file exists before read_text (no traceback)
        if not target_path.exists():
            print(f"error: --target-class file not found: {target_path}", file=sys.stderr)
            return 2
        if not target_path.is_file():
            print(f"error: --target-class path is not a file: {target_path}", file=sys.stderr)
            return 2
        if target_path.suffix != ".cls":
            print(f"error: --target-class expects .cls; got {target_path.suffix} ({target_path})", file=sys.stderr)
            return 2
        targets.append(target_path)
    else:
        directory = Path(args.target_dir)
        if not directory.is_dir():
            print(f"error: --target-dir is not a directory: {directory}", file=sys.stderr)
            return 2
        targets = sorted(directory.glob("*.cls"))

    # R1 Gemini-recursion-risk: filter test classes from BOTH --target-class
    # AND --target-dir to prevent generating AdversarialTest for an AdversarialTest
    try:
        # Test status is syntax, not a name substring; production classes such
        # as TestimonialService remain eligible.
        selected = []
        for target in targets:
            analysis = analyze_class(target.read_text(encoding="utf-8"), target.stem)
            if analysis.is_test:
                print(f"skipped test class: {target}", file=sys.stderr)
            else:
                selected.append(target)
        targets = selected
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if not targets:
        print("No .cls files found (or all were test classes filtered out).", file=sys.stderr)
        return 2

    for t in targets:
        try:
            out = write_test_class(t, output_dir, force=args.force, api_version=args.api_version)
        except (OSError, ValueError, ET.ParseError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        print(f"  generated: {out}")

    print(f"\njsc-probes: wrote {len(targets)} editable draft class(es); not compiled or executed. Replace each DRAFT assertion with an explicit business expectation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
