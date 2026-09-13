"""Compatibility exports for the canonical stdlib-only file lock owner."""

import sys

from _bootstrap.file_lock import (
    MutationLockError,
    exclusive_file_lock,
    mutation_lock_error_message,
    public_mutation_error_message,
    vault_mutation_lock,
)
