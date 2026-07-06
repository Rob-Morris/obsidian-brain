"""Tests for migrations/migrate_to_0_50_0.py - recursive owner folders."""

from __future__ import annotations

import json
import builtins

import compile_router
import migrate_to_0_50_0
from _common import PartialApplyError, parse_frontmatter


def _write(path, fields, body=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, list):
            if value:
                lines.append(f"{key}:")
                for item in value:
                    lines.append(f"  - {item}")
            else:
                lines.append(f"{key}: []")
        else:
            lines.append(f"{key}: {value}")
    lines.extend(["---", "", body])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _taxonomy(root, classification, folder, frontmatter_type, *, statuses=None, terminal=None):
    subdir = "Living" if classification == "living" else "Temporal"
    path = root / "_Config" / "Taxonomy" / subdir / f"{folder.lower()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lifecycle = ""
    if statuses:
        lifecycle = (
            "## Lifecycle\n\n"
            "| Status | Meaning |\n|---|---|\n"
            + "".join(f"| `{status}` | {status} |\n" for status in statuses)
            + "\n"
        )
    terminal_text = ""
    if terminal:
        terminal_text = (
            "## Terminal Status\n\n"
            + "\n".join(
                f"When this artefact reaches `{status}` status, move it to `+{status.capitalize()}/`."
                for status in terminal
            )
            + "\n\n"
        )
    path.write_text(
        f"# {folder}\n\n"
        f"## Naming\n\n`{{Title}}.md` in `{folder}/`.\n\n"
        f"{lifecycle}"
        f"{terminal_text}"
        f"## Frontmatter\n\n"
        f"```yaml\n---\ntype: {frontmatter_type}\ntags: []\n---\n```\n",
        encoding="utf-8",
    )


def _setup_vault(tmp_path):
    (tmp_path / ".brain-core").mkdir()
    (tmp_path / ".brain-core" / "VERSION").write_text("0.50.0\n", encoding="utf-8")
    (tmp_path / ".brain-core" / "session-core.md").write_text("# Session Core\n", encoding="utf-8")
    (tmp_path / "_Config").mkdir()
    (tmp_path / "_Config" / "router.md").write_text(
        "Prefer MCP tools.\n\nAlways:\n- Every artefact belongs in a typed folder.\n",
        encoding="utf-8",
    )
    for folder in ("Projects", "Designs", "Ideas", "Wiki"):
        (tmp_path / folder).mkdir()
    (tmp_path / "_Temporal" / "Logs").mkdir(parents=True)

    _taxonomy(tmp_path, "living", "Projects", "living/project")
    _taxonomy(
        tmp_path,
        "living",
        "Designs",
        "living/design",
        statuses=["active", "implemented"],
        terminal=["implemented"],
    )
    _taxonomy(tmp_path, "living", "Ideas", "living/idea")
    _taxonomy(tmp_path, "living", "Wiki", "living/wiki")
    _taxonomy(tmp_path, "temporal", "Logs", "temporal/log")
    return tmp_path


def _router(vault):
    return compile_router.compile(str(vault))


def _write_compiled_router(vault):
    local = vault / ".brain" / "local"
    local.mkdir(parents=True, exist_ok=True)
    router = _router(vault)
    (local / "compiled-router.json").write_text(json.dumps(router, indent=2) + "\n")
    return router


def test_flat_same_type_grandchild_moves_to_recursive_owner_chain(tmp_path):
    vault = _setup_vault(tmp_path)
    _write(vault / "Designs" / "Parent.md", {"type": "living/design", "tags": [], "key": "parent"})
    _write(
        vault / "Designs" / "parent" / "Child.md",
        {"type": "living/design", "tags": [], "key": "child", "parent": "design/parent"},
    )
    _write(
        vault / "Designs" / "child" / "Grand.md",
        {"type": "living/design", "tags": [], "key": "grand", "parent": "design/child"},
        "See [[Designs/child/Grand]].",
    )
    _write(
        vault / "Wiki" / "Reference.md",
        {"type": "living/wiki", "tags": [], "key": "reference"},
        "See [[Designs/child/Grand]].",
    )

    result = migrate_to_0_50_0.migrate_vault(str(vault), apply=True, router=_router(vault))

    assert result["status"] == "ok"
    assert result["move_result"]["links_updated"] == 2
    assert not (vault / "Designs" / "child" / "Grand.md").exists()
    assert (vault / "Designs" / "parent" / "child" / "Grand.md").is_file()
    assert (vault / "Designs" / "parent" / "Child.md").is_file()
    moved_body = (vault / "Designs" / "parent" / "child" / "Grand.md").read_text()
    assert "[[Designs/parent/child/Grand]]" in moved_body
    assert "[[Designs/child/Grand]]" not in moved_body
    assert "[[Designs/parent/child/Grand]]" in (vault / "Wiki" / "Reference.md").read_text()
    assert "[[Designs/child/Grand]]" not in (vault / "Wiki" / "Reference.md").read_text()


