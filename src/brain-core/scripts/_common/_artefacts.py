"""Shared artefact and config-resource helpers."""

import os
import re
from datetime import datetime, timezone

from ._frontmatter import parse_frontmatter
from ._slugs import is_valid_key, title_to_filename, title_to_slug, validate_key
from ._vault import match_artefact


ARTEFACT_KEY_RE = re.compile(
    r"^([a-z0-9]+(?:-[a-z0-9]+)*)[\/~]([a-z0-9]+(?:-[a-z0-9]+)*)$"
)

# Prefix used for terminal-status subfolders (e.g. "+Adopted", "+Completed").
STATUS_FOLDER_PREFIX = "+"

# Living types whose ownership tag should be stamped on the artefact itself
# (e.g. a project named ``brain`` carries ``project/brain`` in its own tags).
SELF_TAG_PREFIXES = {"project", "person", "workspace", "journal"}


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


def artefact_type_prefix(artefact_or_type):
    """Return the canonical singular type prefix used in artefact keys."""
    if isinstance(artefact_or_type, dict):
        value = (
            artefact_or_type.get("frontmatter_type")
            or artefact_or_type.get("type")
            or artefact_or_type.get("key")
            or ""
        )
    else:
        value = str(artefact_or_type or "")
    if "/" in value:
        return value.rsplit("/", 1)[-1]
    return value


def make_artefact_key(type_prefix, key):
    """Return canonical ``{type-prefix}/{key}`` form."""
    return f"{type_prefix}/{validate_key(key)}"


def parse_artefact_key(value):
    """Parse ``{type-prefix}/{key}`` or ``{type-prefix}~{key}`` form."""
    if not isinstance(value, str):
        return None
    match = ARTEFACT_KEY_RE.fullmatch(value.strip())
    if not match:
        return None
    prefix, key = match.groups()
    if not is_valid_key(key):
        return None
    return prefix, key


def normalize_artefact_key(value):
    """Normalise folder-form or slash-form artefact keys to slash form."""
    parsed = parse_artefact_key(value)
    if not parsed:
        return None
    prefix, slug = parsed
    return f"{prefix}/{slug}"


def resolve_artefact_definition_for_prefix(router, prefix):
    """Resolve a configured artefact definition by canonical type prefix."""
    for artefact in router.get("artefacts", []):
        if artefact_type_prefix(artefact) == prefix:
            return artefact
    return None


def resolve_artefact_key_entry(router, value):
    """Resolve an artefact key against the compiled living-artefact index."""
    key = normalize_artefact_key(value)
    if not key:
        return None
    return (router.get("artefact_index") or {}).get(key)


def iter_markdown_under(type_dir, *, include_status_folders=True):
    """Yield markdown file paths relative to *type_dir*.

    Skips directories whose names begin with ``.`` or ``_``.  When
    *include_status_folders* is ``False``, also skips directories whose names
    begin with ``+`` (terminal-status folders such as ``+Adopted``).

    Emits paths relative to *type_dir*, not to the vault root — stitching the
    artefact base path back on is the caller's responsibility.
    """
    if not os.path.isdir(type_dir):
        return
    for dirpath, dirnames, filenames in os.walk(type_dir):
        dirnames[:] = [
            d
            for d in dirnames
            if not d.startswith(".")
            and not d.startswith("_")
            and (include_status_folders or not d.startswith(STATUS_FOLDER_PREFIX))
        ]
        for fname in filenames:
            if not fname.endswith(".md"):
                continue
            yield os.path.relpath(os.path.join(dirpath, fname), type_dir)


def iter_artefact_paths(vault_root, artefact, *, include_status_folders=True):
    """Yield vault-relative paths for one artefact's markdown files."""
    type_dir = os.path.join(str(vault_root), artefact["path"])
    for sub_rel in iter_markdown_under(type_dir, include_status_folders=include_status_folders):
        yield os.path.join(artefact["path"], sub_rel)


