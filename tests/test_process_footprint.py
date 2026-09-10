"""Tests for physical footprint measurement of runtime processes."""

from __future__ import annotations

from _machine import process_footprint


FOOTPRINT_TEXT = """
======================================================================
Python [16562]: 64-bit    Footprint: 1.6 GB (16384 bytes per page)
======================================================================

Auxiliary data:
    phys_footprint: 1651 MB
    phys_footprint_peak: 2195 MB
"""

PROC_STATUS = """Name:\tpython3.12
VmPeak:\t 3000000 kB
VmRSS:\t  104448 kB
VmSwap:\t   20480 kB
Threads:\t12
"""


def test_parse_footprint_output_reads_phys_footprint_with_units():
    assert process_footprint.parse_footprint_output(FOOTPRINT_TEXT) == 1651 * 1024**2
    assert process_footprint.parse_footprint_output("    phys_footprint: 1.5 GB\n") == int(1.5 * 1024**3)
    assert process_footprint.parse_footprint_output("    phys_footprint: 8112 KB\n") == 8112 * 1024
    assert process_footprint.parse_footprint_output("no footprint here") is None


def test_parse_proc_status_sums_resident_and_swapped_pages():
    assert process_footprint.parse_proc_status(PROC_STATUS) == (104448 + 20480) * 1024
    assert process_footprint.parse_proc_status("VmRSS:\t 1024 kB\n") == 1024 * 1024
    assert process_footprint.parse_proc_status("Name:\tzombie\n") is None


def test_format_bytes_matches_footprint_units():
    assert process_footprint.format_bytes(104 * 1024**2) == "104 MB"
    assert process_footprint.format_bytes(int(1.65 * 1024**3)) == "1.6 GB"


def test_measure_footprint_bytes_uses_platform_reader(monkeypatch):
    monkeypatch.setattr(process_footprint.sys, "platform", "darwin")
    monkeypatch.setattr(process_footprint, "_darwin_footprint", lambda pid: pid * 10)
    assert process_footprint.measure_footprint_bytes(7) == 70

    monkeypatch.setattr(process_footprint.sys, "platform", "linux")
    monkeypatch.setattr(process_footprint, "_linux_footprint", lambda pid: pid * 100)
    assert process_footprint.measure_footprint_bytes(7) == 700

    monkeypatch.setattr(process_footprint.sys, "platform", "win32")
    assert process_footprint.measure_footprint_bytes(7) is None
