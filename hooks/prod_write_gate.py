#!/usr/bin/env python3
"""PreToolUse gate: authorize Salesforce writes by IDENTITY; protect the trust anchor.

Design (hardened after full-panel adversarial audit, 2026-07-31):

  DEFAULT-DENY BY PARSE, not allowlist-by-regex. A determined agent can spell `sf` a
  dozen ways that a `\\bsf\\b` regex misses (`sf""`, `s\\f`, `'sf'`, `force:data:*`,
  `-u` instead of `--target-org`). So this gate does NOT pattern-match the raw string.
  It splits the command into simple commands (respecting quotes + shell operators),
  tokenizes each with shlex, resolves the real argv0 through wrapper prefixes, and:

    - an `sf`/`sfdx` invocation whose subcommand is not a KNOWN READ is treated as a
      write: it must carry exactly one target that is on the allowlist AND classifies
      non-production live, else DENY;
    - a read carries no such requirement;
    - an inline interpreter payload (`bash -c`, `python -c`, `xargs sf …`) that carries
      an `sf` token is DENIED — its runtime target cannot be authorized at hook time;
    - any token that references the trust anchor (~/.torque) is DENIED — the agent's
      Bash tool never legitimately reads the signing secret or writes a token file;
    - a write-shape (`>`, `>>`, `tee`, `cp`, `mv`, `dd`, `sed -i`, `ln`, `install`,
      `truncate`) whose target resolves to a protected path is DENIED;
    - anything unparseable (unbalanced quotes, command substitution wrapping an `sf`
      token) fails CLOSED.

  SCOPE (honest): this gate governs Salesforce writes the agent issues *directly* as a
  Bash/MCP tool call, and the trust-anchor + protected-file surface. It cannot see `sf`
  spawned as a subprocess of a script the agent writes and runs — that, and a same-uid
  actor who already holds live production credentials, are Layer-0 (credentials / PATH)
  territory, documented in guide/TORQUE-GUIDE.md. The v2 PATH-shim closes the subprocess
  channel; v1 raises the direct-call bar as high as a text hook honestly can.
"""
import os, re, shlex, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib
from pathlib import Path

HOOK = "prod_write_gate"

# sf/sfdx subcommands that only READ (no org authorization required). Everything not
# here is treated as a write and must resolve to an authorized org — the safe default.
SF_READS = {
    ("data", "query"), ("data", "search"), ("data", "export"), ("data", "resume"),
    ("sobject", "describe"), ("sobject", "list"),
    ("schema",), ("limits",), ("doctor",), ("version",), ("help",), ("which",),
    ("org", "display"), ("org", "list"), ("org", "open"), ("org", "login"),
    ("org", "logout"),
    ("apex", "list"), ("apex", "tail"), ("apex", "get"),
    ("project", "retrieve"), ("project", "generate"), ("project", "convert"),
    ("package", "installed"), ("package", "version"), ("package", "list"),
    ("config", "get"), ("config", "list"), ("alias",), ("autocomplete",),
    ("info",), ("plugins",), ("community",), ("agent", "preview"),
}
# colon-form (force:*) tokens that only READ
COLON_READ = ("force:schema", "force:org:display", "force:org:list",
              "force:org:open", "force:auth", "force:data:soql:query",
              "force:data:record:get", "force:data:tree:export",
              "force:mdapi:retrieve", "force:source:retrieve", "force:package:installed")

TARGET_FLAGS = {"--target-org", "-o", "--targetusername", "-u"}
WRAPPER_PREFIX = {"env", "command", "nohup", "nice", "ionice", "time", "sudo",
                  "stdbuf", "setsid", "doas", "builtin", "exec"}
INTERPRETERS = {"bash", "sh", "zsh", "dash", "ksh", "fish", "eval",
                "python", "python2", "python3", "perl", "ruby", "node", "deno",
                "xargs", "parallel"}
WRITE_SHAPE_CMDS = {"cp", "mv", "dd", "tee", "ln", "install", "truncate", "rsync"}
PERM_CMDS = {"chmod", "chown", "chgrp", "chflags", "rm", "rmdir", "unlink", "shred"}
REDIR_RE = re.compile(r"^&?\d*>>?\|?$")           # >  >>  1>  2>>  &>  >|
SF_WORD = re.compile(r"(?:^|[\s'\";|&(`])s\s*f(?:dx)?(?:[\s'\";|&)`]|$)", re.I)


