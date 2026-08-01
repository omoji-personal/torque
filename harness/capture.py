#!/usr/bin/env python3
"""Screenshot helper: the ONLY writer of guide images. Strips embedded metadata from PNGs,
refuses images it cannot clean, and records a content-bound manifest entry (SHA-256 + the crop
the operator declares + disposable-org Id). Images not written through here fail the release
check.

WHAT THIS USED TO CLAIM

The docstring said it cropped and stripped EXIF. It did neither: `record()` hashed the file and
wrote `"exif_stripped": True` into the manifest as a literal. Every entry therefore asserted
that metadata had been removed from an image nobody had opened — the one failure mode worse
than no manifest at all, because the manifest is what a reader would trust.

Stripping is now done, verified by re-reading the bytes, and an image whose metadata cannot be
removed is refused rather than recorded with a hopeful flag. Cropping is NOT done here — it is
the operator's action, and the field records what they declare, which the field name now says.
"""
import hashlib
import json
import struct
import sys
import time
from pathlib import Path

MANIFEST = Path(__file__).resolve().parent / "image-manifest.json"

# PNG chunks that carry text, timestamps or EXIF. Everything the renderer needs is critical
# (IHDR/PLTE/IDAT/IEND) or colour-related, so dropping these cannot change how it displays.
_PNG_STRIP = {b"eXIf", b"tEXt", b"iTXt", b"zTXt", b"tIME", b"pHYs"}
_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def png_metadata_chunks(data: bytes):
    """Names of metadata-bearing chunks present, in order. Empty means the file is clean."""
    if not data.startswith(_PNG_SIG):
        return None
    found, i = [], len(_PNG_SIG)
    while i + 8 <= len(data):
        (length,) = struct.unpack(">I", data[i:i + 4])
        name = data[i + 4:i + 8]
        if name in _PNG_STRIP:
            found.append(name.decode("ascii", "replace"))
        if name == b"IEND":
            break
        i += 12 + length                       # length + type + data + CRC
    return found


def strip_png(data: bytes) -> bytes:
    if not data.startswith(_PNG_SIG):
        return data
    out, i = bytearray(_PNG_SIG), len(_PNG_SIG)
    while i + 8 <= len(data):
        (length,) = struct.unpack(">I", data[i:i + 4])
        name = data[i + 4:i + 8]
        chunk = data[i:i + 12 + length]
        if name not in _PNG_STRIP:
            out += chunk
        if name == b"IEND":
            break
        i += 12 + length
    return bytes(out)


def jpeg_has_exif(data: bytes) -> bool:
    return data[:2] == b"\xff\xd8" and (b"Exif\x00\x00" in data[:65536]
                                        or b"\xff\xe1" in data[:4096])


def record(image_path: str, crop_declared: str, org_id: str):
    p = Path(image_path)
    data = p.read_bytes()

    if data.startswith(_PNG_SIG):
        before = png_metadata_chunks(data)
        if before:
            p.write_bytes(strip_png(data))
            data = p.read_bytes()
        after = png_metadata_chunks(data)
        if after:                              # verified by re-reading, not assumed
            raise SystemExit(f"refusing to record {p.name}: metadata chunks survive stripping "
                             f"({after}) — the manifest must not claim otherwise")
        stripped = {"format": "png", "removed": before or [], "verified_clean": True}
    elif jpeg_has_exif(data):
        raise SystemExit(
            f"refusing to record {p.name}: it is a JPEG carrying EXIF, which this tool does not "
            f"strip. Re-export as PNG, or strip it first (exiftool -all= {p.name}), then re-run. "
            f"Recording it would put a false 'metadata removed' claim in the manifest.")
    else:
        stripped = {"format": "other", "removed": [], "verified_clean": False}

    entry = {"file": p.name, "sha256": hashlib.sha256(data).hexdigest(),
             "crop_declared": crop_declared, "org": org_id,
             "metadata": stripped, "t": int(time.time())}
    m = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {"images": []}
    m["images"] = [e for e in m["images"] if e["file"] != p.name] + [entry]
    MANIFEST.write_text(json.dumps(m, indent=2))
    return entry


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        import tempfile
        import zlib
        ok = True

        def chunk(name, payload=b""):
            return (struct.pack(">I", len(payload)) + name + payload
                    + struct.pack(">I", zlib.crc32(name + payload) & 0xFFFFFFFF))

        ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
        dirty = (_PNG_SIG + chunk(b"IHDR", ihdr) + chunk(b"tEXt", b"Author\x00somebody")
                 + chunk(b"eXIf", b"\x00\x01\x02") + chunk(b"IDAT", zlib.compress(b"\x00\x00"))
                 + chunk(b"IEND"))
        ok &= sorted(png_metadata_chunks(dirty)) == ["eXIf", "tEXt"]
        ok &= png_metadata_chunks(strip_png(dirty)) == []
        # and the pixel data survives
        ok &= b"IDAT" in strip_png(dirty) and b"IHDR" in strip_png(dirty)
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "probe.png"
            f.write_bytes(dirty)
            MANIFEST = Path(td) / "m.json"
            globals()["MANIFEST"] = MANIFEST
            e = record(str(f), "component", "00Dxxx")
            ok &= e["metadata"]["verified_clean"] is True
            ok &= sorted(e["metadata"]["removed"]) == ["eXIf", "tEXt"]
            # the recorded hash must be of the CLEANED bytes, not the originals
            ok &= e["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
            ok &= hashlib.sha256(dirty).hexdigest() != e["sha256"]
        print("capture self-test:", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    print(record(sys.argv[1],
                 sys.argv[2] if len(sys.argv) > 2 else "component",
                 sys.argv[3] if len(sys.argv) > 3 else ""))
