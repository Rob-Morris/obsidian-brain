"""Build, seal and validate a local direct-main recovery plan."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Callable, Mapping

import release
from . import candidates, git, workflow
from .model import (PromotionError, SOURCE_TRAILER, TIP_TRAILER, ZERO_SHA,
                    parse_trailers, promotion_branch, version_tuple)
from .recovery_model import (RESULT_SCHEMA, SCHEMA, RecoveryPlan, decode_plan,
                             RecoveryEntry, canonical_origin, local_plan_ref,
                             plan_ref, result_ref)

CheckRunner = Callable[[Path], None]
_NOTE_TYPE = re.compile(r"^\*\*Type:\*\* (.+)$", re.MULTILINE)
_NOTE_BULLET = re.compile(r"^- (.+)$", re.MULTILINE)
_DATE_ROW = re.compile(r"^\| \[v(?P<version>\d+\.\d+\.\d+)\]\(changelog/v[^)]+\) \| (?P<date>\d{4}-\d{2}-\d{2}) \| (?P<summary>[^|]+) \|$", re.MULTILINE)


def _read(root: Path, rev: str, path: str) -> str:
    return git.run(root, "show", f"{rev}:{path}").stdout


def _note_input(root: Path, candidate: str, version: str) -> tuple[str, str, str, str, list[str]]:
    note = _read(root, candidate, f"docs/changelog/v{version}.md")
    summary = release.release_summary(note, version)
    release_type = _NOTE_TYPE.search(note)
    changes = _NOTE_BULLET.findall(note)
    index = _read(root, candidate, release.CHANGELOG_INDEX_PATH)
    rows = [match for match in _DATE_ROW.finditer(index) if match["version"] == version]
    if not summary or not release_type or not changes or len(rows) != 1:
        raise PromotionError(f"old v{version} has unsupported release-note input")
    if rows[0]["summary"].strip() != summary:
        raise PromotionError(f"old v{version} changelog Summary differs from its note")
    return note, summary, release_type.group(1), rows[0]["date"], changes


def _body(message: str) -> str:
    marker = f"\n\n{SOURCE_TRAILER}: "
    if marker not in message:
        raise PromotionError("old candidate has no provenance trailer block")
    _subject, separator, body = message.split(marker, 1)[0].partition("\n\n")
    if not separator or not body.strip():
        raise PromotionError("old candidate has no authored commit body")
    return body.rstrip() + "\n"


def _mappings(value: Mapping[str, Mapping[str, str]] | None) -> dict[str, dict[str, str]]:
    if value is None:
        return {"core": {}, "cli": {}, "proxy": {}}
    if not isinstance(value, Mapping):
        raise PromotionError("recovery version mapping must be an object")
    if set(value) - {"core", "cli", "proxy"}:
        raise PromotionError("recovery request permits only core, cli and proxy version maps")
    result = {"core": {}, "cli": {}, "proxy": {}}
    for kind, mapping in value.items():
        if not isinstance(mapping, Mapping):
            raise PromotionError(f"{kind} version map must be an object")
        for old, new in mapping.items():
            if not isinstance(old, str) or not isinstance(new, str):
                raise PromotionError(f"{kind} version mappings must use semantic-version strings")
            version_tuple(old)
            version_tuple(new)
            result[kind][old] = new
    return result


def _substitute_note(note: str, mappings: Mapping[str, Mapping[str, str]]) -> tuple[str, list[dict[str, object]]]:
    choices: dict[str, str] = {}
    for kind in ("core", "cli", "proxy"):
        for old, new in mappings[kind].items():
            if old in choices and choices[old] != new:
                raise PromotionError(f"ambiguous authored-note substitution for {old}")
            choices[old] = new
    changed = {old: new for old, new in choices.items() if old != new}
    if not changed:
        return note, []
    ordered = sorted(changed, key=lambda old: (-len(old), old))
    pattern = re.compile(rf"(?<![0-9.])(?:{'|'.join(re.escape(old) for old in ordered)})(?![0-9.])")
    counts = {old: 0 for old in ordered}

    def replacement(match: re.Match[str]) -> str:
        old = match.group(0)
        counts[old] += 1
        return changed[old]

    rendered = pattern.sub(replacement, note)
    return rendered, [{"old": old, "new": changed[old], "count": counts[old]} for old in ordered]


def _version_boundary(root: Path, sha: str) -> bool:
    commit = git.read_commit(root, sha)
    facts = release.release_facts(root, sha)
    if not facts.coherent or not facts.core:
        return False
    if not commit.parents:
        return True
    if len(commit.parents) != 1:
        return False
    previous = release.release_facts(root, commit.parents[0])
    if not previous.coherent or not previous.core or version_tuple(facts.core) <= version_tuple(previous.core):
        return False
    path = f"docs/changelog/v{facts.core}.md"
    shown = git.run(root, "show", f"{sha}:{path}", check=False)
    if shown.returncode:
        return False
    summary = release.release_summary(shown.stdout, facts.core)
    return bool(summary and commit.subject == f"{summary} (v{facts.core})")


def _anchor(root: Path, main: str, ledger: str, *, missing_ledger: bool) -> str:
    shared = set(git.ancestry(root, ledger))
    common = [sha for sha in git.ancestry(root, main) if sha in shared]
    if not common:
        raise PromotionError("main and ledger have no common first-parent anchor")
    if not missing_ledger:
        anchor = common[0]
        if not _version_boundary(root, anchor):
            raise PromotionError("common first-parent anchor is not a published version boundary")
        return anchor
    for sha in common:
        if _version_boundary(root, sha):
            return sha
    raise PromotionError("missing unreleased and no unambiguous common version anchor")


def _identity(root: Path, main: str) -> dict[str, str]:
    roots = git.out(root, "rev-list", "--max-parents=0", main).splitlines()
    if len(roots) != 1:
        raise PromotionError("main has ambiguous repository roots")
    return {"origin": canonical_origin(root, git.out(root, "remote", "get-url", "origin")),
            "root_commit": roots[0]}


def _metadata(root: Path, main: str) -> tuple[dict[str, str], dict[str, str]]:
    date = git.out(root, "show", "-s", "--format=%cI", main)
    name, email = "Brain Promotion Recovery", "recovery@brain.invalid"
    return {"name": name, "email": email, "date": date}, git.identity_env(date, name, email)


def _helper_target(kind: str, old_base: str, old_target: str, new_base: str,
                   mappings: Mapping[str, Mapping[str, str]]) -> str:
    target = new_base if old_target == old_base else mappings[kind].get(old_target, old_target)
    if version_tuple(target) < version_tuple(new_base):
        raise PromotionError(
            f"old {kind} target {old_target} is below new base {new_base}; supply an explicit mapping"
        )
    return target


def _render_release(worktree: Path, *, version: str, summary: str, release_type: str,
                    date: str, changes: list[str], cli: str, proxy: str, note: str) -> set[str]:
    source = """
