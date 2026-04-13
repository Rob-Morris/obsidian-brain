"""Tests for migrations/migrate_to_0_19_0.py — Ideas status and transcript naming."""

import pytest

from migrate_to_0_19_0 import migrate


# ---------------------------------------------------------------------------
# Vault fixture helpers
# ---------------------------------------------------------------------------

def make_vault(tmp_path):
    """Create a minimal vault structure."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.18.16\n")
    (bc / "session-core.md").write_text("# Session Core\n")
    return tmp_path


def make_idea(ideas_dir, filename, status, subfolder=None):
    """Create an idea file with frontmatter."""
    target = ideas_dir / subfolder if subfolder else ideas_dir
    target.mkdir(parents=True, exist_ok=True)
    content = f"---\ntype: living/idea\nstatus: {status}\ntags: []\n---\n\n# Idea\n"
    (target / filename).write_text(content)
    return target / filename


# ---------------------------------------------------------------------------
# test_no_ideas_folder
# ---------------------------------------------------------------------------

def test_no_ideas_folder(tmp_path):
    """No Ideas/ folder → returns skipped."""
    vault = make_vault(tmp_path)
    result = migrate(str(vault))
    assert result["status"] == "skipped"
    assert result["actions"] == []


# ---------------------------------------------------------------------------
# test_developing_to_shaping
# ---------------------------------------------------------------------------

def test_developing_to_shaping(tmp_path):
    """Idea with status: developing gets status: shaping."""
    vault = make_vault(tmp_path)
    ideas = vault / "Ideas"
    ideas.mkdir()
    idea_path = make_idea(ideas, "My Idea.md", "developing")

    result = migrate(str(vault))
    assert result["status"] == "ok"
    assert result["ideas_updated"] == 1

    content = idea_path.read_text()
    assert "status: shaping" in content
    assert "status: developing" not in content


# ---------------------------------------------------------------------------
# test_graduated_to_adopted
# ---------------------------------------------------------------------------

def test_graduated_to_adopted(tmp_path):
    """Idea with status: graduated gets status: adopted."""
    vault = make_vault(tmp_path)
    ideas = vault / "Ideas"
    ideas.mkdir()
    idea_path = make_idea(ideas, "Graduated Idea.md", "graduated")

    result = migrate(str(vault))
    assert result["status"] == "ok"
    assert result["ideas_updated"] == 1

    content = idea_path.read_text()
    assert "status: adopted" in content
    assert "status: graduated" not in content


# ---------------------------------------------------------------------------
# test_folder_rename
# ---------------------------------------------------------------------------

def test_folder_rename(tmp_path):
    """Ideas/+Graduated/ is renamed to Ideas/+Adopted/."""
    vault = make_vault(tmp_path)
    ideas = vault / "Ideas"
    graduated = ideas / "+Graduated"
    graduated.mkdir(parents=True)
    make_idea(graduated, "Old Idea.md", "adopted")  # already updated status

    result = migrate(str(vault))
    assert result["status"] == "ok"
    assert result["folder_renamed"] is True

    assert not (ideas / "+Graduated").exists()
    assert (ideas / "+Adopted").exists()
    assert (ideas / "+Adopted" / "Old Idea.md").exists()


def test_folder_rename_skipped_if_already_adopted(tmp_path):
    """Ideas/+Adopted/ already exists, no rename needed."""
    vault = make_vault(tmp_path)
    ideas = vault / "Ideas"
    adopted = ideas / "+Adopted"
    adopted.mkdir(parents=True)
    make_idea(adopted, "Old Idea.md", "adopted")

    result = migrate(str(vault))
    # No folder rename (already +Adopted), no status changes (status is adopted not graduated/developing)
    # So either skipped or folder_renamed=False
    if result["status"] == "ok":
        assert result["folder_renamed"] is False


# ---------------------------------------------------------------------------
# test_transcript_rename
# ---------------------------------------------------------------------------

def test_transcript_rename(tmp_path):
    """yyyymmdd-design-transcript~Title.md → yyyymmdd-shaping-transcript~Title.md."""
    vault = make_vault(tmp_path)
    transcripts_dir = vault / "_Temporal" / "Shaping Transcripts"
    transcripts_dir.mkdir(parents=True)

    old_name = "20260307-design-transcript~Pistols at Dawn Discord Bot.md"
    new_name = "20260307-shaping-transcript~Pistols at Dawn Discord Bot.md"

    (transcripts_dir / old_name).write_text(
        "---\ntype: temporal/shaping-transcripts\ntags: []\n---\n\n# Transcript\n"
    )

    # Create a file with a wikilink to the old transcript
    wiki_dir = vault / "Wiki"
    wiki_dir.mkdir()
    stem_old = f"_Temporal/Shaping Transcripts/{old_name[:-3]}"
    (wiki_dir / "index.md").write_text(
        f"---\ntype: living/wiki\ntags: []\n---\n\nSee [[{stem_old}]].\n"
    )

    result = migrate(str(vault))
    assert result["status"] == "ok"
    assert result["transcripts_renamed"] == 1

    # Old file gone, new file exists
    assert not (transcripts_dir / old_name).exists()
    assert (transcripts_dir / new_name).exists()

    # Wikilink updated in index.md
    index_content = (wiki_dir / "index.md").read_text()
    stem_new = f"_Temporal/Shaping Transcripts/{new_name[:-3]}"
    assert stem_new in index_content
    assert stem_old not in index_content


# ---------------------------------------------------------------------------
# test_transcript_already_shaping
# ---------------------------------------------------------------------------

def test_transcript_already_shaping(tmp_path):
    """yyyymmdd-shaping-transcript~Title.md is not renamed."""
    vault = make_vault(tmp_path)
    transcripts_dir = vault / "_Temporal" / "Shaping Transcripts"
    transcripts_dir.mkdir(parents=True)

    already_good = "20260307-shaping-transcript~My Design.md"
    (transcripts_dir / already_good).write_text(
        "---\ntype: temporal/shaping-transcripts\ntags: []\n---\n\n# Transcript\n"
    )

    result = migrate(str(vault))
    # No changes needed → skipped (no Ideas folder, no old transcripts)
    assert result["status"] == "skipped"
    assert (transcripts_dir / already_good).exists()


# ---------------------------------------------------------------------------
# test_idempotent
# ---------------------------------------------------------------------------

def test_idempotent(tmp_path):
    """Running migration twice produces same result (second run is no-op)."""
    vault = make_vault(tmp_path)
    ideas = vault / "Ideas"
    graduated = ideas / "+Graduated"
    graduated.mkdir(parents=True)
    make_idea(ideas, "My Idea.md", "developing")
    make_idea(graduated, "Done Idea.md", "graduated")

    transcripts_dir = vault / "_Temporal" / "Shaping Transcripts"
    transcripts_dir.mkdir(parents=True)
    (transcripts_dir / "20260307-design-transcript~Title.md").write_text(
        "---\ntype: temporal/shaping-transcripts\ntags: []\n---\n\n# Transcript\n"
    )

    # First run
    result1 = migrate(str(vault))
    assert result1["status"] == "ok"
    assert result1["ideas_updated"] == 2
    assert result1["folder_renamed"] is True
    assert result1["transcripts_renamed"] == 1

    # Second run — everything is already migrated
    result2 = migrate(str(vault))
    assert result2["status"] == "skipped"
    assert result2["actions"] == []
