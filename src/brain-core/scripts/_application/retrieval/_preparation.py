"""Benchmark inputs are validated before provider entry and reused by execution."""

from dataclasses import dataclass

from .._benchmark_support import resolve_brain_path, optional_brain_path
from .._managed_preparation import CACHE_OUTPUTS
from ..preparation import ObservedResource, OperationPreparation, bind_operation, canonical_json, content_digest


@dataclass(frozen=True, slots=True)
class BenchmarkInputs:
    paths: dict
    benchmark: dict | None = None
    semantic_seeds: list | None = None
    hybrid_seeds: list | None = None


def construction_paths(root, request):
    from _common import check_write_allowed
    fixture, fixture_rel = resolve_brain_path(root, request.fixture_path, "fixture_path")
    audit, audit_rel = optional_brain_path(root, request.audit_path, "audit_path")
    if audit is None:
        name = fixture.name[:-5] if fixture.name.endswith(".json") else fixture.name
        audit = fixture.with_name(name + ".audit.json")
        audit_rel = audit.relative_to(root.resolve()).as_posix()
    if fixture == audit:
        raise ValueError("fixture_path and audit_path must identify different files")
    check_write_allowed(fixture_rel)
    check_write_allowed(audit_rel)
    paths = {"fixture": (fixture, fixture_rel), "audit": (audit, audit_rel)}
    for kind in ("semantic", "hybrid"):
        path, relative = optional_brain_path(root, getattr(request, kind + "_seed_path"), kind + "_seed_path")
        if path is not None:
            if not path.is_file():
                raise FileNotFoundError(f"{kind}_seed_path does not exist: {relative}")
            paths[kind] = (path, relative)
    return paths


def benchmark_inputs(context, request):
    root = context.selected_brain.vault_root
    if request.COMMAND_ID == "retrieval.evaluate":
        import evaluate_search
        path, relative = resolve_brain_path(root, request.benchmark_path, "benchmark_path")
        return BenchmarkInputs({"benchmark": (path, relative)}, benchmark=evaluate_search.load_benchmark(path))
    import construct_benchmark_fixture as constructor
    paths = construction_paths(root, request)
    return BenchmarkInputs(paths,
        semantic_seeds=constructor._load_semantic_seed_candidates(paths["semantic"][0]) if "semantic" in paths else None,
        hybrid_seeds=constructor._load_hybrid_seed_candidates(paths["hybrid"][0]) if "hybrid" in paths else None)


def benchmark_binding(context, request, *, plan, frozen_inputs=None):
    from _portable.maintenance_inputs import file_identity, source_manifest

    root = context.selected_brain.vault_root
    sources = source_manifest(root)
    observations = [ObservedResource("benchmark-sources", "selected-brain", sources["sha256"])]
    observations.extend(ObservedResource("retrieval-state", path, file_identity(root / path)) for path in CACHE_OUTPUTS)
    observations.extend(ObservedResource("benchmark-" + kind, relative, file_identity(path))
                        for kind, (path, relative) in plan.paths.items())
    observations.append(ObservedResource("parsed-inputs", "benchmark-and-seeds", content_digest(canonical_json(
        {"benchmark": plan.benchmark, "semantic": plan.semantic_seeds, "hybrid": plan.hybrid_seeds}))))
    return bind_operation(request, observations=observations, frozen_inputs=frozen_inputs,
                          review={"operation": request.COMMAND_ID, "scope": "Selected Brain retrieval corpus",
                                  "sources": sources, "paths": {key: value[1] for key, value in plan.paths.items()},
                                  "replace_outputs": [key for key in ("fixture", "audit") if key in plan.paths and plan.paths[key][0].exists()]})


def prepare_benchmark(context, request, *, frozen_inputs=None):
    from _common import vault_mutation_lock
    with vault_mutation_lock(context.selected_brain.vault_root):
        return benchmark_binding(context, request, plan=benchmark_inputs(context, request), frozen_inputs=frozen_inputs)


BENCHMARK = OperationPreparation(prepare_benchmark)
