"""Timestamp reconciliation — the §5 cascade from the rendering contract.

Fills missing ``created`` / ``modified`` (and optional type-specific
``date_source`` fields) from the best available signal: frontmatter, then
filename prefix, then file ``mtime``, then ``now()``. Returns the updated
``fields`` dict — callers compose the write into their normal path so
reconciliation itself has no filesystem side effects.
"""

import os
import re
from datetime import datetime, timezone

from ._artefacts import parse_date_value
from ._naming import select_rule


_FILENAME_DATE_RE = re.compile(r"^(\d{8}|\d{4}-\d{2}-\d{2})")


def _parse_filename_date(filename):
    """Return a timezone-aware datetime parsed from a leading date prefix, or None."""
    if not filename:
        return None
    m = _FILENAME_DATE_RE.match(os.path.basename(filename))
    if not m:
        return None
    raw = m.group(1)
    fmt = "%Y%m%d" if len(raw) == 8 else "%Y-%m-%d"
    try:
        dt = datetime.strptime(raw, fmt)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc)


def _file_mtime(abs_path):
    """Return a timezone-aware datetime for a file's mtime, or None if missing."""
    if not abs_path:
        return None
    try:
        ts = os.path.getmtime(abs_path)
    except OSError:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _now():
    return datetime.now(timezone.utc).astimezone()


def _to_iso(dt):
    """Serialise a datetime to ISO-8601 in the local timezone."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().isoformat()


def reconcile_timestamps(fields, abs_path, filename=None):
    """Populate missing ``created`` / ``modified`` via the §5 cascade.

    Mutates and returns ``fields``. Idempotent — a second call is a no-op
    once both fields are present. Reads file ``mtime`` but never writes;
    callers persist the returned fields through their normal write path.
    """
    fields = fields or {}

    if parse_date_value(fields.get("created")) is None:
        dt = (
            _parse_filename_date(filename or (abs_path and os.path.basename(abs_path)))
            or _file_mtime(abs_path)
            or _now()
        )
        fields["created"] = _to_iso(dt)

    if parse_date_value(fields.get("modified")) is None:
        dt = _file_mtime(abs_path) or _now()
        fields["modified"] = _to_iso(dt)

    return fields


def reconcile_date_source(fields, abs_path, filename, naming, selected_rule):
    """Populate a type-specific ``date_source`` field from the §5 cascade.

    Resolves the ``date_source`` declared on ``selected_rule`` (or inherited
    from the classification default). If that field is missing from
    ``fields``, fills it from:

    1. Frontmatter value if parseable (no-op passthrough).
    2. Date prefix parsed from ``filename``.
    3. ``fields["created"]`` (date portion) as a last resort for types
       where subject date aligns with creation.

    Raises ``ValueError`` if none of the above yield a value — the caller
    (edit surfaces the error, migration logs and skips).
    """
    fields = fields or {}
    if not selected_rule:
        return fields
    source = selected_rule.get("date_source")
    if not source or source in ("created", "modified"):
        return fields
    if parse_date_value(fields.get(source)) is not None:
        return fields

    dt = _parse_filename_date(filename or (abs_path and os.path.basename(abs_path)))
    if dt is None:
        dt = parse_date_value(fields.get("created"))
    if dt is None:
        raise ValueError(
            f"Cannot reconcile date_source '{source}': no value in frontmatter, "
            f"no date prefix in filename, no 'created' fallback."
        )

    fields[source] = dt.date().isoformat()
    return fields


def reconcile_fields_for_render(fields, artefact=None, abs_path=None, filename=None):
    """Populate render-driving date fields before filename or folder resolution.

    Applies the universal timestamp cascade first, then reconciles the selected
    naming rule's explicit ``date_source`` field when needed. Callers should use
    this before rendering filenames or temporal month folders so explicit
    per-type subject dates (for example logs keyed by ``date`` rather than
    physical ``created`` time) are available consistently across create, edit,
    convert, and migration flows.
    """
    fields = fields or {}
    reconcile_timestamps(fields, abs_path, filename=filename)
    naming = (artefact or {}).get("naming")
    if not naming:
        return fields
    rule = select_rule(naming, fields)
    if not rule:
        return fields
    try:
        reconcile_date_source(fields, abs_path, filename, naming, rule)
    except ValueError:
        pass
    return fields
