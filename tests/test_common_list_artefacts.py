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
                {"path": "Ideas/foo.md", "type": "living/idea", "tags": [], "status": "new", "modified": "2026-04-01", "title": "foo"},
                {"path": "Ideas/bar.md", "type": "living/idea", "tags": [], "status": "new", "modified": "2026-04-02", "title": "bar"},
                {"path": "Wiki/baz.md", "type": "living/wiki", "tags": [], "status": None, "modified": "2026-04-03", "title": "baz"},
            ],
        }

    def _make_router(self):
        return {
            "artefacts": [
                {"key": "ideas", "type": "living/ideas", "frontmatter_type": "living/idea"},
                {"key": "wiki", "type": "living/wiki", "frontmatter_type": "living/wiki"},
            ],
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
        assert len(results) == 3
