# Two independent representations of one fact, compared by nobody.
#
# Three separate defects in the round-9 audit turned out to be one defect at three severities.
# P1-006: two detectors for "is this a Salesforce command", one deciding authorization and the
# other deciding what got logged, disagreeing. P1-009: the Python floor written 3.11 in one
# document and 3.8 in two others, with the enforcing code agreeing with neither document that
# claimed to describe it. And `runnable_implies_unwritable`'s own docstring, which had already
# written the lesson down: "a second list guarding one boundary is how this comes apart."
#
# What these share is not the subject matter. It is that a fact was written down twice, in two
# files, by two people (or by the same one on two days), and nothing in the build ever put the
# two copies side by side. Each copy stays plausible on its own. The divergence is invisible
# until someone reads both, which is the one thing nobody does.
#
# The fix is not vigilance. It is to make the comparison a check. These are the pairs where a
# divergence would be worst:
#
#   every_wired_hook_            settings.json says which hooks run; the gate predicate says
#     classifies_protected       which files the agent may not touch. Diverge and the agent can
#                                edit the hook that is deciding whether it may edit things.
#   catalogue_domains_reachable  the catalogue is organised by domain; the rule that routes the
#                                agent to it lists triggers naming domains. A domain in one and
#                                not the other is entries nobody is ever sent to read.
#
# Each comparison is a pure function over (claimed, actual) so `two_list_checks_can_fail` can
# hand it a divergence and require it to notice — because a consistency check that has never
# seen an inconsistency is the same unexamined second list one layer up.
#
# A third check sat here for about twenty minutes: rule_enforcement_resolves, comparing every
# "ENFORCEMENT: harness-enforced (name)" label against the registry. It was deleted on finding
# `enforcement_map` in check_p1_gates.py, which had been doing that since P1 and does it better
# — it requires the named hook to contain a deny path, so a rule cannot claim enforcement from
# an observer that can only ever exit 0. Writing a second copy of the comparison, inside the
# file whose entire subject is second copies of things, is the joke this defect keeps telling.
# The one thing that check lacked has been added to it in place: a hook can exist, can block,
# and still be wired to nothing.

import importlib.util as _2l_il
import sys as _2l_sys
import tempfile as _2l_tf

_2l_sys.path.insert(0, str(ROOT / "hooks"))
import lib as _2l_lib              # noqa: E402
import shellparse as _2l_sp        # noqa: E402

_2L_RULE_DIR = ROOT / ".claude" / "rules"
_2L_SETTINGS = ROOT / ".claude" / "settings.json"
_2L_CATALOGUE = ROOT / "knowledge" / "salesforce-platform.yml"
_2L_QUIRKS = _2L_RULE_DIR / "platform-quirks.md"

_2L_ENFORCE = re.compile(r"^[ \t]*ENFORCEMENT:[ \t]*(\S+)[ \t]*\((.+?)\)[ \t]*$", re.M)

# Every spelling of a path that resolves to the same file. The basename half of the predicate
# sees only the last component; the resolve half sees all of them. A hook protected by exactly
# one of the two halves is protected — until someone narrows that half.
_2L_SPELLINGS = ("hooks/{n}", "./hooks/{n}", "{root}/hooks/{n}",
                 "hooks/../hooks/{n}", "hooks/./{n}", "{root}/hooks/../hooks/{n}")


# ---- the pure comparisons -------------------------------------------------------------

def _2l_shadowed(fname, bound, owners, provided):
    """PURE. Which of this file's bindings collide with a name another file bound differently?

    `owners` is mutated as files are walked, name -> (file, what). Two files binding one name to
    the SAME thing (the same module under the same alias) is not a collision; two files binding
    it to different things is, and the loader resolves that silently in load order.
    """
    out = []
    for b, what in bound.items():
        if b in provided:
            continue
        prev = owners.get(b)
        if prev and prev[0] != fname and prev[1] != what:
            out.append(f"{b!r}: {prev[1]} in {prev[0]}, {what} in {fname}")
        owners.setdefault(b, (fname, what))
    return out


def _2l_unprotected(paths, is_protected):
    """PURE. Which of these paths does the gate predicate leave writable?"""
    return [p for p in paths if not is_protected(p)]


