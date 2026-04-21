#!/usr/bin/env python3
"""
compile_colours.py — Brain-core CSS colour generator

Reads the compiled router to discover artefact types, distributes hues evenly
across available colour space (avoiding system-reserved zones), and generates
`.obsidian/snippets/brain-folder-colours.css`.

Algorithm:
  1. Four system colours occupy exclusion zones (±15° each, 120° total)
  2. Living types get evenly-spaced hues across the remaining 240°
  3. Temporal types get independent hue distribution, then rose blend
  4. Deterministic: same sorted type list always produces the same colours

Usage:
    python3 compile_colours.py              # write brain-folder-colours.css
    python3 compile_colours.py --json       # output colour assignments to stdout
    python3 compile_colours.py --dry-run    # print CSS to stdout without writing
    python3 compile_colours.py --vault /path/to/vault
"""

import json
import math
import os
import sys
from datetime import datetime, timezone

from _common import find_vault_root, load_compiled_router, safe_write, safe_write_json


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# System colour exclusion zones (hue ranges in degrees)
# Each zone is ±15° around the system colour's hue
EXCLUSION_ZONES = [
    (150, 180),   # Teal / Workspaces    (hue ~165°)
    (195, 225),   # Slate / Assets       (hue ~210°)
    (255, 285),   # Violet / Config      (hue ~270°)
    (285, 315),   # Orchid / Plugins     (hue ~300°)
    (325, 355),   # Rose / Temporal      (hue ~340°)
]

PASTEL_S = 0.57
PASTEL_L = 0.72

# System palette hex values — single source of truth for CSS and graph view
PALETTE_TEAL = "#8ABCB0"
PALETTE_SLATE = "#A0B0C0"
PALETTE_ROSE = "#F2A8C4"
PALETTE_VIOLET = "#C4A8E8"
PALETTE_ORCHID = "#DBA8D6"

ROSE_RGB = (242, 168, 196)
ROSE_BLEND_FACTOR = 0.35

OUTPUT_REL = os.path.join(".obsidian", "snippets", "brain-folder-colours.css")
GRAPH_JSON_REL = os.path.join(".obsidian", "graph.json")
# ---------------------------------------------------------------------------
# Hue distribution algorithm
# ---------------------------------------------------------------------------

def compute_available_arcs(exclusion_zones):
    """Build list of (start, end) arcs in degrees that are NOT excluded.

    Works in 0–360° space. Returns arcs sorted by start degree.
    """
    # Sort exclusion zones
    zones = sorted(exclusion_zones)

    if not zones:
        return [(0, 360)]

    # Build gaps between exclusion zones
    arcs = []
    # Start from the end of the last zone (wrapping around 360°)
    prev_end = zones[-1][1] % 360

    for zone_start, zone_end in zones:
        # Arc from previous zone end to this zone start
        if prev_end != zone_start:
            if prev_end < zone_start:
                arcs.append((prev_end, zone_start))
            else:
                # Wraps around 360°
                arcs.append((prev_end, zone_start + 360))
        prev_end = zone_end % 360

    return arcs


def _arc_length(arc):
    """Length of an arc in degrees."""
    return arc[1] - arc[0]


def distribute_hues(n, available_arcs):
    """Evenly space N hues across available arcs.

    Returns list of N hue values in [0, 360).
    """
    if n == 0:
        return []

    total_available = sum(_arc_length(a) for a in available_arcs)
    spacing = total_available / n

    hues = []
    # Start at offset of half-spacing into the first arc for centering
    offset = spacing / 2
    current_offset = offset

    for _ in range(n):
        # Find which arc this offset falls in
        accumulated = 0
        for arc in available_arcs:
            arc_len = _arc_length(arc)
            if accumulated + arc_len > current_offset:
                # Hue is within this arc
                hue = arc[0] + (current_offset - accumulated)
                hues.append(hue % 360)
                break
            accumulated += arc_len
        else:
            # Wrap around (shouldn't happen with correct math, but safety)
            hue = available_arcs[0][0] + (current_offset % total_available)
            hues.append(hue % 360)
        current_offset += spacing

    return hues


