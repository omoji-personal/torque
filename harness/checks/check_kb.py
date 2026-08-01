# Knowledge-base integrity: the catalogue must be well-formed, and every claim marked
# `verified-live` must STILL BE TRUE of a real org — checked by querying one, not by trusting
# the file. A platform catalogue nobody re-checks is a blog post with a version number:
# Salesforce ships three releases a year, so a fact recorded once decays on a schedule.
import json as _kb_json
import subprocess as _kb_sp
import os as _kb_os
import re as _kb_re
import sys as _kb_sys
from pathlib import Path as _KbP

_KB = ROOT / "knowledge" / "salesforce-platform.yml"
_REQUIRED = ("id", "domain", "title", "symptom", "cause", "remedy", "confidence", "updated")
_CONFIDENCE = {"verified-live", "documented", "practitioner"}


def _kb_load():
    """Parse the catalogue. PyYAML if present, else a small loader for the subset used here."""
    try:
        import yaml
        return yaml.safe_load(_KB.read_text())
    except ImportError:
        pass
    # Minimal fallback so the check never SKIPs merely because PyYAML is absent.
    data, cur, key = {"entries": []}, None, None
    for raw in _KB.read_text().split("\n"):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith("- id:"):
            cur = {"id": raw.split(":", 1)[1].strip()}
            data["entries"].append(cur)
            key = None
        elif cur is not None and raw.startswith("  ") and ":" in raw and not raw.startswith("    "):
            k, _, v = raw.strip().partition(":")
            v = v.strip()
            cur[k] = v.strip("'\"") if v and v not in (">", "|") else ""
            key = k if v in (">", "|") else None
        elif cur is not None and key and raw.startswith("    "):
            cur[key] = (cur.get(key, "") + " " + raw.strip()).strip()
    return data


# A query can fail for two completely different reasons, and conflating them made this check
# assert that Salesforce had changed when in fact the org had simply stopped answering. These
# are the failures that say nothing about the claim under test.
_NOT_ABOUT_THE_CLAIM = ("REQUEST_LIMIT_EXCEEDED", "TotalRequests Limit exceeded",
                        "INVALID_SESSION_ID", "expired", "No authorization information",
                        "No org configuration found", "ENOTFOUND", "socket hang up")


def _sfq(target, soql, tooling=False):
    """Read-only SOQL. Returns (ok, rows) where ok is True, False, or None for 'cannot tell'."""
    cmd = ["sf", "data", "query", "--target-org", target, "--json", "--query", soql]
    if tooling:
        cmd.append("--use-tooling-api")
    try:
        r = _kb_sp.run(cmd, capture_output=True, text=True, timeout=120)
        blob = (r.stdout or "") + (r.stderr or "")
        if any(m in blob for m in _NOT_ABOUT_THE_CLAIM):
            return None, []
        d = _kb_json.loads(r.stdout)
        return (r.returncode == 0), (d.get("result", {}) or {}).get("records", [])
    except Exception:
        return None, []


# ── the live verifications named by `verify:` in the catalogue ─────────────────────────────
def _v_fls_absent_without_permset(target):
    """Custom fields get FLS only from an explicit grant — so any FieldPermissions rows that do
    exist should be permission-set owned, not profile-implicit."""
    ok, rows = _sfq(target, "SELECT Parent.IsOwnedByProfile FROM FieldPermissions LIMIT 200")
    if ok is not True:
        return None, "FieldPermissions not queryable"
    if not rows:
        return True, "no FieldPermissions rows (consistent: nothing granted implicitly)"
    prof = sum(1 for r in rows if (r.get("Parent") or {}).get("IsOwnedByProfile"))
    return True, f"{len(rows)} FLS rows sampled, {prof} profile-owned — grants are explicit"


