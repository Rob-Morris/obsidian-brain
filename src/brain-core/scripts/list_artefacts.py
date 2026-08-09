"""list_artefacts — enumerate vault artefacts and other resources.

Unlike search_index (BM25, relevance-ranked, capped), this module filters the
in-memory index directly and returns all matching documents up to top_k. The
index already contains type, tags, modified, path, title, and status for every
vault artefact — no filesystem walk needed.

For non-artefact resources (skills, styles, triggers, etc.), listing reads from
the compiled router's small collections with optional text filtering.

Usage:
    python3 list_artefacts.py
    python3 list_artefacts.py artefact --type living/wiki --sort title
    python3 list_artefacts.py skill --query vault
    python3 list_artefacts.py workspace --vault /path/to/vault
    python3 list_artefacts.py archive --json
"""

import argparse
import json
import os
import sys

import workspace_registry
from _search import lexical_query
from _common import (
    find_vault_root,
    load_compiled_router,
    parse_frontmatter,
)
from _portable.artefact_listing import (
    _collect_artefacts,
    _cursor_offset,
    _index_date,
    _validate_iso_date,
    list_artefacts,
    list_artefacts_page,
)


# ---------------------------------------------------------------------------
# Non-artefact resource listing
# ---------------------------------------------------------------------------

# Router keys for small collections that list via _list_collection().
# Resources with custom listing logic (artefact, type, template, archive,
# workspace) are handled by explicit branches in list_resources().
_COLLECTION_MAP = {
    "skill": ("skills", "name"),
    "trigger": ("triggers", "name"),
    "style": ("styles", "name"),
    "plugin": ("plugins", "name"),
    "memory": ("memories", "name"),
}


def _list_templates(router, query=None):
    """List available templates derived from artefact type definitions."""
    results = []
    for art in router.get("artefacts", []):
        tpl = art.get("template_file")
        if not tpl:
            continue
        name = art.get("key", "")
        if query and query.lower() not in name.lower():
            continue
        results.append({
            "name": name,
            "type": art.get("frontmatter_type", art.get("type", "")),
            "template_file": tpl,
        })
    return results


def _list_collection(router, router_key, name_field, query=None):
    """List items from a router collection with optional text filter."""
    items = router.get(router_key, [])
    if not query:
        return list(items)  # copy — caller may mutate (e.g. sort)
    lower_q = query.lower()
    return [i for i in items if lower_q in i.get(name_field, "").lower()]


def _list_archive(router, vault_root):
    """List all archived files. Extracted from read.py for shared use."""
    vault_root = str(vault_root)
    results = []
    seen = set()

    def _scan_dir(base_dir):
        if not os.path.isdir(base_dir):
            return
        for dirpath, dirnames, filenames in os.walk(base_dir):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fname in filenames:
                if not fname.endswith(".md"):
                    continue
                abs_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(abs_path, vault_root)
                if rel_path in seen:
                    continue
                seen.add(rel_path)
                try:
                    with open(abs_path, "r", encoding="utf-8") as f:
                        fields, _ = parse_frontmatter(f.read())
                except Exception:
                    fields = {}
                results.append({
                    "path": rel_path,
                    "title": os.path.splitext(fname)[0],
                    "type": fields.get("type", ""),
                    "status": fields.get("status", ""),
                    "archiveddate": fields.get("archiveddate", ""),
                })

    _scan_dir(os.path.join(vault_root, "_Archive"))

    for art in router.get("artefacts", []):
        art_dir = os.path.join(vault_root, art["path"])
        if not os.path.isdir(art_dir):
            continue
        for entry in os.listdir(art_dir):
            if entry == "_Archive":
                _scan_dir(os.path.join(art_dir, "_Archive"))
            sub = os.path.join(art_dir, entry)
            if os.path.isdir(sub) and not entry.startswith((".", "_", "+")):
                archive_sub = os.path.join(sub, "_Archive")
                if os.path.isdir(archive_sub):
                    _scan_dir(archive_sub)

    results.sort(key=lambda r: r.get("archiveddate", ""), reverse=True)
    return results


def list_resources(index, router, vault_root, resource="artefact", query=None,
                   **kwargs):
    """List resources of a given kind.

    For artefacts: delegates to list_artefacts() with full filtering (type,
    since, until, tag, top_k, sort).
    For other resources: reads from router, optional query text filter.

    Args:
        index:     in-memory BM25 index dict (required for artefact listing)
        router:    compiled router dict
        vault_root: absolute path to the vault root
        resource:  which collection to list (default "artefact")
        query:     optional text filter (substring match on name, for non-artefact resources)
        **kwargs:  passed through to list_artefacts for artefact-specific filters

    Returns:
        list of dicts (format varies by resource kind).

    Raises:
        ValueError: if resource is not listable.
    """
    if resource == "artefact":
        return list_artefacts(index, router, **kwargs)

    if resource == "type":
        arts = router.get("artefacts", [])
        if query:
            lower_q = query.lower()
            arts = [a for a in arts if lower_q in a.get("key", "").lower()
                    or lower_q in a.get("frontmatter_type", "").lower()]
        return arts

    if resource == "template":
        return _list_templates(router, query)

    if resource == "archive":
        return _list_archive(router, vault_root)

    if resource == "workspace":
        return workspace_registry.list_workspaces(vault_root)

    if resource in _COLLECTION_MAP:
        router_key, name_field = _COLLECTION_MAP[resource]
        return _list_collection(router, router_key, name_field, query)

    _listable = sorted({"artefact", "archive", "template", "type", "workspace"}
                        | set(_COLLECTION_MAP))
    raise ValueError(
        f"Resource '{resource}' is not listable. "
        f"Listable resources: {', '.join(_listable)}"
    )


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Enumerate vault artefacts and other listable Brain resources."
    )
    parser.add_argument(
        "resource",
        nargs="?",
        default="artefact",
        choices=(
            "artefact",
            "workspace",
            "archive",
            "type",
            "template",
            "skill",
            "trigger",
            "style",
            "plugin",
            "memory",
        ),
        help="resource kind to list (default: artefact)",
    )
    parser.add_argument("--query", help="substring filter for non-artefact resources")
    parser.add_argument("--type", dest="type_filter", help="artefact type filter")
    parser.add_argument("--parent", help="artefact parent canonical key filter")
    parser.add_argument("--since", help="inclusive ISO start date filter (artefacts only)")
    parser.add_argument("--until", help="inclusive ISO end date filter (artefacts only)")
    parser.add_argument("--modified-since", help="inclusive modified-date lower bound")
    parser.add_argument("--modified-until", help="inclusive modified-date upper bound")
    parser.add_argument("--tag", help="artefact tag filter")
    parser.add_argument("--top-k", type=int, help="max artefact results")
    parser.add_argument(
        "--sort",
        choices=("date_desc", "date_asc", "modified_desc", "modified_asc", "title"),
        help="artefact sort order",
    )
    parser.add_argument("--cursor", help="continuation cursor from a prior list")
    parser.add_argument("--vault", help="explicit vault path")
    parser.add_argument("--json", action="store_true", help="emit structured JSON")
    return parser


