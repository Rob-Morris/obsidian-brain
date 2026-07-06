"""Shared exception types for script and MCP boundaries."""


class PartialApplyError(RuntimeError):
    """A mutation wrote some durable state before a later step failed."""

