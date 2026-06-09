"""RACI matrix adapter — activity rows × role columns, cells like 'R', 'A/C'."""
from __future__ import annotations

import re
from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import nearest_enum, to_slug, truncate

_RACI_CODES = ["R", "A", "C", "I"]
_KO_TO_RACI = {
    "실행": "R", "책임": "A", "자문": "C", "통보": "I",
    "수행": "R", "승인": "A", "협의": "C", "공유": "I",
}
_CELL_RE = re.compile(r"^([RACI](/[RACI])*)?$")


class RaciMatrixAdapter(WidgetAdapter):
    type = "raci_matrix"

    def normalize(self, raw: Any, props: dict) -> dict:
        out: dict = {}
        rows_in: list[Any]

        if isinstance(raw, dict):
            if "caption" in raw:
                out["caption"] = truncate(str(raw["caption"]), 200)
            # v0.9.2 — RA defcb74 caption color tokens
            if isinstance(raw.get("caption_color"), str):
                out["caption_color"] = raw["caption_color"]
            if isinstance(raw.get("caption_html"), str):
                out["caption_html"] = raw["caption_html"][:2000]
            if isinstance(raw.get("roles"), list):
                out["roles"] = _coerce_roles(raw["roles"])
            if isinstance(raw.get("rows"), list):
                rows_in = raw["rows"]
            else:
                # activity → {role: code} mapping
                ignored = {"caption", "roles", "rows"}
                rows_in = [{"activity": k, "assignments": v}
                           for k, v in raw.items() if k not in ignored]
        elif isinstance(raw, list):
            rows_in = raw
        else:
            raise NormalizeError(f"raci_matrix: unsupported input type {type(raw).__name__}")

        seen_roles: dict[str, dict] = {r["key"]: r for r in out.get("roles", [])}
        rows_out: list[dict] = []
        for entry in rows_in:
            row = self._coerce_row(entry, seen_roles)
            if row is not None:
                rows_out.append(row)

        if not rows_out:
            raise NormalizeError("raci_matrix: no rows after normalization")
        if seen_roles and "roles" not in out:
            out["roles"] = list(seen_roles.values())
        out["rows"] = rows_out
        return out

    def _coerce_row(self, entry: Any, seen_roles: dict[str, dict]) -> dict | None:
        if not isinstance(entry, dict):
            return None
        label = str(entry.get("label") or entry.get("activity") or "").strip()
        if not label:
            return None
        row: dict = {"label": truncate(label, 200)}
        if entry.get("note"):
            row["note"] = truncate(str(entry["note"]), 500)

        assignments_in = entry.get("assignments")
        if assignments_in is None:
            # Maybe flat dict: {role: code, ...} beyond label/note
            assignments_in = {k: v for k, v in entry.items()
                              if k not in {"label", "activity", "note", "assignments"}}
        if isinstance(assignments_in, dict) and assignments_in:
            out_assign: dict = {}
            for role_name, code in assignments_in.items():
                key = to_slug(str(role_name))
                if not key:
                    continue
                if key not in seen_roles:
                    seen_roles[key] = {"key": key, "label": truncate(str(role_name), 100)}
                cell = _normalize_cell(code)
                if cell:
                    out_assign[key] = cell
            if out_assign:
                row["assignments"] = out_assign
        return row

    def fallback_to(self) -> str:
        return "table"


def _coerce_roles(roles_in: list[Any]) -> list[dict]:
    out: list[dict] = []
    for r in roles_in:
        if isinstance(r, str):
            k = to_slug(r)
            if k:
                out.append({"key": k, "label": truncate(r, 100)})
        elif isinstance(r, dict):
            key = to_slug(str(r.get("key") or r.get("label") or ""))
            if not key:
                continue
            entry: dict = {"key": key}
            if r.get("label"):
                entry["label"] = truncate(str(r["label"]), 100)
            if r.get("group"):
                entry["group"] = truncate(str(r["group"]), 100)
            out.append(entry)
    return out


def _normalize_cell(code: Any) -> str:
    """Coerce a cell value → 'R', 'A/C', etc. Accepts Korean tokens."""
    if code is None:
        return ""
    s = str(code).strip()
    if not s:
        return ""
    parts: list[str] = []
    for raw in re.split(r"[/,\s]+", s):
        if not raw:
            continue
        if raw in _KO_TO_RACI:
            parts.append(_KO_TO_RACI[raw])
            continue
        m = nearest_enum(raw.upper(), _RACI_CODES)
        if m is not None:
            parts.append(m)
    cell = "/".join(parts)
    return cell if _CELL_RE.match(cell) else ""
