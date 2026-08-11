"""Authoritative runtime assembly for migrated selected-Brain command owners."""

from __future__ import annotations

from .artefact import archive as artefact_archive
from .artefact import convert as artefact_convert
from .artefact import create as artefact_create
from .artefact import delete as artefact_delete
from .artefact import list as artefact_list
from .artefact import migrate_naming as artefact_migrate_naming
from .artefact import outline as artefact_outline
from .artefact import read as artefact_read
from .artefact import repair as artefact_repair
from .artefact import reparent as artefact_reparent
from .artefact import rename as artefact_rename
from .artefact import reparent_children as artefact_reparent_children
from .artefact import search as artefact_search
from .artefact import set_key as artefact_set_key
from .artefact import set_naming_field as artefact_set_naming_field
from .artefact import set_status as artefact_set_status
from .artefact import unarchive as artefact_unarchive
from .attachment import upload as attachment_upload
from .content import classify as content_classify
from .content import ingest as content_ingest
from .content import resolve as content_resolve
from .document import edit as document_edit
from .foundation import build_application_catalogue, build_request_resolver
from .links import check as links_check
from .links import fix as links_fix
from .memory import create as memory_create
from .memory import list as memory_list
from .memory import read as memory_read
from .memory import search as memory_search
from .plugin import create as plugin_create
from .plugin import list as plugin_list
from .plugin import read as plugin_read
from .plugin import replace as plugin_replace
from .plugin import search as plugin_search
from .retrieval import construct_benchmark as retrieval_construct_benchmark
from .retrieval import enable as retrieval_enable
from .retrieval import evaluate as retrieval_evaluate
from .retrieval import refresh_lexical as retrieval_refresh_lexical
from .retrieval import rebuild_semantic as retrieval_rebuild_semantic
from .retrieval import repair_semantic as retrieval_repair_semantic
from .runtime import refresh_router as runtime_refresh_router
from .runtime import read_environment as runtime_read_environment
from .session import start as session_start
from .shaping import render_presentation as shaping_render_presentation
from .shaping import render_printable as shaping_render_printable
from .shaping import start as shaping_start
from .skill import create as skill_create
from .skill import list as skill_list
from .skill import read as skill_read
from .skill import search as skill_search
from .stage import create as stage_create
from .stage import discard as stage_discard
from .style import create as style_create
from .style import list as style_list
from .style import read as style_read
from .style import search as style_search
from .template import create as template_create
from .template import list as template_list
from .template import read as template_read
from .trigger import create as trigger_create
from .trigger import delete as trigger_delete
from .trigger import list as trigger_list
from .trigger import read as trigger_read
from .trigger import replace as trigger_replace
from .trigger import search as trigger_search
from .type import create as type_create
from .type import list as artefact_type_list
from .type import read as artefact_type_read
from .type import replace as type_replace
from .type import status as type_status
from .type import sync as type_sync
from .vault import check as vault_check
from .vault import read_config as vault_read_config
from .vault import read_router as vault_read_router
from .vault import read_file as vault_read_file
from .workspace import bind as workspace_bind
from .workspace import configure_bootstrap as workspace_configure_bootstrap
from .workspace import list as workspace_list
from .workspace import read as workspace_read
from .workspace import register as workspace_register
from .workspace import repair_registry as workspace_repair_registry
from .workspace import setup as workspace_setup
from .workspace import unregister as workspace_unregister
from .workspace import update_metadata as workspace_update_metadata


def current_application_catalogue():
    return build_application_catalogue(
        (
            artefact_archive.catalogue_entry(),
            artefact_convert.catalogue_entry(),
            artefact_create.catalogue_entry(),
            artefact_delete.catalogue_entry(),
            artefact_list.catalogue_entry(),
            artefact_migrate_naming.catalogue_entry(),
            artefact_outline.catalogue_entry(),
            artefact_read.catalogue_entry(),
            artefact_repair.catalogue_entry(),
            artefact_rename.catalogue_entry(),
            artefact_reparent.catalogue_entry(),
            artefact_reparent_children.catalogue_entry(),
            artefact_search.catalogue_entry(),
            artefact_set_key.catalogue_entry(),
            artefact_set_naming_field.catalogue_entry(),
            artefact_set_status.catalogue_entry(),
            artefact_unarchive.catalogue_entry(),
            attachment_upload.catalogue_entry(),
            content_classify.catalogue_entry(),
            content_ingest.catalogue_entry(),
            content_resolve.catalogue_entry(),
            document_edit.catalogue_entry(),
            links_check.catalogue_entry(),
            links_fix.catalogue_entry(),
            memory_create.catalogue_entry(),
            memory_list.catalogue_entry(),
            memory_read.catalogue_entry(),
            memory_search.catalogue_entry(),
            plugin_create.catalogue_entry(),
            plugin_list.catalogue_entry(),
            plugin_read.catalogue_entry(),
            plugin_replace.catalogue_entry(),
            plugin_search.catalogue_entry(),
            retrieval_construct_benchmark.catalogue_entry(),
            retrieval_enable.catalogue_entry(),
            retrieval_evaluate.catalogue_entry(),
            retrieval_refresh_lexical.catalogue_entry(),
            retrieval_rebuild_semantic.catalogue_entry(),
            retrieval_repair_semantic.catalogue_entry(),
            runtime_refresh_router.catalogue_entry(),
            runtime_read_environment.catalogue_entry(),
            session_start.catalogue_entry(),
            shaping_render_presentation.catalogue_entry(),
            shaping_render_printable.catalogue_entry(),
            shaping_start.catalogue_entry(),
            skill_create.catalogue_entry(),
            skill_list.catalogue_entry(),
            skill_read.catalogue_entry(),
            skill_search.catalogue_entry(),
            stage_create.catalogue_entry(),
            stage_discard.catalogue_entry(),
            style_create.catalogue_entry(),
            style_list.catalogue_entry(),
            style_read.catalogue_entry(),
            style_search.catalogue_entry(),
            template_create.catalogue_entry(),
            template_list.catalogue_entry(),
            template_read.catalogue_entry(),
            trigger_create.catalogue_entry(),
            trigger_delete.catalogue_entry(),
            trigger_list.catalogue_entry(),
            trigger_read.catalogue_entry(),
            trigger_replace.catalogue_entry(),
            trigger_search.catalogue_entry(),
            type_create.catalogue_entry(),
            artefact_type_list.catalogue_entry(),
            artefact_type_read.catalogue_entry(),
            type_replace.catalogue_entry(),
            type_status.catalogue_entry(),
            type_sync.catalogue_entry(),
            vault_check.catalogue_entry(),
            vault_read_config.catalogue_entry(),
            vault_read_router.catalogue_entry(),
            vault_read_file.catalogue_entry(),
            workspace_bind.catalogue_entry(),
            workspace_configure_bootstrap.catalogue_entry(),
            workspace_list.catalogue_entry(),
            workspace_read.catalogue_entry(),
            workspace_register.catalogue_entry(),
            workspace_repair_registry.catalogue_entry(),
            workspace_setup.catalogue_entry(),
            workspace_unregister.catalogue_entry(),
            workspace_update_metadata.catalogue_entry(),
        )
    )


