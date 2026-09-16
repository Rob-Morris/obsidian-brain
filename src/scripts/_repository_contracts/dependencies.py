"""Offline dependency authority, generation and consumer-routing contracts."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import tempfile
import tomllib

import dependencies as generation
from _repository_contracts.view import RepositoryView


def validate_dependencies(view: RepositoryView) -> list[str]:
    """Validate the snapshot's authored policy and exact offline exports."""
    errors = []
    paths = (*generation.INPUTS, *generation.EXPORTS, ".gitattributes", "pyproject.toml",
             "Makefile", ".github/workflows/linux-test.yml", ".github/workflows/windows-smoke.yml",
             ".github/workflows/dependency-certification.yml",
             "src/scripts/dependency_certification.py",
             "src/brain-core/scripts/_common/_venv.py", "src/brain-core/scripts/_semantic/provision.py")
    missing = [path for path in paths if not view.exists(path)]
    if missing:
        return [f"dependency contract: missing {path}" for path in missing]
    try:
        manifest = tomllib.loads(view.read_text(generation.INPUTS[0]))
        policy = tomllib.loads(view.read_text(generation.INPUTS[1]))
        project = manifest["project"]
        if (project["name"], project["version"], project["requires-python"]) != (
            "brain-dependency-contract", "0", ">=3.12"
        ) or manifest["tool"]["uv"].get("package") is not False:
            errors.append("dependency contract: expected fixed virtual project metadata and Python >=3.12")
        markers = manifest["tool"]["uv"]["required-environments"]
        for platform, machine in (("darwin", "arm64"), ("linux", "x86_64"), ("win32", "AMD64")):
            expected = (f"implementation_name == 'cpython' and python_version == '3.12' "
                        f"and sys_platform == '{platform}' and platform_machine == '{machine}'")
            if expected not in markers:
                errors.append(f"dependency contract: missing required environment {platform}/{machine}")
        if policy != {"index": [{"url": generation.INDEX, "default": True}]}:
            errors.append("dependency contract: uv.toml must select only the PyPI default index")
        if "project" in tomllib.loads(view.read_text("pyproject.toml")):
            errors.append("dependency contract: root pyproject must not duplicate package intent")
        attributes = view.read_text(".gitattributes").splitlines()
        for path in (*generation.INPUTS, *generation.EXPORTS):
            if f"{path} text eol=lf" not in attributes:
                errors.append(f"dependency contract: missing LF checkout policy for {path}")
        make = view.read_text("Makefile")
        for target, export in (("install", "requirements-dev.txt"), ("install-semantic", "requirements-dev-semantic.txt")):
            match = re.search(rf"^{target}:.*\n((?:\t.*\n)+)", make, re.M)
            commands = match.group(1) if match else ""
            if f"$(PIP) install --no-deps -r dependencies/{export}\n" not in commands:
                errors.append(f"dependency contract: {target} must consume its complete export with --no-deps")
        windows = view.read_text(".github/workflows/windows-smoke.yml")
        if "-m pip install --no-deps -r dependencies/requirements-dev.txt\n" not in windows:
            errors.append("dependency contract: Windows smoke must consume the dev export with --no-deps")
        if "make install" not in view.read_text(".github/workflows/linux-test.yml"):
            errors.append("dependency contract: Linux tests must use make install")
        runtime = view.read_text("src/brain-core/scripts/_common/_venv.py")
        semantic = view.read_text("src/brain-core/scripts/_semantic/provision.py")
        if '"--no-deps"' not in runtime or '"-r", str(selected)' not in runtime:
            errors.append("dependency contract: managed runtime must use the complete selected export with --no-deps")
        if "conform_runtime(" not in semantic or "semantic=True" not in semantic or "SEMANTIC_RUNTIME_PACKAGES" in semantic:
            errors.append("dependency contract: semantic provisioning must consume the shared export owner")
        certification = view.read_text(".github/workflows/dependency-certification.yml")
        for required in ("macos-15", "ubuntu-24.04", "windows-2025", 'python-version: "3.12"',
                         "fail-fast: false", "if: always()", "dependency_certification.py", "needs.native.result"):
            if required not in certification:
                errors.append(f"dependency certification: missing required workflow clause {required}")
        if "continue-on-error" in certification:
            errors.append("dependency certification must not ignore failed native jobs")
        certification_script = view.read_text("src/scripts/dependency_certification.py")
        for required in (
            '"--only-binary=:all:"',
            '"--no-deps"',
            '"-r"',
            "subprocess.run(install_command(python, export), check=True)",
        ):
            if required not in certification_script:
                errors.append(
                    "dependency certification: native install boundary missing "
                    + required
                )
        if errors:
            return errors
        # The staged checker runs from its own materialised index, including
        # this policy and generator. A memory view receives the same treatment.
        root = getattr(view, "root", None)
        if isinstance(root, Path) and type(view).__name__ == "WorkingTreeView":
            generation.run(root)
        else:
            with tempfile.TemporaryDirectory(prefix="brain-dependency-view-") as temporary:
                root = Path(temporary)
                for path in (*generation.INPUTS, *generation.EXPORTS):
                    destination = root / path
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_text(view.read_text(path), encoding="utf-8", newline="\n")
                generation.run(root)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        errors.append(f"dependency contract: {exc}")
    return errors
