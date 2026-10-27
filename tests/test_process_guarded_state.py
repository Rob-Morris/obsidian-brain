"""Supplied derived data cannot bypass the current content-admission generation."""

import pytest

import process


@pytest.mark.parametrize("kind", ["types", "documents"])
def test_obsolete_supplied_vectors_are_not_encoded_or_ranked(tmp_path, monkeypatch, kind):
    def unexpected(*args, **kwargs):
        pytest.fail("obsolete vectors reached the semantic query")

    monkeypatch.setattr(process._semantic, "encode_query", unexpected)
    monkeypatch.setattr(process._semantic, "rank_against", unexpected)
    old = {"metadata_version": 2, kind: []}
    if kind == "types":
        assert process._classify_embedding(tmp_path, "safe", object(), old) is None
    else:
        assert process._embedding_search(tmp_path, "safe", None, object(), old) == []


def test_obsolete_supplied_lexical_corpus_does_not_influence_classification(tmp_path, monkeypatch):
    monkeypatch.setattr(process, "extract_type_description", lambda *args: pytest.fail("old corpus used"))
    old = {"meta": {"index_version": "1.2.0"}, "corpus_stats": {"total_docs": 10}}
    assert process._classify_bm25({}, tmp_path, "safe", old) is None
