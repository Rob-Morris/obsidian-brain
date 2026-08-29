"""All sealed command request records on one explicit opt-in import surface.

Importing this module loads command contract modules for every dependency tier.
Consumers that need a narrow, cheap import should prefer domain façade modules.
"""

from _application.requests import *  # noqa: F403
from _application.requests import __all__
