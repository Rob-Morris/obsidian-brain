#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _source_files(root: Path) -> list[Path]:
    candidates = [root / "etc/apt/sources.list", root / "etc/apt/preferences"]
    for directory, patterns in (
        (root / "etc/apt/sources.list.d", ("*.list", "*.sources")),
        (root / "etc/apt/preferences.d", ("*",)),
    ):
        if directory.is_dir():
            for pattern in patterns:
                candidates.extend(directory.glob(pattern))
    return sorted({path for path in candidates if path.is_file()})


def _packages(status_path: Path) -> list[dict[str, str]]:
    if not status_path.is_file():
        return []
    packages = []
    for paragraph in status_path.read_text(encoding="utf-8").split("\n\n"):
        fields = {}
        for line in paragraph.splitlines():
            key, separator, value = line.partition(": ")
            if separator and key in {"Package", "Version", "Architecture", "Status"}:
                fields[key] = value
        if fields.get("Status") == "install ok installed" and all(
            key in fields for key in ("Package", "Version", "Architecture")
        ):
            packages.append(
                {
                    "name": fields["Package"],
                    "version": fields["Version"],
                    "architecture": fields["Architecture"],
                }
            )
    return sorted(packages, key=lambda item: (item["name"], item["architecture"]))


def package_inputs(root: Path) -> dict:
    root = root.resolve()
    repositories = []
    for path in _source_files(root):
        content = path.read_bytes()
        repositories.append(
            {
                "path": "/" + path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "content": content.decode("utf-8", errors="replace"),
            }
        )
    packages = _packages(root / "var/lib/dpkg/status")
    return {
        "schema": "brain-lab.package-inputs/1",
        "repositories": repositories,
        "packages": packages,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    encoded = json.dumps(package_inputs(args.root), indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(encoded, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
