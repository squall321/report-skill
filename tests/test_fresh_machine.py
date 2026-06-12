"""Fresh-machine readiness regressions (v0.16.0).

These lock the fixes that came out of the cold-install audit — the failure
modes a fake-client unit test would never surface on a dev box but that bite
a brand-new Windows machine:

  * config raises ConfigError (NOT SystemExit) so a long-lived MCP server
    can survive an unconfigured first call (audit B1)
  * .env is read as utf-8-sig so a PowerShell BOM doesn't disable the first
    var (audit M9)
  * non-Settings .env keys (REPORT_FRONTEND_URL, SKILL_LLM_*, ...) are
    exported into the process env (audit M10)
  * %LOCALAPPDATA%\\report-skill\\.env is in the discovery chain (audit M6)
  * client._request accepts a `headers` kwarg — reports_list passed one and
    raised TypeError on every call (audit M3)
  * the client honors trust_env / verify / CA-bundle transport settings
    (audit M1 / N1)
  * call_tool maps ConfigError → not_configured and SystemExit → system_exit
    instead of hanging the server (audit B1)
  * catalog sync refuses with an actionable error on a receiver install
    where REPORT_BACKEND_PATH is blank (audit M4)
"""
from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@pytest.fixture(autouse=True)
def _restore_config_module():
    """The discovery tests reload report_skill.config under controlled cwd/env.
    That swaps the module object in sys.modules, which orphans the ConfigError
    class identity that mcp_server/client/cli bound at THEIR import time. Snap
    the original module and restore it after each test so later tests (and the
    rest of the suite) keep a single, shared ConfigError class."""
    orig = sys.modules.get("report_skill.config")
    yield
    if orig is not None:
        sys.modules["report_skill.config"] = orig


