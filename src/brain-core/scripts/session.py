#!/usr/bin/env python3
"""
session.py — Build the canonical bootstrap session model for agents.

The canonical model combines static brain-core bootstrap content, compiled
router state, user preference files, config, and runtime environment. The
MCP server renders it as JSON via `brain_session`; non-MCP flows use the
generated markdown mirror at `.brain/local/session.md`.

Usage:
    python3 session.py
    python3 session.py --vault /path/to/vault --json
    python3 session.py --context mcp-spike
"""

import json
import os
from pathlib import Path
import re
import sys

from _bootstrap.runtime import (
    handoff_current_script_to_managed_runtime,
    required_modules_for_scope,
)
from _bootstrap.workspace_binding import (
    WORKSPACE_MANIFEST_LEGACY_REL,
    WORKSPACE_MANIFEST_REL,
    WorkspaceBindingError,
    extract_workspace_binding,
    load_workspace_manifest_state,
)
from _common import (
    find_vault_root,
    parse_frontmatter,
    quote_arg,
    resolve_structural_target,
    safe_write,
)
from _lifecycle_common import emit_lifecycle_result
from _repair_common import build_repair_command

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PREFERENCES_REL = os.path.join("_Config", "User", "preferences-always.md")
GOTCHAS_REL = os.path.join("_Config", "User", "gotchas.md")
SESSION_CORE_REL = os.path.join(".brain-core", "session-core.md")
SESSION_MARKDOWN_REL = os.path.join(".brain", "local", "session.md")
COMPILED_ROUTER_REL = os.path.join(".brain", "local", "compiled-router.json")
CORE_DOC_SECTION_HEADINGS = ("Core Docs", "Standards")
BOOTSTRAP_TIMEOUT = 300

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class SessionCoreStructureError(ValueError):
    """Raised when the shipped authored bootstrap structure is malformed."""


class SessionConfigLoadError(RuntimeError):
    """Raised when the Brain config cannot be read safely for session bootstrap."""


class SessionWorkspaceLoadError(RuntimeError):
    """Raised when the active workspace manifest cannot be read safely."""

    def __init__(self, manifest_path: str, message: str):
        super().__init__(message)
        self.manifest_path = manifest_path


