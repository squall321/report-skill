"""Provider-agnostic LLM client.

Auto-detects which provider to use based on env vars (priority order):
    1. ANTHROPIC_API_KEY  -> AnthropicProvider  (claude-haiku-4-5-20251001)
    2. OPENAI_API_KEY     -> OpenAIProvider     (gpt-4o-mini)
    3. OLLAMA_BASE_URL    -> OllamaProvider     (llama3.1:8b)

If none are set, defaults to OllamaProvider at http://localhost:11434 to
fit the "local 8B" target user (the W tier).

`SKILL_LLM_MODEL` env var overrides the model on whichever provider is
active. All providers MUST work via httpx alone; the official SDK is
only used as an optional optimisation when importable.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Optional, Protocol

import httpx

# Optional SDK imports — we tolerate them being missing.
try:  # pragma: no cover - import-time only
    import anthropic as _anthropic_sdk  # type: ignore
except ImportError:  # pragma: no cover
    _anthropic_sdk = None  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class LLMError(Exception):
    """Any failure talking to the LLM (network, auth, parse, empty body)."""


# --------------------------------------------------------------------------- #
# Provider protocol
# --------------------------------------------------------------------------- #
class LLMProvider(Protocol):
    name: str
    model: str

    def generate(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.1,
        json_mode: bool = False,
    ) -> str:
        ...


# --------------------------------------------------------------------------- #
# Anthropic
# --------------------------------------------------------------------------- #
_DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: Optional[str] = None,
                 base_url: str = "https://api.anthropic.com",
                 timeout: float = 60.0):
        self.api_key = api_key
        self.model = model or os.getenv("SKILL_LLM_MODEL") or _DEFAULT_ANTHROPIC_MODEL
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @staticmethod
    def _split_system(messages: list[dict]) -> tuple[Optional[str], list[dict]]:
        sys_parts: list[str] = []
        rest: list[dict] = []
        for m in messages:
            if m.get("role") == "system":
                sys_parts.append(str(m.get("content", "")))
            else:
                rest.append({"role": m["role"], "content": m["content"]})
        return ("\n\n".join(sys_parts) if sys_parts else None, rest)

    def generate(self, messages, *, max_tokens=1024, temperature=0.1,
                 json_mode=False) -> str:
        system, rest = self._split_system(messages)
        # SDK shortcut when available
        if _anthropic_sdk is not None:
            try:
                client = _anthropic_sdk.Anthropic(api_key=self.api_key,
                                                  base_url=self.base_url,
                                                  timeout=self.timeout)
                kwargs: dict[str, Any] = dict(
                    model=self.model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    messages=rest,
                )
                if system:
                    kwargs["system"] = system
                resp = client.messages.create(**kwargs)
                # response.content is list of TextBlock | etc.
                parts: list[str] = []
                for blk in resp.content:
                    text = getattr(blk, "text", None)
                    if text:
                        parts.append(text)
                out = "".join(parts).strip()
                if not out:
                    raise LLMError("anthropic: empty response")
                return out
            except LLMError:
                raise
            except Exception as e:  # SDK error -> fall through to httpx? no, just report
                raise LLMError(f"anthropic SDK call failed: {e}") from e

        # httpx fallback
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": rest,
        }
        if system:
            body["system"] = system
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                r = client.post(f"{self.base_url}/v1/messages",
                                json=body, headers=headers)
        except httpx.HTTPError as e:
            raise LLMError(f"anthropic HTTP error: {e}") from e
        if r.status_code >= 400:
            raise LLMError(f"anthropic {r.status_code}: {r.text[:400]}")
        try:
            data = r.json()
        except ValueError as e:
            raise LLMError(f"anthropic: non-JSON response: {r.text[:200]}") from e
        parts = []
        for blk in data.get("content", []):
            if blk.get("type") == "text":
                parts.append(blk.get("text", ""))
        out = "".join(parts).strip()
        if not out:
            raise LLMError(f"anthropic: empty text content (full: {data!r:.200})")
        return out


# --------------------------------------------------------------------------- #
# OpenAI
# --------------------------------------------------------------------------- #
_DEFAULT_OPENAI_MODEL = "gpt-4o-mini"


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, model: Optional[str] = None,
                 base_url: str = "https://api.openai.com/v1",
                 timeout: float = 60.0):
        self.api_key = api_key
        self.model = model or os.getenv("SKILL_LLM_MODEL") or _DEFAULT_OPENAI_MODEL
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def generate(self, messages, *, max_tokens=1024, temperature=0.1,
                 json_mode=False) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m["role"], "content": m["content"]}
                         for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                r = client.post(f"{self.base_url}/chat/completions",
                                json=body, headers=headers)
        except httpx.HTTPError as e:
            raise LLMError(f"openai HTTP error: {e}") from e
        if r.status_code >= 400:
            raise LLMError(f"openai {r.status_code}: {r.text[:400]}")
        try:
            data = r.json()
        except ValueError as e:
            raise LLMError(f"openai: non-JSON response: {r.text[:200]}") from e
        try:
            out = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"openai: unexpected response shape: {data!r:.200}") from e
        out = out.strip()
        if not out:
            raise LLMError("openai: empty response")
        return out


# --------------------------------------------------------------------------- #
# Ollama
# --------------------------------------------------------------------------- #
_DEFAULT_OLLAMA_MODEL = "llama3.1:8b"
_DEFAULT_OLLAMA_BASE = "http://localhost:11434"


class OllamaProvider:
    name = "ollama"

    def __init__(self, base_url: Optional[str] = None,
                 model: Optional[str] = None, timeout: float = 120.0):
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL")
                         or _DEFAULT_OLLAMA_BASE).rstrip("/")
        self.model = (model or os.getenv("SKILL_LLM_MODEL")
                      or os.getenv("OLLAMA_MODEL") or _DEFAULT_OLLAMA_MODEL)
        self.timeout = timeout

    def generate(self, messages, *, max_tokens=1024, temperature=0.1,
                 json_mode=False) -> str:
        # Local 8B models lose track of long format instructions, so we
        # nudge the last user message when JSON is required.
        msgs = [{"role": m["role"], "content": m["content"]} for m in messages]
        if json_mode and msgs:
            for i in range(len(msgs) - 1, -1, -1):
                if msgs[i]["role"] == "user":
                    msgs[i]["content"] += (
                        "\n\nReturn ONLY valid JSON, no prose."
                    )
                    break
        body: dict[str, Any] = {
            "model": self.model,
            "messages": msgs,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if json_mode:
            # Ollama accepts "json" as a shortcut for forcing JSON output.
            body["format"] = "json"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                r = client.post(f"{self.base_url}/api/chat", json=body)
        except httpx.HTTPError as e:
            raise LLMError(f"ollama HTTP error: {e}") from e
        if r.status_code >= 400:
            raise LLMError(f"ollama {r.status_code}: {r.text[:400]}")
        try:
            data = r.json()
        except ValueError as e:
            raise LLMError(f"ollama: non-JSON response: {r.text[:200]}") from e
        msg = data.get("message") or {}
        out = (msg.get("content") or "").strip()
        if not out:
            raise LLMError(f"ollama: empty response: {data!r:.200}")
        return out


# --------------------------------------------------------------------------- #
# Bridge provider — human-in-loop (Claude Code chat as the LLM)
# --------------------------------------------------------------------------- #
class BridgeProvider:
    """File-based human-in-loop provider.

    Use case: developer is in a Claude Code session and wants to use that
    Claude as the LLM backend WITHOUT any API key. The provider drops a
    request JSON into `.skill-cache/bridge/`, prints a clear instruction to
    stderr, then polls for a corresponding response file. The user asks
    Claude in chat to "process the bridge queue" — Claude reads the request
    file and writes the response file. The skill picks it up and continues.

    Triggered by `SKILL_LLM_PROVIDER=bridge` env var. Never auto-selected.
    """

    name = "claude-bridge"

    def __init__(self, model: str = "claude-in-chat",
                 timeout: float = 600.0, poll_interval: float = 1.5):
        from report_skill.config import CACHE_DIR
        self.model = model
        self.timeout = float(timeout)
        self.poll_interval = float(poll_interval)
        self.bridge_dir = CACHE_DIR / "bridge"
        self.bridge_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, messages: list[dict], *,
                 max_tokens: int = 1024,
                 temperature: float = 0.1,
                 json_mode: bool = False) -> str:
        import uuid
        import sys as _sys
        from datetime import datetime as _dt, timezone as _tz

        req_id = uuid.uuid4().hex[:12]
        req_path = self.bridge_dir / f"{req_id}.req.json"
        resp_path = self.bridge_dir / f"{req_id}.resp.txt"

        req_payload = {
            "id": req_id,
            "created_at": _dt.now(_tz.utc).isoformat(),
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "json_mode": json_mode,
            "response_path": str(resp_path),
        }
        req_path.write_text(
            json.dumps(req_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        _sys.stderr.write(
            f"\n[bridge] request {req_id} → {req_path}\n"
            f"[bridge] in Claude Code chat say: \"process bridge queue\"\n"
            f"[bridge] (or manually write the response text to {resp_path})\n"
            f"[bridge] polling up to {int(self.timeout)}s...\n"
        )
        _sys.stderr.flush()

        deadline = time.monotonic() + self.timeout
        last_log = 0.0
        while time.monotonic() < deadline:
            if resp_path.exists():
                content = resp_path.read_text(encoding="utf-8")
                try:
                    req_path.unlink()
                except OSError:
                    pass
                try:
                    resp_path.unlink()
                except OSError:
                    pass
                _sys.stderr.write(f"[bridge] response received ({len(content)} chars)\n")
                return content
            # Periodic still-waiting heartbeat (every 30s)
            now = time.monotonic()
            if now - last_log > 30.0:
                remaining = int(deadline - now)
                _sys.stderr.write(f"[bridge] still waiting ({remaining}s left)\n")
                _sys.stderr.flush()
                last_log = now
            time.sleep(self.poll_interval)

        try:
            req_path.unlink()
        except OSError:
            pass
        raise LLMError(
            f"bridge timeout ({self.timeout}s) — no response file at {resp_path}. "
            "Cancel the CLI and ask Claude in chat to process the bridge queue."
        )


# --------------------------------------------------------------------------- #
# Auto-detection
# --------------------------------------------------------------------------- #
_PROVIDER_SINGLETON: Optional[LLMProvider] = None


def _detect_provider() -> LLMProvider:
    """Pick a provider based on env vars.

    Order:
      1. explicit `SKILL_LLM_PROVIDER` env (bridge | anthropic | openai | ollama)
      2. `ANTHROPIC_API_KEY` → AnthropicProvider
      3. `OPENAI_API_KEY` → OpenAIProvider
      4. fallback → OllamaProvider (http://localhost:11434)
    """
    explicit = os.getenv("SKILL_LLM_PROVIDER", "").strip().lower()
    model_override = os.getenv("SKILL_LLM_MODEL") or None
    if explicit == "bridge":
        return BridgeProvider(
            model=model_override or "claude-in-chat",
            timeout=float(os.getenv("SKILL_BRIDGE_TIMEOUT", "600")),
        )
    if explicit == "anthropic":
        return AnthropicProvider(api_key=os.getenv("ANTHROPIC_API_KEY", ""),
                                 model=model_override)
    if explicit == "openai":
        return OpenAIProvider(api_key=os.getenv("OPENAI_API_KEY", ""),
                              model=model_override)
    if explicit == "ollama":
        return OllamaProvider(model=model_override)
    anth = os.getenv("ANTHROPIC_API_KEY")
    if anth:
        return AnthropicProvider(api_key=anth, model=model_override)
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        return OpenAIProvider(api_key=openai_key, model=model_override)
    return OllamaProvider(model=model_override)


def get_provider() -> LLMProvider:
    """Return the (cached) auto-detected provider."""
    global _PROVIDER_SINGLETON
    if _PROVIDER_SINGLETON is None:
        _PROVIDER_SINGLETON = _detect_provider()
    return _PROVIDER_SINGLETON


def reset_provider() -> None:
    """Clear the cached singleton (useful in tests / after env changes)."""
    global _PROVIDER_SINGLETON
    _PROVIDER_SINGLETON = None


def is_configured() -> bool:
    """True when any provider env var is set.

    Ollama default URL alone does NOT count — `is_configured()` is meant
    to signal that the user has *deliberately* picked a provider. Callers
    can still get a provider via `get_provider()` regardless.

    The bridge provider counts as configured when SKILL_LLM_PROVIDER=bridge
    is set explicitly (it never auto-selects).
    """
    explicit = os.getenv("SKILL_LLM_PROVIDER", "").strip().lower()
    if explicit in ("bridge", "anthropic", "openai", "ollama"):
        return True
    return bool(
        os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("OLLAMA_BASE_URL")
    )


# --------------------------------------------------------------------------- #
# Small helpers used by callers
# --------------------------------------------------------------------------- #
def extract_json(text: str) -> Any:
    """Best-effort JSON extraction from a model's reply.

    Handles three common wrappings: pure JSON, ```json fenced``` blocks,
    and JSON embedded after some leading prose. Raises LLMError when no
    JSON object/array can be found.
    """
    t = text.strip()
    if not t:
        raise LLMError("empty text — nothing to parse as JSON")

    # Strip ```json ... ``` or ``` ... ``` fences if present.
    if t.startswith("```"):
        lines = t.splitlines()
        # drop first fence (```json or ```)
        lines = lines[1:]
        # drop trailing fence
        while lines and lines[-1].strip().startswith("```"):
            lines.pop()
        t = "\n".join(lines).strip()

    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass

    # Fallback: scan for the first balanced { ... } or [ ... ] in the text.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = t.find(opener)
        if start == -1:
            continue
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(t)):
            ch = t[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    candidate = t[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break
    raise LLMError(f"could not parse JSON from response: {text[:200]!r}")
