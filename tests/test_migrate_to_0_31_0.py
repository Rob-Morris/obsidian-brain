"""Tests for migrations/migrate_to_0_31_0.py — three-phase key/parent/workspace migration."""

from __future__ import annotations

import json
import os

import pytest

import compile_router
import migrate_to_0_31_0
from _common import parse_frontmatter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path, fields, body=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for k, v in fields.items():
        if isinstance(v, list):
            if v:
                lines.append(f"{k}:")
                for item in v:
                    lines.append(f"  - {item}")
            else:
                lines.append(f"{k}: []")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    path.write_text("\n".join(lines) + "\n")


def _taxonomy(root, classification, folder, frontmatter_type, body_naming):
    subdir = "Living" if classification == "living" else "Temporal"
    path = root / "_Config" / "Taxonomy" / subdir / f"{folder.lower()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"# {folder}\n\n"
        f"## Naming\n\n{body_naming}\n\n"
        f"## Frontmatter\n\n"
        f"```yaml\n---\ntype: {frontmatter_type}\ntags: []\n---\n```\n"
    )


# ---------------------------------------------------------------------------
# Base vault fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    """Minimal vault with Wiki, Projects, Releases, Workspaces, Ideas living types."""
    (tmp_path / ".brain-core").mkdir()
    (tmp_path / ".brain-core" / "VERSION").write_text("0.31.0\n")
    (tmp_path / ".brain-core" / "session-core.md").write_text("# Session Core\n")

    (tmp_path / "_Config").mkdir()
    (tmp_path / "_Config" / "router.md").write_text(
        "Prefer MCP tools.\n\nAlways:\n- Every artefact belongs in a typed folder.\n"
    )

    (tmp_path / "Wiki").mkdir()
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Releases").mkdir()
    (tmp_path / "Workspaces").mkdir()
    (tmp_path / "_Workspaces").mkdir()
    (tmp_path / "Ideas").mkdir()

    _taxonomy(tmp_path, "living", "Wiki", "living/wiki",
              "`{Title}.md` in `Wiki/`.")
    _taxonomy(tmp_path, "living", "Projects", "living/project",
              "`{Title}.md` in `Projects/`.")
    _taxonomy(tmp_path, "living", "Releases", "living/release",
              "`{Title}.md` in `Releases/`.")
    _taxonomy(tmp_path, "living", "Workspaces", "living/workspace",
              "`{Title}.md` in `Workspaces/`.")
    _taxonomy(tmp_path, "living", "Ideas", "living/idea",
              "`{Title}.md` in `Ideas/`.")

    return tmp_path


@pytest.fixture
def router(vault):
    return compile_router.compile(str(vault))


# ---------------------------------------------------------------------------
# Phase 1 — slug backfill priority
# ---------------------------------------------------------------------------

class TestPhase1KeyDerivation:
    def test_existing_valid_key_skipped(self, vault, router):
        _write(vault / "Wiki" / "Rust Lifetimes.md",
               {"type": "living/wiki", "tags": [], "key": "rust-lifetimes"})
        result = migrate_to_0_31_0.migrate_vault(str(vault), apply=False, router=router)
        assert result["phase1"]["planned"] == 0

    def test_hub_slug_promoted(self, vault, router):
        _write(vault / "Wiki" / "Claude Code.md",
               {"type": "living/wiki", "tags": [], "hub-slug": "claude-code"})
        result = migrate_to_0_31_0.migrate_vault(str(vault), apply=True, router=router)
        plan = result["phase1"]["plans"][0]
        assert plan["key"] == "claude-code"
        assert plan["source"] == "hub-slug"
        content = (vault / "Wiki" / "Claude Code.md").read_text()
        assert "key: claude-code" in content
        assert "hub-slug" not in content

    def test_hub_underscore_slug_promoted(self, vault, router):
        _write(vault / "Wiki" / "Underscore Page.md",
               {"type": "living/wiki", "tags": [], "hub_slug": "underscore-page"})
        result = migrate_to_0_31_0.migrate_vault(str(vault), apply=True, router=router)
        plan = result["phase1"]["plans"][0]
        assert plan["source"] == "hub_slug"
        content = (vault / "Wiki" / "Underscore Page.md").read_text()
        assert "key: underscore-page" in content
        assert "hub_slug" not in content

    def test_self_tag_priority_over_filename(self, vault, router):
        """The canary: filename differs from tag — tag wins for hub references."""
        _write(vault / "Projects" / "Obsidian Brain.md",
               {"type": "living/project", "tags": ["project/brain"]})
        result = migrate_to_0_31_0.migrate_vault(str(vault), apply=True, router=router)
        plan = result["phase1"]["plans"][0]
        assert plan["key"] == "brain"
        assert plan["source"] == "self_tag"

    def test_self_tag_ignored_for_other_type(self, vault, router):
        """A tag like `project/foo` on a wiki page must not become the wiki's key."""
        _write(vault / "Wiki" / "About Project Foo.md",
               {"type": "living/wiki", "tags": ["project/foo"]})
        result = migrate_to_0_31_0.migrate_vault(str(vault), apply=True, router=router)
        plan = result["phase1"]["plans"][0]
        assert plan["key"] == "about-project-foo"
        assert plan["source"] == "title"

    def test_title_derivation_from_filename(self, vault, router):
        _write(vault / "Wiki" / "Some Topic.md",
               {"type": "living/wiki", "tags": []})
        result = migrate_to_0_31_0.migrate_vault(str(vault), apply=True, router=router)
        plan = result["phase1"]["plans"][0]
        assert plan["key"] == "some-topic"
        assert plan["source"] == "title"

    def test_collision_resolution_adds_suffix(self, vault, router):
        _write(vault / "Wiki" / "First Copy.md",
               {"type": "living/wiki", "tags": [], "key": "rust-lifetimes"})
        _write(vault / "Wiki" / "Rust Lifetimes.md",
               {"type": "living/wiki", "tags": []})
        result = migrate_to_0_31_0.migrate_vault(str(vault), apply=True, router=router)
        plans = {p["path"]: p for p in result["phase1"]["plans"]}
        new_plan = plans["Wiki/Rust Lifetimes.md"]
        assert new_plan["key"] == "rust-lifetimes-2"


