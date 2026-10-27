"""Collect bounded CI test diagnostics without altering the original logs."""
import argparse
import json
from pathlib import Path


LOG_LIMIT = 256 * 1024
RESOURCE_LIMIT = 64 * 1024
FILE_LIMIT = 8


def _snapshot(source: Path, destination: Path, limit: int) -> dict:
    size = source.stat().st_size
    with source.open("rb") as stream:
        if size <= limit:
            data = stream.read(limit)
        else:
            marker = b"\n[diagnostic snapshot truncated; see manifest.json]\n"
            head = (limit - len(marker)) // 2
            tail = limit - len(marker) - head
            data = stream.read(head) + marker
            stream.seek(-tail, 2)
            data += stream.read(tail)
    destination.write_bytes(data)
    return {"source": source.name, "snapshot": destination.name,
            "source_bytes": size, "snapshot_bytes": len(data), "truncated": size > limit}


def collect(failure_root: Path, resource_log: Path, destination: Path) -> dict:
    """Save at most eight bounded failure logs and one bounded resource log."""
    destination.mkdir(parents=True, exist_ok=False)
    logs = sorted(p for p in failure_root.glob("*.log") if p.is_file() and not p.is_symlink())
    entries = []
    for index, source in enumerate(logs[:FILE_LIMIT], 1):
        entries.append(_snapshot(source, destination / f"failure-{index}.log", LOG_LIMIT))
    if resource_log.is_file() and not resource_log.is_symlink():
        entries.append(_snapshot(resource_log, destination / "resources.log", RESOURCE_LIMIT))
    manifest = {"schema": "brain.test-diagnostics/1", "files": entries,
                "omitted_failure_logs": max(0, len(logs) - FILE_LIMIT),
                "snapshot_bytes": sum(entry["snapshot_bytes"] for entry in entries)}
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main():
    """Snapshot synthetic test evidence for CI artifact upload."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failure-logs", type=Path, required=True)
    parser.add_argument("--resource-log", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(collect(args.failure_logs, args.resource_log, args.destination)))


if __name__ == "__main__":
    main()
