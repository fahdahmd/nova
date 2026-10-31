"""Conversation context assembly and trimming.

A 4B model with an 8k window cannot afford an unbounded transcript, so history
is trimmed to a configurable token budget, newest first, and never split in the
middle of a tool exchange.
"""

from __future__ import annotations

from typing import Any

from nova.db.models import ChatMessage, MemoryFact
from nova.llm.prompts import build_system_prompt

# Rough but stable: English averages a little under four characters per token.
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Cheap token estimate - no tokeniser download, no model round-trip."""
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def estimate_messages(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        content = str(message.get("content") or "")
        total += estimate_tokens(content) + 4  # role and framing overhead
        for call in message.get("tool_calls") or []:
            total += estimate_tokens(str(call))
    return total


def trim_history(
    history: list[ChatMessage],
    *,
    budget_tokens: int,
    max_turns: int,
) -> list[dict[str, Any]]:
    """Return the most recent history that fits in ``budget_tokens``."""
    selected: list[dict[str, Any]] = []
    used = 0

    for message in reversed(history[-(max_turns * 2) :]):
        if message.role not in {"user", "assistant"}:
            continue
        cost = estimate_tokens(message.content) + 4
        if used + cost > budget_tokens:
            break
        selected.append({"role": message.role, "content": message.content})
        used += cost

    selected.reverse()
    return selected


def build_messages(
    *,
    app_name: str,
    user_text: str,
    history: list[ChatMessage],
    memories: list[MemoryFact],
    budget_tokens: int,
    max_turns: int,
) -> list[dict[str, Any]]:
    """Build the full message list for a turn: system, memory, history, user."""
    system = build_system_prompt(app_name, memories=memories)
    system_cost = estimate_tokens(system)
    user_cost = estimate_tokens(user_text)
    remaining = max(256, budget_tokens - system_cost - user_cost)

    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    messages.extend(trim_history(history, budget_tokens=remaining, max_turns=max_turns))
    messages.append({"role": "user", "content": user_text})
    return messages


def assistant_tool_message(text: str, calls: list[dict[str, Any]]) -> dict[str, Any]:
    """The assistant turn that requested one or more tools."""
    return {"role": "assistant", "content": text, "tool_calls": calls}


def tool_result_message(tool_name: str, content: str) -> dict[str, Any]:
    """A tool's reply, in the shape the Ollama library expects."""
    return {"role": "tool", "tool_name": tool_name, "content": content}
