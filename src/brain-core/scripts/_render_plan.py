"""Resolve one dated rendering document before any file or renderer effect."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from _common import resolve_and_check_bounds, slug_to_title, substitute_template_vars, title_to_filename


@dataclass(frozen=True, slots=True)
class RenderDocumentPlan:
    source: str
    slug: str
    kind: str
    effective_at: str
    path: str
    content: str
    created: bool


def plan_render_document(vault_root, params, *, kind, read_template, effective_at=None):
    root = Path(vault_root)
    if not params or "source" not in params or "slug" not in params:
        raise ValueError(f"shape-{kind} requires source and slug")
    source, slug = params["source"], params["slug"]
    source_abs = Path(resolve_and_check_bounds(root / source, root))
    if not source_abs.is_file():
        raise FileNotFoundError(f"Source file not found: {source}")
    now = effective_at or datetime.now(timezone.utc).astimezone()
    folder = "Presentations" if kind == "presentation" else "Printables"
    path = f"_Temporal/{folder}/{now.strftime('%Y%m%d')}-{kind}~{title_to_filename(slug)}.md"
    absolute = Path(resolve_and_check_bounds(root / path, root))
    created = not absolute.is_file()
    if created:
        template = read_template(str(root))
        if template is None:
            raise ValueError(f"{kind.capitalize()} template not found")
        content = substitute_template_vars(template, {
            kind.upper() + " TITLE": slug_to_title(slug),
            "[[source-artefact|Source document]]": f"[[{Path(source).with_suffix('').as_posix()}|{Path(source).name}]]",
        }, _now=now)
    else:
        content = absolute.read_text(encoding="utf-8")
    return RenderDocumentPlan(source, slug, kind, now.isoformat(), path, content, created)
