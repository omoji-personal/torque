# Two ways Torque could be turned off or walked around, both found by adversarial audit of a
# PLAN rather than of the code — which is worth noting, because neither had a fixture and both
# were reachable.
#
#   1. `torque install-gates --remove-shim` / `--remove` was allowed through the agent's tool
#      surface. The gates denied `rm ~/.torque/shim/sf` as a protected write and permitted the
#      sanctioned uninstaller, which does the same thing more thoroughly. Two doors on the one
#      marked "disable", different answers.
#   2. A `destructiveChanges.xml` inside an MDAPI deploy DIRECTORY carried no destructive token.
#      The Metadata API honours it with no flag; the classifier only ever read argv.
import os as _bos
import pathlib as _bp
import subprocess as _bsp
import sys as _bsys
import tempfile as _btf


@check("uninstall_requires_an_operator", "static", catastrophe=True)
def _uninstall_requires_an_operator():
    """An agent that can uninstall its own guardrails has guardrails only while it agrees.

    Runs the real installer's remove paths and requires refusal with a stated reason.

    THE ASSUMPTION THIS USED TO MAKE, and why it was wrong. It said "this process is a descendant
    of the agent, so `lib.operator_present()` is false here exactly as it is for any tool call".
    That is true when an AGENT runs the harness and false when an OPERATOR does — and the release
    attestation is exactly the run an operator performs at their own terminal. The subprocess
    inherited a genuine login tty, uninstall succeeded because it was legitimately authorised,
    and the check reported the product broken. Backwards: it passed in the context that mattered
    least and failed in the one that mattered most, and had the polarity been reversed it would
    have reported PASS for years while never once testing the agent case.

    `start_new_session=True` is the fix rather than an env override. `_has_tty()` opens /dev/tty,
    so detaching the controlling terminal makes presence deterministically false whoever launched
    the harness — the same answer in CI, in an agent session, and on the operator's laptop.

    Both directions, because "refuse always" passes a refusal-only test. The acceptance arm runs
    in-process against a stubbed `lib`, since a login session cannot be forged on purpose (R10-07
    makes sure of that) and should not become forgeable to satisfy a check.

    NOTE the refusal arm runs the uninstaller for real, so it must be certain the refusal comes
    BEFORE any removal — which is why it also asserts the shim survives when one is installed.
    """
    torque = ROOT / "bin" / "torque"
    shim = _bp.Path(_bos.environ.get("TORQUE_ANCHOR",
                                     _bp.Path.home() / ".torque")) / "shim" / "sf"
    had_shim = shim.is_file()
    bad = []
    for flag in ("--remove-shim", "--remove"):
        r = _bsp.run([_bsys.executable, str(torque), "install-gates", flag],
                     capture_output=True, text=True, cwd=str(ROOT),
                     stdin=_bsp.DEVNULL, start_new_session=True)
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 2 or "operator" not in out.lower():
            bad.append(f"{flag}: exit {r.returncode}, said {out.strip()[:60]!r}")
    if had_shim and not shim.is_file():
        bad.append("the shim was REMOVED despite the refusal — the guard runs after the damage")

    # --- acceptance: with presence proven, the guard must RETURN rather than exit.
    accepted = _uninstall_guard_accepts_an_operator()
    if accepted is not True:
        bad.append(f"an operator is refused too: {accepted}")

    if bad:
        return Result("uninstall_requires_an_operator", FAIL,
                      "uninstalling enforcement is reachable from the agent's tool surface: "
                      + "; ".join(bad))
    return Result("uninstall_requires_an_operator", PASS,
                  "both remove paths refuse without operator presence and exit 2, and the guard "
                  "returns for a present operator rather than refusing everyone"
                  + (", and the installed shim survived the attempt" if had_shim else
                     " (no shim installed, so survival was not observable)"))


