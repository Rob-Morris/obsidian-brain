"""Shared artefact and config-resource helpers."""

import os
import re
from datetime import datetime, timezone

from ._slugs import title_to_filename, title_to_slug
from ._vault import match_artefact


def read_file_content(vault_root, rel_path):
    """Read a vault file's content given a relative path from vault root."""
    original = rel_path
    if not rel_path.endswith(".md"):
        rel_path += ".md"
    abs_path = os.path.join(vault_root, rel_path)
    if not os.path.isfile(abs_path) and original != rel_path:
        abs_path = os.path.join(vault_root, original)
        rel_path = original
    if not os.path.isfile(abs_path):
        return f"Error: file not found: {rel_path}"
    with open(abs_path, "r", encoding="utf-8") as f:
        return f.read()


PLACEHOLDER_TOKEN_RE = re.compile(r"\{([A-Za-z][A-Za-z0-9_-]*)\}")

_DATE_TOKENS = ("yyyymmdd", "yyyy-mm-dd", "yyyy", "ddd", "mm", "dd")


def pattern_has_date_tokens(pattern):
    """Return True if the pattern contains any structural date token.

    Substrings inside ``{...}`` placeholders are ignored so placeholder names
    like ``{address}`` don't false-match ``dd``.
    """
    if not pattern:
        return False
    outside_placeholders = PLACEHOLDER_TOKEN_RE.sub("", pattern)
    return any(tok in outside_placeholders for tok in _DATE_TOKENS)


def parse_date_value(value):
    """Parse a frontmatter date value into a timezone-aware datetime, or None.

    Accepts ISO-8601 strings, ``YYYY-MM-DD``, ``YYYYMMDD``, or datetime/date
    objects. Missing tzinfo is assumed UTC then converted to local.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone()
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc).astimezone()
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone()
    except (TypeError, ValueError):
        return None


def resolve_naming_pattern(pattern, title, variables=None, date_source=None):
    """Resolve a naming pattern to a filename.

    Date tokens (``yyyymmdd``, ``yyyy-mm-dd``, ``yyyy``, ``ddd``, ``mm``,
    ``dd``) are read from ``variables[date_source]``. The caller must
    reconcile the backing field before calling; a pattern with date tokens
    and no parseable ``date_source`` value raises ``ValueError``.

    Non-date placeholders (``{Title}``, ``{slug}``, ``{Version}`` etc.) are
    substituted from ``variables`` or the ``title`` as usual.
    """
    variables = variables or {}
    safe_title = title_to_filename(title)
    result = pattern

    if pattern_has_date_tokens(pattern):
        if not date_source:
            raise ValueError(
                f"Naming pattern '{pattern}' has date tokens but no date_source "
                "is declared. Add `date_source` to the rule (temporal types "
                "default to `created`)."
            )
        raw = variables.get(date_source)
        dt = parse_date_value(raw)
        if dt is None:
            raise ValueError(
                f"Naming pattern '{pattern}' requires parseable "
                f"'{date_source}' in frontmatter (got {raw!r})."
            )
        replacements = [
            ("yyyymmdd", dt.strftime("%Y%m%d")),
            ("yyyy-mm-dd", dt.strftime("%Y-%m-%d")),
            ("yyyy", dt.strftime("%Y")),
            ("ddd", dt.strftime("%a")),
            ("mm", dt.strftime("%m")),
            ("dd", dt.strftime("%d")),
        ]
        for placeholder, value in replacements:
            result = result.replace(placeholder, value)

    for placeholder in ("{slug}", "{name}", "{Title}", "{title}"):
        result = result.replace(placeholder, safe_title)

    for key, raw_value in variables.items():
        if raw_value is None or isinstance(raw_value, (list, dict)):
            continue
        safe_value = title_to_filename(str(raw_value))
        placeholder_names = {
            key,
            str(key).lower(),
            str(key).upper(),
            str(key).title(),
        }
        for name in placeholder_names:
            result = result.replace(f"{{{name}}}", safe_value)

    unresolved = sorted({f"{{{name}}}" for name in PLACEHOLDER_TOKEN_RE.findall(result)})
    if unresolved:
        placeholders = ", ".join(unresolved)
        raise ValueError(
            f"Naming pattern '{pattern}' requires values for placeholder(s): {placeholders}"
        )

    return result


def resolve_type(router, type_key):
    """Match type_key against router artefacts by key, full type, or singular form."""
    artefacts = router.get("artefacts", [])
    match = match_artefact(artefacts, type_key)
    if match is None:
        raise ValueError(
            f"Unknown artefact type '{type_key}'. "
            f"Valid types: {', '.join(a['key'] for a in artefacts)}"
        )
    if not match.get("configured"):
        raise ValueError(
            f"Type '{type_key}' exists but is not configured "
            f"(no taxonomy file). Create a taxonomy file first."
        )
    return match


def resolve_folder(artefact, parent=None, fields=None):
    """Resolve the target folder for a new artefact.

    Temporal artefacts go into ``{base}/yyyy-mm/`` where ``yyyy-mm`` is
    derived from ``fields["created"]``. Callers must reconcile ``created``
    before calling — this function does not consult the wallclock.
    """
    base_path = artefact["path"]
    if artefact.get("classification") == "temporal":
        fields = fields or {}
        dt = parse_date_value(fields.get("created"))
        if dt is None:
            raise ValueError(
                "resolve_folder: temporal artefact requires a parseable "
                "'created' in fields. Reconcile timestamps before calling."
            )
        month_folder = dt.strftime("%Y-%m")
        return os.path.join(base_path, month_folder)
    if parent:
        return os.path.join(base_path, parent)
    return base_path


def config_resource_rel_path(router, resource, name):
    """Return the relative path for a _Config/ resource."""
    slug = title_to_slug(name)
    if resource == "skill":
        return os.path.join("_Config", "Skills", slug, "SKILL.md")
    if resource == "memory":
        return os.path.join("_Config", "Memories", slug + ".md")
    if resource == "style":
        return os.path.join("_Config", "Styles", slug + ".md")
    if resource == "template":
        artefact = resolve_type(router, name)
        classification = artefact.get("classification", "living")
        subdir = "Living" if classification == "living" else "Temporal"
        return os.path.join("_Config", "Templates", subdir, artefact["folder"] + ".md")
    raise ValueError(f"Unknown config resource: {resource}")
