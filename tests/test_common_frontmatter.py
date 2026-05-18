"""Tests for _common._frontmatter — parsing and serialisation."""

import _common as common
from _common import _frontmatter as frontmatter_common


# ---------------------------------------------------------------------------
# parse_frontmatter
# ---------------------------------------------------------------------------

class TestParseFrontmatter:
    def test_basic_fields(self):
        text = "---\ntype: living/wiki\nstatus: active\n---\n\n# Title\n\nBody"
        fields, body = common.parse_frontmatter(text)
        assert fields["type"] == "living/wiki"
        assert fields["status"] == "active"
        assert "# Title" in body

    def test_inline_tags(self):
        text = "---\ntype: x\ntags: [foo, bar]\n---\nBody"
        fields, body = common.parse_frontmatter(text)
        assert fields["tags"] == ["foo", "bar"]

    def test_multiline_tags(self):
        text = "---\ntype: x\ntags:\n  - alpha\n  - beta\n---\nBody"
        fields, body = common.parse_frontmatter(text)
        assert fields["tags"] == ["alpha", "beta"]

    def test_no_frontmatter(self):
        text = "# Just a heading\n\nBody text"
        fields, body = common.parse_frontmatter(text)
        assert fields == {}
        assert body == text

    def test_empty_value_becomes_empty_list(self):
        text = "---\ntype: living/wiki\nempty_field:\nstatus: active\n---\nBody"
        fields, body = common.parse_frontmatter(text)
        assert fields["empty_field"] == []
        assert fields["type"] == "living/wiki"
        assert fields["status"] == "active"

    def test_multiline_aliases(self):
        text = "---\ntype: x\naliases:\n  - brain-master-design\n  - master-design\n---\nBody"
        fields, body = common.parse_frontmatter(text)
        assert fields["aliases"] == ["brain-master-design", "master-design"]

    def test_inline_aliases(self):
        text = "---\ntype: x\naliases: [foo, bar]\n---\nBody"
        fields, body = common.parse_frontmatter(text)
        assert fields["aliases"] == ["foo", "bar"]

    def test_quoted_values(self):
        text = "---\ntitle: 'Hello World'\n---\nBody"
        fields, _ = common.parse_frontmatter(text)
        assert fields["title"] == "Hello World"

    def test_has_leading_frontmatter_detects_document_frontmatter(self):
        text = "---\ntype: living/wiki\n---\n\n# Title\n"
        assert common.has_leading_frontmatter(text) is True

    def test_has_leading_frontmatter_ignores_plain_body(self):
        text = "# Title\n\nBody\n"
        assert common.has_leading_frontmatter(text) is False

    def test_parse_leading_frontmatter_allows_leading_blank_lines_when_requested(self):
        text = "\n\n---\nstatus: active\n---\nBody\n"
        parsed = common.parse_leading_frontmatter(
            text,
            allow_leading_blank_lines=True,
        )
        assert parsed == ({"status": "active"}, "Body\n")

    def test_inspect_duplicate_frontmatter_document_keeps_outer_fields_and_unions_tags(self):
        text = (
            "---\n"
            "type: living/wiki\n"
            "tags:\n"
            "  - wiki\n"
            "aliases:\n"
            "  - canonical\n"
            "status: active\n"
            "---\n\n"
            "---\n"
            "type: living/idea\n"
            "aliases:\n"
            "  - stale\n"
            "status: shaping\n"
            "tags:\n"
            "  - bug\n"
            "  - wiki\n"
            "---\n"
            "# Body\n"
        )
        duplicate = common.inspect_duplicate_frontmatter_document(text)
        assert duplicate is not None
        assert duplicate["outer_fields"] == {
            "type": "living/wiki",
            "tags": ["wiki"],
            "aliases": ["canonical"],
            "status": "active",
        }
        assert duplicate["nested_fields"] == {
            "type": "living/idea",
            "aliases": ["stale"],
            "status": "shaping",
            "tags": ["bug", "wiki"],
        }
        assert duplicate["merged_fields"] == {
            "type": "living/wiki",
            "tags": ["wiki", "bug"],
            "aliases": ["canonical"],
            "status": "active",
        }
        assert common.serialize_frontmatter(duplicate["merged_fields"], body=duplicate["body"]) == (
            "---\n"
            "type: living/wiki\n"
            "tags:\n"
            "  - wiki\n"
            "  - bug\n"
            "aliases:\n"
            "  - canonical\n"
            "status: active\n"
            "---\n"
            "# Body\n"
        )

    def test_merge_duplicate_frontmatter_fields_preserves_outer_modified(self):
        merged = frontmatter_common._merge_duplicate_frontmatter_fields(
            {"type": "living/wiki", "modified": "2026-05-19T10:00:00+10:00"},
            {"modified": "2026-05-01T09:00:00+10:00"},
        )
        assert merged == {
            "type": "living/wiki",
            "modified": "2026-05-19T10:00:00+10:00",
        }

    def test_merge_duplicate_frontmatter_fields_keeps_outer_only_tags(self):
        merged = frontmatter_common._merge_duplicate_frontmatter_fields(
            {"tags": ["outer"]},
            {},
        )
        assert merged == {"tags": ["outer"]}

    def test_merge_duplicate_frontmatter_fields_lifts_nested_only_tags(self):
        merged = frontmatter_common._merge_duplicate_frontmatter_fields(
            {},
            {"tags": ["nested"]},
        )
        assert merged == {"tags": ["nested"]}

    def test_merge_duplicate_frontmatter_fields_does_not_synthesise_empty_tags(self):
        merged = frontmatter_common._merge_duplicate_frontmatter_fields(
            {"type": "living/wiki"},
            {"status": "active"},
        )
        assert merged == {"type": "living/wiki"}


