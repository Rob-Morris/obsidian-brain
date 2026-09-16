#!/usr/bin/env python3
"""Generate or check the four dependency exports from one explicitly configured lock.

``check`` never resolves online or modifies committed artefacts. ``update``
retains locked selections unless ``--upgrade`` or ``--upgrade-package`` is used.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[2]
EXPORTS = {
    "src/brain-core/brain_mcp/requirements.txt": (),
    "src/brain-core/brain_mcp/requirements-semantic.txt": ("--extra", "semantic"),
    "dependencies/requirements-dev.txt": ("--group", "dev"),
    "dependencies/requirements-dev-semantic.txt": ("--group", "dev", "--extra", "semantic"),
}
INPUTS = ("dependencies/pyproject.toml", "dependencies/uv.toml", "dependencies/uv.lock")
INDEX = "https://pypi.org/simple"


def generator_version(root: Path) -> str:
    """Read the sole generator pin from authored dependency intent."""
    manifest = tomllib.loads((root / INPUTS[0]).read_text())
    pins = [re.fullmatch(r"uv==([0-9]+\.[0-9]+\.[0-9]+)", dep)
            for dep in manifest["dependency-groups"]["dev"] if dep.startswith("uv")]
    if len(pins) != 1 or pins[0] is None:
        raise ValueError("dependencies/pyproject.toml must declare one exact uv==X.Y.Z dev pin")
    return pins[0].group(1)


def validate_sources(root: Path) -> None:
    """Reject alternate registries and non-registry packages in the lock."""
    lock = tomllib.loads((root / INPUTS[2]).read_text())
    for package in lock["package"]:
        source = package["source"]
        if source == {"virtual": "."} and package["name"] == "brain-dependency-contract":
            continue
        if source != {"registry": INDEX}:
            raise ValueError(f"uv.lock: unexpected source for {package['name']}: {source}")


def run(root: Path = ROOT, *, update: bool = False, upgrade: bool = False,
        upgrade_packages: tuple[str, ...] = ()) -> None:
    """Run hermetic uv commands and compare or replace generated exports."""
    version = generator_version(root)
    executable = Path(sys.executable).parent / ("uv.exe" if os.name == "nt" else "uv")
    uv = str(executable) if executable.is_file() else shutil.which("uv")
    if not uv:
        raise ValueError("uv is missing; run make install to install the committed dev export")
    # Explicit config replaces user/system config; no ambient UV_* option may
    # override it. Scrub even unknown future variables, including credentials.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("UV_")}
    result = subprocess.run([uv, "--version"], env=env, check=True, capture_output=True, text=True)
    if result.stdout.split()[:2] != ["uv", version]:
        raise ValueError(f"expected uv {version}; run make install (found {result.stdout.strip()!r})")
    with tempfile.TemporaryDirectory(prefix="brain-dependencies-") as temporary:
        env["UV_CACHE_DIR"] = str(Path(temporary) / "cache")
        common = ["--project", str(root / "dependencies"), "--config-file", str(root / INPUTS[1]),
                  "--no-python-downloads", "--no-build", "--python", sys.executable]

        def invoke(arguments: list[str]) -> None:
            completed = subprocess.run([uv, *arguments, *common], cwd=root, env=env,
                                       capture_output=True, text=True)
            if completed.returncode:
                raise ValueError("dependency lock/export check failed; run make install for tooling, "
                                 "or review an intentional dependencies.py update.\n" + completed.stderr)

        flags = [] if update else ["--check", "--offline"]
        if upgrade:
            flags.append("--upgrade")
        for package in upgrade_packages:
            flags.extend(("--upgrade-package", package))
        invoke(["lock", *flags])
        validate_sources(root)
        for relative, selection in EXPORTS.items():
            output = Path(temporary) / Path(relative).name
            # The two base basenames are distinct across all four exports.
            invoke(["export", "--locked", "--offline", "--format", "requirements.txt",
                    "--no-hashes", "--no-annotate", "--no-header", "--no-emit-project",
                    "--no-default-groups", *selection, "--output-file", str(output)])
            generated = output.read_bytes()
            target = root / relative
            if update:
                target.write_bytes(generated)
            elif not target.is_file() or target.read_bytes() != generated:
                raise ValueError(f"{relative}: stale generated export; review dependencies.py update")
        for relative in (*INPUTS, *EXPORTS):
            if b"\r" in (root / relative).read_bytes():
                raise ValueError(f"{relative}: dependency contract requires LF bytes")


def main() -> int:
    """Expose separate offline checking and intentional online update modes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("check", "update"))
    parser.add_argument("--upgrade", action="store_true")
    parser.add_argument("--upgrade-package", action="append", default=[])
    args = parser.parse_args()
    if args.mode == "check" and (args.upgrade or args.upgrade_package):
        parser.error("upgrades require update mode")
    try:
        run(update=args.mode == "update", upgrade=args.upgrade,
            upgrade_packages=tuple(args.upgrade_package))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("dependency contract: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
