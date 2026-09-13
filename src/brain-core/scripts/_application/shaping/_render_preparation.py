"""Preparation binds dated render outputs, reused markdown and provider choices."""

from datetime import datetime
from pathlib import Path
import shutil

from ..preparation import ObservedResource, OperationPreparation, bind_operation, canonical_json, content_digest


def render_owner(request):
    if request.output.kind == "presentation":
        import shape_presentation as owner
        options = {"preview": request.output.preview}
    else:
        import shape_printable as owner
        options = {"keep_heading_with_next": request.output.keep_heading_with_next,
                   "pdf_engine": request.output.pdf_engine}
        options = {key: value for key, value in options.items() if value is not None}
    return owner, {"source": request.source, "slug": request.slug, "render": request.render, **options}


def render_plan(context, request, *, frozen_inputs=None):
    frozen = dict(frozen_inputs or {})
    frozen.setdefault("effective_at", context.clock.now().isoformat())
    owner, params = render_owner(request)
    plan = owner.plan_shape(context.selected_brain.vault_root, params,
                            effective_at=datetime.fromisoformat(frozen["effective_at"]))
    return plan, frozen


def render_binding(context, request, *, plan, frozen_inputs=None):
    from _common import parse_frontmatter
    from _portable.maintenance_inputs import file_identity
    root = context.selected_brain.vault_root
    owner, params = render_owner(request)
    folder = "Presentations" if plan.kind == "presentation" else "Printables"
    pdf = f"_Assets/Generated/{folder}/{Path(plan.path).stem}.pdf"
    observations = [ObservedResource("render-source", plan.source, file_identity(root / plan.source)),
                    ObservedResource("render-document", plan.path, file_identity(root / plan.path)),
                    ObservedResource("render-content", plan.path, content_digest(plan.content))]
    if request.render:
        observations.append(ObservedResource("render-pdf", pdf, file_identity(root / pdf)))
    support, tools = {}, {}
    if plan.kind == "presentation":
        support["theme"] = owner._resolve_theme_path(str(root))
        if request.render or request.output.preview:
            tools["marp"] = shutil.which("marp")
    elif request.render:
        fields, _body = parse_frontmatter(plan.content)
        tool_paths = owner._load_tool_paths(str(root))
        tools["pandoc"] = owner._resolve_tool_path("pandoc", tool_paths)[0]
        engine = params.get("pdf_engine") or fields.get("pdf_engine")
        _name, tools["pdf_engine"], _warnings = owner._select_pdf_engine(engine, tool_paths)
        support = {name: owner._resolve_support_path(str(root), name)
                   for name in ("base.tex", "keep-headings.tex")}
        observations.append(ObservedResource("render-config", "tool-paths", content_digest(canonical_json(tool_paths))))
    for kind, values in (("render-support", support), ("render-tool", tools)):
        for name, value in values.items():
            path = Path(value) if value else None
            observations.append(ObservedResource(kind, name, content_digest(canonical_json(
                {"path": str(path.resolve()) if path else None,
                 "revision": file_identity(path.resolve()) if path else None}))))
    return bind_operation(request, observations=observations, frozen_inputs=frozen_inputs,
                          review={"operation": "render-" + plan.kind, "document": plan.path,
                                  "create": plan.created, "pdf": pdf if request.render else None,
                                  "preview": getattr(request.output, "preview", False), "tools": tools,
                                  "content_sha256": content_digest(plan.content)})


def prepare_render(context, request, *, frozen_inputs=None):
    from _common import vault_mutation_lock
    with vault_mutation_lock(context.selected_brain.vault_root):
        plan, frozen = render_plan(context, request, frozen_inputs=frozen_inputs)
        return render_binding(context, request, plan=plan, frozen_inputs=frozen)


RENDER = OperationPreparation(prepare_render)
