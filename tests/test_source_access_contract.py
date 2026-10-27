"""Keep public-reader I/O in the small, reviewed source-admission shells.

This ownership ratchet is deliberately syntactic, not data-flow authorisation.
Behavioural source/refusal tests prove that each admitted path is actually used.
"""

import ast
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "src/brain-core/scripts"
MODULES = (
    "read.py",
    "_common/_source_access.py",
    "_portable/artefact_read.py", "_portable/artefact_outline.py",
    "_portable/named_documents.py", "_portable/router_collections.py",
    "_portable/type_definitions.py", "_portable/vault_files.py",
    "_search/resource.py", "_search/snippet.py", "_search/index.py",
    "_lifecycle/document_parts.py", "_portable/maintenance_inputs.py",
    "start_shaping_session.py", "_application/_managed_preparation.py",
    "_application/shaping/_start_preparation.py",
    "_application/shaping/_render_preparation.py", "_application/workspace_transitions.py",
)
SHELLS = {
    "_common/_source_access.py::read_public_content": "admitted canonical source after extension inference",
    "_portable/vault_files.py::_read_exact_file": "explicit canonical archive source",
    "_portable/vault_files.py::read_vault_file": "canonical public-file source",
    "_search/resource.py::_read_file_body": "canonical resource body before scoring",
    "_search/snippet.py::extract_snippet": "canonical public snippet source",
    "_search/index.py::parse_doc": "canonical public source before tokens/embedding parts",
    "_lifecycle/document_parts.py::read_embedding_document": "canonical source before current semantic metadata",
    "_portable/maintenance_inputs.py::file_identity": "neutral byte hashing; callers must supply admitted sources",
    "_portable/maintenance_inputs.py::public_file_identity": "canonical public/archive admission before hash reader",
    "_portable/maintenance_inputs.py::owned_file_identity": "fixed-record admission before hash reader",
    "start_shaping_session.py::_linked_transcripts": "admitted linked transcript before reciprocity inspection",
    "start_shaping_session.py::_choose_transcript_path": "admitted transcript candidates before width/collision inspection",
    "start_shaping_session.py::_prepare_shaping_target": "admitted canonical public or explicit archive target",
    "start_shaping_session.py::start_shaping_session": "admitted lifecycle-refreshed target and existing transcript",
    "_application/shaping/_start_preparation.py::session_plan": "admitted existing transcript before workspace policy planning",
    "_application/shaping/_start_preparation.py::semantic_transition": "admitted existing transcript before semantic write planning",
    "_application/shaping/_render_preparation.py::render_binding": "admitted support-file and explicit local-tool hashes; corpus/output observations use public_file_identity",
    "_application/workspace_transitions.py::source_effect_snapshot": "canonical admitted concrete sources before bytes, revisions and conversion diagnosis",
    "_application/workspace_transitions.py::prepare_workspace_transition": "canonical admitted transition subjects; parent revisions use public_file_identity",
}
RAW_READS = {"read_exact_file_content", "read_file_content", "read_artefact", "file_identity"}


def _read_sites(source):
    tree = ast.parse(source)
    aliases = {"open", "file_identity"}
    modules = {"builtins"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "builtins" or module.startswith("_common") or module in {"_artefacts", "_frontmatter"}:
                for alias in node.names:
                    if alias.name in RAW_READS or alias.name == "open":
                        aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "builtins" or alias.name.startswith("_common"):
                    modules.add(alias.asname or alias.name)

    class Reads(ast.NodeVisitor):
        def __init__(self):
            self.scope = []
            self.sites = set()

        def _record(self):
            self.sites.add(".".join(self.scope) or "<module>")

        def visit_FunctionDef(self, node):
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef

        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load) and node.id in aliases:
                self._record()

        def visit_Attribute(self, node):
            if (node.attr in {"open", "read_text", "read_bytes"}
                    or (node.attr in RAW_READS and isinstance(node.value, ast.Name)
                        and node.value.id in modules)):
                self._record()
            self.generic_visit(node)

        def visit_Call(self, node):
            if (isinstance(node.func, ast.Name) and node.func.id == "getattr"
                    and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value in {"open", "read_text", "read_bytes"} | RAW_READS):
                self._record()
            self.generic_visit(node)

    visitor = Reads()
    visitor.visit(tree)
    return visitor.sites


def test_public_content_reads_remain_in_reviewed_admission_shells():
    found = {
        f"{module}::{scope}"
        for module in MODULES
        for scope in _read_sites((SCRIPTS / module).read_text(encoding="utf-8"))
    }
    assert found <= SHELLS.keys(), f"New content read needs source-policy review: {sorted(found - SHELLS.keys())}"
    assert SHELLS.keys() <= found, f"Remove stale shell exceptions: {sorted(SHELLS.keys() - found)}"


@pytest.mark.parametrize("body", [
    "open(path).read()",
    "path.read_text()",
    "from builtins import open as reader\nreader(path)",
    "from _common import read_artefact as reader\nreader(path)",
    "from _common import read_exact_file_content as reader\nreader(path)",
    "import _common as common\ncommon.read_file_content(root, path)",
    "reader = open\nreader(path)",
    "getattr(path, 'read_bytes')()",
])
def test_ratchet_detects_direct_reader_references(body):
    assert _read_sites("def unexpected():\n" + "\n".join("    " + line for line in body.splitlines())) == {"unexpected"}


def test_ratchet_accepts_the_public_owner_and_non_io_mentions():
    assert not _read_sites("def read():\n    # open is not an operation here\n    return read_public_content(root, path)\n")
