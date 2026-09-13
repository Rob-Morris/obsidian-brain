"""Run the real proxy and public server with an explicitly supplied test vault.

Machine registration is covered by launcher tests. This fixture keeps every
command, invocation, authority and recovery boundary of the production relay.
"""

import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "brain-core"))
from brain_mcp import proxy


def main():
    vault = os.environ["BRAIN_CAPTURE_VAULT"]
    os.environ["BRAIN_VAULT_ROOT"] = vault
    os.environ["PYTHONPATH"] = str(Path(vault) / ".brain-core")
    proxy._logger = proxy._setup_logging(vault)
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", vault)
    relay._ensure_background_threads()
    if not relay._start_child():
        raise RuntimeError("test proxy could not negotiate its child interface")
    relay.run()


if __name__ == "__main__":
    main()
