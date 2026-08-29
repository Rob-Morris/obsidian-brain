from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .application import Application, HandlerResult, OperationContext, OperationFailure
from .host_state import capture_host_state
from .model import (
    EffectCertainty,
    EvidenceCompleteness,
    ScenarioAssertion,
    aggregate_effect_certainty,
    require_keys,
)


def _primitive_result_references(payload: dict[str, Any]) -> list[str]:
    return [
        step["operation_id"]
        for step in payload.get("steps", [])
        if isinstance(step, dict) and isinstance(step.get("operation_id"), str)
    ]


def _host_state_request(value: Any) -> dict[str, list[str]] | None:
    if value is False:
        return None
    if value is True:
        value = {}
    if not isinstance(value, dict) or set(value) - {"worktrees", "vaults"}:
        raise ValueError("host_state must be false, true, or an object containing worktrees/vaults")
    worktrees = value.get("worktrees", [])
    vaults = value.get("vaults", [])
    if not isinstance(worktrees, list) or not isinstance(vaults, list):
        raise ValueError("host_state worktrees and vaults must be arrays")
    if not all(isinstance(path, str) for path in [*worktrees, *vaults]):
        raise ValueError("host_state paths must be strings")
    return {"worktrees": worktrees, "vaults": vaults}


def _capture_declared_host_state(request: dict[str, list[str]]) -> dict[str, Any]:
    return capture_host_state(
        worktrees=[Path(path) for path in request["worktrees"]],
        vaults=[Path(path) for path in request["vaults"]],
    )


def _resolve_reference(value: Any, results: list[dict[str, Any]]) -> Any:
    if isinstance(value, dict):
        return {key: _resolve_reference(item, results) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_reference(item, results) for item in value]
    if not isinstance(value, str) or not value.startswith("${steps.") or not value.endswith("}"):
        return value
    parts = value[2:-1].split(".")
    if len(parts) < 3 or parts[0] != "steps" or not parts[1].isdigit():
        raise ValueError(f"invalid scenario result reference: {value}")
    index = int(parts[1])
    if index >= len(results):
        raise ValueError(f"scenario result reference must target an earlier step: {value}")
    resolved: Any = results[index]
    for part in parts[2:]:
        if not isinstance(resolved, dict) or part not in resolved:
            raise ValueError(f"scenario result reference does not exist: {value}")
        resolved = resolved[part]
    return resolved


def _evidence_completeness(results: list[dict[str, Any]]) -> EvidenceCompleteness:
    return (
        EvidenceCompleteness.PARTIAL
        if any(item["evidence_completeness"] == "partial" for item in results)
        else EvidenceCompleteness.COMPLETE
    )


def run_scenario(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(
        request,
        required=frozenset({"steps"}),
        optional=frozenset({"assertions", "continue_after_failure", "host_state"}),
    )
    steps = request["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= 50:
        raise ValueError("scenario steps must be an array containing 1 to 50 operations")
    host_request = _host_state_request(request.get("host_state", {}))
    host_before = (
        _capture_declared_host_state(host_request) if host_request is not None else None
    )
    results: list[dict[str, Any]] = []

    for index, step in enumerate(steps):
        if not isinstance(step, dict) or set(step) != {"operation", "request"}:
            raise ValueError(f"scenario step {index} must contain operation and request")
        operation = step["operation"]
        if operation == "scenario.run":
            raise ValueError("scenarios cannot recursively invoke scenario.run")
        result = context.application.dispatch(
            operation, _resolve_reference(step["request"], results)
        )
        results.append(result.to_dict())
        if not result.ok and request.get("continue_after_failure") is not True:
            break
        if result.effect_certainty.value in {"partial", "unknown"} and request.get("continue_after_failure") is not True:
            break
    assertions = request.get("assertions", [])
    if not isinstance(assertions, list):
        raise ValueError("scenario assertions must be an array")
    assertion_results = []
    if len(results) == len(steps):
        for index, value in enumerate(assertions):
            if not isinstance(value, dict) or set(value) != {"left", "operator", "right"}:
                raise ValueError(
                    f"scenario assertion {index} must contain left, operator, and right"
                )
            assertion = ScenarioAssertion(value["left"], value["operator"], value["right"])
            left = _resolve_reference(assertion.left, results)
            right = _resolve_reference(assertion.right, results)
            assertion_results.append(
                {
                    "index": index,
                    "operator": assertion.operator,
                    "left": left,
                    "right": right,
                    "passed": assertion.evaluate(left, right),
                }
            )
    host_after = None
    if host_before is not None and host_request is not None:
        host_after = _capture_declared_host_state(host_request)
        (context.evidence_directory / "host-state.json").write_text(
            json.dumps({"before": host_before, "after": host_after}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    completed = (
        len(results) == len(steps)
        and all(item["outcome"] == "success" for item in results)
        and all(item["passed"] for item in assertion_results)
    )
    host_state_equal = host_before is None or host_before["fingerprint"] == host_after["fingerprint"]
    if not completed or not host_state_equal:
        effect = aggregate_effect_certainty(item["effect_certainty"] for item in results)
        if not host_state_equal:
            message = "scenario changed declared host Brain, client, worktree, or vault state"
        elif len(results) == len(steps) and assertion_results:
            failed_assertions = [str(item["index"]) for item in assertion_results if not item["passed"]]
            message = f"scenario assertions failed: {', '.join(failed_assertions)}"
        else:
            message = f"scenario stopped after {len(results)} of {len(steps)} steps"
        raise OperationFailure(
            message,
            effect_certainty=effect,
            evidence_completeness=_evidence_completeness(results),
            payload={
                "steps": results,
                "assertions": assertion_results,
                "completed": completed,
                "host_state_equal": host_state_equal,
            },
        )
    return HandlerResult(
        payload={
            "steps": results,
            "assertions": assertion_results,
            "completed": True,
            "host_state_equal": host_state_equal,
        },
        effect_certainty=aggregate_effect_certainty(
            item["effect_certainty"] for item in results
        ),
        evidence_completeness=_evidence_completeness(results),
    )


def register_scenario_handlers(application: Application) -> None:
    application.register(
        "scenario.run",
        run_scenario,
        mutating=True,
        evidence_references=_primitive_result_references,
    )
