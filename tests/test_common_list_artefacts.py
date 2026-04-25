"""Tests for list_artefacts type filtering (uses match_artefact from _common)."""

import list_artefacts as la


# ---------------------------------------------------------------------------
# TestListArtefactsTypeFilter
# ---------------------------------------------------------------------------

class TestListArtefactsTypeFilter:
    """Verify list_artefacts matches index docs using frontmatter_type."""

    def _make_index(self):
        return {
            "documents": [
                {"path": "Ideas/project~brain/foo.md", "type": "living/idea", "tags": ["project/brain"], "parent": "project/brain", "status": "new", "modified": "2026-04-01", "title": "foo", "key": "foo"},
                {"path": "Ideas/bar.md", "type": "living/idea", "tags": [], "status": "new", "modified": "2026-04-02", "title": "bar", "key": "bar"},
                {"path": "Wiki/baz.md", "type": "living/wiki", "tags": [], "status": None, "modified": "2026-04-03", "title": "baz", "key": "baz"},
                {"path": "_Temporal/Reports/2026-04/20260404-report~audit.md", "type": "temporal/report", "tags": ["project/brain"], "parent": "project/brain", "status": None, "modified": "2026-04-04", "title": "20260404-report~audit", "key": None},
            ],
        }

    def _make_router(self):
        return {
            "artefacts": [
                {"key": "ideas", "type": "living/ideas", "frontmatter_type": "living/idea"},
                {"key": "wiki", "type": "living/wiki", "frontmatter_type": "living/wiki"},
                {"key": "projects", "type": "living/projects", "frontmatter_type": "living/project"},
            ],
            "artefact_index": {
                "project/brain": {
                    "path": "Projects/Brain.md",
                    "type": "living/project",
                    "type_key": "projects",
                    "type_prefix": "project",
                    "key": "brain",
                    "parent": None,
                    "children_count": 1,
                },
                "idea/foo": {
                    "path": "Ideas/project~brain/foo.md",
                    "type": "living/idea",
                    "type_key": "ideas",
                    "type_prefix": "idea",
                    "key": "foo",
                    "parent": "project/brain",
                    "children_count": 0,
                },
                "idea/bar": {
                    "path": "Ideas/bar.md",
                    "type": "living/idea",
                    "type_key": "ideas",
                    "type_prefix": "idea",
                    "key": "bar",
                    "parent": None,
                    "children_count": 0,
                },
                "wiki/baz": {
                    "path": "Wiki/baz.md",
                    "type": "living/wiki",
                    "type_key": "wiki",
                    "type_prefix": "wiki",
                    "key": "baz",
                    "parent": None,
                    "children_count": 0,
                },
            },
        }

    def test_filter_by_plural_key(self):
        results = la.list_artefacts(self._make_index(), self._make_router(), type_filter="ideas")
        assert len(results) == 2
        assert all(r["type"] == "living/idea" for r in results)

    def test_filter_by_singular(self):
        results = la.list_artefacts(self._make_index(), self._make_router(), type_filter="idea")
        assert len(results) == 2

    def test_filter_by_full_singular(self):
        results = la.list_artefacts(self._make_index(), self._make_router(), type_filter="living/idea")
        assert len(results) == 2

    def test_filter_by_full_plural(self):
        results = la.list_artefacts(self._make_index(), self._make_router(), type_filter="living/ideas")
        assert len(results) == 2

    def test_no_filter_returns_all(self):
        results = la.list_artefacts(self._make_index(), self._make_router(), type_filter=None)
        assert len(results) == 4

    def test_filter_by_parent_key(self):
        results = la.list_artefacts(
            self._make_index(), self._make_router(), parent="project/brain"
        )
        assert [r["path"] for r in results] == [
            "_Temporal/Reports/2026-04/20260404-report~audit.md",
            "Ideas/project~brain/foo.md",
        ]
        assert all(r["parent"] == "project/brain" for r in results)
        living = next(r for r in results if r["path"] == "Ideas/project~brain/foo.md")
        temporal = next(
            r
            for r in results
            if r["path"] == "_Temporal/Reports/2026-04/20260404-report~audit.md"
        )
        assert living["key"] == "foo"
        assert "children_count" not in temporal

    def test_parent_filter_rejects_unknown_key(self):
        try:
            la.list_artefacts(
                self._make_index(), self._make_router(), parent="project/missing"
            )
        except ValueError as exc:
            assert "No artefact matching parent" in str(exc)
        else:
            raise AssertionError("Expected ValueError for unknown parent")