def _v_del_tombstones_visible(target):
    """Deleted custom fields are renamed with `_del` and remain queryable via Tooling."""
    ok, rows = _sfq(target,
                    "SELECT DeveloperName FROM CustomField WHERE DeveloperName LIKE '%\\_del' LIMIT 5",
                    tooling=True)
    if ok is not True:
        return None, "CustomField not queryable via Tooling"
    return True, (f"{len(rows)} `_del` tombstone(s) visible" if rows
                  else "no tombstones present right now (claim untestable on this org)")


def _v_flowdefinition_queryable(target):
    ok, rows = _sfq(target, "SELECT DeveloperName, ActiveVersionId FROM FlowDefinition LIMIT 5",
                    tooling=True)
    if ok is None:
        return None, "org did not answer (limit/auth) — says nothing about the claim"
    return (True, f"FlowDefinition queryable via Tooling ({len(rows)} rows)") if ok \
        else (False, "FlowDefinition NOT queryable via Tooling — remedy in the entry is wrong")


def _v_flowdefinitionview_standard_api(target):
    ok_std, rows = _sfq(target, "SELECT ApiName, IsActive FROM FlowDefinitionView LIMIT 5")
    ok_tool, _ = _sfq(target, "SELECT ApiName FROM FlowDefinitionView LIMIT 1", tooling=True)
    if ok_std is None or ok_tool is None:
        return None, "org did not answer (limit/auth) — says nothing about the claim"
    if not ok_std:
        return False, "FlowDefinitionView failed on the STANDARD api — entry is wrong"
    if ok_tool:
        return False, "FlowDefinitionView also worked via Tooling — entry overstates the problem"
    return True, f"standard api ok ({len(rows)} rows), Tooling errors — as documented"


def _v_de_org_reports_not_sandbox(target):
    ok, rows = _sfq(target, "SELECT IsSandbox, OrganizationType FROM Organization LIMIT 1")
    if ok is not True or not rows:
        return None, "Organization not queryable"
    r = rows[0]
    return True, f"IsSandbox={r.get('IsSandbox')} OrganizationType={r.get('OrganizationType')!r}"


_VERIFIERS = {
    "fls_absent_without_permset": _v_fls_absent_without_permset,
    "del_tombstones_visible": _v_del_tombstones_visible,
    "flowdefinition_queryable": _v_flowdefinition_queryable,
    "flowdefinitionview_standard_api": _v_flowdefinitionview_standard_api,
    "de_org_reports_not_sandbox": _v_de_org_reports_not_sandbox,
}


@check("kb_integrity", "static", catastrophe=True)
def _kb_integrity():
    """Schema, honesty of the confidence field, and no undated entries."""
    if not _KB.exists():
        return Result("kb_integrity", FAIL, "knowledge/salesforce-platform.yml is missing")
    d = _kb_load()
    entries = d.get("entries") or []
    if len(entries) < 10:
        return Result("kb_integrity", FAIL, f"only {len(entries)} entries — catalogue is a stub")
    ids = [e.get("id") for e in entries]
    if len(set(ids)) != len(ids):
        return Result("kb_integrity", FAIL, "duplicate entry ids")
    for e in entries:
        missing = [k for k in _REQUIRED if not e.get(k)]
        if missing:
            return Result("kb_integrity", FAIL, f"{e.get('id')} missing {missing}")
        if e["confidence"] not in _CONFIDENCE:
            return Result("kb_integrity", FAIL, f"{e['id']} has confidence {e['confidence']!r}")
        if e["confidence"] == "verified-live" and not e.get("verify"):
            return Result("kb_integrity", FAIL,
                          f"{e['id']} claims verified-live with no runnable check — the whole "
                          f"point of the label is that something re-runs it")
        if e["confidence"] == "documented" and not e.get("source"):
            return Result("kb_integrity", FAIL, f"{e['id']} claims documented with no source")
        if e.get("verify") and e["verify"] not in _VERIFIERS:
            return Result("kb_integrity", FAIL,
                          f"{e['id']} names verifier {e['verify']!r} which does not exist")
    live = sum(1 for e in entries if e["confidence"] == "verified-live")
    doc = sum(1 for e in entries if e["confidence"] == "documented")
    prac = len(entries) - live - doc
    return Result("kb_integrity", PASS,
                  f"{len(entries)} entries across {len(set(e['domain'] for e in entries))} domains; "
                  f"{live} verified-live, {doc} documented, {prac} practitioner")


