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
Id jobId = System.enqueueJob(new TorqueProbeQueueable());

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

public class TorqueProbeQueueable implements Queueable {{
    public void execute(QueueableContext ctx) {{
        insert new Account(Name = '{mark}{run}-async-treat');
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
Id jobId = System.enqueueJob(new TorqueProbeQueueable());
System.debug('{mark}ctrlJob=' + jobId);
System.debug('{mark}ctrlDone=1');

public class TorqueProbeQueueable implements Queueable {{
    public void execute(QueueableContext ctx) {{
        insert new Account(Name = '{mark}{run}-async-ctrl');
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
    # This flag has been 25, then 0, and is now 20, which looks like indecision and is not. The
    # observation changed underneath it. Waiting could only LOSE an AsyncApexJob row, because
    # the row exists from the moment of enqueue; waiting is REQUIRED for a side effect, because
    # the effect exists only once the job has run. Same knob, opposite requirement, and the
    # reason is worth writing down rather than leaving the next reader to wonder.
    p.add_argument("--wait", type=int, default=20,
                   help="seconds to let the queued work execute before looking for its side "
                        "effect. Needed now that the observation is what the Queueable DID "
                        "rather than whether a job row existed: a row exists from the moment of "
                        "enqueue, an effect only after execution")
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
    def _async_effect(suffix):
        """Did the Queueable actually RUN? Observed by its side effect, not by its job row.

        Three runs used AsyncApexJob and all three were inconclusive: `System.enqueueJob`
        returned an Id every time and the org holds zero AsyncApexJob rows, ever, even for a
        control that was enqueued and committed and queried with no delay. Whether the row is
        never written or purged instantly cannot be told apart from here, and either way the
        channel cannot carry this measurement.

        So the Queueable now inserts a marked Account and the Account is the observation. It is
        durable, it is queryable, it is deletable by Id, and it answers a strictly better
        question: not "was a job scheduled" but "did the work happen".
        """
        rows_, err_ = query(a.target_org,
                            f"SELECT Id FROM Account WHERE Name = '{MARK}{run}-async-{suffix}'")
        if err_:
            return None, err_
        return bool(rows_), [r["Id"] for r in rows_]

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

    # 3. did the enqueued work actually RUN? Observed by side effect, with a control.
    #
    #    The wait is back, and the earlier reasoning for removing it was right about the wrong
    #    thing. An AsyncApexJob ROW exists from the moment of enqueue, so waiting could only
    #    lose it. A side EFFECT exists only after the job executes, so waiting is exactly what
    #    it needs. Same flag, opposite requirement, because the observation changed.
    ctrl_tmp = ROOT / "local" / f".shadow-control-{run}.apex"
    ctrl_tmp.write_text(CONTROL_APEX.format(mark=MARK, run=run))
    try:
        cr = sh("apex", "run", "--file", str(ctrl_tmp), "--target-org", a.target_org, "--json")
    finally:
        ctrl_tmp.unlink(missing_ok=True)
    ctrl = parse_marks(debug_log(cr.stdout) + "\n" + (cr.stderr or ""))
    ctrl_job = ctrl.get("ctrlJob", "")

    print(f"  waiting {a.wait}s for the queued work to execute...\n")
    time.sleep(a.wait)
    ctrl_seen, ctrl_ids = _async_effect("ctrl")
    treat_seen, treat_ids = _async_effect("treat")
    residue = []

    if ctrl_seen is None:
        findings.append(("enqueued work and rollback", NOT_ESTABLISHED,
                         f"the control could not be read ({ctrl_ids}), so the treatment arm has "
                         f"nothing to be compared against"))
    elif not ctrl_seen:
        findings.append(("enqueued work and rollback", NOT_ESTABLISHED,
                         f"the CONTROL Queueable was enqueued (job {ctrl_job}) and committed, "
                         f"and left no record after {a.wait}s. Its work did not run, so the "
                         f"treatment arm's silence says nothing about rollback — it says a "
                         f"Queueable declared inside anonymous Apex may not execute at all here. "
                         f"INCONCLUSIVE by construction: a control that does not show up is the "
                         f"honest reason to withhold the finding, and the third design to reach "
                         f"this same wall. Establishing it needs a deployed ApexClass rather "
                         f"than an inner class, which is a metadata change this must not make."))
    elif treat_seen:
        residue += (treat_ids or []) + (ctrl_ids or [])
        findings.append(("enqueued work SURVIVES rollback", ESTABLISHED,
                         f"the control ran ({ctrl_ids}) AND the treatment ran ({treat_ids}). "
                         f"Enqueuing inside a savepoint and rolling back does NOT unschedule the "
                         f"work: shadowing an operation that enqueues anything runs that work "
                         f"for real, against real data, outside the transaction that was "
                         f"supposed to contain it"))
    else:
        residue += ctrl_ids or []
        findings.append(("enqueued work is discarded by rollback", ESTABLISHED,
                         f"the control ran and left {ctrl_ids}; the treatment left nothing. The "
                         f"channel works, so the rollback is what stopped the queued work"))

    # The docstring said "deletes by Id" from the first version and nothing ever deleted
    # anything. It happened to be harmless while the rollback removed the probe record — which
    # is the very thing under test, so the one scenario where residue appears is the one where
    # the claim was about to matter. Claiming a cleanup that does not run is the defect class
    # this repository exists to find.
    if residue:
        print(f"  cleaning up {len(residue)} record(s) left by the async arms, by Id\n")
        for rid in residue:
            sh("data", "delete", "record", "--sobject", "Account", "--record-id", rid,
               "--target-org", a.target_org)

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
