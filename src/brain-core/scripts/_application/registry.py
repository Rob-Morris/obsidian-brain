"""Authoritative runtime assembly for migrated selected-Brain command owners."""

from __future__ import annotations

from .artefact import list as artefact_list
from .artefact import list_archived as artefact_list_archived
from .artefact import outline as artefact_outline
from .artefact import read as artefact_read
from .artefact import read_archived as artefact_read_archived
from .artefact import search as artefact_search
from .attachment import upload as attachment_upload
from .content import classify as content_classify
from .content import resolve as content_resolve
from .foundation import build_application_catalogue, build_request_resolver
from .links import check as links_check
from .memory import list as memory_list
from .memory import read as memory_read
from .memory import search as memory_search
from .plugin import list as plugin_list
from .plugin import read as plugin_read
from .plugin import search as plugin_search
from .runtime import read_environment as runtime_read_environment
from .session import start as session_start
from .skill import list as skill_list
from .skill import read as skill_read
from .skill import search as skill_search
from .stage import create as stage_create
from .stage import discard as stage_discard
from .style import list as style_list
from .style import read as style_read
from .style import search as style_search
from .template import list as template_list
from .template import read as template_read
from .trigger import list as trigger_list
from .trigger import read as trigger_read
from .trigger import search as trigger_search
from .type import list as artefact_type_list
from .type import read as artefact_type_read
from .type import status as type_status
from .vault import check as vault_check
from .vault import read_config as vault_read_config
from .vault import read_router as vault_read_router
from .vault import read_file as vault_read_file
from .workspace import list as workspace_list
from .workspace import read as workspace_read
from .workspace import resolve as workspace_resolve


def current_application_catalogue():
    return build_application_catalogue(
        (
            artefact_list.catalogue_entry(),
            artefact_list_archived.catalogue_entry(),
            artefact_outline.catalogue_entry(),
            artefact_read.catalogue_entry(),
            artefact_read_archived.catalogue_entry(),
            artefact_search.catalogue_entry(),
            attachment_upload.catalogue_entry(),
            content_classify.catalogue_entry(),
            content_resolve.catalogue_entry(),
            links_check.catalogue_entry(),
            memory_list.catalogue_entry(),
            memory_read.catalogue_entry(),
            memory_search.catalogue_entry(),
            plugin_list.catalogue_entry(),
            plugin_read.catalogue_entry(),
            plugin_search.catalogue_entry(),
            runtime_read_environment.catalogue_entry(),
            session_start.catalogue_entry(),
            skill_list.catalogue_entry(),
            skill_read.catalogue_entry(),
            skill_search.catalogue_entry(),
            stage_create.catalogue_entry(),
            stage_discard.catalogue_entry(),
            style_list.catalogue_entry(),
            style_read.catalogue_entry(),
            style_search.catalogue_entry(),
            template_list.catalogue_entry(),
            template_read.catalogue_entry(),
            trigger_list.catalogue_entry(),
            trigger_read.catalogue_entry(),
            trigger_search.catalogue_entry(),
            artefact_type_list.catalogue_entry(),
            artefact_type_read.catalogue_entry(),
            type_status.catalogue_entry(),
            vault_check.catalogue_entry(),
            vault_read_config.catalogue_entry(),
            vault_read_router.catalogue_entry(),
            vault_read_file.catalogue_entry(),
            workspace_list.catalogue_entry(),
            workspace_read.catalogue_entry(),
            workspace_resolve.catalogue_entry(),
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
            artefact_search.resolver_entry(),
            attachment_upload.resolver_entry(),
            content_classify.resolver_entry(),
            content_resolve.resolver_entry(),
            links_check.resolver_entry(),
            memory_list.resolver_entry(),
            memory_read.resolver_entry(),
            memory_search.resolver_entry(),
            plugin_list.resolver_entry(),
            plugin_read.resolver_entry(),
            plugin_search.resolver_entry(),
            runtime_read_environment.resolver_entry(),
            session_start.resolver_entry(),
            skill_list.resolver_entry(),
            skill_read.resolver_entry(),
            skill_search.resolver_entry(),
            stage_create.resolver_entry(),
            stage_discard.resolver_entry(),
            style_list.resolver_entry(),
            style_read.resolver_entry(),
            style_search.resolver_entry(),
            template_list.resolver_entry(),
            template_read.resolver_entry(),
            trigger_list.resolver_entry(),
            trigger_read.resolver_entry(),
            trigger_search.resolver_entry(),
            artefact_type_list.resolver_entry(),
            artefact_type_read.resolver_entry(),
            type_status.resolver_entry(),
            vault_check.resolver_entry(),
            vault_read_config.resolver_entry(),
            vault_read_router.resolver_entry(),
            vault_read_file.resolver_entry(),
            workspace_list.resolver_entry(),
            workspace_read.resolver_entry(),
            workspace_resolve.resolver_entry(),
        )
    )
