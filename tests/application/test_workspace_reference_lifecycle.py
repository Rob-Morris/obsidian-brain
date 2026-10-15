"""Canonical relevance maintenance through the real prepared lifecycle owners."""

from dataclasses import replace

import pytest

from _application.artefact.archive import ArtefactArchiveRequest
from _application.artefact.delete import ArtefactDeleteRequest
from _application.artefact.set_key import ArtefactSetKeyRequest
from _application.registry import current_application_catalogue
from _application.workspace.create import WorkspaceCreateRequest
from _common import parse_frontmatter
from command_application import application_for
from test_workspace_skill_preparation import MatchingAdmission


@pytest.fixture
def relevance_only(command_vault_clone):
    root = command_vault_clone.vault_root
    app = application_for(root)
    created = app.invoke(WorkspaceCreateRequest("reference-target"))
    assert created.status == "ok", created
    paths = []
    for folder, kind in (("Ideas", "living/idea"), ("_Temporal/Plans", "temporal/plan")):
        for location in ("active", "root", "type", "owner"):
            relative = {
                "active": folder + "/Relevance.md",
                "root": "_Archive/2026-10-15/" + folder + "/Relevance.md",
                "type": folder + "/_Archive/Relevance.md",
                "owner": folder + "/Legacy owner/_Archive/Relevance.md",
            }[location]
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"---\ntype: {kind}\n"
                + ("key: relevance\n" if kind.startswith("living/") else "")
                + "tags:\n  - workspace/reference-target\n  - workspace/reference-target-extra\n"
                "  - workspace/other\n  - unrelated\n"
                "  - workspace~reference-target\n"
                "  - ' workspace/reference-target '\n---\n\nHistorical body.\n"
            )
            paths.append(relative)
    import compile_router
    compile_router.persist_compiled_router(str(root), compile_router.compile(str(root)))
    router = compile_router.compile(str(root))
    hub = router["artefact_index"]["workspace/reference-target"]["path"]
    return root, app, paths, hub


def _bytes(root, paths):
    return {path: (root / path).read_bytes() for path in paths}


def test_workspace_rekey_updates_active_and_archive_relevance_without_stranding(relevance_only):
    root, app, paths, hub = relevance_only
    before = _bytes(root, paths)
    result = app.invoke(ArtefactSetKeyRequest("workspace/reference-target", "reference-renamed"))
    assert result.status == "ok", result.error.message if result.status == "error" else result
    for path in paths:
        after = (root / path).read_bytes()
        assert after != before[path]
        fields, body = parse_frontmatter(after.decode())
        assert fields["tags"] == ["workspace/reference-renamed", "workspace/reference-target-extra",
                                  "workspace/other", "unrelated", "workspace~reference-target",
                                  " workspace/reference-target "]
        assert "workspace" not in fields and "parent" not in fields
        assert body == parse_frontmatter(before[path].decode())[1]
    assert b"key: reference-renamed" in (root / result.result.path).read_bytes()
    import compile_router
    from _common import scan_artefact_key_references

    router = compile_router.compile(str(root))
    old_references = scan_artefact_key_references(
        str(root), router, "workspace/reference-target", include_archive=True
    )
    assert not set(paths) & {item["path"] for item in old_references}
    new_references = scan_artefact_key_references(
        str(root), router, "workspace/reference-renamed", include_archive=True
    )
    assert set(paths) <= {item["path"] for item in new_references}


@pytest.mark.parametrize("command", [ArtefactDeleteRequest, ArtefactArchiveRequest])
def test_workspace_removal_preserves_authored_active_and_archived_relevance(relevance_only, command):
    root, app, paths, hub = relevance_only
    before = _bytes(root, paths)
    result = app.invoke(command(hub))
    assert result.status == "ok", result.error.message if result.status == "error" else result
    assert _bytes(root, paths) == before


