"""Catalogue-pinned Stage 8 application sweep; failures are behaviour gaps.

Each TEXT role has a UTF-8 control, a lossless UTF-16 source, and a UTF-8
source ending mid-codepoint. Only explicit vault.repair-text may drop the
reported trailing bytes. No router, decoding or owner seam is replaced.
Managed git skill packages are outside the standard vault text scope.
"""
from dataclasses import dataclass
import codecs
import hashlib

import pytest
import compile_router
from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode, WarningCode
from _application.types import EffectClass
from _common import document_revision_at, NonStandardVaultTextError
from _lifecycle.router_errors import UnreadableRouterSourceError
from command_application import application_for

CANDIDATE = 'Ideas/Command Fixture Candidate.md'
DESIGN = 'Designs/project~command-fixture/Command Fixture Design.md'
PROJECT = 'Projects/Command Fixture.md'
PLAN = '_Temporal/Plans/project~command-fixture/20260809-plan~Command Fixture Execution.md'
ROUTER = '_Config/router.md'
TAXONOMY = '_Config/Taxonomy/Living/ideas.md'
TEMPLATE = '_Config/Templates/Living/Ideas.md'
BACKLINK = 'Wiki/Sweep Backlink.md'
MARKER = 'Sweep mutation verified.'
INLINE = {'source': 'inline', 'content': MARKER + '\n'}


@dataclass(frozen=True)
class Role:
    name: str
    path: str
    scenario: str
    read_only: bool = False


# Reasons apply to existing persisted text, not newly supplied inline content.
NO_TEXT = {
    'retrieval.construct-benchmark': 'Writes benchmark and audit JSON; corpus Markdown is read-only, no persisted vault text mutation role.',
    'retrieval.enable': 'Writes operational semantic configuration/runtime/cache assets; corpus Markdown is read-only.',
    'retrieval.rebuild-semantic': 'Rebuilds binary/JSON semantic cache assets; corpus Markdown is read-only.',
    'retrieval.refresh-lexical': 'Rebuilds operational lexical JSON; corpus Markdown is read-only.',
    'retrieval.repair-semantic': 'Repairs operational semantic runtime/cache assets; corpus Markdown is read-only.',
    'runtime.refresh-router': 'Writes compiled-router JSON and derived mirrors; authored router sources are read-only (strict builder coverage is Stage 3–6).',
    'workspace.unregister': 'Removes registry JSON and unlinks operational caller .brain/local/workspace.yaml; both are outside approved TEXT inventory. Canonical hub Markdown is neither read nor mutated (healthy before/after test below).',
    'access.prepare': 'Private consent preparations and pinned request bytes; no vault TEXT mutation.',
    'access.reduce': 'Consent state and grants only; operational JSON, no vault TEXT role.',
    'access.request': 'Consent grants only; operational JSON, no vault TEXT role.',
    'attachment.upload': 'Opaque attachment bytes; even a .txt attachment is not a vault TEXT role.',
    'maintenance.claim': 'Finding claim state in operational JSON; no persisted text role.',
    'maintenance.dismiss': 'Finding dismissal state in operational JSON; no persisted text role.',
    'maintenance.release': 'Finding claim release in operational JSON; no persisted text role.',
    'skill.add-git': 'Managed git package installation and tracking; package files excluded from standard TEXT scope.',
    'skill.detach': 'Managed package ownership/tracking change; package files excluded from standard TEXT scope.',
    'skill.status': 'Managed package tracking refresh; package files excluded from standard TEXT scope.',
    'skill.update': 'Managed git package replacement; package files excluded from standard TEXT scope.',
    'stage.create': 'New caller-supplied staging bytes; no existing persisted text role.',
    'stage.discard': 'Removes a private staged handle without interpreting vault TEXT.',
    'plugin.create': 'Creates an absent plugin from inline bytes; existing plugin is never replaced.',
    'type.create': 'Creates an absent taxonomy/template bundle from inline bytes; replacement has separate roles.',
}
OTHER_MUTATIONS = {
    'maintenance.run': 'Writes derived maintenance findings/cache state; authored text remains read-only.',
    'runtime.remove-temporaries': 'Deletes temporary derived state; no authored text role.',
    'runtime.warmup': 'Writes runtime/cache diagnostics; no authored text role.',
    'session.start': 'Writes generated session mirrors; no authored text mutation role.',
    'workspace.repair-registry': 'Repairs local workspace registry JSON; no authored text role.',
    'workspace.configure-bootstrap': 'Caller-local bootstrap configuration; outside selected-Brain scope.',
    'workspace.update-metadata': 'Caller-local workspace manifest; outside selected-Brain scope.',
}


