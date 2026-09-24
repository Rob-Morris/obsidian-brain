"""Contributor-only promotion workflow; Git refs are its durable state."""

from pathlib import Path
import sys

CLI_ROOT = Path(__file__).resolve().parents[3] / "cli"
if str(CLI_ROOT) not in sys.path:
    sys.path.insert(0, str(CLI_ROOT))
