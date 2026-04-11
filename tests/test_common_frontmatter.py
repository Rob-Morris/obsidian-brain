"""Tests for _common._frontmatter — parsing and serialisation."""

import _common as common


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