# ---------------------------------------------------------------------------
# Colour conversion
# ---------------------------------------------------------------------------

def hsl_to_rgb(h, s, l):
    """Convert HSL to RGB. h in [0,360), s and l in [0,1]. Returns (r,g,b) in [0,255]."""
    h = h % 360
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = l - c / 2

    if h < 60:
        r1, g1, b1 = c, x, 0
    elif h < 120:
        r1, g1, b1 = x, c, 0
    elif h < 180:
        r1, g1, b1 = 0, c, x
    elif h < 240:
        r1, g1, b1 = 0, x, c
    elif h < 300:
        r1, g1, b1 = x, 0, c
    else:
        r1, g1, b1 = c, 0, x

    r = round((r1 + m) * 255)
    g = round((g1 + m) * 255)
    b = round((b1 + m) * 255)
    return (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))


def rgb_to_hex(r, g, b):
    """Convert RGB tuple to hex string like #AABBCC."""
    return f"#{r:02X}{g:02X}{b:02X}"


def hex_to_decimal_rgb(hex_color):
    """Convert '#RRGGBB' to decimal integer for Obsidian graph.json."""
    return int(hex_color[1:], 16)


def rose_blend(rgb, factor=ROSE_BLEND_FACTOR):
    """Blend an RGB tuple towards rose by the given factor.

    Formula: result = base + (rose - base) × factor
    """
    r = round(rgb[0] + (ROSE_RGB[0] - rgb[0]) * factor)
    g = round(rgb[1] + (ROSE_RGB[1] - rgb[1]) * factor)
    b = round(rgb[2] + (ROSE_RGB[2] - rgb[2]) * factor)
    return (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))


# ---------------------------------------------------------------------------
# Colour computation
# ---------------------------------------------------------------------------

def compute_colours(router):
    """Extract types from router, sort, distribute hues, return assignments.

    Returns dict with keys:
      living:   [{key, folder, path, hex, rgb}]
      temporal: [{key, folder, path, hex, rgb, blended_hex, blended_rgb}]
    """
    artefacts = router.get("artefacts", [])

    living = sorted(
        [a for a in artefacts if a["classification"] == "living"],
        key=lambda a: a["key"],
    )
    temporal = sorted(
        [a for a in artefacts if a["classification"] == "temporal"],
        key=lambda a: a["key"],
    )

    available_arcs = compute_available_arcs(EXCLUSION_ZONES)

    # Distribute living hues
    living_hues = distribute_hues(len(living), available_arcs)
    living_assignments = []
    for i, art in enumerate(living):
        rgb = hsl_to_rgb(living_hues[i], PASTEL_S, PASTEL_L)
        living_assignments.append({
            "key": art["key"],
            "folder": art["folder"],
            "path": art["path"],
            "hex": rgb_to_hex(*rgb),
            "rgb": rgb,
            "hue": round(living_hues[i], 1),
        })

    # Distribute temporal hues (independent distribution)
    temporal_hues = distribute_hues(len(temporal), available_arcs)
    temporal_assignments = []
    for i, art in enumerate(temporal):
        rgb = hsl_to_rgb(temporal_hues[i], PASTEL_S, PASTEL_L)
        blended = rose_blend(rgb)
        temporal_assignments.append({
            "key": art["key"],
            "folder": art["folder"],
            "path": art["path"],
            "hex": rgb_to_hex(*rgb),
            "rgb": rgb,
            "blended_hex": rgb_to_hex(*blended),
            "blended_rgb": blended,
            "hue": round(temporal_hues[i], 1),
        })

    return {"living": living_assignments, "temporal": temporal_assignments}


# ---------------------------------------------------------------------------
# CSS rendering
# ---------------------------------------------------------------------------

def _css_header():
    """Header comment."""
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    return (
        f"/* =====================================================\n"
        f"   Folder Colors — Brain Vault\n"
        f"   Auto-generated by compile_colours.py\n"
        f"   Generated: {now}\n"
        f"   Do not edit manually — regenerate with brain_action(\"compile\")\n"
        f"   ===================================================== */\n"
    )


