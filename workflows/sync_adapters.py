"""Refresh self-contained Claude adapters from the agent-neutral recipes.

Source checkout maintenance only: python3 workflows/sync_adapters.py [--check]
"""
from pathlib import Path
import argparse
import json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent
    root = directory.parent
    records = json.loads((directory / 'catalogue.json').read_text(encoding="utf-8"))
    destination = root / '.claude' / 'commands'
    if not args.check:
        destination.mkdir(parents=True, exist_ok=True)
    mismatches = []
    for record in records:
        body = (directory / f"{record['name']}.md").read_text(encoding="utf-8")
        expected = ('---\ndescription: ' + json.dumps(record['description'])
                    + '\n---\n\n' + body + '\n**Conversation input:** $ARGUMENTS\n'
                    + 'Use supplied context and existing authorization. Ask only for consequential missing information. '
                      'Keep work and evidence in the selected private client workspace.\n')
        path = destination / f"{record['name']}.md"
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != expected:
                mismatches.append(path.name)
        else:
            path.write_text(expected, encoding="utf-8")
    if mismatches:
        print('Adapters differ: ' + ', '.join(mismatches))
        return 1
    print(f"{len(records)} adapters {'match' if args.check else 'refreshed'}.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
