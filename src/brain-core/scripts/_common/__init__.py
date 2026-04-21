"""
_common — Shared utilities for brain-core scripts.

Provides vault root discovery, version reading, filesystem scanning,
frontmatter parsing, serialisation, slug generation, and BM25 tokenisation.
All brain-core scripts import from this module rather than duplicating
these functions.

This package re-exports all public names from its internal modules.
"""

from ._vault import (
    BOOTSTRAP_VARIANTS,
    LOCAL_OVERRIDE_VARIANTS,
    TEMPORAL_DIR,
    find_vault_root,
    find_root_bootstrap_file,
    is_archived_path,
    is_system_dir,
    is_vault_root,
    match_artefact,
    read_version,
    scan_living_types,
    scan_temporal_types,
)

from ._router import (
    COMPILED_ROUTER_REL,
    load_compiled_router,
    resolve_and_validate_folder,
    validate_artefact_folder,
)

from ._artefacts import (
    config_resource_rel_path,
    parse_date_value,
    read_file_content,
    resolve_folder,
    resolve_naming_pattern,
    resolve_type,
)

from ._naming import (
    PLACEHOLDER_TOKEN_RE,
    extract_title,
    render_filename,
    render_filename_or_default,
    select_rule,
    validate_filename,
)

from ._filesystem import (
    check_not_in_brain_core,
    check_write_allowed,
    make_temp_path,
    resolve_and_check_bounds,
    resolve_body_file,
    safe_write,
    safe_write_via,
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
    check_wikilinks_in_file,
    discover_temporal_prefixes,
    extract_wikilinks,
    find_duplicate_basenames,
    make_wikilink_replacer,
    replace_wikilinks_in_text,
    replace_wikilinks_in_vault,
    resolve_artefact_path,
    resolve_broken_link,
    resolve_wikilink_stems,
    strip_md_ext,
    temporal_display_name,
)

from ._markdown import (
    REGION_FENCE,
    REGION_HTML_COMMENT,
    REGION_INLINE_CODE,
    REGION_MATH_BLOCK,
    REGION_RAW_HTML,
    collect_headings,
    fenced_ranges,
    find_body_preamble,
    find_section,
    html_comment_ranges,
    inline_code_ranges,
    markdown_region_ranges,
    math_block_ranges,
    parse_structural_anchor_line,
    raw_html_block_ranges,
)

from ._slugs import (
    slug_to_title,
    title_to_filename,
    title_to_slug,
)

from ._search import (
    tokenise,
)

from ._coerce import (
    coerce_bool,
)

from ._templates import (
    now_iso,
    random_short_suffix,
    substitute_template_vars,
    unique_filename,
)

from ._reconcile import (
    reconcile_date_source,
    reconcile_fields_for_render,
    reconcile_timestamps,
)
