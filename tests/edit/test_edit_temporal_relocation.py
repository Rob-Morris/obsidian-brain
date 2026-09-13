"""Temporal relocation fires on parent change only."""

import compile_router
import edit


def _log(vault, rel, *, parent=None):
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    parent_lines = f"  - {parent}\nparent: {parent}\n" if parent else ""
    path.write_text(
        "---\ntype: temporal/logs\ntags:\n  - session\n"
        f"{parent_lines}created: 2026-03-04T09:00:00+10:00\n---\n\nSession.\n"
    )
    return path


class TestRelocationTrigger:
    def test_ordinary_edit_leaves_a_stale_folder_alone(self, vault):
        stale = _log(vault, "_Temporal/Logs/2026-03/log-Session.md")
        router = compile_router.compile(str(vault))
        result = edit.edit_artefact(
            str(vault),
            router,
            "_Temporal/Logs/2026-03/log-Session.md",
            "",
            frontmatter_changes={"created": "2026-04-01T09:00:00+10:00"},
        )
        assert result["path"] == "_Temporal/Logs/2026-03/log-Session.md"
        assert stale.is_file(), "relocation is not the self-heal path; parent_contract reports drift"

    def test_setting_a_parent_relocates_out_of_the_stale_folder(self, vault):
        _log(vault, "_Temporal/Logs/2026-03/log-Session.md")
        router = compile_router.compile(str(vault))
        result = edit.edit_artefact(
            str(vault),
            router,
            "_Temporal/Logs/2026-03/log-Session.md",
            "",
            frontmatter_changes={"parent": "project/brain"},
        )
        assert result["path"] == "_Temporal/Logs/project~brain/log-Session.md"
        assert not (vault / "_Temporal" / "Logs" / "2026-03").exists()
