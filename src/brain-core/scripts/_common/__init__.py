"""
_common — Shared utilities for brain-core scripts.

Provides vault root discovery, version reading, filesystem scanning,
frontmatter parsing, serialisation, slug generation, and BM25 tokenisation.
All brain-core scripts import from this module rather than duplicating
these functions.

This package re-exports all public names from its internal modules.
"""

from ._vault import (
    TEMPORAL_DIR,
    find_vault_root,
    is_archived_path,
    is_system_dir,
    match_artefact,
    read_version,
    scan_living_types,
    scan_temporal_types,
)

from ._router import (
    COMPILED_ROUTER_REL,
    extract_title_from_naming_pattern,
    load_compiled_router,
    naming_pattern_to_regex,
    resolve_and_validate_folder,
    validate_artefact_folder,
    validate_artefact_naming,
    validate_artefact_path,
)

from ._artefacts import (
    config_resource_rel_path,
    read_file_content,
    resolve_folder,
    resolve_naming_pattern,
    resolve_type,
)

from ._filesystem import (
    check_not_in_brain_core,
    check_write_allowed,
    make_temp_path,
    resolve_and_check_bounds,
    resolve_body_file,
    safe_write,
    safe_write_json,
)

from ._frontmatter import (
    FM_RE,
    parse_frontmatter,
    serialize_frontmatter,
)

from ._wikilinks import (
    INDEX_SKIP_DIRS,
    Resolution,
    build_vault_file_index,
    build_wikilink_pattern,
    discover_temporal_prefixes,
    extract_wikilinks,
    find_duplicate_basenames,
    make_wikilink_replacer,
    replace_wikilinks_in_vault,
    resolve_artefact_path,
    resolve_broken_link,
    resolve_wikilink_stems,
    strip_md_ext,
    temporal_display_name,
)

from ._markdown import (
    collect_headings,
    fenced_ranges,
    find_section,
    parse_structural_anchor_line,
)

from ._slugs import (
    slug_to_title,
    title_to_filename,
    title_to_slug,
)

from ._search import (
    tokenise,
)

from ._templates import (
    now_iso,
    substitute_template_vars,
    unique_filename,
)
