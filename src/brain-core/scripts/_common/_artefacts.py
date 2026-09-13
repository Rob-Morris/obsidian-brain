"""Shared artefact and config-resource helpers."""

import json
import os
import re
from datetime import datetime, timezone

from ._document_revision import decode_persisted_document
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


class ParentChainError(ValueError):
    """Base error for invalid recursive parent-chain resolution."""


class BrokenParentChainError(ParentChainError):
    """Raised when a parent key cannot be resolved as a living artefact."""


class StaleArtefactIndexError(ParentChainError):
    """Raised when disk state and the compiled living artefact index disagree."""


class CyclicParentChainError(ParentChainError):
    """Raised when parent references form a cycle."""


class RequestCycleError(CyclicParentChainError):
    """Raised when a mutation request would create a parent cycle."""


def parent_chain_error_message(exc):
    """Return the user-facing mutation-boundary message for parent-chain errors."""
    if isinstance(exc, StaleArtefactIndexError):
        return (
            "Stale compiled artefact index: "
            f"{exc}. Recompile or repair the router/index, resolve any keyless "
            "or newly-created living artefacts, then retry the mutation."
        )
    if isinstance(exc, RequestCycleError):
        return f"Invalid living parent chain: {exc}."
    return (
        "Invalid living parent chain: "
        f"{exc}. Run check/doctor, reconcile the broken or cyclic parent "
        "metadata, then retry the mutation."
    )


class HasDescendantsError(ValueError):
    """Raised when a removal would strand living descendants."""

    code = "HAS_DESCENDANTS"

    def __init__(self, operation, source, descendants):
        self.operation = operation
        self.source = source
        self.descendants = descendants
        super().__init__(self.detailed_message())

    def to_payload(self):
        return {
            "code": self.code,
            "operation": self.operation,
            "source": self.source,
            "descendants": list(self.descendants),
        }

    def detailed_message(self):
        return json.dumps(self.to_payload(), sort_keys=True)


class MissingFileResult(str):
    """Typed missing-file result preserving the legacy rendered string."""

    def __new__(cls, path):
        result = super().__new__(cls, f"Error: file not found: {path}")
        result.path = path
        result.message = f"file not found: {path}"
        return result


def read_file_content(vault_root, rel_path):
    """Read a vault file, inferring Markdown when the supplied path has no suffix."""
    original = rel_path
    if not rel_path.endswith(".md"):
        rel_path += ".md"
    abs_path = os.path.join(vault_root, rel_path)
    if not os.path.isfile(abs_path) and original != rel_path:
        abs_path = os.path.join(vault_root, original)
        rel_path = original
    if not os.path.isfile(abs_path):
        return MissingFileResult(rel_path)
    return read_exact_file_content(abs_path)


def read_exact_file_content(path):
    """Read exact persisted bytes and return decoded text with their revision."""
    with open(path, "rb") as handle:
        return decode_persisted_document(handle.read())


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


def canonical_living_artefact_key(artefact, fields):
    """Return the canonical key for a living artefact/frontmatter pair."""
    key = fields.get("key") if isinstance(fields, dict) else None
    if artefact.get("classification") != "living" or not is_valid_key(key):
        return None
    return make_artefact_key(artefact_type_prefix(artefact), key)


def living_artefact_index_entry(artefact, rel_path, fields):
    """Return the compiled-router living-index entry for parsed frontmatter."""
    type_prefix = artefact_type_prefix(artefact)
    key_value = fields.get("key")
    return {
        "path": rel_path,
        "type": artefact["frontmatter_type"],
        "classification": artefact.get("classification", "living"),
        "type_key": artefact["key"],
        "type_prefix": type_prefix,
        "key": key_value,
        "parent": normalize_artefact_key(fields.get("parent")),
        "children_count": 0,
    }


