"""Lock: every MCP Tool listed in `TOOLS` has a `_DISPATCH` entry, and
vice-versa.

This catches the v0.5.0-class regression where a tool was added to one
list but forgotten in the other — the MCP server would either advertise
a tool that crashed with "unknown tool" on invocation, or silently
expose a dispatcher that no client could discover.
"""
from __future__ import annotations

from report_skill.mcp_server import _DISPATCH, TOOLS


def test_tool_names_match_dispatch_keys() -> None:
    tool_names = {t.name for t in TOOLS}
    dispatch_keys = set(_DISPATCH.keys())
    only_in_tools = sorted(tool_names - dispatch_keys)
    only_in_dispatch = sorted(dispatch_keys - tool_names)
    assert tool_names == dispatch_keys, (
        f"TOOLS / _DISPATCH out of sync:\n"
        f"  only in TOOLS:    {only_in_tools}\n"
        f"  only in DISPATCH: {only_in_dispatch}"
    )


def test_tool_names_are_unique() -> None:
    names = [t.name for t in TOOLS]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    assert not duplicates, f"duplicate Tool name(s) in TOOLS: {duplicates}"