def test_cross_type_descendant_moves_under_full_mixed_chain(tmp_path):
    vault = _setup_vault(tmp_path)
    _write(vault / "Projects" / "Brain.md", {"type": "living/project", "tags": [], "key": "brain"})
    _write(
        vault / "Designs" / "project~brain" / "Design Child.md",
        {"type": "living/design", "tags": [], "key": "design-child", "parent": "project/brain"},
    )
    _write(
        vault / "Wiki" / "design~design-child" / "Grand.md",
        {"type": "living/wiki", "tags": [], "key": "grand", "parent": "design/design-child"},
    )

    result = migrate_to_0_50_0.migrate_vault(str(vault), apply=True, router=_router(vault))

    assert result["status"] == "ok"
    assert not (vault / "Wiki" / "design~design-child" / "Grand.md").exists()
    assert (vault / "Wiki" / "project~brain" / "design~design-child" / "Grand.md").is_file()


def test_missing_parent_backfilled_from_immediate_owner_folder_only(tmp_path):
    vault = _setup_vault(tmp_path)
    _write(vault / "Designs" / "Parent.md", {"type": "living/design", "tags": [], "key": "parent"})
    _write(
        vault / "Ideas" / "design~parent" / "Idea.md",
        {"type": "living/idea", "tags": [], "key": "idea"},
    )

    result = migrate_to_0_50_0.migrate_vault(str(vault), apply=True, router=_router(vault))

    assert result["parent_updates"] == [
        {
            "path": "Ideas/design~parent/Idea.md",
            "parent": "design/parent",
            "source": "immediate_owner_folder",
        }
    ]
    fields, _body = parse_frontmatter((vault / "Ideas" / "design~parent" / "Idea.md").read_text())
    assert fields["parent"] == "design/parent"


def test_backfilled_parent_is_visible_to_later_recursive_move_planning(tmp_path):
    vault = _setup_vault(tmp_path)
    _write(vault / "Designs" / "Parent.md", {"type": "living/design", "tags": [], "key": "parent"})
    _write(
        vault / "Designs" / "parent" / "Child.md",
        {"type": "living/design", "tags": [], "key": "child"},
    )
    _write(
        vault / "Wiki" / "design~child" / "Grand.md",
        {"type": "living/wiki", "tags": [], "key": "grand", "parent": "design/child"},
    )

    result = migrate_to_0_50_0.migrate_vault(str(vault), apply=True, router=_router(vault))

    assert result["parent_updates"][0]["path"] == "Designs/parent/Child.md"
    fields, _body = parse_frontmatter((vault / "Designs" / "parent" / "Child.md").read_text())
    assert fields["parent"] == "design/parent"
    assert not (vault / "Wiki" / "design~child" / "Grand.md").exists()
    assert (vault / "Wiki" / "design~parent" / "design~child" / "Grand.md").is_file()


def test_apply_move_partial_failure_preserves_migration_context(tmp_path, monkeypatch):
    vault = _setup_vault(tmp_path)
    child = vault / "Designs" / "parent" / "Child.md"
    _write(vault / "Designs" / "Parent.md", {"type": "living/design", "tags": [], "key": "parent"})
    _write(child, {"type": "living/design", "tags": [], "key": "child"})
    _write(
        vault / "Wiki" / "design~child" / "Grand.md",
        {"type": "living/wiki", "tags": [], "key": "grand", "parent": "design/child"},
    )

    def fail_move(*_args, **_kwargs):
        raise PartialApplyError("move set partially applied — links already rewritten")

    monkeypatch.setattr(migrate_to_0_50_0, "move_and_update_links", fail_move)

    result = migrate_to_0_50_0.migrate_vault(str(vault), apply=True, router=_router(vault))

    assert result["status"] == "error"
    assert result["written_parent_updates"] == ["Designs/parent/Child.md"]
    assert result["move_result"]["status"] == "error"
    assert result["move_result"]["moves"] == [
        {
            "source": "Wiki/design~child/Grand.md",
            "dest": "Wiki/design~parent/design~child/Grand.md",
        },
    ]
    assert "move set partially applied" in result["move_result"]["error"]
    assert "written_parent_updates ['Designs/parent/Child.md']" in result["error"]
    fields, _body = parse_frontmatter(child.read_text())
    assert fields["parent"] == "design/parent"


