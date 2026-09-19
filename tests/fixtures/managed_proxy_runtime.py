"""Small real managed interpreters for proxy lifecycle subprocess tests."""

import os
from pathlib import Path
import site
import sys
import venv


def prepare_runtime(vault: Path) -> Path:
    """Use an isolated home and reuse only the test runner's installed packages."""
    test_home = vault.parent / "proxy-home"
    os.environ["HOME"] = str(test_home)
    os.environ["USERPROFILE"] = str(test_home)
    sys.path.insert(0, str(vault / ".brain-core" / "scripts"))
    from _common import resolve_vault_venv_python
    python = resolve_vault_venv_python(vault)
    if not python.exists():
        root = python.parent.parent
        venv.EnvBuilder(with_pip=False, symlinks=os.name == "posix").create(root)
        packages = root / ("Lib/site-packages" if os.name == "nt" else
                           f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
        # Dependencies such as pywin32 need their own .pth paths/bootstrap code,
        # which a plain parent-directory path entry does not process.
        (packages / "brain-test-packages.pth").write_text(
            "".join(f"import site; site.addsitedir({path!r})\n" for path in site.getsitepackages()),
            encoding="utf-8",
        )
    return python


if __name__ == "__main__":
    print(prepare_runtime(Path(sys.argv[1])))
