"""Transition-local ownership across current, terminal and archived artefacts."""

from dataclasses import dataclass
from collections import deque
from pathlib import Path

from _common import normalize_artefact_key, parse_frontmatter, read_exact_file_content, UnreadableVaultTextFilesError, vault_text_failure
from .frontmatter_repairs import iter_candidate_artefact_markdown_files


@dataclass(frozen=True)
class OwnershipRecord:
    path: str
    reference: str | None
    fields: dict
    body: str

    @property
    def parent(self):
        return normalize_artefact_key(self.fields.get("parent"))

    @property
    def archived(self):
        return self.path.startswith("_Archive/")


class OwnershipGraph:
    """Only living identities can own children; temporal keys are vestigial."""

    def __init__(self, records):
        self.records = tuple(records)
        self.by_path = {record.path: record for record in self.records}
        self.identities = {}
        self.children = {}
        for record in self.records:
            if record.reference:
                self.identities.setdefault(record.reference, []).append(record)
            if record.parent:
                self.children.setdefault(record.parent, []).append(record)

    def resolve(self, reference):
        """Resolve a unique living identity, never choose among archive duplicates."""
        matches = self.identities.get(reference, ())
        if len(matches) > 1:
            raise ValueError(f"Ambiguous living identity {reference}: " + ", ".join(item.path for item in matches))
        if not matches:
            raise ValueError(f"Missing living owner {reference}; temporal artefacts cannot own descendants")
        return matches[0]

    def subtree(self, path):
        """Traverse explicit parent edges and reject cycles or ambiguous owners."""
        root = self.by_path[path]
        pending = deque([root])
        selected = {}
        while pending:
            record = pending.popleft()
            if record.path in selected:
                raise ValueError(f"Cyclic ownership through {record.path}")
            selected[record.path] = record
            if record.reference:
                self.resolve(record.reference)
                pending.extend(self.children.get(record.reference, ()))
            elif record.fields.get("key"):
                vestigial = normalize_artefact_key(str(record.fields.get("type", "")).rsplit("/", 1)[-1] + "/" + str(record.fields["key"]))
                if self.children.get(vestigial):
                    raise ValueError(f"Temporal artefact {record.path} cannot own descendants through {vestigial}")
        return tuple(selected.values())

    def observation(self):
        """Return only structural facts needed to detect new or changed ownership."""
        return tuple({"path": item.path, "reference": item.reference,
            **{field: item.fields.get(field) for field in ("type", "key", "parent", "workspace", "status")}}
            for item in self.records)

    def validate_lineage(self, record):
        """Check an ownership chain without repeatedly traversing whole subtrees."""
        seen = {record.path}
        while record.parent:
            record = self.resolve(record.parent)
            if record.path in seen:
                raise ValueError(f"Cyclic ownership through {record.path}")
            seen.add(record.path)


def read_ownership_graph(vault_root, *, text_reader=None):
    """Scan all artefact source classes without inferring ownership from paths/tags."""
    root = Path(vault_root).resolve()
    records = []
    failures = []
    for path in sorted(iter_candidate_artefact_markdown_files(root)):
        source = root / path
        try:
            source.resolve().relative_to(root)
            text = text_reader(source) if text_reader else read_exact_file_content(source, convert_lossless=True)
            fields, body = parse_frontmatter(text)
        except (OSError, UnicodeError, ValueError) as exc:
            failures.append(vault_text_failure(path, exc))
            continue
        type_name = fields.get("type", "")
        if not isinstance(type_name, str) or not type_name.startswith(("living/", "temporal/")):
            continue
        reference = None
        if type_name.startswith("living/"):
            reference = normalize_artefact_key(type_name.rsplit("/", 1)[-1] + "/" + str(fields.get("key", "")))
        records.append(OwnershipRecord(path, reference, fields, body))
    if failures:
        raise UnreadableVaultTextFilesError(failures)
    return OwnershipGraph(records)
