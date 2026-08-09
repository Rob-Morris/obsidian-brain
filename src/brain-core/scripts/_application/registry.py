"""Authoritative runtime assembly for migrated selected-Brain command owners."""

from __future__ import annotations

from .artefact import list as artefact_list
from .artefact import list_archived as artefact_list_archived
from .artefact import outline as artefact_outline
from .artefact import read as artefact_read
from .artefact import read_archived as artefact_read_archived
from .foundation import build_application_catalogue, build_request_resolver
from .links import check as links_check
from .memory import list as memory_list
from .memory import read as memory_read
from .plugin import list as plugin_list
from .plugin import read as plugin_read
from .runtime import read_environment as runtime_read_environment
from .skill import list as skill_list
from .skill import read as skill_read
from .style import list as style_list
from .style import read as style_read
from .template import list as template_list
from .template import read as template_read
from .trigger import list as trigger_list
from .trigger import read as trigger_read
from .type import list as artefact_type_list
from .type import read as artefact_type_read
from .vault import read_router as vault_read_router
from .vault import read_file as vault_read_file


def current_application_catalogue():
    return build_application_catalogue(
        (
            artefact_list.catalogue_entry(),
            artefact_list_archived.catalogue_entry(),
            artefact_outline.catalogue_entry(),
            artefact_read.catalogue_entry(),
            artefact_read_archived.catalogue_entry(),
            links_check.catalogue_entry(),
            memory_list.catalogue_entry(),
            memory_read.catalogue_entry(),
            plugin_list.catalogue_entry(),
            plugin_read.catalogue_entry(),
            runtime_read_environment.catalogue_entry(),
            skill_list.catalogue_entry(),
            skill_read.catalogue_entry(),
            style_list.catalogue_entry(),
            style_read.catalogue_entry(),
            template_list.catalogue_entry(),
            template_read.catalogue_entry(),
            trigger_list.catalogue_entry(),
            trigger_read.catalogue_entry(),
            artefact_type_list.catalogue_entry(),
            artefact_type_read.catalogue_entry(),
            vault_read_router.catalogue_entry(),
            vault_read_file.catalogue_entry(),
        )
    )


def current_request_resolver():
    return build_request_resolver(
        (
            artefact_list.resolver_entry(),
            artefact_list_archived.resolver_entry(),
            artefact_outline.resolver_entry(),
            artefact_read.resolver_entry(),
            artefact_read_archived.resolver_entry(),
            links_check.resolver_entry(),
            memory_list.resolver_entry(),
            memory_read.resolver_entry(),
            plugin_list.resolver_entry(),
            plugin_read.resolver_entry(),
            runtime_read_environment.resolver_entry(),
            skill_list.resolver_entry(),
            skill_read.resolver_entry(),
            style_list.resolver_entry(),
            style_read.resolver_entry(),
            template_list.resolver_entry(),
            template_read.resolver_entry(),
            trigger_list.resolver_entry(),
            trigger_read.resolver_entry(),
            artefact_type_list.resolver_entry(),
            artefact_type_read.resolver_entry(),
            vault_read_router.resolver_entry(),
            vault_read_file.resolver_entry(),
        )
    )
