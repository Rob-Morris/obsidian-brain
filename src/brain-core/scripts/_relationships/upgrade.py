"""Read-only local/core catalogue conflict inspection during definition sync."""

from .definitions import definition
from .model import MAX_WORK
from .storage import Store


def catalogue_conflicts(root):
    """Preserve local bytes and report incompatible contracts for explicit migration."""
    store = Store(root)
    warnings = []
    visited = 0
    for kind in ("predicate", "event-type"):
        for path in (store.config / kind).glob("*.json"):
            visited += 1
            target = str(path.relative_to(store.root))
            if visited > MAX_WORK:
                warnings.append({"type": "relationships", "role": "catalogue", "target": target,
                                 "action": "preserved", "reason": "Catalogue work budget exceeded; run relationship.check."})
                return warnings
            try:
                definition(store, kind, path.stem)
            except (ValueError, OSError) as exc:
                warnings.append({"type": "relationships", "role": "catalogue", "target": target,
                                 "action": "preserved", "reason": str(exc)})
    return warnings
