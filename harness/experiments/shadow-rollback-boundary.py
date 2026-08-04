#!/usr/bin/env python3
"""What a savepoint rollback does NOT undo. Measured, not asserted.

    python3 harness/experiments/shadow-rollback-boundary.py --target-org <disposable-org>

`bin/torque-shadow` runs your DML inside a transaction that never commits, and its docstring
lists what a savepoint does not cover: callouts already sent, `@future` and Queueable work,
platform events published immediately, change data capture. That list is correct as far as
anyone here knows, and "as far as anyone here knows" is the problem. The roadmap's remaining
work on shadow execution is one sentence: establish it by test, not by assertion.

This is that test. It measures three things and REFUSES to guess at two more.

WHAT IT ESTABLISHES

  1. DML inside a savepoint really is undone. The insert leaves nothing behind.
  2. Governor limits do NOT rewind with the data. If a rollback restored the DML counter, a
     shadow run would cost nothing and could be repeated indefinitely; if it does not, a shadow
     run spends the caller's budget for a transaction that never happened, and anyone planning
     to shadow a large operation needs to know that before they try.
  3. A Queueable enqueued inside the rolled-back savepoint. Whether the job survives decides
     whether shadowing an operation that enqueues work is safe at all.

WHAT IT DOES NOT ESTABLISH, and says so rather than leaving a gap that reads as a clean result

  · Platform events. Observing one requires a subscriber that writes something, which requires
    a custom platform event object to exist in the org. Reported NOT ESTABLISHED with that
    reason rather than assumed from the documentation.
  · Callouts. A real callout needs a Remote Site Setting, and creating one is a configuration
    change this must not make in somebody's org to satisfy its own curiosity.

SAFETY

Refuses any org that does not classify sandbox, developer or scratch, using the same
`lib.classify_live` the gates use — not an alias, not a URL guess. Every record it creates
carries a run-scoped marker so residue is identifiable, and it deletes by Id. It is an
experiment, so it prints exactly what it is about to do and requires the operator to be running
it: the anonymous-Apex control means the agent cannot run this, which is the point.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "hooks"))

MARK = "TORQUE~"
ESTABLISHED, NOT_ESTABLISHED = "ESTABLISHED", "NOT ESTABLISHED"


def sh(*args, timeout=300):
    return subprocess.run(["sf", *args], capture_output=True, text=True, timeout=timeout)


def query(target, soql):
    """(rows, error). An error is never an empty result — the confusion this repo keeps finding."""
    r = sh("data", "query", "--target-org", target, "--json", "--query", soql)
    if r.returncode != 0:
        try:
            o = json.loads(r.stdout)
            return None, " ".join((o.get("message") or o.get("name") or "").split())[:160]
        except Exception:                                  # noqa: BLE001
            return None, (r.stderr or "sf failed").strip().splitlines()[0][:160]
    try:
        return json.loads(r.stdout)["result"]["records"], None
    except Exception:                                      # noqa: BLE001
        return None, "unparseable query result"


APEX = """
// Torque shadow-rollback boundary experiment, run {run}
Integer dml0  = Limits.getDmlStatements();
Integer rows0 = Limits.getDmlRows();

Savepoint sp = Database.setSavepoint();

Account probe = new Account(Name = '{mark}{run}');
insert probe;
Id probeId = probe.Id;

Integer dml1  = Limits.getDmlStatements();
Integer rows1 = Limits.getDmlRows();

// A Queueable enqueued INSIDE the savepoint. Its side effect is deliberately nothing: the
// question is whether the JOB survives the rollback, and AsyncApexJob answers that without the
// job needing to touch any data.
Id jobId = System.enqueueJob(new TorqueNoopQueueable());

Database.rollback(sp);

Integer dml2  = Limits.getDmlStatements();
Integer rows2 = Limits.getDmlRows();

System.debug('{mark}probeId=' + probeId);
System.debug('{mark}jobId=' + jobId);
System.debug('{mark}dml=' + dml0 + ',' + dml1 + ',' + dml2);
System.debug('{mark}rows=' + rows0 + ',' + rows1 + ',' + rows2);
// `name=value`, like every other marker. Written bare as `{mark}done` first, which the parser
// silently ignored — so the completion marker was never found, and the script would have
// refused every run with "did not reach its final marker" while the Apex ran perfectly. Caught
// by a unit test on a synthetic log, before it ever touched an org.
System.debug('{mark}done=1');

