"""v0.7.0 — coverage for the `widget_relations_list` MCP tool and the
overall `_DISPATCH` cardinality invariant.

`widget_relations_list` is a thin GET wrapper around
`/api/widget-relations` used by the rich_text mention-chip resolver. It's
read-only and arg-less, so the dispatcher just forwards to
`client.list_widget_relations()`. The smoke test ensures that wiring
hasn't bit-rotted (a refactor renaming the client method would break
the chip resolver silently).

`test_dispatch_count_is_66` pins the count: every new MCP tool needs a
matching schema entry on the LLM side, and this test forces a deliberate
bump when a tool is added so it isn't accidentally hidden behind an
unrelated commit.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from report_skill import mcp_server
from report_skill.mcp_server import _DISPATCH


# --------------------------------------------------------------------------- #
# Fake-client harness (mirrors test_dispatch_parametrized / test_mcp_roundtrip).
# --------------------------------------------------------------------------- #
def _install_fake_client(monkeypatch, client_mock: MagicMock) -> None:
    class _FakeClientCtor:
        def __call__(self, *_a, **_kw):
            return self

        def __enter__(self):
            return client_mock

        def __exit__(self, *_exc):
            return None

    monkeypatch.setattr(mcp_server, "ReportArchiveClient", _FakeClientCtor())


# --------------------------------------------------------------------------- #
# 1) Dispatcher smoke — widget_relations_list returns whatever the client
#    returns (no transformation, no envelope unwrap on top of what
#    `client.list_widget_relations` already does).
# --------------------------------------------------------------------------- #
def test_widget_relations_list_dispatch(monkeypatch) -> None:
    """`_DISPATCH['widget_relations_list']({})` should call
    `client.list_widget_relations()` and pass through the result unchanged.
    A future refactor that renames the client method or wraps the result
    in an envelope must update this test (and the chip-resolver consumer)."""
    fake_relations = [
        {"slug": "report", "label": "보고서"},
        {"slug": "dept", "label": "부서"},
        {"slug": "entity", "label": "엔티티"},
    ]
    client_mock = MagicMock()
    client_mock.list_widget_relations.return_value = fake_relations
    _install_fake_client(monkeypatch, client_mock)

    fn = _DISPATCH["widget_relations_list"]
    result = fn({})

    client_mock.list_widget_relations.assert_called_once_with()
    assert result == fake_relations, (
        f"dispatcher should pass through client.list_widget_relations() "
        f"unchanged; got {result!r}"
    )


# --------------------------------------------------------------------------- #
# 2) Cardinality invariant — pins the total tool count so adding a tool
#    requires a deliberate test bump.
# --------------------------------------------------------------------------- #
def test_dispatch_count_is_82() -> None:
    """v0.10.0 surface: 82 MCP tools.

    When you add a tool to `_DISPATCH`, bump this number (and add the
    matching `_ARGS_BY_TOOL` entry in test_dispatch_parametrized.py).
    Pinning the count prevents silent surface growth — every new tool
    should be a deliberate, reviewed addition with schema + test
    coverage attached."""
    assert len(_DISPATCH) == 82, (
        f"expected 82 MCP tools in _DISPATCH, got {len(_DISPATCH)}. "
        "Bump this assertion and update test_dispatch_parametrized.py's "
        "_ARGS_BY_TOOL when adding a tool."
    )