ROLES = {
    'artefact.archive': (Role('target', CANDIDATE, 'lifecycle'),),
    # edit.plan_convert reconciles the source using the compiled target type
    # contract; it never consumes a creation template, so that is not a TEXT role.
    'artefact.convert': (Role('target', CANDIDATE, 'lifecycle'),),
    'artefact.create': (Role('creation-template', TEMPLATE, 'create', read_only=True), Role('type-definition', TAXONOMY, 'create', read_only=True)),
    'artefact.delete': (Role('target', DESIGN, 'lifecycle'),),
    'artefact.migrate-naming': (Role('legacy-target', 'Ideas/sweep-legacy.md', 'migration'),),
    'artefact.rename': (Role('target', DESIGN, 'lifecycle'), Role('backlink', BACKLINK, 'backlink')),
    'artefact.repair': (Role('frontmatter-target', CANDIDATE, 'lifecycle'),),
    'artefact.reparent': (Role('target', CANDIDATE, 'lifecycle'), Role('backlink', BACKLINK, 'backlink')),
    # Production selects direct living children. The temporal owned PLAN is
    # intentionally untouched; the healthy before/after test below guards this.
    'artefact.reparent-children': (Role('design-child', DESIGN, 'children'),),
    'artefact.set-key': (Role('target', CANDIDATE, 'lifecycle'), Role('owned-child', 'Designs/idea~command-fixture-candidate/Sweep Child.md', 'owned-child')),
    'artefact.set-naming-field': (Role('target', PLAN, 'lifecycle'), Role('backlink', BACKLINK, 'backlink')),
    'artefact.set-status': (Role('target', CANDIDATE, 'lifecycle'), Role('backlink', BACKLINK, 'backlink')),
    'artefact.set-workspace': (Role('target', CANDIDATE, 'workspace'),),
    'artefact.unarchive': (Role('archived-target', '_Archive/Ideas/Command Fixture Candidate.md', 'unarchive'),),
    'content.ingest': (Role('existing-exact-title', CANDIDATE, 'ingest'), Role('creation-template', TEMPLATE, 'ingest-create', read_only=True)),
    'links.fix': (Role('target', DESIGN, 'links'),),
    'plugin.replace': (Role('existing-plugin', '_Plugins/Command Plugin/SKILL.md', 'plugin'),),
    'resource.create': (Role('template-type-definition', '_Config/Taxonomy/Living/projects.md', 'resource', read_only=True),),
    'shaping.render': (Role('source', DESIGN, 'render'),),
    'shaping.start': (Role('canonical-target', DESIGN, 'shaping'), Role('existing-transcript', '', 'transcript')),
    'trigger.create': (Role('router-target', ROUTER, 'trigger'),),
    'trigger.delete': (Role('router-target', ROUTER, 'trigger'),),
    'trigger.replace': (Role('router-target', ROUTER, 'trigger'),),
    'type.replace': (Role('existing-definition', '_Config/Taxonomy/Living/widgets.md', 'type'), Role('existing-template', '_Config/Templates/Living/Widgets.md', 'type')),
    'type.sync': (Role('local-definition', '_Config/Taxonomy/Living/journals.md', 'sync'), Role('local-template', '_Config/Templates/Living/Journals.md', 'sync')),
    'vault.repair-text': (Role('selected-text', DESIGN, 'repair-text'),),
    'workspace.ensure-registration': (Role('existing-hub', 'Workspaces/Sweep.md', 'workspace'),),
    'workspace.update-policy': (Role('canonical-hub', 'Workspaces/Sweep.md', 'workspace'),),
    # Setup's YAML binding and registry JSON are operational, excluded TEXT;
    # its canonical hub and consumed creation template remain concrete roles.
    'workspace.setup': (Role('existing-hub', 'Workspaces/Sweep.md', 'mixed-hub'),
                        Role('creation-template', '_Config/Templates/Living/Workspaces.md', 'mixed-template', read_only=True)),
}
DOCUMENTS = (
    ('artefact', DESIGN, DESIGN),
    ('memory', 'brain-core-reference', '_Config/Memories/brain-core-reference.md'),
    ('skill', 'vault-maintenance', '_Config/Skills/vault-maintenance/SKILL.md'),
    ('style', 'writing', '_Config/Styles/writing.md'),
    ('template', 'designs', '_Config/Templates/Living/Designs.md'),
)
for command in ('document.replace-text', 'document.structured-edit', 'document.update-frontmatter', 'document.write-body'):
    ROLES[command] = tuple(Role(resource, path, 'document') for resource, reference, path in DOCUMENTS)

CATALOGUE = current_application_catalogue()
TEXT_MUTATION_EFFECTS = {EffectClass.SELECTED_BRAIN_MUTATION,
                         EffectClass.SELECTED_BRAIN_AND_CALLER_LOCAL_MUTATION}
