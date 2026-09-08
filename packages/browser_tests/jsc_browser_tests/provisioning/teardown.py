"""teardown.py — topological cleanup (children→parents) + ALL-ROWS leak verification."""
from __future__ import annotations

from .object_registry import load_registry


def topo_order(registry: dict | None = None) -> list[str]:
    """Return delete order: every object before its parent (per parent_join_field).

    Kahn-style topological sort on the parent_join_field edges. The loop places
    parents before children (top-down); we reverse at the end so children are
    deleted before the object they point at (FK-safe).
    """
    reg = registry or load_registry()
    # edge: child -> parent (child must be deleted first)
    parents = {api: e.parent_join_field for api, e in reg.items()
               if e.parent_join_field in reg}
    order, placed = [], set()
    remaining = set(reg.keys())
    while remaining:
        progressed = False
        for api in sorted(remaining):
            par = parents.get(api)
            if par is None or par in placed or par not in remaining:
                order.append(api); placed.add(api); remaining.discard(api); progressed = True
        if not progressed:  # cycle guard — append the rest deterministically
            order.extend(sorted(remaining)); break
    # parents were placed first => reverse so children come first (FK-safe delete)
    return list(reversed(order))


def verify_zero_leak(sf, runid: str, objects: list[str]) -> dict[str, int]:
    """Count TEST-<runid> survivors WITH ALL ROWS (sees recycle bin). Returns {obj: count>0}.

    # [LIVE] note: prefix-based scoping (`Name LIKE '<runid>-%'`) catches provisioned
    # PARENTS; non-Name-prefixed carrier CHILDREN (Hours/Benefits/etc., created by
    # managed controllers) are scoped by `Test_Record__c=true` + parent join in the
    # live phase — track for live cert.
    """
    leaks = {}
    for obj in objects:
        rows = sf.query(
            f"SELECT COUNT(Id) c FROM {obj} WHERE Name LIKE '{runid}-%'", all_rows=True,
        )
        count = rows[0].get("c", 0) if rows else 0
        if count:
            leaks[obj] = count
    return leaks