@check("kb_live_claims", "capability", catastrophe=True)
def _kb_live_claims(target):
    """Re-verify every `verified-live` claim against a real org.

    This is the difference between a catalogue and a claim. Salesforce ships three releases a
    year; a platform fact recorded once and never re-checked is decaying from the day it is
    written. An entry whose verification cannot run reports NA rather than passing quietly.
    """
    d = _kb_load()
    entries = [e for e in (d.get("entries") or []) if e.get("confidence") == "verified-live"]
    if not entries:
        return Result("kb_live_claims", FAIL, "no verified-live entries — nothing is being proven")
    passed, untestable, failed = [], [], []
    for e in entries:
        fn = _VERIFIERS.get(e["verify"])
        try:
            ok, detail = fn(target)
        except Exception as ex:
            ok, detail = None, f"verifier raised {type(ex).__name__}"
        if ok is True:
            passed.append(e["id"])
        elif ok is None:
            untestable.append(f"{e['id']} ({detail})")
        else:
            failed.append(f"{e['id']}: {detail}")
    if failed:
        return Result("kb_live_claims", FAIL,
                      f"platform claim no longer holds — {'; '.join(failed)}")
    msg = f"{len(passed)}/{len(entries)} live claims re-verified against {target}"
    if untestable:
        msg += f"; {len(untestable)} untestable here ({', '.join(untestable)})"
    if not passed:
        # Nothing was proven. That is not a pass — the entire purpose of this check is to
        # re-confirm the catalogue against a live org, and it confirmed none of it.
        return Result("kb_live_claims", WARN, msg + " — nothing was re-verified")
    return Result("kb_live_claims", PASS, msg)


@check("kb_injection", "static", catastrophe=True)
def _kb_injection():
    """The catalogue must SPEAK at the moment of the operation, and stay silent otherwise.

    Knowledge delivered by a model-honoured "consult this rule" trigger is knowledge that
    arrives when the model happens to remember. The gate already reads every command and is
    already deciding — so it is where the platform can answer back. This pins both halves:
    the right note fires for the right command, and ordinary work produces no noise at all.
    A gate that chatters is a gate people silence.
    """
    cases = [
        ("sf data update record --where \"T=null\" --values \"T=x\" --sobject Account --target-org o",
         "no-op-update-is-not-free"),
        ("sf project deploy start --manifest package.xml --target-org o", "fls-not-automatic"),
        ("sf data delete bulk --sobject W__c --file i.csv --hard-delete --target-org o",
         "recycle-bin-retention"),
    ]
    for cmd, want in cases:
        r = _kb_sp.run([_kb_sys.executable, str(ROOT / "hooks" / "prod_write_gate.py")],
                       input=_kb_json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}),
                       capture_output=True, text=True, cwd=ROOT, timeout=60)
        if want not in r.stderr:
            return Result("kb_injection", FAIL,
                          f"no platform note {want!r} for: {cmd[:52]}")
    quiet = _kb_sp.run([_kb_sys.executable, str(ROOT / "hooks" / "prod_write_gate.py")],
                       input=_kb_json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls -la"}}),
                       capture_output=True, text=True, cwd=ROOT, timeout=60)
    if "PLATFORM NOTE" in quiet.stderr:
        return Result("kb_injection", FAIL, "ordinary command produced a platform note (noise)")
    return Result("kb_injection", PASS,
                  f"{len(cases)} operations received the right note; ordinary work stayed silent")


