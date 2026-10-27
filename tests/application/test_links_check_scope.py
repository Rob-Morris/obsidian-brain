"""Scoped link observations stay bounded to their source and content namespace."""

from pathlib import Path
from types import SimpleNamespace
import os

import pytest

from _application.links.check import LinksCheckRequest, execute
from _common import _wikilinks


def _check(root, path=None):
    return execute(SimpleNamespace(selected_brain=SimpleNamespace(vault_root=root)),
                   LinksCheckRequest(path))


@pytest.mark.parametrize("cached", [False, True])
def test_link_check_prunes_hidden_onboarding_sources(tmp_path, monkeypatch, cached):
    import check
    import _portable.links as portable

    (tmp_path / "Public.md").write_text("[[Missing public title]]")
    rule = tmp_path / ".grok/rules/brain.md"
    rule.parent.mkdir(parents=True)
    rule.write_text("[[Private onboarding title]]")
    (tmp_path / ".hidden.md").write_text("[[Private root title]]")
    walk = os.walk

    def public_walk(root):
        for row in walk(root):
            assert not any(part.startswith(".") for part in Path(row[0]).relative_to(tmp_path).parts)
            yield row

    monkeypatch.setattr(portable, "os", SimpleNamespace(path=os.path, walk=public_walk))
    context = check.CheckContext(str(tmp_path), {}) if cached else None
    findings = portable.check_broken_wikilinks(str(tmp_path), {}, ctx=context)
    assert [(item["file"], item["check"]) for item in findings] == [
        ("Public.md", "broken_wikilinks"),
    ]


@pytest.mark.parametrize("scoped", [True, False])
def test_link_check_prunes_runtime_state_and_builds_one_index(tmp_path, monkeypatch, scoped):
    (tmp_path / "Wiki").mkdir()
    (tmp_path / "Wiki/Source.md").write_text("[[Runtime Only]]\n")
    runtime = tmp_path / ".brain/local/results/large-runtime-tree"
    runtime.mkdir(parents=True)
    (runtime / "Runtime Only.md").write_text("[[Missing Runtime Target]]")
    import _portable.links as portable
    real_walk, real_scandir = os.walk, os.scandir
    indexed = _wikilinks._iter_indexed_vault_files
    indexes = []

    def bounded_walk(path):
        for row in real_walk(path):
            assert ".brain" not in Path(row[0]).relative_to(tmp_path).parts, (
                "runtime state must be pruned before descent")
            yield row

    def track_index(root):
        indexes.append(root)
        yield from indexed(root)

    facade = SimpleNamespace(path=os.path, walk=bounded_walk)
    monkeypatch.setattr(_wikilinks, "os", facade)
    monkeypatch.setattr(portable, "os", facade)
    monkeypatch.setattr(_wikilinks, "_iter_indexed_vault_files", track_index)
    result = _check(tmp_path, "Wiki/Source.md" if scoped else None)

    assert result.status == "ok"
    assert result.result.unresolvable_targets == ("Runtime Only",)
    assert {f.file for f in result.result.findings} == {"Wiki/Source.md"}
    assert len(indexes) == 1
    assert os.scandir is real_scandir
    if os.scandir in os.supports_fd:
        descriptor = os.open(tmp_path, os.O_RDONLY)
        try:
            with os.scandir(descriptor) as entries:
                assert any(entry.name == "Wiki" for entry in entries)
        finally:
            os.close(descriptor)


def test_scoped_check_reads_and_analyses_only_its_source(tmp_path, monkeypatch):
    import _common
    import _portable.links as portable
    import _portable.vault_files as vault_files
    root = tmp_path
    for folder in ("Wiki", "Other", "_Assets", "_Config"):
        (root / folder).mkdir()
    (root / "Wiki/Target Title.md").write_text("target")
    (root / "Wiki/Duplicate.md").write_text("first")
    (root / "Other/Duplicate.md").write_text("second")
    (root / "_Assets/picture.png").write_bytes(b"asset")
    (root / "_Config/Config Target.md").write_text("configuration")
    (root / "Other/Unrelated.md").write_text("[[Unrelated Missing]]")
    (root / "Wiki/Source.md").write_text(
        "[[target-title]] [[Duplicate]] [[Missing]] ![[picture.png]] [[Config Target]]\n\n"
        "| Name | Link |\n| --- | --- |\n| Example | [[Target Title|alias]] |\n")
    opened, analysed = [], []
    read = _common.read_exact_file_content
    analyse = _common.check_wikilinks_in_file

    def source_read(path, **kwargs):
        opened.append(Path(path).relative_to(root).as_posix())
        return read(path, **kwargs)

    def source_analysis(root, path, **kwargs):
        analysed.append(path)
        return analyse(root, path, **kwargs)

    monkeypatch.setattr(_common, "read_exact_file_content", source_read)
    monkeypatch.setattr(vault_files, "read_exact_file_content", source_read)
    monkeypatch.setattr(_common, "check_wikilinks_in_file", source_analysis)
    monkeypatch.setattr(portable, "check_wikilinks_in_file", source_analysis)
    result = _check(root, "Wiki/Source.md")

    assert opened == analysed == ["Wiki/Source.md"]
    assert result.status == "ok"
    assert [(p.target, p.resolved_to) for p in result.result.proposals] == [("target-title", "Target Title")]
    assert result.result.ambiguous_targets == ("Duplicate",)
    assert result.result.unresolvable_targets == ("Missing",)
    assert {f.check for f in result.result.findings} == {
        "broken_wikilinks", "ambiguous_wikilinks", "table_wikilink_alias"}


