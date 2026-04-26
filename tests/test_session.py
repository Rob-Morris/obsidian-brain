"""Tests for session.py — canonical bootstrap model and CLI."""

import json
import sys

import pytest

import session


MINIMAL_SESSION_CORE = "# Session Core\n\n## Core Docs\n\n## Standards\n"


def _minimal_router(vault_root):
    return {
        "meta": {
            "brain_core_version": "0.25.0",
            "compiled_at": "2026-04-12T20:00:00+10:00",
        },
        "environment": {
            "vault_root": str(vault_root),
            "platform": "darwin",
            "cli_available": False,
        },
        "always_rules": ["Keep artefacts in typed folders."],
        "triggers": [],
        "artefacts": [],
        "memories": [],
        "skills": [],
        "plugins": [],
        "styles": [],
    }


class TestBuildSessionModel:
    def test_extracts_core_docs_and_strips_reference_sections(self, tmp_path):
        bc = tmp_path / ".brain-core"
        bc.mkdir()
        (bc / "session-core.md").write_text(
            "# Session Core\n\n"
            "## Principles\n\n"
            "Keep instruction files lean.\n\n"
            "## Core Docs\n\n"
            "- [Extend the vault](standards/extending/README.md)\n"
            "- malformed bullet without a loadable link\n\n"
            "## Standards\n\n"
            "- [[.brain-core/standards/provenance|Track provenance]]\n\n"
            "Always:\n"
            "- Prefer `brain_list` for exhaustive enumeration.\n"
        )

        model = session.build_session_model(
            _minimal_router(tmp_path),
            str(tmp_path),
            load_config_if_missing=False,
        )

        assert "## Principles" in model["core_bootstrap"]
        assert "## Core Docs" not in model["core_bootstrap"]
        assert "## Standards" not in model["core_bootstrap"]
        assert "Prefer `brain_list`" not in model["core_bootstrap"]
        assert model["core_docs"] == [
            {
                "section": "Core Docs",
                "docs": [
                    {
                        "title": "Extend the vault",
                        "path": ".brain-core/standards/extending/README.md",
                        "load_with": {
                            "tool": "brain_read",
                            "resource": "file",
                            "name": ".brain-core/standards/extending/README.md",
                        },
                    }
                ],
            },
            {
                "section": "Standards",
                "docs": [
                    {
                        "title": "Track provenance",
                        "path": ".brain-core/standards/provenance.md",
                        "load_with": {
                            "tool": "brain_read",
                            "resource": "file",
                            "name": ".brain-core/standards/provenance.md",
                        },
                    }
                ],
            },
        ]

    def test_includes_workspace_metadata_when_provided(self, tmp_path):
        workspace_dir = tmp_path / "demo-workspace"
        model = session.build_session_model(
            _minimal_router(tmp_path),
            str(tmp_path),
            workspace_dir=str(workspace_dir),
            load_config_if_missing=False,
        )

        assert model["workspace"] == {
            "directory": str(workspace_dir),
            "name": "demo-workspace",
            "location": "external",
        }

    def test_includes_workspace_defaults_and_record_from_manifest(self, tmp_path):
        workspace_dir = tmp_path / "demo-workspace"
        (workspace_dir / ".brain" / "local").mkdir(parents=True)
        (workspace_dir / ".brain" / "local" / "workspace.yaml").write_text(
            "slug: demo-workspace\n"
            "links:\n"
            "  workspace: brain-demo\n"
            "defaults:\n"
            "  tags:\n"
            "    - workspace/brain-demo\n"
            "    - project/brain\n"
        )

        model = session.build_session_model(
            _minimal_router(tmp_path),
            str(tmp_path),
            workspace_dir=str(workspace_dir),
            load_config_if_missing=False,
        )

        assert model["workspace_record"] == {
            "slug": "brain-demo",
            "workspace_mode": "linked",
        }
        assert model["workspace_defaults"] == {
            "tags": ["workspace/brain-demo", "project/brain"],
        }

    def test_falls_back_to_legacy_workspace_manifest(self, tmp_path):
        workspace_dir = tmp_path / "demo-workspace"
        (workspace_dir / ".brain").mkdir(parents=True)
        (workspace_dir / ".brain" / "workspace.yaml").write_text(
            "slug: demo-workspace\n"
            "defaults:\n"
            "  tags:\n"
            "    - workspace/legacy\n"
        )

        model = session.build_session_model(
            _minimal_router(tmp_path),
            str(tmp_path),
            workspace_dir=str(workspace_dir),
            load_config_if_missing=False,
        )

        assert model["workspace_defaults"] == {
            "tags": ["workspace/legacy"],
        }

    def test_ignores_invalid_workspace_manifest(self, tmp_path):
        workspace_dir = tmp_path / "demo-workspace"
        (workspace_dir / ".brain" / "local").mkdir(parents=True)
        (workspace_dir / ".brain" / "local" / "workspace.yaml").write_text("defaults: [broken\n")

        model = session.build_session_model(
            _minimal_router(tmp_path),
            str(tmp_path),
            workspace_dir=str(workspace_dir),
            load_config_if_missing=False,
        )

        assert "workspace_defaults" not in model
        assert "workspace_record" not in model