@pytest.mark.parametrize("change", ["body", "association", "new-association"])
@pytest.mark.parametrize("archive_folder", ["Ideas/", "_Temporal/Plans/"])
def test_archived_relevance_changed_after_preparation_refuses_without_effects(
    relevance_only, change, archive_folder
):
    root, app, paths, hub = relevance_only
    request = ArtefactSetKeyRequest("workspace/reference-target", "reference-renamed")
    entry = current_application_catalogue().resolve(request)
    binding = entry.preparation.prepare(app._context, request)
    archived_path = next(path for path in paths if path.startswith("_Archive") and archive_folder in path)
    assert any(item.kind == "source" and item.identity == archived_path for item in binding.observations)
    archived = root / archived_path
    if change == "body":
        archived.write_bytes(archived.read_bytes() + b"Concurrent historical edit.\n")
    elif change == "association":
        archived.write_bytes(archived.read_bytes().replace(b"workspace/reference-target\n", b"workspace/other\n"))
    else:
        archived = archived.with_name("New association.md")
        archived.write_text("---\ntype: living/idea\nkey: another\ntags: [workspace/reference-target]\n---\nHistory\n")
        paths = [*paths, str(archived.relative_to(root))]
    before = _bytes(root, [*paths, hub])
    admission = MatchingAdmission(binding)
    result = entry.executor(replace(app._context, admission=admission), request)
    assert result.status == "error" and result.effects == "none", result
    assert "prepared operation changed" in result.error.message
    assert admission.calls == 0
    assert _bytes(root, [*paths, hub]) == before
    assert b"key: reference-target" in (root / hub).read_bytes()


def test_canonical_reference_replacement_can_preserve_soft_tags_on_removal():
    from _common import replace_artefact_key_references

    fields = {"parent": "workspace/old", "tags": ["workspace/old", "unrelated"]}
    assert replace_artefact_key_references(fields, "workspace/old", None, preserve_tags=True)
    assert fields == {"tags": ["workspace/old", "unrelated"]}
    ordinary = {"parent": "project/old", "tags": ["project/old", "unrelated"]}
    assert replace_artefact_key_references(ordinary, "project/old", None)
    assert ordinary == {"tags": ["unrelated"]}


def test_reference_scanner_archive_scope_is_explicit(relevance_only):
    import compile_router
    from _common import scan_artefact_key_references

    root, _app, paths, _hub = relevance_only
    router = compile_router.compile(str(root))
    active = scan_artefact_key_references(str(root), router, "workspace/reference-target")
    complete = scan_artefact_key_references(str(root), router, "workspace/reference-target", include_archive=True)
    active_paths = {item["path"] for item in active}
    complete_paths = {item["path"] for item in complete}
    assert all("_Archive" not in path.split("/") for path in active_paths)
    assert set(paths) <= complete_paths
    assert complete_paths - active_paths == {path for path in paths if "_Archive" in path.split("/")}


def test_workspace_alias_tags_do_not_create_references(relevance_only):
    import compile_router
    from _common import replace_artefact_key_references, scan_artefact_key_references

    root, app, paths, _hub = relevance_only
    aliases = ["workspace~reference-target", " workspace/reference-target "]
    for path in paths:
        (root / path).write_text(
            "---\ntype: living/idea\ntags: ['workspace~reference-target', "
            "' workspace/reference-target ']\n---\nAlias only.\n"
        )
    before = _bytes(root, paths)
    router = compile_router.compile(str(root))
    compile_router.persist_compiled_router(str(root), router)
    references = scan_artefact_key_references(
        str(root), router, "workspace/reference-target", include_archive=True
    )
    assert not set(paths) & {item["path"] for item in references}
    fields = {"parent": " workspace~reference-target ", "tags": aliases.copy()}
    assert replace_artefact_key_references(fields, "workspace/reference-target", "workspace/renamed")
    assert fields == {"parent": "workspace/renamed", "tags": aliases}
    ordinary = {"tags": [" project~old "]}
    assert replace_artefact_key_references(ordinary, "project/old", "project/new")
    assert ordinary == {"tags": ["project/new"]}
    result = app.invoke(ArtefactSetKeyRequest("workspace/reference-target", "reference-renamed"))
    assert result.status == "ok", result
    assert _bytes(root, paths) == before


@pytest.mark.parametrize("location", ["_Archive/old", "Ideas/_Archive", "Ideas/Owner/_Archive"])
def test_archive_reference_scan_keeps_strict_read_errors(relevance_only, location):
    import compile_router
    from _common import UnreadableVaultTextFilesError, scan_artefact_key_references
    from _portable.vault_files import list_archived_artefacts

    root, _app, _paths, _hub = relevance_only
    broken = root / location / "Unreadable.md"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_bytes(b"\xff\xfe\x00")
    router = compile_router.compile(str(root))
    with pytest.raises(UnreadableVaultTextFilesError):
        scan_artefact_key_references(str(root), router, "workspace/reference-target", include_archive=True)
    entry = next(item for item in list_archived_artefacts(router, str(root))
                 if item["path"] == str(broken.relative_to(root)))
    assert entry["type"] == "" and entry["tags"] == [] and entry["workspace"] is None