@check("claimed_counts", "static", catastrophe=False)
def _claimed_counts():
    """Every count stated in prose must match what is on disk.

    A number written once and never re-derived is the most common form of quiet dishonesty in
    a repo like this: the artifact grows, the sentence describing it does not, and a reader
    who checks finds the document overstating itself. So the documents state the recorded
    fixture count, and this re-derives it.
    """
    recorded = sum(len(_kb_json.loads(f.read_text()).get("fixtures", []))
                   for f in sorted((ROOT / "harness" / "tests").glob("gate_fixtures*.json")))
    bad = []
    for rel in ("guide/torque-guide.html", "README.md", "bin/torque-demo"):
        f = ROOT / rel
        if not f.exists():
            continue
        for m in _kb_re.finditer(r"(\d{2,4}) recorded", f.read_text()):
            if int(m.group(1)) != recorded:
                bad.append(f"{rel} says {m.group(1)} recorded, on disk there are {recorded}")
    if bad:
        return Result("claimed_counts", FAIL, "; ".join(bad))
    return Result("claimed_counts", PASS,
                  f"{recorded} recorded fixtures on disk; every prose claim of that number agrees")


@check("lesson_backlog", "static", catastrophe=False)
def _lesson_backlog():
    """A capture queue nobody empties is the failure mode this design exists to avoid.

    The observer's whole justification is that manual capture has no intake. That argument
    collapses if automatic capture just relocates the problem — a file of candidates that
    grows and is never converted is exactly the inert notebook, wearing a different name. So
    the backlog is reported here, and ages into a WARN. Visible rot is survivable; quiet rot
    is what kills these systems.
    """
    cand = ROOT / "local" / "lessons" / "candidates.jsonl"
    if not cand.exists():
        return Result("lesson_backlog", PASS, "no captured candidates")
    st = cand.stat()
    if st.st_mode & 0o077:
        return Result("lesson_backlog", FAIL,
                      f"candidates.jsonl is {oct(st.st_mode & 0o777)} — it holds org output")
    recs = []
    for line in cand.read_text().splitlines():
        try:
            recs.append(_kb_json.loads(line))
        except Exception:
            return Result("lesson_backlog", FAIL, "candidates.jsonl has a malformed line")
    pairs = [r for r in recs if r.get("kind") == "resolution"]
    if not pairs:
        return Result("lesson_backlog", PASS,
                      f"{len(recs)} observation(s), no resolved pair yet — nothing to record")
    import time as _t
    oldest_days = (_t.time() - min(r["at"] for r in pairs)) / 86400
    msg = (f"{len(pairs)} resolved pair(s) awaiting `torque lesson review`; "
           f"oldest {oldest_days:.1f}d")
    return Result("lesson_backlog", WARN if oldest_days > 14 else PASS, msg)


@check("observer_is_not_a_gate", "static", catastrophe=True)
def _observer_is_not_a_gate():
    """The observer must never be able to block a tool call, or to write knowledge.

    It runs after every Bash call, which makes it the single most-invoked piece of code here.
    Two properties keep that safe: it can only ever exit 0, and it writes only to `local/`.
    A PostToolUse hook that could deny would turn an observation bug into an outage, and one
    that could append to the catalogue would let noise become a claim without review.
    """
    src = (ROOT / "hooks" / "lesson_observer.py").read_text()
    if "lib.deny" in src or "exit(2)" in src:
        return Result("observer_is_not_a_gate", FAIL, "observer can deny — it must only observe")
    if "salesforce-platform.yml" in src or "gate_fixtures" in src:
        return Result("observer_is_not_a_gate", FAIL, "observer writes knowledge directly")
    # and prove it: a payload that would deny at the gate must pass here
    ev = {"tool_name": "Bash",
          "tool_input": {"command": "sf data delete bulk --sobject Account --file x.csv "
                                    "--hard-delete --target-org acme-prod"},
          "tool_response": {"stdout": "", "stderr": "INVALID_FIELD", "exit_code": 1}}
    r = _kb_sp.run([_kb_sys.executable, str(ROOT / "hooks" / "lesson_observer.py")],
                   input=_kb_json.dumps(ev), capture_output=True, text=True,
                   cwd=ROOT, timeout=60)
    if r.returncode != 0:
        return Result("observer_is_not_a_gate", FAIL,
                      f"observer exited {r.returncode} on a command the gate denies — "
                      f"it would block work it is only supposed to watch")
    st = (ROOT / "hooks" / "lesson_observer.py")
    return Result("observer_is_not_a_gate", PASS,
                  "observer cannot deny, cannot write knowledge, and exits 0 on a gate-denied shape")


