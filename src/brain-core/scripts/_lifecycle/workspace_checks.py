"""Workspace diagnostics reuse the complete ownership scan without mutating it.

Every error here is a judgement finding (DD-082); ``JUDGEMENT_FINDINGS`` in
``_repair_common`` says what identifies each code. A per-file error whose
condition can change while the file stays the same declares the field values
or the rule that failed as evidence, so a changed condition reopens a
dismissal; prose never counts (DD-086).
"""

from _common._workspace import BindingState, InvalidBindingCause, OwnershipRuleError

from .ownership_graph import read_ownership_graph


def binding_code(state: BindingState) -> str:
    """The finding code for a binding state that is a finding; the two settled states are none."""
    return f"workspace_binding_{state.value}"


_REPORTED_BINDING_STATES = frozenset(BindingState) - {BindingState.VALID, BindingState.UNCONFIGURED}

# The codes this module emits, by severity; ``report`` refuses any other, so a new code is declared here first.
ERROR_CODES = frozenset({
    "workspace_scan_unreadable",
    "workspace_reference_malformed",
    "workspace_reference_missing",
    "workspace_reference_archived",
    "workspace_reference_wrong_type",
    "workspace_hub_invalid",
    "workspace_ownership_invalid",
    "workspace_policy_invalid",
    *(binding_code(state) for state in _REPORTED_BINDING_STATES),
})
INFO_CODES = frozenset({"workspace_adoption_candidate", "workspace_registry_missing"})

# Ownership causes beyond the two ``OwnershipRuleError`` rules, in the order the rules run.
OWNERSHIP_CAUSES = ("reference", "parent", "archived_parent", "membership", "parent_missing", "workspace_mismatch", "lineage")


def _ownership_failure(graph, record, complete_index):
    """The first ownership rule ``record`` breaks, as ``(cause, message)``, or None."""
    from _common._workspace import validate_ownership

    try:
        if record.reference:
            graph.resolve(record.reference)
    except ValueError as exc:
        return "reference", str(exc)
    try:
        if record.parent:
            owner = graph.resolve(record.parent)
            if owner.archived and not record.archived:
                return "archived_parent", f"Active artefact has archived parent {record.parent}"
    except ValueError as exc:
        return "parent", str(exc)
    try:
        validate_ownership(record.reference or record.path, record.fields, complete_index)
    except OwnershipRuleError as exc:
        return exc.cause, str(exc)
    except ValueError as exc:
        return "membership", str(exc)
    try:
        graph.validate_lineage(record)
    except ValueError as exc:
        return "lineage", str(exc)
    return None