def finalize_living_artefact_index(entries):
    """Return sorted living-index entries with direct children_count populated."""
    index = {key: dict(entry) for key, entry in entries.items()}
    for entry in index.values():
        entry["children_count"] = 0
    for entry in index.values():
        parent_key = entry.get("parent")
        if parent_key and parent_key in index:
            index[parent_key]["children_count"] += 1
    return dict(sorted(index.items()))


def _is_living_index_entry(entry):
    classification = entry.get("classification")
    if classification:
        return classification == "living"
    return str(entry.get("type") or "").startswith("living/")


def parse_artefact_key(value):
    """Parse canonical slash form or cross-type scope form."""
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
    """Normalise scope-form or slash-form artefact keys to slash form."""
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


def owner_folder_segment(target_artefact, owner_entry):
    """Render one owner-folder segment for ``owner_entry``.

    Segments are rendered relative to the target artefact type: same-type
    owners use ``{key}``; cross-type owners use ``{type-prefix}~{key}``.
    """
    target_prefix = artefact_type_prefix(target_artefact)
    owner_prefix = owner_entry.get("type_prefix")
    owner_key = owner_entry.get("key")
    if not owner_prefix or not owner_key:
        raise BrokenParentChainError("Owner entry is missing type_prefix or key")
    if owner_prefix == target_prefix:
        return owner_key
    return f"{owner_prefix}~{owner_key}"


def parent_chain_entries(router, parent, *, include_parent=True):
    """Return living parent-chain entries from root ancestor to ``parent``.

    The compiled artefact index remains direct-child-oriented. This helper
    derives the recursive chain on demand and treats absent or cyclic parents
    as structural errors for recursive placement/planning.
    """
    parent_key = normalize_artefact_key(parent)
    if not parent_key:
        return []

    artefact_index = (router or {}).get("artefact_index") or {}
    chain = []
    visiting = {}
    current_key = parent_key

    while current_key:
        if current_key in visiting:
            cycle_entries = chain[visiting[current_key]:]
            cycle = [entry.get("artefact_key") for entry in cycle_entries]
            cycle.append(current_key)
            raise CyclicParentChainError(
                "Cyclic parent chain: " + " -> ".join(cycle)
            )

        entry = artefact_index.get(current_key)
        if not entry:
            raise BrokenParentChainError(
                f"Broken parent reference: {current_key}"
            )
        if not _is_living_index_entry(entry):
            raise BrokenParentChainError(
                f"Parent is not a living artefact: {current_key}"
            )
        if not entry.get("type_prefix") or not entry.get("key"):
            raise BrokenParentChainError(
                f"Invalid living parent entry: {current_key}"
            )

        visiting[current_key] = len(chain)
        entry_with_key = dict(entry)
        entry_with_key["artefact_key"] = current_key
        chain.append(entry_with_key)
        current_key = normalize_artefact_key(entry.get("parent"))

    ordered = list(reversed(chain))
    if not include_parent and ordered:
        ordered = ordered[:-1]
    return ordered


def resolve_living_owner_folder(artefact, parent=None, router=None):
    """Resolve recursive living owner-folder projection for ``artefact``."""
    base_path = artefact["path"]
    parent_key = normalize_artefact_key(parent)
    if not parent_key:
        return base_path
    if not router:
        return os.path.join(base_path, parent)

    segments = [
        owner_folder_segment(artefact, entry)
        for entry in parent_chain_entries(router, parent_key)
    ]
    return os.path.join(base_path, *segments) if segments else base_path


def direct_child_entries(router, parent):
    """Return direct living children of ``parent`` from the compiled index."""
    parent_key = normalize_artefact_key(parent)
    if not parent_key:
        return []
    artefact_index = (router or {}).get("artefact_index") or {}
    children = []
    for child_key, entry in artefact_index.items():
        if not _is_living_index_entry(entry):
            continue
        if normalize_artefact_key(entry.get("parent")) != parent_key:
            continue
        child = dict(entry)
        child["artefact_key"] = child_key
        children.append(child)
    return sorted(children, key=lambda entry: entry["artefact_key"])