import json, sys
from pathlib import Path
root = Path.cwd()
sys.path.insert(0, str(root / 'src/scripts'))
import release
p = json.load(sys.stdin)
edits = release.prepare_release(root, core_version=p['version'], summary=p['summary'],
    release_date=p['date'], release_type=p['release_type'], changes=p['changes'],
    cli_version=p['cli'], proxy_version=p['proxy'])
path = 'docs/changelog/v' + p['version'] + '.md'
if not isinstance(edits, dict) or path not in edits or not isinstance(edits[path], str):
    raise RuntimeError('source release API did not supply its authored note map value')
edits[path] = p['note']
release.apply_release(root, edits)
print(json.dumps(sorted(edits)))
"""
    payload = {"version": version, "summary": summary, "release_type": release_type,
               "date": date, "changes": changes, "cli": cli, "proxy": proxy, "note": note}
    completed = subprocess.run([sys.executable, "-I", "-c", source], cwd=worktree,
                               input=json.dumps(payload), text=True, capture_output=True)
    if completed.returncode:
        raise PromotionError("rebuilt source release renderer failed: " + completed.stderr.strip())
    try:
        paths = json.loads(completed.stdout)
    except ValueError as exc:
        raise PromotionError("rebuilt source release renderer returned invalid paths") from exc
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        raise PromotionError("rebuilt source release renderer returned invalid paths")
    if (worktree / f"docs/changelog/v{version}.md").read_text(encoding="utf-8") != note:
        raise PromotionError(f"rebuilt v{version} note differs from the authored note")
    return set(paths)


def _scratch(root: Path, sha: str) -> Path:
    parent = root / ".worktrees"
    parent.mkdir(exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="recovery-", dir=parent))
    os.rmdir(path)
    git.run(root, "worktree", "add", "--detach", str(path), sha)
    return path


def _clean_scratch(root: Path, path: Path) -> None:
    if path.exists():
        git.run(root, "worktree", "remove", str(path))


def _commit_tree(root: Path, tree: str, parents: tuple[str, ...], message: str,
                 metadata: Mapping[str, str]) -> str:
    return git.commit_tree(root, tree, parents, message,
                           git.identity_env(metadata["date"], metadata["name"], metadata["email"]))


def _replay_tail(root: Path, base: str, start: str, dev: str,
                 metadata: Mapping[str, str]) -> str:
    tail = git.commits_after(root, start, dev)
    content = candidates.content_tail(root, start, tail)
    parent = base
    for commit in content:
        try:
            tree = git.merge_tree(root, commit.parents[0], git.tree_of(root, parent), commit.sha)
        except PromotionError as exc:
            raise PromotionError(f"uncut tail {commit.sha} conflicts from base {commit.parents[0]} onto {parent}: {exc}") from exc
        if tree == git.tree_of(root, parent):
            raise PromotionError(f"uncut tail replay would drop {commit.sha}")
        parent = git.commit_tree(root, tree, (parent,), commit.message, git.replay_env(root, commit.sha))
    if parent == dev:
        return dev
    return _commit_tree(root, git.tree_of(root, parent), (parent, dev),
                        "WIP: preserve dev history after recovery\n", metadata)


def _transaction(old: str | None, new: str | None) -> dict[str, str | None]:
    return {"old": old, "new": new}


def canonical_transactions(snapshot: Mapping[str, object], entries: tuple[RecoveryEntry, ...],
                           target_unreleased: str, target_dev: str
                           ) -> dict[str, dict[str, dict[str, str | None]]]:
    stage = {"refs/heads/recovery/{plan}/plan": _transaction(None, "{plan}")}
    abort = {"refs/heads/recovery/{plan}/result": _transaction(None, "{result:aborted}")}
    for entry in entries:
        ref = "refs/heads/" + entry.new_ref
        if ref in stage:
            raise PromotionError(f"two replacement releases require {ref}")
        stage[ref] = _transaction(entry.new_ref_old, entry.new_candidate)
        abort[ref] = _transaction(entry.new_candidate, entry.new_ref_old)
    apply = {
        "refs/heads/unreleased": _transaction(snapshot["unreleased"], target_unreleased),
        "refs/heads/dev": _transaction(snapshot["dev"], target_dev),
        "refs/heads/recovery/{plan}/result": _transaction(None, "{result:applied}"),
    }
    return {"stage": stage, "apply": apply, "abort": abort}


def build_plan(root: Path, *, version_map: Mapping[str, Mapping[str, str]] | None = None,
               run_checks: CheckRunner | None = workflow.default_checks) -> RecoveryPlan:
    """Rebuild every queued release locally and pin a sealed, reviewable plan."""
    mappings = _mappings(version_map)
    lock = git.lock(root)
    try:
        git.require_checkout(root)
        git.fetch_refs(root)
        main = git.rev(root, "refs/remotes/origin/main")
        dev = git.rev(root, "refs/remotes/origin/dev")
        if git.rev(root, "refs/heads/dev") != dev:
            raise PromotionError("local dev must match origin/dev to plan recovery")
        exists = git.rev_exists(root, "refs/remotes/origin/unreleased")
        ledger = git.rev(root, "refs/remotes/origin/unreleased") if exists else dev
        if candidates.on_first_parent_line(root, ledger, main):
            raise PromotionError("main is already on the unreleased first-parent line")
        if exists and not candidates.on_first_parent_line(root, dev, ledger):
            raise PromotionError("old unreleased is not on dev's first-parent line")
        anchor = _anchor(root, main, ledger, missing_ledger=not exists)
        if not candidates.on_first_parent_line(root, dev, anchor):
            raise PromotionError("published anchor is not on shared dev's first-parent line")
        for label, sha in (("main", main), ("anchor", anchor), ("ledger", ledger), ("dev", dev)):
            facts = release.release_facts(root, sha)
            if not facts.coherent or not facts.core:
                raise PromotionError(f"{label} {sha} has incoherent release facts")
        old_queue = git.commits_after(root, anchor, ledger) if exists else []
        if exists and not old_queue and anchor != ledger:
            raise PromotionError("old queue could not be enumerated")
        for old in old_queue:
            candidates.validate_candidate(root, old)
        metadata, _env = _metadata(root, main)
        entries: list[dict[str, object]] = []
        new_ledger = main
        base = main
        used_versions: set[str] = set()
        for old in old_queue:
            old_parent = old.parents[0]
            trailers = parse_trailers(old.message)
            old_source, old_tip = trailers[SOURCE_TRAILER], trailers[TIP_TRAILER]
            old_facts = release.release_facts(root, old.sha)
            parent_facts = release.release_facts(root, old_parent)
            base_facts = release.release_facts(root, new_ledger)
            if not all((old_facts.core, old_facts.cli_unix, old_facts.proxy,
                        parent_facts.cli_unix, parent_facts.proxy,
                        base_facts.core, base_facts.cli_unix, base_facts.proxy)):
                raise PromotionError(f"v{old.sha} has incomplete release facts")
            old_version = old_facts.core
            new_version = mappings["core"].get(old_version, old_version)
            if version_tuple(new_version) <= version_tuple(base_facts.core) or new_version in used_versions:
                raise PromotionError(f"v{old_version} collides with new base v{base_facts.core}; supply explicit core mapping")
            used_versions.add(new_version)
            cli = _helper_target("cli", parent_facts.cli_unix, old_facts.cli_unix,
                                 base_facts.cli_unix, mappings)
            proxy = _helper_target("proxy", parent_facts.proxy, old_facts.proxy,
                                   base_facts.proxy, mappings)
            old_note, summary, release_type, date, changes = _note_input(root, old.sha, old_version)
            new_note, substitutions = _substitute_note(old_note, mappings)
            if release.release_summary(new_note, new_version) != summary:
                raise PromotionError(f"v{old_version} note cannot retain canonical Summary after mapping")
            try:
                source_tree = git.merge_tree(root, old_parent, git.tree_of(root, base), old_source)
            except PromotionError as exc:
                raise PromotionError(f"queued v{old_version} source {old_source} conflicts: base {old_parent}, ours {base}, theirs {old_source}: {exc}") from exc
            source = _commit_tree(root, source_tree, (base,),
                                  f"WIP: recover source for v{new_version}\n", metadata)
            if candidates.require_same_release_facts(root, new_ledger, source).core != base_facts.core:
                raise PromotionError(f"queued v{old_version} source changes release facts")
            scratch = _scratch(root, source)
            try:
                declared = _render_release(scratch, version=new_version, summary=summary,
                    release_type=release_type, date=date, changes=changes, cli=cli,
                    proxy=proxy, note=new_note)
                git.run(scratch, "add", "--", *sorted(declared))
                tree = git.out(scratch, "write-tree")
                changed = set(git.out(root, "diff-tree", "--name-only", "-r",
                                      git.tree_of(root, source), tree).splitlines())
                if changed != declared:
                    raise PromotionError(f"rebuilt v{new_version} release bundle differs from declared paths: {sorted(changed ^ declared)}")
                old_ref = promotion_branch(old_version)
                new_ref = promotion_branch(new_version)
                old_ref_sha = git.remote_branch(root, old_ref)
                if old_ref_sha not in (None, old.sha):
                    raise PromotionError(f"{old_ref} is owned by another candidate {old_ref_sha}")
                new_ref_old = git.remote_branch(root, new_ref)
                if new_ref_old not in (None, old.sha):
                    raise PromotionError(f"{new_ref} is owned by another candidate {new_ref_old}")
                message = (f"{summary} (v{new_version})\n\n{_body(old.message).rstrip()}\n\n"
                           f"{SOURCE_TRAILER}: {source}\n{TIP_TRAILER}: {source}\n")
                candidate = _commit_tree(root, tree, (new_ledger,), message, metadata)
                candidates.validate_candidate(root, git.read_commit(root, candidate), new_ref)
                if _read(root, candidate, f"docs/changelog/v{new_version}.md") != new_note:
                    raise PromotionError(f"rebuilt v{new_version} authored note changed")
                git.run(scratch, "reset", "--hard", candidate)
                if run_checks is not None:
                    run_checks(scratch)
                if git.run(scratch, "status", "--porcelain", "--untracked-files=all").stdout.strip():
                    raise PromotionError(f"checks changed rebuilt v{new_version} checkout")
            except Exception as exc:
                raise PromotionError(f"recovery scratch worktree retained at {scratch}: {exc}") from exc
            else:
                _clean_scratch(root, scratch)
            reconciliation = _commit_tree(root, tree, (candidate, source),
                f"WIP: reconcile recovered v{new_version}\n", metadata)
            entries.append({"old_candidate": old.sha, "old_source": old_source,
                "old_tip": old_tip, "old_version": old_version, "old_ref": old_ref,
                "old_ref_sha": old_ref_sha, "new_candidate": candidate,
                "new_source": source, "new_reconciliation": reconciliation,
                "new_version": new_version, "new_ref": new_ref,
                "new_ref_old": new_ref_old, "summary": summary,
                "release_type": release_type, "date": date, "body": _body(old.message),
                "old_note": old_note, "new_note": new_note, "cli_version": cli,
                "proxy_version": proxy, "release_paths": sorted(declared),
                "substitutions": substitutions})
            new_ledger, base = candidate, reconciliation
        tail_base = ledger if exists else anchor
        target_dev = _replay_tail(root, base, tail_base, dev, metadata)
        if not git.is_ancestor(root, dev, target_dev):
            raise PromotionError("rebuilt dev does not retain old shared dev")
        if not candidates.on_first_parent_line(root, target_dev, new_ledger):
            raise PromotionError("rebuilt dev does not follow its replacement ledger")
        snapshot = {"main": main, "anchor": anchor,
                    "unreleased": ledger if exists else None,
                    "unreleased_exists": exists, "dev": dev}
        transactions = canonical_transactions(snapshot,
            tuple(RecoveryEntry.from_dict(entry) for entry in entries), new_ledger, target_dev)
        manifest: dict[str, object] = {"schema": SCHEMA,
            "repository": _identity(root, main),
            "snapshot": snapshot,
            "entries": entries, "target_unreleased": new_ledger,
            "target_dev": target_dev, "version_map": mappings,
            "construction": metadata,
            "transactions": transactions}
        parents = tuple(dict.fromkeys([main, ledger, dev, new_ledger, target_dev,
            *[str(e["old_candidate"]) for e in entries],
            *[str(e["new_reconciliation"]) for e in entries]]))
        data = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")
        blob = git.run(root, "hash-object", "-w", "--stdin", input_text=data).stdout.strip()
        tree = git.run(root, "mktree", input_text=f"100644 blob {blob}\tmanifest.json\n").stdout.strip()
        sha = _commit_tree(root, tree, parents, "recovery: seal direct-main plan\n", metadata)
        pin = local_plan_ref(sha)
        if git.rev_exists(root, pin):
            if git.rev(root, pin) != sha:
                raise PromotionError(f"local recovery pin {pin} was replaced")
        else:
            git.run(root, "update-ref", pin, sha, ZERO_SHA)
        return load_plan(root, sha)
    finally:
        lock.close()


def load_plan(root: Path, sha: str) -> RecoveryPlan:
    """Load a sealed plan and verify its Git retention and transaction graph."""
    commit = git.read_commit(root, sha)
    if commit.sha != sha or commit.message != "recovery: seal direct-main plan\n":
        raise PromotionError("recovery plan commit identity is invalid")
    raw = _read(root, sha, "manifest.json")
    try:
        manifest = json.loads(raw)
    except ValueError as exc:
        raise PromotionError("recovery plan manifest is not JSON") from exc
    if not isinstance(manifest, dict):
        raise PromotionError("recovery plan manifest is not an object")
    plan = decode_plan(sha, manifest)
    if (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n") != raw:
        raise PromotionError("recovery plan manifest encoding is not canonical")
    repository = manifest["repository"]
    if repository != _identity(root, str(plan.snapshot["main"])):
        raise PromotionError("recovery plan belongs to a different repository")
    expected_anchor = _anchor(root, str(plan.snapshot["main"]),
        str(plan.snapshot["unreleased"] or plan.snapshot["dev"]),
        missing_ledger=not bool(plan.snapshot["unreleased_exists"]))
    if plan.snapshot["anchor"] != expected_anchor:
        raise PromotionError("recovery plan anchor differs from shared first-parent history")
    construction = plan.manifest["construction"]
    if set(construction) != {"name", "email", "date"} or any(
            not isinstance(item, str) or not item for item in construction.values()):
        raise PromotionError("recovery construction metadata is malformed")
    if construction != _metadata(root, str(plan.snapshot["main"]))[0]:
        raise PromotionError("recovery construction metadata differs from the main snapshot")
    targets = [plan.snapshot["main"], plan.snapshot["anchor"], plan.snapshot["dev"],
               plan.target_unreleased, plan.target_dev]
    if plan.snapshot["unreleased"] is not None:
        targets.append(plan.snapshot["unreleased"])
    for entry in plan.entries:
        targets.extend([entry.old_candidate, entry.old_source, entry.old_tip,
                        entry.new_candidate, entry.new_source, entry.new_reconciliation])
        candidates.validate_candidate(root, git.read_commit(root, entry.old_candidate), entry.old_ref)
        candidates.validate_candidate(root, git.read_commit(root, entry.new_candidate), entry.new_ref)
        if _read(root, entry.new_candidate, f"docs/changelog/v{entry.new_version}.md") != entry.new_note:
            raise PromotionError(f"recovery plan v{entry.new_version} note is not retained")
    expected_parents = tuple(dict.fromkeys([str(plan.snapshot["main"]),
        str(plan.snapshot["unreleased"] or plan.snapshot["dev"]),
        str(plan.snapshot["dev"]), plan.target_unreleased, plan.target_dev,
        *[entry.old_candidate for entry in plan.entries],
        *[entry.new_reconciliation for entry in plan.entries]]))
    if commit.parents != expected_parents:
        raise PromotionError("recovery plan parents differ from its old/new graph roots")
    for target in targets:
        if not git.is_ancestor(root, str(target), sha):
            raise PromotionError(f"recovery plan does not retain {target}")
    if not git.is_ancestor(root, str(plan.snapshot["dev"]), plan.target_dev):
        raise PromotionError("recovery plan dev target drops old shared dev")
    if not candidates.on_first_parent_line(root, plan.target_dev, plan.target_unreleased):
        raise PromotionError("recovery plan dev target is not based on its ledger")
    if not git.is_ancestor(root, str(plan.snapshot["anchor"]), str(plan.snapshot["main"])):
        raise PromotionError("recovery anchor is not on main")
    old_parent = str(plan.snapshot["anchor"])
    new_parent = str(plan.snapshot["main"])
    expected_old_tip = str(plan.snapshot["unreleased"] or plan.snapshot["anchor"])
    candidates.require_same_release_facts(root, expected_old_tip, str(plan.snapshot["dev"]))
    listed = [commit.sha for commit in git.commits_after(root, old_parent, expected_old_tip)]
    if listed != [entry.old_candidate for entry in plan.entries]:
        raise PromotionError("recovery queue omits or reorders an old ledger commit")
    source_base = new_parent
    mappings = _mappings(plan.manifest["version_map"])  # type: ignore[arg-type]
    for entry in plan.entries:
        old_commit = git.read_commit(root, entry.old_candidate)
        new_commit = git.read_commit(root, entry.new_candidate)
        source = git.read_commit(root, entry.new_source)
        reconciliation = git.read_commit(root, entry.new_reconciliation)
        if old_commit.parents != (old_parent,) or new_commit.parents != (new_parent,):
            raise PromotionError("recovery queue is not an ordered linear ledger")
        if source.parents != (source_base,):
            raise PromotionError("recovery source does not descend from the preceding reconciliation")
        reconstructed_tree = git.merge_tree(root, old_parent, git.tree_of(root, source_base), entry.old_source)
        if source.tree != reconstructed_tree:
            raise PromotionError("recovery source tree differs from explicit-base reconstruction")
        if reconciliation.parents != (entry.new_candidate, entry.new_source) or reconciliation.tree != new_commit.tree:
            raise PromotionError("recovery reconciliation does not retain its source")
        trailers = parse_trailers(new_commit.message)
        if trailers.get(SOURCE_TRAILER) != entry.new_source or trailers.get(TIP_TRAILER) != entry.new_source:
            raise PromotionError("recovery candidate is not bound to its synthetic source")
        old_trailers = parse_trailers(old_commit.message)
        if old_trailers.get(SOURCE_TRAILER) != entry.old_source or old_trailers.get(TIP_TRAILER) != entry.old_tip:
            raise PromotionError("recovery entry differs from old candidate provenance")
        if entry.old_ref != promotion_branch(entry.old_version) or entry.new_ref != promotion_branch(entry.new_version):
            raise PromotionError("recovery candidate ref differs from its version")
        if entry.old_ref_sha not in (None, entry.old_candidate) or entry.new_ref_old not in (None, entry.old_candidate):
            raise PromotionError("recovery candidate ref replacement is not owned by its old version")
        if _body(new_commit.message) != entry.body:
            raise PromotionError("recovery candidate changed the authored commit body")
        old_facts = release.release_facts(root, entry.old_candidate)
        old_parent_facts = release.release_facts(root, old_parent)
        new_base_facts = release.release_facts(root, new_parent)
        if (old_facts.core != entry.old_version or
                mappings["core"].get(entry.old_version, entry.old_version) != entry.new_version):
            raise PromotionError("recovery version mapping differs from candidate facts")
        if not all((old_parent_facts.cli_unix, old_parent_facts.proxy,
                    old_facts.cli_unix, old_facts.proxy,
                    new_base_facts.cli_unix, new_base_facts.proxy)):
            raise PromotionError("recovery helper facts are incomplete")
        if entry.cli_version != _helper_target("cli", old_parent_facts.cli_unix,
                old_facts.cli_unix, new_base_facts.cli_unix, mappings):
            raise PromotionError("recovery CLI intent differs from release facts")
        if entry.proxy_version != _helper_target("proxy", old_parent_facts.proxy,
                old_facts.proxy, new_base_facts.proxy, mappings):
            raise PromotionError("recovery proxy intent differs from release facts")
        new_facts = release.release_facts(root, entry.new_candidate)
        if new_facts.cli_unix != entry.cli_version or new_facts.proxy != entry.proxy_version:
            raise PromotionError("recovery candidate helper facts differ from approved intent")
        note, summary, release_type, date, _changes = _note_input(root, entry.old_candidate,
                                                                    entry.old_version)
        new_note, substitutions = _substitute_note(note, mappings)
        if (entry.old_note != note or entry.new_note != new_note or
                list(entry.substitutions) != substitutions or entry.summary != summary or
                entry.release_type != release_type or entry.date != date or
                entry.body != _body(old_commit.message)):
            raise PromotionError("recovery authored release input differs from its source")
        changed = set(git.out(root, "diff-tree", "--name-only", "-r",
                              source.tree, new_commit.tree).splitlines())
        if changed != set(entry.release_paths):
            raise PromotionError("recovery candidate bundle differs from declared paths")
        old_parent = entry.old_candidate
        new_parent = entry.new_candidate
        source_base = entry.new_reconciliation
    if old_parent != expected_old_tip or new_parent != plan.target_unreleased:
        raise PromotionError("recovery queue endpoints differ from the sealed targets")
    if plan.transactions != canonical_transactions(plan.snapshot, plan.entries,
                                                     plan.target_unreleased, plan.target_dev):
        raise PromotionError("recovery transactions differ from the sealed queue")
    rebuilt_dev = _replay_tail(root, source_base, expected_old_tip,
                               str(plan.snapshot["dev"]), plan.manifest["construction"])
    if rebuilt_dev != plan.target_dev:
        raise PromotionError("recovery dev target differs from its deterministic tail replay")
    return plan


def make_result(root: Path, plan: RecoveryPlan, outcome: str) -> str:
    if outcome not in {"applied", "aborted"}:
        raise PromotionError("recovery result must be applied or aborted")
    payload = {"schema": RESULT_SCHEMA, "plan": plan.sha, "outcome": outcome}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    blob = git.run(root, "hash-object", "-w", "--stdin", input_text=raw).stdout.strip()
    tree = git.run(root, "mktree", input_text=f"100644 blob {blob}\tresult.json\n").stdout.strip()
    return _commit_tree(root, tree, (plan.sha,), f"recovery: {outcome} {plan.sha}\n",
                        plan.manifest["construction"])  # type: ignore[arg-type]


def validate_result(root: Path, plan: RecoveryPlan, sha: str) -> str:
    commit = git.read_commit(root, sha)
    if commit.parents != (plan.sha,):
        raise PromotionError("recovery result does not parent the sealed plan")
    try:
        payload = json.loads(_read(root, sha, "result.json"))
    except ValueError as exc:
        raise PromotionError("recovery result is not JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema") != RESULT_SCHEMA or payload.get("plan") != plan.sha:
        raise PromotionError("recovery result does not name this plan")
    outcome = payload.get("outcome")
    if outcome not in {"applied", "aborted"} or make_result(root, plan, outcome) != sha:
        raise PromotionError("recovery result is not the deterministic outcome")
    return outcome


def resolve_updates(root: Path, plan: RecoveryPlan, phase: str) -> dict[str, dict[str, str | None]]:
    if phase not in {"stage", "apply", "abort"}:
        raise PromotionError(f"unknown recovery phase {phase}")
    raw = plan.transactions[phase]
    if not isinstance(raw, dict):
        raise PromotionError("recovery transaction is malformed")
    result: dict[str, dict[str, str | None]] = {}
    for ref, value in raw.items():
        if not isinstance(ref, str) or not isinstance(value, dict) or set(value) != {"old", "new"}:
            raise PromotionError("recovery transaction update is malformed")
        name = ref.replace("{plan}", plan.sha)
        new = value["new"]
        if new == "{plan}":
            new = plan.sha
        elif new == "{result:applied}":
            new = make_result(root, plan, "applied")
        elif new == "{result:aborted}":
            new = make_result(root, plan, "aborted")
        result[name] = {"old": value["old"], "new": new}
    return result