def test_cli_json_move_partial_failure_preserves_migration_context(
    tmp_path, monkeypatch, capsys
):
    vault = _setup_vault(tmp_path)
    _write(vault / "Designs" / "Parent.md", {"type": "living/design", "tags": [], "key": "parent"})
    _write(vault / "Designs" / "parent" / "Child.md", {"type": "living/design", "tags": [], "key": "child"})
    _write(
        vault / "Wiki" / "design~child" / "Grand.md",
        {"type": "living/wiki", "tags": [], "key": "grand", "parent": "design/child"},
    )
    _write_compiled_router(vault)

    def fail_move(*_args, **_kwargs):
        raise PartialApplyError("move set partially applied — links already rewritten")

    monkeypatch.setattr(migrate_to_0_50_0, "move_and_update_links", fail_move)

    exit_code = migrate_to_0_50_0.main(["--vault", str(vault), "--json"])
    result = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert result["status"] == "error"
    assert result["written_parent_updates"] == ["Designs/parent/Child.md"]
    assert "move set partially applied" in result["move_result"]["error"]


def test_production_migrate_loads_compiled_router_and_applies(tmp_path):
    vault = _setup_vault(tmp_path)
    _write(vault / "Designs" / "Parent.md", {"type": "living/design", "tags": [], "key": "parent"})
    _write(
        vault / "Designs" / "parent" / "Child.md",
        {"type": "living/design", "tags": [], "key": "child", "parent": "design/parent"},
    )
    _write(
        vault / "Designs" / "child" / "Grand.md",
        {"type": "living/design", "tags": [], "key": "grand", "parent": "design/child"},
    )
    _write_compiled_router(vault)

    result = migrate_to_0_50_0.migrate(str(vault))

    assert result["status"] == "ok"
    assert (vault / "Designs" / "parent" / "child" / "Grand.md").is_file()


def test_terminal_status_folder_is_preserved_inside_recursive_chain(tmp_path):
    vault = _setup_vault(tmp_path)
    _write(vault / "Designs" / "Parent.md", {"type": "living/design", "tags": [], "key": "parent"})
    _write(
        vault / "Designs" / "parent" / "Child.md",
        {"type": "living/design", "tags": [], "key": "child", "parent": "design/parent"},
    )
    _write(
        vault / "Designs" / "child" / "+Implemented" / "Grand.md",
        {
            "type": "living/design",
            "tags": [],
            "key": "grand",
            "parent": "design/child",
            "status": "implemented",
        },
    )

    migrate_to_0_50_0.migrate_vault(str(vault), apply=True, router=_router(vault))

    assert (vault / "Designs" / "parent" / "child" / "+Implemented" / "Grand.md").is_file()
    assert not (vault / "Designs" / "child" / "+Implemented" / "Grand.md").exists()


def test_dry_run_reports_without_mutating(tmp_path):
    vault = _setup_vault(tmp_path)
    source = vault / "Designs" / "child" / "Grand.md"
    _write(vault / "Designs" / "Parent.md", {"type": "living/design", "tags": [], "key": "parent"})
    _write(
        vault / "Designs" / "parent" / "Child.md",
        {"type": "living/design", "tags": [], "key": "child", "parent": "design/parent"},
    )
    _write(
        source,
        {"type": "living/design", "tags": [], "key": "grand", "parent": "design/child"},
    )
    before = source.read_text()

    result = migrate_to_0_50_0.migrate_vault(str(vault), apply=False, router=_router(vault))

    assert result["dry_run"] is True
    assert result["moves"] == [
        {
            "source": "Designs/child/Grand.md",
            "dest": "Designs/parent/child/Grand.md",
        }
    ]
    assert source.read_text() == before
    assert not (vault / "Designs" / "parent" / "child" / "Grand.md").exists()


