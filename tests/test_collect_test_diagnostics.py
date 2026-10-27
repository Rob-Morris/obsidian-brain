"""CI snapshots cap uploaded bytes while retaining first and final evidence."""
import json

from collect_test_diagnostics import collect, FILE_LIMIT, LOG_LIMIT, RESOURCE_LIMIT


def test_snapshots_bound_bytes_and_report_truncation_without_changing_sources(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    original = b"FIRST\n" + b"x" * LOG_LIMIT + b"\nLAST"
    for index in range(FILE_LIMIT + 2):
        (logs / f"{index:02}.log").write_bytes(original)
    resource = tmp_path / "resources.log"
    resource.write_bytes(b"r" * (RESOURCE_LIMIT + 1))
    output = tmp_path / "snapshot"
    manifest = collect(logs, resource, output)
    assert manifest["omitted_failure_logs"] == 2
    assert manifest["snapshot_bytes"] <= FILE_LIMIT * LOG_LIMIT + RESOURCE_LIMIT
    assert all(entry["truncated"] for entry in manifest["files"])
    assert (output / "failure-1.log").read_bytes().startswith(b"FIRST")
    assert (output / "failure-1.log").read_bytes().endswith(b"LAST")
    assert (logs / "00.log").read_bytes() == original
    assert json.loads((output / "manifest.json").read_text()) == manifest
    assert sum(p.stat().st_size for p in output.iterdir()) < 2200000


def test_missing_logs_still_produce_a_resource_manifest(tmp_path):
    resource = tmp_path / "resources.log"
    resource.write_text("filesystem capacity", encoding="utf-8")
    manifest = collect(tmp_path / "absent", resource, tmp_path / "snapshot")
    assert len(manifest["files"]) == 1
    assert manifest["files"][0]["truncated"] is False
