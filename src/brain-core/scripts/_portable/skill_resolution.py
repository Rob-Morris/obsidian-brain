"""Pure user-first resolution rules for Brain skill records and paths."""

from __future__ import annotations


SKILL_SUBSTRATE_ORDER = ("user", "core")


def skill_record(name: str, skill_doc: str, source: str) -> dict[str, str]:
    """Build one canonical substrate-aware compiled skill record."""
    if source not in SKILL_SUBSTRATE_ORDER:
        raise ValueError(f"unknown skill substrate: {source}")
    return {"name": name, "skill_doc": skill_doc, "source": source}


def parse_skill_reference(reference: str) -> tuple[str, str | None]:
    """Split an optional user/core qualifier from a skill reference."""
    if ":" not in reference:
        return reference, None
    prefix, name = reference.split(":", 1)
    if prefix not in SKILL_SUBSTRATE_ORDER:
        return reference, None
    return name, prefix


def order_skill_records(records):
    """Return records in canonical user-first substrate order."""
    order = {source: index for index, source in enumerate(SKILL_SUBSTRATE_ORDER)}
    return sorted(records, key=lambda item: order.get(item.get("source"), len(order)))


def resolve_skill_record(records, reference):
    """Resolve an unqualified or substrate-qualified skill reference."""
    name, source = parse_skill_reference(reference)
    if ":" in reference and source is None:
        return None
    return next(
        (
            item
            for item in order_skill_records(records)
            if item.get("name") == name
            and (source is None or item.get("source") == source)
        ),
        None,
    )


def annotate_skill_records(records):
    """Mark same-name records as effective or shadowed in user-first order."""
    effective_names = set()
    annotated = []
    for record in order_skill_records(records):
        item = dict(record)
        effective = item.get("name") not in effective_names
        item["effective"] = effective
        item["shadowed"] = not effective
        effective_names.add(item.get("name"))
        annotated.append(item)
    return annotated


def effective_skill_path(user_path, core_path):
    """Return the effective package root using user-first resolution."""
    return user_path if user_path.is_dir() else core_path