SELECTED = tuple(e for e in CATALOGUE.entries if e.effect_class in TEXT_MUTATION_EFFECTS)
CASES = tuple((e, role) for e in SELECTED for role in ROLES.get(e.command_id, ()))


def test_catalogue_has_an_explicit_text_role_decision_for_every_mutation():
    actual = {e.command_id for e in SELECTED}
    assert actual == set(ROLES) | set(NO_TEXT), 'Unknown or obsolete mutation: declare realistic TEXT roles or a concrete non-text reason'
    assert not set(ROLES) & set(NO_TEXT)
    assert all(reason.strip() for reason in NO_TEXT.values())
    other = {e.command_id for e in CATALOGUE.entries if e.effect_class not in TEXT_MUTATION_EFFECTS | {EffectClass.NONE}}
    assert other == set(OTHER_MUTATIONS), 'Unknown mutating effect command needs an explicit scope decision'
    for roles in ROLES.values():
        assert roles and len({r.name for r in roles}) == len(roles)


def refresh(root):
    compile_router.persist_compiled_router(str(root), compile_router.compile(str(root)))


def invoke(root, command, arguments, *, dry_run=False):
    entry = next(e for e in CATALOGUE.entries if e.command_id == command)
    request = current_request_resolver().resolve(command, arguments)
    options = {}
    if command == 'shaping.render':
        from _application.context import Capability
        from _application.types import Availability
        from _command_interface.context import BoundProvider
        options = {'providers': (BoundProvider('document_renderer'),),
                   'capabilities': (Capability('document_renderer', Availability.AVAILABLE),)}
    if command in {'workspace.setup', 'workspace.unregister'}:
        from _application.context import Capability
        from _application.types import Availability
        from _command_interface.context import BoundProvider
        options = {'providers': (BoundProvider('caller_filesystem'),),
                   'capabilities': (Capability('caller_filesystem', Availability.AVAILABLE),),
                   'workspace_dir': root.parent / 'sweep-caller'}
    return application_for(root, profile='operator', dependency_tier=entry.dependency_tier,
                           dry_run=dry_run, **options).invoke(request)


