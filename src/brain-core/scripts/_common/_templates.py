"""Timestamp utilities and template variable substitution."""

import os
import random
import re
import string
from datetime import datetime, timezone


def now_iso():
    """Return the current local datetime as an ISO 8601 string with timezone offset."""
    return datetime.now(timezone.utc).astimezone().isoformat()


_DATE_PLACEHOLDER_RE = re.compile(r"\{\{date:([^}]+)\}\}")

# Mapping from template date tokens to strftime codes.  Longest tokens
# first so ``YYYYMMDD`` is matched before ``YYYY``.
_DATE_TOKEN_MAP = [
    ("YYYYMMDD", "%Y%m%d"),
    ("YYYY-MM-DD", "%Y-%m-%d"),
    ("YYYY", "%Y"),
    ("ddd", "%a"),
    ("MM", "%m"),
    ("DD", "%d"),
]


def substitute_template_vars(content, template_vars=None, _now=None):
    """Replace template placeholders in *content*.

    Two kinds of substitution:

    1. **Date placeholders** — ``{{date:FORMAT}}`` where *FORMAT* uses
       tokens like ``YYYY``, ``MM``, ``DD``, ``ddd``.  Replaced with the
       formatted current datetime.
    2. **Custom variables** — arbitrary string → string pairs supplied via
       *template_vars*.  Applied longest-key-first to avoid partial matches
       (e.g. ``SOURCE_DOC_PATH|SOURCE_DOC_TITLE`` before ``SOURCE_DOC_PATH``).

    Pass *_now* to pin the datetime for deterministic tests.
    """
    if not content:
        return content

    now = _now if _now is not None else datetime.now(timezone.utc).astimezone()

    def _replace_date(m):
        fmt = m.group(1)
        for token, code in _DATE_TOKEN_MAP:
            fmt = fmt.replace(token, code)
        return now.strftime(fmt)

    content = _DATE_PLACEHOLDER_RE.sub(_replace_date, content)

    if template_vars:
        for key in sorted(template_vars, key=len, reverse=True):
            content = content.replace(key, template_vars[key])

    return content


def random_short_suffix(k=3):
    """Return a short random ``[a-z0-9]{k}`` string.

    Shared collision-avoidance primitive. Used for filename deduplication
    (``unique_filename``) and vault-registry alias deduplication. The
    alphabet is deliberately small so suffixes stay eyeball-readable.
    """
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=k))


def unique_filename(folder, stem, ext=".md"):
    """Return a filename in *folder* that doesn't collide with existing files.

    If ``folder/stem.ext`` doesn't exist, returns ``stem.ext``.
    Otherwise appends a random 3-char suffix: ``stem abc.ext``.
    """
    filename = f"{stem}{ext}"
    while os.path.isfile(os.path.join(folder, filename)):
        filename = f"{stem} {random_short_suffix()}{ext}"
    return filename
