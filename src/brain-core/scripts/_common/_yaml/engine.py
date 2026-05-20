"""Small Brain-owned YAML engine for standalone config-style documents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any


_PLAIN_INT_RE = re.compile(r"-?(0|[1-9][0-9]*)$")
_PLAIN_KEY_RE = re.compile(r"[A-Za-z0-9_.-]+$")


class YamlError(ValueError):
    """Raised when Brain's limited YAML subset cannot parse a document."""


@dataclass(frozen=True)
class _Line:
    indent: int
    text: str
    lineno: int


def load_yaml_text(text: str, *, source: str = "<string>") -> Any:
    """Parse Brain's supported YAML subset from text."""
    lines = _prepare_lines(text, source=source)
    if not lines:
        return {}
    parser = _Parser(lines, source=source)
    value = parser.parse_block(lines[0].indent)
    parser.ensure_finished()
    return value


def load_yaml_file(path: str | Path) -> Any:
    """Parse Brain's supported YAML subset from a file."""
    source = str(path)
    text = Path(path).read_text(encoding="utf-8")
    return load_yaml_text(text, source=source)


def dump_yaml_text(value: Any) -> str:
    """Serialise a supported Python value into Brain's YAML subset."""
    lines = _dump_node(value, indent=0)
    return "\n".join(lines) + ("\n" if lines else "")


def _prepare_lines(text: str, *, source: str) -> list[_Line]:
    prepared: list[_Line] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if "\t" in raw:
            raise YamlError(f"{source}:{lineno}: tabs are not supported")
        stripped = raw.strip()
        if stripped in {"---", "..."}:
            raise YamlError(f"{source}:{lineno}: document markers are not supported")
        content = _strip_comment(raw).rstrip()
        if not content.strip():
            continue
        indent = len(content) - len(content.lstrip(" "))
        prepared.append(_Line(indent=indent, text=content[indent:], lineno=lineno))
    return prepared


def _strip_comment(raw: str) -> str:
    quote: str | None = None
    i = 0
    while i < len(raw):
        char = raw[i]
        if quote == '"':
            if char == "\\":
                i += 2
                continue
            if char == '"':
                quote = None
            i += 1
            continue
        if quote == "'":
            if char == "'" and i + 1 < len(raw) and raw[i + 1] == "'":
                i += 2
                continue
            if char == "'":
                quote = None
            i += 1
            continue
        if char in {'"', "'"}:
            quote = char
            i += 1
            continue
        if char == "#" and (i == 0 or raw[i - 1].isspace()):
            return raw[:i]
        i += 1
    return raw


def _dump_node(value: Any, *, indent: int) -> list[str]:
    if isinstance(value, dict):
        return _dump_mapping(value, indent=indent)
    if isinstance(value, list):
        return _dump_sequence(value, indent=indent)
    raise TypeError(f"unsupported YAML root type: {type(value).__name__}")


def _dump_mapping(mapping: dict[str, Any], *, indent: int) -> list[str]:
    if not mapping:
        return [" " * indent + "{}"]
    lines: list[str] = []
    for key, value in mapping.items():
        if not isinstance(key, str):
            raise TypeError("YAML mapping keys must be strings")
        key_text = _emit_key(key)
        prefix = " " * indent + f"{key_text}:"
        if isinstance(value, dict):
            if value:
                lines.append(prefix)
                lines.extend(_dump_mapping(value, indent=indent + 2))
            else:
                lines.append(prefix + " {}")
        elif isinstance(value, list):
            if value:
                lines.append(prefix)
                lines.extend(_dump_sequence(value, indent=indent + 2))
            else:
                lines.append(prefix + " []")
        else:
            lines.append(prefix + " " + _emit_scalar(value))
    return lines


def _dump_sequence(items: list[Any], *, indent: int) -> list[str]:
    if not items:
        return [" " * indent + "[]"]
    lines: list[str] = []
    for item in items:
        prefix = " " * indent + "- "
        if isinstance(item, dict):
            if not item:
                lines.append(prefix + "{}")
                continue
            lines.append(" " * indent + "-")
            lines.extend(_dump_mapping(item, indent=indent + 2))
            continue
        if isinstance(item, list):
            if not item:
                lines.append(prefix + "[]")
                continue
            lines.append(" " * indent + "-")
            lines.extend(_dump_sequence(item, indent=indent + 2))
            continue
        lines.append(prefix + _emit_scalar(item))
    return lines


def _emit_key(key: str) -> str:
    if _PLAIN_KEY_RE.fullmatch(key):
        return key
    return _emit_quoted(key)


def _emit_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if value is None:
        raise TypeError("null is not part of Brain's YAML subset")
    if not isinstance(value, str):
        raise TypeError(f"unsupported YAML scalar type: {type(value).__name__}")
    return _emit_string(value)


