# DD-069: Compose portable shaping with a narrow Brain adaptor

**Status:** Implemented (v0.62.2)
**Extends:** DD-058, DD-068

## Context

Shaping must work in Brain and in environments with no Brain integration. Its
question flow and routing therefore belong to a portable source, while Brain
still needs to contribute version-matched artefact resolution, taxonomy,
persistence, provenance, and lifecycle capabilities. Copying and then editing
the same workflow in both repositories would create two behavioural owners.
Loading a sibling checkout or network source at runtime would make installed
vaults incomplete and non-reproducible.

Brain availability also does not imply that every session record belongs in
Brain. A repository document, its decision sidecar, a scratch transcript, and a
Brain artefact can coexist in one shaping session. Treating integration as one
session-wide switch would override explicit user choices.

## Decision

The portable `agent-skills/shaping` package is the behavioural source of truth.
Brain materialises its exact files into the shipped core package at release
time. The contributor tool verifies the source checkout's origin, HEAD, clean
shaping path, exact committed file membership, and Git blobs before recording
the repository, revision, file mapping, and SHA-256 identities in
`portable-provenance.json`. Installed clients use only the checked-in package;
they never read the source checkout or network at runtime.

The single public Brain `shaping/SKILL.md` is a Brain-owned composition loader.
It loads the portable workflow, then a separate `references/brain.md` adaptor.
The adaptor supplies capabilities and recommended defaults independently for
the artefact, decision/work record, transcript, and lifecycle. The proposed
session plan shows those selections before any write, and explicit user choices
win over Brain defaults. When a proposed flow may create a Brain record, the
adaptor preflights both Contributor `artefact.create` and Maintainer
`runtime.refresh-router` availability before confirmation so it cannot strand a
partially started session behind an authority ceiling.

The global Claude and Codex discovery adapters remain Brain-owned loaders under
DD-058 and DD-068. They continue resolving the effective skill from the active
Brain and are not copies of the portable source.

## Consequences

- Portable behaviour has one owner and can be checked for byte-level fidelity.
- Brain-specific conventions stay version-matched without becoming portable
  requirements.
- A connected Brain never selects Brain persistence by itself.
- Artefact, decision/work, transcript, and lifecycle locations remain separate
  plan decisions.
- Mixed plans use explicit operations: `artefact.set-status` owns Brain
  lifecycle without a Brain transcript, while normal shaping-transcript
  creation and provenance mutations own a Brain transcript without lifecycle.
- The Brain standard owns command effects and lifecycle semantics; the adaptor
  owns only the confirmed role-combination to operation selection.
- Upgrades narrowly replace the previous shipped shaping-transcript router
  condition while preserving customised router entries.
- Updating the portable source is an explicit contributor materialisation step,
  which refuses unmapped upstream files and removes only stale destinations
  owned by the previous provenance, followed by review and the normal Brain
  release process.
- Core skill Git lifecycle metadata remains separate: this vendored dependency
  is not advertised as a user-updatable core source that would omit the Brain
  adaptor.
