"""Tests for check.py — router-driven vault compliance checker."""

import json
import os
import sys
import time

import pytest

import check
import compile_router as cr
import _lifecycle.semantic_repairs as semantic_repairs
import _search.index as search_index
import _search.paths as search_paths

from brain_test_support import make_router, write_md
from brain_test_support import filesystem_is_case_sensitive


class TestCheckBrokenWikilinks:
    def test_valid_wikilinks_no_findings(self, vault):
        tmp_path, router = vault
        # rust-lifetimes.md already exists in the vault fixture
        write_md(tmp_path / "Wiki" / "linking-page.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[rust-lifetimes]] for details.")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"]
        assert broken == []

    def test_broken_link_detected(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "has-broken-link.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[nonexistent-page]] for details.")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"]
        assert len(broken) >= 1
        assert any("nonexistent-page" in f["message"] for f in broken)

    def test_anchor_only_skipped(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "self-ref.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[#heading]] above.")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"
                  and "self-ref.md" in f.get("file", "")]
        assert broken == []

    def test_link_with_anchor_resolves(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "linking-anchor.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[rust-lifetimes#section]] for details.")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"
                  and "rust-lifetimes" in f["message"]]
        assert broken == []

    def test_link_with_alias_resolves(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "linking-alias.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[rust-lifetimes|Rust Ownership]] for details.")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"
                  and "rust-lifetimes" in f["message"]]
        assert broken == []

    def test_embed_resolves(self, vault):
        tmp_path, router = vault
        assets = tmp_path / "_Assets"
        assets.mkdir(exist_ok=True)
        (assets / "photo.png").write_bytes(b"\x89PNG")
        write_md(tmp_path / "Wiki" / "with-image.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "Image: ![[photo.png]]")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"
                  and "photo.png" in f["message"]]
        assert broken == []

    def test_embed_broken(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "missing-image.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "Image: ![[missing.png]]")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"
                  and "missing.png" in f["message"]]
        assert len(broken) == 1

    def test_template_placeholder_skipped(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "with-template.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "Yesterday: [[{{yesterday}}]]")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"
                  and "with-template.md" in f.get("file", "")]
        assert broken == []

    def test_case_insensitive(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "case-test.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[Rust-Lifetimes]] for details.")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"
                  and "Rust-Lifetimes" in f["message"]]
        assert broken == []

    def test_path_qualified_resolves(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "path-link.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[Wiki/rust-lifetimes]] for details.")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"
                  and "rust-lifetimes" in f["message"]]
        assert broken == []

    def test_code_block_ignored(self, vault):
        tmp_path, router = vault
        body = "Before\n\n```\n[[nonexistent-in-code]]\n```\n\nAfter"
        write_md(tmp_path / "Wiki" / "with-code.md",
                 {"type": "living/wiki", "tags": ["test"]}, body)
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"
                  and "nonexistent-in-code" in f["message"]]
        assert broken == []

    def test_inline_code_span_ignored(self, vault):
        tmp_path, router = vault
        body = "Use `[[nonexistent-inline]]` to link, but not ``[[also-nonexistent]]``."
        write_md(tmp_path / "Wiki" / "with-inline-code.md",
                 {"type": "living/wiki", "tags": ["test"]}, body)
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"
                  and ("nonexistent-inline" in f["message"]
                       or "also-nonexistent" in f["message"])]
        assert broken == []

    def test_inline_code_span_does_not_hide_real_broken_link(self, vault):
        tmp_path, router = vault
        body = "Safe: `[[in-code]]`. Broken: [[really-missing]]."
        write_md(tmp_path / "Wiki" / "mixed-inline.md",
                 {"type": "living/wiki", "tags": ["test"]}, body)
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"]
        in_code = [f for f in broken if "in-code" in f["message"]]
        real = [f for f in broken if "really-missing" in f["message"]]
        assert in_code == []
        assert len(real) == 1

    def test_html_comment_ignored(self, vault):
        tmp_path, router = vault
        body = "Before\n<!-- [[nonexistent-in-comment]] -->\nAfter"
        write_md(tmp_path / "Wiki" / "with-comment.md",
                 {"type": "living/wiki", "tags": ["test"]}, body)
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"
                  and "nonexistent-in-comment" in f["message"]]
        assert broken == []

    def test_math_block_ignored(self, vault):
        tmp_path, router = vault
        body = "Math:\n$$\nf(x) = [[nonexistent-in-math]]\n$$\nDone."
        write_md(tmp_path / "Wiki" / "with-math.md",
                 {"type": "living/wiki", "tags": ["test"]}, body)
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"
                  and "nonexistent-in-math" in f["message"]]
        assert broken == []

    def test_raw_html_block_ignored(self, vault):
        tmp_path, router = vault
        body = "<pre>\n[[nonexistent-in-pre]]\n</pre>"
        write_md(tmp_path / "Wiki" / "with-pre.md",
                 {"type": "living/wiki", "tags": ["test"]}, body)
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"
                  and "nonexistent-in-pre" in f["message"]]
        assert broken == []

    def test_ambiguous_link_flagged(self, vault):
        tmp_path, router = vault
        # Create a second file with same basename in different type folder
        write_md(tmp_path / "Designs" / "rust-lifetimes.md",
                 {"type": "living/design", "tags": ["design"], "status": "shaping"},
                 "# Rust Lifetimes Design")
        # Link using basename only
        write_md(tmp_path / "Wiki" / "ambig-link.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[rust-lifetimes]] for details.")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        ambiguous = [f for f in findings if f["check"] == "ambiguous_wikilinks"
                     and "rust-lifetimes" in f["message"]]
        assert len(ambiguous) >= 1

    def test_ambiguous_path_qualified_not_flagged(self, vault):
        tmp_path, router = vault
        # Create duplicate basename
        write_md(tmp_path / "Designs" / "rust-lifetimes.md",
                 {"type": "living/design", "tags": ["design"], "status": "shaping"},
                 "# Rust Lifetimes Design")
        # Link using path-qualified form — unambiguous
        write_md(tmp_path / "Wiki" / "precise-link.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[Wiki/rust-lifetimes]] for details.")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        ambiguous = [f for f in findings if f["check"] == "ambiguous_wikilinks"
                     and "precise-link.md" in f.get("file", "")]
        assert ambiguous == []

    def test_broken_wikilinks_skips_archive(self, vault):
        """Broken links inside _Archive/ files are not reported."""
        tmp_path, router = vault
        archive = tmp_path / "Wiki" / "_Archive"
        archive.mkdir(parents=True, exist_ok=True)
        write_md(archive / "20260101-old-page.md",
                 {"type": "living/wiki", "tags": [], "archiveddate": "2026-01-01"},
                 "See [[totally-nonexistent-target]] here.")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"
                  and "totally-nonexistent-target" in f["message"]]
        assert broken == []
