"""Public source must stay firm-neutral."""
import hashlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# SHA-256 digests of the forbidden names (spaces, hyphens and case ignored), so this file names no firm.
FORBIDDEN = {
    "87f7ccd3e87428e0e237492b8a992d4272071929b9e3247ef47ed561c1468148",
    "b5481c7239dfa67ead224d9e13b652c7a058e7e7aedd1ce72af553b4e18a4fbe",
}
BINARY = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".whl", ".gz", ".zip", ".ico"}


def _names_firm(text):
    words = re.findall(r"[a-z]+", text)
    grams = set(words)
    grams.update(a + b for a, b in zip(words, words[1:]))
    grams.update(a + b + c for a, b, c in zip(words, words[1:], words[2:]))
    return any(hashlib.sha256(g.encode()).hexdigest() in FORBIDDEN for g in grams)


def test_tracked_text_names_no_firm():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    hits = []
    for name in files:
        path = ROOT / name
        if path.suffix.lower() in BINARY or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        if _names_firm(text):
            hits.append(name)
    assert hits == []


def test_profiles_are_neutral():
    from torque import workspace as ws
    assert ws.PROFILES == ("generic", "solution-lead")