# ---------------------------------------------------------------------------
# Phase 1 — folder-residency parent + self-tag backfill
# ---------------------------------------------------------------------------

class TestPhase1ParentBackfill:
    def test_backfills_key_and_owner_tag(self, vault, router):
        _write(vault / "Projects" / "Brain.md", {"type": "living/project", "tags": []})
        migrate_to_0_31_0.migrate_vault(str(vault), apply=True, router=router)
        fields, _ = parse_frontmatter((vault / "Projects" / "Brain.md").read_text())
        assert fields["key"] == "brain"
        assert "project/brain" in fields["tags"]

    def test_backfills_cross_type_parent_from_folder(self, vault, router):
        _write(vault / "Projects" / "Brain.md", {"type": "living/project", "tags": []})
        ideas = vault / "Ideas" / "project~brain"
        ideas.mkdir(parents=True)
        _write(ideas / "Child Idea.md", {"type": "living/idea", "tags": []})

        router = compile_router.compile(str(vault))
        migrate_to_0_31_0.migrate_vault(str(vault), apply=True, router=router)

        fields, _ = parse_frontmatter((ideas / "Child Idea.md").read_text())
        assert fields["parent"] == "project/brain"
        assert "project/brain" in fields["tags"]
        assert fields["key"] == "child-idea"

    def test_backfills_same_type_parent_from_folder(self, vault, router):
        _write(vault / "Wiki" / "Brain.md", {"type": "living/wiki", "tags": []})
        child_dir = vault / "Wiki" / "brain"
        child_dir.mkdir()
        _write(child_dir / "Child Page.md", {"type": "living/wiki", "tags": []})

        router = compile_router.compile(str(vault))
        migrate_to_0_31_0.migrate_vault(str(vault), apply=True, router=router)

        fields, _ = parse_frontmatter((child_dir / "Child Page.md").read_text())
        assert fields["parent"] == "wiki/brain"
        assert fields["key"] == "child-page"

    def test_idempotent(self, vault, router):
        _write(vault / "Projects" / "Brain.md", {"type": "living/project", "tags": []})
        migrate_to_0_31_0.migrate_vault(str(vault), apply=True, router=router)

        second_router = compile_router.compile(str(vault))
        second = migrate_to_0_31_0.migrate_vault(
            str(vault), apply=True, router=second_router
        )
        assert second["phase1"]["planned"] == 0
        assert second["phase2"]["planned"] == 0


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_does_not_write(self, vault, router):
        path = vault / "Wiki" / "Dry Run Test.md"
        _write(path, {"type": "living/wiki", "tags": []})
        before = path.read_text()

        result = migrate_to_0_31_0.migrate_vault(str(vault), apply=False, router=router)

        assert result["dry_run"] is True
        assert result["phase1"]["planned"] == 1
        assert result["phase1"]["applied"] == 0
        assert path.read_text() == before


# ---------------------------------------------------------------------------
# Phase 2 — children folder relocations
# ---------------------------------------------------------------------------

