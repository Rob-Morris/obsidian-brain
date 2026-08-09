"""Static session command-catalogue route contracts."""

from __future__ import annotations

import json
from pathlib import Path

from _application.registry import current_application_catalogue


REPO_ROOT = Path(__file__).resolve().parents[2]
ROUTE_PATH = REPO_ROOT / "src" / "brain-core" / "command-catalogue.json"
SESSION_PATH = REPO_ROOT / "src" / "brain-core" / "scripts" / "session.py"


def test_shipped_session_route_matches_the_authoritative_catalogue():
    route = json.loads(ROUTE_PATH.read_text(encoding="utf-8"))
    catalogue = current_application_catalogue()

    assert route == {
        "schema": catalogue.schema,
        "interface_epoch": catalogue.interface_epoch,
        "static_fingerprint": catalogue.fingerprint,
        "installed_application_command_count": len(catalogue.entries),
    }


def test_session_route_loader_does_not_import_application_or_command_owners():
    source = SESSION_PATH.read_text(encoding="utf-8")

    assert "from _application" not in source
    assert "import _application" not in source
    assert "from command_application" not in source
    assert "import command_application" not in source
