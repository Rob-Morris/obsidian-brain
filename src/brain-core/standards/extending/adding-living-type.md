# Adding a Living Artefact Type

A type is defined by a folder, a taxonomy, a template, and (optionally) a router trigger. For upstream contributions to brain-core, also add an artefact-library bundle.

> See [when-to-add-type.md](when-to-add-type.md) before creating one — content that is one-off or fits an existing folder usually doesn't need a new type.

## Steps

1. **Folder** — create `{Plural}/` at vault root. Title Case plural for countables (`People`, `Designs`, `Tasks`); singular/mass for collective nouns (`Documentation`, `Wiki`, `Writing`). The compiler derives the type key from the folder name (lowercased, spaces → hyphens), so `My Custom Type/` becomes type key `my-custom-type` and registered type `living/my-custom-type`. See [naming-conventions.md](../naming-conventions.md).
2. **Taxonomy file** — create `_Config/Taxonomy/Living/{key}.md` (use the lowercased-hyphenated key, e.g. `daily-notes.md`) with the sections listed below. Without a taxonomy the folder still registers as a type, but is flagged `configured: false` and tooling cannot validate or auto-create artefacts in it.
3. **Template file** — create `_Config/Templates/Living/{Title}.md` (use the human-readable Title Case form, e.g. `Daily Notes.md`) with default frontmatter and a section skeleton.
4. **Router trigger** *(optional)* — if the type should be created in response to a recurring condition, append a one-line conditional to `_Config/router.md`: `When ... → [[_Config/Taxonomy/Living/{key}]]`. Most living hubs are created reactively and don't need one. The compiler reads `_Config/router.md` for the *condition* and the taxonomy's `## Trigger` section for the *detail*; both are merged into the compiled router.
5. **Compile router** — `brain runtime refresh-router --request-json '{}' --json` regenerates the compiled router and folder-colour CSS. Until this runs, consumers do not see the new type.
6. **Validate** — `brain vault check --request-json '{"actionable":true}' --json` flags missing taxonomy/template files, frontmatter inconsistencies, naming violations, parent-contract breakage, and broken wikilinks.
7. **Log** — record the addition in the daily note.

## Type Identifier vs Frontmatter Type

The type registered by the compiler is `living/{folder-key}` — usually plural (e.g. `living/people`, `living/projects`). The `type:` field on individual artefacts is the **singular form** declared by the taxonomy's frontmatter section (e.g. `living/person`, `living/project`). The compiler tracks both: `type` for the type entry, `frontmatter_type` for the per-artefact value.

Convention: derive the singular form by dropping the trailing `s` (people→person, projects→project, designs→design). Mass-noun types use the same form for both (`living/documentation`, `living/wiki`, `living/writing`).

## Required Taxonomy Sections

The following section headings are parsed by `compile_router.py` and must use the exact heading text:

- **`## Purpose`** — what the type is and when to create one.
- **`## Naming`** — filename pattern and folder rules. Use one of:
  - **Simple form:** `` `{pattern}.md` in `{folder}/` `` on a single line. For date-derived patterns add `` date source: `created` `` (or another field).
  - **Advanced form:** a `### Rules` subsection with a table for status-conditional naming (used by `Releases`, `Writing`). Optionally a `### Placeholders` subsection.
- **`## Frontmatter`** — schema as a YAML code block, opened with `` ```yaml `` and containing a `---` ... `---` block:
  - `type: living/{singular}` (always)
  - `key: {key}` (for hub-style types — see [keys.md](../keys.md))
  - `tags: [{singular}/{key}]` (for hub-style types — see Tag Convention)
  - `status: {default}` (when the type has a lifecycle)

  Every top-level key the example shows is treated as **required** — the example is what an agent authoring without tooling reproduces. To document a genuinely optional field, add an `**Optional:**` line after the code block naming those fields:

  ```markdown
  **Optional:** `version`, `tag`, `commit`, `shipped`
  ```

  Omit the line when every documented field is required. Naming a field the example does not show is a compile error. A field the `## Naming` rules match on cannot be optional — the compiler needs a value to select a pattern — so `Releases` and `Writing` keep `status` required even though it carries a default. Keep the line consistent with the type's `schema.yaml`: it may promote a schema-optional field to required, never the reverse.
- **`## Lifecycle`** *(when the type has a status enum)* — a Markdown table with one row per state, including the default and any terminal states. The compiler extracts the status enum and terminal states from this section. See [archiving.md](../archiving.md) for `+Done/`, `+Shipped/`, `+Published/` conventions.
- **`## Template`** — a single wikilink to the template, e.g. `[[_Config/Templates/Living/People]]` (no `.md` extension). Required for the compiler to record the template pointer.

Optional sections:

