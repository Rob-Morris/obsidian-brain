# Adding a Living Artefact Type

A type is defined by a folder, a taxonomy, a template, and (optionally) a router trigger. For upstream contributions to brain-core, also add an artefact-library bundle.

> See [when-to-add-type.md](when-to-add-type.md) before creating one — content that is one-off or fits an existing folder usually doesn't need a new type.

## Steps

1. **Folder** — create `{Plural}/` at vault root. Title Case plural for countables (`People`, `Designs`, `Tasks`); singular/mass for collective nouns (`Documentation`, `Wiki`, `Writing`). The compiler derives the type key from the folder name (lowercased, spaces → hyphens), so `My Custom Type/` becomes type key `my-custom-type` and registered type `living/my-custom-type`. See [naming-conventions.md](../naming-conventions.md).
2. **Taxonomy file** — create `_Config/Taxonomy/Living/{key}.md` (use the lowercased-hyphenated key, e.g. `daily-notes.md`) with the sections listed below. Without a taxonomy the folder still registers as a type, but is flagged `configured: false` and tooling cannot validate or auto-create artefacts in it.
3. **Template file** — create `_Config/Templates/Living/{Title}.md` (use the human-readable Title Case form, e.g. `Daily Notes.md`) with default frontmatter and a section skeleton.
4. **Router trigger** *(optional)* — if the type should be created in response to a recurring condition, append a one-line conditional to `_Config/router.md`: `When ... → [[_Config/Taxonomy/Living/{key}]]`. Most living hubs are created reactively and don't need one. The compiler reads `_Config/router.md` for the *condition* and the taxonomy's `## Trigger` section for the *detail*; both are merged into the compiled router.
5. **Compile router** — `python3 .brain-core/scripts/compile_router.py` regenerates the compiled router and folder-colour CSS. Until this runs, MCP tools and other consumers don't see the new type.
6. **Validate** — `python3 .brain-core/scripts/check.py` flags missing taxonomy/template files, frontmatter inconsistencies, naming violations, parent-contract breakage, and broken wikilinks. Run with `--actionable` for fix suggestions.
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
- **`## Lifecycle`** *(when the type has a status enum)* — a Markdown table with one row per state, including the default and any terminal states. The compiler extracts the status enum and terminal states from this section. See [archiving.md](../archiving.md) for `+Done/`, `+Shipped/`, `+Published/` conventions.
- **`## Template`** — a single wikilink to the template, e.g. `[[_Config/Templates/Living/People]]` (no `.md` extension). Required for the compiler to record the template pointer.

Optional sections:

- **`## When To Use`** — orienting cue separate from `## Purpose`. Convention only; not parsed.
- **`## Trigger`** — first non-blank line is the *condition* (used to infer category: `before` / `after` / `ongoing`); the rest is *detail* shown to agents. Required only if the type has a router trigger; the compiler merges this with the matching conditional in `_Config/router.md`.
- **`## On Status Change`** — per-status hooks of the form: `When `status` transitions to `{value}`, set `{field}` to {expr}.` The compiler compiles each line into a `{status: {set: {field: expr}}}` rule. Used by `Writing` to set `publisheddate` on `published`.
- **`## Shaping`** — declares shaping flavour, bar, and completion status. Convention only; not parsed.

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

- **`manifest.yaml`** — file mappings and folders. Hand-rolled YAML (no PyYAML dependency); follow the schema exactly:
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
      pattern: "^[a-z0-9]+(-[a-z0-9]+)*$"
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

Then list the new type in `artefact-library/README.md`. If the type should ship installed by default, also add the folder + router trigger to `template-vault/`. The install flow (`upgrade.py` → `sync_definitions.py` → `compile_router.py`) installs and tracks the type in target vaults; existing customisation is preserved unless `--force` is passed.

## Reference

- **Existing taxonomies** in `_Config/Taxonomy/Living/` are the source of truth for settled conventions. People, Projects, and Tasks cover the most common patterns (hub pattern, lifecycle, optional discriminator field). Releases and Writing demonstrate advanced naming and status-change hooks.
- **User-facing introduction** to the shipping types: `docs/user/template-library-guide.md` in the brain-core source repo (not installed into vaults).
- **Related standards:** [keys](../keys.md), [hub-pattern](../hub-pattern.md), [naming-conventions](../naming-conventions.md), [subfolders](../subfolders.md), [archiving](../archiving.md), [linking](../linking.md), [provenance](../provenance.md), [wikilinks](../wikilinks.md).