def test_apply_aborts_on_invalid_parent_chain_before_mutating(tmp_path):
    vault = _setup_vault(tmp_path)
    source = vault / "Designs" / "Broken.md"
    _write(
        source,
        {"type": "living/design", "tags": [], "key": "broken", "parent": "design/missing"},
    )
    before = source.read_text()

    dry = migrate_to_0_50_0.migrate_vault(str(vault), apply=False, router=_router(vault))
    applied = migrate_to_0_50_0.migrate_vault(str(vault), apply=True, router=_router(vault))

    assert dry["status"] == "blocked"
    assert dry["invalid_chains"][0]["parent"] == "design/missing"
    assert applied["status"] == "blocked"
    assert source.read_text() == before


def test_upgrade_runner_migrate_reports_blockers_as_error(tmp_path):
    vault = _setup_vault(tmp_path)
    source = vault / "Designs" / "Broken.md"
    _write(
        source,
        {"type": "living/design", "tags": [], "key": "broken", "parent": "design/missing"},
    )
    before = source.read_text()
    _write_compiled_router(vault)

    result = migrate_to_0_50_0.migrate(str(vault))

    assert result["status"] == "error"
    assert result["message"] == "Cannot apply migration with blockers: invalid_chains"
    assert result["invalid_chains"][0]["parent"] == "design/missing"
    assert source.read_text() == before


def test_cli_reports_blocked_for_operator_facing_blockers(tmp_path, capsys):
    vault = _setup_vault(tmp_path)
    source = vault / "Designs" / "Broken.md"
    _write(
        source,
        {"type": "living/design", "tags": [], "key": "broken", "parent": "design/missing"},
    )
    before = source.read_text()
    _write_compiled_router(vault)

    dry_exit = migrate_to_0_50_0.main(["--vault", str(vault), "--dry-run", "--json"])
    dry = json.loads(capsys.readouterr().out)
    apply_exit = migrate_to_0_50_0.main(["--vault", str(vault), "--json"])
    applied = json.loads(capsys.readouterr().out)

    assert dry_exit == 1
    assert dry["status"] == "blocked"
    assert dry["dry_run"] is True
    assert apply_exit == 1
    assert applied["status"] == "blocked"
    assert applied["dry_run"] is False
    assert applied["error"] == "Cannot apply migration with blockers: invalid_chains"
    assert source.read_text() == before


def test_human_output_lists_blocker_details(capsys):
    exit_code = migrate_to_0_50_0._print_human(
        {
            "status": "blocked",
            "parent_updates": [],
            "moves": [],
            "conflicts": [
                {
                    "path": "Designs/folder-parent/Child.md",
                    "existing_parent": "design/canonical",
                    "folder_parent": "design/folder-parent",
                    "reason": "existing parent conflicts with immediate owner folder",
                }
            ],
            "read_errors": [],
            "keyless_living": [],
            "duplicate_keys": [
                {"key": "design/parent", "paths": ["Designs/A.md", "Designs/B.md"]}
            ],
            "invalid_chains": [],
            "collisions": [
                {
                    "source": "Wiki/a.md",
                    "dest": "Wiki/out.md",
                    "reason": "Destination file already exists: Wiki/out.md",
                }
            ],
            "cyclic_moves": [],
        }
    )

    output = capsys.readouterr().out

    assert exit_code == 1
    assert "duplicate key: design/parent (Designs/A.md, Designs/B.md)" in output
    assert "conflict: Designs/folder-parent/Child.md" in output
    assert "existing=design/canonical folder=design/folder-parent" in output
    assert (
        "collision: Wiki/a.md -> Wiki/out.md "
        "(Destination file already exists: Wiki/out.md)"
    ) in output


def test_unreadable_living_file_blocks_without_moving_parent(tmp_path, monkeypatch):
    vault = _setup_vault(tmp_path)
    parent = vault / "Designs" / "Parent.md"
    child = vault / "Designs" / "parent" / "Child.md"
    _write(parent, {"type": "living/design", "tags": [], "key": "parent", "parent": "project/brain"})
    _write(vault / "Projects" / "Brain.md", {"type": "living/project", "tags": [], "key": "brain"})
    _write(child, {"type": "living/design", "tags": [], "key": "child", "parent": "design/parent"})
    before = parent.read_text()
    original_open = builtins.open

    def fail_child(path, *args, **kwargs):
        if str(path).endswith("Designs/parent/Child.md"):
            raise OSError("cloud placeholder not materialised")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(migrate_to_0_50_0, "open", fail_child, raising=False)

    dry = migrate_to_0_50_0.migrate_vault(str(vault), apply=False, router=_router(vault))
    applied = migrate_to_0_50_0.migrate_vault(str(vault), apply=True, router=_router(vault))

    assert dry["status"] == "blocked"
    assert dry["read_errors"][0]["path"] == "Designs/parent/Child.md"
    assert applied["status"] == "blocked"
    assert parent.read_text() == before
    assert parent.is_file()


