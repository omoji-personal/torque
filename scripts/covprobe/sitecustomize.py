"""Line-hit recorder that every Python subprocess inherits.

The harness spawns most tools as subprocesses, so an in-process tracer sees almost
nothing. Python imports sitecustomize from PYTHONPATH at startup in EVERY process,
which is the one hook that reaches all of them without touching the code under test.

Only frames whose file is in scope get line tracing: the call-event tracer returns
None for everything else, so out-of-scope code runs at full speed.
"""
import atexit
import os
import sys
import threading

# Delegate to the REAL sitecustomize before doing anything else. Shadowing it silently
# removed homebrew's site-packages setup, so `import yaml` started failing and two checks
# went red — an instrumentation artifact that would have been read as a finding. A probe
# that changes the thing it measures produces evidence about the probe.
_me = os.path.dirname(os.path.abspath(__file__))
for _p in sys.path:
    try:
        if not _p or os.path.abspath(_p) == _me:
            continue
        _cand = os.path.join(_p, "sitecustomize.py")
        if os.path.isfile(_cand):
            with open(_cand) as _fh:
                exec(compile(_fh.read(), _cand, "exec"),
                     {"__file__": _cand, "__name__": "sitecustomize"})
            break
    except Exception:
        pass

_DIR = os.environ.get("TQ_COV_DIR")
_SCOPE = tuple(p for p in (os.environ.get("TQ_COV_SCOPE") or "").split(os.pathsep) if p)

if _DIR and _SCOPE:
    _hits = set()

    def _lines(frame, event, arg):
        if event == "line":
            _hits.add((frame.f_code.co_filename, frame.f_lineno))
        return _lines

    def _calls(frame, event, arg):
        if event != "call":
            return None
        f = frame.f_code.co_filename
        for s in _SCOPE:
            if f.startswith(s):
                _hits.add((f, frame.f_lineno))
                return _lines
        return None

    def _dump():
        if not _hits:
            return
        try:
            path = os.path.join(_DIR, f"hits-{os.getpid()}-{id(_hits)}.txt")
            with open(path, "w") as fh:
                for f, n in _hits:
                    fh.write(f"{f}:{n}\n")
        except Exception:
            pass

    atexit.register(_dump)
    threading.settrace(_calls)
    sys.settrace(_calls)