def workspace_findings(vault_root, router, *, workspace_dir=None, text_reader=None):
    from _common._workspace import membership, require_workspace, workspace_policy
    from _bootstrap.workspace_binding import load_workspace_manifest_state, resolve_selected_workspace_binding, WorkspaceBindingError

    findings = []

    def report(code, path, message, fix, *, severity="error", evidence=None):
        if code not in (ERROR_CODES if severity == "error" else INFO_CODES):
            raise ValueError(f"workspace_contract emits undeclared {severity} code {code!r}")
        finding = {"check": "workspace_contract", "code": code, "severity": severity,
                   "file": path, "message": message, "fix": fix}
        if evidence is not None:
            # Declared evidence fingerprints a judgement finding; prose never does.
            finding["evidence"] = evidence
        findings.append(finding)

    try:
        graph = read_ownership_graph(vault_root, text_reader=text_reader)
    except ValueError as exc:
        report("workspace_scan_unreadable", None, str(exc), "Repair the blocking source named in this message; consult any matching per-file unreadable_file finding, or check symlinks and bounds, then retry vault.check")
        return findings
    complete_index = {reference: {**items[0].fields, "path": items[0].path}
                      for reference, items in graph.identities.items() if len(items) == 1}
    for record in graph.records:
        reference = record.reference or record.path
        fields = record.fields
        workspace = None
        try:
            workspace = membership(reference, fields)
        except ValueError as exc:
            report("workspace_reference_malformed", record.path, str(exc), "Use artefact.set-workspace with an explicit destination; workspace hubs must remain self-scoped",
                   evidence={"workspace": fields.get("workspace")})
        archived_self = record.archived and fields.get("type") == "living/workspace" and workspace == record.reference
        if workspace and not archived_self:
            entry = router.get("artefact_index", {}).get(workspace)
            if entry is None:
                candidates = graph.identities.get(workspace, ())
                archived = any(item.archived for item in candidates)
                code = "workspace_reference_archived" if archived else "workspace_reference_missing"
                report(code, record.path, f"{workspace} has no current living workspace hub" + (" (archived identity)" if archived else ""),
                       "Restore the original hub or explicitly reassign affected artefacts; tags are not membership",
                       evidence={"workspace": workspace})
            elif entry.get("type") != "living/workspace":
                report("workspace_reference_wrong_type", record.path, f"{workspace} resolves to {entry.get('type')}, not living/workspace", "Restore the workspace hub's type/identity before mutating its members",
                       evidence={"workspace": workspace, "type": entry.get("type")})
            else:
                try:
                    require_workspace(router, workspace)
                except ValueError as exc:
                    report("workspace_hub_invalid", record.path, str(exc), "Repair the hub's self-scope and ownership explicitly",
                           evidence={"workspace": workspace})
        failure = _ownership_failure(graph, record, complete_index)
        if failure is not None:
            cause, message = failure
            evidence = {"cause": cause, "parent": fields.get("parent")}
            if cause in {"membership", "workspace_mismatch"}:
                evidence["workspace"] = fields.get("workspace")
            report("workspace_ownership_invalid", record.path, message, "Use artefact.set-workspace recursively with an explicit replacement/cleared root parent, or repair the living owner identity",
                   evidence=evidence)
        if fields.get("type") == "living/workspace":
            try:
                workspace_policy(router, workspace, fields)
            except ValueError as exc:
                report("workspace_policy_invalid", record.path, str(exc), "Use workspace.update-policy to replace/clear the shared parent or tags",
                       evidence={"default_parent": fields.get("default_parent"), "default_tags": fields.get("default_tags")})
        if fields.get("workspace") is None and fields.get("type") != "living/workspace":
            tags = fields.get("tags", ())
            if isinstance(tags, list) and any(isinstance(tag, str) and tag.startswith("workspace/") for tag in tags):
                report("workspace_adoption_candidate", record.path, "Legacy workspace relationship tags do not establish membership", "Review ownership and explicitly adopt with artefact.set-workspace; never infer membership from tags", severity="info")
    if workspace_dir is not None:
        manifest_path = str(workspace_dir)
        try:
            state = load_workspace_manifest_state(workspace_dir)
            manifest_path = str(state.manifest_path)
            binding = resolve_selected_workspace_binding(vault_root, router, state.data)
            if binding.state is BindingState.VALID:
                _report_missing_row(vault_root, workspace_dir, binding.reference.split("/", 1)[1], manifest_path, report)
            elif binding.state in _REPORTED_BINDING_STATES:
                evidence = {"workspace": binding.reference}
                if binding.cause is not None:
                    evidence["cause"] = binding.cause.value
                report(binding_code(binding.state), manifest_path, binding.guidance or binding.state.value,
                       "Run brain workspace setup for the intended selected Brain; repair local defaults or reactivate the hub as reported",
                       evidence=evidence)
        except (WorkspaceBindingError, OSError, ValueError) as exc:
            # The manifest could not be read: the error's code, not its wording, is the evidence.
            error = exc.code if isinstance(exc, WorkspaceBindingError) else type(exc).__name__
            report(binding_code(BindingState.CONFIGURED_INVALID), manifest_path, str(exc),
                   "Repair the local manifest with brain workspace setup / workspace.update-metadata",
                   evidence={"workspace": None, "cause": "manifest", "error": error})
    return findings


def _report_missing_row(vault_root, workspace_dir, key, manifest_path, report):
    """From the workspace end, the Brain's registry must record this folder for the hub key.

    Report-only (DD-083 item 6): only a caller-anchored check can see it, so no
    pass acts on it, and ``workspace setup`` from this folder writes the row.
    It reads the rows the Brain end salvages; a registry whose rows cannot be
    read is the Brain end's finding, not a missing row.
    """
    from pathlib import Path
    from _bootstrap.diagnostics import RegistryCondition, inspect_registry
    import workspace_registry

    if workspace_registry.is_embedded(vault_root, key):
        return
    registry = inspect_registry(Path(vault_root))
    if registry.condition in {RegistryCondition.UNREADABLE, RegistryCondition.UNPARSEABLE}:
        return
    if not workspace_registry.row_records(registry.rows.get(key), workspace_dir):
        report("workspace_registry_missing", manifest_path,
               f"The selected Brain's linked workspace registry has no row for {key} at {workspace_dir}.",
               "Run brain workspace setup from this workspace", severity="info")
