"""Supported typed-Python façade for selected-Brain commands.

Importing this module loads only the transport-neutral kernel. Request records
live in domain modules such as :mod:`brain_application.documents`; internal
``_application`` owner modules are not a supported integration surface.
"""

from _application.application import CommandApplication
from _application.context import InvocationContext, SelectedBrain
from _application.results import CommandResult, Error, Ok, Partial

__all__ = (
    "CommandApplication",
    "CommandResult",
    "Error",
    "InvocationContext",
    "Ok",
    "Partial",
    "SelectedBrain",
)