def test_unreadable_non_artefact_markdown_blocks_before_parent_backfill(tmp_path, monkeypatch):
    vault = _setup_vault(tmp_path)
    parent = vault / "Designs" / "Parent.md"
    child = vault / "Designs" / "parent" / "Child.md"
    unreadable = vault / "Notes" / "Unreadable.md"
    _write(parent, {"type": "living/design", "tags": [], "key": "parent"})
    _write(child, {"type": "living/design", "tags": [], "key": "child"})
    unreadable.parent.mkdir(parents=True, exist_ok=True)
    unreadable.write_text("not an artefact, but still a markdown note\n", encoding="utf-8")
    child_before = child.read_text()
    original_open = builtins.open

    def fail_unreadable(path, *args, **kwargs):
        if str(path).endswith("Notes/Unreadable.md"):
            raise OSError("cloud placeholder not materialised")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(migrate_to_0_50_0, "open", fail_unreadable, raising=False)

    dry = migrate_to_0_50_0.migrate_vault(str(vault), apply=False, router=_router(vault))
    applied = migrate_to_0_50_0.migrate_vault(str(vault), apply=True, router=_router(vault))

    assert dry["status"] == "blocked"
    assert dry["read_errors"][0]["path"] == "Notes/Unreadable.md"
    assert dry["parent_updates"] == [{"path": "Designs/parent/Child.md", "parent": "design/parent", "source": "immediate_owner_folder"}]
    assert applied["status"] == "blocked"
    assert child.read_text() == child_before
    assert child.is_file()
    assert not (vault / "Designs" / "parent" / "child" / "Child.md").exists()


def test_keyless_living_file_blocks_without_moving_neighbours(tmp_path):
    vault = _setup_vault(tmp_path)
    source = vault / "Designs" / "child" / "Grand.md"
    _write(vault / "Designs" / "Parent.md", {"type": "living/design", "tags": [], "key": "parent"})
    _write(
        vault / "Designs" / "parent" / "Child.md",
        {"type": "living/design", "tags": [], "key": "child", "parent": "design/parent"},
    )
    _write(vault / "Designs" / "parent" / "Keyless.md", {"type": "living/design", "tags": []})
    _write(
        source,
        {"type": "living/design", "tags": [], "key": "grand", "parent": "design/child"},
    )

    dry = migrate_to_0_50_0.migrate_vault(str(vault), apply=False, router=_router(vault))
    applied = migrate_to_0_50_0.migrate_vault(str(vault), apply=True, router=_router(vault))

    assert dry["status"] == "blocked"
    assert dry["keyless_living"][0]["path"] == "Designs/parent/Keyless.md"
    assert dry["moves"] == []
    assert applied["status"] == "blocked"
    assert source.is_file()


def test_duplicate_key_blocks_without_moving(tmp_path):
    vault = _setup_vault(tmp_path)
    source = vault / "Designs" / "child" / "Grand.md"
    _write(vault / "Designs" / "Parent.md", {"type": "living/design", "tags": [], "key": "parent"})
    _write(
        vault / "Designs" / "parent" / "Child.md",
        {"type": "living/design", "tags": [], "key": "child", "parent": "design/parent"},
    )
    _write(source, {"type": "living/design", "tags": [], "key": "grand", "parent": "design/child"})
    router = _router(vault)
    _write(vault / "Designs" / "Duplicate.md", {"type": "living/design", "tags": [], "key": "parent"})

    dry = migrate_to_0_50_0.migrate_vault(str(vault), apply=False, router=router)
    applied = migrate_to_0_50_0.migrate_vault(str(vault), apply=True, router=router)

    assert dry["status"] == "blocked"
    assert dry["duplicate_keys"][0]["key"] == "design/parent"
    assert dry["moves"] == []
    assert applied["status"] == "blocked"
    assert source.is_file()