def _2l_domain_gaps(catalogue_domains, cited_domains):
    """PURE. Domains present in one list and absent from the other, both directions.

    Unreachable is the expensive one: entries that exist, are correct, cost work to write, and
    are never consulted, because the document that tells the agent when to consult the catalogue
    does not mention the word they are filed under. Phantom is the cheaper one: the rule sends
    the agent to a domain with nothing in it, and an empty grep result reads as "no known
    problem here", which is this repository's most-repeated defect.
    """
    unreachable = sorted(d for d in catalogue_domains if d not in cited_domains)
    phantom = sorted(d for d in cited_domains if d not in catalogue_domains)
    return unreachable, phantom


# ---- reading the two sides ------------------------------------------------------------

def _2l_wired():
    """Hook basenames wired in settings.json, without assuming its schema shape."""
    if not _2L_SETTINGS.exists():
        return set()
    return set(re.findall(r"hooks/([A-Za-z_][A-Za-z0-9_]*)\.py", _2L_SETTINGS.read_text()))


def _2l_is_protected(path):
    return (os.path.basename(path) in _2l_sp.PROTECTED_BASENAMES
            or bool(_2l_lib.is_protected_target(path)))


def _2l_catalogue_domains():
    """domain -> entry count, from the catalogue itself."""
    if not _2L_CATALOGUE.exists():
        return {}
    counts = {}
    for d in re.findall(r"^\s*domain:\s*(\S+)\s*$", _2L_CATALOGUE.read_text(), re.M):
        counts[d] = counts.get(d, 0) + 1
    return counts


