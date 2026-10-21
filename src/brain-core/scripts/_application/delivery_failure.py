"""Content-safe failures of the retained-result delivery port."""

from dataclasses import dataclass

from .results import CommandError, Error, ErrorCode


class DeliveryFailure(Exception):
    """Carry a stable reason and safe recovery message, never backend diagnostics."""

    def __init__(self, reason: str, message: str, *, capacity_limits: dict[str, int] | None = None):
        self.reason = reason
        self.message = message
        self.capacity_limits = capacity_limits
        super().__init__(message)

    def navigation_error(self, command_id: str, command_version: int,
                         result_id: str) -> "NavigationDeliveryError":
        """Keep a failed navigation distinct from its retained source outcome."""
        return NavigationDeliveryError(command_id, command_version,
            CommandError(ErrorCode.CAPABILITY_UNAVAILABLE, self.message),
            failure=self, result_id=result_id)


@dataclass(frozen=True, slots=True, kw_only=True)
class NavigationDeliveryError(Error):
    """Known no-effect navigation failure carrying sanitised delivery recovery."""

    failure: DeliveryFailure
    result_id: str