def _split_segments(cmd: str):
    """Split a shell line into simple-command strings on ; && || | & and newline,
    honoring single/double quotes and backslash. Returns (segments, ok)."""
    segs, buf = [], []
    i, n, quote = 0, len(cmd), None
    while i < n:
        c = cmd[i]
        if quote:
            buf.append(c)
            if quote == '"' and c == "\\" and i + 1 < n:
                buf.append(cmd[i + 1]); i += 2; continue
            if c == quote:
                quote = None
            i += 1; continue
        if c in ("'", '"'):
            quote = c; buf.append(c); i += 1; continue
        if c == "\\" and i + 1 < n:
            buf.append(c); buf.append(cmd[i + 1]); i += 2; continue
        if cmd[i:i + 2] in ("&&", "||"):
            segs.append("".join(buf)); buf = []; i += 2; continue
        if c in (";", "|", "&", "\n"):
            segs.append("".join(buf)); buf = []; i += 1; continue
        buf.append(c); i += 1
    if quote:
        return segs, False                            # unterminated quote ⇒ unparseable
    segs.append("".join(buf))
    return segs, True


def _under_anchor(tok: str) -> bool:
    """Any path under the trust anchor — used to deny WRITE-shapes (mint/tamper)."""
    if ".torque" in tok:                              # cheap catch incl. $HOME/.torque
        return True
    try:
        rp = str(Path(os.path.expanduser(tok)).resolve())
    except Exception:
        return False
    a = str(lib.ANCHOR.resolve())
    return rp == a or rp.startswith(a + "/")


def _ref_secret_or_token(tok: str) -> bool:
    """The signing secret and the token store are NEVER legitimately referenced by agent
    Bash — reading the secret forges tokens, writing the token dir mints them. The approved-
    Apex copy (~/.torque/approved/*) is deliberately excluded: `sf apex run --file` must read
    it. Writes anywhere under the anchor are still denied by the write-shape check."""
    low = tok.lower()
    if "/secret" in low and ".torque" in low:
        return True
    if ".torque/tokens" in low or "/tokens/" in low and ".torque" in low:
        return True
    try:
        rp = str(Path(os.path.expanduser(tok)).resolve())
    except Exception:
        return False
    for p in (str(lib.SECRET.resolve()), str(lib.TOKENS.resolve())):
        if rp == p or rp.startswith(p + "/"):
            return True
    return False


def _real_argv(argv):
    """Strip VAR=val assignments and wrapper prefixes (env/sudo/nohup/…) to the real cmd."""
    i = 0
    while i < len(argv):
        tok = argv[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):
            i += 1; continue
        base = os.path.basename(tok).lower()
        if base in WRAPPER_PREFIX:
            i += 1
            while i < len(argv) and argv[i].startswith("-"):
                i += 1
            continue
        return argv[i:]
    return []


def _targets(argv):
    out, i = [], 0
    while i < len(argv):
        t = argv[i]
        if t in TARGET_FLAGS and i + 1 < len(argv):
            out.append(argv[i + 1]); i += 2; continue
        matched = False
        for f in TARGET_FLAGS:
            if t.startswith(f + "="):
                out.append(t[len(f) + 1:]); matched = True; break
        i += 1
    return out


def _is_sf_read(sf_args) -> bool:
    pos = [a for a in sf_args if not a.startswith("-")]
    if not pos:
        return True                                   # bare `sf` ⇒ help, harmless
    first = pos[0].lower()
    if ":" in first:                                  # colon (force:*) form
        return any(first.startswith(r) or (":" + first).find(":query") >= 0
                   for r in COLON_READ) or first.startswith(COLON_READ)
    key1 = (first,)
    key2 = (first, pos[1].lower()) if len(pos) > 1 else None
    return key1 in SF_READS or (key2 is not None and key2 in SF_READS)


def _authorize_sf(sf_args):
    if _is_sf_read(sf_args):
        return                                        # reads need no authorization
    targets = _targets(sf_args)
    if len(targets) == 0:
        lib.deny("Salesforce write without an explicit --target-org/-o/-u "
                 "(default-org and env-target writes are refused)", "no-target", HOOK)
    if len(set(targets)) > 1:
        lib.deny(f"ambiguous write targets {sorted(set(targets))} in one command",
                 "ambiguous-target", HOOK)
    ok, reason = lib.authorize_write(targets[0])
    if not ok:
        lib.deny(reason, "not-authorized", HOOK)


