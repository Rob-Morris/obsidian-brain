"""Shared value types for cohesive resource commands."""

from enum import Enum


class SkillSource(str, Enum):
    CORE = "core"
    USER = "user"


class TriggerCategory(str, Enum):
    BEFORE = "before"
    AFTER = "after"
    ONGOING = "ongoing"
