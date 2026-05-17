from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


DEFAULT_LIMIT = 20
MAX_CELL_CHARS = 180


@dataclass
class AxiError(Exception):
    code: str
    message: str
    hint: str | None = None


def unwrap_mcp_text(response: Any) -> Any:
    """Convert postgres-mcp TextContent responses into Python values when possible."""
    if isinstance(response, list) and response:
        first = response[0]
        text = getattr(first, "text", first)
    else:
        text = response

    if not isinstance(text, str):
        return text

    if text.startswith("Error: "):
        raise AxiError(code="upstream_error", message=text.removeprefix("Error: "))
    if text.startswith("Error "):
        raise AxiError(code="upstream_error", message=text.removeprefix("Error "))

    try:
        return ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return text


def render_error(error: AxiError) -> str:
    lines = ["error:", f"  code: {error.code}", f"  message: {error.message}"]
    if error.hint:
        lines.extend(["help[1]:", f"  {error.hint}"])
    return "\n".join(lines)


def render_value(
    value: Any,
    *,
    name: str = "result",
    limit: int = DEFAULT_LIMIT,
    fields: Sequence[str] | None = None,
    help: Sequence[str] = (),
) -> str:
    if value is None:
        lines = [f"{name}: null"]
    elif isinstance(value, str):
        structured = _parse_structured_text(value)
        if structured is not value:
            return render_value(structured, name=name, limit=limit, fields=fields, help=help)
        lines = _render_text(name, value, limit)
    elif isinstance(value, Mapping):
        lines = _render_mapping(name, value, fields=fields, limit=limit)
    elif _is_row_sequence(value):
        lines = _render_rows(name, list(value), fields=fields, limit=limit)
    else:
        lines = [f"{name}: {value}"]

    if help:
        lines.extend(_render_help(help))
    return "\n".join(lines)


def _render_text(name: str, text: str, limit: int) -> list[str]:
    max_chars = max(limit, 1) * 240
    if len(text) <= max_chars:
        return [f"{name}: |", *_indent_block(text)]

    clipped = _clip_text(text, max_chars)
    return [
        f"{name}: |",
        *_indent_block(clipped),
        f"note: truncated at {max_chars} chars, use --full for more",
    ]


def _render_mapping(
    name: str,
    value: Mapping[str, Any],
    *,
    fields: Sequence[str] | None,
    limit: int,
) -> list[str]:
    if "error" in value:
        hypopg_error = _compact_hypopg_error(value["error"])
        if hypopg_error:
            return [
                f"{name}:",
                "  status: unavailable",
                f"  error: {hypopg_error}",
            ]
        return [
            f"{name}:",
            "  error:",
            "    code: upstream_error",
            "    message: |",
            *_indent_block(str(value["error"]), spaces=6),
        ]

    selected = list(fields) if fields else list(value.keys())
    lines = [f"{name}:"]
    for key in selected:
        if key.startswith("_"):
            continue
        if key not in value:
            continue
        child = value[key]
        if _is_row_sequence(child):
            lines.extend(_render_rows(str(key), list(child), limit=limit))
        elif isinstance(child, Mapping):
            lines.append(f"  {key}:")
            for nested_key, nested_value in child.items():
                lines.append(f"    {nested_key}: {_scalar(nested_value)}")
        else:
            lines.append(f"  {key}: {_scalar(child)}")
    return lines


def _render_rows(
    name: str,
    rows: list[Any],
    *,
    fields: Sequence[str] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[str]:
    if not rows:
        return [f"{name}[0]: none"]

    normalized = [_normalize_row(row) for row in rows]
    selected_fields = list(fields) if fields else _default_fields(normalized)
    shown = normalized[:limit]
    suffix = f" of {len(normalized)}" if len(normalized) > len(shown) else ""

    lines = [f"{name}[{len(shown)}{suffix}]{{{','.join(selected_fields)}}}:"]
    for row in shown:
        lines.append("  " + ",".join(_csvish(row.get(field)) for field in selected_fields))
    if _has_insufficient_privilege_query(normalized):
        lines.append(
            "note: query text is hidden by PostgreSQL privileges; use a role that can view pg_stat_statements query text"
        )
    if len(normalized) > len(shown):
        lines.append(f"note: truncated, use --limit {len(normalized)}, --full, or narrower filters")
    return lines


def _render_help(commands: Sequence[str]) -> list[str]:
    lines = [f"help[{len(commands)}]:"]
    lines.extend(f"  {command}" for command in commands)
    return lines


def _default_fields(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    if not rows:
        return []
    keys = list(rows[0].keys())
    return keys[:4]


def _normalize_row(row: Any) -> Mapping[str, Any]:
    if isinstance(row, Mapping):
        return row
    if isinstance(row, Sequence) and not isinstance(row, str):
        return {f"c{i + 1}": value for i, value in enumerate(row)}
    return {"value": row}


def _is_row_sequence(value: Any) -> bool:
    return isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping))


def _parse_structured_text(text: str) -> Any:
    stripped = text.strip()
    for candidate in _literal_candidates(stripped):
        try:
            parsed = ast.literal_eval(candidate)
        except (SyntaxError, ValueError):
            continue
        if isinstance(parsed, Mapping) or _is_row_sequence(parsed):
            return parsed
    return text


def _literal_candidates(text: str) -> Iterable[str]:
    yield text
    for marker in ("[", "{"):
        start = text.find(marker)
        if start > 0:
            yield text[start:]


def _compact_hypopg_error(error: Any) -> str | None:
    text = str(error)
    if "hypopg" not in text.lower():
        return None
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line.replace("**", "")
    return "HypoPG is unavailable."


def _has_insufficient_privilege_query(rows: Sequence[Mapping[str, Any]]) -> bool:
    return any(row.get("query") == "<insufficient privilege>" for row in rows)


def _scalar(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_scalar(item) for item in value) + "]"
    if isinstance(value, Mapping):
        return "{" + ",".join(f"{k}:{_scalar(v)}" for k, v in value.items()) + "}"
    return _csvish(value)


def _csvish(value: Any) -> str:
    if value is None:
        return "null"
    text = str(value).replace("\n", "\\n")
    if len(text) > MAX_CELL_CHARS:
        text = text[: MAX_CELL_CHARS - 1].rstrip() + "…"
    if "," in text or ":" in text:
        return repr(text)
    return text


def _clip_text(text: str, max_chars: int) -> str:
    clipped = text[:max_chars].rstrip()
    if "\n" not in clipped:
        return clipped
    complete_lines = clipped.splitlines()[:-1]
    return "\n".join(complete_lines).rstrip() or clipped


def _indent_block_with_spaces(text: str, spaces: int) -> list[str]:
    prefix = " " * spaces
    return [f"{prefix}{line}" for line in text.splitlines()]


def _indent_block(text: str, spaces: int = 2) -> list[str]:
    return _indent_block_with_spaces(text, spaces)
