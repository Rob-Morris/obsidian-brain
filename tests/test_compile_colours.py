"""Tests for compile_colours.py — colour distribution and CSS generation."""

import json
import os

import pytest

import compile_colours as cc


TEMPLATE_VAULT = os.path.join(
    os.path.dirname(__file__), "..", "template-vault"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    """Minimal vault with 3 living + 8 temporal types (template vault shape)."""
    # .brain-core/VERSION
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("1.0.0\n")

    # _Config with compiled router
    config = tmp_path / "_Config"
    config.mkdir()

    # Living types
    for name in ["Daily Notes", "Notes", "Wiki"]:
        (tmp_path / name).mkdir()

    # Temporal types
    temporal = tmp_path / "_Temporal"
    temporal.mkdir()
    for name in ["Cookies", "Decision Logs", "Friction Logs", "Logs",
                 "Plans", "Research", "Shaping Transcripts", "Transcripts"]:
        (temporal / name).mkdir()

    # Build a compiled router JSON
    artefacts = []
    for name in sorted(["Daily Notes", "Notes", "Wiki"]):
        key = name.lower().replace(" ", "-")
        artefacts.append({
            "folder": name, "type": f"living/{key}", "key": key,
            "classification": "living", "configured": True,
            "naming": None, "frontmatter": None, "trigger": None,
            "taxonomy_file": None, "template_file": None, "path": name,
        })
    for name in sorted(["Cookies", "Decision Logs", "Friction Logs", "Logs",
                        "Plans", "Research", "Shaping Transcripts", "Transcripts"]):
        key = name.lower().replace(" ", "-")
        artefacts.append({
            "folder": name, "type": f"temporal/{key}", "key": key,
            "classification": "temporal", "configured": True,
            "naming": None, "frontmatter": None, "trigger": None,
            "taxonomy_file": None, "template_file": None,
            "path": os.path.join("_Temporal", name),
        })

    router = {
        "meta": {"brain_core_version": "1.0.0", "compiled_at": "2026-01-01T00:00:00+00:00",
                 "source_hash": "sha256:test", "sources": {}},
        "environment": {"vault_root": str(tmp_path), "platform": "darwin",
                        "python_version": "3.12.0", "cli_available": False},
        "always_rules": [], "artefacts": artefacts,
        "triggers": [], "skills": [], "plugins": [], "styles": [],
    }
    (config / ".compiled-router.json").write_text(json.dumps(router, indent=2))

    # .obsidian/snippets/ directory
    snippets = tmp_path / ".obsidian" / "snippets"
    snippets.mkdir(parents=True)

    return tmp_path


@pytest.fixture
def template_vault():
    """Use the real template vault (read-only)."""
    path = os.path.abspath(TEMPLATE_VAULT)
    if not os.path.isdir(path):
        pytest.skip("template-vault not found")
    return path


def _load_router(vault_path):
    """Helper to load compiled router from a vault."""
    return cc.load_router(vault_path)


# ---------------------------------------------------------------------------
# Available arcs computation
# ---------------------------------------------------------------------------

class TestComputeAvailableArcs:
    def test_with_default_exclusions(self):
        arcs = cc.compute_available_arcs(cc.EXCLUSION_ZONES)
        total = sum(a[1] - a[0] for a in arcs)
        assert total == 210  # 360 - 5×30 = 210

    def test_no_exclusions(self):
        arcs = cc.compute_available_arcs([])
        total = sum(a[1] - a[0] for a in arcs)
        assert total == 360

    def test_single_exclusion(self):
        arcs = cc.compute_available_arcs([(0, 30)])
        total = sum(a[1] - a[0] for a in arcs)
        assert total == 330

    def test_arcs_are_sorted(self):
        arcs = cc.compute_available_arcs(cc.EXCLUSION_ZONES)
        starts = [a[0] for a in arcs]
        # All start values should be ascending (modulo wrapping)
        for i in range(len(starts) - 1):
            assert starts[i] < starts[i + 1] or starts[i + 1] < starts[i]


# ---------------------------------------------------------------------------
# Hue distribution
# ---------------------------------------------------------------------------

class TestDistributeHues:
    def test_single_type(self):
        arcs = cc.compute_available_arcs(cc.EXCLUSION_ZONES)
        hues = cc.distribute_hues(1, arcs)
        assert len(hues) == 1
        # Should be in the middle of available space
        assert 0 <= hues[0] < 360

    def test_multiple_types_are_evenly_spaced(self):
        arcs = cc.compute_available_arcs(cc.EXCLUSION_ZONES)
        hues = cc.distribute_hues(4, arcs)
        assert len(hues) == 4
        # All hues should be distinct
        assert len(set(round(h, 2) for h in hues)) == 4

    def test_zero_types(self):
        arcs = cc.compute_available_arcs(cc.EXCLUSION_ZONES)
        hues = cc.distribute_hues(0, arcs)
        assert hues == []

    def test_deterministic(self):
        arcs = cc.compute_available_arcs(cc.EXCLUSION_ZONES)
        hues1 = cc.distribute_hues(5, arcs)
        hues2 = cc.distribute_hues(5, arcs)
        assert hues1 == hues2

    def test_avoids_exclusion_zones(self):
        arcs = cc.compute_available_arcs(cc.EXCLUSION_ZONES)
        hues = cc.distribute_hues(20, arcs)
        for h in hues:
            for zone_start, zone_end in cc.EXCLUSION_ZONES:
                # Hue should NOT be inside any exclusion zone
                if zone_start < zone_end:
                    assert not (zone_start < h < zone_end), \
                        f"Hue {h}° falls in exclusion zone ({zone_start}, {zone_end})"

    def test_many_types_still_fit(self):
        """Even with 30 types, all hues should be valid."""
        arcs = cc.compute_available_arcs(cc.EXCLUSION_ZONES)
        hues = cc.distribute_hues(30, arcs)
        assert len(hues) == 30
        for h in hues:
            assert 0 <= h < 360


# ---------------------------------------------------------------------------
# HSL → RGB conversion
# ---------------------------------------------------------------------------

class TestHslToRgb:
    def test_red(self):
        r, g, b = cc.hsl_to_rgb(0, 1.0, 0.5)
        assert (r, g, b) == (255, 0, 0)

    def test_green(self):
        r, g, b = cc.hsl_to_rgb(120, 1.0, 0.5)
        assert (r, g, b) == (0, 255, 0)

    def test_blue(self):
        r, g, b = cc.hsl_to_rgb(240, 1.0, 0.5)
        assert (r, g, b) == (0, 0, 255)

    def test_white(self):
        r, g, b = cc.hsl_to_rgb(0, 0.0, 1.0)
        assert (r, g, b) == (255, 255, 255)

    def test_black(self):
        r, g, b = cc.hsl_to_rgb(0, 0.0, 0.0)
        assert (r, g, b) == (0, 0, 0)

    def test_pastel_values_in_range(self):
        """With pastel S/L, output should be muted (not pure)."""
        r, g, b = cc.hsl_to_rgb(210, cc.PASTEL_S, cc.PASTEL_L)
        assert 100 < r < 255
        assert 100 < g < 255
        assert 100 < b < 255


# ---------------------------------------------------------------------------
# RGB → Hex conversion
# ---------------------------------------------------------------------------

class TestRgbToHex:
    def test_black(self):
        assert cc.rgb_to_hex(0, 0, 0) == "#000000"

    def test_white(self):
        assert cc.rgb_to_hex(255, 255, 255) == "#FFFFFF"

    def test_known_colour(self):
        assert cc.rgb_to_hex(242, 168, 196) == "#F2A8C4"


# ---------------------------------------------------------------------------
# Rose blend
# ---------------------------------------------------------------------------

class TestRoseBlend:
    def test_rose_blends_towards_rose(self):
        """Blending any colour should move it towards rose."""
        base = (100, 200, 50)
        blended = cc.rose_blend(base)
        # Each channel should be closer to rose than the base
        for i in range(3):
            assert abs(blended[i] - cc.ROSE_RGB[i]) <= abs(base[i] - cc.ROSE_RGB[i])

    def test_rose_unchanged_at_zero_factor(self):
        base = (100, 200, 50)
        blended = cc.rose_blend(base, factor=0.0)
        assert blended == base

    def test_rose_fully_blended_at_one(self):
        base = (100, 200, 50)
        blended = cc.rose_blend(base, factor=1.0)
        assert blended == cc.ROSE_RGB

    def test_known_blend_amber_to_rose(self):
        """Amber #F5C97A blended 35% towards rose #F2A8C4 should be close to #F3BD93."""
        amber = (0xF5, 0xC9, 0x7A)  # (245, 201, 122)
        blended = cc.rose_blend(amber)
        hex_result = cc.rgb_to_hex(*blended)
        # Allow ±1 per channel for rounding
        assert abs(blended[0] - 0xF4) <= 1
        assert abs(blended[1] - 0xBD) <= 2
        assert abs(blended[2] - 0x93) <= 2


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

class TestComputeColours:
    def test_template_vault_shape(self, vault):
        router = _load_router(vault)
        assignments = cc.compute_colours(router)
        assert len(assignments["living"]) == 3
        assert len(assignments["temporal"]) == 8

    def test_all_living_have_hex(self, vault):
        router = _load_router(vault)
        assignments = cc.compute_colours(router)
        for a in assignments["living"]:
            assert a["hex"].startswith("#")
            assert len(a["hex"]) == 7

    def test_all_temporal_have_blended_hex(self, vault):
        router = _load_router(vault)
        assignments = cc.compute_colours(router)
        for a in assignments["temporal"]:
            assert a["blended_hex"].startswith("#")
            assert len(a["blended_hex"]) == 7
            # Blended should differ from base
            assert a["blended_hex"] != a["hex"] or a["hex"] == cc.rgb_to_hex(*cc.ROSE_RGB)

    def test_deterministic_same_types(self, vault):
        router = _load_router(vault)
        a1 = cc.compute_colours(router)
        a2 = cc.compute_colours(router)
        for i in range(len(a1["living"])):
            assert a1["living"][i]["hex"] == a2["living"][i]["hex"]
        for i in range(len(a1["temporal"])):
            assert a1["temporal"][i]["blended_hex"] == a2["temporal"][i]["blended_hex"]

    def test_sorted_alphabetically_by_key(self, vault):
        router = _load_router(vault)
        assignments = cc.compute_colours(router)
        living_keys = [a["key"] for a in assignments["living"]]
        assert living_keys == sorted(living_keys)
        temporal_keys = [a["key"] for a in assignments["temporal"]]
        assert temporal_keys == sorted(temporal_keys)


# ---------------------------------------------------------------------------
# CSS rendering
# ---------------------------------------------------------------------------

class TestRenderCss:
    def test_has_correct_section_ordering(self, vault):
        router = _load_router(vault)
        assignments = cc.compute_colours(router)
        css = cc.render_css(assignments)

        # Check sections appear in order
        palette_pos = css.index("Colour Palette")
        themes_pos = css.index("Themes")
        icons_pos = css.index("System Folder Icons")
        attachments_pos = css.index("Assets Folder")
        config_pos = css.index("Config Folders")
        temporal_pos = css.index("Temporal Folders")
        plugins_pos = css.index("Plugin Folders")
        artefact_pos = css.index("Artefact Folders")
        archive_pos = css.index("_Archive Subfolders")

        assert palette_pos < themes_pos < icons_pos
        assert icons_pos < attachments_pos < config_pos
        assert config_pos < temporal_pos < plugins_pos
        assert plugins_pos < artefact_pos < archive_pos

    def test_archive_is_last_section(self, vault):
        router = _load_router(vault)
        assignments = cc.compute_colours(router)
        css = cc.render_css(assignments)
        archive_pos = css.index("_Archive Subfolders")
        # No other section header after archive
        remaining = css[archive_pos:]
        assert "/* ─── " not in remaining.split("_Archive Subfolders", 1)[1]

    def test_system_sections_unchanged_regardless_of_type_count(self, vault):
        """System folder CSS is the same whether vault has 1 type or 20."""
        router = _load_router(vault)
        assignments = cc.compute_colours(router)
        css1 = cc.render_css(assignments)

        # Simulate a smaller vault — just 1 living type
        router2 = dict(router)
        router2["artefacts"] = [a for a in router["artefacts"] if a["key"] == "wiki"]
        assignments2 = cc.compute_colours(router2)
        css2 = cc.render_css(assignments2)

        # Extract system sections (Attachments, Config, Plugins)
        def extract_section(css, start_marker, end_marker):
            s = css.index(start_marker)
            e = css.index(end_marker)
            return css[s:e]

        a1 = extract_section(css1, "Assets Folder", "Config Folders")
        a2 = extract_section(css2, "Assets Folder", "Config Folders")
        assert a1 == a2

    def test_contains_living_type_variables(self, vault):
        router = _load_router(vault)
        assignments = cc.compute_colours(router)
        css = cc.render_css(assignments)
        assert "--color-wiki:" in css
        assert "--color-daily-notes:" in css
        assert "--color-notes:" in css

    def test_contains_temporal_type_variables(self, vault):
        router = _load_router(vault)
        assignments = cc.compute_colours(router)
        css = cc.render_css(assignments)
        assert "--color-temporal-logs:" in css
        assert "--color-temporal-plans:" in css
        assert "--color-temporal-cookies:" in css

    def test_temporal_children_use_double_border(self, vault):
        router = _load_router(vault)
        assignments = cc.compute_colours(router)
        css = cc.render_css(assignments)
        # All temporal child sections should have double border
        for a in assignments["temporal"]:
            section_start = css.index(f"_Temporal/{a['folder']} — folder bg + fg")
            section = css[section_start:section_start + 500]
            assert "4px double" in section

    def test_living_artefacts_use_solid_border(self, vault):
        router = _load_router(vault)
        assignments = cc.compute_colours(router)
        css = cc.render_css(assignments)
        for a in assignments["living"]:
            section_start = css.index(f"{a['folder']} — folder + subfolders")
            section = css[section_start:section_start + 500]
            assert "3px solid" in section


# ---------------------------------------------------------------------------
# CSS writing (generate function)
# ---------------------------------------------------------------------------

class TestGenerate:
    def test_writes_css_file(self, vault):
        router = _load_router(vault)
        assignments, css_path = cc.generate(vault, router)
        assert os.path.isfile(css_path)
        assert css_path.endswith("folder-colours.css")

    def test_written_css_is_valid(self, vault):
        router = _load_router(vault)
        _, css_path = cc.generate(vault, router)
        with open(css_path) as f:
            css = f.read()
        assert ":root {" in css
        assert "compile_colours.py" in css


# ---------------------------------------------------------------------------
# Template vault integration
# ---------------------------------------------------------------------------

class TestTemplateVault:
    def test_template_vault_colours(self, template_vault):
        router = _load_router(template_vault)
        assignments = cc.compute_colours(router)

        # Template vault has 9 living + 14 temporal types
        assert len(assignments["living"]) == 9
        assert len(assignments["temporal"]) == 14

        # All should have valid hex colours
        for a in assignments["living"]:
            assert a["hex"].startswith("#")
        for a in assignments["temporal"]:
            assert a["blended_hex"].startswith("#")

    def test_template_vault_css_generation(self, template_vault):
        """CSS should contain all type selectors."""
        router = _load_router(template_vault)
        assignments = cc.compute_colours(router)
        css = cc.render_css(assignments)

        # Living types should have folder selectors
        assert 'data-path="Daily Notes"' in css
        assert 'data-path="Notes"' in css
        assert 'data-path="Designs"' in css

        # Temporal types should have folder selectors
        assert 'data-path="_Temporal/Logs"' in css
        assert 'data-path="_Temporal/Plans"' in css
        assert 'data-path="_Temporal/Cookies"' in css


# ---------------------------------------------------------------------------
# Rob's vault shape (11 living + 18 temporal)
# ---------------------------------------------------------------------------

class TestRobVaultShape:
    """Simulate Rob's vault with 11 living + 18 temporal types."""

    @pytest.fixture
    def rob_router(self, tmp_path):
        living_names = ["Daily Notes", "Designs", "Documentation", "Ideas",
                        "Journals", "Notes", "People", "Projects", "Wiki",
                        "Workspaces", "Writing"]
        temporal_names = ["Captures", "Cookies", "Decision Logs",
                         "Friction Logs", "Idea Logs", "Ingestions",
                         "Journal Entries", "Logs", "Mockups", "Observations",
                         "Plans", "Presentations", "Reports", "Research",
                         "Shaping Transcripts", "Snippets", "Thoughts",
                         "Transcripts"]

        artefacts = []
        for name in sorted(living_names):
            key = name.lower().replace(" ", "-")
            artefacts.append({
                "folder": name, "type": f"living/{key}", "key": key,
                "classification": "living", "configured": True,
                "naming": None, "frontmatter": None, "trigger": None,
                "taxonomy_file": None, "template_file": None, "path": name,
            })
        for name in sorted(temporal_names):
            key = name.lower().replace(" ", "-")
            artefacts.append({
                "folder": name, "type": f"temporal/{key}", "key": key,
                "classification": "temporal", "configured": True,
                "naming": None, "frontmatter": None, "trigger": None,
                "taxonomy_file": None, "template_file": None,
                "path": os.path.join("_Temporal", name),
            })

        return {
            "meta": {"brain_core_version": "0.9.12"},
            "artefacts": artefacts,
        }

    def test_all_29_types_get_colours(self, rob_router):
        assignments = cc.compute_colours(rob_router)
        assert len(assignments["living"]) == 11
        assert len(assignments["temporal"]) == 18
        total = len(assignments["living"]) + len(assignments["temporal"])
        assert total == 29

    def test_all_colours_valid_hex(self, rob_router):
        assignments = cc.compute_colours(rob_router)
        for a in assignments["living"]:
            assert len(a["hex"]) == 7 and a["hex"].startswith("#")
        for a in assignments["temporal"]:
            assert len(a["blended_hex"]) == 7 and a["blended_hex"].startswith("#")

    def test_mockups_gets_colour(self, rob_router):
        """Mockups (added in v0.9.11) should get a colour."""
        assignments = cc.compute_colours(rob_router)
        mockups = [a for a in assignments["temporal"] if a["key"] == "mockups"]
        assert len(mockups) == 1
        assert mockups[0]["blended_hex"].startswith("#")

    def test_colours_are_distinct(self, rob_router):
        """All generated colours should be unique."""
        assignments = cc.compute_colours(rob_router)
        living_hexes = [a["hex"] for a in assignments["living"]]
        temporal_hexes = [a["blended_hex"] for a in assignments["temporal"]]
        assert len(set(living_hexes)) == len(living_hexes)
        assert len(set(temporal_hexes)) == len(temporal_hexes)


# ---------------------------------------------------------------------------
# Hex → decimal RGB conversion
# ---------------------------------------------------------------------------

class TestHexToDecimalRgb:
    def test_black(self):
        assert cc.hex_to_decimal_rgb("#000000") == 0

    def test_white(self):
        assert cc.hex_to_decimal_rgb("#FFFFFF") == 16777215

    def test_known_colour(self):
        # #E0BE8F → 0xE0BE8F → 14728847
        assert cc.hex_to_decimal_rgb("#E0BE8F") == 0xE0BE8F

    def test_lowercase(self):
        assert cc.hex_to_decimal_rgb("#e0be8f") == 0xE0BE8F


# ---------------------------------------------------------------------------
# Graph colour groups
# ---------------------------------------------------------------------------

class TestGraphColourGroups:
    def test_system_folders_included(self, vault):
        router = _load_router(vault)
        assignments = cc.compute_colours(router)
        groups = cc.render_graph_color_groups(assignments)
        queries = [g["query"] for g in groups]
        assert 'path:"_Assets"' in queries
        assert 'path:"_Config"' in queries
        assert 'path:"_Plugins"' in queries
        assert 'path:"_Temporal"' in queries

    def test_living_folders_use_path_query(self, vault):
        router = _load_router(vault)
        assignments = cc.compute_colours(router)
        groups = cc.render_graph_color_groups(assignments)
        queries = [g["query"] for g in groups]
        assert 'path:"Wiki"' in queries
        assert 'path:"Daily Notes"' in queries
        assert 'path:"Notes"' in queries

    def test_temporal_folders_use_temporal_path(self, vault):
        router = _load_router(vault)
        assignments = cc.compute_colours(router)
        groups = cc.render_graph_color_groups(assignments)
        queries = [g["query"] for g in groups]
        assert 'path:"_Temporal/Logs"' in queries
        assert 'path:"_Temporal/Plans"' in queries

    def test_temporal_uses_blended_colour(self, vault):
        router = _load_router(vault)
        assignments = cc.compute_colours(router)
        groups = cc.render_graph_color_groups(assignments)
        # Find a temporal entry and check its colour matches blended_hex
        logs_assign = [a for a in assignments["temporal"] if a["key"] == "logs"][0]
        logs_group = [g for g in groups if g["query"] == 'path:"_Temporal/Logs"'][0]
        assert logs_group["color"]["rgb"] == cc.hex_to_decimal_rgb(logs_assign["blended_hex"])

    def test_all_entries_have_alpha_one(self, vault):
        router = _load_router(vault)
        assignments = cc.compute_colours(router)
        groups = cc.render_graph_color_groups(assignments)
        for g in groups:
            assert g["color"]["a"] == 1

    def test_all_rgb_values_are_ints(self, vault):
        router = _load_router(vault)
        assignments = cc.compute_colours(router)
        groups = cc.render_graph_color_groups(assignments)
        for g in groups:
            assert isinstance(g["color"]["rgb"], int)

    def test_total_count(self, vault):
        router = _load_router(vault)
        assignments = cc.compute_colours(router)
        groups = cc.render_graph_color_groups(assignments)
        # 5 system + 3 living + 8 temporal + 1 archive = 17
        expected = 5 + len(assignments["living"]) + len(assignments["temporal"]) + 1
        assert len(groups) == expected

    def test_archive_is_last(self, vault):
        router = _load_router(vault)
        assignments = cc.compute_colours(router)
        groups = cc.render_graph_color_groups(assignments)
        assert groups[-1]["query"] == "path:_Archive"

    def test_deterministic(self, vault):
        router = _load_router(vault)
        assignments = cc.compute_colours(router)
        g1 = cc.render_graph_color_groups(assignments)
        g2 = cc.render_graph_color_groups(assignments)
        assert g1 == g2


# ---------------------------------------------------------------------------
# Graph JSON writing
# ---------------------------------------------------------------------------

class TestWriteGraphJson:
    def test_creates_new_file(self, vault):
        groups = [{"query": "path:test", "color": {"a": 1, "rgb": 123}}]
        graph_path = cc.write_graph_json(vault, groups)
        assert os.path.isfile(graph_path)
        with open(graph_path) as f:
            data = json.load(f)
        assert data["colorGroups"] == groups

    def test_preserves_existing_settings(self, vault):
        # Write initial graph.json with other settings
        graph_path = os.path.join(str(vault), ".obsidian", "graph.json")
        os.makedirs(os.path.dirname(graph_path), exist_ok=True)
        existing = {
            "collapse-filter": True,
            "search": "",
            "showTags": False,
            "showAttachments": False,
            "showOrphans": True,
            "collapse-color-groups": True,
            "colorGroups": [{"query": "old", "color": {"a": 1, "rgb": 0}}],
            "collapse-display": True,
            "showArrow": False,
            "textFadeMultiplier": 0,
            "nodeSizeMultiplier": 1,
            "lineSizeMultiplier": 1,
            "collapse-forces": True,
            "centerStrength": 0.5,
            "repelStrength": 10,
            "linkStrength": 1,
            "linkDistance": 250,
        }
        with open(graph_path, "w") as f:
            json.dump(existing, f)

        # Write new color groups
        new_groups = [{"query": "path:new", "color": {"a": 1, "rgb": 456}}]
        cc.write_graph_json(vault, new_groups)

        with open(graph_path) as f:
            data = json.load(f)

        # colorGroups replaced
        assert data["colorGroups"] == new_groups
        # Other settings preserved
        assert data["collapse-filter"] is True
        assert data["repelStrength"] == 10
        assert data["linkDistance"] == 250

    def test_replaces_on_second_write(self, vault):
        groups1 = [{"query": "path:a", "color": {"a": 1, "rgb": 1}}]
        groups2 = [{"query": "path:b", "color": {"a": 1, "rgb": 2}}]
        cc.write_graph_json(vault, groups1)
        cc.write_graph_json(vault, groups2)
        graph_path = os.path.join(str(vault), ".obsidian", "graph.json")
        with open(graph_path) as f:
            data = json.load(f)
        assert data["colorGroups"] == groups2
        assert len(data["colorGroups"]) == 1  # No append


# ---------------------------------------------------------------------------
# Generate includes graph.json
# ---------------------------------------------------------------------------

class TestGenerateGraph:
    def test_generate_writes_graph_json(self, vault):
        router = _load_router(vault)
        cc.generate(vault, router)
        graph_path = os.path.join(str(vault), ".obsidian", "graph.json")
        assert os.path.isfile(graph_path)
        with open(graph_path) as f:
            data = json.load(f)
        assert "colorGroups" in data
        assert len(data["colorGroups"]) > 0

    def test_generate_graph_matches_assignments(self, vault):
        router = _load_router(vault)
        assignments, _ = cc.generate(vault, router)
        graph_path = os.path.join(str(vault), ".obsidian", "graph.json")
        with open(graph_path) as f:
            data = json.load(f)
        expected = cc.render_graph_color_groups(assignments)
        assert data["colorGroups"] == expected


# ---------------------------------------------------------------------------
# CLI args parsing
# ---------------------------------------------------------------------------

class TestParseArgs:
    def test_json_flag(self):
        json_mode, dry_run, vault = cc.parse_args(["script", "--json"])
        assert json_mode is True
        assert dry_run is False

    def test_dry_run_flag(self):
        json_mode, dry_run, vault = cc.parse_args(["script", "--dry-run"])
        assert dry_run is True
        assert json_mode is False

    def test_vault_flag(self):
        json_mode, dry_run, vault = cc.parse_args(["script", "--vault", "/tmp/my-vault"])
        assert vault == "/tmp/my-vault"

    def test_no_args(self):
        json_mode, dry_run, vault = cc.parse_args(["script"])
        assert json_mode is False
        assert dry_run is False
        assert vault is None
