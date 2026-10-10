"""Behaviour of the shared byte classifier, without filesystem dependencies."""

import codecs
from dataclasses import FrozenInstanceError

import pytest

from _common._text_encoding import diagnose_text, strip_leading_byte_order_marks


@pytest.mark.parametrize("data", [b"", b"ASCII\r\n", "café 😀\r\n".encode(), "a\ufeffb".encode()])
def test_standard_text_has_no_diagnosis(data):
    assert diagnose_text(data) is None


@pytest.mark.parametrize("mark,encoding,code", [
    (codecs.BOM_UTF8, "utf-8", "utf8_bom"),
    (codecs.BOM_UTF16_LE, "utf-16-le", "utf16_bom"),
    (codecs.BOM_UTF16_BE, "utf-16-be", "utf16_bom"),
    (codecs.BOM_UTF32_LE, "utf-32-le", "utf32_bom"),
    (codecs.BOM_UTF32_BE, "utf-32-be", "utf32_bom"),
])
@pytest.mark.parametrize("text", ["", "café 😀\r\nSecond\rline\n", "\ufeff\ufeffTitle\r\n\ufeffbody"])
def test_marked_text_has_a_lossless_idempotent_fix(mark, encoding, code, text):
    result = diagnose_text(mark + text.encode(encoding))
    assert result.code == code
    assert result.lossless is True
    assert result.fixed_bytes == text.lstrip("\ufeff").encode("utf-8")
    assert result.dropped_bytes == b""
    assert result.line is None and result.column is None
    assert diagnose_text(result.fixed_bytes) is None


@pytest.mark.parametrize("tail", [b"\xc3", b"\xe2", b"\xe2\x82", b"\xf0", b"\xf0\x9f", b"\xf0\x9f\x98"])
def test_truncation_retains_every_byte_before_the_incomplete_character(tail):
    prefix = "café\r\n".encode("utf-8")
    result = diagnose_text(prefix + tail)
    assert result.code == "truncated_utf8"
    assert result.lossless is False
    assert result.fixed_bytes == prefix
    assert result.dropped_bytes == tail
    assert diagnose_text(result.fixed_bytes) is None


@pytest.mark.parametrize("data", [b"\xc3", b"ASCII\xe2\x82", b"ASCII\xf0\x9f\x98"])
def test_truncation_without_prior_multibyte_text_is_ambiguous(data):
    result = diagnose_text(data)
    assert result.code == "not_utf8"
    assert result.fixed_bytes is None


def test_windows_1252_counterexample_remains_a_judgement_case():
    data = "Ã© then Ã".encode("cp1252")
    result = diagnose_text(data)
    assert result.code == "truncated_utf8"
    assert result.lossless is False
    assert result.dropped_bytes.decode("cp1252") == "Ã"
    assert result.fixed_bytes == "é then ".encode("utf-8")


@pytest.mark.parametrize("data", [
    b"a\x00b",
    b"\x00\xff",
    "café".encode("utf-16-le"),
    "café".encode("utf-16-be"),
    "café".encode("utf-32-le"),
    "café".encode("utf-32-be"),
    codecs.BOM_UTF8 + b"a\x00b",
    codecs.BOM_UTF16_LE + "a\x00b".encode("utf-16-le"),
    codecs.BOM_UTF32_BE + "a\x00b".encode("utf-32-be"),
])
def test_nul_is_not_text_even_when_decode_would_fail(data):
    result = diagnose_text(data)
    assert result.code == "not_text"
    assert result.fixed_bytes is None
    assert result.lossless is False


@pytest.mark.parametrize("data,code", [
    (codecs.BOM_UTF8 + b"caf\xc3\xa9\xc3", "not_utf8"),
    (codecs.BOM_UTF8 + b"\xff", "not_utf8"),
    (codecs.BOM_UTF16_LE + b"a", "not_utf8"),
    (codecs.BOM_UTF16_BE + b"\xd8\x00", "not_text"),
    (codecs.BOM_UTF32_LE + b"a", "not_text"),
    (codecs.BOM_UTF32_BE + b"\x00\x11\x00\x00", "not_text"),
    (b"caf\xc3\xa9\xff\xc3", "not_utf8"),
])
def test_combined_damage_never_offers_a_clear_fix(data, code):
    result = diagnose_text(data)
    assert result.code == code
    assert result.fixed_bytes is None
    assert result.dropped_bytes == b""


@pytest.mark.parametrize("data,line,column", [
    (b"\xff", 1, 1),
    (b"plain\xff", 1, 6),
    (b"a\r\n\xff", 2, 1),
    (b"a\r\xff", 2, 1),
    ("a\n café".encode() + b"\xff", 2, 7),
    (b"a\n\n\xc3", 3, 1),
])
def test_invalid_utf8_locates_the_first_bad_byte(data, line, column):
    result = diagnose_text(data)
    assert result.code == "not_utf8"
    assert (result.line, result.column) == (line, column)


@pytest.mark.parametrize("text,expected", [
    ("", ""),
    ("\ufeff", ""),
    ("\ufeff\ufeffTitle\r\n\ufeffBody", "Title\r\n\ufeffBody"),
    (" \ufeffTitle", " \ufeffTitle"),
    ("\n\ufeffTitle", "\n\ufeffTitle"),
])
def test_strip_removes_only_leading_marks(text, expected):
    assert strip_leading_byte_order_marks(text) == expected


def test_diagnosis_is_an_immutable_value():
    result = diagnose_text(codecs.BOM_UTF8 + b"text")
    with pytest.raises(FrozenInstanceError):
        result.code = "not_utf8"
