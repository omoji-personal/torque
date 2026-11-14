"""Shared pytest fixtures for jsc_memory tests: a made-up workspace with two clients
and one initiative, built from plain files (no Torque command needed)."""
import json
import sys
from pathlib import Path

# Add packages/memory to sys.path so `import jsc_memory` works
_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))


import pytest


@pytest.fixture(autouse=True)
def no_inherited_scope(monkeypatch):
    """No test reads a workspace selected by the surrounding shell."""
    for name in ("TORQUE_WORKSPACE", "JSC_ROOT", "JSC_MEMORY_DIR"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "workspace.json").write_text(json.dumps({"schema": "torque.workspace/1", "name": "Trial Practice",
                                                     "profile": "generic"}), encoding="utf-8")
    for slug, name, org in (("alpha", "Alpha Nonprofit", "alpha-dev"), ("beta", "Beta Land Trust", None)):
        folder = root / "clients" / slug
        folder.mkdir(parents=True)
        (folder / "client.json").write_text(json.dumps({"schema": "torque.client/1", "name": name, "slug": slug,
                                                        "org": org}), encoding="utf-8")
    playbook = root / "initiatives" / "playbook"
    playbook.mkdir(parents=True)
    (playbook / "binding.json").write_text(json.dumps({"schema": "torque.binding/1", "kind": "initiative",
                                                       "slug": "playbook", "repositories": []}), encoding="utf-8")
    return root


@pytest.fixture
def scopes(workspace):
    """Scopes for a client, an initiative or the firm alone: scopes("alpha"), scopes(None)."""
    from jsc_memory import storage

    def select(slug="alpha"):
        if slug is None:
            return storage.scopes_for(workspace)
        folder = "initiatives" if slug == "playbook" else "clients"
        return storage.scopes_for(workspace / folder / slug)
    return select
