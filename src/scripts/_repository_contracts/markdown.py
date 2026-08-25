"""Markdown structure helpers shared by repository policy modules."""

from _common import resolve_structural_target


def section(text: str, heading: str) -> str:
    try:
        resolved = resolve_structural_target(text, heading)
    except ValueError:
        return ""
    start, end = resolved["ranges"]["body"]
    return text[start:end]