@pytest.mark.parametrize("path, code", [("Wiki/Missing.md", "not_found"), ("Wiki", "invalid_request"),
                                        ("../Outside.md", "invalid_request")])
def test_scoped_check_rejects_unavailable_or_escaping_sources(tmp_path, path, code):
    (tmp_path / "Wiki").mkdir()
    result = _check(tmp_path, path)
    assert result.status == "error"
    assert result.error.code.value == code


def test_scoped_check_rejects_a_symlink_escape(tmp_path):
    root = tmp_path / "Brain"
    root.mkdir()
    outside = tmp_path / "Outside.md"
    outside.write_text("[[Secret]]")
    (root / "Source.md").symlink_to(outside)
    result = _check(root, "Source.md")
    assert result.status == "error"
    assert result.error.code.value == "invalid_request"


@pytest.mark.parametrize("path", [".brain/local/results/Private.md", "Wiki/Alias.md", ".hidden/Private.md"])
def test_scoped_check_never_opens_private_runtime_or_hidden_sources(tmp_path, monkeypatch, path):
    import _portable.vault_files as vault_files
    runtime = tmp_path / ".brain/local/results/Private.md"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("[[Private retained result title]]")
    (tmp_path / "Wiki").mkdir()
    (tmp_path / "Wiki/Alias.md").symlink_to(runtime)
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden/Private.md").write_text("[[Private hidden title]]")

    def forbidden_read(*args, **kwargs):
        pytest.fail("a private source was opened")

    monkeypatch.setattr(vault_files, "read_exact_file_content", forbidden_read)
    result = _check(tmp_path, path)
    assert result.status == "error"
    assert result.error.code.value == "invalid_request"


def test_scoped_check_keeps_the_source_identity_observed_by_the_reader(tmp_path, monkeypatch):
    import _portable.vault_files as vault_files
    (tmp_path / "Wiki").mkdir()
    first, second, alias = (tmp_path / "Wiki" / name for name in ("A.md", "B.md", "Alias.md"))
    first.write_text("[[Missing from A]]")
    second.write_text("[[Missing from B]]")
    alias.symlink_to(first)
    read = vault_files.read_vault_file

    def read_then_switch(root, reference, **kwargs):
        content = read(root, reference, **kwargs)
        alias.unlink()
        alias.symlink_to(second)
        return content

    monkeypatch.setattr(vault_files, "read_vault_file", read_then_switch)
    result = _check(tmp_path, "Wiki/Alias.md")
    assert result.status == "ok"
    assert [(f.file, f.stem) for f in result.result.findings] == [("Wiki/A.md", "Missing from A")]


def test_public_read_opens_the_approved_source_when_the_requested_alias_changes(tmp_path, monkeypatch):
    import _portable.vault_files as vault_files
    (tmp_path / "Wiki").mkdir()
    public = tmp_path / "Wiki/Public.md"
    public.write_text("[[Public missing title]]")
    private = tmp_path / ".brain/local/results/Private.md"
    private.parent.mkdir(parents=True)
    private.write_text("[[Private retained title]]")
    alias = tmp_path / "Wiki/Alias.md"
    alias.symlink_to(public)
    resolve, read = vault_files.resolve_public_source, vault_files.read_exact_file_content
    switched = False
    opened = []

    def resolve_then_switch(root, reference, **kwargs):
        nonlocal switched
        approved = resolve(root, reference, **kwargs)
        assert approved == public, "admission must return the canonical public target"
        if Path(root) / reference == alias and not switched:
            switched = True
            alias.unlink()
            alias.symlink_to(private)
        return approved

    def approved_read(path, **kwargs):
        opened.append(Path(path))
        assert Path(path) == public, "the approved public source must supply the bytes"
        return read(path, **kwargs)

    monkeypatch.setattr(vault_files, "resolve_public_source", resolve_then_switch)
    monkeypatch.setattr(vault_files, "read_exact_file_content", approved_read)
    result = _check(tmp_path, "Wiki/Alias.md")
    assert result.status == "ok"
    assert switched and alias.resolve() == private
    assert opened == [public]
    assert [(f.file, f.stem) for f in result.result.findings] == [("Wiki/Public.md", "Public missing title")]


