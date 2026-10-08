#!/usr/bin/env python3
"""Seed a disposable template vault and exercise installed text commands."""
from __future__ import annotations

import argparse
import codecs
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from historical_upgrade_acceptance import AcceptanceFailure, _portable_manifest, _require_ok_envelope, _run_json

MARKER = "encodingacceptancequokka"
BRAIN_NAME = "Text encoding acceptance"
CLEAR_CODES = ("utf8_bom", "utf16_bom", "utf32_bom", "truncated_utf8")
LEGACY = "Designs/ambiguous_not_utf8.md"
GOOD = "Designs/good_utf8.md"
CONFIG = ".brain/config.yaml"


def note_text(name: str) -> str:
    return (f"---\r\ntype: living/design\r\nkey: {name.replace('_', '-')}\r\n"
            f"status: shaping\r\n---\r\n\r\n# {name}\r\n\r\n{MARKER} café 🦘\r\n")


def seed_fixture(vault: Path) -> dict[str, bytes]:
    """Write exact damage bytes; refuse to overwrite existing fixture notes."""
    notes = {
        f"Designs/{code}.md": note_text(code).encode(encoding)
        for code, encoding in zip(CLEAR_CODES[:3], ("utf-8-sig", "utf-16", "utf-32"))
    }
    notes["Designs/truncated_utf8.md"] = note_text("truncated_utf8").encode() + b"\xe2\x82"
    notes[LEGACY] = note_text("ambiguous_not_utf8").encode() + b"legacy \x96 text\r\n"
    notes[GOOD] = note_text("good_utf8").encode()
    for relative in notes:
        if (vault / relative).exists():
            raise AcceptanceFailure(f"fixture already exists: {relative}")
    config = f"vault:\n  brain_name: {json.dumps(BRAIN_NAME)}\n  access:\n    request_policy: denied\n"
    notes[CONFIG] = codecs.BOM_UTF8 + config.encode()
    for relative, raw in notes.items():
        path = vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return notes


def require_findings(result: dict, *, repaired: bool) -> None:
    findings = {(item["file"], item["code"]): item for item in result["findings"]
                if item.get("check") in {"text_encoding", "unreadable_file"}}
    expected = {(LEGACY, "not_utf8")}
    if not repaired:
        expected.update((f"Designs/{code}.md", code) for code in CLEAR_CODES)
    if set(findings) != expected:
        raise AcceptanceFailure(f"unexpected text findings: {sorted(findings)}; expected {sorted(expected)}")
    for (path, code), finding in findings.items():
        if path == LEGACY:
            if finding.get("repair") is not None:
                raise AcceptanceFailure("ambiguous legacy text offered a repair")
        elif finding["severity"] != ("warning" if code == "utf8_bom" else "error"):
            raise AcceptanceFailure(f"incorrect severity: {path}")


def require_config(result: dict) -> None:
    if result.get("brain_name") != BRAIN_NAME or result.get("access", {}).get("request_policy") != "denied":
        raise AcceptanceFailure("BOM configuration lost the custom name or failed open")


def run_acceptance(vault: Path, *, brain: str = "brain") -> dict:
    vault = vault.resolve()
    original = seed_fixture(vault)
    receipts = []

    def command(domain, verb, request=None, *, dry_run=False):
        argv = [brain, "--vault", str(vault), domain, verb,
                "--request-json", json.dumps(request or {}), "--json"]
        if dry_run:
            argv.append("--dry-run")
        envelope, receipt = _run_json(argv, cwd=vault, accepted={0, 1, 2}, timeout=300,
                                      env={**os.environ, "BRAIN_VAULT_ROOT": str(vault)})
        receipts.append(receipt)
        return envelope, _require_ok_envelope(envelope, f"{domain}.{verb}")

    _, config = command("vault", "read-config")
    require_config(config)
    _, before = command("vault", "check")
    require_findings(before, repaired=False)
    source_before_preview = _portable_manifest(vault)
    preview, planned = command("vault", "repair-text", dry_run=True)
    expected_paths = {f"Designs/{code}.md" for code in CLEAR_CODES}
    if preview.get("committed_effects") != [] or planned.get("dry_run") is not True:
        raise AcceptanceFailure("dry run reported effects or was not a preview")
    if {item["path"] for item in planned["files"]} != expected_paths:
        raise AcceptanceFailure("preview omitted clear damage or included legacy text")
    cut = next(item for item in planned["files"] if item["code"] == "truncated_utf8")
    if cut["dropped_hex"] != "e2 82" or not cut["dropped_windows1252"]:
        raise AcceptanceFailure("truncation preview omitted dropped byte evidence")
    if _portable_manifest(vault) != source_before_preview:
        raise AcceptanceFailure("dry run changed portable source state")
    if any((vault / path).read_bytes() != raw for path, raw in original.items()):
        raise AcceptanceFailure("dry run changed fixture source bytes")
    applied, changed = command("vault", "repair-text")
    if {item["path"] for item in changed["files"] if item["status"] == "changed"} != expected_paths:
        raise AcceptanceFailure("repair did not change exactly the clear set")
    if {item["subject"] for item in applied.get("committed_effects", [])} != expected_paths:
        raise AcceptanceFailure("repair effects do not name exactly the clear set")
    for code in CLEAR_CODES:
        if (vault / f"Designs/{code}.md").read_bytes() != note_text(code).encode():
            raise AcceptanceFailure(f"repair changed valid content or line endings: {code}")
    for path in (LEGACY, GOOD, CONFIG):
        if (vault / path).read_bytes() != original[path]:
            raise AcceptanceFailure(f"repair changed excluded source: {path}")
    _, after = command("vault", "check")
    require_findings(after, repaired=True)
    if any(item["check"] == "lexical_index" for item in after["findings"]):
        raise AcceptanceFailure("repair left the lexical index stale")
    _, config = command("vault", "read-config")
    require_config(config)
    _, search = command("artefact", "search", {"query": MARKER, "mode": "lexical", "top_k": 20})
    paths = {item["path"] for item in search["items"]}
    if paths != expected_paths | {GOOD}:
        raise AcceptanceFailure(f"lexical results omit repaired/good notes or include legacy: {paths}")
    return {"schema": "brain-lab.text-encoding-acceptance/1", "config_preserved": True,
            "preview_source_unchanged": True, "clear_set_repaired": sorted(expected_paths),
            "legacy_unchanged": True, "lexical_fresh": True, "search_paths": sorted(paths),
            "commands": receipts}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument("--brain", default="brain", help="Installed CLI for a disposable local fixture")
    parser.add_argument("--seed-only", action="store_true")
    args = parser.parse_args()
    try:
        result = ({"seeded": sorted(seed_fixture(args.vault))} if args.seed_only
                  else run_acceptance(args.vault, brain=args.brain))
    except (AcceptanceFailure, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
