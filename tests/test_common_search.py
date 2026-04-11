"""Tests for _common._search — BM25 tokenisation."""

import _common as common


# ---------------------------------------------------------------------------
# tokenise
# ---------------------------------------------------------------------------

class TestTokenise:
    def test_basic_tokenisation(self):
        tokens = common.tokenise("Hello World 123")
        assert "hello" in tokens
        assert "world" in tokens
        assert "123" in tokens

    def test_strips_short_tokens(self):
        tokens = common.tokenise("I am a great coder")
        assert "i" not in tokens
        assert "am" in tokens
        assert "a" not in tokens
        assert "great" in tokens

    def test_splits_on_non_alphanumeric(self):
        tokens = common.tokenise("foo-bar_baz.qux")
        assert "foo" in tokens
        assert "bar" in tokens
        assert "baz" in tokens
        assert "qux" in tokens

    def test_empty_string(self):
        assert common.tokenise("") == []