class TestPhase2Relocations:
    def test_same_type_subfolder_lowercased(self, vault, router):
        _write(vault / "Wiki" / "Claude Code.md",
               {"type": "living/wiki", "tags": [], "key": "claude-code"})
        _write(vault / "Wiki" / "Claude Code" / "Sub Page.md",
               {"type": "living/wiki", "tags": [],
                "key": "sub-page", "parent": "wiki/claude-code"})

        router = compile_router.compile(str(vault))
        result = migrate_to_0_31_0.migrate_vault(str(vault), apply=True, router=router)

        moves = result["phase2"]["moves"]
        assert len(moves) == 1
        assert moves[0]["source"] == "Wiki/Claude Code/Sub Page.md"
        assert moves[0]["dest"] == "Wiki/claude-code/Sub Page.md"
        assert (vault / "Wiki" / "claude-code" / "Sub Page.md").is_file()
        assert not (vault / "Wiki" / "Claude Code" / "Sub Page.md").exists()

    def test_cross_type_subfolder_adds_prefix(self, vault, router):
        _write(vault / "Projects" / "Brain.md",
               {"type": "living/project", "tags": ["project/brain"], "key": "brain"})
        _write(vault / "Releases" / "brain" / "v0.28.6.md",
               {"type": "living/release", "tags": [], "key": "v0-28-6",
                "parent": "project/brain"})

        router = compile_router.compile(str(vault))
        result = migrate_to_0_31_0.migrate_vault(str(vault), apply=True, router=router)

        moves = result["phase2"]["moves"]
        assert any(
            m["source"] == "Releases/brain/v0.28.6.md"
            and m["dest"] == "Releases/project~brain/v0.28.6.md"
            for m in moves
        ), moves
        assert (vault / "Releases" / "project~brain" / "v0.28.6.md").is_file()

    def test_single_tag_fallback_without_parent_field(self, vault, router):
        """A child with no `parent:` field but a single resolvable tag still relocates,
        and the inferred parent is written back into frontmatter so the check suite
        no longer warns on the next run."""
        _write(vault / "Projects" / "Brain.md",
               {"type": "living/project", "tags": ["project/brain"], "key": "brain"})
        _write(vault / "Releases" / "brain" / "v0.1.0.md",
               {"type": "living/release", "tags": ["project/brain"], "key": "v0-1-0"})

        router = compile_router.compile(str(vault))
        result = migrate_to_0_31_0.migrate_vault(str(vault), apply=True, router=router)

        moves = result["phase2"]["moves"]
        assert any(m["dest"] == "Releases/project~brain/v0.1.0.md" for m in moves), moves

        moved = vault / "Releases" / "project~brain" / "v0.1.0.md"
        assert moved.is_file()
        content = moved.read_text()
        assert "parent: project/brain" in content

    def test_terminal_status_subfolder_preserved(self, vault, router):
        _write(vault / "Projects" / "Brain.md",
               {"type": "living/project", "tags": ["project/brain"], "key": "brain"})
        _write(vault / "Releases" / "brain" / "+Shipped" / "v0.28.6.md",
               {"type": "living/release", "tags": [], "key": "v0-28-6",
                "status": "shipped", "parent": "project/brain"})

        router = compile_router.compile(str(vault))
        result = migrate_to_0_31_0.migrate_vault(str(vault), apply=True, router=router)

        moves = result["phase2"]["moves"]
        assert any(
            m["dest"] == "Releases/project~brain/+Shipped/v0.28.6.md"
            for m in moves
        ), moves
        assert (vault / "Releases" / "project~brain" / "+Shipped" / "v0.28.6.md").is_file()

    def test_wikilink_updated_on_move(self, vault, router):
        _write(vault / "Wiki" / "Claude Code.md",
               {"type": "living/wiki", "tags": [], "key": "claude-code"})
        _write(vault / "Wiki" / "Claude Code" / "Sub Page.md",
               {"type": "living/wiki", "tags": [],
                "key": "sub-page", "parent": "wiki/claude-code"})
        (vault / "Wiki" / "Index.md").write_text(
            "---\ntype: living/wiki\ntags: []\nkey: index\n---\n\n"
            "See [[Sub Page]] for details.\n"
        )

        router = compile_router.compile(str(vault))
        migrate_to_0_31_0.migrate_vault(str(vault), apply=True, router=router)

        content = (vault / "Wiki" / "Index.md").read_text()
        # Direct basename link survives the move; rename_and_update_links
        # handles both path-style and basename-style references.
        assert "Sub Page" in content

    def test_orphan_reported_not_moved(self, vault, router):
        """Child under a non-existent parent — reported as orphan, not moved."""
        _write(vault / "Wiki" / "no-parent-here" / "Page.md",
               {"type": "living/wiki", "tags": [], "key": "page"})

        router = compile_router.compile(str(vault))
        result = migrate_to_0_31_0.migrate_vault(str(vault), apply=True, router=router)

        orphans = result["phase2"]["orphans"]
        assert any(o["path"] == "Wiki/no-parent-here/Page.md" for o in orphans)
        assert (vault / "Wiki" / "no-parent-here" / "Page.md").is_file()

    def test_hub_with_self_tag_not_moved(self, vault, router):
        """Projects/Obsidian Brain.md has tags=[project/brain] — must not self-move."""
        _write(vault / "Projects" / "Obsidian Brain.md",
               {"type": "living/project", "tags": ["project/brain"]})

        router = compile_router.compile(str(vault))
        result = migrate_to_0_31_0.migrate_vault(str(vault), apply=True, router=router)

        assert result["phase2"]["planned"] == 0
        assert (vault / "Projects" / "Obsidian Brain.md").is_file()


