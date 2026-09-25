"""Calendar dates retain their identity; timestamps retain their instant."""

from datetime import date, datetime, timezone

import pytest

from _common._artefacts import parse_date_value, resolve_naming_pattern
from _common._reconcile import reconcile_timestamps


@pytest.mark.parametrize("value", ["2026-09-24", "20260924", "2026-W39-4", date(2026, 9, 24)])
def test_calendar_date_does_not_shift_with_timezone(calendar_timezone, value):
    parsed = parse_date_value(value)
    assert type(parsed) is date
    assert parsed == date(2026, 9, 24)
    assert resolve_naming_pattern(
        "yyyymmdd-log.md", "", {"date": value}, date_source="date"
    ) == "20260924-log.md"


@pytest.mark.parametrize("value", [
    "2026-09-24T00:30:00+00:00",
    "2026-09-24T00:30:00",
    datetime(2026, 9, 24, 0, 30),
    datetime(2026, 9, 24, 0, 30, tzinfo=timezone.utc),
])
def test_timestamp_still_converts_its_instant_to_local_time(calendar_timezone, value):
    parsed = parse_date_value(value)
    expected = datetime(2026, 9, 24, 0, 30, tzinfo=timezone.utc).astimezone()
    assert parsed.isoformat() == expected.isoformat()


@pytest.mark.parametrize("filename", ["20260924-log.md", "2026-09-24 Thu.md"])
def test_filename_date_reconciliation_preserves_calendar_day(calendar_timezone, filename):
    fields = reconcile_timestamps({}, None, filename=filename)
    assert fields["created"] == "2026-09-24"


@pytest.mark.parametrize("calendar_timezone", ["Pacific/Apia"], indirect=True)
def test_skipped_local_day_remains_a_calendar_date(calendar_timezone):
    fields = reconcile_timestamps({}, None, filename="20111230-log.md")
    assert fields["created"] == "2011-12-30"
    assert parse_date_value(fields["created"]) == date(2011, 12, 30)
    assert resolve_naming_pattern(
        "yyyymmdd-log.md", "", fields, date_source="created"
    ) == "20111230-log.md"
