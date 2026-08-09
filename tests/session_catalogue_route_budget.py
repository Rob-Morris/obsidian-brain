"""Reproducible latency runner for the static session catalogue route."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time


REPO_ROOT = Path(__file__).resolve().parents[1]
for path in (
    REPO_ROOT / "src" / "brain-core",
    REPO_ROOT / "src" / "brain-core" / "scripts",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import session


COLD_PROCESS_COUNT = 30
WARMUP_COUNT = 20
WARM_SAMPLE_COUNT = 200


def nearest_rank_p95(samples: list[int]) -> int:
    if not samples:
        raise ValueError("latency samples cannot be empty")
    ordered = sorted(samples)
    return ordered[(95 * len(ordered) + 99) // 100 - 1]


def one_sample(vault_root: Path) -> dict[str, object]:
    started = time.perf_counter_ns()
    route = session._load_command_catalogue_route(vault_root, "test")
    elapsed = time.perf_counter_ns() - started
    return {
        "elapsed_ns": elapsed,
        "application_imported": any(
            name == "_application" or name.startswith("_application.")
            for name in sys.modules
        ),
        "route_bytes": len(
            json.dumps(route, separators=(",", ":")).encode("utf-8")
        ),
    }


def cold_samples(vault_root: Path) -> list[dict[str, object]]:
    samples = []
    for _index in range(COLD_PROCESS_COUNT):
        completed = subprocess.run(
            [sys.executable, __file__, "--sample", str(vault_root)],
            check=True,
            capture_output=True,
            text=True,
        )
        samples.append(json.loads(completed.stdout))
    return samples


def warm_samples(vault_root: Path) -> list[int]:
    for _index in range(WARMUP_COUNT):
        one_sample(vault_root)
    return [
        int(one_sample(vault_root)["elapsed_ns"])
        for _index in range(WARM_SAMPLE_COUNT)
    ]


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "--sample":
        raise SystemExit("usage: session_catalogue_route_budget.py --sample VAULT")
    print(json.dumps(one_sample(Path(argv[1])), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
