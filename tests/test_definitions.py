"""Guarded definition workflow tests."""

import pytest

from naming_pattern_cases import UNSAFE_NAMING_PATH_PATTERNS

import compile_router
import define


TYPE_DEFINITION = """# Widgets

## Naming

`{Title}.md` in `Widgets/`

## Frontmatter

```yaml
---
type: living/widget
status: new
---
```

## Template

[[_Config/Templates/Living/Widgets]]
"""

TYPE_TEMPLATE = """---
type: living/widget
status: new
---
# {{title}}
"""


def _write_compile_skeleton(vault):
    (vault / ".brain-core").mkdir(exist_ok=True)
    (vault / ".brain-core/VERSION").write_text("test\n")
    (vault / ".brain-core/session-core.md").write_text("Always:\n")
    (vault / "_Config").mkdir(exist_ok=True)
    (vault / "_Config/router.md").write_text("Always:\n")


@pytest.mark.parametrize("pattern", UNSAFE_NAMING_PATH_PATTERNS)
@pytest.mark.parametrize("advanced", [False, True])
def test_type_definition_rejects_path_patterns_without_writes(tmp_path, pattern, advanced):
    before = set(tmp_path.rglob("*"))
    naming = (
        "Primary folder: `Widgets/`\n\n### Rules\n\n"
        "| Match field | Match values | Pattern |\n|---|---|---|\n"
        f"| `status` | `*` | `{pattern}` |"
        if advanced else f"`{pattern}` in `Widgets/`"
    )
    definition = TYPE_DEFINITION.replace("`{Title}.md` in `Widgets/`", naming)
    with pytest.raises(ValueError, match="Naming pattern must be a single filename"):
        define.write_definition(
            str(tmp_path), kind="type", operation="create", name="widgets",
            classification="living", definition=definition, template=TYPE_TEMPLATE,
        )
    assert set(tmp_path.rglob("*")) == before


def test_type_create_validates_identity_and_replace_requires_current_hash(tmp_path):
    result = define.write_definition(
        str(tmp_path),
        kind="type",
        operation="create",
        name="widgets",
        classification="living",
        definition=TYPE_DEFINITION,
        template=TYPE_TEMPLATE,
    )
    path = tmp_path / result["path"]
    assert path.read_text() == TYPE_DEFINITION
    assert result["type"] == "living/widget"

    with pytest.raises(ValueError, match="requires expected SHA-256"):
        define.write_definition(
            str(tmp_path),
            kind="type",
            operation="replace",
            name="widgets",
            classification="living",
            definition=TYPE_DEFINITION + "\nMore.\n",
            template=TYPE_TEMPLATE,
        )

    replacement = TYPE_DEFINITION + "\nMore.\n"
    replaced = define.write_definition(
        str(tmp_path),
        kind="type",
        operation="replace",
        name="widgets",
        classification="living",
        definition=replacement,
        template=TYPE_TEMPLATE,
        expected_sha256=result["sha256"],
        expected_template_sha256=result["template_sha256"],
    )
    assert replaced["before_sha256"] == result["sha256"]
    assert path.read_text() == replacement
    assert (tmp_path / "Widgets").is_dir()


def test_defined_type_round_trips_through_shared_compiler_layout(tmp_path):
    _write_compile_skeleton(tmp_path)
    custom_definition = TYPE_DEFINITION.replace(
        "_Config/Templates/Living/Widgets",
        "_Config/Templates/Living/Widget Blueprint",
    )
    define.write_definition(
        str(tmp_path),
        kind="type",
        operation="create",
        name="widgets",
        classification="living",
        definition=custom_definition,
        template=TYPE_TEMPLATE,
    )

    router = compile_router.compile(str(tmp_path))
    widget = next(art for art in router["artefacts"] if art["key"] == "widgets")

    assert widget["taxonomy_file"] == "_Config/Taxonomy/Living/widgets.md"
    assert widget["template_file"] == "_Config/Templates/Living/Widget Blueprint"


def test_type_rejects_mismatched_classification_before_write(tmp_path):
    with pytest.raises(ValueError, match="classification"):
        define.write_definition(
            str(tmp_path),
            kind="type",
            operation="create",
            name="widgets",
            classification="temporal",
            definition=TYPE_DEFINITION,
            template=TYPE_TEMPLATE,
        )
    assert not (tmp_path / "_Config/Taxonomy/Temporal/widgets.md").exists()