def test_collision_blocks_without_moving(tmp_path):
    vault = _setup_vault(tmp_path)
    _write(vault / "Designs" / "Parent.md", {"type": "living/design", "tags": [], "key": "parent"})
    _write(
        vault / "Designs" / "parent" / "Child.md",
        {"type": "living/design", "tags": [], "key": "child"},
    )
    clean_source = vault / "Ideas" / "design~child" / "Grand A.md"
    colliding_source = vault / "Wiki" / "design~child" / "Grand B.md"
    collision = vault / "Wiki" / "design~parent" / "design~child" / "Grand B.md"
    _write(clean_source, {"type": "living/idea", "tags": [], "key": "grand-a", "parent": "design/child"})
    _write(colliding_source, {"type": "living/wiki", "tags": [], "key": "grand-b", "parent": "design/child"})
    _write(collision, {"type": "living/wiki", "tags": [], "key": "other", "parent": "design/child"})
    child_before = (vault / "Designs" / "parent" / "Child.md").read_text()

    dry = migrate_to_0_50_0.migrate_vault(str(vault), apply=False, router=_router(vault))
    applied = migrate_to_0_50_0.migrate_vault(str(vault), apply=True, router=_router(vault))

    assert dry["status"] == "blocked"
    assert dry["moves"] == [
        {
            "source": "Ideas/design~child/Grand A.md",
            "dest": "Ideas/design~parent/design~child/Grand A.md",
        },
        {
            "source": "Wiki/design~child/Grand B.md",
            "dest": "Wiki/design~parent/design~child/Grand B.md",
        },
    ]
    assert dry["collisions"][0]["dest"] == "Wiki/design~parent/design~child/Grand B.md"
    assert applied["status"] == "blocked"
    assert (vault / "Designs" / "parent" / "Child.md").read_text() == child_before
    assert clean_source.is_file()
    assert colliding_source.is_file()
    assert collision.is_file()


def test_destination_parent_file_blocks_without_parent_writes_or_moves(tmp_path):
    vault = _setup_vault(tmp_path)
    _write(vault / "Designs" / "Parent.md", {"type": "living/design", "tags": [], "key": "parent"})
    child = vault / "Designs" / "parent" / "Child.md"
    grand = vault / "Wiki" / "design~child" / "Grand.md"
    _write(child, {"type": "living/design", "tags": [], "key": "child"})
    _write(grand, {"type": "living/wiki", "tags": [], "key": "grand", "parent": "design/child"})
    (vault / "Wiki" / "design~parent").write_text("not a directory\n", encoding="utf-8")
    child_before = child.read_text(encoding="utf-8")

    dry = migrate_to_0_50_0.migrate_vault(str(vault), apply=False, router=_router(vault))
    applied = migrate_to_0_50_0.migrate_vault(str(vault), apply=True, router=_router(vault))

    assert dry["status"] == "blocked"
    assert dry["collisions"] == [
        {
            "source": None,
            "dest": "Wiki/design~parent/design~child/Grand.md",
            "reason": (
                "Destination parent is not a directory: Wiki/design~parent "
                "for Wiki/design~parent/design~child/Grand.md"
            ),
        }
    ]
    assert applied["status"] == "blocked"
    assert applied["collisions"] == dry["collisions"]
    assert child.read_text(encoding="utf-8") == child_before
    assert grand.is_file()
    assert not (vault / "Wiki" / "design~parent" / "design~child" / "Grand.md").exists()


def test_broken_symlink_destination_parent_blocks_without_parent_writes_or_moves(tmp_path):
    vault = _setup_vault(tmp_path)
    _write(vault / "Designs" / "Parent.md", {"type": "living/design", "tags": [], "key": "parent"})
    child = vault / "Designs" / "parent" / "Child.md"
    grand = vault / "Wiki" / "design~child" / "Grand.md"
    _write(child, {"type": "living/design", "tags": [], "key": "child"})
    _write(grand, {"type": "living/wiki", "tags": [], "key": "grand", "parent": "design/child"})
    blocked = vault / "Wiki" / "design~parent"
    blocked.symlink_to(vault / "Wiki" / "missing-target")
    child_before = child.read_text(encoding="utf-8")

    dry = migrate_to_0_50_0.migrate_vault(str(vault), apply=False, router=_router(vault))
    applied = migrate_to_0_50_0.migrate_vault(str(vault), apply=True, router=_router(vault))

    assert dry["status"] == "blocked"
    assert dry["collisions"] == [
        {
            "source": None,
            "dest": "Wiki/design~parent/design~child/Grand.md",
            "reason": (
                "Destination parent is not a directory: Wiki/design~parent "
                "for Wiki/design~parent/design~child/Grand.md"
            ),
        }
    ]
    assert applied["status"] == "blocked"
    assert applied["collisions"] == dry["collisions"]
    assert child.read_text(encoding="utf-8") == child_before
    assert grand.is_file()
    assert blocked.is_symlink()
    assert not (vault / "Wiki" / "missing-target" / "design~child" / "Grand.md").exists()


