"""Compiled router loading and artefact path validation helpers."""

import json
import os

from ._artefacts import normalize_artefact_key, resolve_artefact_key_entry
from ._wikilinks import resolve_artefact_path


COMPILED_ROUTER_REL = os.path.join(".brain", "local", "compiled-router.json")


def load_compiled_router(vault_root):
    """Load compiled router JSON. Returns dict or error dict."""
    router_path = os.path.join(str(vault_root), COMPILED_ROUTER_REL)
    if not os.path.isfile(router_path):
        return {"error": f"Compiled router not found at {COMPILED_ROUTER_REL}. Run compile_router.py first."}
    try:
        with open(router_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return {"error": f"Failed to read compiled router: {e}"}


def validate_artefact_folder(vault_root, router, path):
    """Validate path belongs to a known, configured type folder."""
    vault_root = str(vault_root)

    for art in router.get("artefacts", []):
        art_path = art["path"]
        if path.startswith(art_path + os.sep) or path.startswith(art_path + "/"):
            if not art.get("configured"):
                raise ValueError(
                    f"Path '{path}' belongs to unconfigured type '{art['key']}'. "
                    f"Create a taxonomy file first."
                )
            return art

    known_paths = [a["path"] for a in router.get("artefacts", [])]
    raise ValueError(
        f"Path '{path}' does not belong to any known artefact folder. "
        f"Known: {', '.join(known_paths)}"
    )


def resolve_and_validate_folder(vault_root, router, path):
    """Validate path belongs to a known artefact folder, falling back to basename resolution."""
    key = normalize_artefact_key(path)
    if key:
        entry = resolve_artefact_key_entry(router, key)
        if not entry:
            _name_encoded_identity(vault_root, router, key)
            raise ValueError(f"No artefact matching key '{path}'")
        art = validate_artefact_folder(vault_root, router, entry["path"])
        return entry["path"], art

    if not path.endswith(".md"):
        path += ".md"
    try:
        art = validate_artefact_folder(vault_root, router, path)
        return path, art
    except ValueError:
        resolved = resolve_artefact_path(path, vault_root)
        art = validate_artefact_folder(vault_root, router, resolved)
        return resolved, art


def _name_encoded_identity(vault_root, router, key):
    """Diagnose an encoded source omitted from a strict canonical index."""
    from ._artefacts import (resolve_artefact_definition_for_prefix, iter_artefact_paths,
                            read_exact_file_content, canonical_living_artefact_key)
    from ._frontmatter import parse_frontmatter
    from ._document_revision import NonStandardVaultTextError
    artefact = resolve_artefact_definition_for_prefix(router, key.split("/", 1)[0])
    if artefact is None:
        return
    for path in iter_artefact_paths(vault_root, artefact, include_status_folders=True):
        content = read_exact_file_content(os.path.join(vault_root, path), convert_lossless=True)
        fields, _ = parse_frontmatter(content)
        if canonical_living_artefact_key(artefact, fields) == key and content.conversion_code:
            raise NonStandardVaultTextError(path, content.conversion_code)
