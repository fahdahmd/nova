"""LLM integration: Ollama client, prompts, context assembly, tool loop."""

from __future__ import annotations

from nova.llm.client import (
    LlmError,
    ModelMissing,
    OllamaClient,
    OllamaUnavailable,
    StreamChunk,
    ToolCall,
)
from nova.llm.context import build_messages, estimate_tokens, trim_history
from nova.llm.orchestrator import Orchestrator, TurnResult
from nova.llm.prompts import build_system_prompt

__all__ = [
    "LlmError",
    "ModelMissing",
    "OllamaClient",
    "OllamaUnavailable",
    "Orchestrator",
    "StreamChunk",
    "ToolCall",
    "TurnResult",
    "build_messages",
    "build_system_prompt",
    "estimate_tokens",
    "trim_history",
]