def test_cyclic_move_set_blocks_before_parent_writes(tmp_path, monkeypatch):
    vault = _setup_vault(tmp_path)
    child = vault / "Designs" / "parent" / "Child.md"
    other = vault / "Designs" / "other" / "Other.md"
    _write(vault / "Designs" / "Parent.md", {"type": "living/design", "tags": [], "key": "parent"})
    _write(child, {"type": "living/design", "tags": [], "key": "child"})
    _write(other, {"type": "living/design", "tags": [], "key": "other"})
    before = child.read_text()

    monkeypatch.setattr(
        migrate_to_0_50_0,
        "_plan_moves",
        lambda records, router: [
            {"source": "Designs/parent/Child.md", "dest": "Designs/other/Other.md"},
            {"source": "Designs/other/Other.md", "dest": "Designs/parent/Child.md"},
        ],
    )

    dry = migrate_to_0_50_0.migrate_vault(str(vault), apply=False, router=_router(vault))
    applied = migrate_to_0_50_0.migrate_vault(str(vault), apply=True, router=_router(vault))

    assert dry["status"] == "blocked"
    assert "Cyclic move set" in dry["cyclic_moves"][0]["error"]
    assert applied["status"] == "blocked"
    assert child.read_text() == before


def test_aliased_duplicate_destination_blocks_before_parent_writes(tmp_path, monkeypatch):
    vault = _setup_vault(tmp_path)
    child = vault / "Designs" / "parent" / "Child.md"
    _write(vault / "Designs" / "Parent.md", {"type": "living/design", "tags": [], "key": "parent"})
    _write(child, {"type": "living/design", "tags": [], "key": "child"})
    _write(vault / "Wiki" / "a.md", {"type": "living/wiki", "tags": [], "key": "a"})
    _write(vault / "Wiki" / "b.md", {"type": "living/wiki", "tags": [], "key": "b"})
    before = child.read_text()

    monkeypatch.setattr(
        migrate_to_0_50_0,
        "_plan_moves",
        lambda records, router: [
            {"source": "Wiki/a.md", "dest": "Wiki/out.md"},
            {"source": "Wiki/b.md", "dest": "Wiki/./out.md"},
        ],
    )

    dry = migrate_to_0_50_0.migrate_vault(str(vault), apply=False, router=_router(vault))
    applied = migrate_to_0_50_0.migrate_vault(str(vault), apply=True, router=_router(vault))

    assert dry["status"] == "blocked"
    assert "Duplicate move destination" in dry["collisions"][0]["reason"]
    assert applied["status"] == "blocked"
    assert child.read_text() == before
    fields, _ = parse_frontmatter(child.read_text())
    assert "parent" not in fields
    assert not (vault / "Wiki" / "out.md").exists()


def test_aliased_cycle_blocks_before_parent_writes(tmp_path, monkeypatch):
    vault = _setup_vault(tmp_path)
    child = vault / "Designs" / "parent" / "Child.md"
    _write(vault / "Designs" / "Parent.md", {"type": "living/design", "tags": [], "key": "parent"})
    _write(child, {"type": "living/design", "tags": [], "key": "child"})
    _write(vault / "Wiki" / "a.md", {"type": "living/wiki", "tags": [], "key": "a"})
    _write(vault / "Wiki" / "b.md", {"type": "living/wiki", "tags": [], "key": "b"})
    before = child.read_text()

    monkeypatch.setattr(
        migrate_to_0_50_0,
        "_plan_moves",
        lambda records, router: [
            {"source": "Wiki/./a.md", "dest": "Wiki/b.md"},
            {"source": "Wiki/b.md", "dest": "Wiki/a.md"},
        ],
    )

    dry = migrate_to_0_50_0.migrate_vault(str(vault), apply=False, router=_router(vault))
    applied = migrate_to_0_50_0.migrate_vault(str(vault), apply=True, router=_router(vault))

    assert dry["status"] == "blocked"
    assert "Cyclic move set" in dry["cyclic_moves"][0]["error"]
    assert applied["status"] == "blocked"
    assert child.read_text() == before
    fields, _ = parse_frontmatter(child.read_text())
    assert "parent" not in fields
    assert (vault / "Wiki" / "a.md").is_file()
    assert (vault / "Wiki" / "b.md").is_file()


