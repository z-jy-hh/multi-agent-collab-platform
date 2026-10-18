"""LLM protocol layer (C4/C9).

- ``LLMClient`` protocol — everything above core talks to THIS, never to a
  vendor SDK.
- ``OpenAICompatClient`` — the only place the ``openai`` package is touched.
  Config priority: environment variable > .env > defaults (C9). A missing
  .env never raises at import or test time.
- ``FakeLLM`` — deterministic offline client; with no key configured the
  whole system degrades to this (rollback path, task book §五).
"""

from __future__ import annotations

import os
from typing import Callable, Protocol

from core.errors import ConfigError
from core.paths import PROJECT_ROOT

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"


def _load_env_file() -> None:
    """C9: .env is optional. Never an error when absent (R27: only the
    principal ever creates .env)."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import dotenv_values

        for k, v in dotenv_values(env_path).items():
            if v is not None and k not in os.environ:  # env wins over .env
                os.environ[k] = v
    except Exception:
        pass  # a malformed .env must not break import; env vars still work


class LLMClient(Protocol):
    def complete(self, prompt: str, *, system: str = "", json_mode: bool = False) -> str:
        ...


class OpenAICompatClient:
    """OpenAI-protocol client (works with DeepSeek / Zhipu / SiliconFlow;
    'OpenAI-compatible' refers to the protocol only — api.openai.com is
    unreachable on this machine per task book §一)."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        _load_env_file()
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL") or DEFAULT_BASE_URL
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or ""
        self.model = model or os.getenv("MODEL") or DEFAULT_MODEL
        if not self.api_key:
            raise ConfigError(
                "OPENAI_API_KEY not set — copy .env.example to .env and fill it "
                "(R27), or use FakeLLM for offline mode"
            )
        from openai import OpenAI

        self._client = OpenAI(base_url=self.base_url, api_key=self.api_key)

    def complete(self, prompt: str, *, system: str = "", json_mode: bool = False) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        kwargs: dict = {"model": self.model, "messages": messages, "temperature": 0.2}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self._client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""


class FakeLLM:
    """Deterministic offline client. Either a script list (popped in order,
    last item repeats) or a callable (prompt, json_mode) -> str."""

    def __init__(
        self,
        script: list[str] | Callable[[str, bool], str] | None = None,
    ) -> None:
        if script is None or callable(script):
            self._script = script
        else:
            self._script = list(script)
        self._calls = 0

    def complete(self, prompt: str, *, system: str = "", json_mode: bool = False) -> str:
        self._calls += 1
        if callable(self._script):
            return self._script(prompt, json_mode)
        if self._script:
            idx = min(self._calls - 1, len(self._script) - 1)
            text = self._script[idx]
        else:
            text = (
                '{"subtasks": [], "reply": "fake"}'
                if json_mode
                else "fake offline reply"
            )
        if json_mode:
            import json

            try:
                json.loads(text)
            except Exception:
                text = json.dumps({"reply": text}, ensure_ascii=False)
        return text

    @property
    def calls(self) -> int:
        return self._calls


def llm_from_env(script: list[str] | Callable[[str, bool], str] | None = None) -> LLMClient:
    """Task book §五 rollback: no key → FakeLLM offline mode."""
    _load_env_file()
    if os.getenv("OPENAI_API_KEY"):
        return OpenAICompatClient()
    return FakeLLM(script=script)
