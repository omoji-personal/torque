"""Two test files with the same name in different tests folders are one module to pytest here.

Every `tests` folder under `packages/` is a package named `tests`, as is the top-level one for this purpose, so
`packages/memory/tests/test_x.py` and `tests/test_x.py` both import as `tests.test_x`. In one run the second is
collected as the first: its own tests never run, and the first's run again without their fixtures. This happened
once (a new `test_cli.py` beside the existing one: 12 setup errors in a combined run, and in the default order the
new tests would simply not have run). Keep every test file's name unique in the repository."""
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_no_two_test_files_share_a_name():
    seen = defaultdict(list)
    folders = [ROOT / "tests", *sorted((ROOT / "packages").glob("*/tests"))]
    for folder in folders:
        for path in sorted(folder.rglob("*.py")):
            if path.name in ("__init__.py", "conftest.py") or "__pycache__" in path.parts:
                continue
            seen[path.name].append(path.relative_to(ROOT).as_posix())
    clashes = {name: paths for name, paths in seen.items() if len(paths) > 1}
    assert not clashes, f"rename one of each pair so both are collected: {clashes}"
