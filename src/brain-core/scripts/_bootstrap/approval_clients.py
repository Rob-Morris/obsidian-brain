"""Native client approval syntax and bounded, semantics-checked file edits."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shlex
import tomllib
from fnmatch import fnmatchcase


@dataclass(frozen=True, slots=True)
class Selection:
    client: str
    scope: str
    surface: str
    root: Path
    executable: Path
    server: str = "brain"

    def __post_init__(self):
        if self.client not in {"codex", "claude"} or self.scope not in {"user", "project", "local"}:
            raise ValueError("unsupported approval client/scope")
        if self.scope == "local" and self.client != "claude":
            raise ValueError("local approvals are supported only for Claude")
        if self.surface not in {"mcp", "cli"}:
            raise ValueError("select mcp or cli approvals explicitly")
        if not self.root.is_absolute() or not self.executable.is_absolute():
            raise ValueError("approval config root and executable must be absolute")
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", self.server):
            raise ValueError("approval server identity must be a literal name")

    @property
    def path(self) -> Path:
        if self.client == "codex":
            return self.root / ("config.toml" if self.surface == "mcp" else "rules/brain.rules")
        return self.root / ("settings.local.json" if self.scope == "local" else "settings.json")

    @property
    def identity(self) -> str:
        return json.dumps([self.client, self.scope, self.surface, str(self.root),
                           self.server if self.surface == "mcp" else str(self.executable)])


def config_root(client: str, scope: str, home: Path, target: Path | None) -> Path:
    """Resolve only the selected native layout, including user config relocation."""
    if scope != "user":
        if target is None or not target.is_absolute():
            raise ValueError("project/local approvals require an absolute workspace")
        if client == "claude" and scope == "local" and os.name != "nt":
            for directory in (target, *target.parents):
                if directory == home:
                    break
                git = directory / ".git"
                if git.exists():
                    if directory != target or not git.is_dir():
                        raise ValueError("Claude local policy loads at the main checkout root; select that root explicitly, or use project scope")
                    break
        return target / f".{client}"
    variable = {"codex": "CODEX_HOME", "claude": "CLAUDE_CONFIG_DIR"}[client]
    override = os.environ.get(variable) if home == Path.home() else None
    if override:
        if not Path(override).is_absolute():
            raise ValueError(f"{variable} must be absolute for managed approvals")
        return Path(override)
    return home / f".{client}"


def desired_items(selection: Selection, policy: dict[str, str]) -> dict:
    """Project exact tool identities or canonical command-first invocation forms."""
    result = {}
    for identity, decision in policy.items():
        if decision == "unknown":
            continue
        if decision not in {"allow", "prompt"}:
            raise ValueError("unknown policy decision")
        if selection.surface == "mcp":
            if selection.client == "codex":
                result[identity] = "approve" if decision == "allow" else "prompt"
            else:
                name = f"mcp__{selection.server}__{identity}"
                result[("allow" if decision == "allow" else "ask") + ":" + name] = 1
        else:
            argv = json.loads(identity)
            if not isinstance(argv, list) or not argv or any(not re.fullmatch(r"[a-z][a-z0-9-]*", p) for p in argv):
                raise ValueError("invalid command-first CLI policy identity")
            prefix = [str(selection.executable), *argv]
            if selection.client == "codex":
                line = "prefix_rule(pattern=" + json.dumps(prefix) + ", decision=" + json.dumps(decision) + ")"
                result[line] = 1
            else:
                name = "Bash(" + shlex.join(prefix) + " *)"
                result[("allow" if decision == "allow" else "ask") + ":" + name] = 1
    return result


def _json(content: str) -> dict:
    value = json.loads(content or "{}")
    if not isinstance(value, dict):
        raise ValueError("client settings must be an object")
    permissions = value.get("permissions", {})
    if not isinstance(permissions, dict):
        raise ValueError("client permissions must be an object")
    for kind in ("allow", "ask", "deny"):
        rules = permissions.get(kind, [])
        if not isinstance(rules, list) or any(not isinstance(rule, str) for rule in rules):
            raise ValueError(f"client permissions.{kind} must contain string rules")
    return value


def observed_items(selection: Selection, content: str) -> dict:
    """Read items without inferring ownership from names, comments or equality."""
    if selection.client == "claude":
        permissions = _json(content).get("permissions", {})
        return {f"{kind}:{rule}": count for kind in ("allow", "ask", "deny")
                for rule, count in Counter(permissions.get(kind, [])).items()}
    if selection.surface == "cli":
        return dict(Counter(line for line in content.splitlines() if line.strip()))
    data = tomllib.loads(content)
    server = data.get("mcp_servers", {}).get(selection.server)
    if not isinstance(server, dict) or not (server.get("command") or server.get("url")):
        raise ValueError("configure the Codex MCP transport before managing its approvals")
    tools = server.get("tools", {})
    if not isinstance(tools, dict) or any(not isinstance(value, dict) for value in tools.values()):
        raise ValueError("Codex tool settings must be tables")
    return {key: value["approval_mode"] for key, value in tools.items() if "approval_mode" in value}


def known_overrides(selection, content, desired):
    """Report recognised restrictions; this is not a replica of host policy evaluation."""
    if selection.client == "claude":
        permissions = _json(content).get("permissions", {})
        restricted = permissions.get("ask", []) + permissions.get("deny", [])
        for identity in desired:
            if not identity.startswith("allow:"):
                continue
            rule = identity.removeprefix("allow:")
            if any(fnmatchcase(rule, pattern.replace(":*", " *")) or
                   (pattern == f"mcp__{selection.server}" and rule.startswith(pattern + "__")) or
                   (pattern == "Bash" and rule.startswith("Bash(")) for pattern in restricted):
                yield identity
    elif selection.surface == "mcp":
        server = tomllib.loads(content)["mcp_servers"][selection.server]
        for name, value in desired.items():
            if value == "approve" and (server.get("enabled") is False or server.get("tools", {}).get(name, {}).get("enabled") is False):
                yield name


def render_items(selection: Selection, content: str, values: dict) -> str:
    before = observed_items(selection, content)
    changed = {key for key in before.keys() | values.keys() if before.get(key) != values.get(key)}
    if not changed:
        return content
    if selection.client == "claude":
        data = _json(content)
        permissions = data.setdefault("permissions", {})
        for key in sorted(changed):
            kind, rule = key.split(":", 1)
            if kind not in {"allow", "ask"} or not _owns_rule_shape(selection, rule):
                raise ValueError("refusing an edit outside this Brain approval surface")
            count = values.get(key, 0)
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ValueError("invalid native permission count")
            rules = permissions.setdefault(kind, [])
            existing = rules.count(rule)
            if count > existing:
                rules.extend([rule] * (count - existing))
            else:
                for _ in range(existing - count):
                    rules.remove(rule)
        rendered = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    elif selection.surface == "cli":
        for line in changed:
            if not _codex_prefix_shape(selection, line):
                raise ValueError("refusing an edit outside generated Brain prefix rules")
            if values.get(line, 0) not in {0, 1}:
                raise ValueError("generated prefix rules must be individually owned")
        lines = content.splitlines(keepends=True)
        lines = [line for line in lines if line.rstrip("\r\n") not in changed]
        rendered = "".join(lines)
        additions = [line for line in sorted(changed) if values.get(line)]
        if additions:
            rendered = rendered.rstrip("\n") + ("\n" if rendered else "") + "\n".join(additions) + "\n"
    else:
        rendered = _render_codex_tools(selection, content, values, changed)
    if observed_items(selection, rendered) != values:
        raise ValueError("native approval edit changed unexpected rule semantics")
    return rendered


def _owns_rule_shape(selection: Selection, rule: str) -> bool:
    if selection.surface == "mcp":
        prefix = f"mcp__{selection.server}__"
        return rule.startswith(prefix) and bool(re.fullmatch(r"[a-z][a-z0-9_-]*", rule[len(prefix):]))
    prefix = "Bash(" + shlex.quote(str(selection.executable)) + " "
    return rule.startswith(prefix) and rule.endswith(" *)") and bool(re.fullmatch(r"[a-z][a-z0-9-]*(?: [a-z][a-z0-9-]*)*", rule[len(prefix):-3]))


def _codex_prefix_shape(selection: Selection, line: str) -> bool:
    # Parse only literal generated prefix calls, never execute Starlark/Python.
    import ast

    try:
        tree = ast.parse(line).body
        call = tree[0].value if len(tree) == 1 and isinstance(tree[0], ast.Expr) else None
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.func.id != "prefix_rule" or call.args:
            return False
        if len(call.keywords) != 2 or {k.arg for k in call.keywords} != {"pattern", "decision"}:
            return False
        fields = {k.arg: ast.literal_eval(k.value) for k in call.keywords}
        pattern = fields["pattern"]
        return (isinstance(pattern, list) and len(pattern) > 1 and pattern[0] == str(selection.executable)
                and all(isinstance(p, str) and re.fullmatch(r"[a-z][a-z0-9-]*", p) for p in pattern[1:])
                and fields["decision"] in {"allow", "prompt"})
    except (SyntaxError, ValueError, TypeError):
        return False


def _section_path(header: str) -> tuple[str, ...]:
    value = tomllib.loads(header + "\n__brain_approval_probe__ = true\n")
    path = []
    while isinstance(value, dict) and len(value) == 1:
        key, value = next(iter(value.items()))
        if key == "__brain_approval_probe__":
            return tuple(path)
        path.append(key)
    return ()


def _render_codex_tools(selection, content, values, changed):
    from _bootstrap.mcp_state import _parse_toml_sections, _toml_body_lines

    data = tomllib.loads(content)
    expected = deepcopy(data)
    server = expected["mcp_servers"][selection.server]
    tools = server.setdefault("tools", {})
    for key in changed:
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", key) or values.get(key) not in {None, "approve", "prompt", "auto", "writes"}:
            raise ValueError("invalid exact Codex tool approval")
        if key in values:
            tools.setdefault(key, {})["approval_mode"] = values[key]
        elif key in tools:
            tools[key].pop("approval_mode", None)
            if not tools[key]:
                del tools[key]
    if not tools:
        server.pop("tools", None)
    preamble, sections = _parse_toml_sections(content)
    kept, comments, found = [], [], False
    for section in sections:
        path = _section_path(section["header"])
        if path[:2] == ("mcp_servers", selection.server):
            found = True
            comments.extend(line for line in section["body"] if line.lstrip().startswith("#"))
        else:
            kept.append(section["header"] + "".join(section["body"]))
    if not found:
        raise ValueError("unsupported inline root MCP layout; no client settings changed")
    rendered = "".join(preamble) + "".join(kept)
    rendered = rendered.rstrip("\n") + "\n\n[mcp_servers." + selection.server + "]\n"
    rendered += "".join(comments) + "".join(_toml_body_lines(server))
    if tomllib.loads(rendered) != expected:
        raise ValueError("unsupported TOML layout: approval edit would alter unrelated settings")
    return rendered
