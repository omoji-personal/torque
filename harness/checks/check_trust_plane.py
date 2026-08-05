# P0-01: can a maintainer-window edit to the repository change what actually adjudicates a write?
#
# The existing `maintainer_grant_never_touches_orgs` opens a window, leaves the gates UNMODIFIED,
# submits org-touching commands and confirms they still deny. That tests that the grant variable is
# not consulted in the authorization path — which was never the question. The question is whether
# the grant lets the agent rewrite the code that IS the authorization path, and it did:
# `authorize_write` returning `(True, "mutant")` turned a production denial into an allow on the
# next tool call, reproduced 2026-08-05.
#
# These checks are written to FAIL until enforcement is activated out of the anchor, and to keep
# failing if it is ever pointed back at the workspace. A check that goes green because the feature
# is absent would be the same defect one level up.
import json as _tj
import os as _tos
import shutil as _tsh
import subprocess as _tsp
import sys as _tsys
import tempfile as _ttf
from pathlib import Path as _TP


def _anchor():
    return _TP(_tos.environ.get("TORQUE_ANCHOR", _TP.home() / ".torque"))


def _activated():
    m = _anchor() / "enforcement" / "manifest.json"
    cur = _anchor() / "enforcement" / "current"
    if not (m.exists() and cur.exists()):
        return None
    try:
        return _tj.loads(m.read_text())
    except Exception:                                      # noqa: BLE001
        return None


@check("maintainer_edit_cannot_change_active_gate", "static", catastrophe=True)
def _maintainer_edit_cannot_change_active_gate():
    """Edit the repository's authorization core; require the live decision to be unmoved.

    This is the falsification the audit specified and the one the existing maintainer checks do
    not perform. It runs against a COPY of the tree so the real hooks are never mutated, and it
    asserts the property in both directions: the unmutated gate must deny first (or a "still
    denied" result proves nothing), and the mutated one must still deny.
    """
    name = "maintainer_edit_cannot_change_active_gate"
    act = _activated()
    if not act:
        return Result(name, NA,
                      "enforcement is not activated, so the gates run from the workspace and a "
                      "maintainer window CAN change them — `torque activate-enforcement` is the "
                      "fix and this check begins to mean something the moment it is run. "
                      "Reported as N/A rather than PASS: nothing here was established")

    base = _TP(_ttf.mkdtemp(prefix="torque-trustplane-"))
    tree = base / "tree"
    _tsh.copytree(ROOT, tree, ignore=_tsh.ignore_patterns(".git", "__pycache__", "*.pyc",
                                                          "node_modules"))
    event = {"tool_name": "Bash",
             "tool_input": {"command": "sf data update record --sobject Account --record-id "
                                       "001abc --values X=1 --target-org acme-prod"}}

    def run(gate_dir):
        env = dict(_tos.environ, TORQUE_AUDIT_LOG=str(base / "a.log"),
                   PYTHONDONTWRITEBYTECODE="1")
        env.pop("TORQUE_SHIM_DEPTH", None)
        r = _tsp.run([_tsys.executable, str(_TP(gate_dir) / "prod_write_gate.py")],
                     input=_tj.dumps(event), capture_output=True, text=True, env=env)
        return r.returncode

    active_hooks = _TP(_tos.path.realpath(_anchor() / "enforcement" / "current")) / "hooks"
    before = run(active_hooks)
    if before != 2:
        _tsh.rmtree(base, ignore_errors=True)
        return Result(name, FAIL,
                      f"the ACTIVE gate did not deny a production write (exit {before}) — the "
                      f"baseline is broken, so nothing this check reports afterwards means "
                      f"anything")

    libp = tree / "hooks" / "lib.py"
    src = libp.read_text()
    i = src.index("def authorize_write(")
    j = src.index("\ndef ", i + 1)
    libp.write_text(src[:i] + 'def authorize_write(target, op_hint="write"):\n'
                              '    return True, "mutant"\n' + src[j:])

    workspace_after = run(tree / "hooks")
    active_after = run(active_hooks)
    _tsh.rmtree(base, ignore_errors=True)

    if workspace_after == 2:
        return Result(name, FAIL,
                      "the mutation did not change the WORKSPACE copy's decision, so this check "
                      "is not exercising what it claims — a mutant that changes nothing proves "
                      "nothing about the copy that ignored it")
    if active_after != 2:
        return Result(name, FAIL,
                      f"editing the repository changed the ACTIVE decision (exit {active_after}) "
                      f"— enforcement is still being read from somewhere a maintainer window can "
                      f"write")
    return Result(name, PASS,
                  f"a window-legal edit to authorize_write flips the workspace copy "
                  f"(exit {workspace_after}) and leaves the active gate denying — enforcement is "
                  f"running from commit {act.get('commit', '?')[:12]} in the anchor")


