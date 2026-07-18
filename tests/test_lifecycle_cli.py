import contextlib

import pytest

import lifecycle


@pytest.mark.parametrize(
    "extra",
    [[], ["--parent", "wiki/parent", "--clear"]],
)
def test_reparent_requires_exactly_one_parent_mode(tmp_path, extra):
    with pytest.raises(SystemExit) as exc_info:
        lifecycle.main(["reparent", "Wiki/Child.md", *extra, "--vault", str(tmp_path)])

    assert exc_info.value.code == 2


def test_reparent_clear_passes_none_to_lifecycle_engine(tmp_path, monkeypatch, capsys):
    captured = {}
    monkeypatch.setattr(lifecycle, "find_vault_root", lambda _vault: tmp_path)
    monkeypatch.setattr(lifecycle, "load_fresh_compiled_router", lambda _vault: {})
    monkeypatch.setattr(
        lifecycle, "vault_mutation_lock", lambda _vault: contextlib.nullcontext()
    )

    def update(_vault, _router, path, field, value):
        captured.update(path=path, field=field, value=value)
        return {
            "old_value": "wiki/parent",
            "new_value": value,
            "resolved_path": path,
            "path": path,
        }

    monkeypatch.setattr(lifecycle.edit, "update_lifecycle_field", update)

    lifecycle.main([
        "reparent", "Wiki/Child.md", "--clear", "--vault", str(tmp_path), "--json"
    ])

    assert captured == {"path": "Wiki/Child.md", "field": "parent", "value": None}
    assert '"new_value": null' in capsys.readouterr().out


@pytest.mark.parametrize(
    ("command", "expected_field", "expected_value"),
    [
        (["reparent", "Wiki/Child.md", "--parent", "wiki/parent"], "parent", "wiki/parent"),
        (["set-status", "Wiki/Child.md", "active"], "status", "active"),
        (["set-key", "Wiki/Child.md", "child"], "key", "child"),
        (["set-naming-field", "Wiki/Child.md", "code", "ABC"], "code", "ABC"),
    ],
)
def test_lifecycle_cli_routes_explicit_field_commands(
    tmp_path, monkeypatch, command, expected_field, expected_value
):
    captured = {}
    monkeypatch.setattr(lifecycle, "find_vault_root", lambda _vault: tmp_path)
    monkeypatch.setattr(lifecycle, "load_fresh_compiled_router", lambda _vault: {})
    monkeypatch.setattr(
        lifecycle, "vault_mutation_lock", lambda _vault: contextlib.nullcontext()
    )

    def update(_vault, _router, path, field, value):
        captured.update(path=path, field=field, value=value)
        return {
            "old_value": None,
            "new_value": value,
            "resolved_path": path,
            "path": path,
        }

    monkeypatch.setattr(lifecycle.edit, "update_lifecycle_field", update)

    lifecycle.main([*command, "--vault", str(tmp_path), "--json"])

    assert captured == {
        "path": "Wiki/Child.md",
        "field": expected_field,
        "value": expected_value,
    }
