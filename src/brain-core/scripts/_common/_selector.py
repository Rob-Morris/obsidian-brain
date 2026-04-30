"""Shared structural-selector contract for markdown target resolution."""

from __future__ import annotations

SELECTOR_OCCURRENCE_DESCRIPTION = (
    "1-based duplicate selector in the current search space after "
    "applying any 'within' ancestor filters."
)

SELECTOR_WITHIN_DESCRIPTION = (
    "Ordered ancestor chain from outermost to innermost used to narrow "
    "duplicate target matches before applying occurrence."
)

SELECTOR_WITHIN_TARGET_DESCRIPTION = (
    "Ancestor structural target for this disambiguation step, e.g. "
    "'# API' or '## Notes'. ':body' is not allowed here."
)

SELECTOR_WITHIN_OCCURRENCE_DESCRIPTION = (
    "1-based duplicate selector for this ancestor step within its "
    "current search space."
)

_SELECTOR_FIELDS = frozenset({"within", "occurrence"})
_SELECTOR_STEP_FIELDS = frozenset({"target", "occurrence"})


def _validate_positive_occurrence(value, *, label):
    if value is not None and (type(value) is not int or value < 1):
        raise ValueError(f"{label} must be a positive integer")


def normalize_structural_selector(selector):
    """Validate and normalize the public selector object."""
    if selector is None:
        return {"within": [], "occurrence": None}
    if not isinstance(selector, dict):
        raise ValueError("selector must be an object with optional within/occurrence fields")

    unknown = set(selector) - _SELECTOR_FIELDS
    if unknown:
        bad = ", ".join(sorted(unknown))
        raise ValueError(f"selector has unsupported fields: {bad}")

    occurrence = selector.get("occurrence")
    _validate_positive_occurrence(occurrence, label="selector.occurrence")

    within_steps = []
    for idx, step in enumerate(selector.get("within") or []):
        if not isinstance(step, dict):
            raise ValueError(f"selector.within[{idx}] must be an object")
        unknown = set(step) - _SELECTOR_STEP_FIELDS
        if unknown:
            bad = ", ".join(sorted(unknown))
            raise ValueError(f"selector.within[{idx}] has unsupported fields: {bad}")
        target = step.get("target")
        if not isinstance(target, str) or not target.strip():
            raise ValueError(f"selector.within[{idx}].target is required")
        if target.strip() == ":body":
            raise ValueError("selector.within targets cannot use ':body'")
        step_occurrence = step.get("occurrence")
        _validate_positive_occurrence(
            step_occurrence,
            label=f"selector.within[{idx}].occurrence",
        )
        within_steps.append({"target": target, "occurrence": step_occurrence})

    return {"within": within_steps, "occurrence": occurrence}