def _read_user_body(vault_root, rel_path):
    """Read a user file and return its body with frontmatter stripped.

    Returns "" if the file does not exist or is empty.
    """
    abs_path = os.path.join(vault_root, rel_path)
    try:
        with open(abs_path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return ""
    if not text.strip():
        return ""
    _, body = parse_frontmatter(text)
    return body.strip()


def _read_text(vault_root, rel_path):
    """Read a file relative to vault_root and return stripped text."""
    abs_path = os.path.join(vault_root, rel_path)
    try:
        with open(abs_path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _strip_first_heading(text):
    """Drop a leading H1 so rendered markdown can supply its own title."""
    return re.sub(r"\A# .+?(?:\n+|$)", "", text, count=1, flags=re.DOTALL).lstrip()


def _strip_always_section(text):
    """Remove a router-style Always: section from authored core bootstrap."""
    return re.sub(
        r"\n*Always:\n(?:- .*(?:\n|$))+",
        "\n",
        text,
        count=1,
        flags=re.MULTILINE,
    ).strip()


def _extract_markdown_section(text, heading):
    """Return the body of a required H2 section in session-core.

    Raises ValueError if the section is missing or duplicated — both indicate
    a malformed core install rather than a runtime data condition.
    """
    try:
        resolved = resolve_structural_target(text, f"## {heading}")
    except ValueError as exc:
        raise SessionCoreStructureError(str(exc)) from exc
    start, end = resolved["ranges"]["body"]
    return text[start:end].strip()


def _strip_markdown_section(text, heading):
    """Remove a required H2 section from session-core.

    Raises ValueError if the section is missing or duplicated — both indicate
    a malformed core install rather than a runtime data condition.
    """
    try:
        resolved = resolve_structural_target(text, f"## {heading}")
    except ValueError as exc:
        raise SessionCoreStructureError(str(exc)) from exc
    start, end = resolved["ranges"]["section"]
    stripped = text[:start] + text[end:]
    return re.sub(r"\n{3,}", "\n\n", stripped).strip()


def _load_session_core_body(vault_root):
    """Return authored session-core content without the file heading or Always block."""
    core_text = _read_text(vault_root, SESSION_CORE_REL)
    if not core_text:
        return ""
    return _strip_always_section(_strip_first_heading(core_text))


def _load_config_if_available(vault_root):
    """Load merged config when the config module is available."""
    try:
        import config as config_mod
    except ImportError:
        return None
    from _common._yaml import YamlError

    try:
        return config_mod.load_config(vault_root)
    except (OSError, YamlError) as exc:
        raise SessionConfigLoadError(f"failed to load config for {vault_root}: {exc}") from exc


def _workspace_summary(workspace_dir, vault_root):
    """Return stable workspace metadata from an optional directory path."""
    if not workspace_dir:
        return None
    directory = os.path.abspath(os.path.expanduser(str(workspace_dir)))
    if not directory:
        return None
    name = os.path.basename(os.path.normpath(directory)) or directory
    embedded_root = os.path.abspath(os.path.join(str(vault_root), "_Workspaces"))
    location = "external"
    if os.path.commonpath([embedded_root, directory]) == embedded_root:
        location = "embedded"
    return {
        "directory": directory,
        "name": name,
        "location": location,
    }


def _json_safe(value):
    """Convert YAML-loaded values into JSON-safe structures."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(val) for key, val in value.items()}
    return str(value)


def _load_workspace_manifest(workspace_dir):
    """Load the active workspace manifest via the shared binding helper."""
    if not workspace_dir:
        return None

    ws_abs = Path(os.path.abspath(os.path.expanduser(str(workspace_dir))))
    try:
        state = load_workspace_manifest_state(ws_abs)
    except WorkspaceBindingError as exc:
        manifest_path = os.path.join(str(ws_abs), WORKSPACE_MANIFEST_REL)
        raise SessionWorkspaceLoadError(
            manifest_path,
            f"failed to load workspace manifest {manifest_path}: {exc}",
        ) from exc

    if state.source_path is None:
        return None
    if state.source_path == state.legacy_path:
        print(
            f"Warning: workspace manifest found at legacy location "
            f"{WORKSPACE_MANIFEST_LEGACY_REL} — move it to "
            f"{WORKSPACE_MANIFEST_REL}",
            file=sys.stderr,
        )
    return state.data if isinstance(state.data, dict) else None


def _extract_workspace_defaults(manifest):
    """Return workspace-owned filing defaults from the manifest."""
    if not isinstance(manifest, dict):
        return None
    defaults = manifest.get("defaults")
    if not isinstance(defaults, dict) or not defaults:
        return None
    return _json_safe(defaults)


def _workspace_record_from_entry(entry):
    """Project a workspace list entry into the bootstrap session shape."""
    record = {
        "slug": entry.get("slug", ""),
        "workspace_mode": entry.get("mode", ""),
    }
    if entry.get("hub_path"):
        record["hub_path"] = entry["hub_path"]
    if entry.get("tags"):
        record["tags"] = entry["tags"]
    return record


def _resolve_workspace_record(vault_root, workspace, manifest):
    """Resolve optional canonical workspace metadata when it is safe to do so."""
    if not workspace:
        return None

    try:
        import workspace_registry
    except ImportError:
        workspace_registry = None

    entries = []
    if workspace_registry is not None:
        try:
            entries = workspace_registry.list_workspaces(vault_root)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"Warning: failed to read linked workspace registry: {exc}", file=sys.stderr)
            entries = []

    directory = os.path.abspath(workspace["directory"])
    for entry in entries:
        entry_path = entry.get("path")
        if not entry_path:
            continue
        if os.path.abspath(os.path.expanduser(entry_path)) == directory:
            return _workspace_record_from_entry(entry)

    links = manifest.get("links") if isinstance(manifest, dict) else None
    linked_slug = links.get("workspace") if isinstance(links, dict) else None
    if not linked_slug:
        return None

    for entry in entries:
        if entry.get("slug") == linked_slug:
            return _workspace_record_from_entry(entry)

    return {
        "slug": str(linked_slug),
        "workspace_mode": (
            "embedded"
            if workspace.get("location") == "embedded"
            else "linked"
        ),
    }


def _load_core_bootstrap(core_body):
    """Return static, authored bootstrap content with routing-only sections removed."""
    if not core_body:
        return ""
    text = core_body
    for heading in CORE_DOC_SECTION_HEADINGS:
        text = _strip_markdown_section(text, heading)
    return text.strip()


def _normalise_doc_path(raw_link):
    """Normalise a markdown or wikilink doc target to a vault-relative `.md` path."""
    target = raw_link.strip().strip("`")
    markdown_link = False

    wikilink_match = re.fullmatch(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", target)
    if wikilink_match:
        target = wikilink_match.group(1)
    else:
        markdown_match = re.fullmatch(r"\[[^\]]+\]\(([^)]+)\)", target)
        if markdown_match:
            markdown_link = True
            target = markdown_match.group(1).strip()

    target = target.strip("<>").strip()
    if target.startswith("./"):
        target = target[2:]
    if markdown_link and not target.startswith("/"):
        target = os.path.normpath(os.path.join(os.path.dirname(SESSION_CORE_REL), target))
    else:
        target = target.lstrip("/")
    if not target.endswith(".md"):
        target += ".md"
    return target


def _load_core_docs(core_body):
    """Return structured session-core doc references with MCP load instructions."""
    if not core_body:
        return []

    sections = []
    for heading in CORE_DOC_SECTION_HEADINGS:
        body = _extract_markdown_section(core_body, heading)
        if not body:
            continue

        docs = []
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped.startswith("- "):
                continue
            item = stripped[2:]
            title = None
            raw_link = None
            if " — " in item:
                title, raw_link = item.split(" — ", 1)
            else:
                markdown_match = re.fullmatch(r"\[([^\]]+)\]\(([^)]+)\)", item)
                if markdown_match:
                    title = markdown_match.group(1)
                    raw_link = item
                else:
                    wikilink_match = re.fullmatch(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", item)
                    if wikilink_match:
                        raw_link = wikilink_match.group(0)
                        title = wikilink_match.group(2) or wikilink_match.group(1)
            if not title or not raw_link:
                continue
            path = _normalise_doc_path(raw_link)
            docs.append(
                {
                    "title": title.strip(),
                    "path": path,
                    "load_with": {
                        "tool": "brain_read",
                        "resource": "file",
                        "name": path,
                    },
                }
            )

        if docs:
            sections.append({"section": heading, "docs": docs})

    return sections


def _condense_artefacts(artefacts):
    """Extract the fields agents need from full artefact entries."""
    condensed = []
    for a in artefacts:
        naming = a.get("naming") or {}
        fm = a.get("frontmatter") or {}
        pattern = naming.get("pattern")
        if pattern is None:
            rule_patterns = [r.get("pattern") for r in (naming.get("rules") or []) if r.get("pattern")]
            pattern = " | ".join(rule_patterns) if rule_patterns else None
        condensed.append({
            "type": a.get("type"),
            "key": a.get("key"),
            "path": a.get("path"),
            "naming_pattern": pattern,
            "status_enum": fm.get("status_enum"),
            "configured": a.get("configured", False),
        })
    return condensed


def _condense_memories(memories):
    """Extract name and triggers only."""
    return [{"name": m["name"], "triggers": m.get("triggers", [])} for m in memories]


def _condense_skills(skills):
    """Extract name and source only."""
    return [{"name": s["name"], "source": s.get("source", "user")} for s in skills]


def _condense_plugins(plugins):
    """Extract name only."""
    return [{"name": p["name"]} for p in plugins]


def _extract_style_names(styles):
    """Extract just the style names."""
    return [s["name"] for s in styles]


def _summarise_config(config):
    """Extract config fields relevant to bootstrap."""
    if not config:
        return None
    vault_cfg = config.get("vault", {})
    defaults_cfg = config.get("defaults", {})
    profiles = list(vault_cfg.get("profiles", {}).keys())
    return {
        "brain_name": vault_cfg.get("brain_name", ""),
        "default_profile": defaults_cfg.get("default_profile", "operator"),
        "profiles": profiles,
    }


def _resolve_active_profile(config, active_profile):
    """Return the active profile when bootstrap can know it."""
    if active_profile:
        return active_profile
    if not config:
        return None
    return config.get("defaults", {}).get("default_profile", "operator")


def _format_scalar(value):
    """Render a scalar or simple structure for markdown output."""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _render_bullets(items, formatter=str, empty="_None._"):
    """Render a bullet list or an empty placeholder."""
    if not items:
        return empty
    return "\n".join(f"- {formatter(item)}" for item in items)


def _render_named_list(items, empty="_None._"):
    """Render name-first list items for skills, memories, and plugins."""
    if not items:
        return empty
    lines = []
    for item in items:
        if "triggers" in item:
            triggers = ", ".join(item.get("triggers", [])) or "-"
            lines.append(f"- `{item['name']}` — triggers: {triggers}")
        elif "source" in item:
            lines.append(f"- `{item['name']}` — source: `{item.get('source', 'user')}`")
        else:
            lines.append(f"- `{item['name']}`")
    return "\n".join(lines)


def _escape_table_cell(value):
    """Escape markdown table cell content."""
    text = _format_scalar(value)
    return text.replace("|", r"\|").replace("\n", "<br>")


def _render_triggers(triggers):
    """Render trigger summaries for the markdown mirror."""
    if not triggers:
        return "_None._"
    lines = []
    for trigger in triggers:
        category = trigger.get("category", "ongoing")
        condition = trigger.get("condition", "")
        target = trigger.get("target")
        detail = trigger.get("detail")
        line = f"- `[{category}]` {condition}"
        if target:
            line += f" -> `{target}`"
        lines.append(line)
        if detail and detail != condition:
            lines.append(f"  {detail}")
    return "\n".join(lines)


def _doc_link_target(path):
    """Render a markdown link target from `.brain/local/session.md` to a vault file."""
    rel = os.path.relpath(path, start=os.path.dirname(SESSION_MARKDOWN_REL))
    return rel.replace(os.sep, "/")


def _render_doc_links(docs):
    """Render core-doc references as markdown links."""
    if not docs:
        return "_None._"
    return "\n".join(
        f"- [{doc['title']}]({_doc_link_target(doc['path'])})"
        for doc in docs
    )


def _render_artefacts_table(artefacts):
    """Render condensed artefact metadata as a markdown table."""
    if not artefacts:
        return "_None._"
    lines = [
        "| Key | Type | Path | Naming | Status | Configured |",
        "|---|---|---|---|---|---|",
    ]
    for artefact in artefacts:
        lines.append(
            "| "
            + " | ".join([
                _escape_table_cell(artefact.get("key")),
                _escape_table_cell(artefact.get("type")),
                _escape_table_cell(artefact.get("path")),
                _escape_table_cell(artefact.get("naming_pattern")),
                _escape_table_cell(", ".join(artefact.get("status_enum") or [])),
                _escape_table_cell("yes" if artefact.get("configured") else "no"),
            ])
            + " |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main compilation
# ---------------------------------------------------------------------------


def build_session_model(
    router,
    vault_root,
    obsidian_cli_available=False,
    context=None,
    workspace_dir=None,
    config=None,
    active_profile=None,
    load_config_if_missing=True,
):
    """Build the canonical session model from router + authored bootstrap sources.

    Args:
        router: The compiled router dict (in-memory or loaded from JSON).
        vault_root: Absolute path to the vault root.
        obsidian_cli_available: Whether the Obsidian REST CLI is reachable.
        context: Optional context slug for scoped sessions (not yet implemented).
        workspace_dir: Optional active workspace directory for this session.
        config: Optional merged config dict from config.load_config().
        active_profile: Optional resolved active profile for this bootstrap flow.
        load_config_if_missing: Whether to lazily load config from disk when the
            caller did not supply it.

    Returns:
        dict with the canonical session model.
    """
    if config is None and load_config_if_missing:
        config = _load_config_if_available(vault_root)

    meta = router.get("meta", {})
    env = dict(router.get("environment", {}))
    env["obsidian_cli_available"] = obsidian_cli_available
    config_summary = _summarise_config(config)
    resolved_profile = _resolve_active_profile(config, active_profile)
    workspace_summary = _workspace_summary(workspace_dir, vault_root)
    workspace_manifest = _load_workspace_manifest(workspace_dir)
    workspace_defaults = _extract_workspace_defaults(workspace_manifest)
    workspace_binding = extract_workspace_binding(workspace_manifest)
    workspace_record = _resolve_workspace_record(
        vault_root,
        workspace_summary,
        workspace_manifest,
    )
    core_body = _load_session_core_body(vault_root)

    model = {
        "version": "1",
        "brain_core_version": meta.get("brain_core_version", ""),
        "compiled_at": meta.get("compiled_at", ""),
        "core_bootstrap": _load_core_bootstrap(core_body),
        "core_docs": _load_core_docs(core_body),
        "always_rules": router.get("always_rules", []),
        "preferences": _read_user_body(vault_root, PREFERENCES_REL),
        "gotchas": _read_user_body(vault_root, GOTCHAS_REL),
        "triggers": router.get("triggers", []),
        "artefacts": _condense_artefacts(router.get("artefacts", [])),
        "environment": env,
        "memories": _condense_memories(router.get("memories", [])),
        "skills": _condense_skills(router.get("skills", [])),
        "plugins": _condense_plugins(router.get("plugins", [])),
        "styles": _extract_style_names(router.get("styles", [])),
    }

    if config_summary:
        model["config"] = config_summary
    if resolved_profile:
        model["active_profile"] = resolved_profile
    if workspace_summary:
        model["workspace"] = workspace_summary
    if workspace_binding:
        model["workspace_binding"] = workspace_binding
    if workspace_record:
        model["workspace_record"] = workspace_record
    if workspace_defaults:
        model["workspace_defaults"] = workspace_defaults

    if context is not None:
        model["context"] = {
            "slug": context,
            "status": "not_implemented",
            "message": (
                "Context scoping is not yet implemented. "
                "The general bootstrap payload has been returned. "
                "Context-aware sessions will be available in a future version."
            ),
        }

    return model


def render_session_markdown(model):
    """Render the canonical session model as markdown."""
    sections = [
        "<!-- Generated by .brain-core/scripts/session.py. Do not edit directly. -->",
        "",
        "# Brain Session",
        "",
        f"**brain-core version:** `{model.get('brain_core_version', '')}`",
        f"**compiled at:** `{model.get('compiled_at', '')}`",
    ]

    core_bootstrap = model.get("core_bootstrap", "").strip()
    if core_bootstrap:
        sections.extend(["", core_bootstrap])

    for section in model.get("core_docs", []):
        sections.extend(
            [
                "",
                f"## {section.get('section', 'Core Docs')}",
                "",
                _render_doc_links(section.get("docs", [])),
            ]
        )

    sections.extend([
        "",
        "## Always Rules",
        "",
        _render_bullets(model.get("always_rules", [])),
        "",
        "## Preferences",
        "",
        model.get("preferences", "").strip() or "_None._",
        "",
        "## Gotchas",
        "",
        model.get("gotchas", "").strip() or "_None._",
        "",
        "## Triggers",
        "",
        _render_triggers(model.get("triggers", [])),
        "",
        "## Artefacts",
        "",
        _render_artefacts_table(model.get("artefacts", [])),
        "",
        "## Environment",
        "",
        _render_bullets(
            sorted((model.get("environment") or {}).items()),
            formatter=lambda item: f"`{item[0]}`: `{_format_scalar(item[1])}`",
        ),
    ])

    workspace = model.get("workspace")
    if workspace:
        sections.extend([
            "",
            "## Workspace",
            "",
            _render_bullets(
                [
                    ("name", workspace.get("name", "")),
                    ("directory", workspace.get("directory", "")),
                    ("location", workspace.get("location", "")),
                ],
                formatter=lambda item: f"`{item[0]}`: `{_format_scalar(item[1])}`",
            ),
        ])

    workspace_record = model.get("workspace_record")
    if workspace_record:
        sections.extend([
            "",
            "## Workspace Record",
            "",
            _render_bullets(
                workspace_record.items(),
                formatter=lambda item: f"`{item[0]}`: `{_format_scalar(item[1])}`",
            ),
        ])

    workspace_defaults = model.get("workspace_defaults")
    if workspace_defaults:
        sections.extend([
            "",
            "## Workspace Defaults",
            "",
            _render_bullets(
                workspace_defaults.items(),
                formatter=lambda item: f"`{item[0]}`: `{_format_scalar(item[1])}`",
            ),
        ])

    sections.extend([
        "",
        "## Memories",
        "",
        _render_named_list(model.get("memories", [])),
        "",
        "## Skills",
        "",
        _render_named_list(model.get("skills", [])),
        "",
        "## Plugins",
        "",
        _render_named_list(model.get("plugins", [])),
        "",
        "## Styles",
        "",
        _render_bullets(model.get("styles", []), formatter=lambda item: f"`{item}`"),
    ])

    config = model.get("config")
    if config:
        sections.extend([
            "",
            "## Config",
            "",
            _render_bullets(
                [
                    ("brain_name", config.get("brain_name", "")),
                    ("default_profile", config.get("default_profile", "")),
                    ("profiles", ", ".join(config.get("profiles", []))),
                ],
                formatter=lambda item: f"`{item[0]}`: `{_format_scalar(item[1])}`",
            ),
        ])

    active_profile = model.get("active_profile")
    if active_profile:
        sections.extend([
            "",
            "## Active Profile",
            "",
            f"`{active_profile}`",
        ])

    context = model.get("context")
    if context:
        sections.extend([
            "",
            "## Context",
            "",
            _render_bullets(
                [
                    ("slug", context.get("slug", "")),
                    ("status", context.get("status", "")),
                    ("message", context.get("message", "")),
                ],
                formatter=lambda item: f"`{item[0]}`: `{_format_scalar(item[1])}`",
            ),
        ])

    return "\n".join(sections).rstrip() + "\n"


def persist_session_markdown(model, vault_root):
    """Write the markdown session mirror to `.brain/local/session.md`."""
    output_path = os.path.join(vault_root, SESSION_MARKDOWN_REL)
    safe_write(output_path, render_session_markdown(model), bounds=vault_root)


def _bootstrap_reinstall_command(vault_root) -> str:
    if sys.platform == "win32":
        vault_arg = quote_arg(str(vault_root))
        return (
            "python <obsidian-brain>\\src\\brain-core\\scripts\\upgrade.py "
            "--source <obsidian-brain>\\src\\brain-core "
            f"--vault {vault_arg}"
        )
    return f"bash install.sh --non-interactive --skip-mcp {quote_arg(str(vault_root))}"


def _config_inspect_command(vault_root) -> str:
    shared = quote_arg(str(Path(vault_root) / ".brain" / "config.yaml"))
    local = quote_arg(str(Path(vault_root) / ".brain" / "local" / "config.yaml"))
    return f"cat {shared} {local}"


def _workspace_manifest_inspect_command(manifest_path: str) -> str:
    return f"cat {quote_arg(manifest_path)}"


_RECOVERY_BY_CLASS = {
    "runtime": {
        "command": lambda vault_root, _detail=None: build_repair_command(vault_root, "runtime"),
        "action": "Repair the canonical managed runtime, then rerun session.py.",
    },
    "router": {
        "command": lambda vault_root, _detail=None: build_repair_command(vault_root, "router"),
        "action": "Rebuild the compiled router, then rerun session.py.",
    },
    "session_core": {
        "command": lambda vault_root, _detail=None: _bootstrap_reinstall_command(vault_root),
        "action": "Reinstall or upgrade the shipped .brain-core files for this vault, then rerun session.py.",
    },
    "config": {
        "command": lambda vault_root, _detail=None: _config_inspect_command(vault_root),
        "action": "Inspect and fix the broken Brain config file, then rerun session.py.",
    },
    "workspace": {
        "command": lambda _vault_root, detail=None: _workspace_manifest_inspect_command(str(detail)),
        "action": "Inspect and fix the broken workspace manifest, then rerun session.py.",
    },
}


def _session_error_payload(
    vault_root,
    *,
    workspace_dir=None,
    message,
    recovery_class,
    recovery_detail=None,
):
    recovery = _RECOVERY_BY_CLASS[recovery_class]
    payload = {
        "status": "degraded",
        "vault_root": str(vault_root),
        "message": message,
        "recovery": {
            "command": recovery["command"](vault_root, recovery_detail),
            "action": recovery["action"],
        },
    }
    if workspace_dir:
        payload["workspace_dir"] = str(workspace_dir)
    return payload


def _runtime_degraded_session_payload(vault_root, *, workspace_dir=None, message):
    return _session_error_payload(
        vault_root,
        workspace_dir=workspace_dir,
        message=message,
        recovery_class="runtime",
    )


def _router_degraded_session_payload(vault_root, *, workspace_dir=None, message):
    return _session_error_payload(
        vault_root,
        workspace_dir=workspace_dir,
        message=message,
        recovery_class="router",
    )


def _session_core_degraded_session_payload(vault_root, *, workspace_dir=None, message):
    return _session_error_payload(
        vault_root,
        workspace_dir=workspace_dir,
        message=message,
        recovery_class="session_core",
    )


def _config_degraded_session_payload(vault_root, *, workspace_dir=None, message):
    return _session_error_payload(
        vault_root,
        workspace_dir=workspace_dir,
        message=message,
        recovery_class="config",
    )


def _workspace_degraded_session_payload(
    vault_root,
    *,
    workspace_dir=None,
    message,
    manifest_path: str,
):
    return _session_error_payload(
        vault_root,
        workspace_dir=workspace_dir,
        message=message,
        recovery_class="workspace",
        recovery_detail=manifest_path,
    )


def _render_degraded_session_payload(payload: dict) -> str:
    return "\n".join(
        [
            f"Error: {payload['message']}",
            payload["recovery"]["action"],
            payload["recovery"]["command"],
        ]
    )


def _emit_degraded_session(payload: dict, *, as_json: bool) -> int:
    emit_lifecycle_result(
        payload,
        as_json=as_json,
        render_human=_render_degraded_session_payload,
        stream=sys.stdout if as_json else sys.stderr,
    )
    return 2


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Compile the canonical session bootstrap model and refresh "
            ".brain/local/session.md"
        )
    )
    parser.add_argument("--vault", help="Vault root (auto-detected if omitted)")
    parser.add_argument("--context", help="Optional context slug")
    parser.add_argument("--workspace-dir", help="Optional active workspace directory")
    parser.add_argument(
        "--project-dir",
        dest="workspace_dir_deprecated",
        help="Deprecated alias for --workspace-dir",
    )
    parser.add_argument("--json", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args()

    vault_root = args.vault or find_vault_root()
    if not vault_root:
        print("Error: could not find vault root", file=sys.stderr)
        sys.exit(1)

    workspace_dir = args.workspace_dir or args.workspace_dir_deprecated
    try:
        handoff_current_script_to_managed_runtime(
            vault_root,
            dependency_owner="session.py",
            required_modules=required_modules_for_scope("runtime"),
            script_path=os.path.abspath(__file__),
            forwarded_args=sys.argv[1:],
            timeout=BOOTSTRAP_TIMEOUT,
        )
    except RuntimeError as exc:
        payload = _runtime_degraded_session_payload(
            vault_root,
            workspace_dir=workspace_dir,
            message=str(exc),
        )
        return _emit_degraded_session(payload, as_json=args.json)

    router_path = os.path.join(vault_root, COMPILED_ROUTER_REL)
    if not os.path.isfile(router_path):
        payload = _router_degraded_session_payload(
            vault_root,
            workspace_dir=workspace_dir,
            message="compiled router not found — run compile_router.py first",
        )
        return _emit_degraded_session(payload, as_json=args.json)

    try:
        with open(router_path, encoding="utf-8") as f:
            router = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        payload = _router_degraded_session_payload(
            vault_root,
            workspace_dir=workspace_dir,
            message=str(exc),
        )
        return _emit_degraded_session(payload, as_json=args.json)

    try:
        result = build_session_model(
            router,
            vault_root,
            context=args.context,
            workspace_dir=workspace_dir,
        )
    except SessionCoreStructureError as exc:
        payload = _session_core_degraded_session_payload(
            vault_root,
            workspace_dir=workspace_dir,
            message=str(exc),
        )
        return _emit_degraded_session(payload, as_json=args.json)
    except SessionConfigLoadError as exc:
        payload = _config_degraded_session_payload(
            vault_root,
            workspace_dir=workspace_dir,
            message=str(exc),
        )
        return _emit_degraded_session(payload, as_json=args.json)
    except SessionWorkspaceLoadError as exc:
        payload = _workspace_degraded_session_payload(
            vault_root,
            workspace_dir=workspace_dir,
            message=str(exc),
            manifest_path=exc.manifest_path,
        )
        return _emit_degraded_session(payload, as_json=args.json)
    except (OSError, RuntimeError) as exc:
        payload = _runtime_degraded_session_payload(
            vault_root,
            workspace_dir=workspace_dir,
            message=str(exc),
        )
        return _emit_degraded_session(payload, as_json=args.json)

    try:
        persist_session_markdown(result, vault_root)
    except OSError as exc:
        payload = _runtime_degraded_session_payload(
            vault_root,
            workspace_dir=workspace_dir,
            message=str(exc),
        )
        return _emit_degraded_session(payload, as_json=args.json)

    indent = 2 if args.json else None
    print(json.dumps(result, indent=indent, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
