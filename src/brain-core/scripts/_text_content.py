"""Pure string rules shared by request contracts and persisted text writers."""


def strip_leading_byte_order_marks(text: str) -> str:
    """Remove every leading U+FEFF, preserving the rest of the text."""
    return text.lstrip("\ufeff")


def require_bom_free_text(content: str) -> None:
    """Refuse a leading Unicode marker before a persisted text write."""
    if content.startswith("\ufeff"):
        raise ValueError("Vault text must not begin with a byte-order mark")


def decode_bom_free_utf8(raw: bytes) -> str | None:
    """Return exact UTF-8 text, or None when named diagnosis is required."""
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return None if content.startswith("\ufeff") else content
