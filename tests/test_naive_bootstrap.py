"""Contract tests for the naive-agent bootstrap layer.

The naive path is the shipped-markdown fallback for an agent with no MCP, no
generated session mirror, and no ability to run code: copy the template vault
somewhere, point an agent at it, and it must be able to work with the vault
correctly. See `docs/standards/naive-agent-bootstrap.md` for the policy these
tests enforce.

Each test locks in one invariant that has silently regressed before. They run
against the authored sources in `src/brain-core/` and the installed copies in
`template-vault/`, not a built vault, so a break is attributable to the edit that
caused it.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest
import yaml

from _application.registry import current_application_catalogue
from _common import AGENT_INSTRUCTION_RE, FM_RE, is_valid_key
from _common._markdown import resolve_structural_target
from check import check_authoring_hint_tokens, check_frontmatter_required, run_checks
from compile_router import naming_storage_root, parse_taxonomy_content
from _portable.vault_files import PUBLIC_BRAIN_CORE_FILES, PUBLIC_BRAIN_CORE_TREES

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "src" / "brain-core"
TEMPLATE_VAULT = REPO / "template-vault"
LIBRARY = CORE / "artefact-library"
STANDARDS = CORE / "standards"

# Dotted tokens in prose that are filenames, not `<noun>.<verb>` command ids.
FILE_EXTENSIONS = frozenset(
    {
        "md", "py", "json", "yaml", "yml", "css", "sh", "txt", "log",
        "toml", "cfg", "ini", "lock", "example", "gitignore",
    }
)

# Namespaces that are never Brain commands. Excluding whole namespaces is more
# stable than listing individual tokens as docs gain code examples.
NON_COMMAND_NAMESPACES = frozenset(
    {
        # Python identifiers cited in prose and code samples.
        "os", "sys", "re", "json", "time", "datetime", "pathlib", "yaml",
        "subprocess", "shutil", "random", "string", "math", "typing",
        # Obsidian / CSS field names.
        "color",
    }
)

# Dotted tokens that look like `<noun>.<verb>` but are not application commands.
# Keep this list short and justified — every entry weakens the catalogue check.
KNOWN_NON_COMMANDS = {
    "mcp.configure": "launcher-owned setup command (configure.py), outside the application catalogue",
}

# Shipped docs a naive agent can reach — the public brain-core namespace enforced
# by `_portable/vault_files.py`: five root files plus four trees.
BACKTICKED = re.compile(r"`([^`\n]+)`")
DOTTED_COMMAND = re.compile(r"^([a-z][a-z0-9-]*)\.([a-z][a-z0-9-]*)$")
PLACEHOLDER = re.compile(r"\{[^}]+\}")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section_body(text: str, heading: str) -> str:
    """Return the body of an H2 section using the canonical markdown resolver."""
    resolved = resolve_structural_target(text, f"## {heading}")
    start, end = resolved["ranges"]["body"]
    return text[start:end]


def _public_docs() -> list[Path]:
    docs = [CORE / name for name in PUBLIC_BRAIN_CORE_FILES]
    for tree in PUBLIC_BRAIN_CORE_TREES:
        docs.extend(sorted((CORE / tree).rglob("*.md")))
    return [p for p in docs if p.is_file()]


def _cited_commands(text: str) -> set[str]:
    cited = set()
    for token in BACKTICKED.findall(text):
        token = token.split("(")[0].strip()
        match = DOTTED_COMMAND.match(token)
        if not match:
            continue
        noun, verb = match.groups()
        if verb in FILE_EXTENSIONS or noun in NON_COMMAND_NAMESPACES:
            continue
        if len(noun) == 1:  # generic `a.b` illustrations
            continue
        cited.add(token)
    return cited


def _frontmatter_example(taxonomy_text: str) -> str | None:
    body = _section_body(taxonomy_text, "Frontmatter")
    match = re.search(r"```yaml\n---\n(.*?)\n---\n```", body, re.S)
    return match.group(1) if match else None


def _required_fields(type_dir: Path) -> list[str]:
    schema = type_dir / "schema.yaml"
    if not schema.is_file():
        return []
    parsed = yaml.safe_load(_read(schema)) or {}
    return list(parsed.get("required") or {})


def _living_type_dirs() -> list[Path]:
    return sorted(
        d
        for d in (LIBRARY / "living").iterdir()
        if (d / "taxonomy.md").is_file()
    )


def _all_type_dirs() -> list[Path]:
    return sorted(
        d
        for classification in ("living", "temporal")
        for d in (LIBRARY / classification).iterdir()
        if (d / "taxonomy.md").is_file()
    )


@pytest.fixture(scope="module")
def command_ids() -> frozenset[str]:
    return frozenset(e.command_id for e in current_application_catalogue().entries)


class TestBootstrapRouting:
    """md-bootstrap.md is the naive agent's only entry point — it must resolve."""

    def test_every_referenced_path_exists_in_the_template_vault(self):
        text = _read(CORE / "md-bootstrap.md")
        # Backticked spans include full command lines; the path is the first token
        # that names a vault location.
        referenced = []
        for span in BACKTICKED.findall(text):
            for token in span.split():
                if token.startswith((".brain-core/", "_Config/")):
                    referenced.append(token)
        assert referenced, "md-bootstrap.md references no vault paths"

        missing = []
        for token in referenced:
            if token.startswith(".brain-core/"):
                candidate = CORE / token.removeprefix(".brain-core/")
            else:
                candidate = TEMPLATE_VAULT / token
            if not candidate.exists():
                missing.append(token)
        assert not missing, f"md-bootstrap.md references non-existent paths: {missing}"

    def test_script_invocations_name_an_interpreter(self):
        """command.py is mode 644 — a bare path invocation is permission denied."""
        text = _read(CORE / "md-bootstrap.md")
        bare = [
            span
            for span in BACKTICKED.findall(text)
            if span.lstrip().startswith(".brain-core/scripts/")
        ]
        assert not bare, (
            f"documented as directly executable but scripts are not: {bare}"
        )


