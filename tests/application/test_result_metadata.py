"""Shared delivery fields preserve application and launcher-safe transport semantics."""
from dataclasses import asdict
from pathlib import Path
import subprocess
import sys

from _application.result_navigation import Delivery, delivery_metadata
from _bootstrap.result_metadata import Delivery as SharedDelivery
from brain_mcp._result_content import delivery_descriptor


def test_application_reexports_shared_delivery_with_unchanged_defaults():
    assert Delivery is SharedDelivery
    expected = dict.fromkeys((
        "result_id", "view_id", "unit", "requested_page_size", "page_size", "page",
        "first_item", "last_item", "total_items", "total_pages", "source_lines",
        "fragments", "selection_id", "start_page", "max_pages", "end_page",
        "selected_pages", "remaining_pages", "pages_before_selection",
        "pages_after_selection", "continuation", "outside_continuation", "reason",
        "message", "source_revision", "change_notice", "requested_anchor", "overlap_notice",
    ))
    expected.update(status="unavailable", availability="unavailable", backing="retained",
                    consistency="immutable-output", count_scope="produced-output",
                    has_more=False, has_more_outside_selection=False, superseded=False)
    assert delivery_metadata(Delivery()) == expected
    assert delivery_descriptor() == {
        **expected, "status": "complete", "availability": "available", "backing": "inline",
    }


def test_transport_keeps_arbitrary_overrides_and_shared_anchor_fields():
    overrides = {"status": "unavailable", "availability": None, "backing": "query",
                 "requested_anchor": 0, "overlap_notice": "preceding units",
                 "next_detail": "detail-2", "capacity_limits": {"result_bytes": 100}}
    assert delivery_descriptor(**overrides) == {**delivery_descriptor(), **overrides}
    descriptor = delivery_descriptor()
    descriptor["requested_anchor"] = 10
    assert delivery_descriptor()["requested_anchor"] is None
    assert set(delivery_descriptor()) == set(asdict(SharedDelivery()))


def test_transport_metadata_imports_without_application_or_site_packages():
    root = Path(__file__).resolve().parents[2] / "src/brain-core"
    program = """
import sys
sys.path[:0] = sys.argv[1:]
from brain_mcp._result_content import delivery_descriptor
assert delivery_descriptor()['requested_anchor'] is None
assert delivery_descriptor()['overlap_notice'] is None
assert not any(name == '_application' or name.startswith('_application.') for name in sys.modules)
assert 'mcp' not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-c", program, str(root), str(root / "scripts")],
        capture_output=True, text=True, timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
