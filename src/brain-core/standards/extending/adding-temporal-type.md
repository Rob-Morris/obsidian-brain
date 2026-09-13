# Adding a Temporal Artefact Type

A type is defined by a folder under `_Temporal/`, a taxonomy, a template, and (almost always) a router trigger. For upstream contributions to brain-core, also add an artefact-library bundle.

> See [when-to-add-type.md](when-to-add-type.md) before creating one — content that is one-off or fits an existing folder usually doesn't need a new type.

## Steps

1. **Folder** — create `_Temporal/{Plural}/` at vault root. Title Case plural (`Logs`, `Plans`, `Reports`, `Decision Logs`). The compiler derives the type key from the folder name (lowercased, spaces → hyphens), so `Decision Logs/` becomes type key `decision-logs` and registered type `temporal/decision-logs`. See [naming-conventions.md](../naming-conventions.md).
2. **Taxonomy file** — create `_Config/Taxonomy/Temporal/{key}.md` (use the lowercased-hyphenated key) with the sections listed below. Without a taxonomy the folder still registers as a type but is flagged `configured: false`.
3. **Template file** — create `_Config/Templates/Temporal/{Title}.md` (use the human-readable Title Case form) with default frontmatter and a section skeleton.
4. **Router trigger** — most temporal types have one. Append a one-line conditional to `_Config/router.md`: `When ... → [[_Config/Taxonomy/Temporal/{key}]]`. The compiler reads the *condition* from `_Config/router.md` and the *detail* from the taxonomy's `## Trigger` section, then merges them in the compiled router.
5. **Compile router** — `brain runtime refresh-router --request-json '{}' --json` regenerates the compiled router and folder-colour CSS (rose blend applied automatically to temporal types). Until this runs, consumers do not see the new type.
6. **Validate** — `brain vault check --request-json '{"actionable":true}' --json` flags missing taxonomy/template files, frontmatter inconsistencies, naming violations, broken wikilinks, and empty folders.
7. **Log** — record the addition in the daily note.

## Type Identifier vs Frontmatter Type

The type registered by the compiler is `temporal/{folder-key}` — usually plural (e.g. `temporal/reports`, `temporal/plans`). The `type:` field on individual artefacts is the **singular form** declared by the taxonomy's frontmatter section (e.g. `temporal/report`, `temporal/plan`). The compiler tracks both: `type` for the type entry, `frontmatter_type` for the per-artefact value.

Convention: derive the singular form by dropping the trailing `s` (reports→report, observations→observation). Hyphenated types keep the structure (idea-logs→idea-log, shaping-transcripts→shaping-transcript).

## Required Taxonomy Sections

The following section headings are parsed by `compile_router.py` and must use the exact heading text:

- **`## Purpose`** — what the type is and when to create one.
- **`## Naming`** — filename pattern and folder rules. Standard form:
  ```
  `yyyymmdd-{singular-key}~{Title}.md` in `_Temporal/{Plural}/`.
  ```
  Use `created` as the date source unless the type has a different anchor date. Date-only types like `logs` (`yyyymmdd-log.md`) are the exception, not the norm.
- **`## Frontmatter`** — schema as a YAML code block, opened with `` ```yaml `` and containing a `---` ... `---` block:
  - `type: temporal/{singular}` (always)
  - `tags: [{singular}]` or topical tags (always)
  - `status: {default}` (when the type has a lifecycle — most don't)

  Every top-level key the example shows is treated as **required** — the example is what an agent authoring without tooling reproduces. To document a genuinely optional field, add an `**Optional:**` line after the code block naming those fields (for example ``**Optional:** `status``). Omit the line when every documented field is required. Naming a field the example does not show is a compile error, and a field the `## Naming` rules match on cannot be optional.
- **`## Lifecycle`** *(when the type has a status enum)* — a Markdown table with one row per state. Examples: plans (`draft | shaping | approved | implementing | completed | deprecated | parked`), idea-logs (`open | graduated | deprecated | parked`).
- **`## Template`** — a single wikilink to the template, e.g. `[[_Config/Templates/Temporal/Reports]]` (no `.md` extension). Required for the compiler to record the template pointer.

Optional sections:

