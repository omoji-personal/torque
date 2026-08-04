# The per-check org exemption: is the label true, and does the gate honour exactly it?
#
# validate.py lost its read-only exemption when P1-002 closed, correctly — probe_cycle deploys
# and hard-deletes metadata, so the harness IS an org-mutating tool. That was true of the harness
# and false of most of its checks, and the cost was real: diagnosing any live failure meant the
# operator running a whole profile and pasting it back.
#
# The exemption is now per check. That widens the agent's reach, so it gets three separate
# guards, because each covers a different way the arrangement could quietly stop being true:
#
#   readonly_declaration_survives_source   the label must survive re-derivation from source
#   readonly_manifest_matches_declarations the file the gate reads must equal the declarations
#   readonly_gate_honours_exactly_the_list the gate must permit those and refuse everything else
#
# A declaration alone is trusting a name. A derivation alone is trusting an incomplete static
# analysis to prove a negative. The manifest alone is trusting a file the agent might edit. Only
# all three together say anything.
import ast as _ro_ast
import importlib.util as _ro_il
import json as _ro_json
import subprocess as _ro_sp
import sys as _ro_sys

_RO_MANIFEST = ROOT / "harness" / "checks" / "read-only-checks.json"
_RO_VERBS = {"data", "org", "project", "apex", "sobject", "schema", "package", "limits",
             "alias", "config", "force", "lightning"}
# tools that reach an org destructively or mint authorization; naming one is disqualifying
_RO_MUTATING_TOOLS = ("torque-shadow", "torque-approve", "torque-init", "torque-attest")


def _ro_declared():
    spec = _ro_il.spec_from_file_location("tv_ro", ROOT / "harness" / "validate.py")
    v = _ro_il.module_from_spec(spec)
    spec.loader.exec_module(v)
    return {n for n, ro in v.READS_ONLY.items() if ro}


def _ro_manifest():
    return set(_ro_json.loads(_RO_MANIFEST.read_text())["checks"])


def _ro_evidence_of_writing(fnnode, sp):
    """Anything in this check's source that reads as an org mutation. Fail-closed by design:
    a false positive costs an exemption, a false negative costs somebody's org."""
    found = []
    for n in _ro_ast.walk(fnnode):
        items = n.elts if isinstance(n, (_ro_ast.List, _ro_ast.Tuple)) else (
            n.args if isinstance(n, _ro_ast.Call) else None)
        if items is None:
            continue
        run = []
        for el in items:
            if isinstance(el, _ro_ast.Constant) and isinstance(el.value, str):
                run.append(el.value)
            else:
                break
        if run and run[0] == "sf":
            run = run[1:]
        if len(run) >= 2 and run[0] in _RO_VERBS and not sp.is_read(run):
            found.append("argv: sf " + " ".join(run[:3]))
    for n in _ro_ast.walk(fnnode):
        if not (isinstance(n, _ro_ast.Constant) and isinstance(n.value, str)):
            continue
        s = n.value
        if any(t in s for t in _RO_MUTATING_TOOLS):
            found.append(f"names {[t for t in _RO_MUTATING_TOOLS if t in s][0]}")
        if "sf" not in s:
            continue
        try:
            for w in (sp.analyze_bash(s).get("writes") or []):
                found.append("builds: sf " + " ".join(list(w)[:3]))
        except Exception:                                  # noqa: BLE001
            continue
    return sorted(set(found))


@check("experiments_are_not_checks", "static")
def _experiments_are_not_checks():
    """Nothing under harness/experiments/ may register a check or run in a profile.

    An experiment establishes a claim nobody has measured yet; a check asserts one that is
    settled. Letting the first quietly become the second is how an unverified org-touching probe
    ends up deciding a build — and `torque done` shipped with two dead layers precisely because
    nothing had run it against an org. The loader globs `harness/checks/check_*.py`, so this
    separation holds by construction today; the point of asserting it is that a future
    convenience — a symlink, a wider glob, an experiment renamed into checks/ — would erase it
    without anyone noticing.
    """
    name = "experiments_are_not_checks"
    d = ROOT / "harness" / "experiments"
    if not d.is_dir():
        return Result(name, NA, "no experiments directory")
    # Every experiment, not only the Python ones. The glob was `*.py`, so a probe written in any
    # other language was neither inspected nor counted — and the PASS line said "1 experiment(s)"
    # while two sat in the directory. A count that understates is the same defect as a claim that
    # overstates; it just fails in the flattering direction.
    files = [p for p in sorted(d.rglob("*")) if p.is_file() and p.suffix in (".py", ".mjs", ".js",
                                                                            ".sh", ".ts")]
    if not files:
        return Result(name, NA, "no experiments present")
    bad = []
    registered = {n for n, _p, _c, _f in REGISTRY}
    for f in files:
        src = f.read_text()
        if "@check(" in src:
            bad.append(f"{f.name} registers a check")
        for m in re.finditer(r"@check\(\s*[\"']([^\"']+)", src):
            if m.group(1) in registered:
                bad.append(f"{f.name} registered {m.group(1)!r} into the live registry")
        # the loader's own glob is what keeps them out; say so if the name would match it
        if f.parent == d and f.name.startswith("check_"):
            bad.append(f"{f.name} is named so a wider glob would pick it up")
    if bad:
        return Result(name, FAIL, "; ".join(bad))
    return Result(name, PASS,
                  f"{len(files)} experiment(s) register no checks and cannot be loaded as one")