def setup(root, entry, role):
    """Return a real role path and a post-seeding request factory."""
    command = entry.command_id
    relative = role.path
    if role.scenario == 'backlink':
        target = DESIGN if command == 'artefact.rename' else PLAN if command == 'artefact.set-naming-field' else CANDIDATE
        (root / BACKLINK).parent.mkdir(parents=True, exist_ok=True)
        (root / BACKLINK).write_text('---\ntype: living/wiki\nkey: sweep-backlink\ntags: []\n---\n# Sweep Backlink\n', encoding='utf-8')
        with (root / BACKLINK).open('a', encoding='utf-8') as stream:
            stream.write('\n[[' + target.removesuffix('.md') + ']]\n')
    if command == 'artefact.repair':
        path = root / CANDIDATE
        before = path.read_text(encoding='utf-8')
        boundary = before.index('---', 3) + 3
        path.write_text(before[:boundary] + '\n\n---\ntags: [sweep-repaired]\n---\n' + before[boundary:], encoding='utf-8')
    if command == 'artefact.migrate-naming':
        (root / CANDIDATE).rename(root / relative)
    if role.scenario == 'owned-child':
        result = invoke(root, 'artefact.create', {'type': 'living/design', 'title': 'Sweep Child', 'key': 'sweep-child', 'parent': 'idea/command-fixture-candidate', 'content': INLINE})
        assert result.status == 'ok', result
        relative = result.result.path
    if role.scenario == 'unarchive':
        result = invoke(root, 'artefact.archive', {'path': CANDIDATE})
        assert result.status == 'ok', result
        relative = result.result.new_path
    if role.scenario == 'plugin':
        result = invoke(root, 'plugin.create', {'name': 'Command Plugin', 'content': INLINE})
        assert result.status == 'ok', result
    if role.scenario == 'type':
        from test_type_definition_mutation_owners import TYPE_DEFINITION, TYPE_TEMPLATE
        result = invoke(root, 'type.create', {'name': 'widgets', 'classification': 'living', 'definition': {'source': 'inline', 'content': TYPE_DEFINITION}, 'template': {'source': 'inline', 'content': TYPE_TEMPLATE}})
        assert result.status == 'ok', result
    if role.scenario == 'sync':
        result = invoke(root, 'type.sync', {'type_key': 'living/journals'})
        assert result.status == 'ok', result
        import sync_definitions
        # Model an installed previous source revision, rather than customised
        # text with an unchanged upstream (which legitimately returns skip).
        path = root / relative
        old_revision = path.read_text(encoding='utf-8') + '\nPreviously installed source revision.\n'
        path.write_text(old_revision, encoding='utf-8')
        tracking = sync_definitions.load_tracking(str(root))
        tracked_role = 'taxonomy' if role.name == 'local-definition' else 'template'
        tracking['installed']['living/journals']['files'][tracked_role]['source_hash'] = (
            hashlib.sha256(path.read_bytes()).hexdigest())
        sync_definitions.save_tracking(str(root), tracking)
    if role.scenario == 'resource':
        router = compile_router.compile(str(root))
        import create
        (root / create.config_resource_rel_path(router, 'template', 'projects')).unlink()
    if role.scenario.startswith('mixed-'):
        import vault_registry
        from _bootstrap.workspace_binding import save_workspace_manifest_data
        caller = root.parent / 'sweep-caller'
        caller.mkdir()
        vault_registry.register(root, 'command-vault')
        save_workspace_manifest_data(caller, {'brain': 'command-vault', 'slug': 'sweep',
                                             'links': {'workspace': 'sweep'},
                                             'defaults': {'tags': ['sweep']}})
        if role.scenario == 'mixed-hub':
            result = invoke(root, 'workspace.ensure-registration', {'key': 'sweep', 'title': 'Sweep'})
            assert result.status == 'ok', result
    if role.scenario == 'workspace':
        result = invoke(root, 'workspace.ensure-registration', {'key': 'sweep', 'title': 'Sweep'})
        assert result.status == 'ok', result
    if role.scenario == 'transcript':
        result = invoke(root, 'shaping.start', {'target': 'design/command-fixture-design', 'mode': 'refine'})
        assert result.status == 'ok', result
        relative = result.result.transcript_path
    if role.scenario == 'trigger' and command != 'trigger.create':
        result = invoke(root, 'trigger.create', {'condition': 'When sweeping text', 'target': 'Projects/Command Fixture'})
        assert result.status == 'ok', result
    if role.scenario == 'document':
        # Append a unique selection without changing valid resource frontmatter.
        with (root / relative).open('a', encoding='utf-8') as stream:
            stream.write('\n## Sweep Selection\n\nSweep original wording.\n')
    if role.scenario == 'links':
        with (root / relative).open('a', encoding='utf-8') as stream:
            stream.write('\nSweep broken reference: [[command-fixture]]\n')
    refresh(root)

    def request():
        path = root / relative
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lifecycle = {
            'artefact.archive': {'path': CANDIDATE},
            'artefact.convert': {'path': CANDIDATE, 'target_type': 'design', 'parent': 'project/command-fixture'},
            'artefact.delete': {'path': DESIGN},
            'artefact.migrate-naming': {},
            'artefact.rename': {'source': DESIGN, 'dest': 'Designs/project~command-fixture/Sweep Renamed.md'},
            'artefact.repair': {'scope': 'frontmatter'},
            'artefact.reparent': {'path': CANDIDATE, 'parent': 'project/command-fixture'},
            'artefact.reparent-children': {'source': 'project/command-fixture', 'mode': 'top-level'},
            'artefact.set-key': {'path': CANDIDATE, 'key': 'sweep-renamed'},
            'artefact.set-naming-field': {'path': PLAN, 'field': 'created', 'value': '2026-08-10T09:30:00+10:00'},
            'artefact.set-status': {'path': CANDIDATE, 'status': 'adopted'},
            'artefact.set-workspace': {'path': CANDIDATE, 'workspace_context': 'workspace/sweep'},
            'artefact.unarchive': {'path': relative},
        }
        if command in lifecycle:
            return lifecycle[command]
        if command.startswith('document.'):
            resource, reference, _ = next(x for x in DOCUMENTS if x[0] == role.name)
            args = {'document': {'resource': resource, 'reference': reference}, 'expected_revision': document_revision_at(path)}
            args.update({
                'document.write-body': {'operation': 'replace', 'content': INLINE},
                'document.replace-text': {'old_text': 'Sweep original wording.', 'new_text': MARKER, 'match': {'mode': 'unique'}},
                'document.update-frontmatter': {'updates': {'sweep_reviewed': True}},
                'document.structured-edit': {'change': {'operation': 'replace', 'selection': {'kind': 'heading', 'text': 'Sweep Selection', 'level': 2, 'part': 'body'}, 'content': INLINE}},
            }[command])
            return args
        if command == 'plugin.replace':
            return {'name': 'Command Plugin', 'content': INLINE, 'expected_sha256': digest}
        if command == 'type.replace':
            from test_type_definition_mutation_owners import TYPE_DEFINITION, TYPE_TEMPLATE
            return {'name': 'widgets', 'classification': 'living', 'definition': {'source': 'inline', 'content': TYPE_DEFINITION + '\n' + MARKER}, 'template': {'source': 'inline', 'content': TYPE_TEMPLATE + '\n' + MARKER}, 'expected_sha256': hashlib.sha256((root / '_Config/Taxonomy/Living/widgets.md').read_bytes()).hexdigest(), 'expected_template_sha256': hashlib.sha256((root / '_Config/Templates/Living/Widgets.md').read_bytes()).hexdigest()}
        if command.startswith('trigger.'):
            args = {'condition': 'When sweeping text', 'target': 'Projects/Command Fixture'}
            if command == 'trigger.replace':
                args['new_condition'] = 'After sweeping text'
            return args
        return {
            'artefact.create': {'type': 'living/idea', 'title': 'Sweep Created', 'content': INLINE, 'key': 'sweep-created'},
            'content.ingest': {'content': INLINE, 'type_key': 'ideas', 'title': 'Sweep Created' if role.scenario == 'ingest-create' else 'Command Fixture Candidate', 'mode': 'bm25_only'},
            'resource.create': {'target': {'resource': 'template', 'name': 'projects'}, 'content': {'source': 'inline', 'content': '---\ntype: living/project\ntags: []\n---\n# Sweep Template\n'}},
            'links.fix': {'path': DESIGN},
            'type.sync': {'type_key': 'living/journals', 'force': True},
            'vault.repair-text': {'paths': [relative]},
            'shaping.start': {'target': 'design/command-fixture-design', 'mode': 'refine'},
            'shaping.render': {'source': DESIGN, 'slug': 'sweep', 'output': {'kind': 'presentation', 'preview': False}, 'render': False},
            'workspace.ensure-registration': {'key': 'sweep'},
            'workspace.update-policy': {'workspace': 'workspace/sweep', 'default_tags': ['sweep']},
            'workspace.setup': {'brain_id': 'command-vault', 'slug': 'sweep'},
            'workspace.unregister': {'key': 'sweep'},
        }[command]
    return relative, request