def _css_system_palette():
    """Fixed system palette variables."""
    return (
        "\n/* ─── Colour Palette ──────────────────────────────────────────────────────── */\n"
        ":root {\n"
        f"  --palette-teal:          {PALETTE_TEAL}; /* Pastel Teal       */\n"
        f"  --palette-rose:         {PALETTE_ROSE}; /* Pastel Rose      */\n"
        "  --palette-rose-gold:    #EDBEA7; /* Rose Gold         */\n"
        f"  --palette-slate:        {PALETTE_SLATE}; /* Pastel Slate      */\n"
        f"  --palette-violet-light: {PALETTE_VIOLET}; /* Light Purple      */\n"
        "  --palette-violet-dark:  #3B2060; /* Deep Purple       */\n"
        f"  --palette-orchid:       {PALETTE_ORCHID}; /* Pastel Orchid     */\n"
        "}\n"
    )


def _css_theme_variables(living, temporal):
    """Theme variables — system themes fixed, per-type computed."""
    lines = [
        "\n/* ─── Themes ──────────────────────────────────────────────────────────────── */",
        ":root {",
        "  /* Config folders: purple bg + purple fg */",
        "  --theme-config-fg: var(--palette-violet-light);",
        "  --theme-config-bg: rgba(196, 168, 232, 0.12);  /* violet-light at 12% */",
        "",
        "  /* Temporal folders: rose bg + per-child fg */",
        "  --theme-temporal-fg: var(--palette-rose);",
        "  --theme-temporal-bg: rgba(242, 168, 196, 0.12); /* rose at 12% */",
        "",
        "  /* Plugin folders: orchid bg + orchid fg (purple↔rose midpoint) */",
        "  --theme-plugins-fg: var(--palette-orchid);",
        "  --theme-plugins-bg: rgba(219, 168, 214, 0.12); /* orchid at 12% */",
        "",
        "  /* Workspaces folder: teal bg + teal fg */",
        "  --theme-workspaces-fg: var(--palette-teal);",
        "  --theme-workspaces-bg: rgba(138, 188, 176, 0.12); /* teal at 12% */",
        "",
        "  /* Assets folder: slate bg + slate fg */",
        "  --theme-assets-fg: var(--palette-slate);",
        "  --theme-assets-bg: rgba(160, 176, 192, 0.12); /* slate at 12% */",
        "",
        "  /* Artefact folders: rose-gold bg + per-folder fg */",
        "  --theme-artefact-bg: rgba(237, 190, 167, 0.12); /* rose-gold at 12% */",
        "",
        "  /* Living artefact folder colours (auto-generated) */",
    ]
    for a in living:
        lines.append(f"  --color-{a['key']}: {a['hex']};")
    lines.append("")
    lines.append("  /* Temporal parent and children (auto-generated, rose-blended) */")
    lines.append("  --color-temporal: var(--palette-rose);  /* Rose */")
    for a in temporal:
        lines.append(f"  --color-temporal-{a['key']}: {a['blended_hex']};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _css_system_folder_icons():
    """System folder icon badges (fixed)."""
    return (
        "\n\n/* ─── System Folder Icons ───────────────────────────────────────────────── */\n"
        "\n"
        ".nav-folder-title[data-path=\"_Archive\"],\n"
        ".nav-folder-title[data-path=\"_Assets\"],\n"
        ".nav-folder-title[data-path=\"_Config\"],\n"
        ".nav-folder-title[data-path=\"_Plugins\"],\n"
        ".nav-folder-title[data-path=\"_Temporal\"],\n"
        ".nav-folder-title[data-path=\"_Workspaces\"],\n"
        ".nav-file-title[data-path=\"AGENTS.md\"],\n"
        ".nav-file-title[data-path=\"Agents.md\"],\n"
        ".nav-file-title[data-path=\"CLAUDE.md\"] {\n"
        "  position: relative;\n"
        "}\n"
        "\n"
        ".nav-folder-title[data-path=\"_Archive\"]::after,\n"
        ".nav-folder-title[data-path=\"_Assets\"]::after,\n"
        ".nav-folder-title[data-path=\"_Config\"]::after,\n"
        ".nav-folder-title[data-path=\"_Plugins\"]::after,\n"
        ".nav-folder-title[data-path=\"_Temporal\"]::after,\n"
        ".nav-folder-title[data-path=\"_Workspaces\"]::after,\n"
        ".nav-file-title[data-path=\"AGENTS.md\"]::after,\n"
        ".nav-file-title[data-path=\"Agents.md\"]::after,\n"
        ".nav-file-title[data-path=\"CLAUDE.md\"]::after {\n"
        "  position: absolute;\n"
        "  right: 8px;\n"
        "  top: 50%;\n"
        "  transform: translateY(-50%);\n"
        "  font-size: 1.15em;\n"
        "  opacity: 0.6;\n"
        "}\n"
        "\n"
        ".nav-folder-title[data-path=\"_Archive\"]::after {\n"
        "  content: \"⧈\";\n"
        "  color: var(--theme-assets-fg);\n"
        "}\n"
        ".nav-folder-title[data-path=\"_Assets\"]::after {\n"
        "  content: \"⧉\";\n"
        "  color: var(--theme-assets-fg);\n"
        "}\n"
        ".nav-folder-title[data-path=\"_Config\"]::after {\n"
        "  content: \"⍟\";\n"
        "  color: var(--theme-config-fg);\n"
        "}\n"
        ".nav-folder-title[data-path=\"_Plugins\"]::after {\n"
        "  content: \"⬡\";\n"
        "  color: var(--theme-plugins-fg);\n"
        "}\n"
        ".nav-folder-title[data-path=\"_Temporal\"]::after {\n"
        "  content: \"◷\";\n"
        "  color: var(--color-temporal);\n"
        "}\n"
        ".nav-folder-title[data-path=\"_Workspaces\"]::after {\n"
        "  content: \"⊞\";\n"
        "  color: var(--theme-workspaces-fg);\n"
        "}\n"
        ".nav-file-title[data-path=\"AGENTS.md\"]::after,\n"
        ".nav-file-title[data-path=\"Agents.md\"]::after,\n"
        ".nav-file-title[data-path=\"CLAUDE.md\"]::after {\n"
        "  content: \"⍟\";\n"
        "  color: var(--theme-config-fg);\n"
        "}\n"
    )


def _css_assets_section():
    """Assets folder (fixed)."""
    return (
        "\n\n/* ─── Assets Folder (_Assets) ───────────────────────────────────────── */\n"
        "\n"
        "/* _Assets folder and subfolders — background + slate fg */\n"
        ".nav-folder-title[data-path=\"_Assets\"] .nav-folder-title-content,\n"
        ".nav-folder-title[data-path^=\"_Assets/\"] .nav-folder-title-content {\n"
        "  color: var(--theme-assets-fg);\n"
        "}\n"
        ".nav-folder-title[data-path=\"_Assets\"],\n"
        ".nav-folder-title[data-path^=\"_Assets/\"] {\n"
        "  background-color: var(--theme-assets-bg);\n"
        "  border-left: 4px double var(--theme-assets-fg);\n"
        "  border-radius: 4px;\n"
        "}\n"
        "\n"
        "/* Files inside _Assets — background + fg */\n"
        ".nav-file-title[data-path^=\"_Assets/\"] {\n"
        "  background-color: var(--theme-assets-bg);\n"
        "  border-radius: 4px;\n"
        "}\n"
        ".nav-file-title[data-path^=\"_Assets/\"] .nav-file-title-content {\n"
        "  color: var(--theme-assets-fg);\n"
        "  opacity: 0.85;\n"
        "}\n"
    )


def _css_config_section():
    """Config folders (fixed)."""
    return (
        "\n\n/* ─── Config Folders (_Config) ───────────────────────────────────────────── */\n"
        "\n"
        "/* _Config folder and subfolders — background + purple fg */\n"
        ".nav-folder-title[data-path=\"_Config\"] .nav-folder-title-content,\n"
        ".nav-folder-title[data-path^=\"_Config/\"] .nav-folder-title-content {\n"
        "  color: var(--theme-config-fg);\n"
        "}\n"
        ".nav-folder-title[data-path=\"_Config\"],\n"
        ".nav-folder-title[data-path^=\"_Config/\"] {\n"
        "  background-color: var(--theme-config-bg);\n"
        "  border-left: 4px double var(--theme-config-fg);\n"
        "  border-radius: 4px;\n"
        "}\n"
        "\n"
        "/* Files inside _Config — background + fg */\n"
        ".nav-file-title[data-path^=\"_Config/\"] {\n"
        "  background-color: var(--theme-config-bg);\n"
        "  border-radius: 4px;\n"
        "}\n"
        ".nav-file-title[data-path^=\"_Config/\"] .nav-file-title-content {\n"
        "  color: var(--theme-config-fg);\n"
        "  opacity: 0.85;\n"
        "}\n"
        "\n"
        "/* AGENTS.md / CLAUDE.md — root-level config files, foreground only */\n"
        ".nav-file-title[data-path=\"AGENTS.md\"] .nav-file-title-content,\n"
        ".nav-file-title[data-path=\"Agents.md\"] .nav-file-title-content,\n"
        ".nav-file-title[data-path=\"CLAUDE.md\"] .nav-file-title-content {\n"
        "  color: var(--theme-config-fg);\n"
        "  opacity: 0.85;\n"
        "}\n"
    )


def _css_temporal_parent():
    """Temporal parent folder (fixed)."""
    return (
        "\n\n/* ─── Temporal Folders (_Temporal) ───────────────────────────────────────── */\n"
        "\n"
        "/* _Temporal parent — background + rose fg */\n"
        ".nav-folder-title[data-path=\"_Temporal\"] .nav-folder-title-content {\n"
        "  color: var(--color-temporal);\n"
        "}\n"
        ".nav-folder-title[data-path=\"_Temporal\"] {\n"
        "  background-color: var(--theme-temporal-bg);\n"
        "  border-left: 4px double var(--color-temporal);\n"
        "  border-radius: 4px;\n"
        "}\n"
    )


def _css_temporal_child(folder, key):
    """Generate CSS for a single temporal child folder."""
    path = f"_Temporal/{folder}"
    var = f"--color-temporal-{key}"
    return (
        f"\n/* _Temporal/{folder} — folder bg + fg */\n"
        f".nav-folder-title[data-path=\"{path}\"] .nav-folder-title-content,\n"
        f".nav-folder-title[data-path^=\"{path}/\"] .nav-folder-title-content {{\n"
        f"  color: var({var});\n"
        f"}}\n"
        f".nav-folder-title[data-path=\"{path}\"],\n"
        f".nav-folder-title[data-path^=\"{path}/\"] {{\n"
        f"  background-color: var(--theme-temporal-bg);\n"
        f"  border-left: 4px double var({var});\n"
        f"  border-radius: 4px;\n"
        f"}}\n"
        f"/* _Temporal/{folder} — files */\n"
        f".nav-file-title[data-path^=\"{path}/\"] {{\n"
        f"  background-color: var(--theme-temporal-bg);\n"
        f"  border-radius: 4px;\n"
        f"}}\n"
        f".nav-file-title[data-path^=\"{path}/\"] .nav-file-title-content {{\n"
        f"  color: var({var});\n"
        f"}}\n"
    )


def _css_plugins_section():
    """Plugin folders (fixed)."""
    return (
        "\n\n/* ─── Plugin Folders (_Plugins) ─────────────────────────────────────────── */\n"
        "\n"
        ".nav-folder-title[data-path=\"_Plugins\"] .nav-folder-title-content,\n"
        ".nav-folder-title[data-path^=\"_Plugins/\"] .nav-folder-title-content {\n"
        "  color: var(--theme-plugins-fg);\n"
        "}\n"
        ".nav-folder-title[data-path=\"_Plugins\"],\n"
        ".nav-folder-title[data-path^=\"_Plugins/\"] {\n"
        "  background-color: var(--theme-plugins-bg);\n"
        "  border-left: 4px double var(--theme-plugins-fg);\n"
        "  border-radius: 4px;\n"
        "}\n"
        ".nav-file-title[data-path^=\"_Plugins/\"] {\n"
        "  background-color: var(--theme-plugins-bg);\n"
        "  border-radius: 4px;\n"
        "}\n"
        ".nav-file-title[data-path^=\"_Plugins/\"] .nav-file-title-content {\n"
        "  color: var(--theme-plugins-fg);\n"
        "  opacity: 0.85;\n"
        "}\n"
    )


def _css_workspaces_section():
    """Workspaces data folder (fixed)."""
    return (
        "\n\n/* ─── Workspaces Data Folder (_Workspaces) ──────────────────────────────── */\n"
        "\n"
        ".nav-folder-title[data-path=\"_Workspaces\"] .nav-folder-title-content,\n"
        ".nav-folder-title[data-path^=\"_Workspaces/\"] .nav-folder-title-content {\n"
        "  color: var(--theme-workspaces-fg);\n"
        "}\n"
        ".nav-folder-title[data-path=\"_Workspaces\"],\n"
        ".nav-folder-title[data-path^=\"_Workspaces/\"] {\n"
        "  background-color: var(--theme-workspaces-bg);\n"
        "  border-left: 4px double var(--theme-workspaces-fg);\n"
        "  border-radius: 4px;\n"
        "}\n"
        ".nav-file-title[data-path^=\"_Workspaces/\"] {\n"
        "  background-color: var(--theme-workspaces-bg);\n"
        "  border-radius: 4px;\n"
        "}\n"
        ".nav-file-title[data-path^=\"_Workspaces/\"] .nav-file-title-content {\n"
        "  color: var(--theme-workspaces-fg);\n"
        "  opacity: 0.85;\n"
        "}\n"
    )


def _css_artefact_folder(folder, key):
    """Generate CSS for a single living artefact folder."""
    var = f"--color-{key}"
    return (
        f"\n/* {folder} — folder + subfolders */\n"
        f".nav-folder-title[data-path=\"{folder}\"] .nav-folder-title-content,\n"
        f".nav-folder-title[data-path^=\"{folder}/\"] .nav-folder-title-content {{\n"
        f"  color: var({var});\n"
        f"}}\n"
        f".nav-folder-title[data-path=\"{folder}\"],\n"
        f".nav-folder-title[data-path^=\"{folder}/\"] {{\n"
        f"  background-color: var(--theme-artefact-bg);\n"
        f"  border-left: 3px solid var({var});\n"
        f"  border-radius: 4px;\n"
        f"}}\n"
        f"/* {folder} — files */\n"
        f".nav-file-title[data-path^=\"{folder}/\"] {{\n"
        f"  background-color: var(--theme-artefact-bg);\n"
        f"  border-radius: 4px;\n"
        f"}}\n"
        f".nav-file-title[data-path^=\"{folder}/\"] .nav-file-title-content {{\n"
        f"  color: var({var});\n"
        f"}}\n"
    )


def _css_archive_section():
    """Archive styling — root _Archive/ and artefact _Archive/ subfolders (fixed, must be last)."""
    return (
        "\n\n/* ─── _Archive (root + subfolders) ──────────────────────────────────────── */\n"
        "/* Must come AFTER artefact folders — same specificity, last rule wins */\n"
        "\n"
        "/* Root _Archive — full slate treatment (fg + bg + border), like _Assets */\n"
        ".nav-folder-title[data-path=\"_Archive\"] .nav-folder-title-content,\n"
        ".nav-folder-title[data-path^=\"_Archive/\"] .nav-folder-title-content {\n"
        "  color: var(--theme-assets-fg);\n"
        "}\n"
        ".nav-folder-title[data-path=\"_Archive\"],\n"
        ".nav-folder-title[data-path^=\"_Archive/\"] {\n"
        "  background-color: var(--theme-assets-bg);\n"
        "  border-left: 4px double var(--theme-assets-fg);\n"
        "  border-radius: 4px;\n"
        "}\n"
        ".nav-file-title[data-path^=\"_Archive/\"] {\n"
        "  background-color: var(--theme-assets-bg);\n"
        "  border-radius: 4px;\n"
        "}\n"
        ".nav-file-title[data-path^=\"_Archive/\"] .nav-file-title-content {\n"
        "  color: var(--theme-assets-fg);\n"
        "  opacity: 0.85;\n"
        "}\n"
        "\n"
        "/* Artefact _Archive subfolders — slate text, parent background + double border in parent colour */\n"
        ".nav-folder-title[data-path$=\"/_Archive\"] .nav-folder-title-content,\n"
        ".nav-folder-title[data-path*=\"/_Archive/\"] .nav-folder-title-content {\n"
        "  color: var(--theme-assets-fg);\n"
        "}\n"
        ".nav-folder-title[data-path$=\"/_Archive\"],\n"
        ".nav-folder-title[data-path*=\"/_Archive/\"] {\n"
        "  border-left-style: double;\n"
        "  border-left-width: 4px;\n"
        "}\n"
        ".nav-file-title[data-path*=\"/_Archive/\"] .nav-file-title-content {\n"
        "  color: var(--theme-assets-fg);\n"
        "  opacity: 0.85;\n"
        "}\n"
    )


def render_css(assignments):
    """Generate full CSS from colour assignments."""
    living = assignments["living"]
    temporal = assignments["temporal"]

    sections = [
        _css_header(),
        _css_system_palette(),
        _css_theme_variables(living, temporal),
        _css_system_folder_icons(),
        _css_assets_section(),
        _css_config_section(),
        _css_temporal_parent(),
    ]

    # Temporal children (generated per-type)
    for a in temporal:
        sections.append(_css_temporal_child(a["folder"], a["key"]))

    sections.append(_css_plugins_section())
    sections.append(_css_workspaces_section())

    # Artefact folders header
    sections.append(
        "\n\n/* ─── Artefact Folders ───────────────────────────────────────────────────── */\n"
    )

    # Living artefact folders (generated per-type)
    for a in living:
        sections.append(_css_artefact_folder(a["folder"], a["key"]))

    # Archive must be last
    sections.append(_css_archive_section())

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Graph view colour groups
# ---------------------------------------------------------------------------

# System folder colours — references to shared palette constants
_SYSTEM_GRAPH_COLOURS = [
    ("_Assets", PALETTE_SLATE),
    ("_Config",      PALETTE_VIOLET),
    ("_Plugins",     PALETTE_ORCHID),
    ("_Temporal",    PALETTE_ROSE),
    ("_Workspaces",  PALETTE_TEAL),
]

_ARCHIVE_COLOUR = PALETTE_SLATE


def _graph_entry(query, hex_color):
    """Build a single graph colorGroup entry."""
    return {
        "query": query,
        "color": {"a": 1, "rgb": hex_to_decimal_rgb(hex_color)},
    }


def render_graph_color_groups(assignments):
    """Generate graph.json colorGroups from colour assignments.

    Returns list of {query, color} dicts. Ordering: system → living →
    temporal children → archive (Obsidian uses last-match-wins for
    overlapping queries).
    """
    groups = []

    # System folders
    for folder, hex_color in _SYSTEM_GRAPH_COLOURS:
        groups.append(_graph_entry(f'path:"{folder}"', hex_color))

    # Living artefact folders
    for a in assignments["living"]:
        groups.append(_graph_entry(f'path:"{a["folder"]}"', a["hex"]))

    # Temporal child folders
    for a in assignments["temporal"]:
        groups.append(_graph_entry(f'path:"{a["path"]}"', a["blended_hex"]))

    # Archive — last, so it overrides parent colours
    groups.append(_graph_entry('path:_Archive', _ARCHIVE_COLOUR))

    return groups


def write_graph_json(vault_root, color_groups):
    """Write colorGroups to .obsidian/graph.json, preserving other settings.

    Merges brain-generated entries (path: queries) with any user-defined
    entries (tag:, file:, freetext, etc.) so manual graph colours survive
    recompiles.
    """
    graph_path = os.path.join(str(vault_root), GRAPH_JSON_REL)

    try:
        with open(graph_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
    except (json.JSONDecodeError, OSError):
        existing = {}

    # Preserve user-defined colorGroups that don't use path: queries
    brain_queries = {g["query"] for g in color_groups}
    user_groups = [
        g for g in existing.get("colorGroups", [])
        if not g.get("query", "").startswith("path:") and g.get("query") not in brain_queries
    ]
    existing["colorGroups"] = user_groups + color_groups

    safe_write_json(graph_path, existing, bounds=vault_root)

    return graph_path


# ---------------------------------------------------------------------------
# Public API (for MCP server import)
# ---------------------------------------------------------------------------

def generate(vault_root, router=None):
    """Generate colours and write CSS + graph.json. Returns (assignments, css_path).

    Can be called from MCP server with a pre-loaded router.
    """
    vault_root = str(vault_root)
    if router is None:
        router = _require_compiled_router(vault_root)

    assignments = compute_colours(router)
    css = render_css(assignments)

    css_path = os.path.join(vault_root, OUTPUT_REL)
    safe_write(css_path, css, bounds=vault_root)

    color_groups = render_graph_color_groups(assignments)
    write_graph_json(vault_root, color_groups)

    return assignments, css_path


def load_router(vault_root):
    """Public compatibility wrapper for tests and callers."""
    return _require_compiled_router(vault_root)


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

def parse_args(argv):
    """Parse CLI arguments. Returns (json_mode, dry_run, vault_path)."""
    json_mode = "--json" in argv
    dry_run = "--dry-run" in argv
    vault_path = None
    if "--vault" in argv:
        idx = argv.index("--vault")
        if idx + 1 < len(argv):
            vault_path = argv[idx + 1]
        else:
            print("Error: --vault requires a path argument.", file=sys.stderr)
            sys.exit(1)
    return json_mode, dry_run, vault_path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _require_compiled_router(vault_root):
    """Load the compiled router or exit with a CLI-friendly error."""
    router = load_compiled_router(vault_root)
    if "error" in router:
        print(f"Error: {router['error']}", file=sys.stderr)
        sys.exit(1)
    return router


def main():
    json_mode, dry_run, vault_path = parse_args(sys.argv)
    vault_root = find_vault_root(vault_path)
    router = _require_compiled_router(vault_root)
    assignments = compute_colours(router)
    color_groups = render_graph_color_groups(assignments)

    if json_mode:
        # Serialise — strip non-JSON-friendly tuples
        output = {
            "living": [
                {k: v for k, v in a.items() if k != "rgb"}
                for a in assignments["living"]
            ],
            "temporal": [
                {k: v for k, v in a.items() if k not in ("rgb", "blended_rgb")}
                for a in assignments["temporal"]
            ],
            "graph": color_groups,
        }
        print(json.dumps(output, indent=2))
        return

    css = render_css(assignments)

    if dry_run:
        print(css)
        print("\n--- graph.json colorGroups ---")
        print(json.dumps(color_groups, indent=2))
        return

    generate(vault_root, router)

    living_count = len(assignments["living"])
    temporal_count = len(assignments["temporal"])
    graph_count = len(color_groups)
    print(
        f"Generated colours: {living_count} living + {temporal_count} temporal "
        f"= {living_count + temporal_count} types → {OUTPUT_REL}",
        file=sys.stderr,
    )
    print(
        f"Graph colour groups: {graph_count} entries → {GRAPH_JSON_REL}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
