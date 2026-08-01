# P6: session-log integrity. "Log before/after values so a change can be reversed" and "no
# secrets in logs" were rules with nothing enforcing them. This exercises the real writer and
# asserts the record is usable for rollback AND redacted — so the claim binds.
import sys as _s, os as _os, time as _t
_s.path.insert(0, str(ROOT / "hooks"))
import lib as _lib


@check("session_log_integrity", "capability", catastrophe=True)
def _session_log(target):
    if not target:
        return Result("session_log_integrity", SKIP, "no --target-org")
    # bin/torque-log has no .py extension, so the loader must be given explicitly — importlib
    # cannot infer one from the suffix and silently returns a spec with loader=None.
    import importlib.util
    from importlib.machinery import SourceFileLoader
    _ldr = SourceFileLoader("torque_log", str(ROOT / "bin" / "torque-log"))
    spec = importlib.util.spec_from_loader("torque_log", _ldr)
    m = importlib.util.module_from_spec(spec); _ldr.exec_module(m)

    # a realistic entry, deliberately seeded with a secret-shaped value and an org id
    tag = f"harness-probe-{int(_t.time())}"
    secret_shaped = "sid" "=AbC123NotARealSession"
    p, e = m.write_entry(target, f"{tag} {secret_shaped}",
                         before='{"Rating":"Warm"}', after='{"Rating":"Hot"}',
                         note="00D" + "000000000001AAA")

    def _cleanup():
        # remove the probe line WHATEVER the outcome — proving the check can fail must not
        # leave an unredacted value on disk (it did exactly that the first time).
        try:
            lines = [l for l in p.read_text().splitlines() if tag not in l]
            p.write_text("\n".join(lines) + ("\n" if lines else ""))
        except Exception:
            pass

    def _fail(msg):
        _cleanup(); return Result("session_log_integrity", FAIL, msg)

    # 1. every field a rollback needs must be present
    missing = [k for k in m.REQUIRED if k not in e]
    if missing:
        return _fail(f"entry missing fields: {missing}")
    # 2. it must actually be reversible — a before-value distinct from the after-value
    if not e["reversible"]:
        return _fail("entry not marked reversible despite distinct before/after")
    # 3. redaction must have run on EVERY written field (this is the privacy claim)
    blob = json.dumps(e)
    if "AbC123NotARealSession" in blob:
        return _fail("session-id value written to disk in clear")
    if re.search("00D" + r"[A-Za-z0-9]{12,15}", blob):
        return _fail("org id written to disk in clear")
    # 4. the file must be operator-only and inside the gitignored workspace
    mode = _os.stat(p).st_mode & 0o777
    if mode & 0o077:
        return _fail(f"{p.name} mode {oct(mode)} is not 0600")
    if not str(p).startswith(str(_lib.LOCAL)):
        return _fail(f"log written outside local/: {p}")
    # 5. it must be readable back as JSONL (a log you cannot parse is not a log)
    try:
        last = json.loads(p.read_text().strip().splitlines()[-1])
    except Exception as ex:
        return _fail(f"log line not parseable: {ex}")
    if last.get("action", "").find(tag) < 0:
        return _fail("written entry not found on read-back")

    _cleanup()
    return Result("session_log_integrity", PASS,
                  "entry written, reversible, redacted (session-id + org id), 0600, parseable")