def text_files(root):
    """Observe authored files, excluding operational and managed installation state."""
    files = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*.md')
            if not any(part in {'.brain', '.brain-core', '.git', '.obsidian'} for part in p.relative_to(root).parts)}
    return files



def refusal_files(root):
    files = text_files(root)
    shared = root / '.brain'
    if shared.exists():
        files.update({p.relative_to(root).as_posix(): p.read_bytes()
                      for p in shared.rglob('*') if p.is_file()
                      and 'local' not in p.relative_to(shared).parts})
    for relative in ('.brain/local/config.yaml', '.brain/local/workspaces.json'):
        if (root / relative).is_file():
            files[relative] = (root / relative).read_bytes()
    return files


def verify_success(root, command, role, relative, result):
    """Validate application claims against the persisted result, not a mock."""
    payload = result.result
    if command == 'artefact.delete':
        assert not (root / DESIGN).exists()
        return
    if command == 'vault.repair-text' and not payload.files:
        assert (root / relative).is_file()
        return
    path = getattr(payload, 'new_path', None) or getattr(payload, 'path', None)
    if path:
        assert (root / path).is_file(), result
    if command.startswith('document.'):
        persisted = (root / payload.path).read_text(encoding='utf-8')
        assert payload.revision == document_revision_at(root / payload.path)
        if command == 'document.update-frontmatter':
            from _common import parse_frontmatter
            assert str(parse_frontmatter(persisted)[0]['sweep_reviewed']).lower() == 'true'
        else:
            assert MARKER in persisted
    elif command == 'links.fix':
        persisted = (root / relative).read_text(encoding='utf-8')
        assert 'Sweep broken reference: [[Command Fixture]]' in persisted, result
        assert '[[command-fixture]]' not in persisted, result
    elif command == 'type.sync':
        selected = next(item for item in payload.updated if item.target == relative)
        assert selected.action != 'skip', result
        persisted = (root / relative).read_bytes()
        assert b'Previously installed source revision.' not in persisted, result
        assert b'Encoding sentinel:' not in persisted, result
    elif command == 'content.ingest':
        assert MARKER in (root / payload.path).read_text(encoding='utf-8')
    elif command == 'plugin.replace':
        assert (root / payload.path).read_text(encoding='utf-8') == INLINE['content']
    elif command == 'type.replace':
        assert MARKER in (root / payload.path).read_text(encoding='utf-8')
        assert MARKER in (root / payload.template_path).read_text(encoding='utf-8')
    elif command in {'workspace.setup', 'workspace.unregister'}:
        from _bootstrap.workspace_binding import read_workspace_manifest
        import workspace_registry
        caller = root.parent / 'sweep-caller'
        manifest = read_workspace_manifest(caller)
        registry = workspace_registry.load_registry(root)
        if command == 'workspace.setup':
            assert manifest['brain'] == 'command-vault'
            assert manifest['links']['workspace'] == 'sweep'
            assert registry['sweep']['path'] == str(caller)
            assert (root / payload.registration.path).is_file()
        else:
            assert 'brain' not in manifest and 'workspace' not in manifest.get('links', {})
            assert 'sweep' not in registry
    elif command == 'shaping.start':
        assert (root / payload.target_path).is_file()
        assert (root / payload.transcript_path).is_file()
    elif command == 'artefact.migrate-naming':
        assert not (root / relative).exists()
        assert (root / 'Ideas/Sweep Legacy.md').is_file()
    elif command == 'artefact.repair':
        from _common import parse_frontmatter
        fields, body = parse_frontmatter((root / relative).read_text(encoding='utf-8'))
        assert 'sweep-repaired' in fields['tags']
        assert 'tags: [sweep-repaired]' not in body
    elif command == 'artefact.reparent-children':
        assert payload.children and payload.moves
        for move in payload.moves:
            assert (root / move.new_path).is_file()