@check("readonly_declaration_survives_source", "static", catastrophe=True)
def _readonly_declaration_survives_source():
    """Every check declared read-only must show no org mutation when re-derived from its source.

    This is the guard that matters. `reads_only=True` is a sentence a person wrote, and a check
    that gains an `sf project deploy` next year keeps the sentence. Re-derivation is what turns
    the label from an assertion into a claim something is checking.
    """
    name = "readonly_declaration_survives_source"
    import importlib.util as _il2
    spec = _il2.spec_from_file_location("torque_sp_ro", ROOT / "hooks" / "shellparse.py")
    sp = _il2.module_from_spec(spec)
    spec.loader.exec_module(sp)

    declared = _ro_declared()
    if not declared:
        return Result(name, NA, "no check is declared read-only, so nothing is exempt")
    seen, bad = set(), []
    for f in sorted((ROOT / "harness" / "checks").glob("check_*.py")) + \
            [ROOT / "harness" / "validate.py"]:
        tree = _ro_ast.parse(f.read_text())
        for node in _ro_ast.walk(tree):
            if not isinstance(node, _ro_ast.FunctionDef):
                continue
            decs = [d for d in node.decorator_list
                    if isinstance(d, _ro_ast.Call) and getattr(d.func, "id", "") == "check"]
            if not decs or not decs[0].args:
                continue
            cname = decs[0].args[0].value
            if cname not in declared:
                continue
            seen.add(cname)
            ev = _ro_evidence_of_writing(node, sp)
            if ev:
                bad.append(f"{cname} is declared read-only, and its source shows {ev[:3]}")
    missing = declared - seen
    if missing:
        return Result(name, FAIL,
                      f"declared read-only but not found in any source file: {sorted(missing)} — "
                      f"an exemption for a check nobody can locate is an exemption nobody can "
                      f"audit")
    if bad:
        return Result(name, FAIL, "; ".join(bad))
    return Result(name, PASS,
                  f"all {len(declared)} declared read-only check(s) re-derive clean from source "
                  f"({', '.join(sorted(declared))})")


@check("readonly_manifest_matches_declarations", "static", catastrophe=True)
def _readonly_manifest_matches_declarations():
    """The file the gate reads must equal the declarations, exactly.

    Two representations of one fact, and the gate trusts the copy it can read rather than the
    one a person wrote. Regenerate with `python3 scripts/gen-readonly-manifest.py`.
    """
    name = "readonly_manifest_matches_declarations"
    if not _RO_MANIFEST.exists():
        return Result(name, FAIL,
                      "harness/checks/read-only-checks.json is missing, so the gate falls back "
                      "to refusing everything. Safe, and not what the declarations say")
    try:
        manifest = _ro_manifest()
    except Exception as e:                                 # noqa: BLE001
        return Result(name, FAIL, f"the manifest does not parse ({type(e).__name__})")
    declared = _ro_declared()
    if manifest != declared:
        return Result(name, FAIL,
                      f"the manifest and the declarations disagree — only in manifest: "
                      f"{sorted(manifest - declared)}, only declared: "
                      f"{sorted(declared - manifest)}. Regenerate with "
                      f"scripts/gen-readonly-manifest.py")
    return Result(name, PASS,
                  f"the manifest lists exactly the {len(declared)} declared read-only check(s)")


@check("readonly_gate_honours_exactly_the_list", "static", catastrophe=True)
def _readonly_gate_honours_exactly_the_list():
    """The gate must permit the listed checks against an org and refuse every other shape.

    Parsed, not pattern-matched. The failure worth designing against is a permitted name
    appearing anywhere in the argv and carrying the rest of it through — a second `--only`, a
    `--self-test` riding alongside, a bare `--only` with no value.
    """
    name = "readonly_gate_honours_exactly_the_list"
    gate = ROOT / "hooks" / "prod_write_gate.py"
    declared = sorted(_ro_declared())
    if not declared:
        return Result(name, NA, "nothing declared read-only, so there is nothing to honour")

    def verdict(cmd):
        ev = _ro_json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
        return _ro_sp.run([_ro_sys.executable, str(gate)], input=ev, capture_output=True,
                          text=True, cwd=str(ROOT), timeout=120).returncode

    org = "torque-readonly-check-org"
    bad = []
    for c in declared:
        rc = verdict(f"python3 harness/validate.py --only {c} --target-org {org}")
        if rc != 0:
            bad.append(f"{c} is declared read-only and the gate refused it (exit {rc})")
    refuse = [
        (f"python3 harness/validate.py --profile capability --target-org {org}",
         "a whole profile"),
        (f"python3 harness/validate.py --only probe_cycle --target-org {org}",
         "a check that deploys"),
        (f"python3 harness/validate.py --only {declared[0]} --only probe_cycle "
         f"--target-org {org}", "two --only flags, one of them permitted"),
        (f"python3 harness/validate.py --only --target-org {org}", "--only with no value"),
        (f"python3 harness/validate.py --self-test --only {declared[0]} --target-org {org}",
         "--self-test riding alongside a permitted check"),
        (f"python3 harness/validate.py --only {declared[0]} "
         f"--allow-skip=x:y --target-org {org}", "--allow-skip, which degrades a run"),
        (f"python3 harness/validate.py --only nonexistent_check --target-org {org}",
         "a check that does not exist"),
    ]
    for cmd, label in refuse:
        if verdict(cmd) != 2:
            bad.append(f"the gate ALLOWED {label}")
    if bad:
        return Result(name, FAIL, "; ".join(bad))
    return Result(name, PASS,
                  f"the gate runs all {len(declared)} listed check(s) against an org and refuses "
                  f"{len(refuse)} other shape(s), including a permitted name carried alongside "
                  f"a forbidden one")