- **`## When To Use`** — orienting cue separate from `## Purpose`. Convention only; not parsed.
- **`## Trigger`** — first non-blank line is the *condition* (used to infer category: `before` / `after` / `ongoing`); the rest is *detail* shown to agents. Required only if the type has a router trigger; the compiler merges this with the matching conditional in `_Config/router.md`.
- **`## On Status Change`** — per-status hooks of the form: `When `status` transitions to `{value}`, set `{field}` to {expr}.` The compiler compiles each line into a `{status: {set: {field: expr}}}` rule. Used by `Writing` to set `publisheddate` on `published`.
- **`## Shaping`** — opts the type into shaping and declares the parsed contract: `**Flavour:**` (`Convergent` or `Discovery`) and `**Bar:**`. Status behaviour defaults to `transition`, which requires a backtick-delimited `**Completion status:**`. A discovery type whose lifecycle represents an enduring domain state may instead declare `**Status behaviour:** \`preserve\``; it may omit completion status or declare one solely as the explicit exit for an artefact already in `shaping`. The compiler exposes this metadata to shaping skills. Both behaviours require `shaping` in the explicit lifecycle enum, and every declared completion status must also appear there.

## Tag Convention

Hub-style types use the **singular form** of the type name as the tag prefix. No abbreviations:

- `person/{key}`, `project/{key}`, `journal/{key}`, `workspace/{key}`, `design/{key}`, `release`

Files related to the hub artefact carry that tag. Tags signal relationship; they never substitute for `parent:` ownership. See [keys.md](../keys.md) and [hub-pattern.md](../hub-pattern.md).

## Hub Pattern

Hub-style types own children. The full contract is in [hub-pattern.md](../hub-pattern.md). In summary:

- Children declare `parent: {type}/{key}` in frontmatter.
- Living children of the **same** type live in `{key}/` subfolders within the parent's folder.
- Cross-type children live in `{scope}/` subfolders (e.g. `Releases/project~brain/`), where `{scope}` is the tokenised parent key.
- Temporal children stay in their date folders.

See [subfolders.md](../subfolders.md) for when and how subfolders appear inside living artefact folders.

## Provenance

If artefacts of this type can originate from or spin out to other artefacts, reference [provenance.md](../provenance.md) in the taxonomy. See also [linking.md](../linking.md) and [wikilinks.md](../wikilinks.md) for link conventions used in templates.

## Local vs Upstream Types

A type added directly to a vault by following steps 1–7 is a **local type**. It lives only in that vault's `_Config/` and `{Folder}/`.

- **Upgrades preserve local types.** The sync flow (`sync_definitions.py`) operates only on types defined in `.brain-core/artefact-library/`. Local-only types have no tracking entry in `.brain/tracking.json` and are untouched by upgrades.
- **Local edits to library types are also preserved.** When a library type is installed in a vault, sync uses three-way hash comparison (upstream / installed / local) and warns on conflicts rather than overwriting unprompted. `--force` overrides.
- **Existing artefacts are not migrated.** Adding or renaming a type doesn't move existing files. If files in another folder belong in the new type, move them by hand (and update their `type:` and tags).
- **Promoting a local type to upstream:** add an artefact-library bundle (next section). The next sync run picks up the type as managed; the local files become the installed baseline.

## Upstream Contribution

To add a new type to brain-core for distribution, create a bundle at `artefact-library/living/{key}/`:

- **`manifest.yaml`** — file mappings and folders. Parsed by Brain's shared standalone YAML subset; follow the schema exactly:
  ```yaml
  files:
    taxonomy:
      source: taxonomy.md
      target: _Config/Taxonomy/Living/{key}.md
    template:
      source: template.md
      target: _Config/Templates/Living/{Title}.md
  folders:
    - {Plural}/
  router_trigger: "When ... → [[_Config/Taxonomy/Living/{key}]]"   # optional, informational
  ```
  The `router_trigger` field documents the recommended trigger but is **not auto-installed into `_Config/router.md`** by sync — it is metadata for operators and template-vault generation. Default-installed types ship with their trigger pre-populated in `template-vault/_Config/router.md`.
- **`schema.yaml`** — required/optional frontmatter validation. Standard constraints: `const:`, `type:` (`string` / `array`), `pattern:` (regex), `enum:`, `default:`, `format:` (e.g. `iso-datetime`), `contains:` (for arrays). Example:
  ```yaml
  required:
    type:
      const: "living/{singular}"
    key:
      type: string
      pattern: "^(?=.{1,64}$)(?=.*[a-z])[a-z0-9]+(?:-[a-z0-9]+)*$"
    tags:
      type: array
      contains: "{singular}/{key}"
  optional:
    status:
      enum: [active, shaping, parked]
      default: active
  ```
- **`taxonomy.md`** and **`template.md`** — the canonical content vaults install.
- **`README.md`** — short summary for the artefact library index.

Then list the new type in `artefact-library/README.md`. If the type should ship installed by default, also add the folder + router trigger to `template-vault/`. The checked upgrade and definition-sync owners install and track the type in target vaults; existing customisation is preserved unless explicit force policy is supplied.

## Reference

- **Existing taxonomies** in `_Config/Taxonomy/Living/` are the source of truth for settled conventions. People, Projects, and Tasks cover the most common patterns (hub pattern, lifecycle, optional discriminator field). Releases and Writing demonstrate advanced naming and status-change hooks.
- **User-facing introduction** to the shipping types: `docs/user/template-library-guide.md` in the brain-core source repo (not installed into vaults).
- **Related standards:** [keys](../keys.md), [hub-pattern](../hub-pattern.md), [naming-conventions](../naming-conventions.md), [subfolders](../subfolders.md), [archiving](../archiving.md), [linking](../linking.md), [provenance](../provenance.md), [wikilinks](../wikilinks.md).
