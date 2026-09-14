"""Selected-Brain composition for trusted local Python integrations.

The composer resolves the current credential permissions and Brain policy,
records owned outcomes, and must be closed by its embedding process. Importing
this adapter is an explicit opt-in to installed local infrastructure; the
``brain_application`` kernel remains independent of that infrastructure.
"""
from _command_interface.direct import DirectContextComposer as LocalContextComposer

__all__ = ("LocalContextComposer",)
