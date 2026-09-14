#!/usr/bin/env python3
"""Open a shaping session for an existing, shapeable artefact."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import sys

import edit
from _common import (
    build_vault_file_index,
    extract_title,
    extract_wikilinks,
    find_vault_root,
    match_artefact,
    MissingFileResult,
    MutationLockError,
    parse_frontmatter,
    PartialApplyError,
    public_mutation_error_message,
    read_file_content,
    reconcile_fields_for_render,
    render_filename_or_default,
    resolve_and_validate_folder,
    resolve_folder,
    safe_write_artefact,
    serialize_frontmatter,
    substitute_template_vars,
    strip_md_ext,
    terminal_status_folder,
    unique_filename,
    vault_mutation_lock,
)
from _lifecycle.derived_cache_state import load_fresh_compiled_router


SHAPING_MODES = ("brainstorm", "refine", "discover")
TRANSCRIPT_TYPE_KEY = "shaping-transcripts"


@dataclass(frozen=True)
class _PreparedShapingTarget:
    resolved_path: str
    artefact: dict
    fields: dict
    body: str
    status_behaviour: str


def _normalise_stem(path):
    stem = strip_md_ext(path)
    return os.path.normpath(stem).replace(os.sep, "/")


def _resolve_link_path(stem, file_index):
    """Resolve an existing full-path or basename wikilink without guessing."""
    normalised = _normalise_stem(stem)
    basename = os.path.basename(normalised).lower()
    candidates = file_index["md_basenames"].get(basename, [])
    if "/" in normalised:
        candidates = [
            path
            for path in candidates
            if _normalise_stem(path).lower() == normalised.lower()
        ]
    if len(candidates) != 1:
        return None
    return os.path.normpath(candidates[0]).replace(os.sep, "/")


def _transcript_names_source(content, source_path, file_index):
    expected_path = os.path.normpath(source_path).replace(os.sep, "/")
    for line in content.splitlines():
        if line.startswith("**Source:**"):
            return any(
                _resolve_link_path(link["stem"], file_index) == expected_path
                for link in extract_wikilinks(line)
            )
    return False


def _transcript_source_count(content, file_index):
    """Return the number of distinct source links declared by a transcript."""
    for line in content.splitlines():
        if not line.startswith("**Source:**"):
            continue
        return len(
            {
                (
                    "resolved",
                    resolved.casefold(),
                )
                if (
                    resolved := _resolve_link_path(link["stem"], file_index)
                )
                else (
                    "unresolved",
                    _normalise_stem(link["stem"]).casefold(),
                )
                for link in extract_wikilinks(line)
            }
        )
    return 0


def _transcript_artefact(router):
    artefact = match_artefact(router.get("artefacts", []), TRANSCRIPT_TYPE_KEY)
    if not artefact:
        raise ValueError("Shaping transcript type is not configured")
    if not artefact.get("naming"):
        raise ValueError("Shaping transcript type has no compiled naming contract")
    if not artefact.get("frontmatter_type"):
        raise ValueError("Shaping transcript type has no compiled frontmatter type")
    return artefact


def _transcript_layout(artefact, router, title, now):
    fields = {
        "created": now.isoformat(),
        "modified": now.isoformat(),
        "type": artefact["frontmatter_type"],
    }
    reconcile_fields_for_render(fields, artefact)
    folder = resolve_folder(artefact, router=router)
    filename = render_filename_or_default(artefact["naming"], title, fields)
    return folder, filename, fields


def _is_same_day_transcript(rel_path, artefact, folder, fields):
    expected_folder = os.path.normpath(folder).replace(os.sep, "/")
    actual_folder = os.path.normpath(os.path.dirname(rel_path)).replace(os.sep, "/")
    if actual_folder != expected_folder:
        return False
    filename = os.path.basename(rel_path)
    title = extract_title(artefact["naming"], fields, filename)
    if title is None:
        title = ""
    return render_filename_or_default(artefact["naming"], title, fields) == filename


def _linked_transcripts(
    body,
    vault_root,
    source_path,
    file_index,
    transcript_artefact,
    folder,
    layout_fields,
):
    matches = []
    for line in body.splitlines():
        if not line.startswith("**Transcripts:**"):
            continue
        for link in extract_wikilinks(line):
            rel_path = _resolve_link_path(link["stem"], file_index)
            if rel_path is None:
                continue
            if not _is_same_day_transcript(
                rel_path, transcript_artefact, folder, layout_fields
            ):
                continue
            abs_path = os.path.join(vault_root, rel_path)
            if not os.path.isfile(abs_path):
                continue
            with open(abs_path, "r", encoding="utf-8") as handle:
                transcript = handle.read()
            if not _transcript_names_source(transcript, source_path, file_index):
                continue
            matches.append(rel_path)
    return sorted(set(matches))


def _choose_transcript_path(
    vault_root,
    source_path,
    source_body,
    artefact_title,
    now,
    file_index,
    transcript_artefact,
    router,
):
    folder, filename, layout_fields = _transcript_layout(
        transcript_artefact, router, artefact_title, now
    )
    linked = _linked_transcripts(
        source_body,
        vault_root,
        source_path,
        file_index,
        transcript_artefact,
        folder,
        layout_fields,
    )
    if len(linked) > 1:
        source_counts = {}
        for rel_path in linked:
            transcript_abs = os.path.join(vault_root, rel_path)
            with open(transcript_abs, "r", encoding="utf-8") as handle:
                source_counts[rel_path] = _transcript_source_count(
                    handle.read(),
                    file_index,
                )
        widest_count = max(source_counts.values())
        widest = [
            rel_path
            for rel_path, count in source_counts.items()
            if count == widest_count
        ]
        if len(widest) == 1:
            return widest[0], True
        raise ValueError(
            f"Multiple shaping transcripts are linked to '{source_path}' for today "
            f"with equally wide source sets: " + ", ".join(widest)
        )
    if linked:
        return linked[0], True

    stem = os.path.splitext(filename)[0]
    candidate = os.path.join(folder, filename)
    candidate_abs = os.path.join(vault_root, candidate)
    if os.path.isfile(candidate_abs):
        with open(candidate_abs, "r", encoding="utf-8") as handle:
            existing = handle.read()
        if _transcript_names_source(existing, source_path, file_index):
            return candidate, True
        filename = unique_filename(os.path.join(vault_root, folder), stem)
        candidate = os.path.join(folder, filename)
    return candidate, False


def _read_transcript_template(vault_root, artefact):
    template_path = artefact.get("template_file")
    if not template_path:
        raise FileNotFoundError(
            "Shaping transcript type has no configured template_file"
        )
    content = read_file_content(vault_root, template_path)
    if isinstance(content, MissingFileResult):
        raise FileNotFoundError(
            f"Shaping transcript template not found at '{template_path}'"
        )
    return content


def _add_transcript_link(
    abs_path,
    vault_root,
    fields,
    body,
    transcript_stem,
    transcript_display,
    modified,
):
    link = f"[[{transcript_stem}|{transcript_display}]]"
    fields["modified"] = modified
    content = serialize_frontmatter(fields, body=body)
    lines = content.split("\n")
    for index, line in enumerate(lines):
        if not line.startswith("**Transcripts:**"):
            continue
        linked_stems = {
            _normalise_stem(item["stem"]).lower()
            for item in extract_wikilinks(line)
        }
        if (
            _normalise_stem(transcript_stem).lower() in linked_stems
            or os.path.basename(_normalise_stem(transcript_stem)).lower()
            in linked_stems
        ):
            return False
        lines[index] = line.rstrip() + f" {link}"
        safe_write_artefact(abs_path, "\n".join(lines), bounds=vault_root)
        return True

    if not body.strip():
        new_content = content.rstrip() + f"\n\n**Transcripts:** {link}\n"
    else:
        body_lines = body.split("\n")
        insert_at = 0
        while insert_at < len(body_lines) and not body_lines[insert_at].strip():
            insert_at += 1
        body_lines.insert(insert_at, f"**Transcripts:** {link}\n")
        new_content = serialize_frontmatter(fields, body="\n".join(body_lines))
    safe_write_artefact(abs_path, new_content, bounds=vault_root)
    return True


def _validate_mode(requested_mode):
    if requested_mode not in SHAPING_MODES:
        raise ValueError(
            f"Invalid shaping mode '{requested_mode}'. Valid modes: "
            + ", ".join(SHAPING_MODES)
        )
    return requested_mode


def _prepare_shaping_target(vault_root, router, target):
    """Resolve and read a shapeable target once for a session mutation."""
    vault_root = str(vault_root)
    if not isinstance(target, str) or not target.strip():
        raise ValueError("target must be a non-empty string")

    resolved_path, artefact = resolve_and_validate_folder(
        vault_root, router, target.strip()
    )
    source_content = read_file_content(vault_root, resolved_path)
    if isinstance(source_content, MissingFileResult):
        raise FileNotFoundError(f"Cannot read target: {source_content}")
    fields, body = parse_frontmatter(source_content)

    shaping = artefact.get("shaping")
    if not shaping:
        raise ValueError(
            f"Artefact '{resolved_path}' is not shapeable; its type must declare "
            "a complete ## Shaping and lifecycle contract"
        )
    status_behaviour = shaping.get("status_behaviour", "transition")
    if (
        status_behaviour == "preserve"
        and terminal_status_folder(artefact, fields) is not None
    ):
        raise ValueError(
            f"Artefact '{resolved_path}' has terminal status "
            f"'{fields['status']}'. Set an explicit non-terminal status before "
            "opening a status-preserving shaping session."
        )
    return _PreparedShapingTarget(
        resolved_path,
        artefact,
        fields,
        body,
        status_behaviour,
    )


def plan_shaping_session(vault_root, router, target, *, mode, _now=None, _prepared_target=None,
                         chosen_transcript=None):
    """Resolve transcript naming and lifecycle effects without writing anything."""
    vault_root = str(vault_root)
    prepared = _prepared_target or _prepare_shaping_target(
        vault_root, router, target
    )
    resolved_path = prepared.resolved_path
    artefact = prepared.artefact
    fields = prepared.fields
    body = prepared.body
    file_index = build_vault_file_index(vault_root)
    session_mode = _validate_mode(mode)

    now = _now or datetime.now(timezone.utc).astimezone()
    source_filename = os.path.basename(resolved_path)
    artefact_title = None
    if artefact.get("naming"):
        artefact_title = extract_title(artefact["naming"], fields, source_filename)
    if artefact_title is None:
        artefact_title = os.path.splitext(source_filename)[0]
    transcript_artefact = _transcript_artefact(router)
    transcript_type = transcript_artefact["frontmatter_type"]
    transcript_path, transcript_exists = _choose_transcript_path(
        vault_root,
        resolved_path,
        body,
        artefact_title,
        now,
        file_index,
        transcript_artefact,
        router,
    )
    if chosen_transcript is not None and not transcript_exists:
        transcript_path = chosen_transcript
        if os.path.exists(os.path.join(vault_root, transcript_path)):
            raise ValueError("Prepared transcript destination is no longer available")
    template = (
        None
        if transcript_exists
        else _read_transcript_template(vault_root, transcript_artefact)
    )
    transcript_abs = os.path.join(vault_root, transcript_path)

    status_behaviour = prepared.status_behaviour
    status_changed = (
        status_behaviour == "transition"
        and fields.get("status") != "shaping"
    )
    lifecycle = (edit.plan_lifecycle_field(vault_root, router, resolved_path,
                                           "status", "shaping", effective_at=now.isoformat())
                 if status_changed else None)
    return {"prepared": prepared, "now": now, "file_index": file_index,
            "mode": session_mode, "transcript_type": transcript_type,
            "transcript_path": transcript_path, "transcript_exists": transcript_exists,
            "template": template, "status_changed": status_changed, "lifecycle": lifecycle}


def start_shaping_session(
    vault_root,
    router,
    target,
    *,
    mode,
    _now=None,
    _prepared_target=None,
    _plan=None,
):
    """Validate and apply the mechanical opening of one shaping session."""
    vault_root = str(vault_root)
    plan = _plan or plan_shaping_session(vault_root, router, target, mode=mode,
                                         _now=_now, _prepared_target=_prepared_target)
    prepared, now = plan["prepared"], plan["now"]
    resolved_path, artefact = prepared.resolved_path, prepared.artefact
    fields, body = prepared.fields, prepared.body
    file_index = plan["file_index"]
    session_mode, transcript_type = plan["mode"], plan["transcript_type"]
    transcript_path, transcript_exists = plan["transcript_path"], plan["transcript_exists"]
    template = plan["template"]
    transcript_abs = os.path.join(vault_root, transcript_path)
    status_behaviour, status_changed = prepared.status_behaviour, plan["status_changed"]
    lifecycle_applied = False
    transcript_changed = False
    target_path = resolved_path
    try:
        if status_changed:
            lifecycle = edit.apply_artefact_transition(vault_root, plan["lifecycle"])
            lifecycle_applied = True
            target_path = lifecycle["path"]
            refreshed = read_file_content(vault_root, target_path)
            if isinstance(refreshed, MissingFileResult):
                raise PartialApplyError(
                    f"shaping session partially applied — lifecycle updated "
                    f"'{resolved_path}' but the resolved target cannot be read at "
                    f"'{target_path}'"
                )
            fields, body = parse_frontmatter(refreshed)
        session_heading = (
            f"\n\n## {session_mode.capitalize()} session start — "
            f"{now.strftime('%H:%M')}\n"
        )
        if transcript_exists:
            with open(transcript_abs, "r", encoding="utf-8") as handle:
                transcript_content = handle.read()
            safe_write_artefact(
                transcript_abs,
                transcript_content.rstrip() + session_heading,
                bounds=vault_root,
            )
            transcript_changed = True
            transcript_operation = "appended"
        else:
            source_stem = os.path.splitext(target_path)[0]
            source_display = os.path.splitext(os.path.basename(target_path))[0]
            transcript_content = substitute_template_vars(
                template,
                {
                    "SOURCE_DOC_PATH|SOURCE_DOC_TITLE": (
                        f"{source_stem}|{source_display}"
                    ),
                    "SOURCE_DOC_PATH": source_stem,
                    "SOURCE_DOC_TITLE": source_display,
                    "SOURCE_TYPE": artefact["key"],
                },
                _now=now,
            )
            safe_write_artefact(
                transcript_abs,
                transcript_content.rstrip() + session_heading,
                bounds=vault_root,
            )
            transcript_changed = True
            transcript_operation = "created"

        transcript_stem = os.path.splitext(transcript_path)[0]
        transcript_display = os.path.splitext(os.path.basename(transcript_path))[0]
        backlink_changed = _add_transcript_link(
            os.path.join(vault_root, target_path),
            vault_root,
            fields,
            body,
            transcript_stem,
            transcript_display,
            now.isoformat(),
        )
    except PartialApplyError:
        raise
    except Exception as exc:
        if not lifecycle_applied and not transcript_changed:
            raise
        durable = [target_path] if lifecycle_applied else []
        if transcript_changed:
            durable.append(transcript_path)
        raise PartialApplyError(
            "shaping session partially applied — durable files "
            f"{durable}; session write failed: {exc}"
        ) from exc

    target_changed = status_changed or backlink_changed
    changed_paths = [transcript_path]
    if target_changed:
        changed_paths.insert(0, target_path)
    return {
        "status": "ok",
        "resolved_target_path": resolved_path,
        "target_path": target_path,
        "target_path_changed": target_path != resolved_path,
        "transcript_path": transcript_path,
        "type": transcript_type,
        "mode": session_mode,
        "status_behaviour": status_behaviour,
        "status_changed": status_changed,
        "transcript_operation": transcript_operation,
        "changed_paths": changed_paths,
    }


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Open a shaping session for an existing artefact."
    )
    parser.add_argument("--target", required=True)
    parser.add_argument("--mode", required=True, choices=SHAPING_MODES)
    parser.add_argument("--vault")
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    vault_root = str(find_vault_root(args.vault))
    router = load_fresh_compiled_router(vault_root)
    if "error" in router:
        raise SystemExit(f"Error: {router['error']}")
    try:
        with vault_mutation_lock(vault_root):
            result = start_shaping_session(
                vault_root,
                router,
                args.target,
                mode=args.mode,
            )
    except (
        MutationLockError,
        PartialApplyError,
        ValueError,
        FileNotFoundError,
        OSError,
    ) as exc:
        print(f"Error: {public_mutation_error_message(exc)}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
