"""Task D19 (spec requirement 16): the delegated approver's grant touches only
the paths documented in `approval.DELEGATED_READS` / `DELEGATED_WRITES`.

This traces a real subprocess with `sys.addaudithook`, so it needs a genuine
process outside any AI session: an agent's ancestry (or CLAUDECODE) makes the
delegated grant itself refuse with reason class "agent-session" before the
trace could mean anything, and pre-flight ruling F34 has this whole file skip
inside any AI session on top of that, so a human or CI is the one who
observes it pass. Run it from a plain terminal (or CI):

    ~/.venvs/torque-a16/bin/python -m pytest tests/test_delegated_reads.py -v

or, offline-suite style:

    ~/.venvs/torque-a16/bin/python scripts/test-offline.py \
        --pytest-only tests/test_delegated_reads.py -q
"""
import fnmatch
import json
import os
import subprocess
import sys

import pytest

from delegated_helpers import MODEL, ORGS, delegated_workspace, flow_request
from torque import approval
from torque.presence import agent_reason

pytestmark = [pytest.mark.skipif(not hasattr(os, "getuid"), reason="tier 2 is POSIX only"),
              pytest.mark.skipif(bool(agent_reason()), reason="run outside an AI session (CI runs it)")]
SCRIPT = r"""
import io, json, os, sys
from collections import namedtuple
root, req_id, sha, digest = sys.argv[1:5]
seen = []
def hook(event, args):
    if event == "open" and isinstance(args[0], (str, bytes)):
        seen.append(["open", str(args[0])])
    elif event in ("os.scandir", "os.listdir") and args and args[0] is not None:
        seen.append([event, str(args[0])])
    elif event == "os.mkdir":
        seen.append(["mkdir", str(args[0])])
sys.addaudithook(hook)
from torque import approval
Org = namedtuple("Org", "org_id_18 detected_org_type is_production instance_url")
orgs = {"acme-dev": Org("00D000000000003AAA", "developer", False, "https://acme-dev.develop.my.salesforce.com")}
# This subprocess is one OS account (there is no second one to run the
# approver as, in CI or on a plain laptop either), so R41 (the delegate must
# not own the workspace directory) and R46 (the control files and the folders
# holding them must not belong to the approver account) need the same
# call-scoped fakes every other delegated test in this suite applies through
# tests/delegated_helpers.py's FAKE_OWNER and control_owner(): a subprocess
# does not inherit the parent process's monkeypatch of the module-global
# approval._control_stat, so this passes its own root_owner/control_stat
# instead, exactly the seam fix round 1 added those parameters for. Real
# st_mode (writable-by-others, sticky bit) still comes from the file; only the
# reported owner is faked, to a uid distinct from the approver's.
class _FakeSt:
    def __init__(self, st):
        self.st_mode = st.st_mode
        self.st_uid = 0
def _control_stat(path, st=None):
    # Always overrides st_uid, even when workspace.json's own protected-read
    # fstat is handed in (delegated_helpers.control_owner()'s fake() does the
    # same): a real `st` still needs its owner faked, or the workspace.json
    # check alone would see this process's own uid and refuse.
    return _FakeSt(st if st is not None else os.lstat(path))
approval.grant(root, "Acme", req_id, delegated=True, model_id="reviewer-model-1", request_sha256=sha,
               payload_digest=digest, out=io.StringIO(), resolve=orgs.get, env={}, ancestors=lambda: [],
               root_owner=lambda p: os.getuid() + 1, control_stat=_control_stat)
print(json.dumps(seen))
"""


def test_the_grant_touches_only_documented_paths(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    case = root / "clients" / "acme" / "cases" / "ax-01"
    case.mkdir(parents=True)
    req = flow_request(root, cwd=case)
    view = approval.request_view(root, "Acme", req["id"], resolve=ORGS.get)
    run = subprocess.run([sys.executable, "-c", SCRIPT, str(root), req["id"], view["request_sha256"],
                          view["payload"]["digest"]], capture_output=True, text=True, timeout=60,
                         env={k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")})
    assert run.returncode == 0, run.stderr
    seen = json.loads(run.stdout.splitlines()[-1])
    cwd = case.relative_to(root).as_posix()
    reads = [p.format(client="acme", cwd=cwd) for p in approval.DELEGATED_READS]
    writes = [p.format(client="acme", cwd=cwd) for p in approval.DELEGATED_WRITES]
    inside = [(kind, os.path.relpath(os.path.realpath(path), root)) for kind, path in seen
              if os.path.realpath(path).startswith(str(root) + os.sep) or os.path.realpath(path) == str(root)]
    assert not [p for kind, p in inside if kind == "mkdir"]
    stray = [p for _, p in inside if not any(fnmatch.fnmatch(p, pat) for pat in reads + writes) and p != "."]
    assert stray == []
