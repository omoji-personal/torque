"""Report executable lines in bin/ and hooks/ that the whole static profile never ran.

D2 — the production session window that raised UnboundLocalError on its first line —
was a whole branch nothing executed. Nobody would have written a check for it, because
writing the check requires already suspecting the branch. Unreached code is the one
defect class you can find WITHOUT a hypothesis, which is what makes it worth automating.
"""
import ast
import pathlib
import sys
from collections import defaultdict

REPO = pathlib.Path("/Users/omidmojtahedi/Desktop/torque")
HITS = pathlib.Path(sys.argv[1])
SCOPE = [REPO / "bin", REPO / "hooks"]

# Lines that are executable but uninteresting when unhit.
SKIP_NODES = (ast.Import, ast.ImportFrom)


def executable_lines(path):
    """Statement lines, minus imports, docstrings and `if __name__` guards."""
    try:
        tree = ast.parse(path.read_text(errors="ignore"))
    except SyntaxError:
        return {}
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.stmt) or isinstance(node, SKIP_NODES):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            continue                                   # docstring
        out[node.lineno] = type(node).__name__
    return out


def enclosing_defs(path):
    """line -> nearest enclosing def name, for readable output."""
    tree = ast.parse(path.read_text(errors="ignore"))
    owner = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for ln in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                owner[ln] = node.name
    return owner


hit = defaultdict(set)
for f in HITS.glob("hits-*.txt"):
    for line in f.read_text().splitlines():
        p, _, n = line.rpartition(":")
        if n.isdigit():
            hit[p].add(int(n))

files = []
for root in SCOPE:
    for p in sorted(root.rglob("*")):
        if p.is_file() and (p.suffix == ".py" or p.name.startswith("torque")):
            files.append(p)

total_exec = total_hit = 0
report = []
for p in files:
    ex = executable_lines(p)
    if not ex:
        continue
    got = hit.get(str(p), set())
    if not got:
        continue                       # never loaded at all — reported separately below
    miss = sorted(set(ex) - got)
    total_exec += len(ex)
    total_hit += len(ex) - len(miss)
    if miss:
        owner = enclosing_defs(p)
        by_fn = defaultdict(list)
        for ln in miss:
            by_fn[owner.get(ln, "<module>")].append(ln)
        report.append((p, len(ex), len(miss), by_fn))

never_loaded = [p for p in files if executable_lines(p) and str(p) not in hit]

report.sort(key=lambda r: -r[2])
print(f"=== unreached executable lines, static profile ===")
print(f"in-scope files exercised: {len(report) + (len(files) - len(report) - len(never_loaded))}")
print(f"lines executed: {total_hit}/{total_exec} "
      f"({100 * total_hit / max(total_exec, 1):.0f}%)\n")

for p, n_ex, n_miss, by_fn in report[:14]:
    rel = p.relative_to(REPO)
    print(f"{rel}  —  {n_miss}/{n_ex} lines never run")
    worst = sorted(by_fn.items(), key=lambda kv: -len(kv[1]))[:5]
    for fn, lns in worst:
        span = f"{lns[0]}" if len(lns) == 1 else f"{lns[0]}-{lns[-1]}"
        print(f"    {fn:42s} {len(lns):4d} lines  (L{span})")
    print()

if never_loaded:
    print("=== never imported or executed at all by any static check ===")
    for p in never_loaded:
        print(f"  {p.relative_to(REPO)}")
