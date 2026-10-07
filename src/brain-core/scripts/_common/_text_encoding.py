"""Pure diagnosis of non-standard vault text and deterministic byte repairs."""

from __future__ import annotations

import codecs
from dataclasses import dataclass


UTF32_BOM = "utf32_bom"
UTF16_BOM = "utf16_bom"
UTF8_BOM = "utf8_bom"
TRUNCATED_UTF8 = "truncated_utf8"
NOT_UTF8 = "not_utf8"
NOT_TEXT = "not_text"


@dataclass(frozen=True)
class TextDiagnosis:
    """A clear repair's bytes, or an ambiguous failure's one-based position.

    Line and column locate the first invalid byte in the original bytes;
    columns count bytes, not decoded characters. Only ``not_utf8`` carries
    a position. Only clear codes carry ``fixed_bytes``.
    """

    code: str
    lossless: bool = False
    fixed_bytes: bytes | None = None
    dropped_bytes: bytes = b""
    line: int | None = None
    column: int | None = None


def strip_leading_byte_order_marks(text: str) -> str:
    """Remove every leading U+FEFF, preserving the rest of the text."""
    return text.lstrip("\ufeff")


def _not_utf8(data: bytes, start: int) -> TextDiagnosis:
    prefix = data[:start].replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return TextDiagnosis(
        NOT_UTF8,
        line=prefix.count(b"\n") + 1,
        column=len(prefix) - prefix.rfind(b"\n"),
    )


def _ambiguous(data: bytes) -> TextDiagnosis:
    if b"\x00" in data:
        return TextDiagnosis(NOT_TEXT)
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return _not_utf8(data, exc.start)
    raise ValueError("ambiguous text must contain NUL or invalid UTF-8")


def diagnose_text(data: bytes) -> TextDiagnosis | None:
    """Classify bytes without guessing an encoding or changing line endings.

    Marked text is repairable only when it decodes strictly and contains no
    NUL. A final incomplete UTF-8 character is repairable only after a valid
    multibyte character; marked text with additional damage is never truncated.
    """
    for marks, encoding, code in (
        ((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE), "utf-32", UTF32_BOM),
        ((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE), "utf-16", UTF16_BOM),
        ((codecs.BOM_UTF8,), "utf-8", UTF8_BOM),
    ):
        if data.startswith(marks):
            try:
                text = data.decode(encoding)
            except UnicodeDecodeError:
                return _ambiguous(data)
            if "\x00" in text:
                return TextDiagnosis(NOT_TEXT)
            return TextDiagnosis(
                code,
                lossless=True,
                fixed_bytes=strip_leading_byte_order_marks(text).encode("utf-8"),
            )

    if b"\x00" in data:
        return TextDiagnosis(NOT_TEXT)
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        prefix = data[:exc.start]
        if (
            exc.reason == "unexpected end of data"
            and exc.end == len(data)
            and any(byte >= 0x80 for byte in prefix)
        ):
            return TextDiagnosis(
                TRUNCATED_UTF8,
                fixed_bytes=prefix,
                dropped_bytes=data[exc.start:],
            )
        return _not_utf8(data, exc.start)
    return None