class TestCommandNames:
    """Shipped docs must not advertise commands that do not exist."""

    @pytest.mark.parametrize(
        "doc", _public_docs(), ids=lambda p: str(p.relative_to(CORE))
    )
    def test_dotted_command_names_exist_in_the_catalogue(self, doc, command_ids):
        cited = _cited_commands(_read(doc))
        unknown = sorted(
            name for name in cited if name not in command_ids and name not in KNOWN_NON_COMMANDS
        )
        assert not unknown, (
            f"{doc.relative_to(CORE)} cites command names absent from the catalogue: {unknown}"
        )


class TestRequiredFieldVisibility:
    """R1 — every schema-required field must be visible without tooling."""

    @pytest.mark.parametrize(
        "type_dir", _all_type_dirs(), ids=lambda p: str(p.relative_to(LIBRARY))
    )
    def test_frontmatter_example_declares_every_required_field(self, type_dir):
        example = _frontmatter_example(_read(type_dir / "taxonomy.md"))
        assert example is not None, f"{type_dir.name}: no yaml Frontmatter example"
        declared = set(re.findall(r"^([a-z_]+):", example, re.M))
        missing = sorted(set(_required_fields(type_dir)) - declared)
        assert not missing, (
            f"{type_dir.name}: schema requires {missing} but the Frontmatter example "
            "omits them, so an agent authoring from it produces a non-compliant artefact"
        )

    @pytest.mark.parametrize(
        "type_dir", _all_type_dirs(), ids=lambda p: str(p.relative_to(LIBRARY))
    )
    def test_template_supplies_or_names_every_required_field(self, type_dir):
        template = _read(type_dir / "template.md")
        match = re.match(r"---\n(.*?)\n---\n", template, re.S)
        present = set(re.findall(r"^([a-z_]+):", match.group(1), re.M)) if match else set()
        hints = " ".join(AGENT_INSTRUCTION_RE.findall(template))

        unmet = [
            field
            for field in _required_fields(type_dir)
            if field not in present and f"`{field}:`" not in hints
        ]
        assert not unmet, (
            f"{type_dir.name}: required {unmet} neither present in template frontmatter "
            "nor named by an authoring hint"
        )

    @pytest.mark.parametrize(
        "type_dir", _all_type_dirs(), ids=lambda p: str(p.relative_to(LIBRARY))
    )
    def test_frontmatter_example_parses_once_placeholders_are_substituted(self, type_dir):
        """The example must be valid YAML after placeholder substitution.

        Braces are documentation placeholders. `key: {key}` parses as a nested
        mapping rather than a string, so the example is only meaningful once a real
        value replaces it — which is what md-bootstrap.md instructs.
        """
        example = _frontmatter_example(_read(type_dir / "taxonomy.md"))
        substituted = PLACEHOLDER.sub("example-value", example)
        parsed = yaml.safe_load(substituted)
        assert isinstance(parsed, dict), f"{type_dir.name}: example is not a mapping"

    @pytest.mark.parametrize("type_dir", _living_type_dirs(), ids=lambda p: p.name)
    def test_frontmatter_example_key_is_canonical_after_substitution(self, type_dir):
        example = _frontmatter_example(_read(type_dir / "taxonomy.md"))
        parsed = yaml.safe_load(PLACEHOLDER.sub("example-value", example))
        assert is_valid_key(parsed.get("key")), (
            f"{type_dir.name}: `key` does not satisfy the canonical contract once "
            f"substituted (got {parsed.get('key')!r})"
        )

    @pytest.mark.parametrize("type_dir", _living_type_dirs(), ids=lambda p: p.name)
    @pytest.mark.parametrize("candidate", ("123", "a" * 64, "a" * 65))
    def test_key_schema_matches_canonical_validation(self, type_dir, candidate):
        schema = yaml.safe_load(_read(type_dir / "schema.yaml")) or {}
        key_rule = (schema.get("required") or {}).get("key") or {}
        pattern = key_rule.get("pattern")

        assert isinstance(pattern, str), f"{type_dir.name}: key pattern is missing"
        assert bool(re.fullmatch(pattern, candidate)) == is_valid_key(candidate), (
            f"{type_dir.name}: schema and is_valid_key disagree for {candidate!r}"
        )