def descendant_entries(router, parent):
    """Return all living descendants of ``parent`` in parent-before-child order."""
    parent_key = normalize_artefact_key(parent)
    if not parent_key:
        return []
    artefact_index = (router or {}).get("artefact_index") or {}
    parent_entry = artefact_index.get(parent_key)
    if not parent_entry:
        raise StaleArtefactIndexError(
            f"source artefact key is not in the compiled living index: {parent_key}"
        )
    if not _is_living_index_entry(parent_entry):
        raise BrokenParentChainError(
            f"Parent is not a living artefact: {parent_key}"
        )

    descendants = []
    active = [parent_key]
    active_keys = {parent_key}
    visited = set()
    children_by_parent = {}
    for child_key, entry in artefact_index.items():
        if not _is_living_index_entry(entry):
            continue
        child_parent = normalize_artefact_key(entry.get("parent"))
        if child_parent:
            child = dict(entry)
            child["artefact_key"] = child_key
            children_by_parent.setdefault(child_parent, []).append(child)
    for children in children_by_parent.values():
        children.sort(key=lambda entry: entry["artefact_key"])

    def visit(current_key):
        for child in children_by_parent.get(current_key, []):
            child_key = child["artefact_key"]
            if child_key in active_keys:
                cycle = active[active.index(child_key):] + [child_key]
                raise CyclicParentChainError(
                    "Cyclic descendant chain: " + " -> ".join(cycle)
                )
            if child_key in visited:
                continue
            active.append(child_key)
            active_keys.add(child_key)
            visited.add(child_key)
            descendants.append(child)
            visit(child_key)
            active.pop()
            active_keys.remove(child_key)

    visit(parent_key)
    return descendants


def descendant_payload(entries):
    """Return stable structured descendant records for gate diagnostics."""
    return [
        {
            "key": entry.get("artefact_key"),
            "path": entry.get("path"),
            "type": entry.get("type"),
            "parent": entry.get("parent"),
        }
        for entry in entries
    ]


def terminal_status_folder(artefact, fields):
    """Return the canonical ``+Status`` folder for terminal artefacts, if any."""
    terminal = ((artefact or {}).get("frontmatter") or {}).get("terminal_statuses") or []
    status = (fields or {}).get("status")
    if status in terminal:
        return f"{STATUS_FOLDER_PREFIX}{status.capitalize()}"
    return None


def apply_terminal_status_folder(folder, artefact, fields):
    """Append the terminal ``+Status`` folder to *folder* if the artefact is in one."""
    status_folder = terminal_status_folder(artefact, fields)
    return os.path.join(folder, status_folder) if status_folder else folder


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


def artefact_territory_roots(vault_root, router):
    """Return the absolute roots of artefact territory: type roots plus ``_Archive``.

    Owner-folder pruning and the empty-folder scan both operate only inside
    these roots, so the two can never disagree about where artefact space ends.
    """
    roots = set()
    for artefact in (router or {}).get("artefacts", []):
        path = artefact.get("path")
        if path:
            roots.add(os.path.abspath(os.path.join(vault_root, path)))
    roots.add(os.path.abspath(os.path.join(vault_root, "_Archive")))
    return roots


def owner_folder_stop_dirs(vault_root, router):
    """Return directories where vacated owner-folder pruning must stop."""
    return artefact_territory_roots(vault_root, router) | {os.path.abspath(vault_root)}


def _is_within(path, root):
    """Return True when ``path`` is ``root`` or lies beneath it (path-aware, not a prefix test)."""
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def _rmdir_empty_subtree(directory):
    """Remove every rmdir-empty directory strictly beneath ``directory``, deepest first.

    ``os.rmdir`` is the atomic emptiness check: any real content, including
    junk such as ``.DS_Store``, leaves that branch in place. Symlinked
    directories are neither followed nor removed. ``directory`` itself is left
    to the caller.
    """
    for dirpath, _dirnames, _filenames in os.walk(directory, topdown=False):
        if dirpath == directory:
            continue
        try:
            os.rmdir(dirpath)
        except OSError:
            continue


