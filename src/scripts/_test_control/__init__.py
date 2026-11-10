"""Opt-in controls for contributor pytest runs; ordinary runs remain inert."""
from .client import checkpoint, register_cleanup

__all__ = ["checkpoint", "register_cleanup"]