class TestRequirednessOwnership:
    """One defect, one finding — and the taxonomy may not contradict the schema."""

    def test_a_missing_key_is_reported_once(self, command_vault_clone):
        """`living_key_fields` owns key presence; frontmatter_required defers."""
        vault = command_vault_clone.vault_root
        artefact = vault / "Designs" / "Keyless.md"
        artefact.parent.mkdir(parents=True, exist_ok=True)
        artefact.write_text(
            "---\ntype: living/design\ntags:\n  - design\nstatus: shaping\n"
            "created: 2026-08-14T10:00:00+10:00\n"
            "modified: 2026-08-14T10:00:00+10:00\n---\n\nNo key.\n",
            encoding="utf-8",
        )
        findings = [
            f for f in run_checks(str(vault))["findings"]
            if f.get("file") == "Designs/Keyless.md"
        ]
        assert [f["check"] for f in findings] == ["living_key_fields"], (
            f"expected exactly one owning finding, got {[f['check'] for f in findings]}"
        )
        assert findings[0]["severity"] == "error"

    def test_missing_key_in_terminal_status_folder_is_reported_once(
        self, command_vault_clone
    ):
        vault = command_vault_clone.vault_root
        artefact = vault / "Designs" / "+Implemented" / "Keyless.md"
        artefact.parent.mkdir(parents=True, exist_ok=True)
        artefact.write_text(
            "---\ntype: living/design\ntags:\n  - design\nstatus: implemented\n"
            "created: 2026-08-14T10:00:00+10:00\n"
            "modified: 2026-08-14T10:00:00+10:00\n---\n\nNo key.\n",
            encoding="utf-8",
        )
        findings = [
            finding
            for finding in run_checks(str(vault))["findings"]
            if finding.get("file") == "Designs/+Implemented/Keyless.md"
            and finding.get("check") in {"frontmatter_required", "living_key_fields"}
        ]
        assert [finding["check"] for finding in findings] == ["living_key_fields"]

    def test_temporal_required_key_is_not_delegated_to_living_check(self, tmp_path):
        artefact = tmp_path / "_Temporal" / "Events" / "Keyless.md"
        artefact.parent.mkdir(parents=True)
        artefact.write_text(
            "---\ntype: temporal/event\ntags:\n  - event\n---\n\nNo key.\n",
            encoding="utf-8",
        )
        router = {
            "artefacts": [
                {
                    "classification": "temporal",
                    "configured": True,
                    "frontmatter": {"required": ["type", "tags", "key"]},
                    "path": "_Temporal/Events",
                }
            ]
        }

        findings = check_frontmatter_required(str(tmp_path), router)

        assert [finding["message"] for finding in findings] == [
            "Missing required field: key"
        ]

    @pytest.mark.parametrize(
        "type_dir", _all_type_dirs(), ids=lambda p: str(p.relative_to(LIBRARY))
    )
    def test_declared_optional_never_contradicts_the_schema(self, type_dir):
        """A taxonomy may promote a schema-optional field to required, never the reverse."""
        text = _read(type_dir / "taxonomy.md")
        declaration = re.search(r"^\*\*Optional:\*\*[ \t]*([^\r\n]+)$", text, re.M)
        if declaration is None:
            return
        declared = set(re.findall(r"`([^`]+)`", declaration.group(1)))
        widened = sorted(declared & set(_required_fields(type_dir)))
        assert not widened, (
            f"{type_dir.name}: declares {widened} optional but schema.yaml requires them"
        )