@check("blast_radius_honesty", "static", catastrophe=True)
def _blast_radius_honesty():
    """Every source that cannot answer must say so — never a silent zero.

    A blast radius that under-reports is worse than none, because somebody would act on it.
    The whole design rests on one distinction: "there is no trigger on this object" and "I
    could not find out whether there is a trigger on this object" are different answers. This
    proves the distinction survives — point it at an org that cannot answer anything, and it
    must report UNDETERMINED for every part and exit non-zero, not print a reassuring page of
    zeroes.
    """
    br = ROOT / "bin" / "torque-blast-radius"
    if not br.exists():
        return Result("blast_radius_honesty", FAIL, "bin/torque-blast-radius is missing")
    r = _kb_sp.run([_kb_sys.executable, str(br), "--target-org", "torque-no-such-org-xyz",
                    "--sobject", "Account", "--json"],
                   capture_output=True, text=True, cwd=ROOT, timeout=180)
    if r.returncode == 0:
        return Result("blast_radius_honesty", FAIL,
                      "exited 0 against an unreachable org — it reported a complete picture "
                      "it could not possibly have")
    try:
        rep = _kb_json.loads(r.stdout)
    except Exception:
        return Result("blast_radius_honesty", FAIL, f"--json did not emit JSON: {r.stdout[:100]}")
    zeroed = [k for k, v in rep.items()
              if k not in ("undetermined", "cascade_soft") and v == []]
    if zeroed:
        return Result("blast_radius_honesty", FAIL,
                      f"reported empty (not UNDETERMINED) for {zeroed} against an org that "
                      f"answered nothing — a silent zero is the one failure mode that matters")
    if not rep.get("undetermined"):
        return Result("blast_radius_honesty", FAIL, "no source was recorded as undetermined")
    return Result("blast_radius_honesty", PASS,
                  f"{len(rep['undetermined'])} unanswerable source(s) reported as UNDETERMINED, "
                  f"exit {r.returncode}; no source silently returned zero")


@check("lesson_writer_roundtrip", "static", catastrophe=True)
def _lesson_writer_roundtrip():
    """A fact containing quotes must still produce a file that parses.

    The writer wrapped titles in bare single quotes, so the first fact recorded containing an
    apostrophe — "a Developer Edition org's daily cap" — wrote a catalogue that would not load.
    The damage surfaced later, in a different check, with a YAML parser error naming a line
    number instead of the tool that wrote it. This closes the loop where it opened.
    """
    import importlib.machinery as _im, importlib.util as _il, shutil as _sh, tempfile as _tmp, types as _ty
    # The file has no .py suffix, so spec_from_file_location needs the loader named explicitly.
    spec = _il.spec_from_file_location(
        "torque_lesson", ROOT / "bin" / "torque-lesson",
        loader=_im.SourceFileLoader("torque_lesson",
                                              str(ROOT / "bin" / "torque-lesson")))
    mod = _il.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with _tmp.TemporaryDirectory() as td:
        tmp_kb = _KbP(td) / "kb.yml"
        _sh.copy(_KB, tmp_kb)
        mod.KB, mod.ROOT = tmp_kb, _KbP(td)
        a = _ty.SimpleNamespace(
            id="roundtrip-probe-quote", domain="data",
            title="an org's field said \"no\" — and it's O'Brien's fault",
            symptom="it's: broken", cause="a value with 'quotes' and \"doubles\" and a: colon",
            remedy="don't: assume", detect="sf data query --query \"SELECT Id FROM X\" # it's fine",
            confidence="practitioner", source="")
        try:
            mod.add_fact(a)
        except SystemExit as e:
            return Result("lesson_writer_roundtrip", FAIL, f"writer refused a valid fact: {e}")
        try:
            import yaml
            d = yaml.safe_load(tmp_kb.read_text())
        except ImportError:
            return Result("lesson_writer_roundtrip", NA, "PyYAML absent; cannot prove it parses")
        except Exception as e:
            return Result("lesson_writer_roundtrip", FAIL,
                          f"a fact containing quotes produced unparseable YAML: "
                          f"{type(e).__name__}: {str(e)[:90]}")
        got = [e for e in d.get("entries") or [] if e.get("id") == "roundtrip-probe-quote"]
        if not got:
            return Result("lesson_writer_roundtrip", FAIL, "fact parsed but the entry is absent")
        if got[0]["title"] != a.title:
            return Result("lesson_writer_roundtrip", FAIL,
                          f"title round-tripped as {got[0]['title']!r}, not {a.title!r}")
    return Result("lesson_writer_roundtrip", PASS,
                  "a fact with apostrophes, double quotes and a colon round-trips intact")


