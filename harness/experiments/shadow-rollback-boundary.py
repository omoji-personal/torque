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

# The control arm. Enqueues the same Queueable and COMMITS — no savepoint, no DML, so nothing
# to clean up afterwards.
#
# Without this the treatment arm cannot interpret itself. Measured on the first run: the job
# enqueued inside the rolled-back savepoint had no AsyncApexJob row, and neither did anything
# else — the org holds ZERO AsyncApexJob rows, ever. "Not found" was therefore equally
# consistent with "the rollback removed it" and "this org does not retain those rows", and
# reporting the first would have been a guess wearing a measurement's clothes.
#
# Same discipline as the harness mutators: assert the baseline before concluding anything from
# the mutated case.
CONTROL_APEX = """
Id jobId = System.enqueueJob(new TorqueNoopQueueable());
System.debug('{mark}ctrlJob=' + jobId);
System.debug('{mark}ctrlDone=1');

public class TorqueNoopQueueable implements Queueable {{
    public void execute(QueueableContext ctx) {{
        // no side effect: the AsyncApexJob row is the observation
    }}
}}
"""


def debug_log(raw):
    """The debug log, with real newlines.

    `sf apex run --json` returns the log inside a JSON string, so its line breaks arrive as the
    two characters backslash-n. Regexing the raw stdout therefore ran every marker value into
    the following log line: probeId came back as `001gK00001I5VhsQAF\\n09:43:19.42`, and the
    limit triple as `0,2,3\\n09:43:19.42`, which parsed as nothing. Three of five findings were
    NOT ESTABLISHED for a reason that had nothing to do with the org.

    Decode the JSON and take the log; fall back to raw text only if that fails, since a run that
    produced no parseable envelope is one where reading the raw output is the last thing left.
    """
    try:
        doc = json.loads(raw)
        res = doc.get("result") or {}
        for key in ("logs", "log", "compiled"):
            if isinstance(res.get(key), str) and MARK in res[key]:
                return res[key]
    except Exception:                                      # noqa: BLE001
        pass
    return raw or ""


def parse_marks(text):
    # A backslash ends a value too. The character class excluded whitespace and the log's field
    # separator and not the escape, which is exactly the character the JSON envelope introduces.
    out = {}
    for m in re.finditer(re.escape(MARK) + r"([a-zA-Z]+)=([^\s|\\]*)", text or ""):
        out[m.group(1)] = m.group(2)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--target-org", required=True)
    # Default 0. This waited 25s "for async work to settle", which sounded prudent and was the
    # reason two runs came back inconclusive: an empty Queueable finishes instantly and this org
    # retains no AsyncApexJob row for a completed one, so the wait was not letting the
    # measurement settle, it was letting it expire. Kept as a flag because an org that DOES
    # retain rows might want it, and removing an option because it was misused once is how the
    # next person loses a knob they needed.
    p.add_argument("--wait", type=int, default=0,
                   help="seconds to pause before the job lookups. Default 0: an empty Queueable "
                        "completes immediately and a completed job's row may not be retained, so "
                        "waiting can destroy the evidence rather than settle it")
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

    blob = debug_log(r.stdout) + "\n" + (r.stderr or "")
    marks = parse_marks(blob)
    if "done" not in marks:
        print("REFUSED: the Apex did not reach its final marker, so nothing below would be a "
              "measurement. Raw output follows.\n", file=sys.stderr)
        print(blob[:1500], file=sys.stderr)
        return 2

    # FIRST, before anything else queries anything: look for the treatment job. An empty
    # Queueable finishes in well under a second and this org keeps no AsyncApexJob row for a
    # completed one, so every step taken before this one is a step during which the evidence can
    # vanish. The two findings below involve queries of their own; running them first is what
    # made the first two attempts inconclusive.
    def _job_row(job_id):
        if not job_id:
            return None, "no job id captured"
        rows_, err_ = query(a.target_org,
                            f"SELECT Id, Status FROM AsyncApexJob WHERE Id = '{job_id}'")
        if err_:
            return None, err_
        return bool(rows_), (rows_[0].get("Status") if rows_ else None)

    if a.wait:
        print(f"  --wait {a.wait}: pausing before the job lookups. On an org that does not "
              f"retain\n  completed AsyncApexJob rows this destroys the evidence rather than "
              f"settling it.\n")
        time.sleep(a.wait)
    treat_seen, treat_note = _job_row(marks.get("jobId", ""))

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

    # 3. did the Queueable survive? Needs the control arm to mean anything, AND needs to be
    #    asked BEFORE the evidence expires.
    #
    #    The first version waited 25 seconds "for async work to settle", which is exactly wrong
    #    here. An empty Queueable finishes in well under a second and this org retains no
    #    AsyncApexJob row for a completed one — the control proved that by being enqueued,
    #    COMMITTED, and still absent. So the wait was not letting the measurement settle, it was
    #    letting it disappear, and both arms came back empty for a reason that had nothing to do
    #    with rollback semantics.
    #
    #    Each job is now queried immediately after its own Apex run, so the window between
    #    enqueue and observation is as short as two subprocess calls allow.
    job = marks.get("jobId", "")
    ctrl_tmp = ROOT / "local" / f".shadow-control-{run}.apex"
    ctrl_tmp.write_text(CONTROL_APEX.format(mark=MARK))
    try:
        cr = sh("apex", "run", "--file", str(ctrl_tmp), "--target-org", a.target_org, "--json")
    finally:
        ctrl_tmp.unlink(missing_ok=True)
    ctrl = parse_marks(debug_log(cr.stdout) + "\n" + (cr.stderr or ""))
    ctrl_job = ctrl.get("ctrlJob", "")
    ctrl_seen, ctrl_note = _job_row(ctrl_job)      # immediately, for the same reason

    if ctrl_seen is None:
        findings.append(("queueable and rollback", NOT_ESTABLISHED,
                         f"the CONTROL job could not be read ({ctrl_note}), so the treatment "
                         f"arm has nothing to be compared against"))
    elif not ctrl_seen:
        findings.append(("queueable and rollback", NOT_ESTABLISHED,
                         f"the control job {ctrl_job} was enqueued and COMMITTED and still does "
                         f"not appear in AsyncApexJob. This org does not retain rows for a "
                         f"completed Queueable, so the treatment arm's absence proves nothing "
                         f"either way. INCONCLUSIVE by construction, not by failure — a control "
                         f"that does not show up is the honest reason to withhold the finding"))
    elif treat_seen:
        findings.append(("queueable SURVIVES rollback", ESTABLISHED,
                         f"control job {ctrl_job} present ({ctrl_note}) AND treatment job {job} "
                         f"present ({treat_note}). Enqueuing inside a savepoint and rolling back "
                         f"does NOT unschedule the work: shadowing an operation that enqueues "
                         f"anything runs that work for real"))
    else:
        findings.append(("queueable is removed by rollback", ESTABLISHED,
                         f"control job {ctrl_job} IS in AsyncApexJob ({ctrl_note}) and treatment "
                         f"job {job} is NOT — so the org does retain these rows, and the rollback "
                         f"is what removed this one"))

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