def verify_explicit_truncated_repair(result, relative, raw, prefix, suffix, *, planned):
    assert result.status == 'ok', result
    assert result.result.dry_run is planned
    item, = result.result.files
    assert (item.path, item.code, item.status) == (
        relative, 'truncated_utf8', 'planned' if planned else 'changed')
    assert (item.before_bytes, item.after_bytes) == (len(raw), len(prefix))
    assert item.dropped_hex == suffix.hex(' ')
    assert item.dropped_windows1252 == suffix.decode('cp1252', errors='backslashreplace')
    warning_prefix = (f'{relative}: truncated_utf8 drops {suffix.hex(" ")} '
                      f'(Windows-1252: {item.dropped_windows1252}).')
    assert any(w.code is WarningCode.FOLLOW_UP_REQUIRED
               and warning_prefix in w.message
               and 'Content after the cut may already be lost' in w.message
               for w in result.warnings), result
    if planned:
        assert not result.committed_effects
    else:
        assert [(effect.kind, effect.subject) for effect in result.committed_effects] == [
            ('vault.repair-text', relative)]

@pytest.mark.parametrize('entry,role', CASES, ids=[e.command_id + ':' + r.name for e, r in CASES])
@pytest.mark.parametrize('encoding', ('utf-8', 'utf-16', 'truncated-utf8'))
def test_catalogue_mutation_text_role(command_vault_clone, entry, role, encoding):
    root = command_vault_clone.vault_root
    relative, request = setup(root, entry, role)
    path = root / relative
    original = path.read_text(encoding='utf-8')
    text = original + '\nEncoding sentinel: café 🦘\n'
    if entry.command_id == 'vault.repair-text':
        # The approved byte repair preserves existing line endings too.
        text = text.replace('\n', '\r\n')
    raw = text.encode('utf-16') if encoding == 'utf-16' else text.encode('utf-8')
    if encoding == 'truncated-utf8':
        raw += '🦘'.encode('utf-8')[:-1]
    path.write_bytes(raw)
    # Recompile real sources. In particular, UTF-16 identity skipping must not
    # be masked by a stale pre-corruption canonical index.
    # Router source corruption must be handled by the invoked command, rather
    # than by a fixture-side builder which refuses that source first.
    if relative != ROUTER:
        try:
            refresh(root)
        except (NonStandardVaultTextError, UnreadableRouterSourceError):
            # Strict builder refusal leaves the real cache untouched. Invoke
            # the application anyway: only its named refusal can pass.
            pass
    before = text_files(root)
    refused_before = refusal_files(root)
    arguments = request()
    if entry.command_id == 'vault.repair-text' and encoding == 'utf-8':
        # Explicit path selection requires a finding; the healthy control is
        # the documented automatic selection, which must be a genuine no-op.
        arguments = {}
    if entry.command_id == 'vault.repair-text' and encoding == 'truncated-utf8':
        suffix = '🦘'.encode('utf-8')[:-1]
        prefix = raw[:-len(suffix)]
        assert prefix == text.encode('utf-8')
        preview = invoke(root, entry.command_id, arguments, dry_run=True)
        verify_explicit_truncated_repair(preview, relative, raw, prefix, suffix, planned=True)
        assert path.read_bytes() == raw
        assert refusal_files(root) == refused_before
    result = invoke(root, entry.command_id, arguments)
    label = f'{entry.command_id}/{role.name}/{encoding}: {result}'
    if encoding == 'utf-8':
        assert result.status == 'ok', 'Invalid fixture/control: ' + label
        verify_success(root, entry.command_id, role, relative, result)
        if entry.command_id == 'vault.repair-text':
            assert not result.result.files and not result.committed_effects
            assert text_files(root) == before
        return
    diagnosis = 'utf16_bom' if encoding == 'utf-16' else 'truncated_utf8'
    if entry.command_id == 'vault.repair-text' and encoding == 'truncated-utf8':
        verify_explicit_truncated_repair(result, relative, raw, prefix, suffix, planned=False)
        persisted = path.read_bytes()
        persisted.decode('utf-8', errors='strict')
        assert persisted == prefix and raw == persisted + suffix
        after = refusal_files(root)
        assert {p for p in after if after[p] != refused_before.get(p)} == {relative}
        assert set(after) == set(refused_before)
        return
    if result.status == 'error':
        assert result.error.code is ErrorCode.CONFLICT, label
        assert relative in result.error.message and diagnosis in result.error.message, label
        assert result.error.next_action is not None, label
        assert result.effects == 'none', label
        assert path.read_bytes() == raw, label
        assert refusal_files(root) == refused_before, 'Refusal changed persisted files: ' + label
        return
    if role.read_only:
        # Application opt-in may consume lossless text without rewriting its
        # read-only template/type source. The default seam remains strict;
        # named refusal above is equally valid. Truncation cannot be consumed.
        assert encoding == 'utf-16', 'Lossy read-only input must be refused: ' + label
        assert result.status == 'ok', label
        verify_success(root, entry.command_id, role, relative, result)
        assert path.read_bytes() == raw, 'Read-only source was rewritten: ' + label
        assert any(w.code is WarningCode.FOLLOW_UP_REQUIRED
                   and relative in w.message and diagnosis in w.message
                   for w in result.warnings), 'Unreported lossless source consumption: ' + label
        return
    assert encoding == 'utf-16', 'Lossy text must be refused: ' + label
    assert result.status == 'ok', label
    verify_success(root, entry.command_id, role, relative, result)
    after = text_files(root)
    changed = {p for p, content in after.items() if before.get(p) != content}
    assert changed, 'Command ignored the declared TEXT role: ' + label
    warnings = '\n'.join(w.message for w in result.warnings if w.code is WarningCode.FOLLOW_UP_REQUIRED)
    assert relative in warnings and diagnosis in warnings, label
    converted = {p for p in changed if 'Encoding sentinel: café 🦘' in after[p].decode('utf-8', errors='strict')}
    # The damaged source must be converted or removed by the actual transition;
    # an unrelated successful write cannot satisfy this role.
    if path.exists():
        persisted = path.read_bytes()
        persisted.decode('utf-8', errors='strict')
        assert persisted != raw and not persisted.startswith(codecs.BOM_UTF8), label
        converted.add(relative)
    else:
        assert converted or entry.command_id == 'artefact.delete', label
    assert all(p in warnings for p in converted), 'Conversion warnings omit written paths: ' + label
    for p in converted:
        assert not after[p].startswith((codecs.BOM_UTF8, codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)), label


