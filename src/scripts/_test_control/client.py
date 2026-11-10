"""Cooperative checkpoints at test-owned resource boundaries."""
from contextvars import ContextVar
import sys


CURRENT = ContextVar("controlled_test_attempt", default=None)


class OperatorInterrupted(BaseException):
    """Unwind a supported wait without disguising an ordinary test failure."""


def checkpoint(progress: str | None = None) -> bool:
    """Observe wrap-up and report active control; ordinary pytest does no I/O."""
    attempt = CURRENT.get()
    if attempt is not None and sys.exc_info()[0] is None:
        attempt.checkpoint(progress)
        return True
    return False


def register_cleanup(check) -> None:
    """Register an audited resource-restoration predicate for this attempt.

    The caller owns the cleanup contract. A passing pytest teardown alone is
    insufficient; no predicates, a false predicate or an exception stops the run.
    """
    attempt = CURRENT.get()
    if attempt is not None:
        attempt.cleanup_checks.append(check)
