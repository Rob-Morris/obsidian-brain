"""Check candidate compatibility using the candidate's own validation owners."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile

from .model import PromotionError

PREFLIGHT_TIMEOUT_SECONDS = 30
DIAGNOSTIC_BYTES = 4096

def check_lab_compatibility(worktree: Path) -> None:
    """Require coherent candidate facts and one adapter with a matching CLI baseline."""
    source = """
import sys
from pathlib import Path
root = Path.cwd()
sys.path[:0] = [str(root / 'src/scripts'), str(root / 'tools/brain-lab')]
import release
from brain_lab.compatibility import CompatibilityManifest, render_expected
facts = release.release_facts(root, 'worktree')
if not facts.coherent or not facts.core:
    raise ValueError('candidate release facts are incoherent')
adapter = CompatibilityManifest(root / 'tools/brain-lab/compatibility.json').select(facts.core)
values = {'version': facts.core, 'cli_version': facts.cli_unix}
for gate in adapter.health:
    if gate.gate_id != 'cli-version' or gate.expected_json is None:
        continue
    key = 'result.full_support_core_version'
    if key not in gate.expected_json:
        continue
    baseline = render_expected(gate.expected_json[key], values)
    if baseline != facts.full_support_core_version:
        raise ValueError(
            f'candidate Brain Lab adapter {adapter.adapter_id} declares CLI full-support '
            f'Core baseline {baseline!r}; '
            f'candidate declares {facts.full_support_core_version!r}'
        )
"""
    with tempfile.TemporaryFile() as diagnostics:
        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-c", source], cwd=worktree,
                stdout=subprocess.DEVNULL, stderr=diagnostics, check=False,
                timeout=PREFLIGHT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise PromotionError(
                "candidate Brain Lab compatibility preflight timed out; "
                "repository contracts and the serial suite were not started"
            ) from exc
        except OSError as exc:
            raise PromotionError("candidate Brain Lab compatibility preflight could not start") from exc
        if completed.returncode:
            end = diagnostics.seek(0, 2)
            diagnostics.seek(max(0, end - DIAGNOSTIC_BYTES))
            detail = diagnostics.read(DIAGNOSTIC_BYTES).decode("utf-8", errors="replace").strip()
            raise PromotionError("candidate Brain Lab compatibility preflight failed: " + detail)