@pytest.mark.parametrize('encoding', ('utf-16', 'truncated-utf8'))
def test_rename_accounts_for_every_converted_collateral_path(command_vault_clone, encoding):
    root = command_vault_clone.vault_root
    entry = next(e for e in SELECTED if e.command_id == 'artefact.rename')
    relative, request = setup(root, entry, next(r for r in ROLES[entry.command_id] if r.name == 'backlink'))
    second = 'Wiki/Sweep Second Backlink.md'
    (root / second).write_text((root / relative).read_text(encoding='utf-8').replace('key: sweep-backlink', 'key: sweep-second-backlink'), encoding='utf-8')
    refresh(root)
    raw_sources = {}
    for selected in (relative, second):
        text = (root / selected).read_text(encoding='utf-8') + '\nValid multibyte: café 🦘\n'
        raw = text.encode('utf-16') if encoding == 'utf-16' else text.encode('utf-8') + b'\xf0\x9f\xa6'
        raw_sources[selected] = raw
        (root / selected).write_bytes(raw)
    refresh(root)
    before = refusal_files(root)
    result = invoke(root, entry.command_id, request())
    if result.status == 'error':
        diagnosis = 'utf16_bom' if encoding == 'utf-16' else 'truncated_utf8'
        assert result.error.code is ErrorCode.CONFLICT, result
        assert diagnosis in result.error.message, result
        assert any(p in result.error.message for p in raw_sources), result
        assert result.error.next_action is not None, result
        assert result.effects == 'none', result
        assert refusal_files(root) == before, result
        for p, raw in raw_sources.items():
            assert (root / p).read_bytes() == raw
        return
    assert encoding == 'utf-16', 'Truncated collateral must be refused before any rename or rewrite'
    assert result.status == 'ok', result
    assert not (root / DESIGN).exists()
    assert (root / result.result.new_path).is_file()
    for p, raw in raw_sources.items():
        persisted = (root / p).read_bytes()
        text = persisted.decode('utf-8', errors='strict')
        assert persisted != raw and not persisted.startswith(codecs.BOM_UTF8)
        assert 'Sweep Renamed' in text and 'Command Fixture Design' not in text
        assert any(w.code is WarningCode.FOLLOW_UP_REQUIRED and p in w.message and 'utf16_bom' in w.message for w in result.warnings), (p, result)


