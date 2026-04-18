"""Shared naming engine.

Single source of truth for filename render, validate, reverse-parse, and
rule-selection against the compiled naming contract produced by
compile_router.py. All command modules should call into this engine rather
than building private placeholder or regex logic.

A compiled naming contract has this shape::

    {
        "pattern": "{Title}.md" | None,       # simple-form convenience; None in advanced form
        "folder": "Wiki/" | None,
        "rules": [
            {"match_field": "status" | None,
             "match_values": ["planned","active"] | ["*"] | None,
             "pattern": "{Title}.md"},
            ...
        ],
        "placeholders": [
            {"name": "Version", "field": "version",
             "required_when_field": "status" | None,
             "required_values": ["shipped"] | None,
             "regex": "^v?\\d+\\.\\d+\\.\\d+$" | None},
            ...
        ],
    }
"""

import re

from ._artefacts import PLACEHOLDER_TOKEN_RE, resolve_naming_pattern
from ._slugs import title_to_slug


_STRUCTURAL_PLACEHOLDERS = [
    ("yyyymmdd", r"\d{8}"),
    ("yyyy-mm-dd", r"\d{4}-\d{2}-\d{2}"),
    ("yyyy", r"\d{4}"),
    ("ddd", r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)"),
    ("mm", r"\d{2}"),
    ("dd", r"\d{2}"),
    ("{sourcedoctype}", r"[a-z]+(?:-[a-z]+)*"),
]

_TITLE_PLACEHOLDERS = ("{Title}", "{title}", "{name}", "{slug}")


def _rules_of(naming):
    """Return the naming contract's rules, synthesising a default for simple-form."""
    if not naming:
        return []
    rules = naming.get("rules")
    if rules:
        return rules
    pattern = naming.get("pattern")
    if pattern:
        return [{"match_field": None, "match_values": None, "pattern": pattern}]
    return []


def select_rule(naming, fields):
    """Return the first rule matching the given fields, or None.

    A rule with ``match_field`` None matches unconditionally (simple-form).
    A rule with ``match_values`` containing ``"*"`` matches any value of the
    declared ``match_field``. Otherwise the field's value must be one of
    ``match_values``.
    """
    if not naming:
        return None
    fields = fields or {}
    for rule in _rules_of(naming):
        mf = rule.get("match_field")
        if mf is None:
            return rule
        if mf not in fields:
            continue
        values = rule.get("match_values") or []
        if "*" in values or fields[mf] in values:
            return rule
    return None


def _required_placeholders_for_rule(naming, rule, fields):
    """Yield placeholder dicts that are required in the current field state."""
    pattern = rule.get("pattern") or ""
    fields = fields or {}
    for ph in naming.get("placeholders") or []:
        name = ph.get("name")
        if not name:
            continue
        if f"{{{name}}}" not in pattern and f"{{{name.lower()}}}" not in pattern:
            continue
        when_field = ph.get("required_when_field")
        if when_field:
            when_values = ph.get("required_values") or []
            current = fields.get(when_field)
            if current not in when_values and "*" not in when_values:
                continue
        yield ph


def render_filename(naming, title, fields):
    """Render a filename from the rule matching the given fields.

    Date tokens in the pattern resolve from ``fields[rule["date_source"]]``;
    the compiler fills ``date_source`` at compile time (temporal classification
    default ``created``; living types with date tokens must declare one).

    Raises ``ValueError`` if no rule matches, a required placeholder's
    backing field is missing, or a date token's backing field is missing or
    unparseable.
    """
    rule = select_rule(naming, fields)
    if rule is None:
        raise ValueError(
            "No naming rule matches the current frontmatter state "
            f"(fields={sorted((fields or {}).keys())})"
        )
    for ph in _required_placeholders_for_rule(naming, rule, fields):
        field_name = ph.get("field")
        if not field_name:
            continue
        value = (fields or {}).get(field_name)
        if value in (None, ""):
            raise ValueError(
                f"Naming pattern '{rule['pattern']}' requires frontmatter field "
                f"'{field_name}' for placeholder '{{{ph['name']}}}'"
            )
    return resolve_naming_pattern(
        rule["pattern"],
        title,
        variables=fields or {},
        date_source=rule.get("date_source"),
    )