def _validate_cli_args(args, parser):
    artefact_filters_used = any(
        value is not None
        for value in (
            args.type_filter,
            args.parent,
            args.since,
            args.until,
            args.modified_since,
            args.modified_until,
            args.tag,
            args.top_k,
            args.sort,
            args.cursor,
        )
    )

    if args.resource == "artefact":
        if args.query is not None:
            parser.error("resource='artefact' does not accept --query")
        return

    if artefact_filters_used:
        parser.error(
            f"resource='{args.resource}' does not accept artefact-only filters "
            "(--type, --parent, --since, --until, --modified-since, "
            "--modified-until, --tag, --top-k, --sort, --cursor)"
        )

    if args.resource in {"workspace", "archive"} and args.query is not None:
        parser.error(f"resource='{args.resource}' does not accept --query")


def _load_router_for_cli(vault_root):
    router = load_compiled_router(vault_root)
    if "error" in router:
        print(f"Error: {router['error']}", file=sys.stderr)
        raise SystemExit(1)
    return router


def _load_index_for_cli(vault_root):
    try:
        return lexical_query.load_index(vault_root)
    except lexical_query.IndexNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def _fmt_artefact_results(page, type_filter=None):
    results = page["items"]
    type_part = f" (type: {type_filter})" if type_filter else ""
    lines = [
        f"Listed: {page['returned']} of {page['total']} results{type_part}; "
        f"truncated={'yes' if page['truncated'] else 'no'}"
    ]
    if page["next_cursor"] is not None:
        lines[0] += f"; next_cursor={page['next_cursor']}"
    if page["omitted_missing_created"]:
        lines[0] += (
            f"; omitted_missing_created={page['omitted_missing_created']} "
            "(run brain doctor)"
        )
    if not results:
        return "\n".join(lines)
    body = []
    for result in results:
        status_part = f"\t{result['status']}" if result.get("status") else ""
        extras = []
        if result.get("key"):
            extras.append(f"key={result['key']}")
        if result.get("parent"):
            extras.append(f"parent={result['parent']}")
        if "children_count" in result:
            extras.append(f"children={result['children_count']}")
        extras_part = f"\t{', '.join(extras)}" if extras else ""
        body.append(
            f"{result['created']}\t{result['title']}\t{result['path']}\t"
            f"{result['type']}{status_part}{extras_part}"
        )
    return "\n".join([*lines, *body])


def _fmt_workspace_results(results):
    lines = [f"Listed: {len(results)} workspace(s)"]
    if not results:
        return "\n".join(lines)
    for result in results:
        status_part = f"\t[{result['status']}]" if result.get("status") else ""
        lines.append(f"{result['slug']}\t{result['mode']}\t{result['path']}{status_part}")
    return "\n".join(lines)


def _fmt_named_results(resource, results, query=None):
    lines = [f"Listed: {len(results)} {resource}(s)"]
    if query:
        lines[0] += f" matching '{query}'"
    if not results:
        return "\n".join(lines)
    for result in results:
        if isinstance(result, dict):
            lines.append(result.get("name", result.get("path", str(result))))
        else:
            lines.append(str(result))
    return "\n".join(lines)


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    _validate_cli_args(args, parser)

    vault_root = str(find_vault_root(args.vault))
    router = _load_router_for_cli(vault_root)
    index = {} if args.resource != "artefact" else _load_index_for_cli(vault_root)

    try:
        if args.resource == "artefact":
            results = list_artefacts_page(
                index,
                router,
                type_filter=args.type_filter,
                parent=args.parent,
                since=args.since,
                until=args.until,
                modified_since=args.modified_since,
                modified_until=args.modified_until,
                tag=args.tag,
                top_k=args.top_k or 500,
                sort=args.sort or "date_desc",
                cursor=args.cursor,
            )
        else:
            results = list_resources(
                index,
                router,
                vault_root,
                resource=args.resource,
                query=args.query,
            )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return

    if args.resource == "artefact":
        print(_fmt_artefact_results(results, args.type_filter))
    elif args.resource == "workspace":
        print(_fmt_workspace_results(results))
    else:
        print(_fmt_named_results(args.resource, results, args.query))


if __name__ == "__main__":
    main()