# ---------------------------------------------------------------------------
# serialize_frontmatter
# ---------------------------------------------------------------------------

class TestSerializeFrontmatter:
    def test_basic_fields(self):
        result = common.serialize_frontmatter({"type": "living/wiki", "status": "active"})
        assert result.startswith("---\n")
        assert "type: living/wiki\n" in result
        assert "status: active\n" in result

    def test_tags_list(self):
        result = common.serialize_frontmatter({"tags": ["brain-core", "overview"]})
        assert "tags:\n  - brain-core\n  - overview\n" in result

    def test_empty_tags(self):
        result = common.serialize_frontmatter({"tags": []})
        assert "tags: []\n" in result

    def test_with_body(self):
        result = common.serialize_frontmatter({"type": "x"}, body="# Title\n\nBody text")
        assert result.endswith("# Title\n\nBody text")

    def test_empty_body(self):
        result = common.serialize_frontmatter({"type": "x"})
        assert result.endswith("---\n")

    def test_roundtrip_with_parse(self):
        original_fields = {"type": "living/wiki", "status": "active"}
        original_body = "# Title\n\nBody content.\n"
        serialized = common.serialize_frontmatter(original_fields, body=original_body)
        parsed_fields, parsed_body = common.parse_frontmatter(serialized)
        assert parsed_fields["type"] == "living/wiki"
        assert parsed_fields["status"] == "active"
        assert "# Title" in parsed_body
        assert "Body content." in parsed_body

    def test_roundtrip_tags(self):
        original_fields = {"type": "x", "tags": ["alpha", "beta"]}
        serialized = common.serialize_frontmatter(original_fields)
        parsed_fields, _ = common.parse_frontmatter(serialized)
        assert parsed_fields["tags"] == ["alpha", "beta"]

    def test_aliases_list(self):
        result = common.serialize_frontmatter({"aliases": ["brain-master", "master"]})
        assert "aliases:\n  - brain-master\n  - master\n" in result

    def test_roundtrip_aliases(self):
        original_fields = {"type": "x", "aliases": ["brain-master-design"]}
        serialized = common.serialize_frontmatter(original_fields)
        parsed_fields, _ = common.parse_frontmatter(serialized)
        assert parsed_fields["aliases"] == ["brain-master-design"]

    def test_roundtrip_multiple_list_fields(self):
        original_fields = {"type": "x", "tags": ["a", "b"], "aliases": ["c"], "cssclasses": ["d"]}
        serialized = common.serialize_frontmatter(original_fields)
        parsed_fields, _ = common.parse_frontmatter(serialized)
        assert parsed_fields["tags"] == ["a", "b"]
        assert parsed_fields["aliases"] == ["c"]
        assert parsed_fields["cssclasses"] == ["d"]


