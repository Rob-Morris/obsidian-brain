"""Pure string rules shared by request contracts and persisted text writers."""


def strip_leading_byte_order_marks(text: str) -> str:
    """Remove every leading U+FEFF, preserving the rest of the text."""
    return text.lstrip("\ufeff")
