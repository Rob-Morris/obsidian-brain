# Brainstorm workflow

Use brainstorm when a convergent artefact has too little shape for concrete decisions. Explore collaboratively until its purpose, boundaries, and likely decision surface are clear enough to hand to refine.

<HARD-GATE>
Do not write code, scaffold a project, or take implementation action until a design has been presented and the user has approved it.
</HARD-GATE>

If the parent workflow has not supplied an artefact path and transcript path, return to the root workflow and run `references/assess.md` first.

## Shape synthesis loop

1. **Understand context.** Read the vault router with `vault.read-router()` and relevant existing artefacts. Help decompose the idea if it contains independent artefacts.
2. **Ask one clarifying question** about purpose, constraints, success, boundaries, or a genuinely consequential approach. Focus on what and why before how, and follow the shared Q&A rules loaded during assessment.
3. **Apply every answer broadly.** Update all affected sections and related in-scope artefacts rather than only the section or prompt currently in view.
4. **Refresh the working shape.** Reconsider purpose, intended users, boundaries, constraints, success criteria, established assumptions, unknowns, and emerging approaches. Retire questions already answered, narrow the rest, and identify factual work the agent can do.
5. **Put evidence first.** Perform bounded vault or repository inspection when it could collapse or materially reframe a question. This is artefact-definition work, not implementation.
6. **Follow useful momentum.** Prefer high-leverage unknowns, but continue a thread the user is engaging with when it remains productive. Make concise adjacent suggestions rather than forcing a checklist.
7. **Promote mature work.** When a genuine material trade-off becomes concrete, add it to the canonical shaping-decisions table with a stable ID. Put agent-owned definition or verification work in the shaping-work table. Do not force premature rows during open exploration.
8. **Record material synthesis.** Use `### Reconciliation Rn` for material body propagation, scope changes, candidate-prompt additions or transitions, automatic narrowing, or promotions. The transcript owns the mutation history; the source artefact holds the resulting current shape. Do not duplicate the event in another audit artefact or log ordinary copy-editing.

When several approaches remain genuinely viable, present two or three with material trade-offs and a recommendation. When evidence leaves one sensible approach, explain and apply it under the normal authority rules instead of inventing alternatives.

Present a design in sections scaled to complexity. Apply established content directly. Ask for confirmation only when a section embodies a material interpretation that could reasonably diverge from the user's intent; otherwise use a compact synthesis checkpoint after a coherent group of sections.

## Handoff to refine

When the artefact's purpose, boundaries, and decision/work surface are explicit enough for refine, it has reached a candidate handoff point. It should either contain at least one concrete decision or work item, or explicitly have no remaining agenda and be ready for refine's exit handling. Brainstorm never applies the artefact's completion status directly.

Use the recommended A/B/C four-Cs offer in `references/review.md`. State at a high level why the shape meets the handoff bar, recommend review first, and offer B to skip review and hand off or C to stop without handoff. Selecting B returns directly to the root workflow and loads `references/refine.md`; do not ask for handoff twice. Only accepted findings enter shaping state. If accepted findings require more exploration, re-enter the shape synthesis loop; if they expose mature decisions or work, promote them. After fixes, assess the shape again and use the same A/B/C offer.

## Red flags

- Jumping to approaches before understanding the problem
- Asking several substantive questions in one turn
- Writing code or scaffolding before design approval
- Presenting strawman alternatives where one option is clearly better
- Requiring serial approval for content already established
- Forgetting to reconcile affected content and the remaining exploration
- Forgetting the transcript and material-reconciliation audit
