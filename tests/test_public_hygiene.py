"""Public source must stay firm-neutral."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Built from parts so this file does not match itself.
FORBIDDEN = ("back" + "office", "back office " + "thinking")
BINARY = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".whl", ".gz", ".zip", ".ico"}


def test_tracked_text_names_no_firm():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    hits = []
    for name in files:
        path = ROOT / name
        if path.suffix.lower() in BINARY or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        hits += [name for term in FORBIDDEN if term in text]
    assert hits == []


def test_profiles_are_neutral():
    from torque import workspace as ws
    assert ws.PROFILES == ("generic", "solution-lead")
