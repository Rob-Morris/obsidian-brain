"""One policy inventory separates intentional artefact subjects from maintenance."""

SEMANTIC_ARTEFACT_COMMANDS = frozenset({
    "artefact.create", "document.write-body", "document.update-frontmatter",
    "document.structured-edit", "document.replace-text", "artefact.rename",
    "artefact.set-naming-field", "artefact.set-status", "artefact.set-key",
    "artefact.convert", "artefact.reparent", "artefact.reparent-children",
    "artefact.archive", "artefact.unarchive", "artefact.delete", "artefact.set-workspace", "shaping.start",
})
MAINTENANCE_ARTEFACT_COMMANDS = frozenset({
    "links.fix", "artefact.repair", "artefact.migrate-naming",
    "runtime.refresh-router", "retrieval.refresh-lexical", "retrieval.repair-semantic",
})


def applies_policy_tags(command_id):
    """Deletion and maintenance have no surviving semantic subject to tag."""
    if command_id in MAINTENANCE_ARTEFACT_COMMANDS or command_id == "artefact.delete":
        return False
    if command_id not in SEMANTIC_ARTEFACT_COMMANDS:
        raise ValueError(f"{command_id} is not a classified semantic artefact mutation")
    return True