def _2l_cited_domains():
    """Domains named in the rule's numbered trigger list, and nowhere else.

    Scoped to the trigger section on purpose. Backticked words appear all over the rule —
    `grep`, `verify`, confidence values — and a matcher that swept the whole file would report
    those as phantom domains, which trains the reader to ignore the check.
    """
    if not _2L_QUIRKS.exists():
        return set(), False
    text = _2L_QUIRKS.read_text()
    m = re.search(r"^##\s*When to consult it.*?$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    if not m:
        return set(), False
    body = m.group(1)
    triggers = re.findall(r"^\s*\d+\.\s+(.*(?:\n(?!\s*\d+\.).*)*)", body, re.M)
    cited = set()
    for t in triggers:
        cited.update(re.findall(r"`([a-z][a-z-]*)`", t))
    return cited, True


# ---- the checks -----------------------------------------------------------------------

@check("every_wired_hook_classifies_protected", "static", catastrophe=True)
def _every_wired_hook_classifies_protected():
    """A hook the agent can edit is not a gate, it is a suggestion.

    PROTECTED_BASENAMES names four files in hooks/; six are on disk and four are wired. The two
    newest wired hooks are absent from that list and are protected only by the resolve-based
    half of the predicate. That is fine today and is exactly the arrangement that breaks
    quietly: narrow `is_protected_target`, and the two hooks added most recently — the ones
    least likely to be on anyone's mind — become writable while the four oldest stay safe, so
    every gate check still passes.

    Named for what it measures. It asks the gate's predicate whether each path is protected; it
    does NOT attempt a write, so it cannot tell you a write would be refused. An open maintainer
    window makes protected paths deliberately writable and leaves this check green, correctly —
    `runnable_implies_unwritable` is the one that measures the writability boundary, and it
    SKIPs while a window is open rather than reporting a boundary it did not test. Calling this
    one `wired_hooks_are_unwritable` was the first name, and it claimed the stronger of the two.
    """
    name = "every_wired_hook_classifies_protected"
    wired = sorted(_2l_wired())
    if not wired:
        return Result(name, FAIL, "settings.json names no hooks to check")
    paths = []
    for n in wired:
        if not (ROOT / "hooks" / f"{n}.py").exists():
            return Result(name, FAIL, f"settings.json wires hooks/{n}.py, which does not exist")
        paths += [s.format(n=f"{n}.py", root=str(ROOT)) for s in _2L_SPELLINGS]
    # settings.json decides which hooks run at all, so it belongs in the same boundary.
    paths.append(str(_2L_SETTINGS))
    holes = _2l_unprotected(paths, _2l_is_protected)
    if holes:
        return Result(name, FAIL,
                      f"{len(holes)} spelling(s) of a wired hook the gate does not classify as "
                      f"protected: {holes[:4]}")
    return Result(name, PASS,
                  f"{len(wired)} wired hooks classify protected under all {len(_2L_SPELLINGS)} "
                  f"path spellings, settings.json included (classification only — "
                  f"runnable_implies_unwritable measures the write boundary)")


@check("catalogue_domains_reachable", "static")
def _catalogue_domains_reachable():
    counts = _2l_catalogue_domains()
    if not counts:
        return Result("catalogue_domains_reachable", FAIL,
                      "read no domains out of the catalogue — an empty read is not an empty file")
    cited, found_section = _2l_cited_domains()
    if not found_section:
        return Result("catalogue_domains_reachable", FAIL,
                      "platform-quirks.md has no 'When to consult it' trigger section to compare "
                      "against; the rule cannot route to the catalogue at all")
    unreachable, phantom = _2l_domain_gaps(set(counts), cited)
    problems = []
    if unreachable:
        problems.append("no trigger names " + ", ".join(
            f"'{d}' ({counts[d]} entries)" for d in unreachable))
    if phantom:
        problems.append("triggers name " + ", ".join(f"'{d}'" for d in phantom)
                        + ", which no catalogue entry carries — the agent is sent to an empty grep")
    if problems:
        return Result("catalogue_domains_reachable", FAIL, "; ".join(problems))
    return Result("catalogue_domains_reachable", PASS,
                  f"all {len(counts)} catalogue domains ({sum(counts.values())} entries) are "
                  f"named by a trigger, and every trigger names a domain that has entries")


@check("two_list_checks_can_fail", "static")
def _two_list_checks_can_fail():
    """Hand each comparison a divergence and require it to say so.

    These checks exist because a second copy of a fact went unexamined. A consistency check
    nobody has shown an inconsistency to is that same second copy, one layer up.

    Each comparison gets both directions: it must report a divergence that is there, and stay
    quiet on two lists that agree. A tripwire that is always on gets ignored, and then it is
    not a tripwire.
    """
    survived = []

    # protection: a predicate that has stopped protecting anything.
    if len(_2l_unprotected(["hooks/lib.py", "hooks/x.py"], lambda p: False)) != 2:
        survived.append("unprotected() did not report paths a False predicate leaves open")
    if _2l_unprotected(["hooks/lib.py"], lambda p: True):
        survived.append("unprotected() reported a hole against a predicate that protects all")

    # plugin shadowing: the real collision, and the benign one it must not cry wolf about.
    owners = {"_REQUIRED": ("check_kb.py", "value:check_kb.py"),
              "_sp": ("check_kb.py", "import:subprocess")}
    if not _2l_shadowed("check_attest.py", {"_REQUIRED": "value:check_attest.py"},
                        dict(owners), set()):
        survived.append("shadowed() missed two files binding one name to different values")
    if _2l_shadowed("check_attest.py", {"_sp": "import:subprocess"}, dict(owners), set()):
        survived.append("shadowed() flagged the same module imported under the same alias")
    if _2l_shadowed("check_attest.py", {"ROOT": "value:check_attest.py"}, dict(owners), {"ROOT"}):
        survived.append("shadowed() flagged a name the loader itself provides")

    # domains: an entry nobody is routed to, and a trigger pointing at nothing.
    unreachable, phantom = _2l_domain_gaps({"a", "b"}, {"a", "c"})
    if unreachable != ["b"] or phantom != ["c"]:
        survived.append(f"domain_gaps() returned {unreachable}/{phantom}, expected ['b']/['c']")
    if any(_2l_domain_gaps({"a"}, {"a"})):
        survived.append("domain_gaps() reported a gap between two identical lists")

    if survived:
        return Result("two_list_checks_can_fail", FAIL, "; ".join(survived))
    return Result("two_list_checks_can_fail", PASS,
                  "all three comparisons report the divergence they are for and stay quiet on "
                  "lists that agree (7 cases)")


@check("needs_vocabulary_reaches_the_catalogue", "static")
def _needs_vocabulary_reaches_the_catalogue():
    """Every operation `torque needs` offers must still reach the catalogue.

    Third instance of the same pair in this file: a list of things the tool claims to know
    about, and the data it would have to consult to know them, with nothing comparing the two.
    An operation sitting in the menu whose exemplar has stopped matching any entry is a front
    door that opens onto nothing, and it reports that as "no catalogue entry matches this
    operation at all" — honest, and indistinguishable from a genuinely unmapped operation.

    The exemplars are CANONICAL COMMANDS on purpose, so they are matched by the triggers that
    already exist rather than by a second matcher. That is what this protects: the moment an
    exemplar stops being a real command, it has stopped reusing the catalogue.
    """
    name = "needs_vocabulary_reaches_the_catalogue"
    tool = ROOT / "bin" / "torque-needs"
    if not tool.exists():
        return Result(name, NA, "bin/torque-needs is not present")
    import importlib.machinery as _2l_mach
    loader = _2l_mach.SourceFileLoader("torque_needs_mod", str(tool))
    spec = _2l_il.spec_from_loader("torque_needs_mod", loader)
    mod = _2l_il.module_from_spec(spec)
    loader.exec_module(mod)

    silent, thin = [], []
    for op, (exemplar, _desc) in sorted(mod.OPERATIONS.items()):
        rep = _2l_lib.closure_report(exemplar)
        if not rep["matched"]:
            silent.append(f"{op} ({exemplar.split(' --')[0]})")
        elif not rep["requirements"]:
            thin.append(op)
    if silent:
        return Result(name, FAIL,
                      f"{len(silent)} operation(s) in the menu reach no catalogue entry at all, "
                      f"so asking about them returns nothing: {silent}")
    tail = (f"; {len(thin)} reach entries that record no requirement ({', '.join(thin)}), which "
            f"the tool reports as a catalogue gap rather than as nothing being required"
            if thin else "; every one returns at least one requirement")
    return Result(name, PASS,
                  f"all {len(mod.OPERATIONS)} offered operation(s) reach the catalogue{tail}")


@check("legacy_spelling_reaches_the_catalogue", "static")
def _legacy_spelling_reaches_the_catalogue():
    """The gate authorizes both CLI spellings. The catalogue must speak to both.

    `classify_destructive` pairs every modern shape with its `force:` twin, one `or` at a time,
    so a legacy bulk delete is gated exactly like a modern one. The catalogue's triggers were
    written against modern wording only, so the same command reached ZERO entries — measured at
    five operation pairs, legacy reached nothing five times out of five. Correctly refused, and
    told nothing about what it was doing.

    Two comparisons, because the defect has two halves. The map must not fall behind the
    classifier: any `force:` command ID the parser knows must be in `LEGACY_TO_MODERN`. And the
    map must actually work: for each pair, the legacy spelling must reach entries.

    Exact parity is NOT required, and pretending otherwise would make this a check that gets
    weakened rather than fixed. Some triggers match flag text (`--manifest`, `--sobject`) whose
    legacy spellings are single letters, so a small residual gap is expected. The gap is
    reported in the detail rather than hidden, so it is visible if it grows.
    """
    name = "legacy_spelling_reaches_the_catalogue"
    src = (ROOT / "hooks" / "shellparse.py").read_text()
    known = set(re.findall(r"force:[a-z]+:[a-z:]+", src))
    mapped = set(_2l_sp.LEGACY_TO_MODERN)
    missing = sorted(i for i in known
                     if i not in mapped and not any(k.startswith(i) for k in mapped))
    if missing:
        return Result(name, FAIL,
                      f"the parser knows {len(missing)} legacy command id(s) the map does not, "
                      f"so knowledge written for the modern spelling cannot reach them: "
                      f"{missing[:6]}")

    PAIRS = [
        ("bulk delete",
         "sf data delete bulk --sobject Log__c --file ids.csv --target-org dev",
         "sf force:data:bulk:delete -s Log__c -f ids.csv -u dev"),
        ("bulk upsert",
         "sf data upsert bulk --sobject Account --file r.csv --external-id Id --target-org dev",
         "sf force:data:bulk:upsert -s Account -f r.csv -i Id -u dev"),
        ("record create",
         "sf data create record --sobject Account --values Name=x --target-org dev",
         "sf force:data:record:create -s Account -v Name=x -u dev"),
        ("deploy",
         "sf project deploy start --manifest p.xml --target-org dev",
         "sf force:source:deploy -x p.xml -u dev"),
        ("anonymous apex",
         "sf apex run --file s.apex --target-org dev",
         "sf force:apex:execute -f s.apex -u dev"),
    ]
    silent, gaps = [], []
    for label, modern, legacy in PAIRS:
        m = _2l_lib.closure_report(modern)
        l = _2l_lib.closure_report(legacy)
        if m["matched"] and not l["matched"]:
            silent.append(f"{label}: modern reaches {len(m['matched'])}, legacy reaches nothing")
        elif len(l["matched"]) < len(m["matched"]):
            gaps.append(f"{label} {len(l['matched'])}/{len(m['matched'])}")
    if silent:
        return Result(name, FAIL, "; ".join(silent))
    tail = f"; residual flag-spelling gap on {', '.join(gaps)}" if gaps else "; full parity"
    return Result(name, PASS,
                  f"{len(mapped)} legacy ids mapped, covering every one the parser knows; all "
                  f"{len(PAIRS)} operation pairs reach the catalogue by either spelling{tail}")


@check("mutation_coverage_is_stated_honestly", "static")
def _mutation_coverage_is_stated_honestly():
    """`catastrophe=True` is a claim, and TOTAL_MUTATORS is the other half of it.

    The decorator flag and the mutator list are two representations of "this check has been
    shown to fail". They were never compared, and the harness's own module docstring said
    --self-test proved *each* catastrophe-class check can fail. Counted: 17 mutators against
    sixty-odd catastrophe-class checks. The claim was off by a factor of three and sat in the
    first paragraph of the file the whole design rests on.

    This does not demand a mutator per check — several checks carry their falsification inline
    instead, which runs in every profile rather than only under --self-test, and is the better
    arrangement. What it demands is that the prose never asserts universal coverage while the
    numbers say partial.
    """
    name = "mutation_coverage_is_stated_honestly"
    src = (ROOT / "harness" / "validate.py").read_text()
    m = re.search(r"TOTAL_MUTATORS\s*=\s*(\d+)", src)
    if not m:
        return Result(name, FAIL, "validate.py no longer declares TOTAL_MUTATORS")
    mutators = int(m.group(1))
    catastrophic = [n for n, _p, cat, _f in REGISTRY if cat]
    doc = (src.split('"""')[1] if '"""' in src else "")
    # The affirmative shape only: "proving each catastrophe-class check can fail". Matching a
    # bare "every ... catastrophe-class" also fires on the sentence that says coverage is NOT
    # universal, which is how this check first failed — against the correction that fixed the
    # very claim it was written to catch.
    universal = re.search(r"prov\w*[^.]{0,50}\b(each|every|all)\b[^.]{0,50}catastrophe-class",
                          doc, re.I | re.S)
    if universal and mutators < len(catastrophic):
        return Result(name, FAIL,
                      f"the harness docstring claims {' '.join(universal.group(0).split())!r} "
                      f"while TOTAL_MUTATORS is {mutators} and {len(catastrophic)} checks are "
                      f"catastrophe-class — the claim is true only at "
                      f"{mutators}/{len(catastrophic)}")
    return Result(name, PASS,
                  f"{mutators} mutators against {len(catastrophic)} catastrophe-class checks, "
                  f"and the harness docstring claims no universal coverage (searched for a "
                  f"'proving each/every/all catastrophe-class' assertion)")


@check("check_plugins_do_not_shadow_each_other", "static")
def _check_plugins_do_not_shadow_each_other():
    """Every check_*.py is exec'd into ONE namespace, so a repeated module-level name rebinds.

    Not hypothetical. check_attest.py was written with `_REQUIRED = ("schema", "verdict", ...)`
    and check_kb.py already used that name for catalogue entry fields. check_kb loads later
    alphabetically, so the attestation check ran against the catalogue's required fields and
    reported that every attestation was missing an id, a symptom and a remedy.

    That one announced itself. The dangerous direction is the other one: a name that shadows
    something a LATER-loading file only reads, where the shadowed check keeps running and
    quietly measures the wrong thing. Nothing about the loader makes this visible — the files
    look like modules and are not.

    Bare names in a shared namespace are the failure. The convention is a per-file prefix, and
    this is what enforces it.
    """
    name = "check_plugins_do_not_shadow_each_other"
    import ast as _2l_ast
    # Provided by the loader to every plugin; a plugin re-binding one of these is a different
    # (and worse) problem, caught by the collision rule below only if two files do it.
    PROVIDED = {"check", "Result", "sh", "ROOT", "CHECKS", "PASS", "FAIL", "WARN", "SKIP", "NA",
                "REGISTRY", "subprocess", "json", "os", "re", "Path"}
    owners = {}
    clashes = []
    for f in sorted((ROOT / "harness" / "checks").glob("check_*.py")):
        try:
            tree = _2l_ast.parse(f.read_text())
        except SyntaxError as e:
            return Result(name, FAIL, f"{f.name} does not parse ({e})")
        # Each binding carries WHAT it binds, not merely that it binds. Two files doing
        # `import subprocess as _sp` rebind the same alias to the same module and nothing
        # changes meaning — flagging that trains a reader to skim the finding. Two files
        # binding one name to different objects is the real defect, and it is what this
        # reports.
        bound = {}
        for node in tree.body:                     # module level only — locals cannot collide
            # Anything carrying a `.name` at module level defines that name: functions, async
            # functions, classes. Asking for the attribute rather than listing the three node
            # types covers all of them and cannot fall behind a future one.
            declared = getattr(node, "name", None)
            if isinstance(declared, str):
                bound[declared] = f"def:{f.name}"
            elif isinstance(node, _2l_ast.Assign):
                for t in node.targets:
                    if isinstance(t, _2l_ast.Name):
                        bound[t.id] = f"value:{f.name}"
            elif isinstance(node, _2l_ast.AnnAssign) and isinstance(node.target, _2l_ast.Name):
                bound[node.target.id] = f"value:{f.name}"
            elif isinstance(node, _2l_ast.Import):
                for a in node.names:
                    bound[(a.asname or a.name).split(".")[0]] = f"import:{a.name}"
            elif isinstance(node, _2l_ast.ImportFrom):
                for a in node.names:
                    bound[a.asname or a.name] = f"from:{node.module}.{a.name}"
        clashes += _2l_shadowed(f.name, bound, owners, PROVIDED)
    if clashes:
        return Result(name, FAIL,
                      f"{len(clashes)} module-level name(s) shared across check plugins, which "
                      f"the loader silently rebinds: {clashes[:6]}")
    return Result(name, PASS,
                  f"{len(owners)} module-level names across "
                  f"{len(list((ROOT / 'harness' / 'checks').glob('check_*.py')))} plugin files, "
                  f"each bound in exactly one of them")


def _2l_fake_tree(root, wire=("gate",)):
    """A minimal repo shaped like the parts enforcement_map reads."""
    (root / "hooks").mkdir(parents=True)
    (root / "hooks" / "gate.py").write_text("import lib\nlib.deny('x', 'y', 'z')\n")
    (root / ".claude" / "rules").mkdir(parents=True)
    (root / ".claude" / "rules" / "r.md").write_text(
        "# r\n\nENFORCEMENT: hook-enforced (gate)\n")
    entries = ", ".join('{"command": "hooks/%s.py"}' % w for w in wire)
    (root / ".claude" / "settings.json").write_text("{\"hooks\": [%s]}" % entries)


@check("enforcement_map_can_fail", "static")
def _enforcement_map_can_fail():
    """Show enforcement_map an unwired hook and require it to say so.

    The wiring requirement was added to that check on 2026-08-04, and a requirement nobody has
    watched reject anything is a requirement on paper. Proved by rebinding ROOT in the shared
    plugin namespace and pointing the check at a throwaway tree: no file in this repository is
    written, so an interrupted run cannot leave the rules or settings.json in a mutated state.
    That matters more here than elsewhere — settings.json is what wires the gates, and a
    self-test that could leave it half-edited would be disabling the gates to test them.
    """
    fn = dict((n, f) for n, _p, _c, f in REGISTRY).get("enforcement_map")
    if fn is None:
        return Result("enforcement_map_can_fail", FAIL,
                      "enforcement_map is not registered — the check this one proves is gone")
    g = fn.__globals__
    real_root = g["ROOT"]
    cases = []
    try:
        with _2l_tf.TemporaryDirectory() as td:
            base = Path(td)
            # baseline: claimed, able to deny, and wired. Must PASS, or the two negatives below
            # would be indistinguishable from a check that fails on everything.
            ok_root = base / "ok"
            _2l_fake_tree(ok_root, wire=("gate",))
            g["ROOT"] = ok_root
            r = fn()
            cases.append(("wired hook", r.outcome == PASS, r.outcome, r.detail))

            # the new half: same tree, settings.json wiring something else.
            bad_root = base / "unwired"
            _2l_fake_tree(bad_root, wire=("other",))
            g["ROOT"] = bad_root
            r = fn()
            cases.append(("unwired hook", r.outcome == FAIL and "wires nothing to it" in r.detail,
                          r.outcome, r.detail))

            # nothing wired at all: the whole map is false and the check must say that, not
            # quietly resolve zero labels against zero hooks.
            empty_root = base / "empty"
            _2l_fake_tree(empty_root, wire=())
            g["ROOT"] = empty_root
            r = fn()
            cases.append(("no hooks wired", r.outcome == FAIL and "wires no hooks" in r.detail,
                          r.outcome, r.detail))
    finally:
        g["ROOT"] = real_root
    bad = [f"{label}: got {out} ({detail[:70]})" for label, good, out, detail in cases if not good]
    if bad:
        return Result("enforcement_map_can_fail", FAIL, "; ".join(bad))
    return Result("enforcement_map_can_fail", PASS,
                  f"enforcement_map passes a wired hook and fails both unwired shapes "
                  f"({len(cases)} cases, against a throwaway tree)")