def _emit_string(value: str) -> str:
    if value == "":
        return '""'
    if value in {"true", "false", "[]", "{}"}:
        return _emit_quoted(value)
    # Integer-looking strings must stay strings on round-trip, not YAML ints.
    if _PLAIN_INT_RE.fullmatch(value):
        return _emit_quoted(value)
    if value[0] in {"[", "{", "!", "&", "*", "|", ">", "-", "?", "@", "`"}:
        return _emit_quoted(value)
    if ": " in value or value.endswith(":") or " #" in value or value.startswith("#"):
        return _emit_quoted(value)
    if value != value.strip():
        return _emit_quoted(value)
    if "\n" in value:
        raise TypeError("multiline strings are not part of Brain's YAML subset")
    return value


def _emit_quoted(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


class _Parser:
    def __init__(self, lines: list[_Line], *, source: str) -> None:
        self._lines = lines
        self._source = source
        self._index = 0

    def ensure_finished(self) -> None:
        if self._index != len(self._lines):
            line = self._lines[self._index]
            self._error(line, "unexpected trailing content")

    def parse_block(self, indent: int) -> Any:
        line = self._peek()
        if line is None:
            return {}
        if line.indent != indent:
            self._error(line, f"unexpected indentation; expected {indent} spaces")
        if _is_sequence_line(line.text):
            return self._parse_sequence(indent)
        return self._parse_mapping(indent)

    def _parse_mapping(self, indent: int, *, seed: dict[str, Any] | None = None) -> dict[str, Any]:
        mapping = dict(seed or {})
        while True:
            line = self._peek()
            if line is None or line.indent < indent:
                return mapping
            if line.indent > indent:
                self._error(line, f"unexpected indentation; expected {indent} spaces")
            if _is_sequence_line(line.text):
                self._error(line, "cannot mix sequence items into a mapping block")

            key, rest = _split_key_value(line.text, source=self._source, lineno=line.lineno)
            if key == "<<":
                self._error(line, "merge keys are not supported")
            if any(key.startswith(marker) for marker in ("!", "&", "*")):
                self._error(line, "YAML tags, anchors, and aliases are not supported")
            self._index += 1

            if rest == "":
                next_line = self._peek()
                if next_line is None or next_line.indent <= indent:
                    self._error(line, "empty values are not supported here; use [] or {} explicitly")
                value = self.parse_block(next_line.indent)
            else:
                value = _parse_inline_value(rest, source=self._source, lineno=line.lineno)
            mapping[key] = value

    def _parse_sequence(self, indent: int) -> list[Any]:
        items: list[Any] = []
        while True:
            line = self._peek()
            if line is None or line.indent < indent:
                return items
            if line.indent > indent:
                self._error(line, f"unexpected indentation; expected {indent} spaces")
            if not _is_sequence_line(line.text):
                break

            body = line.text[1:].strip()
            self._index += 1
            if body == "":
                next_line = self._peek()
                if next_line is None or next_line.indent <= indent:
                    self._error(line, "empty sequence items are not supported")
                item = self.parse_block(next_line.indent)
                items.append(item)
                continue

            colon = _find_top_level_colon(body)
            if colon != -1:
                key = body[:colon].strip()
                rest = body[colon + 1 :].strip()
                if not key:
                    self._error(line, "sequence mapping item is missing a key")
                if rest == "":
                    next_line = self._peek()
                    if next_line is None or next_line.indent <= indent:
                        self._error(line, "empty values are not supported here; use [] or {} explicitly")
                    seed = {key: self.parse_block(next_line.indent)}
                else:
                    seed = {key: _parse_inline_value(rest, source=self._source, lineno=line.lineno)}
                next_line = self._peek()
                if next_line is not None and next_line.indent > indent:
                    child = self.parse_block(next_line.indent)
                    if not isinstance(child, dict):
                        self._error(next_line, "sequence mapping items may only extend with nested mappings")
                    seed.update(child)
                items.append(seed)
                continue

            items.append(_parse_inline_value(body, source=self._source, lineno=line.lineno))
        self._error(self._peek(), "cannot mix mapping entries into a sequence block")

    def _peek(self) -> _Line | None:
        if self._index >= len(self._lines):
            return None
        return self._lines[self._index]

    def _error(self, line: _Line | None, message: str) -> None:
        if line is None:
            raise YamlError(f"{self._source}: {message}")
        raise YamlError(f"{self._source}:{line.lineno}: {message}")


def _parse_inline_value(token: str, *, source: str, lineno: int) -> Any:
    token = token.strip()
    if token == "":
        raise YamlError(f"{source}:{lineno}: missing scalar value")
    if token.startswith(("!", "&", "*")):
        raise YamlError(f"{source}:{lineno}: YAML tags, anchors, and aliases are not supported")
    if token in {"|", ">"} or token.startswith("|") or token.startswith(">"):
        raise YamlError(f"{source}:{lineno}: block scalars are not supported")
    if token == "[]":
        return []
    if token == "{}":
        return {}
    if token.startswith("["):
        return _parse_inline_sequence(token, source=source, lineno=lineno)
    if token.startswith("{"):
        return _parse_inline_mapping(token, source=source, lineno=lineno)
    if token.startswith('"') or token.startswith("'"):
        return _parse_quoted(token, source=source, lineno=lineno)
    if token == "true":
        return True
    if token == "false":
        return False
    if _PLAIN_INT_RE.fullmatch(token):
        if len(token) > 1 and token[0] == "0":
            return token
        if len(token) > 2 and token.startswith("-0"):
            return token
        return int(token)
    return token


def _parse_inline_sequence(token: str, *, source: str, lineno: int) -> list[Any]:
    if not token.endswith("]"):
        raise YamlError(f"{source}:{lineno}: inline sequence is missing a closing ]")
    inner = token[1:-1].strip()
    if inner == "":
        return []
    return [
        _parse_inline_value(part, source=source, lineno=lineno)
        for part in _split_top_level(inner, ",", source=source, lineno=lineno)
    ]


def _parse_inline_mapping(token: str, *, source: str, lineno: int) -> dict[str, Any]:
    if not token.endswith("}"):
        raise YamlError(f"{source}:{lineno}: inline mapping is missing a closing }}")
    inner = token[1:-1].strip()
    if inner == "":
        return {}
    mapping: dict[str, Any] = {}
    for part in _split_top_level(inner, ",", source=source, lineno=lineno):
        key, rest = _split_key_value(part, source=source, lineno=lineno)
        if key == "<<":
            raise YamlError(f"{source}:{lineno}: merge keys are not supported")
        mapping[key] = _parse_inline_value(rest, source=source, lineno=lineno)
    return mapping


def _parse_quoted(token: str, *, source: str, lineno: int) -> str:
    quote = token[0]
    if len(token) < 2 or token[-1] != quote:
        raise YamlError(f"{source}:{lineno}: unterminated quoted string")
    body = token[1:-1]
    if quote == '"':
        return _unescape_double_quoted(body, source=source, lineno=lineno)
    return body.replace("''", "'")


def _unescape_double_quoted(body: str, *, source: str, lineno: int) -> str:
    out: list[str] = []
    i = 0
    while i < len(body):
        char = body[i]
        if char != "\\":
            out.append(char)
            i += 1
            continue
        i += 1
        if i >= len(body):
            raise YamlError(f"{source}:{lineno}: trailing backslash in quoted string")
        esc = body[i]
        out.append({
            "\\": "\\",
            '"': '"',
            "n": "\n",
            "t": "\t",
        }.get(esc, esc))
        i += 1
    return "".join(out)


def _split_key_value(text: str, *, source: str, lineno: int) -> tuple[str, str]:
    colon = _find_top_level_colon(text)
    if colon == -1:
        raise YamlError(f"{source}:{lineno}: mapping entry is missing ':'")
    key = text[:colon].strip()
    if not key:
        raise YamlError(f"{source}:{lineno}: mapping entry is missing a key")
    return key, text[colon + 1 :].strip()


def _find_top_level_colon(text: str) -> int:
    quote: str | None = None
    bracket_depth = 0
    brace_depth = 0
    i = 0
    while i < len(text):
        char = text[i]
        if quote == '"':
            if char == "\\":
                i += 2
                continue
            if char == '"':
                quote = None
            i += 1
            continue
        if quote == "'":
            if char == "'" and i + 1 < len(text) and text[i + 1] == "'":
                i += 2
                continue
            if char == "'":
                quote = None
            i += 1
            continue
        if char in {'"', "'"}:
            quote = char
            i += 1
            continue
        if char == "[":
            bracket_depth += 1
        elif char == "]":
            bracket_depth -= 1
        elif char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth -= 1
        elif char == ":" and bracket_depth == 0 and brace_depth == 0:
            return i
        i += 1
    return -1


def _split_top_level(text: str, separator: str, *, source: str, lineno: int) -> list[str]:
    parts: list[str] = []
    quote: str | None = None
    bracket_depth = 0
    brace_depth = 0
    start = 0
    i = 0
    while i < len(text):
        char = text[i]
        if quote == '"':
            if char == "\\":
                i += 2
                continue
            if char == '"':
                quote = None
            i += 1
            continue
        if quote == "'":
            if char == "'" and i + 1 < len(text) and text[i + 1] == "'":
                i += 2
                continue
            if char == "'":
                quote = None
            i += 1
            continue
        if char in {'"', "'"}:
            quote = char
            i += 1
            continue
        if char == "[":
            bracket_depth += 1
        elif char == "]":
            bracket_depth -= 1
        elif char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth -= 1
        elif char == separator and bracket_depth == 0 and brace_depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
        i += 1
    if quote is not None or bracket_depth != 0 or brace_depth != 0:
        raise YamlError(f"{source}:{lineno}: malformed inline YAML value")
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    elif text.endswith(separator):
        raise YamlError(f"{source}:{lineno}: trailing separator in inline YAML value")
    return parts


def _is_sequence_line(text: str) -> bool:
    return text == "-" or text.startswith("- ")
