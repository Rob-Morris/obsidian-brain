"""Mixed native policy is migrated by explicit exact-value selection only."""

import json

import pytest

from _bootstrap.approval_clients import Selection, desired_items
from _bootstrap.approval_migration import migrate_codex


def fixture(tmp_path):
    selection = Selection("codex", "user", "cli", tmp_path, tmp_path / "brain")
    desired = desired_items(selection, {json.dumps(["artefact", "read"]): "allow"})
    rule = next(iter(desired))
    original = '# personal rules\nprefix_rule(pattern=["git", "status"], decision="allow")\n' + rule + "\n" + rule + "\n# trailing\n"
    return selection, desired, rule, original


def test_preview_does_not_claim_or_change_matching_legacy_rules(tmp_path):
    selection, desired, _, original = fixture(tmp_path)
    content, receipts, findings = migrate_codex(selection, original, desired, {})
    assert content == original and not receipts
    assert findings and all(status == "legacy_matching" for _, status in findings)


def test_selected_migration_preserves_other_lines_and_remove_restores_duplicates(tmp_path):
    selection, desired, rule, original = fixture(tmp_path)
    _, _, findings = migrate_codex(selection, original, desired, {})
    identity = findings[0][0]
    content, receipts, _ = migrate_codex(selection, original, desired, {}, (identity,))
    assert rule not in content and "git" in content
    restored, receipts, _ = migrate_codex(selection, content, {}, receipts, remove=True)
    assert restored == original and not receipts


def test_reappeared_legacy_rule_is_preserved_not_duplicated_on_remove(tmp_path):
    selection, desired, rule, original = fixture(tmp_path)
    _, _, findings = migrate_codex(selection, original, desired, {})
    content, receipts, _ = migrate_codex(selection, original, desired, {}, (findings[0][0],))
    changed = content + rule + "\n"
    result, retained, findings = migrate_codex(selection, changed, {}, receipts, remove=True)
    assert result == changed and retained
    assert findings[0][1] == "legacy_modified"


def test_grouped_and_raw_script_rules_are_reported_not_adopted(tmp_path):
    selection, desired, _, _ = fixture(tmp_path)
    content = 'prefix_rule(pattern=["brain", ["read", "delete"]], decision="allow")\n'
    _, _, findings = migrate_codex(selection, content, desired, {})
    assert findings[0][1] == "legacy_review_required"
    with pytest.raises(ValueError, match="exact matching"):
        migrate_codex(selection, content, desired, {}, (findings[0][0],))


def test_separated_duplicates_restore_their_original_positions(tmp_path):
    selection, desired, rule, _ = fixture(tmp_path)
    original = rule + "\n# keep between duplicates\n" + rule + "\n# tail\n"
    _, _, findings = migrate_codex(selection, original, desired, {})
    moved, receipts, _ = migrate_codex(selection, original, desired, {}, (findings[0][0],))
    restored, _, _ = migrate_codex(selection, moved, {}, receipts, remove=True)
    assert restored == original
