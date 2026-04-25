from _common._artefacts import replace_artefact_key_references


class TestReplaceArtefactKeyReferences:
    def test_removes_parent_and_tag_when_new_key_is_none(self):
        fields = {
            "parent": "project/brain",
            "tags": ["brain-core", "project/brain", "wiki/reference"],
        }

        changed = replace_artefact_key_references(
            fields, "project/brain", None
        )

        assert changed is True
        assert "parent" not in fields
        assert fields["tags"] == ["brain-core", "wiki/reference"]

    def test_rewrites_parent_and_tag_when_new_key_is_present(self):
        fields = {
            "parent": "project/brain",
            "tags": ["project/brain", "wiki/reference"],
        }

        changed = replace_artefact_key_references(
            fields, "project/brain", "project/brain-two"
        )

        assert changed is True
        assert fields["parent"] == "project/brain-two"
        assert fields["tags"] == ["project/brain-two", "wiki/reference"]