def iter_artefact_markdown_files(
    vault_root, router, *, classifications=None, include_status_folders=False
):
    """Yield relative paths for artefact markdown files in configured folders.

    ``include_status_folders`` gates ``+*`` terminal-status subfolders (e.g.
    ``+Adopted``, ``+Shipped``). It has no effect on non-living classifications,
    which always include status folders.
    """
    allowed = set(classifications or [])
    for artefact in router.get("artefacts", []):
        classification = artefact.get("classification")
        if allowed and classification not in allowed:
            continue
        include = include_status_folders or classification != "living"
        yield from iter_artefact_paths(vault_root, artefact, include_status_folders=include)


def iter_living_markdown_files(vault_root, router, *, include_status_folders=False):
    """Yield relative paths for living artefact markdown files."""
    yield from iter_artefact_markdown_files(
        vault_root,
        router,
        classifications={"living"},
        include_status_folders=include_status_folders,
    )


def ensure_tags_list(fields):
    """Normalise ``fields['tags']`` to a mutable list and return it."""
    tags = fields.get("tags")
    if tags is None:
        tags = []
    elif not isinstance(tags, list):
        tags = [tags]
    fields["tags"] = tags
    return tags


def ensure_self_tag(fields, type_prefix, key):
    """Stamp ``{type-prefix}/{key}`` into ``tags`` for self-tagging types."""
    if type_prefix not in SELF_TAG_PREFIXES:
        return False
    scoped_tag = make_artefact_key(type_prefix, key)
    tags = ensure_tags_list(fields)
    if scoped_tag in tags:
        return False
    tags.append(scoped_tag)
    return True


def ensure_parent_tag(fields):
    """Ensure the canonical parent key is present in ``tags`` when set."""
    parent_key = normalize_artefact_key(fields.get("parent"))
    if not parent_key:
        return False
    tags = ensure_tags_list(fields)
    if parent_key in tags:
        return False
    tags.append(parent_key)
    return True


def living_key_set(vault_root, router, artefact, *, exclude_path=None):
    """Return the set of known keys for a living artefact type.

    Consults the compiled artefact index when present; otherwise falls back to
    a filesystem walk (degraded path used before the router is compiled).
    """
    from ._router import resolve_and_validate_folder

    type_prefix = artefact_type_prefix(artefact)
    artefact_index = router.get("artefact_index")
    if artefact_index is not None:
        return {
            entry["key"]
            for entry in artefact_index.values()
            if entry.get("type_prefix") == type_prefix
            and entry.get("path") != exclude_path
        }

    keys = set()
    for rel_path in iter_living_markdown_files(vault_root, router):
        if rel_path == exclude_path:
            continue
        try:
            _resolved, art = resolve_and_validate_folder(vault_root, router, rel_path)
        except ValueError:
            continue
        if artefact_type_prefix(art) != type_prefix:
            continue
        content = read_file_content(vault_root, rel_path)
        if content.startswith("Error:"):
            continue
        fields, _ = parse_frontmatter(content)
        key = fields.get("key")
        if is_valid_key(key):
            keys.add(key)
    return keys


def replace_artefact_key_references(fields, old_key, new_key):
    """Replace exact canonical artefact-key references in frontmatter."""
    changed = False

    if normalize_artefact_key(fields.get("parent")) == old_key:
        if new_key is None:
            fields.pop("parent", None)
        else:
            fields["parent"] = new_key
        changed = True

    tags = fields.get("tags")
    if isinstance(tags, list):
        updated = []
        for tag in tags:
            if normalize_artefact_key(tag) == old_key:
                if new_key is not None:
                    updated.append(new_key)
                changed = True
            else:
                updated.append(tag)
        fields["tags"] = updated

    return changed