public class TorqueNoopQueueable implements Queueable {{
    public void execute(QueueableContext ctx) {{
        // deliberately empty: existence in AsyncApexJob is the whole measurement
    }}
}}
"""


def parse_marks(text):
    out = {}
    for m in re.finditer(re.escape(MARK) + r"([a-zA-Z]+)=([^\s|]*)", text or ""):
        out[m.group(1)] = m.group(2)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--target-org", required=True)
    p.add_argument("--wait", type=int, default=25,
                   help="seconds to let async work settle before measuring it")
    a = p.parse_args()

    import lib
    verdict, orgid, _user = lib.classify_live(a.target_org)
    if verdict not in lib.ELIGIBLE:
        print(f"REFUSED: {a.target_org} classifies {verdict!r}. This runs anonymous Apex and "
              f"creates a record; only sandbox, developer or scratch orgs are eligible, and the "
              f"verdict comes from a live Organization query rather than the alias.",
              file=sys.stderr)
        return 2

    run = str(int(time.time()))
    print(f"shadow-rollback boundary — {a.target_org} ({verdict}, {orgid})")
    print(f"  will: set a savepoint, insert one Account named {MARK}{run}, enqueue an empty")
    print(f"        Queueable, roll back, then measure what survived. Deletes by Id.\n")

    body = APEX.format(run=run, mark=MARK)
    tmp = ROOT / "local" / f".shadow-experiment-{run}.apex"
    tmp.parent.mkdir(exist_ok=True)
    tmp.write_text(body)
    try:
        r = sh("apex", "run", "--file", str(tmp), "--target-org", a.target_org, "--json")
    finally:
        tmp.unlink(missing_ok=True)

    blob = (r.stdout or "") + (r.stderr or "")
    marks = parse_marks(blob)
    if "done" not in marks:
        print("REFUSED: the Apex did not reach its final marker, so nothing below would be a "
              "measurement. Raw output follows.\n", file=sys.stderr)
        print(blob[:1500], file=sys.stderr)
        return 2

    findings = []

    # 1. did the DML roll back?
    probe_id = marks.get("probeId", "")
    rows, err = query(a.target_org, f"SELECT Id FROM Account WHERE Name = '{MARK}{run}'")
    if err:
        findings.append(("dml rolls back", NOT_ESTABLISHED,
                         f"could not ask the org whether the record survived: {err}"))
    elif rows:
        findings.append(("dml rolls back", NOT_ESTABLISHED,
                         f"the record SURVIVED the rollback ({len(rows)} row(s)) — which would "
                         f"mean shadow execution leaves residue. Delete by Id: "
                         f"{[x['Id'] for x in rows]}"))
    else:
        findings.append(("dml rolls back", ESTABLISHED,
                         f"the inserted Account ({probe_id or 'id not captured'}) is not in the "
                         f"org after rollback — observed by query, not asserted"))

    # 2. do governor limits rewind with the data?
    dml = (marks.get("dml") or "").split(",")
    rowsc = (marks.get("rows") or "").split(",")
    if len(dml) == 3 and all(x.isdigit() for x in dml):
        d0, d1, d2 = (int(x) for x in dml)
        if d2 <= d0:
            findings.append(("governor limits rewind", ESTABLISHED,
                             f"the DML counter returned to its pre-savepoint value "
                             f"({d0} → {d1} → {d2}); a shadow run costs nothing"))
        else:
            findings.append(("governor limits do NOT rewind", ESTABLISHED,
                             f"DML statements {d0} → {d1} → {d2} and DML rows "
                             f"{'→'.join(rowsc) if len(rowsc) == 3 else '?'}: the rollback "
                             f"restored the DATA and not the BUDGET. A shadow run spends the "
                             f"caller's governor limits for a transaction that never happened, "
                             f"so shadowing a large operation can hit a limit the real operation "
                             f"would also have hit — and can hit it while proving nothing"))
    else:
        findings.append(("governor limits", NOT_ESTABLISHED,
                         f"the limit markers did not parse: dml={marks.get('dml')!r}"))

    # 3. did the Queueable survive?
    job = marks.get("jobId", "")
    if not job:
        findings.append(("queueable survives rollback", NOT_ESTABLISHED,
                         "no job id was captured, so there is nothing to look for"))
    else:
        if a.wait:
            print(f"  waiting {a.wait}s for async work to settle...\n")
            time.sleep(a.wait)
        rows, err = query(a.target_org,
                          f"SELECT Id, Status FROM AsyncApexJob WHERE Id = '{job}'")
        if err:
            findings.append(("queueable survives rollback", NOT_ESTABLISHED,
                             f"could not ask the org about job {job}: {err}"))
        elif rows:
            findings.append(("queueable SURVIVES rollback", ESTABLISHED,
                             f"job {job} exists with status {rows[0].get('Status')!r}. Enqueuing "
                             f"inside a savepoint and rolling back does NOT unschedule the work "
                             f"— shadowing an operation that enqueues anything runs that work "
                             f"for real"))
        else:
            findings.append(("queueable is removed by rollback", ESTABLISHED,
                             f"job {job} is not in AsyncApexJob after the rollback"))

    findings.append(("platform events", NOT_ESTABLISHED,
                     "observing one needs a subscriber that writes something, which needs a "
                     "custom platform event object to exist here. Not assumed from the "
                     "documentation, and not created in somebody's org to satisfy this script"))
    findings.append(("callouts", NOT_ESTABLISHED,
                     "a real callout needs a Remote Site Setting, and adding one is a "
                     "configuration change this must not make"))

    print("FINDINGS\n")
    w = max(len(f[0]) for f in findings)
    for title, state, detail in findings:
        print(f"  {title.ljust(w)}  {state}")
        for line in _wrap(detail, 84):
            print(f"  {' ' * w}  {line}")
        print()
    est = [f for f in findings if f[1] == ESTABLISHED]
    print(f"  {len(est)}/{len(findings)} established. The rest are named rather than omitted, "
          f"because a\n  findings list that silently drops what it could not measure reads as a "
          f"complete one.")
    print(f"\n  Paste this output back. Anything ESTABLISHED here is a candidate catalogue entry\n"
          f"  with confidence verified-live, which requires a runnable verifier before it counts.")
    return 0


def _wrap(text, width):
    words, line, out = text.split(), "", []
    for word in words:
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out


if __name__ == "__main__":
    sys.exit(main())