def test_relocating_intermediate_and_grandchild_move_in_one_batch(tmp_path):
    vault = _setup_vault(tmp_path)
    _write(vault / "Designs" / "Parent.md", {"type": "living/design", "tags": [], "key": "parent"})
    _write(
        vault / "Designs" / "child" / "Child.md",
        {"type": "living/design", "tags": [], "key": "child", "parent": "design/parent"},
    )
    _write(
        vault / "Wiki" / "design~child" / "Grand.md",
        {"type": "living/wiki", "tags": [], "key": "grand", "parent": "design/child"},
    )

    dry = migrate_to_0_50_0.migrate_vault(str(vault), apply=False, router=_router(vault))
    result = migrate_to_0_50_0.migrate_vault(str(vault), apply=True, router=_router(vault))

    assert dry["moves"] == [
        {"source": "Designs/child/Child.md", "dest": "Designs/parent/Child.md"},
        {"source": "Wiki/design~child/Grand.md", "dest": "Wiki/design~parent/design~child/Grand.md"},
    ]
    assert result["move_result"]["moves"] == dry["moves"]
    assert (vault / "Designs" / "parent" / "Child.md").is_file()
    assert (vault / "Wiki" / "design~parent" / "design~child" / "Grand.md").is_file()


def test_conflicting_folder_parent_is_reported_but_canonical_parent_wins(tmp_path):
    vault = _setup_vault(tmp_path)
    _write(vault / "Designs" / "Canonical.md", {"type": "living/design", "tags": [], "key": "canonical"})
    _write(vault / "Designs" / "Folder Parent.md", {"type": "living/design", "tags": [], "key": "folder-parent"})
    _write(
        vault / "Designs" / "folder-parent" / "Child.md",
        {
            "type": "living/design",
            "tags": [],
            "key": "child",
            "parent": "design/canonical",
        },
    )

    result = migrate_to_0_50_0.migrate_vault(str(vault), apply=True, router=_router(vault))

    assert result["status"] == "ok"
    assert result["conflicts"] == [
        {
            "path": "Designs/folder-parent/Child.md",
            "existing_parent": "design/canonical",
            "folder_parent": "design/folder-parent",
            "reason": "existing parent conflicts with immediate owner folder",
        }
    ]
    assert (vault / "Designs" / "canonical" / "Child.md").is_file()


def test_temporal_artefacts_are_left_in_date_folders(tmp_path):
    vault = _setup_vault(tmp_path)
    _write(vault / "Designs" / "Parent.md", {"type": "living/design", "tags": [], "key": "parent"})
    log = vault / "_Temporal" / "Logs" / "2026-04" / "20260426-log.md"
    _write(
        log,
        {"type": "temporal/log", "tags": [], "parent": "design/parent", "created": "2026-04-26"},
    )

    result = migrate_to_0_50_0.migrate_vault(str(vault), apply=True, router=_router(vault))

    assert result["status"] == "skipped"
    assert log.is_file()


def test_idempotent_after_apply(tmp_path):
    vault = _setup_vault(tmp_path)
    _write(vault / "Designs" / "Parent.md", {"type": "living/design", "tags": [], "key": "parent"})
    _write(
        vault / "Designs" / "parent" / "Child.md",
        {"type": "living/design", "tags": [], "key": "child", "parent": "design/parent"},
    )
    _write(
        vault / "Designs" / "child" / "Grand.md",
        {"type": "living/design", "tags": [], "key": "grand", "parent": "design/child"},
    )

    migrate_to_0_50_0.migrate_vault(str(vault), apply=True, router=_router(vault))
    second = migrate_to_0_50_0.migrate_vault(str(vault), apply=True, router=_router(vault))

    assert second["status"] == "skipped"
    assert second["moves"] == []
    assert second["parent_updates"] == []
