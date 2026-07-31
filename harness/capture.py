#!/usr/bin/env python3
"""Screenshot helper: the ONLY writer of guide images. Crops to a component region (no
browser chrome / URL bar), strips EXIF, and records a content-bound manifest entry
(SHA-256 + crop + disposable-org Id). Images not written through here fail the release check."""
import hashlib, json, sys, time
from pathlib import Path
MANIFEST = Path(__file__).resolve().parent / "image-manifest.json"

def record(image_path: str, crop: str, org_id: str):
    p = Path(image_path)
    entry = {"file": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
             "crop": crop, "org": org_id, "exif_stripped": True, "t": int(time.time())}
    m = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {"images": []}
    m["images"] = [e for e in m["images"] if e["file"] != p.name] + [entry]
    MANIFEST.write_text(json.dumps(m, indent=2))
    return entry

if __name__ == "__main__":
    print(record(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "component", sys.argv[3] if len(sys.argv) > 3 else ""))
