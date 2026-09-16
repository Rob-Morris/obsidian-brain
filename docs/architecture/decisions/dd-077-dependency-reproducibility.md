# DD-077: One resolved dependency contract for contributors and managed runtimes

**Status:** Implemented (v0.68.7); native release certification is a separate gate
**Extends:** DD-048, DD-070

## Context

Exact direct pins still allowed transitive packages to change at installation.
Contributor tools and semantic provisioning repeated intent in several places.
The old base-manifest hash omitted semantic pins, so releases could share and
mutate one environment despite requiring different semantic versions. Import
probes did not detect an installed package with the wrong version.

## Decision

Use one virtual dependency project, one uv lock and four generated pip exports.
The generator pin lives only in the project's dev group. Explicit resolver
configuration, scrubbed `UV_*` state, PyPI lock-source validation, no source
builds and no Python downloads bound generation. Offline staged checks execute
the staged generator and compare locked exports; LF attributes make their bytes
stable across checkout platforms. See the [contributor contract](../../contributor/dependencies.md).

The stdlib `_common._venv` owner hashes a versioned domain, ordered filenames
and complete LF-normalised bytes of both shipped runtime exports. It excludes
contributor exports and the universal lock. A semantic-only change rotates the
shared environment even for a base-only Brain; semantic installation remains
opt-in. Older runtimes are retained, so rolling back selects the older contract.

Every installation consumes a complete export with `pip --no-deps`. Fresh
installation, upgrade dependency sync, semantic provisioning and explicit
runtime repair compare all applicable installed versions and run `pip check`.
The target interpreter evaluates markers, including compatible-minor reuse.
Base repair preserves an already authorised semantic extension. The readiness
sentinel records its verification schema, contract digest, interpreter path/tag
and semantic state. Failed conformance invalidates verification; semantic intent
can remain without verification so a retry does not forget the extension.
Healthy handoff trusts matching readiness without pip or a distribution audit.

Required native CPython 3.12 certification covers macOS arm64, Linux x86_64 and
Windows x86_64, base and semantic. Wheel resolution alone is insufficient.
Other Python 3.12+ environments are best-effort. This corrects DD-070's broad
wheel assumption: the current ONNX Runtime pin has no Intel macOS wheel.

## Alternatives and limits

Independent per-platform locks would add authorities without demonstrated
need. Constraints without complete exports could omit transitives. Exact
base-only environment syncing would remove the optional semantic extension.
A new semantic process or mandatory user package manager would expand scope.
The selected contract preserves existing install/scaffold recovery, explicit
upgrade dependency opt-outs, model identity and launcher ownership.

`upgrade.py --no-sync-deps` applies the core files but defers every later
phase that could create or conform the managed runtime: MCP registration
repair, retrieval-asset repair and runtime warm-up. Its structured result and
human output carry explicit follow-up commands; an opt-out cannot report
dependency work as skipped and then provision packages indirectly.

This is dependency-version reproducibility, not byte-identical installations,
offline availability or package-content integrity certification.
