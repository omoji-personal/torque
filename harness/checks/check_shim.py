# The exec-time `sf` shim: does it actually stand in the way, and does it stand aside correctly?
#
# The hooks bind the agent's tool surface. `.claude/rules/org-safety.md` has disclosed for as
# long as the gates have existed that a script the agent writes and runs can spawn `sf` where no
# hook sees it. The shim closes that by deciding at exec time, where bash has already finished
# every expansion and the argv is simply a fact.
#
# Everything here runs against a THROWAWAY PATH containing a fake `sf`. That is not only for
# isolation: with a fake binary on PATH, org classification cannot reach Salesforce, fails, and
# therefore classifies production — so every write decision below is offline, deterministic, and
# takes milliseconds. The fake `sf` also records every invocation, which is how these checks tell
# "refused" from "allowed and then failed for some other reason". A test that only reads exit
# codes cannot tell those apart, and the difference is the entire subject.
import os as _sh_os
import json as _sh_json
import shutil as _sh_shutil
import subprocess as _sh_sp
import sys as _sh_sys
import tempfile as _sh_tf
from pathlib import Path as _ShP

_SHIM_SRC = ROOT / "bin" / "torque-shim-sf"
DENY_CODE = 2                     # the exit every Torque gate uses to mean "refused"

# A fake `sf` that records the argv it was handed and succeeds. Written in Python so it behaves
# the same on any platform the rest of the harness runs on.
_FAKE_SF = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["TORQUE_TEST_RAN"], "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
print("fake sf ok")
"""


class _Bench:
    """A PATH with the shim in front of a fake `sf`, and a record of what got through."""

    def __init__(self, td, gates=None):
        self.dir = _ShP(td)
        self.shim_dir = self.dir / "shim"
        self.real_dir = self.dir / "realbin"
        self.shim_dir.mkdir(parents=True, exist_ok=True)
        self.real_dir.mkdir(parents=True, exist_ok=True)
        src = _SHIM_SRC.read_text()
        if gates is not None:
            # The falsifying build: same shim, no gates consulted.
            src = src.replace('GATES = ("prod_write_gate.py", "destructive_data_gate.py")',
                              f"GATES = {gates!r}", 1)
        for name in ("sf", "sfdx"):
            p = self.shim_dir / name
            p.write_text(src)
            _sh_os.chmod(str(p), 0o755)
        (self.shim_dir / "home").write_text(str(ROOT) + "\n")
        fake = self.real_dir / "sf"
        fake.write_text(_FAKE_SF)
        _sh_os.chmod(str(fake), 0o755)
        _sh_shutil.copyfile(str(fake), str(self.real_dir / "sfdx"))
        _sh_os.chmod(str(self.real_dir / "sfdx"), 0o755)
        self.ran = self.dir / "ran.jsonl"

    def env(self, **extra):
        e = dict(_sh_os.environ)
        # shim, then the fake CLI, then wherever this interpreter lives — the shim's shebang is
        # `env python3`, so a PATH with no python3 on it means the shim never starts and every
        # invocation "fails" with 127. That looked exactly like a refusal and turned the first
        # version of shim_gates_the_subprocess_channel green while testing nothing. The
        # interpreter's own directory is added rather than the whole inherited PATH so no real
        # `sf` can be found behind the fake one.
        interp = str(_ShP(_sh_sys.executable).resolve().parent)
        e["PATH"] = _sh_os.pathsep.join([str(self.shim_dir), str(self.real_dir), interp])
        e["TORQUE_TEST_RAN"] = str(self.ran)
        e.pop("TORQUE_HOME", None)
        e.update(extra)
        return e

    def interpreter_dir_is_clean(self):
        """No real `sf` next to python3, or 'the fake is the only CLI' stops being true."""
        d = _ShP(_sh_sys.executable).resolve().parent
        return not any((d / n).exists() for n in ("sf", "sfdx"))

    def run(self, *args, name="sf", **envextra):
        """Invoke the shim as the CLI. stdin is a pipe, so operator_present() is False and the
        classifier path is exercised — the same reason a real agent subprocess is not an
        operator."""
        before = self.ran.read_text() if self.ran.exists() else ""
        r = _sh_sp.run([str(self.shim_dir / name), *args], input="", capture_output=True,
                       text=True, env=self.env(**envextra), cwd=str(self.dir), timeout=180)
        after = self.ran.read_text() if self.ran.exists() else ""
        return r, after != before          # (result, did the real binary run)


@check("shim_gates_the_subprocess_channel", "static", catastrophe=True)
def _shim_gates_the_subprocess_channel():
    """A write reaching `sf` through the shim is refused, and the binary is never reached.

    "Refused" has to mean the operation did not happen, not that a non-zero code was printed
    somewhere. So the fake `sf` records every invocation and this asserts the record stayed
    empty. Every earlier version of this repository's gate tests that checked only an exit code
    could have passed against a shim that denied loudly and executed anyway.
    """
    name = "shim_gates_the_subprocess_channel"
    if not _SHIM_SRC.exists():
        return Result(name, FAIL, "bin/torque-shim-sf is missing")
    cases = [
        (["data", "update", "record", "--sobject", "Account", "--values", "Name=x"],
         "write with no target"),
        (["data", "update", "record", "--sobject", "Account", "--values", "Name=x",
          "--target-org", "torque-test-nonexistent"], "write to an org that will not classify"),
        (["data", "delete", "bulk", "--sobject", "Log__c", "--file", "i.csv",
          "--target-org", "torque-test-nonexistent"], "bulk delete"),
        (["apex", "run", "--file", "x.apex", "--target-org", "torque-test-nonexistent"],
         "anonymous apex"),
        (["force:data:bulk:delete", "-s", "Log__c", "-f", "i.csv", "-u",
          "torque-test-nonexistent"], "legacy force: spelling"),
    ]
    bad = []

    def judge(r, ran, label):
        # "Non-zero" is not "refused". 127 is the shell saying it could not start the shim, and
        # an earlier version of this check accepted it as a denial across every case — five
        # green assertions, none of which had reached a gate. The verdict must be exit 2 AND a
        # denial message, which is the contract every other gate in this repo is held to.
        if ran:
            bad.append(f"{label}: the binary RAN (exit {r.returncode})")
        elif r.returncode != DENY_CODE:
            bad.append(f"{label}: exit {r.returncode}, want {DENY_CODE} — "
                       f"{((r.stderr or r.stdout) or '(silent)').strip()[:70]}")
        elif "DENY" not in (r.stderr or ""):
            bad.append(f"{label}: exit 2 with no denial message — "
                       f"{(r.stderr or '(silent)').strip()[:70]}")

    with _sh_tf.TemporaryDirectory() as td:
        b = _Bench(td)
        if not b.interpreter_dir_is_clean():
            return Result(name, FAIL,
                          "a real sf/sfdx sits beside this interpreter, so the bench cannot "
                          "guarantee the fake CLI is the only one reachable")
        for argv, label in cases:
            judge(*b.run(*argv), label)
        # and the legacy name is shimmed too, or half the vocabulary is ungated
        judge(*b.run("data", "update", "record", "--sobject", "Account", "--values", "N=x",
                     name="sfdx"), "sfdx spelling")
    if bad:
        return Result(name, FAIL, "; ".join(bad))
    return Result(name, PASS,
                  f"{len(cases) + 1} write shapes refused at exec time under both binary names, "
                  f"each with exit {DENY_CODE} and a stated reason, and the real CLI was never "
                  f"reached")


@check("shim_lets_real_work_through", "static", catastrophe=True)
def _shim_lets_real_work_through():
    """A gate that blocks real work gets switched off, and then protects nothing.

    Reads must reach the binary WITHOUT the gates being consulted at all. That is not only a
    latency argument: a read that consulted the gates would make the gates classify an org,
    which runs `sf org display`, which comes back through this shim. The recursion is bounded by
    exactly this branch, so this check is also the proof that the shim terminates.
    """
    name = "shim_lets_real_work_through"
    reads = [
        (["org", "display", "--target-org", "torque-test-nonexistent"], "org display"),
        (["data", "query", "--query", "SELECT Id FROM Account", "--target-org",
          "torque-test-nonexistent"], "soql query"),
        (["--version"], "version"),
        (["project", "deploy", "start", "--dry-run", "--manifest", "p.xml", "--target-org",
          "torque-test-nonexistent"], "validate-only deploy"),
    ]
    bad = []
    with _sh_tf.TemporaryDirectory() as td:
        b = _Bench(td)
        for argv, label in reads:
            r, ran = b.run(*argv)
            if r.returncode != 0:
                bad.append(f"{label}: refused (exit {r.returncode}) — {(r.stderr or '')[:80]}")
            elif not ran:
                bad.append(f"{label}: exit 0 but the binary never ran")
        # argv is delivered byte-for-byte. The shim used to rebuild a command string with
        # shlex.join and post it as a Bash event; a value carrying a literal backtick came back
        # as command substitution and the write was refused for punctuation in somebody's
        # Description field. Nothing had expanded — bash was finished before the shim existed.
        odd = "N=" + chr(96) + "x" + chr(96) + " " + chr(36) + "(y) 'z'"
        r, ran = b.run("data", "query", "--query", odd, "--target-org", "torque-test-nonexistent")
        if r.returncode != 0 or not ran:
            bad.append(f"shell punctuation in a value: exit {r.returncode}, ran={ran}")
        else:
            got = _sh_json.loads([l for l in b.ran.read_text().splitlines() if l][-1])
            if odd not in got:
                bad.append(f"argv was altered in transit: {got!r}")
    if bad:
        return Result(name, FAIL, "; ".join(bad))
    return Result(name, PASS,
                  f"{len(reads)} read shapes reach the CLI ungated, and a value carrying "
                  f"backticks, {chr(36)}( and quotes arrives byte-for-byte")


@check("shim_fails_closed", "static", catastrophe=True)
def _shim_fails_closed():
    """Every way the shim can fail to reach a verdict must end in a refusal.

    A gate that cannot load its rules, cannot find its home, or finds itself re-entered has not
    decided anything. The only safe reading of "I do not know" is no.
    """
    name = "shim_fails_closed"
    bad = []
    with _sh_tf.TemporaryDirectory() as td:
        b = _Bench(td)
        write = ["data", "update", "record", "--sobject", "Account", "--values", "N=x",
                 "--target-org", "torque-test-nonexistent"]

        # 1. no recorded home, no TORQUE_HOME in the environment
        (b.shim_dir / "home").unlink()
        r, ran = b.run(*write)
        if r.returncode == 0 or ran:
            bad.append(f"missing home: exit {r.returncode}, ran={ran}")
        if "TORQUE_HOME" not in (r.stderr or ""):
            bad.append("missing home: refused without saying it could not find its rules")
        (b.shim_dir / "home").write_text(str(ROOT) + "\n")

        # 2. a recorded home that does not hold the gates
        (b.shim_dir / "home").write_text(str(b.dir / "nowhere") + "\n")
        r, ran = b.run(*write)
        if r.returncode == 0 or ran:
            bad.append(f"home pointing nowhere: exit {r.returncode}, ran={ran}")
        (b.shim_dir / "home").write_text(str(ROOT) + "\n")

        # 3. re-entry. Bounded by construction, so this should be unreachable — which is exactly
        #    why it is worth asserting, since an unreachable branch nobody tests is a branch that
        #    quietly stops working.
        r, ran = b.run(*write, TORQUE_SHIM_DEPTH="1")
        if r.returncode == 0 or ran:
            bad.append(f"re-entry: exit {r.returncode}, ran={ran}")

        # 4. no real binary behind the shim. It must say Torque did not refuse this — a missing
        #    CLI reported as a policy denial sends the operator to fix the wrong thing.
        for n in ("sf", "sfdx"):
            (b.real_dir / n).unlink()
        r, ran = b.run(*write)
        if r.returncode == 0:
            bad.append("no real binary: exit 0")
        if "did not refuse" not in (r.stderr or ""):
            bad.append("no real binary: refusal does not distinguish itself from a policy deny")
    if bad:
        return Result(name, FAIL, "; ".join(bad))
    return Result(name, PASS,
                  "4 indeterminate states (no home, wrong home, re-entry, no CLI behind it) all "
                  "refuse, and a missing CLI says so rather than posing as a policy denial")


@check("shim_gate_consultation_is_load_bearing", "static")
def _shim_gate_consultation_is_load_bearing():
    """Build the shim with no gates and require the same write to go straight through.

    Without this, `shim_gates_the_subprocess_channel` could be passing because the fake `sf`
    happens not to run, because the temp PATH is wrong, because the shim crashes early — any
    number of reasons unrelated to a gate having refused anything. This is the same trap that
    kept a catastrophe-class check in this repo green for thirteen commits while asserting
    nothing: the expected outcome arrived for a reason nobody checked.
    """
    name = "shim_gate_consultation_is_load_bearing"
    write = ["data", "update", "record", "--sobject", "Account", "--values", "N=x",
             "--target-org", "torque-test-nonexistent"]
    with _sh_tf.TemporaryDirectory() as td:
        base = _Bench(_ShP(td) / "real")
        r_base, ran_base = base.run(*write)
        with _sh_tf.TemporaryDirectory() as td2:
            mut = _Bench(td2, gates=())
            r_mut, ran_mut = mut.run(*write)
    if r_base.returncode == 0 or ran_base:
        return Result(name, FAIL,
                      f"VACUOUS — the un-mutated shim did not refuse this write "
                      f"(exit {r_base.returncode}, ran={ran_base}), so removing the gates "
                      f"proves nothing")
    if r_mut.returncode != 0 or not ran_mut:
        return Result(name, FAIL,
                      f"with the gates removed the write was still stopped "
                      f"(exit {r_mut.returncode}, ran={ran_mut}) — something other than the "
                      f"gates is doing the refusing, and it is not under test")
    return Result(name, PASS,
                  "the same write is refused with the gates and executes without them; the "
                  "refusal is theirs")


@check("shim_and_bash_paths_agree", "static")
def _shim_and_bash_paths_agree():
    """Two entry points, one question. Something has to compare them.

    handle_argv and handle_bash decide the same thing about the same operation, arriving by
    different roads. That is the shape this repository keeps getting wrong — a second
    representation nobody puts next to the first — so it is checked rather than assumed.

    They are permitted to disagree in exactly one direction, on exactly one class of input: when
    a value carries shell punctuation, the text road cannot tell a byte the kernel delivered
    from a substitution a human typed, and refuses. The argv road knows. That case is listed
    here as an expected disagreement WITH its direction asserted, so it cannot silently invert.
    """
    name = "shim_and_bash_paths_agree"
    import shlex as _shx
    gate = ROOT / "hooks" / "prod_write_gate.py"

    def verdict(payload):
        r = _sh_sp.run([_sh_sys.executable, str(gate)], input=_sh_json.dumps(payload),
                       capture_output=True, text=True, cwd=str(ROOT), timeout=180)
        return r.returncode

    agree = [
        ["data", "update", "record", "--sobject", "Account", "--values", "Name=x"],
        ["org", "display", "--target-org", "torque-test-nonexistent"],
        ["data", "query", "--query", "SELECT Id FROM Account", "-o", "torque-test-nonexistent"],
        ["project", "deploy", "start", "--dry-run", "--manifest", "p.xml", "-o",
         "torque-test-nonexistent"],
        ["data", "delete", "bulk", "--sobject", "Log__c", "--file", "i.csv", "--target-org",
         "torque-test-nonexistent"],
    ]
    bad = []
    for argv in agree:
        a = verdict({"tool_name": "SfArgv", "tool_input": {"argv": argv}})
        t = verdict({"tool_name": "Bash", "tool_input": {"command": _shx.join(["sf", *argv])}})
        if a != t:
            bad.append(f"{argv[:3]}: argv exit {a}, text exit {t}")

    # the one licensed disagreement, asserted in its direction
    odd = ["data", "query", "--query", "SELECT Id FROM A WHERE N=" + chr(96) + "x" + chr(96),
           "--target-org", "torque-test-nonexistent"]
    a = verdict({"tool_name": "SfArgv", "tool_input": {"argv": odd}})
    t = verdict({"tool_name": "Bash", "tool_input": {"command": _shx.join(["sf", *odd])}})
    if not (a == 0 and t == 2):
        bad.append(f"the licensed disagreement inverted or vanished: argv exit {a} "
                   f"(want 0, a read), text exit {t} (want 2, refused on punctuation)")
    if bad:
        return Result(name, FAIL, "; ".join(bad))
    return Result(name, PASS,
                  f"{len(agree)} shapes reach the same verdict by both roads; the one shape that "
                  f"differs is a value carrying a backtick, where the text road refuses and the "
                  f"argv road is right")
