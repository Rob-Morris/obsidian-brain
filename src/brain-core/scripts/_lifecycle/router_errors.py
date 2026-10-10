"""Named source failures before compiled-router outputs are persisted."""

from __future__ import annotations

from _common._text_encoding import TextDiagnosis


class UnreadableRouterSourceError(OSError):
    """A router source needs a text repair or investigation before compilation."""

    def __init__(self, path: str, diagnosis: TextDiagnosis):
        self.path = path
        self.diagnosis = diagnosis
        self.next_command = (
            "vault.repair-text" if diagnosis.fixed_bytes is not None else "vault.check"
        )
        super().__init__(
            f"unreadable router source '{path}' ({diagnosis.code}); "
            f"run {self.next_command} before refreshing the router"
        )
