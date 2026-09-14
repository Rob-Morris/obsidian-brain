"""Run the real proxy and public server with an explicitly supplied test vault.

Machine registration is covered by launcher tests. This fixture keeps every
command, invocation, authority and recovery boundary of the production relay.
"""

import os
from pathlib import Path
import sys


def main():
    vault = os.environ["BRAIN_CAPTURE_VAULT"]
    sys.path.insert(0, str(Path(vault) / ".brain-core"))
    from brain_mcp import proxy
    os.environ["BRAIN_VAULT_ROOT"] = vault
    os.environ["PYTHONPATH"] = str(Path(vault) / ".brain-core")
    proxy._logger = proxy._setup_logging(vault)
    if os.environ.get("BRAIN_CAPTURE_EXEC_FAILURE") == "1":
        def fail_exec(*args):
            raise OSError("injected exec failure")
        proxy.os.execve = fail_exec
    proxy._serve_proxy(sys.executable, "brain_mcp.server", vault)


if __name__ == "__main__":
    main()
