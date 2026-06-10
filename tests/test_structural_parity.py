"""Generic structural-parity locks (v0.13.0).

The project's recurring failure mode is "layer N updated, layer N+1
forgotten": a typed exception added to client.py but never mapped in
mcp_server, a write method added without logging, a passthrough key added
to an adapter but never reflected in the bundled snapshot, and so on
(6+ incidents). Earlier locks were per-incident hardcoded lists; these four
are GENERIC — they discover layer N via reflection / AST and assert layer
N+1 keeps up, so the next addition is caught without editing this file.

Locks:
  (a) every ApiError subclass is registered in mcp_server's typed-error map
  (b) every ApiError subclass is handled (or explicitly exempted) in cli*.py
  (c) every ReportArchiveClient write method logs (logger.info/debug)
  (d) every adapter passthrough key exists in the bundled widget snapshot
      (hard lock since v0.13.1 — snapshot regenerated 2026-06-10; keep it
      fresh via scripts/refresh_bundled_data.py before each release)
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

# Ensure in-tree src/ is importable (same nudge as conftest.py).
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import report_skill  # noqa: E402
from report_skill import mcp_server  # noqa: E402

_PKG_DIR = Path(report_skill.__file__).resolve().parent
_CLIENT_PY = _PKG_DIR / "client.py"
# cli.py + cli_*.py — NOT glob("cli*.py"), which would also match client.py
# (its `except (httpx.RequestError, NetworkUnreachableError)` would then count
# as a CLI handler and silently satisfy the lock).
_CLI_FILES = sorted([_PKG_DIR / "cli.py", *_PKG_DIR.glob("cli_*.py")])


# --------------------------------------------------------------------------- #
# shared reflection / AST helpers
# --------------------------------------------------------------------------- #
def _all_api_error_subclasses(cls=None):
    """Yield every ApiError subclass transitively (ApiError itself excluded —
    it is the base envelope, not a typed error)."""
    from report_skill.client import ApiError
    cls = cls or ApiError
    for sub in cls.__subclasses__():
        yield sub
        yield from _all_api_error_subclasses(sub)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _handled_exception_names(tree: ast.Module) -> set[str]:
    """Names appearing in `except` handler types across a module.

    Handles ast.Name, ast.Tuple and ast.Attribute handler types, and expands
    module-level tuple aliases (cli.py groups typed errors in
    `_TYPED_LOCK_ERRORS = (LockHeldByOtherError, ...)` and catches the tuple
    by name — those members count as handled).
    """
    aliases: dict[str, set[str]] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Tuple)
        ):
            members = {e.id for e in node.value.elts if isinstance(e, ast.Name)}
            if members:
                aliases[node.targets[0].id] = members

    handled: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or node.type is None:
            continue
        types = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
        for t in types:
            if isinstance(t, ast.Name):
                handled |= aliases.get(t.id, {t.id})
            elif isinstance(t, ast.Attribute):
                handled.add(t.attr)
    return handled


# --------------------------------------------------------------------------- #
# (a) client.py typed exception → mcp_server typed-error map
# --------------------------------------------------------------------------- #
# Subclasses intentionally absent from the typed-error map. Must stay empty,
# or carry a justification comment per entry.
_MAP_EXEMPT: set[str] = set()


def test_every_typed_exception_in_typed_error_map():
    mapped = {cls for cls, _, _ in mcp_server._build_typed_error_map()}
    missing = sorted(
        cls.__name__
        for cls in _all_api_error_subclasses()
        if cls not in mapped and cls.__name__ not in _MAP_EXEMPT
    )
    assert not missing, (
        "ApiError subclasses missing from mcp_server._build_typed_error_map() "
        f"(register them or add to _MAP_EXEMPT with a justification): {missing}"
    )
    stale = sorted(
        name for name in _MAP_EXEMPT
        if any(c.__name__ == name and c in mapped for c in _all_api_error_subclasses())
    )
    assert not stale, f"_MAP_EXEMPT entries now mapped — remove them: {stale}"


# --------------------------------------------------------------------------- #
# (b) client.py typed exception → cli*.py except handlers
# --------------------------------------------------------------------------- #
# Typed exceptions that genuinely never need a dedicated CLI handler — they
# reach the user as ApiError text via the generic `except ApiError` fallback
# that every command carries, which is acceptable UX for these cases.
_CLI_EXEMPT: set[str] = {
    # v0.13.0 — surfaced via ApiError text; promote to typed handlers when
    # CLI UX demands.
    "NetworkUnreachableError",
    "AuthUnavailableError",
    "MountForbiddenError",
    "MountTargetInvalidError",
    "ReportStillMountedError",
}


def test_cli_handles_every_typed_exception():
    assert _CLI_FILES, f"no cli*.py found under {_PKG_DIR}"
    handled: set[str] = set()
    for path in _CLI_FILES:
        handled |= _handled_exception_names(_parse(path))

    required = {cls.__name__ for cls in _all_api_error_subclasses()}
    missing = sorted(required - handled - _CLI_EXEMPT)
    assert not missing, (
        "ApiError subclasses with no `except` handler in any cli*.py "
        "(add a typed handler before the generic ApiError catch, or add to "
        f"_CLI_EXEMPT with a justification): {missing}"
    )
    stale = sorted(_CLI_EXEMPT & handled)
    assert not stale, f"_CLI_EXEMPT entries now handled in cli*.py — remove them: {stale}"


# --------------------------------------------------------------------------- #
# (c) ReportArchiveClient write methods must log
# --------------------------------------------------------------------------- #
# HTTP plumbing / read-only / lifecycle methods — not subject to the rule.
_LOGGING_SKIP: set[str] = {
    "__init__", "__enter__", "__exit__", "close",
    "login", "ensure_logged_in",
    "get", "post", "_post", "_request", "_build_typed_error",
}
# Write methods still lacking logger.info/debug — burn this set down to empty.
_LOGGING_BASELINE: set[str] = set()

_WRITE_HTTP_METHODS = {"POST", "PATCH", "DELETE", "PUT"}


def _is_write_call(call: ast.Call) -> bool:
    f = call.func
    if not (
        isinstance(f, ast.Attribute)
        and isinstance(f.value, ast.Name)
        and f.value.id == "self"
    ):
        return False
    if f.attr in ("post", "_post"):
        return True
    if f.attr == "_request" and call.args:
        a0 = call.args[0]
        return (
            isinstance(a0, ast.Constant)
            and isinstance(a0.value, str)
            and a0.value.upper() in _WRITE_HTTP_METHODS
        )
    return False


def _calls_logger(fn: ast.FunctionDef) -> bool:
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("info", "debug")
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "logger"
        ):
            return True
    return False


def test_write_methods_have_logging():
    tree = _parse(_CLIENT_PY)
    cls = next(
        (n for n in tree.body
         if isinstance(n, ast.ClassDef) and n.name == "ReportArchiveClient"),
        None,
    )
    assert cls is not None, "class ReportArchiveClient not found in client.py"

    unlogged: list[str] = []
    for fn in cls.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.name in _LOGGING_SKIP or fn.name in _LOGGING_BASELINE:
            continue
        is_write = any(
            isinstance(node, ast.Call) and _is_write_call(node)
            for node in ast.walk(fn)
        )
        if is_write and not _calls_logger(fn):
            unlogged.append(fn.name)

    assert not unlogged, (
        "ReportArchiveClient write methods without logger.info/logger.debug "
        f"(add logging, or park temporarily in _LOGGING_BASELINE): {sorted(unlogged)}"
    )


# --------------------------------------------------------------------------- #
# (d) adapter passthrough keys → bundled snapshot content_schema
# --------------------------------------------------------------------------- #
def _all_property_names(schema) -> set[str]:
    """Every key of every `properties` dict anywhere in a JSON schema —
    passthrough keys may live at the top level or nested in item schemas."""
    out: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict):
                out.update(props.keys())
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    return out


def test_adapter_passthrough_known_to_snapshot():
    from report_skill.adapters import ADAPTERS

    snap_path = _PKG_DIR / "data" / "widgets.snapshot.json"
    data = json.loads(snap_path.read_text(encoding="utf-8"))
    widgets = data["widgets"]

    failures: list[str] = []
    checked = 0
    for wtype, adapter in sorted(ADAPTERS.items()):
        module = sys.modules[type(adapter).__module__]
        keys: list[str] = []
        for attr in ("_PASSTHROUGH", "_PASSTHROUGH_SIMPLE"):
            val = getattr(module, attr, None)
            if val:
                keys.extend(val)
        if not keys:
            continue
        checked += 1
        entry = widgets.get(wtype)
        if entry is None:
            failures.append(f"{wtype}: widget missing from snapshot")
            continue
        known = _all_property_names(entry.get("content_schema") or {})
        for k in keys:
            if k not in known:
                failures.append(
                    f"{wtype}: passthrough key {k!r} not in snapshot content_schema"
                )

    assert checked, "no adapter module defines _PASSTHROUGH/_PASSTHROUGH_SIMPLE"
    assert not failures, (
        f"{len(failures)} passthrough key(s) unknown to the bundled snapshot:\n  "
        + "\n  ".join(failures)
    )
