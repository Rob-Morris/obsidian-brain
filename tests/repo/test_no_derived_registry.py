"""The derived machine registry is retired: nothing shipped names its file (DD-083)."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOTS = (REPO_ROOT / "src", REPO_ROOT / "cli")
DERIVED_FILE = "brains.json"


def test_no_shipped_file_reads_or_writes_the_derived_machine_registry():
    # Readers and writers both construct the file name, so the name is the contract.
    offenders = sorted(
        str(path.relative_to(REPO_ROOT))
        for root in SHIPPED_ROOTS
        for path in root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and DERIVED_FILE in path.read_text(encoding="utf-8", errors="ignore")
    )
    assert offenders == []