class TestAuthoringHints:
    """A naive agent has no tooling to strip hints — each must say to remove it."""

    REMOVAL_INSTRUCTION = "Delete this line once applied."

    def test_template_frontmatter_contains_no_brace_placeholders(self):
        offenders = []
        for template in sorted(LIBRARY.rglob("template.md")):
            match = FM_RE.match(_read(template))
            if match and PLACEHOLDER.search(match.group(1)):
                offenders.append(str(template.relative_to(LIBRARY)))
        assert not offenders, f"template frontmatter contains placeholders: {offenders}"

    def test_every_hint_is_self_terminating(self):
        offenders = []
        for template in sorted(LIBRARY.rglob("template.md")):
            for hint in AGENT_INSTRUCTION_RE.findall(_read(template)):
                if self.REMOVAL_INSTRUCTION not in hint:
                    offenders.append(str(template.relative_to(LIBRARY)))
        assert not offenders, (
            f"authoring hints missing '{self.REMOVAL_INSTRUCTION}': {offenders}"
        )

    def test_leaked_hint_is_reported_through_run_checks(self, command_vault_clone):
        """The check must fire through the real compliance surface, not just alone."""
        vault = command_vault_clone.vault_root
        assert run_checks(str(vault))["findings"] == [], "clone did not start clean"

        artefact = vault / "Designs" / "Leaked Hint.md"
        artefact.parent.mkdir(parents=True, exist_ok=True)
        artefact.write_text(
            "---\ntype: living/design\nkey: leaked-hint\ntags:\n  - design\n"
            "status: shaping\n---\n\n"
            "{{agent: also set `key:`. Delete this line once applied.}}\n",
            encoding="utf-8",
        )

        findings = run_checks(str(vault))["findings"]
        leaked = [f for f in findings if f.get("check") == "authoring_hint_tokens"]
        assert leaked, (
            "run_checks did not surface authoring_hint_tokens; saw "
            f"{sorted({f.get('check') for f in findings})}"
        )
        assert leaked[0]["file"] == "Designs/Leaked Hint.md"
        assert leaked[0]["severity"] == "warning"


class TestTypeDiscovery:
    """md-bootstrap routes type discovery to _Config/Taxonomy/, so it must be complete."""

    @pytest.mark.parametrize(
        "classification", ["Living", "Temporal"], ids=["living", "temporal"]
    )
    def test_installed_taxonomy_declares_folders_that_exist(self, classification):
        taxonomy_dir = TEMPLATE_VAULT / "_Config" / "Taxonomy" / classification
        installed = sorted(taxonomy_dir.glob("*.md"))
        assert installed, f"no {classification} taxonomy files installed"

        missing = []
        for taxonomy in installed:
            # Parse the installed copy — that is what a naive agent reads.
            parsed = parse_taxonomy_content(_read(taxonomy))
            naming = parsed.get("naming") or {}
            folder = naming_storage_root(naming.get("folder"))
            if folder is None:
                missing.append(f"{taxonomy.stem} (declares no folder)")
            elif not (TEMPLATE_VAULT / folder).is_dir():
                missing.append(f"{taxonomy.stem} -> {folder}/")
        assert not missing, (
            f"{classification} types declare folders absent from the template vault: {missing}"
        )


class TestStandardsIndexes:
    """An unindexed standard is unreachable — that is how keys.md was orphaned."""

    def test_standards_readme_is_exhaustive(self):
        """R5: README.md is exhaustive; session-core.md stays deliberately curated."""
        shipped = {p.name for p in STANDARDS.glob("*.md")} - {"README.md"}
        readme = _read(STANDARDS / "README.md")
        missing = sorted(name for name in shipped if name not in readme)
        assert not missing, f"standards/README.md omits: {missing}"

    def test_md_bootstrap_routes_to_the_exhaustive_index(self):
        """The curated session-core list is only sufficient because this route exists."""
        assert "standards/README.md" in _read(CORE / "md-bootstrap.md"), (
            "md-bootstrap.md must route to standards/README.md — it is the only "
            "exhaustive standards index on the naive path"
        )
