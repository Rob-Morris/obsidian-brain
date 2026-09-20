"""Managed policy owns only recorded unchanged items, not Brain-looking names."""

from _bootstrap.approval_ownership import reconcile_items


def test_add_update_remove_preserves_unrelated():
    first = reconcile_items({"brain": "allow"}, {"other": "ask"}, {})
    next = reconcile_items({"brain": "ask"}, first.observed, first.owned)
    assert next.observed == {"brain": "ask", "other": "ask"}
    removed = reconcile_items({}, next.observed, next.owned, remove=True)
    assert removed.observed == {"other": "ask"}


def test_equal_preexisting_is_unowned_until_explicit_adoption():
    first = reconcile_items({"brain": "allow"}, {"brain": "allow"}, {})
    assert not first.owned
    adopted = reconcile_items({"brain": "allow"}, first.observed, {}, adopt=("brain",))
    removed = reconcile_items({}, adopted.observed, adopted.owned, remove=True)
    assert removed.observed == {"brain": "allow"}


def test_user_edit_is_preserved_during_update_and_removal():
    first = reconcile_items({"brain": "allow"}, {}, {})
    edited = reconcile_items({"brain": "allow"}, {"brain": "ask"}, first.owned)
    assert edited.observed == {"brain": "ask"}
    assert edited.findings == (("brain", "modified"),)
    assert reconcile_items({}, edited.observed, edited.owned, remove=True).observed == edited.observed


def test_deleted_rules_are_not_recreated_until_explicit_restore():
    first = reconcile_items({"brain": "allow"}, {}, {})
    deleted = reconcile_items({"brain": "allow"}, {}, first.owned)
    assert deleted.observed == {}
    again = reconcile_items({"brain": "allow"}, {}, deleted.owned, deleted.exclusions)
    assert again.observed == {}
    restored = reconcile_items({"brain": "allow"}, {}, again.owned, again.exclusions, restore=("brain",))
    assert restored.observed == {"brain": "allow"}
    assert restored.exclusions == ()
