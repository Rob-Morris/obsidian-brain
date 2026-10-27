"""Repository-only template validation; publish definitions, never runtime projections."""
from dataclasses import replace
from pathlib import Path
import shutil
import subprocess
import sys


def assemble(repo: Path, destination: Path) -> None:
    # Use the repository's authored-file inventory, not a recursive Core copy:
    # ignored interpreters, caches and models are not installation source.
    inventory = subprocess.check_output(
        ['git', '-C', str(repo), 'ls-files', '--cached', '--others',
         '--exclude-standard', '-z', '--', 'src/brain-core/', 'template-vault/'])
    selected = {Path(name) for name in inventory.decode().split('\0') if name}
    for relative in sorted(selected):
        if relative.parts[:2] == ('src', 'brain-core'):
            target = Path('.brain-core', *relative.parts[2:])
        elif relative.parts[0] == 'template-vault':
            target = Path(*relative.parts[1:])
            if target.parts[:1] == ('.brain-core',) or target.parts[:2] == ('.brain', 'local'):
                continue
        else:
            raise ValueError('Unexpected repository source selection')
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unexpected repository source selection')
        source = repo / relative
        # Deleted indexed files are absent from the working source, as in Git's
        # normal working-tree semantics. Other redirects are never source grants.
        if not source.exists() and not source.is_symlink():
            continue
        from _bootstrap.owned_paths import owned_directory, owned_file
        if source.is_symlink():
            # Authored bootstrap aliases are source inputs, not installed-vault
            # capabilities. Materialise only another admitted authored file.
            owned_directory(repo, relative.parent)
            try:
                canonical = source.resolve(strict=True).relative_to(repo.resolve())
            except (OSError, RuntimeError, ValueError):
                raise ValueError('Authored source alias leaves the selected repository inventory') from None
            if canonical not in selected:
                raise ValueError('Authored source alias leaves the selected repository inventory')
            source = owned_file(repo, canonical)
        else:
            source = owned_file(repo, relative)
        output = destination / target
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, output)


def apply(repo: Path, assembly: Path) -> None:
    from _bootstrap.owned_paths import owned_output_paths
    from sync_definitions import plan_sync_definitions, apply_definition_sync
    import compile_router

    plan = plan_sync_definitions(str(assembly), force=True)
    if plan.result.get('errors'):
        raise ValueError('Template definition planning failed')
    # Validate all planned source-derived bytes and the complete assembled vault
    # before publishing anything to the repository template.
    apply_definition_sync(str(assembly), plan)
    compile_router.compile(str(assembly))
    actual = repo / 'template-vault'
    outputs = []
    for item in plan.copies:
        relative = Path(item.target)
        if relative.is_absolute() or '..' in relative.parts or relative.parts[:1] != ('_Config',):
            raise ValueError('Template sync selected a non-definition output')
        outputs.append(relative)
    owned_output_paths(actual, (*outputs, '.brain/tracking.json'))
    # Starter content directories and ephemeral-root router/session projections
    # are not repository sync outputs. Keep every unrelated template file intact.
    apply_definition_sync(str(actual), replace(plan, folders=()))


if __name__ == '__main__':
    mode, root, destination = sys.argv[1:]
    root, destination = Path(root), Path(destination)
    sys.path.insert(0, str(root / 'src/brain-core/scripts'))
    if mode == 'assemble':
        assemble(root, destination)
    elif mode == 'apply':
        apply(root, destination)
    else:
        raise SystemExit('Unknown template source assembly operation')