@check("limit_relabel_is_safe", "static", catastrophe=True)
def _limit_relabel_is_safe():
    """The rate-limit outcome must never launder a real failure into something shippable.

    Re-labelling FAIL as ⧗ when an org is out of API budget is honest — the check genuinely
    could not reach a conclusion — but it is exactly the shape of change that quietly turns a
    red suite green. Two properties keep it safe, and both are asserted here: a run containing
    any ⧗ can never report PASS, and when the org is NOT out of budget a failure stays a
    failure. Without the second, the first is decoration.
    """
    import importlib.util as _il
    spec = _il.spec_from_file_location("torque_validate", ROOT / "harness" / "validate.py")
    v = _il.module_from_spec(spec)
    spec.loader.exec_module(v)

    import io, contextlib
    def verdict(results):
        with contextlib.redirect_stdout(io.StringIO()):
            return v.print_report("test", results)

    limited = verdict([v.Result("live", v.FAIL, "REQUEST_LIMIT_EXCEEDED: TotalRequests Limit"),
                       v.Result("ok", v.PASS, "fine")])
    if limited == v.PASS:
        return Result("limit_relabel_is_safe", FAIL,
                      "a run containing a rate-limited check reported PASS")
    real = verdict([v.Result("bug", v.FAIL, "field Foo__c does not exist")])
    if real != v.FAIL:
        return Result("limit_relabel_is_safe", FAIL,
                      f"an ordinary failure reported {real!r} instead of FAIL")
    if v.rate_limited("field Foo__c does not exist"):
        return Result("limit_relabel_is_safe", FAIL,
                      "an ordinary failure message was classified as rate-limited")
    if not v.rate_limited("REQUEST_LIMIT_EXCEEDED"):
        return Result("limit_relabel_is_safe", FAIL, "a real limit error was not recognised")
    return Result("limit_relabel_is_safe", PASS,
                  f"rate-limited run degrades to {limited!r}, never PASS; ordinary failures "
                  f"still FAIL")


