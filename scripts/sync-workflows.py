#!/usr/bin/env python3
"""Bundle the conversational surface into wheels; --check detects drift."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def expected_files():
    result = {"catalogue.json": (ROOT / "workflows/catalogue.json").read_bytes()}
    for directory, target in ((".claude/commands", "commands"),
                              (".claude/rules", "rules"),
                              (".claude/agents", "agents")):
        for path in sorted((ROOT / directory).glob("*.md")):
            if path.name == "torque-marketing.md":
                continue
            result[f"{target}/{path.name}"] = path.read_bytes()
    skills = ROOT / ".agents/skills"
    for path in sorted([*skills.glob("*/SKILL.md"), *skills.glob("*/references/*.md")]):
        result["skills/" + path.relative_to(skills).as_posix()] = path.read_bytes()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = ROOT / "src/torque/data"
    expected = expected_files()
    actual = {p.relative_to(target).as_posix(): p.read_bytes()
              for p in target.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    managed = lambda key: key == "catalogue.json" or key.split("/")[0] in {"commands", "rules", "agents", "skills"}
    drift = sorted(k for k in set(expected) | set(actual)
                   if managed(k) and expected.get(k) != actual.get(k))
    if args.check:
        if drift:
            print("Bundled workflow drift: " + ", ".join(drift))
            return 1
        print(f"Bundled conversational resources match ({len(expected)} files).")
        return 0
    for key in drift:
        path = target / key
        if key not in expected:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(expected[key])
    print(f"Bundled {len(expected)} conversational resources.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
