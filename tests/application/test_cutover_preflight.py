"""Complete-registry and acknowledgement gates for global CLI cutover."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_ROOT = REPO_ROOT / "cli"
SCRIPTS_ROOT = REPO_ROOT / "src" / "brain-core" / "scripts"
for root in (CLI_ROOT, SCRIPTS_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from _launcher.cutover import CutoverPreflightError, preflight  # noqa: E402


def _brain(tmp_path, name, version):
    root = tmp_path / name
    core = root / ".brain-core"
    core.mkdir(parents=True)
    (core / "VERSION").write_text(version + "\n")
    return root.resolve()


def _registry(monkeypatch, tmp_path, lines):
    config = tmp_path / "config"
    path = config / "brain" / "vaults"
    path.parent.mkdir(parents=True)
    path.write_text("# brain registry v2\n" + "".join(lines))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    return path


def _preflight(selected, **kwargs):
    arguments = dict(
        selected_vault=selected,
        source_brain_core_version="0.55.0",
        old_cli_version="1.2.0",
        new_cli_version="2.0.0",
        interface_epoch=1,
        proxy_protocol=2,
        acknowledge_global_cli_cutover=False,
        excluded_stale_brain_ids=(),
    )
    arguments.update(kwargs)
    return preflight(**arguments)


def test_known_other_pre_cutover_brain_requires_exact_global_acknowledgement(
    tmp_path, monkeypatch
):
    selected = _brain(tmp_path, "selected", "0.54.59")
    other = _brain(tmp_path, "other", "0.54.40")
    _registry(
        monkeypatch,
        tmp_path,
        [f"selected\tlocal\t{selected}\n", f"other\tlocal\t{other}\n"],
    )

    with pytest.raises(CutoverPreflightError, match="other"):
        _preflight(selected)

    report = _preflight(selected, acknowledge_global_cli_cutover=True)
    assert report.registry_complete is True
    assert report.selected_brain_ids == ("selected",)
    assert report.affected_brain_ids == ("other",)
    assert report.classified_brains[0].requires_recovery_cli is True
    assert "Restart every MCP client" in report.restart_actions[0]


def test_stale_registry_scope_requires_separate_exact_exclusion(tmp_path, monkeypatch):
    selected = _brain(tmp_path, "selected", "0.54.59")
    missing = tmp_path / "missing"
    _registry(
        monkeypatch,
        tmp_path,
        [f"selected\tlocal\t{selected}\n", f"stale\tlocal\t{missing}\n"],
    )

    with pytest.raises(CutoverPreflightError, match="stale"):
        _preflight(selected)

    report = _preflight(selected, excluded_stale_brain_ids=("stale",))
    assert report.excluded_stale_brain_ids == ("stale",)
    assert report.affected_brain_ids == ()
    with pytest.raises(CutoverPreflightError, match="not stale"):
        _preflight(selected, excluded_stale_brain_ids=("unknown",))


@pytest.mark.parametrize(
    "contents, message",
    (
        ("# brain registry v3\n", "newer than supported"),
        ("# brain registry v2\nbad row\n", "unreadable"),
        ("# brain registry v2\nfuture\tquantum\tvalue\n", "unsupported kind"),
    ),
)
def test_unknown_registry_scope_refuses_before_mutation(
    tmp_path, monkeypatch, contents, message
):
    selected = _brain(tmp_path, "selected", "0.54.59")
    config = tmp_path / "config"
    path = config / "brain" / "vaults"
    path.parent.mkdir(parents=True)
    path.write_text(contents)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))

    with pytest.raises(CutoverPreflightError, match=message):
        _preflight(selected)


def test_newer_other_brain_refuses_instead_of_installing_older_global_cli(
    tmp_path, monkeypatch
):
    selected = _brain(tmp_path, "selected", "0.54.59")
    newer = _brain(tmp_path, "newer", "0.56.0")
    _registry(
        monkeypatch,
        tmp_path,
        [f"selected\tlocal\t{selected}\n", f"newer\tlocal\t{newer}\n"],
    )

    with pytest.raises(CutoverPreflightError, match="newer than"):
        _preflight(selected, acknowledge_global_cli_cutover=True)


def test_selected_brain_must_be_classified_by_the_complete_registry(
    tmp_path, monkeypatch
):
    selected = _brain(tmp_path, "selected", "0.54.59")
    _registry(monkeypatch, tmp_path, [])

    with pytest.raises(CutoverPreflightError, match="not present"):
        _preflight(selected)


@pytest.mark.parametrize(
    "field, value, message",
    (
        ("source_brain_core_version", "0.54.59", "0.55.0"),
        ("old_cli_version", "unknown", "unsupported version"),
        ("new_cli_version", "1.9.9", "2.0.0"),
    ),
)
def test_unclassifiable_or_pre_cutover_source_versions_refuse(
    tmp_path, monkeypatch, field, value, message
):
    selected = _brain(tmp_path, "selected", "0.54.59")
    _registry(monkeypatch, tmp_path, [f"selected\tlocal\t{selected}\n"])

    with pytest.raises(CutoverPreflightError, match=message):
        _preflight(selected, **{field: value})
