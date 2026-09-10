"""Physical memory footprint of live Brain runtime processes.

Resident set size understates a process that has been compressed or swapped
by two orders of magnitude, so the doctor reads the platform's physical
footprint instead: macOS `footprint` (phys_footprint) and Linux
`/proc/<pid>/status` (VmRSS + VmSwap). Other platforms report no measurement.
"""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

# A baseline session server sits near 100 MB and one that has answered
# semantic queries near 200 MB. Anything past 512 MB means a corpus encode or
# a heavyweight runtime is resident in a long-lived process.
PROCESS_FOOTPRINT_WARN_BYTES = 512 * 1024 * 1024
TOTAL_FOOTPRINT_WARN_BYTES = 2 * 1024 * 1024 * 1024

_UNIT_BYTES = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
_PHYS_FOOTPRINT_RE = re.compile(r"^\s*phys_footprint:\s+([\d.]+)\s*([KMGT]?B)\s*$", re.MULTILINE)
_PROC_FIELD_RE = re.compile(r"^(VmRSS|VmSwap):\s+(\d+)\s+kB", re.MULTILINE)


def measure_footprint_bytes(pid: int) -> int | None:
    """Return the physical footprint of ``pid`` in bytes, or None when unmeasurable."""
    if sys.platform == "darwin":
        return _darwin_footprint(pid)
    if sys.platform.startswith("linux"):
        return _linux_footprint(pid)
    return None


def format_bytes(value: int) -> str:
    """Render a byte count the way `footprint` does: whole MB, or GB with one decimal."""
    if value >= _UNIT_BYTES["GB"]:
        return f"{value / _UNIT_BYTES['GB']:.1f} GB"
    return f"{round(value / _UNIT_BYTES['MB'])} MB"


def _darwin_footprint(pid: int) -> int | None:
    try:
        completed = subprocess.run(
            ["footprint", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return parse_footprint_output(completed.stdout)


def parse_footprint_output(text: str) -> int | None:
    """Extract phys_footprint from `footprint` text output."""
    match = _PHYS_FOOTPRINT_RE.search(text)
    if match is None:
        return None
    return int(float(match.group(1)) * _UNIT_BYTES[match.group(2)])


def _linux_footprint(pid: int) -> int | None:
    try:
        text = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
    except OSError:
        return None
    return parse_proc_status(text)


def parse_proc_status(text: str) -> int | None:
    """Sum VmRSS and VmSwap from a /proc/<pid>/status document."""
    fields = {name: int(value) for name, value in _PROC_FIELD_RE.findall(text)}
    if "VmRSS" not in fields:
        return None
    return (fields["VmRSS"] + fields.get("VmSwap", 0)) * 1024