def test_reparent_children_selects_living_children_and_leaves_temporal_plan_unchanged(command_vault_clone):
    """edit.plan_reparent_children promises direct living children, not temporal ownership."""
    root = command_vault_clone.vault_root
    plan_before = (root / PLAN).read_bytes()
    from _common import parse_frontmatter
    assert parse_frontmatter(plan_before.decode('utf-8'))[0]['parent'] == 'project/command-fixture'
    result = invoke(root, 'artefact.reparent-children', {
        'source': 'project/command-fixture', 'mode': 'top-level'})
    assert result.status == 'ok', result
    assert (root / PLAN).read_bytes() == plan_before
    assert PLAN not in {child.old_path for child in result.result.children}
    assert PLAN not in {move.old_path for move in result.result.moves}
    assert DESIGN in {child.old_path for child in result.result.children}
    moved = next(move for move in result.result.moves if move.old_path == DESIGN)
    assert not (root / DESIGN).exists()
    fields, _ = parse_frontmatter((root / moved.new_path).read_text(encoding='utf-8'))
    assert fields['type'] == 'living/design' and 'parent' not in fields



def test_workspace_unregister_leaves_canonical_hub_markdown_unchanged(command_vault_clone):
    """Unregister mutates operational registry/binding state, not canonical TEXT."""
    root = command_vault_clone.vault_root
    entry = next(e for e in SELECTED if e.command_id == 'workspace.setup')
    relative, request = setup(root, entry, next(r for r in ROLES[entry.command_id]
                                              if r.name == 'existing-hub'))
    registered = invoke(root, 'workspace.setup', request())
    assert registered.status == 'ok', registered
    before = (root / relative).read_bytes()
    result = invoke(root, 'workspace.unregister', {'key': 'sweep'})
    assert result.status == 'ok', result
    verify_success(root, 'workspace.unregister', None, relative, result)
    assert (root / relative).read_bytes() == before
    assert all(effect.subject != relative for effect in result.committed_effects)


def test_fix_links_aggregate_truncation_names_every_path_and_repair_action(command_vault_clone):
    root = command_vault_clone.vault_root
    paths = ('Wiki/Truncated One.md', 'Wiki/Truncated Two.md')
    for relative in paths:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = 'No links here. Café. '.encode('utf-8') + b'\xe2\x82'
        from _common._text_encoding import diagnose_text
        assert diagnose_text(raw).code == 'truncated_utf8'
        path.write_bytes(raw)
    refresh(root)
    before = refusal_files(root)
    result = invoke(root, 'links.fix', {})
    assert result.status == 'error', result
    assert result.error.code is ErrorCode.CONFLICT
    assert all(relative in result.error.message for relative in paths), result
    assert 'truncated_utf8' in result.error.message
    assert result.error.next_action.command_id == 'vault.repair-text'
    assert result.effects == 'none'
    assert refusal_files(root) == before


@pytest.mark.parametrize('scoped', (False, True))
@pytest.mark.parametrize('raw,code', ((b'legacy \xff', 'not_utf8'),
                                    ('Café '.encode() + b'\xe2\x82', 'truncated_utf8')))
def test_link_observation_skips_but_mutation_refuses_nonstandard_sources(command_vault_clone, scoped, raw, code):
    root = command_vault_clone.vault_root
    relative = 'Wiki/Nonstandard Link Source.md'
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    refresh(root)
    before = refusal_files(root)
    arguments = {'path': relative} if scoped else {}
    observed = invoke(root, 'links.check', arguments)
    assert observed.status == 'ok', observed
    assert refusal_files(root) == before
    for dry_run in (False, True):
        refused = invoke(root, 'links.fix', arguments, dry_run=dry_run)
        assert refused.status == 'error', refused
        assert refused.error.code is ErrorCode.CONFLICT
        assert relative in refused.error.message and code in refused.error.message
        assert refused.effects == 'none'
        assert refusal_files(root) == before
