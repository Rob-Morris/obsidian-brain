"""Tests for ``replace_wikilinks_in_text`` and ``extract_wikilinks`` literals modes.

Both operate on an in-memory string and honour the same skip-range contract —
wikilinks inside fenced code, inline code, HTML comments, ``$$`` math, and
raw HTML blocks are treated as literal text, not live links.
"""

import re

from _common import (
    build_wikilink_pattern,
    extract_wikilinks,
    replace_wikilinks_in_text,
)


def _rewriter(old_to_new):
    pattern = build_wikilink_pattern(*old_to_new.keys())

    def _replace(m):
        return (
            f"{m.group('prefix')}{old_to_new[m.group('stem')]}"
            f"{m.group('anchor') or ''}{m.group('alias') or ''}]]"
        )
    return pattern, _replace


# ---------------------------------------------------------------------------
# replace_wikilinks_in_text
# ---------------------------------------------------------------------------


class TestReplaceWikilinksInText:
    def test_noop_when_no_matches(self):
        pattern, replacer = _rewriter({"old": "new"})
        new_text, count = replace_wikilinks_in_text("plain body\n", pattern, replacer)
        assert new_text == "plain body\n"
        assert count == 0

    def test_single_live_match_rewritten(self):
        pattern, replacer = _rewriter({"old": "new"})
        new_text, count = replace_wikilinks_in_text("See [[old]].", pattern, replacer)
        assert new_text == "See [[new]]."
        assert count == 1

    def test_inline_code_preserved(self):
        pattern, replacer = _rewriter({"old": "new"})
        text = "Use `[[old]]` in docs."
        new_text, count = replace_wikilinks_in_text(text, pattern, replacer)
        assert new_text == text
        assert count == 0

    def test_fenced_block_preserved(self):
        pattern, replacer = _rewriter({"old": "new"})
        text = "intro\n```\n[[old]]\n```\ntail"
        new_text, count = replace_wikilinks_in_text(text, pattern, replacer)
        assert new_text == text
        assert count == 0

    def test_html_comment_preserved(self):
        pattern, replacer = _rewriter({"old": "new"})
        text = "before <!-- [[old]] --> after"
        new_text, count = replace_wikilinks_in_text(text, pattern, replacer)
        assert new_text == text
        assert count == 0

    def test_math_block_preserved(self):
        pattern, replacer = _rewriter({"old": "new"})
        text = "math:\n$$\nf = [[old]]\n$$\nend"
        new_text, count = replace_wikilinks_in_text(text, pattern, replacer)
        assert new_text == text
        assert count == 0

    def test_raw_html_block_preserved(self):
        pattern, replacer = _rewriter({"old": "new"})
        text = "<pre>\n[[old]]\n</pre>"
        new_text, count = replace_wikilinks_in_text(text, pattern, replacer)
        assert new_text == text
        assert count == 0

    def test_frontmatter_is_rewritten(self):
        """YAML property wikilinks are real links and get rewritten (D10)."""
        pattern, replacer = _rewriter({"old": "new"})
        text = '---\nparent: "[[old]]"\n---\n# body\n'
        new_text, count = replace_wikilinks_in_text(text, pattern, replacer)
        assert '"[[new]]"' in new_text
        assert count == 1

    def test_mixed_live_and_literal(self):
        pattern, replacer = _rewriter({"old": "new"})
        text = "Live: [[old]]. Doc: `[[old]]`."
        new_text, count = replace_wikilinks_in_text(text, pattern, replacer)
        assert new_text == "Live: [[new]]. Doc: `[[old]]`."
        assert count == 1

    def test_string_replacement_with_backrefs(self):
        """Plain-string replacement supports re.sub-style backreferences."""
        pattern = re.compile(r"\[\[([^\]]+)\]\]")
        new_text, count = replace_wikilinks_in_text(
            "See [[foo]] and `[[bar]]`.", pattern, r"LINK(\1)"
        )
        assert new_text == "See LINK(foo) and `[[bar]]`."
        assert count == 1


# ---------------------------------------------------------------------------
# extract_wikilinks(literals=...)
# ---------------------------------------------------------------------------


class TestExtractWikilinksLiteralsModes:
    TEXT = (
        "Live: [[live-one]]. Code: `[[inline-masked]]`. "
        "Fence:\n```\n[[fenced-masked]]\n```\n"
        "Comment: <!-- [[comment-masked]] -->. "
        "Math:\n$$\n[[math-masked]]\n$$\n"
        "Raw: <pre>[[pre-masked]]</pre>"
    )

    def _stems(self, text, **kw):
        return sorted(link["stem"] for link in extract_wikilinks(text, **kw))

    def test_default_excludes_literals(self):
        stems = self._stems(self.TEXT)
        assert stems == ["live-one"]

    def test_include_returns_all(self):
        stems = self._stems(self.TEXT, literals="include")
        assert stems == [
            "comment-masked",
            "fenced-masked",
            "inline-masked",
            "live-one",
            "math-masked",
            "pre-masked",
        ]

    def test_only_returns_literals(self):
        stems = self._stems(self.TEXT, literals="only")
        assert stems == [
            "comment-masked",
            "fenced-masked",
            "inline-masked",
            "math-masked",
            "pre-masked",
        ]

    def test_frontmatter_is_live_by_default(self):
        """FM wikilinks are not in literal regions — default mode returns them."""
        text = '---\nparent: "[[fm-link]]"\n---\n# body\n'
        stems = self._stems(text)
        assert stems == ["fm-link"]

    def test_invalid_mode_raises(self):
        import pytest
        with pytest.raises(ValueError, match="literals must be"):
            extract_wikilinks("hello", literals="bogus")
