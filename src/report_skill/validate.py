"""jsonschema validation with human-readable error messages.

Wraps the `jsonschema` package and turns the verbose Draft7 error output
into one-line messages like:
    "items[2].text: '' shorter than 1 char"
    "rows[0].severity: '중간' not in enum [낮음, 보통, 높음]"
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import jsonschema
from jsonschema import Draft7Validator


@dataclass(frozen=True)
class ValidationIssue:
    path: str          # JSON pointer-ish: "items[2].text"
    message: str       # human one-liner
    raw: jsonschema.ValidationError

    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message


def _format_path(absolute_path: Iterable) -> str:
    parts: list[str] = []
    for p in absolute_path:
        if isinstance(p, int):
            parts.append(f"[{p}]")
        else:
            parts.append(f".{p}" if parts else str(p))
    return "".join(parts)


def _humanize(err: jsonschema.ValidationError) -> str:
    v = err.validator
    val = err.instance
    if v == "required":
        msg = err.message
        if "'" in msg:
            try:
                field = msg.split("'")[1]
                return f"missing required field '{field}'"
            except IndexError:
                pass
        return msg
    if v == "enum":
        return f"{val!r} not in enum {err.schema.get('enum')}"
    if v == "type":
        expected = err.schema.get("type")
        return f"expected {expected}, got {type(val).__name__} ({val!r})"
    if v == "maxLength":
        return f"length {len(val)} exceeds maxLength {err.schema['maxLength']}"
    if v == "minLength":
        return f"length {len(val)} below minLength {err.schema['minLength']}"
    if v == "minimum":
        return f"{val} below minimum {err.schema['minimum']}"
    if v == "maximum":
        return f"{val} above maximum {err.schema['maximum']}"
    if v == "pattern":
        return f"{val!r} doesn't match pattern {err.schema['pattern']!r}"
    if v == "additionalProperties":
        return f"unexpected fields present ({err.message})"
    return err.message


def validate(instance: Any, schema: dict) -> list[ValidationIssue]:
    """Return list of ValidationIssue. Empty list ⇒ valid."""
    if schema is None:
        return []
    validator = Draft7Validator(schema)
    issues: list[ValidationIssue] = []
    for err in sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path)):
        issues.append(ValidationIssue(
            path=_format_path(err.absolute_path),
            message=_humanize(err),
            raw=err,
        ))
    return issues


def is_valid(instance: Any, schema: dict) -> bool:
    return not validate(instance, schema)