def current_request_resolver():
    return build_request_resolver(
        (
            artefact_archive.resolver_entry(),
            artefact_convert.resolver_entry(),
            artefact_create.resolver_entry(),
            artefact_delete.resolver_entry(),
            artefact_list.resolver_entry(),
            artefact_migrate_naming.resolver_entry(),
            artefact_outline.resolver_entry(),
            artefact_read.resolver_entry(),
            artefact_repair.resolver_entry(),
            artefact_rename.resolver_entry(),
            artefact_reparent.resolver_entry(),
            artefact_reparent_children.resolver_entry(),
            artefact_search.resolver_entry(),
            artefact_set_key.resolver_entry(),
            artefact_set_naming_field.resolver_entry(),
            artefact_set_status.resolver_entry(),
            artefact_unarchive.resolver_entry(),
            attachment_upload.resolver_entry(),
            content_classify.resolver_entry(),
            content_ingest.resolver_entry(),
            content_resolve.resolver_entry(),
            document_edit.resolver_entry(),
            links_check.resolver_entry(),
            links_fix.resolver_entry(),
            memory_create.resolver_entry(),
            memory_list.resolver_entry(),
            memory_read.resolver_entry(),
            memory_search.resolver_entry(),
            plugin_create.resolver_entry(),
            plugin_list.resolver_entry(),
            plugin_read.resolver_entry(),
            plugin_replace.resolver_entry(),
            plugin_search.resolver_entry(),
            retrieval_construct_benchmark.resolver_entry(),
            retrieval_enable.resolver_entry(),
            retrieval_evaluate.resolver_entry(),
            retrieval_refresh_lexical.resolver_entry(),
            retrieval_rebuild_semantic.resolver_entry(),
            retrieval_repair_semantic.resolver_entry(),
            runtime_refresh_router.resolver_entry(),
            runtime_read_environment.resolver_entry(),
            session_start.resolver_entry(),
            shaping_render_presentation.resolver_entry(),
            shaping_render_printable.resolver_entry(),
            shaping_start.resolver_entry(),
            skill_create.resolver_entry(),
            skill_list.resolver_entry(),
            skill_read.resolver_entry(),
            skill_search.resolver_entry(),
            stage_create.resolver_entry(),
            stage_discard.resolver_entry(),
            style_create.resolver_entry(),
            style_list.resolver_entry(),
            style_read.resolver_entry(),
            style_search.resolver_entry(),
            template_create.resolver_entry(),
            template_list.resolver_entry(),
            template_read.resolver_entry(),
            trigger_create.resolver_entry(),
            trigger_delete.resolver_entry(),
            trigger_list.resolver_entry(),
            trigger_read.resolver_entry(),
            trigger_replace.resolver_entry(),
            trigger_search.resolver_entry(),
            type_create.resolver_entry(),
            artefact_type_list.resolver_entry(),
            artefact_type_read.resolver_entry(),
            type_replace.resolver_entry(),
            type_status.resolver_entry(),
            type_sync.resolver_entry(),
            vault_check.resolver_entry(),
            vault_read_config.resolver_entry(),
            vault_read_router.resolver_entry(),
            vault_read_file.resolver_entry(),
            workspace_bind.resolver_entry(),
            workspace_configure_bootstrap.resolver_entry(),
            workspace_list.resolver_entry(),
            workspace_read.resolver_entry(),
            workspace_register.resolver_entry(),
            workspace_repair_registry.resolver_entry(),
            workspace_setup.resolver_entry(),
            workspace_unregister.resolver_entry(),
            workspace_update_metadata.resolver_entry(),
        )
    )
