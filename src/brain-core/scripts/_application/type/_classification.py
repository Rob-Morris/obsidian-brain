"""Shared artefact-type classification contract."""

from enum import Enum


class ArtefactTypeClassification(str, Enum):
    LIVING = "living"
    TEMPORAL = "temporal"