- **`## When To Use`** — orienting cue separate from `## Purpose`. Convention only; not parsed.
- **`## Trigger`** — first non-blank line is the *condition* (used to infer category: `before` / `after` / `ongoing`); the rest is *detail* shown to agents. Almost always present for temporal types; the compiler merges this with the matching conditional in `_Config/router.md`.
- **`## On Status Change`** — per-status hooks of the form: `When `status` transitions to `{value}`, set `{field}` to {expr}.` Rare for temporal types.
- **`## Shaping`** — opts the type into shaping and declares the parsed contract: `**Flavour:**` (`Convergent` or `Discovery`) and `**Bar:**`. Status behaviour defaults to `transition`, which requires a backtick-delimited `**Completion status:**`. A discovery type whose lifecycle represents an enduring domain state may instead declare `**Status behaviour:** \`preserve\``; it may omit completion status or declare one solely as the explicit exit for an artefact already in `shaping`. The compiler exposes this metadata to shaping skills. Both behaviours require `shaping` in the explicit lifecycle enum, and every declared completion status must also appear there.

## Hub Relationships

Temporal artefacts never act as hubs themselves and don't carry a `key:` field — see [keys.md](../keys.md). They can still be **owned** by a living hub via `parent: {type}/{key}`, filing flat under that owner chain. Temporal artefacts can also use a hub's relationship tag (e.g. `project/{key}`) to surface alongside related work without taking ownership. See [hub-pattern.md](../hub-pattern.md).

## Provenance

Most temporal artefacts spin out from other artefacts (research from a session, a plan from a design, a report from a process). Reference [provenance.md](../provenance.md) in the taxonomy. See also [linking.md](../linking.md) and [wikilinks.md](../wikilinks.md) for link conventions used in templates.

## Local vs Upstream Types

A type added directly to a vault by following steps 1–7 is a **local type**. It lives only in that vault's `_Config/` and `_Temporal/{Folder}/`.

- **Upgrades preserve local types.** The sync flow (`sync_definitions.py`) operates only on types defined in `.brain-core/artefact-library/`. Local-only types have no tracking entry in `.brain/tracking.json` and are untouched by upgrades.
- **Local edits to library types are also preserved.** When a library type is installed in a vault, sync uses three-way hash comparison (upstream / installed / local) and warns on conflicts rather than overwriting unprompted. `--force` overrides.
- **Existing artefacts are not migrated.** Adding or renaming a type doesn't move existing files. If files in another folder belong in the new type, move them by hand (and update their `type:` and tags).
- **Promoting a local type to upstream:** add an artefact-library bundle (next section). The next sync run picks up the type as managed; the local files become the installed baseline.

## Upstream Contribution

To add a new type to brain-core for distribution, create a bundle at `artefact-library/temporal/{key}/`:

- **`manifest.yaml`** — file mappings and folders. Parsed by Brain's shared standalone YAML subset; follow the schema exactly:
  ```yaml
  files:
    taxonomy:
      source: taxonomy.md
      target: _Config/Taxonomy/Temporal/{key}.md
    template:
      source: template.md
      target: _Config/Templates/Temporal/{Title}.md
  folders:
    - _Temporal/{Plural}/
  router_trigger: "When ... → [[_Config/Taxonomy/Temporal/{key}]]"   # informational
  ```
  The `router_trigger` field documents the recommended trigger but is **not auto-installed into `_Config/router.md`** by sync — it is metadata for operators and template-vault generation. Default-installed types ship with their trigger pre-populated in `template-vault/_Config/router.md`.
- **`schema.yaml`** — required/optional frontmatter validation. Standard constraints: `const:`, `type:` (`string` / `array`), `pattern:` (regex), `enum:`, `default:`, `format:` (e.g. `iso-datetime`), `contains:` (for arrays). Example:
  ```yaml
  required:
    type:
      const: "temporal/{singular}"
    tags:
      type: array
      contains: "{singular}"
  optional:
    status:
      enum: [draft, shaping, approved, implementing, completed, deprecated, parked]
      default: draft
  ```
- **`taxonomy.md`** and **`template.md`** — the canonical content vaults install.
- **`README.md`** — short summary for the artefact library index.

Then list the new type in `artefact-library/README.md`. If the type should ship installed by default, also add the folder + router trigger to `template-vault/`. The checked upgrade and definition-sync owners install and track the type in target vaults; existing customisation is preserved unless explicit force policy is supplied.

## Reference

- **Existing taxonomies** in `_Config/Taxonomy/Temporal/` are the source of truth for settled conventions. Reports, Plans, and Idea Logs cover the most common patterns (date-anchored naming, optional lifecycle, provenance lineage).
- **User-facing introduction** to the shipping types: `docs/user/template-library-guide.md` in the brain-core source repo (not installed into vaults).
- **Related standards:** [keys](../keys.md), [hub-pattern](../hub-pattern.md), [naming-conventions](../naming-conventions.md), [archiving](../archiving.md), [linking](../linking.md), [provenance](../provenance.md), [wikilinks](../wikilinks.md).