def handle_bash(cmd: str):
    segs, ok = _split_segments(cmd)
    if not ok:
        if SF_WORD.search(cmd):
            lib.deny("unparseable command (unbalanced quotes) carrying an sf token",
                     "unparseable-sf", HOOK)
        lib.allow()
    # command substitution wrapping an sf token can't be authorized ⇒ fail closed
    if ("$(" in cmd or "`" in cmd) and SF_WORD.search(cmd):
        lib.deny("command substitution around an sf token — not authorizable",
                 "substitution-sf", HOOK)
    for seg in segs:
        seg = seg.strip()
        if not seg:
            continue
        try:
            argv = shlex.split(seg)
        except ValueError:
            if SF_WORD.search(seg):
                lib.deny("unparseable segment carrying an sf token", "unparseable-sf", HOOK)
            continue
        if not argv:
            continue
        # 1. secret/token reference (read forges, write mints) — any token, deny.
        #    Reads of the approved-apex copy are allowed; anchor WRITES caught in step 2.
        for tok in argv:
            if _ref_secret_or_token(tok):
                lib.deny("Bash reference to the signing secret or token store is denied; "
                         "these are operator-only", "anchor-ref", HOOK)
        # 2. write-shapes into a protected path (redirects, cp/mv/tee/dd/sed -i/ln)
        _check_write_shapes(argv, seg)
        # 3. the real command after wrappers
        real = _real_argv(argv)
        if not real:
            continue
        base = os.path.basename(real[0]).lower()
        # 4. inline interpreter carrying an sf token — runtime target unknowable ⇒ deny
        if base in INTERPRETERS:
            rest = " ".join(real[1:])
            if base in ("xargs", "parallel"):
                if any(os.path.basename(a).lower() in ("sf", "sfdx") for a in real[1:]):
                    lib.deny("sf invoked via xargs/parallel — target not authorizable",
                             "indirect-sf", HOOK)
                continue
            if any(fl in real[1:] for fl in ("-c", "-e", "-")) and SF_WORD.search(rest):
                lib.deny(f"{base} inline code carrying an sf token — not authorizable",
                         "interp-sf", HOOK)
            continue
        # 5. a direct sf/sfdx call
        if base in ("sf", "sfdx"):
            _authorize_sf(real[1:])
    lib.allow()


def _check_write_shapes(argv, seg):
    base0 = os.path.basename(argv[0]).lower() if argv else ""
    candidates = []
    for i, tok in enumerate(argv):
        if REDIR_RE.match(tok) and i + 1 < len(argv):
            candidates.append(argv[i + 1])
        elif tok.startswith((">", ">>")) and len(tok.lstrip(">|&")) > 0:
            candidates.append(tok.lstrip(">|&"))
    if base0 in WRITE_SHAPE_CMDS or (base0 == "sed" and "-i" in argv):
        candidates += [a for a in argv[1:] if not a.startswith("-")]
        candidates += [a.split("=", 1)[1] for a in argv if a.startswith("of=")]
    # perm/removal commands can weaken protection or the local store as easily as a write
    if base0 in PERM_CMDS:
        candidates += [a for a in argv[1:] if not a.startswith("-")]
    for c in candidates:
        if not c:
            continue
        if _under_anchor(c) or lib.is_protected_target(c) or _is_gate_input(c) or _is_local_store(c):
            lib.deny(f"Bash {base0 or 'redirect'} targeting a protected file: {c}",
                     "protected-write", HOOK)


def _is_gate_input(path_str: str) -> bool:
    base = os.path.basename(path_str)
    return base in ("writable-orgs.json", "protected-objects", "cli-write-surface.json",
                    "clean-ip.rules", ".classify-cache.json", "audit.log")


def _is_local_store(path_str: str) -> bool:
    """The local/ store dir itself (chmod/rm here is a fail-open attempt on audit + cache)."""
    try:
        rp = str(Path(os.path.expanduser(path_str)).resolve())
    except Exception:
        return False
    lp = str(lib.LOCAL.resolve())
    return rp == lp or rp.startswith(lp + "/")


def handle_mcp(tool: str, tinput: dict):
    if not re.search(r"(deploy_metadata|create_record|update_record|delete_record|"
                     r"upsert_record|assign_permission_set|execute_anonymous_apex|bulk)",
                     tool, re.I):
        lib.allow()
    target = (tinput.get("targetOrg") or tinput.get("target-org")
              or tinput.get("username") or tinput.get("usernameOrAlias"))
    if not target:
        lib.deny(f"MCP write tool {tool} without an org parameter", "mcp-no-target", HOOK)
    ok, reason = lib.authorize_write(target)
    if not ok:
        lib.deny(reason, "not-authorized", HOOK)
    lib.allow()


def handle_edit(tinput: dict):
    path = tinput.get("file_path", "")
    if lib.is_protected_target(path) or _is_gate_input(path) or _under_anchor(path):
        lib.deny(f"agent modification of protected file {os.path.basename(path)} is denied; "
                 "operator-present issuance only", "artifact-edit", HOOK)
    lib.allow()


def main():
    ev = lib.read_event()
    tool = ev.get("tool_name", "")
    tinput = ev.get("tool_input", {}) or {}
    if tool == "Bash":
        handle_bash(tinput.get("command", ""))
    elif tool in ("Edit", "Write", "MultiEdit"):
        handle_edit(tinput)
    elif tool.startswith("mcp__"):
        handle_mcp(tool, tinput)
    lib.allow()


if __name__ == "__main__":
    lib.run_gate(main, HOOK)
