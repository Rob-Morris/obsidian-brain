"""Repository snapshot read contract shared by policy modules."""

from __future__ import annotations

import subprocess
from typing import Protocol


class RepositoryView(Protocol):
    """Read-only view over either the checkout or a staged snapshot."""

    def read_text(self, path: str) -> str: ...

    def files(self, prefix: str) -> set[str]: ...

    def exists(self, path: str) -> bool: ...


def read(view: RepositoryView, path: str, errors: list[str]) -> str | None:
    try:
        return view.read_text(path)
    except (OSError, UnicodeError, subprocess.CalledProcessError) as exc:
        errors.append(f"{path}: cannot read: {exc}")
        return None