def scan_artefact_key_references(vault_root, router, key):
    """Return artefacts whose frontmatter references ``key``."""
    normalized = normalize_artefact_key(key)
    if not normalized:
        return []

    findings = []
    for rel_path in iter_artefact_markdown_files(
        vault_root, router, classifications={"living", "temporal"}, include_status_folders=True
    ):
        content = read_file_content(vault_root, rel_path)
        if content.startswith("Error:"):
            continue
        fields, _ = parse_frontmatter(content)
        parent_matches = normalize_artefact_key(fields.get("parent")) == normalized
        tag_matches = [
            tag
            for tag in fields.get("tags", [])
            if normalize_artefact_key(tag) == normalized
        ]
        if not parent_matches and not tag_matches:
            continue
        findings.append(
            {
                "path": rel_path,
                "fields": fields,
                "parent": parent_matches,
                "tags": tag_matches,
            }
        )
    return findings


def resolve_parent_reference(vault_root, router, parent):
    """Resolve a parent artefact reference to canonical key + metadata."""
    from ._router import resolve_and_validate_folder

    index_available = "artefact_index" in router
    key = normalize_artefact_key(parent)
    if key:
        if not index_available:
            raise ValueError(
                "Compiled artefact index missing; canonical parent lookup is unavailable"
            )
        entry = resolve_artefact_key_entry(router, key)
        if not entry:
            raise ValueError(f"INVALID_PARENT: no artefact matching '{parent}'")
        return key, entry

    resolved_path, parent_art = resolve_and_validate_folder(vault_root, router, parent)
    if parent_art.get("classification") != "living":
        raise ValueError("parent must resolve to a living artefact")
    content = read_file_content(vault_root, resolved_path)
    fields, _ = parse_frontmatter(content)
    slug = fields.get("key")
    if not is_valid_key(slug):
        raise ValueError(
            f"INVALID_PARENT: '{resolved_path}' has no valid key in frontmatter"
        )
    key = make_artefact_key(artefact_type_prefix(parent_art), slug)
    entry = resolve_artefact_key_entry(router, key)
    if entry is None:
        # The file exists and parses, but the compiled index doesn't know it.
        # Prefer a loud failure over a fabricated entry with a wrong children_count;
        # a missing entry after a path/name resolve means the router is stale.
        raise ValueError(
            f"INDEX_STALE: '{resolved_path}' resolved but is missing from the "
            "compiled artefact index; recompile the router and retry"
        )
    return key, entry


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

    Non-date placeholders (``{Title}``, ``{Version}`` etc.) are substituted
    from ``variables`` or the ``title`` as usual.
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

    for placeholder in ("{name}", "{Title}", "{title}"):
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


def resolve_folder(artefact, parent=None, fields=None, router=None):
    """Resolve the target folder for a new artefact.

    Temporal artefacts go into ``{base}/yyyy-mm/`` where ``yyyy-mm`` is
    derived from the selected naming rule's ``date_source`` when one is
    declared, else ``created``. Callers must reconcile timestamps and any
    explicit ``date_source`` field before calling — this function does not
    consult the wallclock.
    """
    base_path = artefact["path"]
    if artefact.get("classification") == "temporal":
        fields = fields or {}
        source_field = "created"
        naming = artefact.get("naming") or {}
        for rule in naming.get("rules") or []:
            match_field = rule.get("match_field")
            if match_field is None:
                source_field = rule.get("date_source") or "created"
                break
            if match_field not in fields:
                continue
            values = rule.get("match_values") or []
            if "*" in values or fields[match_field] in values:
                source_field = rule.get("date_source") or "created"
                break
        dt = parse_date_value(fields.get(source_field))
        if dt is None:
            raise ValueError(
                "resolve_folder: temporal artefact requires a parseable "
                f"'{source_field}' in fields. Reconcile render fields before calling."
            )
        month_folder = dt.strftime("%Y-%m")
        return os.path.join(base_path, month_folder)
    if parent and router:
        entry = resolve_artefact_key_entry(router, parent)
        if entry:
            if entry["type_prefix"] == artefact_type_prefix(artefact):
                return os.path.join(base_path, entry["key"])
            return os.path.join(base_path, f"{entry['type_prefix']}~{entry['key']}")
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
