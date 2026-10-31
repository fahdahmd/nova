"""Ollama client wrapper.

Adds the things the app needs on top of the official library: a readiness check
with friendly diagnostics, streaming that yields text and tool calls as they
arrive, and ``think=False`` by default for latency.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import ollama

from nova.config import Settings
from nova.logging_setup import get_logger

log = get_logger(__name__)


class LlmError(Exception):
    """Base class for LLM failures that the UI should explain, not hide."""

    code = "llm_error"
    hint = ""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        if hint:
            self.hint = hint


class OllamaUnavailable(LlmError):  # noqa: N818 - reads better than ...Error here
    code = "ollama_unavailable"


class ModelMissing(LlmError):  # noqa: N818 - reads better than ...Error here
    code = "model_missing"


@dataclass(slots=True)
class ToolCall:
    """A tool invocation requested by the model."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StreamChunk:
    """One step of a streamed response."""

    text: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    done: bool = False


class OllamaClient:
    """Thin async wrapper around :class:`ollama.AsyncClient`."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = ollama.AsyncClient(
            host=settings.ollama_host,
            timeout=settings.ollama_timeout_s,
        )
        self._verified = False

    # ------------------------------------------------------------- readiness
    async def ensure_ready(self) -> None:
        """Confirm the server is up and the model is pulled.

        Raises :class:`OllamaUnavailable` or :class:`ModelMissing` with a
        message that tells the user exactly what to run.
        """
        if self._verified:
            return

        model = self._settings.ollama_model
        try:
            listing = await self._client.list()
        except (ollama.ResponseError, ollama.RequestError, ConnectionError, OSError) as exc:
            raise OllamaUnavailable(
                f"Ollama is not reachable at {self._settings.ollama_host}.",
                hint="Start it with `ollama serve`, then reload this page.",
            ) from exc

        available = {
            str(entry.model)
            for entry in getattr(listing, "models", [])
            if getattr(entry, "model", None)
        }
        # Ollama reports "name:tag"; accept a bare name that matches one tag.
        if model not in available and not any(
            name.split(":")[0] == model.split(":")[0] for name in available
        ):
            raise ModelMissing(
                f"The model {model!r} is not installed.",
                hint=f"Run `ollama pull {model}` and reload this page.",
            )

        self._verified = True
        log.info("ollama.ready", model=model, host=self._settings.ollama_host)

    # --------------------------------------------------------------- options
    def _options(self) -> dict[str, Any]:
        return {
            "temperature": self._settings.temperature,
            "top_p": self._settings.top_p,
            "num_ctx": self._settings.num_ctx,
        }

    # -------------------------------------------------------------- chatting
    async def stream_chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        think: bool | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a chat completion, yielding text and tool calls."""
        await self.ensure_ready()
        thinking_enabled = self._settings.ollama_think if think is None else think

        kwargs: dict[str, Any] = {
            "model": self._settings.ollama_model,
            "messages": list(messages),
            "stream": True,
            "options": self._options(),
            # Without this, Ollama unloads the model after a few idle minutes
            # and the next message pays the reload plus a full prompt
            # re-evaluation - measured at about 75 seconds on CPU.
            "keep_alive": self._settings.ollama_keep_alive,
        }
        if tools:
            kwargs["tools"] = list(tools)
        # Some models reject the flag outright; only send it when meaningful.
        if thinking_enabled:
            kwargs["think"] = True

        try:
            stream = await self._client.chat(**kwargs)
            async for part in stream:
                message = getattr(part, "message", None)
                if message is None:
                    continue

                calls: list[ToolCall] = []
                for raw_call in getattr(message, "tool_calls", None) or []:
                    function = getattr(raw_call, "function", None)
                    if function is None:
                        continue
                    arguments = getattr(function, "arguments", None) or {}
                    calls.append(
                        ToolCall(
                            name=str(getattr(function, "name", "")),
                            arguments=dict(arguments) if isinstance(arguments, dict) else {},
                        )
                    )

                yield StreamChunk(
                    text=str(getattr(message, "content", "") or ""),
                    thinking=str(getattr(message, "thinking", "") or ""),
                    tool_calls=calls,
                    done=bool(getattr(part, "done", False)),
                )
        except ollama.ResponseError as exc:
            message = str(getattr(exc, "error", exc))
            if "not found" in message.lower():
                raise ModelMissing(
                    f"The model {self._settings.ollama_model!r} is not installed.",
                    hint=f"Run `ollama pull {self._settings.ollama_model}`.",
                ) from exc
            raise LlmError(f"Ollama rejected the request: {message}") from exc
        except (ollama.RequestError, ConnectionError, OSError) as exc:
            self._verified = False
            raise OllamaUnavailable(
                "Lost the connection to Ollama.",
                hint="Check that `ollama serve` is still running.",
            ) from exc

    async def warmup(self) -> None:
        """Load the model into memory so the first real reply is fast."""
        try:
            await self.ensure_ready()
            await self._client.chat(
                model=self._settings.ollama_model,
                messages=[{"role": "user", "content": "hi"}],
                options={"num_predict": 1, "num_ctx": 512},
                keep_alive=self._settings.ollama_keep_alive,
            )
            log.info("ollama.warm")
        except (LlmError, ollama.ResponseError, ollama.RequestError, OSError) as exc:
            log.info("ollama.warmup_skipped", error=str(exc))