def _uninstall_guard_accepts_an_operator():
    """True if `_require_operator` returns when presence is proven, else a reason string.

    In-process with a stubbed `lib`, because `_require_operator` does its own `import lib` after
    putting hooks/ on the path — so seeding sys.modules is what it will find. A subprocess cannot
    be used here: proving the ACCEPT direction would mean forging a login session, which is the
    one thing the presence check exists to make impossible.
    """
    import importlib.util as _iu
    import types as _t
    saved = _bsys.modules.get("lib")
    stub = _t.ModuleType("lib")
    stub.operator_present = lambda: True
    stub.audit = lambda *a, **k: True
    _bsys.modules["lib"] = stub
    try:
        # SourceFileLoader explicitly: bin/torque-install-gates carries no .py suffix, so
        # spec_from_file_location cannot infer a loader and returns None.
        from importlib.machinery import SourceFileLoader as _SFL
        loader = _SFL("torque_install_gates_probe",
                      str(ROOT / "bin" / "torque-install-gates"))
        spec = _iu.spec_from_loader(loader.name, loader)
        mod = _iu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod._require_operator("--remove")       # must return; sys.exit(2) would raise SystemExit
        return True
    except SystemExit as e:
        return f"_require_operator exited {e.code} despite operator_present() being True"
    except Exception as e:                      # noqa: BLE001 — a broken probe is not a pass
        return f"acceptance arm could not run ({type(e).__name__}: {e}), so it was NOT measured"
    finally:
        if saved is not None:
            _bsys.modules["lib"] = saved
        else:
            _bsys.modules.pop("lib", None)


@check("deploy_directory_destructiveness_is_seen", "static", catastrophe=True)
def _deploy_directory_destructiveness_is_seen():
    """A destructiveChanges.xml on disk must charge a token even when argv never names it.

    Both directions asserted. A directory carrying the manifest must classify destructive; a
    clean directory, a path that does not resolve, and a RETRIEVE must not — because a gate that
    charges a token for every deploy is the gate people uninstall, and this file exists partly
    because the last over-block cost 854 denials.
    """
    base = _bp.Path(_btf.mkdtemp(prefix="torque-mdapi-check-"))
    carrying, clean = base / "carrying", base / "clean"
    for d in (carrying, clean):
        d.mkdir()
        (d / "package.xml").write_text("<Package/>")
    (carrying / "destructiveChanges.xml").write_text("<Package/>")

    _bsys.path.insert(0, str(ROOT / "hooks"))
    # Popping these forces a fresh import so this check reads the CURRENT source rather than a
    # copy some earlier check left in memory. What it must not do is walk away having emptied a
    # slot other checks depend on: `cache_poison_resistant` holds a reference to `lib` and calls
    # importlib.reload on it, which requires the name still be registered. It was not, so that
    # check raised "module lib not in sys.modules" — and only in a full run, because in isolation
    # nothing had popped it. A check that mutates interpreter-global state restores it.
    _popped = {m: _bsys.modules.pop(m, None) for m in ("shellparse", "lib")}
    import shellparse

    cases = [
        ("flag form", ["project", "deploy", "start", "--manifest", "p.xml",
                       "--pre-destructive-changes", "d.xml", "-o", "x"], True),
        ("MDAPI dir carrying it", ["project", "deploy", "start", "--metadata-dir",
                                   str(carrying), "-o", "x"], True),
        ("glued flag", ["project", "deploy", "start", f"--metadata-dir={carrying}",
                        "-o", "x"], True),
        ("source-dir carrying it", ["project", "deploy", "start", "--source-dir",
                                    str(carrying), "-o", "x"], True),
        ("legacy mdapi:deploy", ["force:mdapi:deploy", "--deploydir", str(carrying),
                                 "-o", "x"], True),
        ("CLEAN dir", ["project", "deploy", "start", "--metadata-dir", str(clean),
                       "-o", "x"], False),
        ("path that does not resolve", ["project", "deploy", "start", "--metadata-dir",
                                        str(base / "nope"), "-o", "x"], False),
        ("retrieve is not a deploy", ["project", "retrieve", "start", "--metadata-dir",
                                      str(carrying), "-o", "x"], False),
    ]
    wrong = []
    for name, argv, want in cases:
        got = shellparse.classify_destructive(argv) == "destructive-metadata"
        if got != want:
            wrong.append(f"{name}: got {'destructive' if got else 'not destructive'}")
    import shutil as _bsh
    _bsh.rmtree(base, ignore_errors=True)
    for _m, _prev in _popped.items():                  # put back what we borrowed; see above
        if _prev is not None:
            _bsys.modules[_m] = _prev

    if wrong:
        return Result("deploy_directory_destructiveness_is_seen", FAIL,
                      "the classifier misreads a deploy directory: " + "; ".join(wrong))
    return Result("deploy_directory_destructiveness_is_seen", PASS,
                  f"{len(cases)} cases: a destructiveChanges manifest on disk charges a token "
                  f"through 5 flag spellings, and a clean directory, an unresolvable path and a "
                  f"retrieve do not")