def prune_vacated_owner_folders(vault_root, source_paths, router):
    """Remove rmdir-empty owner folders vacated by a successful move or delete set.

    Two notions of "empty" exist in this module. This helper uses
    *rmdir-emptiness*: it never unlinks a file, so junk such as ``.DS_Store``
    counts as content and blocks a branch silently. The explicit
    ``empty_folders`` repair (``scan_empty_artefact_folders`` /
    ``remove_empty_artefact_folders``) is the only path that treats junk as
    removable, and only after a dry run.

    Bounds:

    - A vacated directory outside artefact territory (type roots and
      ``_Archive`` — see ``artefact_territory_roots``) is skipped entirely, so
      attachment scopes under ``_Assets`` and configuration under ``_Config``
      are never touched and callers need no filtering of their own.
    - Within territory, empty subdirectories of the vacated directory are
      removed deepest-first, then the directory and its ancestors are removed
      upward until the first non-empty directory or stop dir (type root,
      ``_Archive``, vault root). The subtree pass is skipped when the vacated
      directory is a stop dir or an ``_Archive/<type>`` mirror root, so an
      unrelated move out of a root never sweeps that root's other empty
      folders.

    Each distinct vacated directory is processed once however many sources it
    contributed. Filesystem failures are never raised: a committed move set
    must not turn into a reported failure because tidying could not finish.
    """
    roots = artefact_territory_roots(vault_root, router)
    vault_abs = os.path.abspath(vault_root)
    stop_dirs = roots | {vault_abs}
    archive_root = os.path.abspath(os.path.join(vault_root, "_Archive"))
    descent_stops = stop_dirs | {
        os.path.join(archive_root, os.path.relpath(root, vault_abs))
        for root in roots
        if root != archive_root
    }
    vacated = dict.fromkeys(
        os.path.abspath(os.path.join(vault_root, os.path.dirname(source_path)))
        for source_path in source_paths
    )
    for current in vacated:
        if not any(_is_within(current, root) for root in roots):
            continue
        if current not in descent_stops and not os.path.islink(current):
            _rmdir_empty_subtree(current)
        while current not in stop_dirs and _is_within(current, vault_abs):
            try:
                os.rmdir(current)
            except OSError:
                break
            current = os.path.dirname(current)


INCIDENTAL_ENTRIES = frozenset({".DS_Store", "Thumbs.db"})
"""Filesystem junk that the empty-folder scan treats as removable, not as content."""


def _is_scannable_child_dir(entry, boundaries):
    """Return True for a plain, visible directory that is not itself a territory root."""
    return (
        not entry.is_symlink()
        and entry.is_dir(follow_symlinks=False)
        and not entry.name.startswith(".")
        and os.path.abspath(entry.path) not in boundaries
    )