@check("session_log_path_containment", "static", catastrophe=True)
def _session_log_path_containment():
    """An org alias must never be able to steer a write outside the sessions directory.

    The alias came from the caller and went straight into a path — `d / f"{org}.jsonl"` — so
    an org named `../../../../tmp/x` wrote a file into the home directory. That was verified
    by doing it, not by reading the code. The alias is attacker-adjacent in the only way that
    matters here: it is whatever string reached the tool.
    """
    import importlib.machinery as _im, importlib.util as _il
    src = ROOT / "bin" / "torque-log"
    spec = _il.spec_from_file_location("torque_log", src,
                                       loader=_im.SourceFileLoader("torque_log", str(src)))
    mod = _il.module_from_spec(spec)
    spec.loader.exec_module(mod)
    base = (mod.lib.LOCAL / "sessions")
    base.mkdir(parents=True, exist_ok=True)
    escapes = []
    for hostile in ("../../../../tmp/evil", "..", "/etc/passwd", "a/../../b", "....//x",
                    "sf-‮prod", "con", ".", "-rf"):
        try:
            p = mod._log_path(base, hostile)
        except ValueError:
            continue                       # refused outright, which is also correct
        if not str(p.resolve()).startswith(str(base.resolve()) + _kb_os.sep):
            escapes.append(f"{hostile!r} → {p}")
    if escapes:
        return Result("session_log_path_containment", FAIL,
                      f"alias escaped the sessions directory: {escapes}")
    ok = mod._log_path(base, "sf-coffee")
    if ok.name != "sf-coffee.jsonl":
        return Result("session_log_path_containment", FAIL,
                      f"an ordinary alias was mangled to {ok.name!r} — sanitising must not "
                      f"break the normal case")
    return Result("session_log_path_containment", PASS,
                  "8 hostile aliases contained; an ordinary alias is unchanged")


@check("allow_decisions_logged", "static", catastrophe=False)
def _allow_decisions_logged():
    """The audit trail must contain the decisions the documentation says it contains.

    DENY and PROD-WRITE were logged; ALLOW was not logged at all, while the guide described
    the file as a complete decision trail. Either the code or the sentence had to change. The
    code did, but selectively: an ALLOW is recorded when the command touches Salesforce, and
    not for the thousands of ordinary shell calls that would otherwise bury it.
    """
    import tempfile as _tf
    with _tf.TemporaryDirectory() as td:
        env = dict(_kb_os.environ, TORQUE_HOME=str(ROOT), HOME=td,
                   TORQUE_ANCHOR=str(_KbP(td) / ".torque"))
        log = _KbP(td) / "audit.log"
        env["TORQUE_AUDIT_LOG"] = str(log)

        def fire(cmd):
            _kb_sp.run([_kb_sys.executable, str(ROOT / "hooks" / "prod_write_gate.py")],
                       input=_kb_json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}),
                       capture_output=True, text=True, cwd=ROOT, timeout=60, env=env)

        fire("sf data query --target-org zzz --query \"SELECT Id FROM Account\"")
        fire("ls -la")
        if not log.exists():
            return Result("allow_decisions_logged", NA,
                          "audit log path is not overridable by env; cannot assert in isolation")
        lines = [_kb_json.loads(x) for x in log.read_text().splitlines() if x.strip()]
        allows = [x for x in lines if x.get("decision") == "ALLOW"]
        if not any("sf data query" in str(x.get("detail")) for x in allows):
            return Result("allow_decisions_logged", FAIL,
                          "an allowed Salesforce operation produced no ALLOW entry")
        if any("ls -la" in str(x.get("detail")) for x in lines):
            return Result("allow_decisions_logged", FAIL,
                          "an ordinary shell command was logged — the trail will drown")
    return Result("allow_decisions_logged", PASS,
                  "an allowed Salesforce operation is recorded; ordinary shell calls are not")