@pytest.mark.parametrize("scoped", [False, True])
def test_link_check_does_not_open_private_sources_through_public_aliases(tmp_path, monkeypatch, scoped):
    import builtins
    import _portable.links as portable
    import _portable.vault_files as vault_files
    import fix_links
    (tmp_path / "Wiki").mkdir()
    (tmp_path / "Wiki/Public.md").write_text("[[Public missing title]]")
    private = tmp_path / ".brain/local/results/Private.md"
    private.parent.mkdir(parents=True)
    private.write_text("[[Private retained title]]")
    (tmp_path / "Wiki/Alias.md").symlink_to(private)
    open_file, read = builtins.open, vault_files.read_exact_file_content

    def public_open(path, *args, **kwargs):
        assert Path(path).resolve() != private, "a private source was opened"
        return open_file(path, *args, **kwargs)

    def public_read(path, **kwargs):
        assert Path(path).resolve() != private, "a private source was opened"
        return read(path, **kwargs)

    monkeypatch.setattr(portable, "open", public_open, raising=False)
    monkeypatch.setattr(vault_files, "read_exact_file_content", public_read)
    monkeypatch.setattr(fix_links, "read_exact_file_content", public_read)
    result = _check(tmp_path, "Wiki/Alias.md" if scoped else None)
    if scoped:
        assert result.status == "error"
        assert result.error.code.value == "invalid_request"
    else:
        assert result.status == "ok"
        assert result.result.unresolvable_targets == ("Public missing title",)
        assert {(f.file, f.check, f.severity, f.stem) for f in result.result.findings} == {
            ("Wiki/Public.md", "broken_wikilinks", "warning", "Public missing title"),
            ("Wiki/Alias.md", "source_admission", "warning", None),
        }
        excluded = next(f for f in result.result.findings if f.check == "source_admission")
        assert excluded.message == "Source excluded; link-check coverage is incomplete."
        assert result.result.warnings == 2
        assert "Private retained title" not in repr(result.result)


def test_cached_compliance_link_check_rejects_private_alias_before_cache_read(tmp_path, monkeypatch):
    import check
    import _portable.links as portable
    (tmp_path / "Wiki").mkdir()
    (tmp_path / "Wiki/Public.md").write_text("[[Public missing title]]")
    private = tmp_path / ".brain/local/results/Private.md"
    private.parent.mkdir(parents=True)
    private.write_text("[[Private retained title]]")
    (tmp_path / "Wiki/Alias.md").symlink_to(private)
    path_open = Path.open

    def public_open(path, *args, **kwargs):
        assert path.resolve() != private, "a private source was opened before the cache read"
        return path_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", public_open)
    context = check.CheckContext(str(tmp_path), {})
    findings = portable.check_broken_wikilinks(str(tmp_path), {}, ctx=context)
    assert {(f["file"], f["check"], f["severity"], f.get("stem")) for f in findings} == {
        ("Wiki/Public.md", "broken_wikilinks", "warning", "Public missing title"),
        ("Wiki/Alias.md", "source_admission", "warning", None),
    }
    excluded = next(f for f in findings if f["check"] == "source_admission")
    assert excluded["message"] == "Source excluded; link-check coverage is incomplete."
    assert "Private retained title" not in repr(findings)
    assert not any(path.resolve() == private for path in context._text_cache)


@pytest.mark.parametrize("content", [b"A link-free note", b"nonstandard \xff"])
def test_link_free_or_skipped_scoped_source_does_not_build_a_target_index(tmp_path, monkeypatch, content):
    import _common
    (tmp_path / "Source.md").write_bytes(content)

    def no_index(*args, **kwargs):
        pytest.fail("a source with no wikilinks must not walk the target namespace")

    monkeypatch.setattr(_common, "build_vault_file_index", no_index)
    result = _check(tmp_path, "Source.md")
    assert result.status == "ok"
    assert result.result.findings == result.result.proposals == ()
    assert result.result.ambiguous_targets == result.result.unresolvable_targets == ()
