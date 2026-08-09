"""Authoritative runtime assembly for migrated selected-Brain command owners."""

from __future__ import annotations

from .artefact import list as artefact_list
from .artefact import outline as artefact_outline
from .artefact import read as artefact_read
from .foundation import build_application_catalogue, build_request_resolver


def current_application_catalogue():
    return build_application_catalogue(
        (
            artefact_list.catalogue_entry(),
            artefact_outline.catalogue_entry(),
            artefact_read.catalogue_entry(),
        )
    )


def current_request_resolver():
    return build_request_resolver(
        (
            artefact_list.resolver_entry(),
            artefact_outline.resolver_entry(),
            artefact_read.resolver_entry(),
        )
    )