def render_filename_or_default(naming, title, fields):
    """Render via the naming contract when present, else fall back to ``{slug}.md``.

    Centralises the "no naming contract declared" policy so every caller that
    produces a filename for a new or renamed artefact treats the fallback
    identically.
    """
    if naming:
        return render_filename(naming, title, fields)
    return title_to_slug(title) + ".md"


def _build_pattern_regex(pattern, placeholders_by_name, capture_title=False):
    """Translate a naming pattern into an anchored-body regex.

    Structural date tokens expand to fixed-width digit classes; title-like
    placeholders expand to ``(.+?)`` (capture once, then non-capturing);
    declared placeholders use their ``regex`` constraint when present,
    falling back to ``.+?``; every other character is escaped literally.
    """
    result = ""
    capture_emitted = False
    i = 0
    while i < len(pattern):
        matched = False

        for token, regex in _STRUCTURAL_PLACEHOLDERS:
            if pattern.startswith(token, i):
                result += regex
                i += len(token)
                matched = True
                break
        if matched:
            continue

        for token in _TITLE_PLACEHOLDERS:
            if pattern.startswith(token, i):
                if capture_title and not capture_emitted:
                    result += r"(.+?)"
                    capture_emitted = True
                else:
                    result += r".+?"
                i += len(token)
                matched = True
                break
        if matched:
            continue

        ph_match = PLACEHOLDER_TOKEN_RE.match(pattern[i:])
        if ph_match:
            name = ph_match.group(1)
            ph = placeholders_by_name.get(name) or placeholders_by_name.get(name.lower())
            if ph and ph.get("regex"):
                inner = ph["regex"]
                if inner.startswith("^"):
                    inner = inner[1:]
                if inner.endswith("$"):
                    inner = inner[:-1]
                result += f"(?:{inner})"
            else:
                result += r".+?"
            i += len(ph_match.group(0))
            continue

        result += re.escape(pattern[i])
        i += 1
    return result


_RULE_REGEX_CACHE = {}


def _rule_regex(naming, rule, capture_title=False):
    """Return a compiled anchored regex for a rule's pattern, or None on error.

    Results are cached keyed on (id(rule), capture_title). The compiled naming
    contract is immutable after ``load_compiled_router``, so one compile per
    rule/flavour per process is enough for hot paths (check, migrate, edit).
    """
    cache_key = (id(rule), capture_title)
    if cache_key in _RULE_REGEX_CACHE:
        return _RULE_REGEX_CACHE[cache_key]
    placeholders_by_name = {
        p["name"]: p for p in (naming.get("placeholders") or []) if p.get("name")
    }
    body = _build_pattern_regex(
        rule["pattern"], placeholders_by_name, capture_title=capture_title
    )
    md_suffix = re.escape(".md")
    if capture_title and body.endswith(md_suffix):
        body = body[: -len(md_suffix)] + r"(?:\.md)?"
    try:
        compiled = re.compile(r"\A" + body + r"\Z")
    except re.error:
        compiled = None
    _RULE_REGEX_CACHE[cache_key] = compiled
    return compiled


def validate_filename(naming, fields, filename):
    """Does ``filename`` match the naming rule selected for ``fields``?

    Returns True/False. Returns False if no rule matches or the pattern
    cannot compile to a regex.
    """
    rule = select_rule(naming, fields)
    if rule is None:
        return False
    regex = _rule_regex(naming, rule)
    if regex is None:
        return False
    return regex.match(filename) is not None


def extract_title(naming, fields, filename):
    """Reverse-parse the title portion of a filename using the matching rule.

    Returns the captured title string or None if the filename does not match
    the selected rule's pattern. The filename may be passed as a stem (no
    ``.md``) or with the extension.
    """
    rule = select_rule(naming, fields)
    if rule is None:
        return None
    regex = _rule_regex(naming, rule, capture_title=True)
    if regex is None:
        return None
    m = regex.match(filename)
    if not m or not m.groups():
        return None
    return m.group(1)