def test_type_create_rolls_back_taxonomy_and_folder_if_template_write_fails(
    tmp_path, monkeypatch
):
    real_safe_write = define.safe_write
    calls = 0

    def fail_second_write(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic template failure")
        return real_safe_write(*args, **kwargs)

    monkeypatch.setattr(define, "safe_write", fail_second_write)
    with pytest.raises(OSError, match="synthetic template failure"):
        define.write_definition(
            str(tmp_path),
            kind="type",
            operation="create",
            name="widgets",
            classification="living",
            definition=TYPE_DEFINITION,
            template=TYPE_TEMPLATE,
        )

    assert not (tmp_path / "_Config/Taxonomy/Living/widgets.md").exists()
    assert not (tmp_path / "_Config/Templates/Living/Widgets.md").exists()
    assert not (tmp_path / "Widgets").exists()


def test_plugin_replace_is_optimistic(tmp_path):
    created = define.write_definition(
        str(tmp_path),
        kind="plugin",
        operation="create",
        name="My Plugin",
        definition="# My plugin\n",
    )
    assert created["path"] == "_Plugins/My Plugin/SKILL.md"
    with pytest.raises(ValueError, match="Definition changed"):
        define.write_definition(
            str(tmp_path),
            kind="plugin",
            operation="replace",
            name="My Plugin",
            definition="# Changed\n",
            expected_sha256="0" * 64,
        )
    assert (tmp_path / created["path"]).read_text() == "# My plugin\n"


def test_trigger_create_replace_delete_preserves_other_router_content(tmp_path):
    target = tmp_path / "_Config/Taxonomy/Temporal/plans.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Plans\n")
    router = tmp_path / "_Config/router.md"
    router.write_text("Conditional:\n- Existing → [[_Config/Taxonomy/Temporal/plans]]\n\nNotes:\nKeep me.\n")

    created = define.update_trigger(
        str(tmp_path),
        operation="create",
        condition="Before a risky change",
        target="_Config/Taxonomy/Temporal/plans",
    )
    assert created["target"] == "_Config/Taxonomy/Temporal/plans"
    assert "Before a risky change" in router.read_text()
    assert "Keep me." in router.read_text()

    replaced = define.update_trigger(
        str(tmp_path),
        operation="replace",
        condition="Before a risky change",
        target="_Config/Taxonomy/Temporal/plans",
        new_condition="Before consequential work",
    )
    assert replaced["condition"] == "Before consequential work"

    define.update_trigger(
        str(tmp_path),
        operation="delete",
        condition="Before consequential work",
        target="_Config/Taxonomy/Temporal/plans",
    )
    assert "Before consequential work" not in router.read_text()
    assert "Existing" in router.read_text()


def test_trigger_replace_target_precondition_fails_without_write(tmp_path):
    target = tmp_path / "_Config/Taxonomy/Temporal/plans.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Plans\n")
    router = tmp_path / "_Config/router.md"
    original = "Conditional:\n- Existing → [[_Config/Taxonomy/Temporal/plans]]\n"
    router.write_text(original)

    with pytest.raises(ValueError, match="precondition failed"):
        define.update_trigger(
            str(tmp_path),
            operation="replace",
            condition="Existing",
            target="wrong/target",
            new_condition="Changed",
        )
    assert router.read_text() == original


def test_definition_cli_success_and_failure_before_mutation(tmp_path, capsys):
    (tmp_path / ".brain-core").mkdir()
    (tmp_path / ".brain-core/VERSION").write_text("test\n")
    document = tmp_path / "plugin.md"
    document.write_text("# CLI plugin\n")
    argv = [
        "--vault", str(tmp_path),
        "plugin", "create",
        "--name", "cli-plugin",
        "--definition-file", str(document),
        "--json",
    ]

    define.main(argv)
    first = capsys.readouterr()
    assert '"path": "_Plugins/cli-plugin/SKILL.md"' in first.out

    before = (tmp_path / "_Plugins/cli-plugin/SKILL.md").read_text()
    with pytest.raises(SystemExit) as exc:
        define.main(argv)
    assert exc.value.code == 1
    assert (tmp_path / "_Plugins/cli-plugin/SKILL.md").read_text() == before
