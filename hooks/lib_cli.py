#!/usr/bin/env python3
"""CLI over lib.py for skills: classify, whoami. Read-only; never writes the allowlist
(that path is operator-present via `torque approve`)."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib

def main():
    if len(sys.argv) < 2:
        print("usage: lib_cli.py classify <alias>", file=sys.stderr); sys.exit(2)
    cmd = sys.argv[1]
    if cmd == "classify":
        verdict, orgid, user = lib.classify(sys.argv[2])
        eligible = verdict in lib.ELIGIBLE
        print(json.dumps({"target": sys.argv[2], "verdict": verdict, "orgId": orgid,
                          "username": user, "write_eligible": eligible}))
    else:
        print(f"unknown: {cmd}", file=sys.stderr); sys.exit(2)

if __name__ == "__main__":
    main()