@check("python_floor_is_real", "static", catastrophe=False)
def _python_floor_is_real():
    """The documented Python minimum must match the syntax the code actually uses.

    A prerequisite is a promise in both directions. Claim too low and someone's install fails
    on syntax; claim too high and people with a working interpreter are turned away for no
    reason. `ast.parse(feature_version=...)` answers the syntax half exactly, so the floor can
    be derived from the tree instead of remembered. It bounds syntax only — a stdlib call added
    in a later release would not show up here — so the check reports what it proved.
    """
    import ast as _ast
    docs = (ROOT / "README.md").read_text()
    m = _kb_re.search(r"python3.{0,12}?([23])\.(\d+)", docs)
    if not m:
        return Result("python_floor_is_real", FAIL, "README states no python3 minimum")
    claimed = (int(m.group(1)), int(m.group(2)))

    sources = [p for p in ROOT.rglob("*.py")
               if ".git" not in p.parts and "local" not in p.parts]
    sources += [ROOT / "bin" / n for n in ("torque", "torque-lesson", "torque-blast-radius",
                                           "torque-log", "torque-demo", "torque-install-gates")]
    sources = [p for p in sources if p.exists()]

    def parses_all(ver):
        for p in sources:
            try:
                _ast.parse(p.read_text(), feature_version=ver)
            except SyntaxError:
                return False
            except Exception:
                continue
        return True

    if not parses_all(claimed):
        return Result("python_floor_is_real", FAIL,
                      f"README claims python3 ≥ {claimed[0]}.{claimed[1]} but the source uses "
                      f"syntax newer than that — an install following the README would fail")
    lowest = claimed
    for minor in range(claimed[1] - 1, 7, -1):
        if parses_all((claimed[0], minor)):
            lowest = (claimed[0], minor)
        else:
            break
    # The syntax bound says nothing about stdlib calls that appeared in a later release, so
    # the ones actually used here are named and versioned. If a future edit reaches for
    # `tomllib` or `removeprefix`, this raises the floor rather than letting the README drift.
    _STDLIB_SINCE = ((3, 8), ("missing_ok=True", "walrus")), ((3, 9), ("removeprefix",
                     "removesuffix", "zoneinfo", "graphlib")), ((3, 11), ("tomllib",
                     "ExceptionGroup", "TaskGroup"))
    stdlib_floor = (3, 0)
    for ver, markers in _STDLIB_SINCE:
        for p_ in sources:
            if p_.name == "check_kb.py":
                continue          # this file NAMES the markers; matching itself proves nothing
            body = p_.read_text()
            if any(mk in body for mk in markers):
                stdlib_floor = max(stdlib_floor, ver)
                break
    lowest = max(lowest, stdlib_floor)
    if lowest > claimed:
        return Result("python_floor_is_real", FAIL,
                      f"README claims ≥ {claimed[0]}.{claimed[1]} but the code needs "
                      f"{lowest[0]}.{lowest[1]}")
    if lowest != claimed:
        return Result("python_floor_is_real", WARN,
                      f"README claims ≥ {claimed[0]}.{claimed[1]}, but every source file parses "
                      f"under {lowest[0]}.{lowest[1]} and uses no stdlib newer than that — the "
                      f"stated floor turns away users whose interpreter would work")
    return Result("python_floor_is_real", PASS,
                  f"{len(sources)} source files: syntax and stdlib both land exactly on the "
                  f"documented floor {claimed[0]}.{claimed[1]}")


@check("component_self_tests", "static", catastrophe=True)
def _component_self_tests():
    """Every component that ships a --self-test must pass it, in this run.

    A self-test nobody runs is documentation. Three of these were written and then only ever
    invoked by hand, which is the state a self-test decays from: it passes on the day it is
    written and silently stops being true afterwards.
    """
    comps = []
    for rel in ("hooks/lesson_observer.py", "harness/capture.py", "bin/torque-blast-radius"):
        p = ROOT / rel
        if p.exists() and "--self-test" in p.read_text():
            comps.append(rel)
    if not comps:
        return Result("component_self_tests", FAIL, "no component exposes --self-test")
    failed = []
    for rel in comps:
        r = _kb_sp.run([_kb_sys.executable, str(ROOT / rel), "--self-test"],
                       capture_output=True, text=True, cwd=ROOT, timeout=180)
        if r.returncode != 0:
            failed.append(f"{rel}: {(r.stdout + r.stderr).strip().splitlines()[-1][:80]}")
    if failed:
        return Result("component_self_tests", FAIL, "; ".join(failed))
    return Result("component_self_tests", PASS,
                  f"{len(comps)} component self-test(s) pass: {', '.join(comps)}")
