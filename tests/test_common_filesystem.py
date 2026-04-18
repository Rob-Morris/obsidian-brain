"""Tests for _common._filesystem — safe writes, bounds checking, body file resolution."""
import os
import tempfile
import threading

import pytest

import _common as common


# ---------------------------------------------------------------------------
# resolve_and_check_bounds
# ---------------------------------------------------------------------------

class TestResolveAndCheckBounds:
    def test_path_within_bounds(self, tmp_path):
        target = tmp_path / "sub" / "file.txt"
        result = common.resolve_and_check_bounds(target, tmp_path)
        assert result == str(target)

    def test_path_at_bounds_root(self, tmp_path):
        target = tmp_path / "file.txt"
        result = common.resolve_and_check_bounds(target, tmp_path)
        assert result == str(target)

    def test_path_outside_bounds(self, tmp_path):
        target = tmp_path / ".." / "escape.txt"
        with pytest.raises(ValueError, match="outside allowed boundary"):
            common.resolve_and_check_bounds(target, tmp_path)

    def test_symlink_resolved_within_bounds(self, tmp_path):
        real = tmp_path / "real.txt"
        real.write_text("x")
        link = tmp_path / "link.txt"
        link.symlink_to(real)
        result = common.resolve_and_check_bounds(link, tmp_path)
        assert result == str(real)

    def test_symlink_resolved_outside_bounds(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        real = outside / "real.txt"
        real.write_text("x")
        bounded = tmp_path / "bounded"
        bounded.mkdir()
        link = bounded / "link.txt"
        link.symlink_to(real)
        with pytest.raises(ValueError, match="outside allowed boundary"):
            common.resolve_and_check_bounds(link, bounded)

    def test_follow_symlinks_false_rejects_symlink(self, tmp_path):
        real = tmp_path / "real.txt"
        real.write_text("x")
        link = tmp_path / "link.txt"
        link.symlink_to(real)
        with pytest.raises(ValueError, match="Refusing to follow symlink"):
            common.resolve_and_check_bounds(link, tmp_path, follow_symlinks=False)

    def test_follow_symlinks_false_allows_regular_file(self, tmp_path):
        target = tmp_path / "file.txt"
        result = common.resolve_and_check_bounds(target, tmp_path, follow_symlinks=False)
        assert result == str(target)

    def test_prefix_collision(self, tmp_path):
        """'/home/foo' must not match bounds '/home/fo'."""
        bounds = tmp_path / "fo"
        bounds.mkdir()
        target = tmp_path / "foobar" / "file.txt"
        with pytest.raises(ValueError, match="outside allowed boundary"):
            common.resolve_and_check_bounds(target, bounds)


# ---------------------------------------------------------------------------
# safe_write
# ---------------------------------------------------------------------------

class TestSafeWrite:
    def test_basic_write_new_file(self, tmp_path):
        target = tmp_path / "out.txt"
        result = common.safe_write(target, "hello")
        assert result == str(target)
        assert target.read_text() == "hello"

    def test_overwrite_existing(self, tmp_path):
        target = tmp_path / "out.txt"
        target.write_text("old")
        common.safe_write(target, "new")
        assert target.read_text() == "new"

    def test_creates_parent_directories(self, tmp_path):
        target = tmp_path / "a" / "b" / "c" / "file.txt"
        common.safe_write(target, "deep")
        assert target.read_text() == "deep"

    def test_exclusive_fails_if_exists(self, tmp_path):
        target = tmp_path / "out.txt"
        target.write_text("existing")
        with pytest.raises(FileExistsError):
            common.safe_write(target, "new", exclusive=True)
        assert target.read_text() == "existing"

    def test_exclusive_succeeds_if_new(self, tmp_path):
        target = tmp_path / "out.txt"
        common.safe_write(target, "new", exclusive=True)
        assert target.read_text() == "new"

    def test_symlink_followed(self, tmp_path):
        real = tmp_path / "real.txt"
        real.write_text("old")
        link = tmp_path / "link.txt"
        link.symlink_to(real)
        common.safe_write(link, "updated")
        assert real.read_text() == "updated"

    def test_symlink_with_bounds_inside(self, tmp_path):
        real = tmp_path / "real.txt"
        real.write_text("old")
        link = tmp_path / "link.txt"
        link.symlink_to(real)
        common.safe_write(link, "updated", bounds=tmp_path)
        assert real.read_text() == "updated"

    def test_symlink_with_bounds_outside(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        real = outside / "real.txt"
        real.write_text("old")
        bounded = tmp_path / "bounded"
        bounded.mkdir()
        link = bounded / "link.txt"
        link.symlink_to(real)
        with pytest.raises(ValueError, match="outside allowed boundary"):
            common.safe_write(link, "new", bounds=bounded)
        assert real.read_text() == "old"

    def test_follow_symlinks_false_rejects(self, tmp_path):
        real = tmp_path / "real.txt"
        real.write_text("old")
        link = tmp_path / "link.txt"
        link.symlink_to(real)
        with pytest.raises(ValueError, match="Refusing to follow symlink"):
            common.safe_write(link, "new", follow_symlinks=False)

    def test_no_stale_tmp_on_failure(self, tmp_path, monkeypatch):
        target = tmp_path / "out.txt"

        def failing_replace(src, dst):
            raise OSError("simulated failure")

        monkeypatch.setattr("os.replace", failing_replace)
        with pytest.raises(OSError, match="simulated failure"):
            common.safe_write(target, "content")
        remaining = list(tmp_path.iterdir())
        assert not any(str(f).endswith(".tmp") for f in remaining)

    def test_concurrent_same_target_uses_unique_tempfiles(self, tmp_path, monkeypatch):
        target = tmp_path / "out.txt"
        barrier = threading.Barrier(2)
        real_fsync = os.fsync
        errors = []

        def synced_fsync(fd):
            barrier.wait(timeout=2)
            return real_fsync(fd)

        monkeypatch.setattr("_common._filesystem.os.fsync", synced_fsync)

        def writer(content):
            try:
                common.safe_write(target, content)
            except Exception as e:  # pragma: no cover - asserted below
                errors.append(e)

        t1 = threading.Thread(target=writer, args=("one",))
        t2 = threading.Thread(target=writer, args=("two",))
        t1.start()
        t2.start()
        t1.join(timeout=2)
        t2.join(timeout=2)

        assert not errors
        assert target.read_text() in {"one", "two"}
        assert not any(p.suffix == ".tmp" for p in tmp_path.iterdir())


# ---------------------------------------------------------------------------
# safe_write_json
# ---------------------------------------------------------------------------

class TestSafeWriteJson:
    def test_writes_valid_json(self, tmp_path):
        import json
        target = tmp_path / "data.json"
        common.safe_write_json(target, {"key": "value"})
        content = target.read_text()
        assert content.endswith("\n")
        assert json.loads(content) == {"key": "value"}

    def test_custom_indent(self, tmp_path):
        target = tmp_path / "data.json"
        common.safe_write_json(target, {"a": 1}, indent=4)
        assert '    "a": 1' in target.read_text()

    def test_with_bounds(self, tmp_path):
        target = tmp_path / "data.json"
        common.safe_write_json(target, {"a": 1}, bounds=tmp_path)
        assert target.exists()

    def test_unicode_preserved(self, tmp_path):
        target = tmp_path / "data.json"
        common.safe_write_json(target, {"emoji": "\U0001f600"})
        assert "\U0001f600" in target.read_text()


# ---------------------------------------------------------------------------
# safe_write_via
# ---------------------------------------------------------------------------

class TestSafeWriteVia:
    def test_writes_binary_via_callback(self, tmp_path):
        target = tmp_path / "data.bin"
        result = common.safe_write_via(
            target,
            lambda handle: handle.write(b"abc123"),
        )
        assert result == str(target)
        assert target.read_bytes() == b"abc123"

    def test_cleans_up_tmp_on_callback_failure(self, tmp_path):
        target = tmp_path / "data.bin"

        def failing_writer(handle):
            handle.write(b"partial")
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            common.safe_write_via(target, failing_writer)
        assert not any(p.suffix == ".tmp" for p in tmp_path.iterdir())

    def test_rejects_out_of_bounds_target(self, tmp_path):
        target = tmp_path / ".." / "escape.bin"
        with pytest.raises(ValueError, match="outside allowed boundary"):
            common.safe_write_via(target, lambda handle: handle.write(b"x"), bounds=tmp_path)

    def test_text_mode_supports_text_serializers(self, tmp_path):
        target = tmp_path / "data.txt"
        common.safe_write_via(
            target,
            lambda handle: handle.write("hello via callback"),
            mode="w",
        )
        assert target.read_text() == "hello via callback"


# ---------------------------------------------------------------------------
# check_write_allowed
# ---------------------------------------------------------------------------

class TestCheckWriteAllowed:
    """Write guard: block dot-prefixed and protected underscore folders."""

    # -- Dot-prefixed: always blocked --

    def test_dot_brain_blocked(self):
        with pytest.raises(ValueError, match="dot-prefixed"):
            common.check_write_allowed(".brain/local/index.json")

    def test_dot_brain_core_blocked(self):
        with pytest.raises(ValueError, match="dot-prefixed"):
            common.check_write_allowed(".brain-core/scripts/foo.py")

    def test_dot_obsidian_blocked(self):
        with pytest.raises(ValueError, match="dot-prefixed"):
            common.check_write_allowed(".obsidian/config")

    # -- Underscore-prefixed: blocked unless in allowlist --

    def test_plugins_blocked(self):
        with pytest.raises(ValueError, match="protected folder"):
            common.check_write_allowed("_Plugins/my-plugin/SKILL.md")

    def test_workspaces_blocked(self):
        with pytest.raises(ValueError, match="protected folder"):
            common.check_write_allowed("_Workspaces/ws1/config.md")

    def test_assets_blocked(self):
        with pytest.raises(ValueError, match="protected folder"):
            common.check_write_allowed("_Assets/image.png")

    def test_archive_blocked(self):
        with pytest.raises(ValueError, match="protected folder"):
            common.check_write_allowed("_Archive/old-doc.md")

    # -- Underscore-prefixed: allowed exceptions --

    def test_temporal_allowed(self):
        common.check_write_allowed("_Temporal/Research/2026-04/foo.md")

    def test_config_allowed(self):
        common.check_write_allowed("_Config/Skills/my-skill/SKILL.md")

    # -- Normal folders: allowed --

    def test_ideas_allowed(self):
        common.check_write_allowed("Ideas/my-idea.md")

    def test_wiki_allowed(self):
        common.check_write_allowed("Wiki/my-page.md")

    def test_daily_notes_allowed(self):
        common.check_write_allowed("Daily Notes/2026-04-06 Mon.md")

    # -- Edge cases --

    def test_bare_filename_allowed(self):
        common.check_write_allowed("README.md")

    def test_empty_path_raises(self):
        with pytest.raises(ValueError, match="Empty path"):
            common.check_write_allowed("")


# ---------------------------------------------------------------------------
# resolve_body_file — path boundary checks
# ---------------------------------------------------------------------------

class TestResolveBodyFile:
    """Tests for resolve_body_file with vault_root boundary enforcement."""

    def test_body_only(self):
        body, cleanup = common.resolve_body_file("hello", "")
        assert body == "hello"
        assert cleanup is None

    def test_both_raises(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("x")
        with pytest.raises(ValueError, match="Cannot specify both"):
            common.resolve_body_file("body", str(f))

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ValueError, match="not found"):
            common.resolve_body_file("", str(tmp_path / "nope.txt"), vault_root=str(tmp_path))

    def test_vault_file_ok_no_cleanup(self, non_tmp_vault):
        f = os.path.join(non_tmp_vault, "Wiki", "source.md")
        with open(f, "w") as fh:
            fh.write("vault content")
        body, cleanup = common.resolve_body_file("", f, vault_root=non_tmp_vault)
        assert body == "vault content"
        assert cleanup is None
        assert os.path.exists(f), "vault file must not be deleted"

    def test_tmp_file_returns_cleanup_path(self, non_tmp_vault):
        tmp_dir = tempfile.gettempdir()
        tmp_file = os.path.join(tmp_dir, "brain-test-body.txt")
        try:
            with open(tmp_file, "w") as fh:
                fh.write("tmp content")
            body, cleanup = common.resolve_body_file("", tmp_file, vault_root=non_tmp_vault)
            assert body == "tmp content"
            assert cleanup == os.path.realpath(tmp_file)
        finally:
            if os.path.exists(tmp_file):
                os.remove(tmp_file)

    def test_outside_vault_and_tmp_raises(self, non_tmp_vault):
        outside = os.path.join(non_tmp_vault, "..", "outside-secret.txt")
        with open(outside, "w") as fh:
            fh.write("secret")
        try:
            with pytest.raises(ValueError, match="outside allowed boundary"):
                common.resolve_body_file("", outside, vault_root=non_tmp_vault)
        finally:
            os.remove(outside)

    def test_symlink_escape_raises(self, non_tmp_vault):
        outside = os.path.join(non_tmp_vault, "..", "symlink-target.txt")
        with open(outside, "w") as fh:
            fh.write("secret")
        link = os.path.join(non_tmp_vault, "Wiki", "escape.md")
        try:
            os.symlink(outside, link)
            with pytest.raises(ValueError, match="outside allowed boundary"):
                common.resolve_body_file("", link, vault_root=non_tmp_vault)
        finally:
            os.unlink(link)
            os.remove(outside)

    def test_no_vault_root_allows_any_path(self, tmp_path):
        """Without vault_root (CLI mode), any readable path works."""
        f = tmp_path / "anywhere.txt"
        f.write_text("anything")
        body, cleanup = common.resolve_body_file("", str(f))
        assert body == "anything"
        assert cleanup is None


# ---------------------------------------------------------------------------
# make_temp_path
# ---------------------------------------------------------------------------

class TestMakeTempPath:
    def test_returns_writable_path(self):
        path = common.make_temp_path()
        try:
            assert os.path.exists(path)
            with open(path, "w") as f:
                f.write("test")
            with open(path) as f:
                assert f.read() == "test"
        finally:
            os.remove(path)

    def test_default_suffix_is_md(self):
        path = common.make_temp_path()
        try:
            assert path.endswith(".md")
        finally:
            os.remove(path)

    def test_custom_suffix(self):
        path = common.make_temp_path(suffix=".txt")
        try:
            assert path.endswith(".txt")
        finally:
            os.remove(path)

    def test_path_inside_system_temp_dir(self):
        path = common.make_temp_path()
        try:
            real_path = os.path.realpath(path)
            real_tmp = os.path.realpath(tempfile.gettempdir())
            assert real_path.startswith(real_tmp)
        finally:
            os.remove(path)

    def test_resolve_body_file_accepts_make_temp_path(self, non_tmp_vault):
        """make_temp_path output is accepted by resolve_body_file."""
        path = common.make_temp_path()
        try:
            with open(path, "w") as f:
                f.write("staged content")
            body, cleanup = common.resolve_body_file("", path, vault_root=non_tmp_vault)
            assert body == "staged content"
            assert cleanup == os.path.realpath(path)
        finally:
            if os.path.exists(path):
                os.remove(path)