def _classify_empty_folder(abs_dir, vault_root, boundaries):
    """Classify ``abs_dir`` for the empty-folder scan.

    Returns ``(finding, descendants)``: ``finding`` describes ``abs_dir`` when
    it is vacated-empty (every entry is junk or a vacated-empty subdirectory),
    listing every directory (itself first, pre-order) and junk file its
    removal would take; otherwise ``finding`` is ``None`` and ``descendants``
    holds the maximal vacated-empty directories found beneath it. Symlinks,
    ``.``-prefixed directories and other territory roots count as content.
    Raises ``OSError`` when a directory cannot be read.
    """
    directories = [os.path.relpath(abs_dir, vault_root)]
    junk_files = []
    vacated_children = []
    descendants = []
    vacated = True
    for entry in list(os.scandir(abs_dir)):
        if entry.is_symlink():
            vacated = False
            continue
        if entry.is_dir(follow_symlinks=False):
            if not _is_scannable_child_dir(entry, boundaries):
                vacated = False
                continue
            child, found = _classify_empty_folder(entry.path, vault_root, boundaries)
            if child is not None:
                vacated_children.append(child)
            else:
                vacated = False
                descendants.extend(found)
            continue
        if entry.name in INCIDENTAL_ENTRIES:
            junk_files.append(os.path.relpath(entry.path, vault_root))
            continue
        vacated = False
    if vacated:
        for child in vacated_children:
            directories.extend(child["directories"])
            junk_files.extend(child["junk_files"])
        return {
            "path": directories[0],
            "directories": directories,
            "junk_files": junk_files,
        }, []
    # A vacated child beneath a parent that holds content is itself maximal.
    return None, vacated_children + descendants


def scan_empty_artefact_folders(vault_root, router, *, unreadable=None):
    """Return maximal vacated-empty directories under type roots and ``_Archive``.

    A directory is *vacated-empty* when every entry is an incidental junk file
    (``INCIDENTAL_ENTRIES``) or a vacated-empty subdirectory — deliberately
    looser than the rmdir-emptiness the move engine uses, because this scan
    feeds an explicit, dry-run-first repair. Scan roots are
    ``artefact_territory_roots``; the roots themselves are never reported, and
    symlinks, ``.``-prefixed directories and any other root met during the
    descent count as content. Other ``_``-prefixed children are descended so
    legacy ``<Type>/_Archive`` shapes are covered.

    Directories the scan cannot read are appended (vault-relative) to
    ``unreadable`` when a list is given, otherwise skipped silently — the
    ``info``-level check tolerates under-reporting; the repair must not.
    Results are sorted by path.
    """
    vault_root = str(vault_root)
    roots = artefact_territory_roots(vault_root, router)
    findings = []
    for root in sorted(roots):
        if not os.path.isdir(root) or os.path.islink(root):
            continue
        try:
            entries = list(os.scandir(root))
        except OSError:
            if unreadable is not None:
                unreadable.append(os.path.relpath(root, vault_root))
            continue
        for entry in entries:
            if not _is_scannable_child_dir(entry, roots):
                continue
            try:
                finding, found = _classify_empty_folder(entry.path, vault_root, roots)
            except OSError:
                if unreadable is not None:
                    unreadable.append(os.path.relpath(entry.path, vault_root))
                continue
            findings.extend([finding] if finding is not None else found)
    findings.sort(key=lambda item: item["path"])
    return findings


def remove_empty_artefact_folders(vault_root, router, paths):
    """Remove previously scanned vacated-empty folders, re-verifying each first.

    Removal is driven entirely by a fresh classification of each path
    immediately before deletion, never by the earlier scan: a directory that
    has since gained content, vanished, or stopped being a plain directory is
    reported ``skipped`` and left untouched. Junk files are removed first,
    then the recorded directories deepest-first. Returns one outcome per path:
    ``{"path", "status": "removed" | "skipped" | "failed", "reason",
    "removed": [entries actually removed]}``.
    """
    vault_root = str(vault_root)
    boundaries = artefact_territory_roots(vault_root, router)
    outcomes = []

    def outcome(path, status, reason=None, removed=()):
        outcomes.append({
            "path": path,
            "status": status,
            "reason": reason,
            "removed": list(removed),
        })

    for rel_path in paths:
        abs_dir = os.path.join(vault_root, rel_path)
        if os.path.islink(abs_dir) or (os.path.lexists(abs_dir) and not os.path.isdir(abs_dir)):
            outcome(rel_path, "skipped", "path is no longer a plain directory")
            continue
        if not os.path.isdir(abs_dir):
            outcome(rel_path, "skipped", "directory no longer exists")
            continue
        try:
            finding, _descendants = _classify_empty_folder(abs_dir, vault_root, boundaries)
        except OSError as exc:
            outcome(rel_path, "failed", f"could not re-scan: {exc}")
            continue
        if finding is None:
            outcome(rel_path, "skipped", "directory is no longer vacated-empty")
            continue
        removed = []
        try:
            for rel_file in finding["junk_files"]:
                os.remove(os.path.join(vault_root, rel_file))
                removed.append(rel_file)
            for rel_dir in reversed(finding["directories"]):
                os.rmdir(os.path.join(vault_root, rel_dir))
                removed.append(rel_dir)
        except OSError as exc:
            outcome(rel_path, "failed", str(exc), removed)
            continue
        outcome(rel_path, "removed", None, removed)
    return outcomes


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
        if isinstance(content, MissingFileResult):
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