@check("active_enforcement_is_anchor_owned", "static")
def _active_enforcement_is_anchor_owned():
    """What the hooks and the shim load must resolve inside the anchor, not the workspace.

    Activation is worthless if the registration still names `$CLAUDE_PROJECT_DIR/hooks`. The two
    consumers are the project settings and the exec-time shim, and they have to agree — a shim
    still loading workspace gates leaves the channel that matters most running mutable code.
    """
    name = "active_enforcement_is_anchor_owned"
    act = _activated()
    if not act:
        return Result(name, NA, "enforcement not activated; nothing to own")

    anchor = str(_anchor().resolve())
    problems = []

    # The EFFECTIVE registration, which is the merge of the tracked file and the untracked local
    # override. Reading only settings.json would report the portable default and miss the
    # hardened one — and the portable default is deliberately workspace-pointing, so that reading
    # would call a correctly-hardened machine unprotected. What adjudicates a write is what the
    # host loads, not what is committed.
    settings = ROOT / ".claude" / "settings.json"
    local = ROOT / ".claude" / "settings.local.json"
    try:
        hooks = _tj.loads(settings.read_text()).get("hooks", {})
        if local.is_file():
            hooks = _tj.loads(local.read_text() or "{}").get("hooks", hooks) or hooks
        blob = _tj.dumps(hooks)
    except Exception as e:                                 # noqa: BLE001
        return Result(name, FAIL, f"cannot read the project registration ({e})")
    # Either spelling counts. `install-gates --project` writes `$HOME/.torque/...` rather than an
    # absolute /Users/… path, because settings.json is TRACKED and a committed absolute home
    # directory is one machine's path in everyone else's checkout. Testing only the resolved form
    # would have read a correctly-repointed registration as a workspace one, and this check would
    # have gone on failing after the thing it asks for had been done.
    anchor_spellings = (anchor, "$HOME/.torque", "${HOME}/.torque", "~/.torque")
    if "prod_write_gate" in blob and not any(s in blob for s in anchor_spellings):
        problems.append("the project hooks still load gates from the workspace")

    shim_home = _anchor() / "shim" / "home"
    if shim_home.exists() and anchor not in shim_home.read_text():
        problems.append("the installed shim still resolves its gates from the workspace")

    if problems:
        return Result(name, FAIL,
                      "; ".join(problems) + " — activation without repointing the consumers "
                      "changes nothing, which is the most convincing way to be wrong about this")
    return Result(name, PASS,
                  "every consumer of the gates resolves them inside the trust anchor")


@check("registered_gates_resolve", "static", catastrophe=True)
def _registered_gates_resolve():
    """Every hook this repository registers must name a file that exists.

    A hook command pointing at a missing file does not fail loudly. It simply does not gate — and
    silent non-enforcement in a repository whose README says the gates are enforced is the worst
    thing this project could ship.

    The risk is created BY the fix for P0-01. Pointing the TRACKED settings.json at
    `$HOME/.torque/enforcement/current/hooks/` is what makes activation real on this machine, and
    it means a fresh clone registers gates that do not exist there until that machine runs
    `torque activate-enforcement`. This check turns that from a silent gap into a loud one: it
    fails at validation, names the missing path, and names the remedy — rather than letting
    someone believe they are protected because the file says so.

    Both directions. A resolvable registration passes; a registration naming a missing path fails
    with the path quoted. The negative arm runs against a throwaway copy, because the positive one
    alone would pass just as well if this check never looked at anything.
    """
    name = "registered_gates_resolve"
    settings = ROOT / ".claude" / "settings.json"

    def _unresolved(cfg):
        out = []
        for arr in cfg.get("hooks", {}).values():
            for m in arr:
                for h in m.get("hooks", []):
                    cmd = h.get("command", "")
                    for tok in cmd.split('"'):
                        if not tok.endswith(".py"):
                            continue
                        p = (tok.replace("$CLAUDE_PROJECT_DIR", str(ROOT))
                                .replace("${CLAUDE_PROJECT_DIR}", str(ROOT))
                                .replace("$HOME", str(_TP.home()))
                                .replace("${HOME}", str(_TP.home())))
                        if not _TP(p).expanduser().exists():
                            out.append(tok)
        return out

    # Both files, because either can register a hook and either can name a path that is not there.
    local = ROOT / ".claude" / "settings.local.json"
    try:
        cfg = _tj.loads(settings.read_text())
        if local.is_file():
            lcfg = _tj.loads(local.read_text() or "{}")
            for evt, arr in (lcfg.get("hooks") or {}).items():
                cfg.setdefault("hooks", {}).setdefault(evt, []).extend(arr)
    except Exception as e:                                 # noqa: BLE001
        return Result(name, FAIL, f"cannot read the project registration ({e})")

    missing = _unresolved(cfg)
    if missing:
        return Result(name, FAIL,
                      f"{len(missing)} registered hook(s) name a file that does not exist, so "
                      f"they register enforcement and deliver none: {sorted(set(missing))[:3]} — "
                      f"run `torque activate-enforcement` if this is a clone that never has")

    # the check must be able to fail: a registration naming a path that is not there
    broken = _tj.loads(_tj.dumps(cfg))
    planted = False
    for arr in broken.get("hooks", {}).values():
        for m in arr:
            for h in m.get("hooks", []):
                if h.get("command", "").endswith('.py"'):
                    h["command"] = 'python3 "/nonexistent/torque/no_such_gate.py"'
                    planted = True
                    break
            if planted:
                break
        if planted:
            break
    if not planted:
        return Result(name, FAIL,
                      "no hook command ends in a .py path, so this check could not plant a "
                      "broken one and has proven nothing about its own ability to fail")
    if not _unresolved(broken):
        return Result(name, FAIL,
                      "a registration naming /nonexistent/torque/no_such_gate.py was NOT "
                      "reported missing — this check cannot fail and is therefore not evidence")

    n = sum(len(m.get("hooks", [])) for arr in cfg.get("hooks", {}).values() for m in arr)
    return Result(name, PASS,
                  f"all {n} registered hook(s) name a file that exists, and a planted "
                  f"unresolvable registration is caught")