# ---------------------------------------------------------------------------
# read_frontmatter
# ---------------------------------------------------------------------------

class TestReadFrontmatter:
    def test_basic_fields(self, tmp_path):
        f = tmp_path / "note.md"
        f.write_text("---\ntype: living/wiki\nstatus: active\n---\n\n# Title\n\nBody\n")
        fields = common.read_frontmatter(str(f))
        assert fields == {"type": "living/wiki", "status": "active"}

    def test_multiline_tags(self, tmp_path):
        f = tmp_path / "note.md"
        f.write_text("---\ntype: x\ntags:\n  - alpha\n  - beta\n---\nBody\n")
        fields = common.read_frontmatter(str(f))
        assert fields["tags"] == ["alpha", "beta"]

    def test_inline_tags(self, tmp_path):
        f = tmp_path / "note.md"
        f.write_text("---\ntype: x\ntags: [foo, bar]\n---\nBody\n")
        fields = common.read_frontmatter(str(f))
        assert fields["tags"] == ["foo", "bar"]

    def test_no_frontmatter_returns_empty(self, tmp_path):
        f = tmp_path / "note.md"
        f.write_text("# Just a heading\n\nBody\n")
        assert common.read_frontmatter(str(f)) == {}

    def test_empty_file_returns_empty(self, tmp_path):
        f = tmp_path / "empty.md"
        f.write_text("")
        assert common.read_frontmatter(str(f)) == {}

    def test_unclosed_frontmatter_returns_empty(self, tmp_path):
        f = tmp_path / "bad.md"
        f.write_text("---\ntype: x\nstatus: active\nno closing delim\n")
        assert common.read_frontmatter(str(f)) == {}

    def test_agrees_with_parse_frontmatter(self, tmp_path):
        body = "# Title\n\nLorem ipsum dolor sit amet.\n\n## Section\n\nMore text.\n"
        fields_in = {
            "type": "living/wiki",
            "key": "example",
            "tags": ["alpha", "beta"],
            "aliases": ["first-name", "second-name"],
            "status": "active",
        }
        text = common.serialize_frontmatter(fields_in, body=body)
        f = tmp_path / "note.md"
        f.write_text(text)

        parsed, _ = common.parse_frontmatter(text)
        streamed = common.read_frontmatter(str(f))
        assert parsed == streamed

    def test_missing_file_raises(self, tmp_path):
        import pytest
        with pytest.raises(OSError):
            common.read_frontmatter(str(tmp_path / "does-not-exist.md"))

    def test_does_not_read_body(self, tmp_path):
        """The body after the closing delim must not affect parsing."""
        f = tmp_path / "note.md"
        f.write_text(
            "---\ntype: living/wiki\n---\n"
            "---\nthis: looks-like-frontmatter\nbut: is-body\n---\n"
            "More body.\n"
        )
        fields = common.read_frontmatter(str(f))
        assert fields == {"type": "living/wiki"}


# ---------------------------------------------------------------------------
# read_artefact
# ---------------------------------------------------------------------------

class TestReadArtefact:
    def test_basic_fields_and_body(self, tmp_path):
        f = tmp_path / "note.md"
        text = "---\ntype: living/wiki\nstatus: active\n---\n\n# Title\n\nBody\n"
        f.write_text(text)
        fields, body = common.read_artefact(str(f))
        expected_fields, expected_body = common.parse_frontmatter(text)
        assert fields == expected_fields
        assert body == expected_body

    def test_no_frontmatter_returns_full_text(self, tmp_path):
        f = tmp_path / "note.md"
        f.write_text("# Just a heading\n\nBody\n")
        fields, body = common.read_artefact(str(f))
        assert fields == {}
        assert body == "# Just a heading\n\nBody\n"

    def test_missing_file_raises(self, tmp_path):
        import pytest
        with pytest.raises(OSError):
            common.read_artefact(str(tmp_path / "does-not-exist.md"))

    def test_roundtrips_through_serialize(self, tmp_path):
        body_in = "# Title\n\nLorem ipsum.\n"
        fields_in = {"type": "living/wiki", "key": "example"}
        text = common.serialize_frontmatter(fields_in, body=body_in)
        f = tmp_path / "note.md"
        f.write_text(text)
        fields, body = common.read_artefact(str(f))
        assert fields == fields_in
        assert body == body_in
