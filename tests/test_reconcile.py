"""Tests for _common._reconcile — the §5 cascade."""

import os
from datetime import datetime, timedelta, timezone

import pytest

from _common._reconcile import (
    reconcile_date_source,
    reconcile_timestamps,
)


# ---------------------------------------------------------------------------
# reconcile_timestamps — universal cascade
# ---------------------------------------------------------------------------

def _touch(path, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    if mtime is not None:
        ts = mtime.timestamp() if isinstance(mtime, datetime) else mtime
        os.utime(str(path), (ts, ts))
    return str(path)


def test_reconcile_passes_through_when_both_present(tmp_path):
    abs_path = _touch(tmp_path / "doc.md")
    fields = {
        "created": "2026-04-10T09:00:00+10:00",
        "modified": "2026-04-15T09:00:00+10:00",
    }
    out = reconcile_timestamps(dict(fields), abs_path)
    assert out["created"] == fields["created"]
    assert out["modified"] == fields["modified"]


def test_reconcile_fills_created_from_filename_prefix(tmp_path):
    abs_path = _touch(tmp_path / "20260410-plan~thing.md")
    out = reconcile_timestamps({}, abs_path)
    assert out["created"].startswith("2026-04-10")


def test_reconcile_fills_created_from_dashed_filename_prefix(tmp_path):
    abs_path = _touch(tmp_path / "2026-04-10 Fri.md")
    out = reconcile_timestamps({"modified": "2026-04-15T09:00:00+10:00"}, abs_path)
    assert out["created"].startswith("2026-04-10")


def test_reconcile_fills_created_from_mtime_when_no_prefix(tmp_path):
    mtime = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
    abs_path = _touch(tmp_path / "My Page.md", mtime=mtime)
    out = reconcile_timestamps({}, abs_path)
    assert out["created"].startswith("2026-03-15")


def test_reconcile_falls_back_to_now_when_no_signal(tmp_path):
    out = reconcile_timestamps({}, str(tmp_path / "does_not_exist.md"))
    today = datetime.now(timezone.utc).astimezone().date().isoformat()
    assert out["created"].startswith(today)
    assert out["modified"].startswith(today)


def test_reconcile_fills_modified_from_mtime(tmp_path):
    mtime = datetime(2026, 3, 20, 9, 0, tzinfo=timezone.utc)
    abs_path = _touch(tmp_path / "doc.md", mtime=mtime)
    out = reconcile_timestamps({"created": "2026-03-15T09:00:00+10:00"}, abs_path)
    assert out["modified"].startswith("2026-03-20")


def test_reconcile_is_idempotent(tmp_path):
    abs_path = _touch(tmp_path / "20260410-plan~thing.md")
    first = reconcile_timestamps({}, abs_path)
    second = reconcile_timestamps(dict(first), abs_path)
    assert first == second


def test_reconcile_frontmatter_wins_on_disagreement(tmp_path):
    """Frontmatter ``created`` is authoritative even if filename prefix disagrees."""
    abs_path = _touch(tmp_path / "20260410-plan~thing.md")
    out = reconcile_timestamps({"created": "2026-02-14T09:00:00+10:00"}, abs_path)
    assert out["created"].startswith("2026-02-14")


def test_reconcile_ignores_garbage_created(tmp_path):
    abs_path = _touch(tmp_path / "20260410-plan~thing.md")
    out = reconcile_timestamps({"created": "not a date"}, abs_path)
    assert out["created"].startswith("2026-04-10")


def test_reconcile_handles_none_abs_path():
    out = reconcile_timestamps({}, None, filename="20260410-plan~thing.md")
    assert out["created"].startswith("2026-04-10")


# ---------------------------------------------------------------------------
# reconcile_date_source — type-aware cascade
# ---------------------------------------------------------------------------

def test_reconcile_date_source_passthrough_when_present(tmp_path):
    abs_path = _touch(tmp_path / "2026-04-10 Fri.md")
    rule = {"date_source": "date"}
    out = reconcile_date_source(
        {"date": "2026-04-10"}, abs_path, "2026-04-10 Fri.md", None, rule,
    )
    assert out["date"] == "2026-04-10"


def test_reconcile_date_source_from_filename_prefix(tmp_path):
    abs_path = _touch(tmp_path / "2026-04-10 Fri.md")
    rule = {"date_source": "date"}
    out = reconcile_date_source({}, abs_path, "2026-04-10 Fri.md", None, rule)
    assert out["date"] == "2026-04-10"


def test_reconcile_date_source_falls_back_to_created(tmp_path):
    abs_path = _touch(tmp_path / "My Writing.md")
    rule = {"date_source": "publisheddate"}
    out = reconcile_date_source(
        {"created": "2026-03-20T09:00:00+10:00"},
        abs_path, "My Writing.md", None, rule,
    )
    assert out["publisheddate"] == "2026-03-20"


def test_reconcile_date_source_raises_when_nothing_available(tmp_path):
    abs_path = _touch(tmp_path / "My Writing.md")
    rule = {"date_source": "publisheddate"}
    with pytest.raises(ValueError, match="date_source"):
        reconcile_date_source({}, abs_path, "My Writing.md", None, rule)


def test_reconcile_date_source_noop_for_universal_fields(tmp_path):
    abs_path = _touch(tmp_path / "2026-04-10 Fri.md")
    rule = {"date_source": "created"}
    out = reconcile_date_source({}, abs_path, "2026-04-10 Fri.md", None, rule)
    assert "created" not in out


def test_reconcile_date_source_noop_when_rule_has_no_source(tmp_path):
    abs_path = _touch(tmp_path / "thing.md")
    out = reconcile_date_source({}, abs_path, "thing.md", None, {})
    assert out == {}