class TestSessionCli:
    def test_main_writes_session_markdown_and_prints_json(self, tmp_path, monkeypatch, capsys):
        bc = tmp_path / ".brain-core"
        bc.mkdir()
        (bc / "session-core.md").write_text(
            "# Session Core\n\n"
            "## Principles\n\n"
            "Keep instruction files lean.\n\n"
            "## Core Docs\n\n"
            "- [Extend the vault](standards/extending/README.md)\n\n"
            "## Standards\n"
        )

        local = tmp_path / ".brain" / "local"
        local.mkdir(parents=True)
        (local / "compiled-router.json").write_text(
            json.dumps(_minimal_router(tmp_path))
        )

        monkeypatch.setattr(
            sys,
            "argv",
            ["session.py", "--vault", str(tmp_path), "--json"],
        )

        session.main()

        stdout = capsys.readouterr().out
        payload = json.loads(stdout)
        assert payload["core_docs"][0]["docs"][0]["path"] == ".brain-core/standards/extending/README.md"

        session_path = tmp_path / ".brain" / "local" / "session.md"
        assert session_path.exists()
        content = session_path.read_text()
        assert "# Brain Session" in content
        assert "[Extend the vault](../../.brain-core/standards/extending/README.md)" in content

    def test_main_includes_workspace_metadata_when_provided(self, tmp_path, monkeypatch, capsys):
        bc = tmp_path / ".brain-core"
        bc.mkdir()
        (bc / "session-core.md").write_text(MINIMAL_SESSION_CORE)

        local = tmp_path / ".brain" / "local"
        local.mkdir(parents=True)
        (local / "compiled-router.json").write_text(
            json.dumps(_minimal_router(tmp_path))
        )

        workspace_dir = tmp_path / "demo-workspace"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "session.py",
                "--vault", str(tmp_path),
                "--workspace-dir", str(workspace_dir),
                "--json",
            ],
        )

        session.main()

        payload = json.loads(capsys.readouterr().out)
        assert payload["workspace"] == {
            "directory": str(workspace_dir),
            "name": "demo-workspace",
            "location": "external",
        }

        content = (tmp_path / ".brain" / "local" / "session.md").read_text()
        assert "## Workspace" in content
        assert "`name`: `demo-workspace`" in content
        assert f"`directory`: `{workspace_dir}`" in content
        assert "`location`: `external`" in content

    def test_main_includes_workspace_defaults_when_manifest_present(
        self, tmp_path, monkeypatch, capsys
    ):
        bc = tmp_path / ".brain-core"
        bc.mkdir()
        (bc / "session-core.md").write_text(MINIMAL_SESSION_CORE)

        local = tmp_path / ".brain" / "local"
        local.mkdir(parents=True)
        (local / "compiled-router.json").write_text(
            json.dumps(_minimal_router(tmp_path))
        )

        workspace_dir = tmp_path / "demo-workspace"
        (workspace_dir / ".brain" / "local").mkdir(parents=True)
        (workspace_dir / ".brain" / "local" / "workspace.yaml").write_text(
            "slug: demo-workspace\n"
            "defaults:\n"
            "  tags:\n"
            "    - workspace/demo-workspace\n"
            "    - project/brain\n"
        )
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "session.py",
                "--vault", str(tmp_path),
                "--workspace-dir", str(workspace_dir),
                "--json",
            ],
        )

        session.main()

        payload = json.loads(capsys.readouterr().out)
        assert payload["workspace_defaults"] == {
            "tags": ["workspace/demo-workspace", "project/brain"],
        }

        content = (tmp_path / ".brain" / "local" / "session.md").read_text()
        assert "## Workspace Defaults" in content
        assert '`tags`: `["workspace/demo-workspace", "project/brain"]`' in content

    def test_main_errors_when_compiled_router_missing(self, tmp_path, monkeypatch, capsys):
        bc = tmp_path / ".brain-core"
        bc.mkdir()
        (bc / "session-core.md").write_text(MINIMAL_SESSION_CORE)

        monkeypatch.setattr(
            sys,
            "argv",
            ["session.py", "--vault", str(tmp_path)],
        )

        with pytest.raises(SystemExit) as excinfo:
            session.main()

        assert excinfo.value.code == 1
        assert "compiled router not found" in capsys.readouterr().err
