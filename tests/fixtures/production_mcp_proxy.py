"""Run the real proxy and public server with an explicitly supplied test vault.

Machine registration is covered by launcher tests. This fixture keeps every
command, invocation, authority and recovery boundary of the production relay.
"""

import os
from pathlib import Path
import sys


def main():
    vault = os.environ["BRAIN_CAPTURE_VAULT"]
    from managed_proxy_runtime import prepare_runtime
    python = str(prepare_runtime(Path(vault)))
    if os.path.abspath(sys.executable) != os.path.abspath(python):
        os.execve(python, [python, __file__], os.environ)
    sys.path.insert(0, str(Path(vault) / ".brain-core"))
    from brain_mcp import proxy
    os.environ["BRAIN_VAULT_ROOT"] = vault
    os.environ["PYTHONPATH"] = str(Path(vault) / ".brain-core")
    proxy._logger = proxy._setup_logging(vault)
    if os.environ.get("BRAIN_CAPTURE_PROTOCOL_FOUR") == "1":
        # Deployed v4 uses this same capture/refresh/admission path. Freeze its
        # process marker rather than copying the historical 2,800-line relay;
        # the fixture keeps dependencies unchanged across the Core refresh.
        proxy.PROXY_PROTOCOL = 4
    if os.environ.get("BRAIN_CAPTURE_EXEC_FAILURE") == "1":
        def fail_exec(*args):
            raise OSError("injected exec failure")
        proxy.os.execve = fail_exec
    if os.environ.get("BRAIN_CAPTURE_POST_RETIREMENT_RUNTIME_FAILURE") == "1":
        original = proxy.Proxy._required_runtime_python
        def fail_after_retirement(self):
            if self._shutdown:
                (Path(vault) / ".brain-core/brain_mcp/requirements.txt").write_bytes(b"invalid\rcontract")
            return original(self)
        proxy.Proxy._required_runtime_python = fail_after_retirement
    if os.environ.get("BRAIN_CAPTURE_BLOCKED") == "1":
        original_probe = proxy._probe_local_state
        def probe(root):
            if not (Path(vault) / "repaired").exists():
                raise PermissionError("test local state unavailable until repaired")
            original_probe(root)
        proxy._probe_local_state = probe
        sys.argv = [__file__, sys.executable, "brain_mcp.server"]
        proxy.main()
    else:
        proxy._serve_proxy(sys.executable, "brain_mcp.server", vault)


if __name__ == "__main__":
    main()