def scan_artefact_key_reference_index(vault_root, router):
    """Index frontmatter references by canonical artefact key in one vault pass."""
    references = {}
    for rel_path in iter_artefact_markdown_files(
        vault_root, router, classifications={"living", "temporal"}, include_status_folders=True
    ):
        content = read_file_content(vault_root, rel_path)
        if isinstance(content, MissingFileResult):
            continue
        fields, _ = parse_frontmatter(content)
        parent_key = normalize_artefact_key(fields.get("parent"))
        tags_by_key = {}
        for tag in fields.get("tags", []):
            tag_key = normalize_artefact_key(tag)
            if tag_key:
                tags_by_key.setdefault(tag_key, []).append(tag)
        referenced_keys = set(tags_by_key)
        if parent_key:
            referenced_keys.add(parent_key)
        for referenced_key in referenced_keys:
            references.setdefault(referenced_key, []).append({
                "path": rel_path,
                "fields": fields,
                "parent": referenced_key == parent_key,
                "tags": tags_by_key.get(referenced_key, []),
            })
    return references


def scan_artefact_key_references(vault_root, router, key):
    """Return artefacts whose frontmatter references ``key``."""
    normalized = normalize_artefact_key(key)
    if not normalized:
        return []
    return scan_artefact_key_reference_index(vault_root, router).get(normalized, [])


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
    if isinstance(content, MissingFileResult):
        raise StaleArtefactIndexError(
            f"resolved parent {resolved_path} is missing on disk"
        )
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
        raise StaleArtefactIndexError(
            f"resolved parent {resolved_path} is missing from the compiled living index"
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


def parse_scalar_index_date(value):
    """Parse scalar date text from index metadata, rejecting YAML collections."""
    if not isinstance(value, str):
        return None
    return parse_date_value(value)


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

    # ``{slug}`` was a historical title-derived built-in. Preserve an explicit
    # frontmatter ``slug`` value when supplied; otherwise derive it from title.
    result = result.replace("{slug}", title_to_slug(title))

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

    Temporal artefacts go into ``{base}/{owner-chain}/yyyy-mm/`` when a
    living parent is set, or ``{base}/yyyy-mm/`` otherwise. The month is
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
        parent_key = normalize_artefact_key(parent)
        if parent_key and router:
            segments = [
                owner_folder_segment(artefact, entry)
                for entry in parent_chain_entries(router, parent_key)
            ]
            return os.path.join(base_path, *segments, month_folder)
        if parent_key:
            raise BrokenParentChainError(
                parent_key,
                "Parent-scoped temporal filing requires a compiled router.",
            )
        return os.path.join(base_path, month_folder)
    if artefact.get("classification") == "living":
        return resolve_living_owner_folder(artefact, parent=parent, router=router)
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
        configured_path = artefact.get("template_file")
        if configured_path:
            from ._config_layout import markdown_rel_path

            return markdown_rel_path(configured_path)
        classification = artefact.get("classification", "living")
        from ._config_layout import template_rel_path

        return template_rel_path(classification, artefact["folder"])
    raise ValueError(f"Unknown config resource: {resource}")
