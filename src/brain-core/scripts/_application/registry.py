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
from .artefact import set_workspace as artefact_set_workspace
from .artefact import set_naming_field as artefact_set_naming_field
from .artefact import set_status as artefact_set_status
from .artefact import unarchive as artefact_unarchive
from .access import prepare as access_prepare
from .access import reduce as access_reduce
from .access import request as access_request
from .access import status as access_status
from .attachment import upload as attachment_upload
from .content import classify as content_classify
from .content import ingest as content_ingest
from .content import resolve as content_resolve
from .document import replace_text as document_replace_text
from .document import structured_edit as document_structured_edit
from .document import update_frontmatter as document_update_frontmatter
from .document import write_body as document_write_body
from .foundation import build_application_catalogue, build_request_resolver
from .links import check as links_check
from .links import fix as links_fix
from .plugin import create as plugin_create
from .plugin import replace as plugin_replace
from .resource import create as resource_create
from .resource import list as resource_list
from .resource import read as resource_read
from .resource import search as resource_search
from .retrieval import construct_benchmark as retrieval_construct_benchmark
from .retrieval import enable as retrieval_enable
from .retrieval import evaluate as retrieval_evaluate
from .retrieval import refresh_lexical as retrieval_refresh_lexical
from .retrieval import rebuild_semantic as retrieval_rebuild_semantic
from .retrieval import repair_semantic as retrieval_repair_semantic
from .runtime import refresh_router as runtime_refresh_router
from .runtime import read_environment as runtime_read_environment
from .runtime import status as runtime_status
from .runtime import warmup as runtime_warmup
from .session import start as session_start
from .skill import add_git as skill_add_git
from .skill import detach as skill_detach
from .skill import list as skill_list
from .skill import status as skill_status
from .skill import update as skill_update
from .shaping import render as shaping_render
from .shaping import start as shaping_start
from .stage import create as stage_create
from .stage import discard as stage_discard
from .trigger import create as trigger_create
from .trigger import delete as trigger_delete
from .trigger import replace as trigger_replace
from .type import create as type_create
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
from .workspace import ensure_registration as workspace_ensure_registration
from .workspace import update_policy as workspace_update_policy
from .workspace import unregister as workspace_unregister
from .workspace import update_metadata as workspace_update_metadata
from .resolver import ResolverEntry


_COMMAND_OWNERS = (
    access_prepare,
    access_reduce,
    access_request,
    access_status,
    artefact_archive,
    artefact_convert,
    artefact_create,
    artefact_delete,
    artefact_list,
    artefact_migrate_naming,
    artefact_outline,
    artefact_read,
    artefact_repair,
    artefact_rename,
    artefact_reparent,
    artefact_reparent_children,
    artefact_search,
    artefact_set_key,
    artefact_set_workspace,
    artefact_set_naming_field,
    artefact_set_status,
    artefact_unarchive,
    attachment_upload,
    content_classify,
    content_ingest,
    content_resolve,
    document_replace_text,
    document_structured_edit,
    document_update_frontmatter,
    document_write_body,
    links_check,
    links_fix,
    plugin_create,
    plugin_replace,
    resource_create,
    resource_list,
    resource_read,
    resource_search,
    retrieval_construct_benchmark,
    retrieval_enable,
    retrieval_evaluate,
    retrieval_refresh_lexical,
    retrieval_rebuild_semantic,
    retrieval_repair_semantic,
    runtime_refresh_router,
    runtime_read_environment,
    runtime_status,
    runtime_warmup,
    session_start,
    skill_add_git,
    skill_detach,
    skill_list,
    skill_status,
    skill_update,
    shaping_render,
    shaping_start,
    stage_create,
    stage_discard,
    trigger_create,
    trigger_delete,
    trigger_replace,
    type_create,
    type_replace,
    type_status,
    type_sync,
    vault_check,
    vault_read_config,
    vault_read_router,
    vault_read_file,
    workspace_bind,
    workspace_configure_bootstrap,
    workspace_list,
    workspace_read,
    workspace_register,
    workspace_repair_registry,
    workspace_setup,
    workspace_ensure_registration,
    workspace_update_policy,
    workspace_unregister,
    workspace_update_metadata,
)


def current_application_catalogue():
    return build_application_catalogue(
        tuple(owner.catalogue_entry() for owner in _COMMAND_OWNERS)
    )


def current_request_resolver():
    return build_request_resolver(
        tuple(
            ResolverEntry(owner.catalogue_entry().request_type, owner.decode)
            for owner in _COMMAND_OWNERS
        )
    )