# ---------------------------------------------------------------------------
# Phase 3 — workspace reconciliation
# ---------------------------------------------------------------------------

class TestPhase3Workspaces:
    def test_data_folder_renamed(self, vault, router):
        _write(vault / "Workspaces" / "Foo Bar.md",
               {"type": "living/workspace", "tags": ["workspace/foo-bar"],
                "key": "foo-bar"})
        (vault / "_Workspaces" / "Foo Bar").mkdir()
        (vault / "_Workspaces" / "Foo Bar" / "placeholder.md").write_text("x\n")

        router = compile_router.compile(str(vault))
        result = migrate_to_0_31_0.migrate_vault(str(vault), apply=True, router=router)

        renames = result["phase3"]["folder_renames"]
        assert any(r["key"] == "foo-bar" for r in renames), renames
        assert (vault / "_Workspaces" / "foo-bar").is_dir()
        assert not (vault / "_Workspaces" / "Foo Bar").exists()

    def test_registry_key_remapped(self, vault, router):
        _write(vault / "Workspaces" / "Foo Bar.md",
               {"type": "living/workspace", "tags": ["workspace/foo-bar"],
                "key": "foo-bar"})

        local_dir = vault / ".brain" / "local"
        local_dir.mkdir(parents=True)
        (local_dir / "workspaces.json").write_text(
            json.dumps({"workspaces": {"Foo Bar": {"path": "/external/foo-bar"}}})
        )

        router = compile_router.compile(str(vault))
        result = migrate_to_0_31_0.migrate_vault(str(vault), apply=True, router=router)

        remaps = result["phase3"]["registry_remaps"]
        assert any(r["from"] == "Foo Bar" and r["to"] == "foo-bar" for r in remaps)
        registry = json.loads((local_dir / "workspaces.json").read_text())["workspaces"]
        assert "foo-bar" in registry
        assert "Foo Bar" not in registry

    def test_data_folder_renamed_for_completed_workspace(self, vault, router):
        completed_dir = vault / "Workspaces" / "+Completed"
        completed_dir.mkdir(parents=True)
        _write(completed_dir / "Foo Bar.md",
               {"type": "living/workspace", "tags": ["workspace/foo-bar"],
                "key": "foo-bar", "status": "completed"})
        (vault / "_Workspaces" / "Foo Bar").mkdir()
        (vault / "_Workspaces" / "Foo Bar" / "placeholder.md").write_text("x\n")

        router = compile_router.compile(str(vault))
        result = migrate_to_0_31_0.migrate_vault(str(vault), apply=True, router=router)

        renames = result["phase3"]["folder_renames"]
        assert any(r["key"] == "foo-bar" for r in renames), renames
        assert (vault / "_Workspaces" / "foo-bar").is_dir()
        assert not (vault / "_Workspaces" / "Foo Bar").exists()
        assert not (vault / "_Workspaces" / "+Completed").exists()

    def test_workspace_with_matching_key_folder_untouched(self, vault, router):
        _write(vault / "Workspaces" / "Foo Bar.md",
               {"type": "living/workspace", "tags": ["workspace/foo-bar"],
                "key": "foo-bar"})
        (vault / "_Workspaces" / "foo-bar").mkdir()

        router = compile_router.compile(str(vault))
        result = migrate_to_0_31_0.migrate_vault(str(vault), apply=True, router=router)

        assert result["phase3"]["folder_renames"] == []


# ---------------------------------------------------------------------------
# Upgrade-runner entry point
# ---------------------------------------------------------------------------

class TestMigrateEntry:
    def test_migrate_reads_compiled_router_from_disk(self, vault):
        _write(vault / "Projects" / "Brain.md", {"type": "living/project", "tags": []})
        local = vault / ".brain" / "local"
        local.mkdir(parents=True, exist_ok=True)
        (local / "compiled-router.json").write_text(
            json.dumps(compile_router.compile(str(vault)))
        )
        result = migrate_to_0_31_0.migrate(str(vault))
        assert result["status"] == "ok"
        assert result["version"] == "0.31.0"
        assert result["phase1"]["applied"] >= 1