def test_archive_enumeration_never_opens_outside_vault(relevance_only, tmp_path, monkeypatch):
    import builtins
    from pathlib import Path

    import compile_router
    from _common import scan_artefact_key_references
    from _portable.vault_files import list_archived_artefacts

    root, _app, paths, _hub = relevance_only
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "Secret.md"
    secret.write_text("---\ntags: [workspace/reference-target]\n---\nOutside.\n")
    for location in ("_Archive", "Ideas/_Archive", "Ideas/Owner/_Archive"):
        archive = root / location
        archive.mkdir(parents=True, exist_ok=True)
        (archive / "Escape.md").symlink_to(secret)
        (archive / "Escape directory").symlink_to(outside, target_is_directory=True)
    (root / "Ideas/Outside owner").symlink_to(outside, target_is_directory=True)
    (root / "Linked type").symlink_to(outside, target_is_directory=True)
    router = compile_router.compile(str(root))
    router["artefacts"].append({"path": "Linked type", "classification": "living"})
    original_open = builtins.open

    def guarded_open(file, *args, **kwargs):
        if isinstance(file, (str, bytes, Path)):
            assert not Path(file).resolve().is_relative_to(outside.resolve()), file
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    references = scan_artefact_key_references(
        str(root), router, "workspace/reference-target", include_archive=True
    )
    listed = list_archived_artefacts(router, str(root))
    assert set(paths) <= {item["path"] for item in references}
    assert {path for path in paths if "_Archive" in path.split("/")} <= {
        item["path"] for item in listed
    }
    assert not any("Escape" in item["path"] for item in [*references, *listed])


@pytest.mark.parametrize("location", ["_Archive/old", "Ideas/_Archive", "Ideas/Owner/_Archive"])
def test_archive_listing_retains_lossless_bom_workspace_metadata(relevance_only, location):
    import compile_router
    from _portable.vault_files import list_archived_artefacts

    root, _app, _paths, _hub = relevance_only
    archived = root / location / "Workspace history.md"
    archived.parent.mkdir(parents=True, exist_ok=True)
    archived.write_bytes(
        b"\xef\xbb\xbf---\ntype: living/workspace\nkey: history\n"
        b"workspace: reference-target\ntags: [workspace/reference-target]\n---\nHistory.\n"
    )
    before = archived.read_bytes()
    router = compile_router.compile(str(root))
    entry = next(item for item in list_archived_artefacts(router, str(root))
                 if item["path"] == str(archived.relative_to(root)))
    assert entry["type"] == "living/workspace"
    assert entry["key"] == "history"
    assert entry["workspace"] == "reference-target"
    assert entry["tags"] == ["workspace/reference-target"]
    assert archived.read_bytes() == before


@pytest.mark.parametrize("location", ["_Archive", "Ideas/_Archive", "Ideas/Owner/_Archive"])
def test_archive_roots_outside_vault_are_not_walked(tmp_path, monkeypatch, location):
    from types import SimpleNamespace

    from _common import _artefacts
    from _portable.vault_files import list_archived_artefacts

    root = tmp_path / "vault"
    outside = tmp_path / "outside"
    outside.mkdir()
    archive = root / location
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.symlink_to(outside, target_is_directory=True)
    real_os = _artefacts.os

    def bounded_walk(path, *args, **kwargs):
        from pathlib import Path
        assert not Path(path).resolve().is_relative_to(outside), path
        return real_os.walk(path, *args, **kwargs)

    monkeypatch.setattr(_artefacts, "os", SimpleNamespace(
        path=real_os.path, listdir=real_os.listdir, walk=bounded_walk
    ))
    router = {"artefacts": [{"path": "Ideas", "classification": "living"}]}
    assert list(_artefacts.iter_archived_artefact_paths(str(root), router)) == []
    assert list_archived_artefacts(router, str(root)) == []
