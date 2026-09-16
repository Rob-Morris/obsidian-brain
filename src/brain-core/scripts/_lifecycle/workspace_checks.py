"""Workspace diagnostics reuse the complete ownership scan without mutating it."""

from .ownership_graph import read_ownership_graph


def workspace_findings(vault_root, router, *, workspace_dir=None):
    from _common._workspace import membership, require_workspace, workspace_policy, validate_ownership
    from _bootstrap.workspace_binding import load_workspace_manifest_state, resolve_selected_workspace_binding, WorkspaceBindingError

    findings = []

    def report(code, path, message, fix, severity="error"):
        findings.append({"check": "workspace_contract", "code": code, "severity": severity,
                         "file": path, "message": message, "fix": fix})

    try:
        graph = read_ownership_graph(vault_root)
    except ValueError as exc:
        report("workspace_scan_unreadable", None, str(exc), "Repair the unreadable or out-of-bounds source and retry vault.check")
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
            report("workspace_reference_malformed", record.path, str(exc), "Use artefact.set-workspace with an explicit destination; workspace hubs must remain self-scoped")
        archived_self = record.archived and fields.get("type") == "living/workspace" and workspace == record.reference
        if workspace and not archived_self:
            entry = router.get("artefact_index", {}).get(workspace)
            if entry is None:
                candidates = graph.identities.get(workspace, ())
                archived = any(item.archived for item in candidates)
                code = "workspace_reference_archived" if archived else "workspace_reference_missing"
                report(code, record.path, f"{workspace} has no current living workspace hub" + (" (archived identity)" if archived else ""),
                       "Restore the original hub or explicitly reassign affected artefacts; tags are not membership")
            elif entry.get("type") != "living/workspace":
                report("workspace_reference_wrong_type", record.path, f"{workspace} resolves to {entry.get('type')}, not living/workspace", "Restore the workspace hub's type/identity before mutating its members")
            else:
                try:
                    require_workspace(router, workspace)
                except ValueError as exc:
                    report("workspace_hub_invalid", record.path, str(exc), "Repair the hub's self-scope and ownership explicitly")
        try:
            if record.reference:
                graph.resolve(record.reference)
            if record.parent:
                owner = graph.resolve(record.parent)
                if owner.archived and not record.archived:
                    raise ValueError(f"Active artefact has archived parent {record.parent}")
            validate_ownership(reference, fields, complete_index)
            graph.validate_lineage(record)
        except ValueError as exc:
            report("workspace_ownership_invalid", record.path, str(exc), "Use artefact.set-workspace recursively with an explicit replacement/cleared root parent, or repair the living owner identity")
        if fields.get("type") == "living/workspace":
            try:
                workspace_policy(router, workspace, fields)
            except ValueError as exc:
                report("workspace_policy_invalid", record.path, str(exc), "Use workspace.update-policy to replace/clear the shared parent or tags")
        if fields.get("workspace") is None and fields.get("type") != "living/workspace":
            tags = fields.get("tags", ())
            if isinstance(tags, list) and any(isinstance(tag, str) and tag.startswith("workspace/") for tag in tags):
                report("workspace_adoption_candidate", record.path, "Legacy workspace relationship tags do not establish membership", "Review ownership and explicitly adopt with artefact.set-workspace; never infer membership from tags", "info")
    if workspace_dir is not None:
        manifest_path = str(workspace_dir)
        try:
            state = load_workspace_manifest_state(workspace_dir)
            manifest_path = str(state.manifest_path)
            binding, _reference, guidance = resolve_selected_workspace_binding(vault_root, router, state.data)
            if binding not in {"valid", "unconfigured"}:
                report("workspace_binding_" + binding, manifest_path, guidance or binding, "Run brain workspace setup for the intended selected Brain; repair local defaults or reactivate the hub as reported")
        except (WorkspaceBindingError, OSError, ValueError) as exc:
            report("workspace_binding_configured_invalid", manifest_path, str(exc), "Repair the local manifest with brain workspace setup / workspace.update-metadata")
    return findings
