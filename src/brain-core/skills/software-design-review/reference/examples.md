# Worked Examples

Three examples illustrating principle application in context. Reviewers can cite them when a finding maps cleanly to one of these patterns.

## Example A — `assert` vs `raise` at an I/O boundary

```python
def load_artefact(name: str) -> dict:
    path = ARTEFACT_DIR / f"{name}.json"
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not load artefact '{name}' from {path}") from exc
    if "kind" not in data:
        raise ValueError(f"artefact file is corrupt — 'kind' missing: {path}")
    return data
```

Two things here:

- **`assert "kind" in data` would be wrong.** `assert` is stripped under `python -O`, and corrupt JSON is a real failure mode at a deserialisation boundary, not "what cannot happen." P13 says enforce — at I/O boundaries, that means `raise`.
- **Wrap I/O exceptions with diagnostic context, then chain with `from exc`.** Trusting internal callers does not mean propagating raw `FileNotFoundError`. Wrap with the artefact name and path so the failure is meaningful at the call site; chain with `from exc` so the original cause is preserved for debugging.

## Example B — P21 governs direction, not metadata

```python
@dataclass
class Artefact:
    name: str
    kind: str
    body: str
    loaded_at: datetime  # OK — infrastructure annotation; does not couple domain to storage
```

P21 ("persistence is a detail") forbids the domain *depending on* storage modules; it does not forbid returned values carrying infrastructure metadata. Removing `loaded_at` to satisfy P21 over-applies the principle.

## Example C — When to extract a helper

Three modules each validate an artefact name with the same rule (non-empty, no slashes, lowercase). Default rule: extract on the third occurrence. Three sites + same rule + drift risk if changed in only one place → **extract now.**

Two modules formatting wikilinks identically — if the formatting rule changes (a new wikilink syntax, a different escape character), both must change together or one will silently drift → **also extract** (override on second occurrence: same rule, drift risk).

A single occurrence of similar-looking validation? **Leave it** — duplication is cheaper than the wrong abstraction.