# --------------------------------------------------------------------------- #
# config — ConfigError, BOM tolerance, env export, discovery chain
# --------------------------------------------------------------------------- #
def _fresh_config(monkeypatch, tmp_path: Path, env_text,
                  *, localappdata: Path | None = None,
                  via_skill_env: bool = False, hide_dev_env: bool = False):
    """Re-import report_skill.config with a controlled cwd + env so module
    import-time resolution (PROJECT_ROOT / ENV_PATH) runs against the fixture.

    The dev checkout's own D:\\report-skill\\.env sits at the `dev_root`
    discovery step (parents[2] of config.py), so a test that just chdir's
    into tmp would still resolve to the real dev .env. Two escape hatches:
      * via_skill_env=True  → point REPORT_SKILL_ENV at the fixture .env
        (step 1 wins, deterministic — used by the parsing tests)
      * hide_dev_env=True   → monkeypatch Path.is_file so the dev_root .env
        reports absent, exercising the later discovery steps
    Returns the freshly-imported module."""
    monkeypatch.delenv("REPORT_SKILL_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    if localappdata is not None:
        monkeypatch.setenv("LOCALAPPDATA", str(localappdata))
    else:
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
    fixture_env = tmp_path / ".env"
    if env_text is not None:
        fixture_env.write_bytes(env_text.encode("utf-8")
                                if isinstance(env_text, str) else env_text)
    if via_skill_env:
        monkeypatch.setenv("REPORT_SKILL_ENV", str(fixture_env))
    if hide_dev_env:
        _dev_env = (Path(__file__).resolve().parents[1] / ".env").resolve()
        _orig_is_file = Path.is_file

        def _patched_is_file(self):
            if self.resolve() == _dev_env:
                return False
            return _orig_is_file(self)
        monkeypatch.setattr(Path, "is_file", _patched_is_file)
    # Drop cached config + anything that imported `settings` from it.
    for mod in list(sys.modules):
        if mod == "report_skill.config" or mod.startswith("report_skill.config."):
            del sys.modules[mod]
    return importlib.import_module("report_skill.config")


def test_missing_credentials_raises_configerror_not_systemexit(monkeypatch, tmp_path):
    """An unconfigured install must raise ConfigError (catchable), NOT
    SystemExit (which escaped the MCP server's except-handler and hung it)."""
    monkeypatch.delenv("REPORT_API_EMAIL", raising=False)
    monkeypatch.delenv("REPORT_API_PASSWORD", raising=False)
    monkeypatch.delenv("REPORT_API_BASE_URL", raising=False)
    # via_skill_env points REPORT_SKILL_ENV at a .env that does NOT exist
    # (env_text=None), so no file + no env vars → required fields missing.
    cfg = _fresh_config(monkeypatch, tmp_path, env_text=None, via_skill_env=True)
    with pytest.raises(cfg.ConfigError) as ei:
        _ = cfg.settings.report_api_email
    assert not isinstance(ei.value, SystemExit)
    # carries the parsed missing-var list (pydantic v2 e.errors() walk)
    missing = getattr(ei.value, "missing_vars", [])
    assert any("EMAIL" in m or "PASSWORD" in m for m in missing), missing


def test_env_bom_does_not_disable_first_var(monkeypatch, tmp_path):
    """A UTF-8 BOM (what PS5.1 Out-File -Encoding utf8 writes) must not glue
    onto REPORT_API_BASE_URL and silently disable it."""
    body = (
        "REPORT_API_BASE_URL=http://bom-host:3000/api\n"
        "REPORT_API_EMAIL=a@b.c\n"
        "REPORT_API_PASSWORD=pw\n"
    )
    bom = b"\xef\xbb\xbf" + body.encode("utf-8")
    monkeypatch.delenv("REPORT_API_BASE_URL", raising=False)
    cfg = _fresh_config(monkeypatch, tmp_path, env_text=bom, via_skill_env=True)
    assert cfg.settings.report_api_base_url == "http://bom-host:3000/api", (
        "BOM disabled the first var — env_file_encoding must be utf-8-sig"
    )


def test_extra_env_keys_exported_to_process_env(monkeypatch, tmp_path):
    """Non-Settings keys in .env (REPORT_FRONTEND_URL etc.) are os.environ-only
    in the code, so the loader must export them or they silently do nothing."""
    monkeypatch.delenv("REPORT_FRONTEND_URL", raising=False)
    monkeypatch.delenv("SKILL_LLM_PROVIDER", raising=False)
    env_text = (
        "REPORT_API_BASE_URL=http://h:3000/api\n"
        "REPORT_API_EMAIL=a@b.c\n"
        "REPORT_API_PASSWORD=pw\n"
        "REPORT_FRONTEND_URL=http://frontend:3001\n"
        'SKILL_LLM_PROVIDER=bridge\n'
    )
    import os
    cfg = _fresh_config(monkeypatch, tmp_path, env_text=env_text)
    assert cfg.ENV_PATH.is_file()
    assert os.environ.get("REPORT_FRONTEND_URL") == "http://frontend:3001"
    assert os.environ.get("SKILL_LLM_PROVIDER") == "bridge"


def test_real_process_env_wins_over_dotenv(monkeypatch, tmp_path):
    """setdefault semantics: a value already in the real process env must not
    be overwritten by the .env export."""
    import os
    monkeypatch.setenv("REPORT_FRONTEND_URL", "http://real-env:9999")
    env_text = (
        "REPORT_API_BASE_URL=http://h:3000/api\n"
        "REPORT_API_EMAIL=a@b.c\nREPORT_API_PASSWORD=pw\n"
        "REPORT_FRONTEND_URL=http://dotenv:3001\n"
    )
    _fresh_config(monkeypatch, tmp_path, env_text=env_text)
    assert os.environ.get("REPORT_FRONTEND_URL") == "http://real-env:9999"


def test_localappdata_env_in_discovery_chain(monkeypatch, tmp_path):
    """%LOCALAPPDATA%\\report-skill\\.env must resolve when no cwd/repo .env
    exists — env-scrubbed MCP launches relied on this (audit M6)."""
    workdir = tmp_path / "somewhere-else"
    workdir.mkdir()
    la = tmp_path / "LocalAppData"
    (la / "report-skill").mkdir(parents=True)
    (la / "report-skill" / ".env").write_text(
        "REPORT_API_BASE_URL=http://la-host:3000/api\n"
        "REPORT_API_EMAIL=a@b.c\nREPORT_API_PASSWORD=pw\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("REPORT_API_BASE_URL", raising=False)
    cfg = _fresh_config(monkeypatch, workdir, env_text=None, localappdata=la,
                        hide_dev_env=True)
    assert cfg.ENV_PATH == (la / "report-skill" / ".env")
    assert cfg.settings.report_api_base_url == "http://la-host:3000/api"


# --------------------------------------------------------------------------- #
# client — headers kwarg + transport settings
# --------------------------------------------------------------------------- #
def test_request_accepts_headers_kwarg():
    """reports_list passes headers=… — the param must exist or every call
    raises TypeError (audit M3)."""
    from report_skill.client import ReportArchiveClient
    params = inspect.signature(ReportArchiveClient._request).parameters
    assert "headers" in params, "_request lost its headers kwarg — reports_list breaks"


def test_settings_has_transport_knobs():
    """Corporate-network knobs must exist with safe defaults."""
    # import from src copy explicitly to avoid a stale installed package
    cfg = importlib.import_module("report_skill.config")
    importlib.reload(cfg)
    s = cfg.Settings.model_fields
    assert "report_api_trust_env" in s
    assert s["report_api_trust_env"].default is False  # proxy off by default
    assert "report_api_ca_bundle" in s
    assert "report_api_verify_tls" in s
    assert s["report_api_verify_tls"].default is True


# --------------------------------------------------------------------------- #
# mcp_server — ConfigError / SystemExit do not hang the server
# --------------------------------------------------------------------------- #
def _invoke(name, args):
    import asyncio
    import json as _json
    from report_skill import mcp_server
    try:
        content = asyncio.run(mcp_server.call_tool(name, args))
    except Exception as exc:  # error path raises Exception(json_envelope)
        return _json.loads(str(exc))
    return _json.loads(content[0].text)


def _install_raiser(monkeypatch, exc):
    from report_skill import mcp_server

    class _Ctor:
        def __call__(self, *a, **k): return self
        def __enter__(self): raise exc
        def __exit__(self, *a): return None
    monkeypatch.setattr(mcp_server, "ReportArchiveClient", _Ctor())


def test_call_tool_configerror_is_not_configured(monkeypatch):
    # Use the exact ConfigError class mcp_server binds at its import (its
    # `except ConfigError` checks against THAT object). A config-module reload
    # elsewhere in the suite can mint a second ConfigError class; raising
    # mcp_server's own avoids that identity trap.
    from report_skill import mcp_server
    _install_raiser(monkeypatch, mcp_server.ConfigError("missing REPORT_API_EMAIL"))
    out = _invoke("workspaces_list", {})
    assert out.get("error") == "not_configured", out
    assert "env_path" in out and "fix" in out


def test_call_tool_systemexit_does_not_escape(monkeypatch):
    """A library calling exit() inside the worker must surface as a tool
    error, not tear down the event loop (audit B1 defensive)."""
    _install_raiser(monkeypatch, SystemExit(2))
    out = _invoke("workspaces_list", {})
    assert out.get("error") == "system_exit", out


def test_server_version_is_real_package_version():
    from report_skill import mcp_server
    assert mcp_server.SERVER_VERSION not in ("", "1.27.2", None)
    # looks like a semver (0.x.y) not the mcp SDK version
    assert mcp_server.SERVER_VERSION[0].isdigit()


def test_cli_main_entry_guards_configerror():
    """The PyInstaller standalone freezes cli.py's `if __name__=='__main__'`
    block as its entry — it MUST call main() (which maps ConfigError → exit 2),
    not app() directly. Calling app() let an unconfigured tools/composites
    command escape as a pydantic traceback + exit 1 on the .exe (re-verify R2).
    Source-level lock so a refactor can't silently reintroduce it."""
    import ast
    from report_skill import cli as cli_mod
    tree = ast.parse(Path(cli_mod.__file__).read_text(encoding="utf-8"))
    main_block = next(
        (n for n in tree.body
         if isinstance(n, ast.If)
         and isinstance(n.test, ast.Compare)
         and isinstance(n.test.left, ast.Name)
         and n.test.left.id == "__name__"),
        None,
    )
    assert main_block is not None, "cli.py has no `if __name__ == '__main__'` block"
    called = {
        node.func.id
        for stmt in main_block.body
        for node in ast.walk(stmt)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "main" in called, "cli.py __main__ must call main() (the ConfigError guard)"
    assert "app" not in called, (
        "cli.py __main__ calls app() directly — bypasses the ConfigError→exit-2 "
        "guard on the standalone .exe (re-verify R2)"
    )


def test_cli_main_catches_configerror():
    """main() must convert ConfigError into SystemExit(2) (not let it become a
    typer/pydantic traceback)."""
    import ast
    from report_skill import cli as cli_mod
    src = Path(cli_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    main_fn = next(
        (n for n in tree.body
         if isinstance(n, ast.FunctionDef) and n.name == "main"),
        None,
    )
    assert main_fn is not None, "cli.py has no main() function"
    handlers = [h for node in ast.walk(main_fn)
                if isinstance(node, ast.Try) for h in node.handlers]
    names = {
        (h.type.id if isinstance(h.type, ast.Name)
         else h.type.attr if isinstance(h.type, ast.Attribute) else None)
        for h in handlers
    }
    assert "ConfigError" in names, "main() does not catch ConfigError"


# --------------------------------------------------------------------------- #
# catalog — receiver guard on dev-only `catalog sync`
# --------------------------------------------------------------------------- #
def test_catalog_sync_guarded_when_backend_path_blank(monkeypatch):
    """On a receiver install REPORT_BACKEND_PATH is blank (→ Path('.')); the
    bridge must refuse with an actionable message, not a cwd-dependent
    'not a valid backend root' deep in a subprocess (audit M4)."""
    from report_skill import catalog
    monkeypatch.setattr(catalog.settings, "report_backend_path", Path(""),
                        raising=False)
    with pytest.raises(RuntimeError) as ei:
        catalog._backend_python()
    msg = str(ei.value)
    assert "dev-maintainer" in msg and "bundled" in msg, msg
