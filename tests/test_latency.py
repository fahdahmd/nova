"""Regressions for the things that made replies slow.

Each test here pins a measured fix in place. The numbers in the comments come
from benchmarking a CPU-only run of qwen3.5:4b.
"""

from __future__ import annotations

import json

import pytest

from nova.config import CONTEXT_RESERVE_TOKENS, Settings
from nova.tools import build_registry
from nova.tools.base import strip_schema_noise


# -------------------------------------------------- keeping the model resident
def test_the_model_is_kept_resident_by_default() -> None:
    """A reload cost about 77 seconds on CPU, so this must not default to off."""
    assert Settings(allowed_roots=["."]).ollama_keep_alive == "30m"


def test_keep_alive_is_sent_on_every_chat_call() -> None:
    """Setting it only on warmup is what caused the long pause after idling."""
    import inspect

    from nova.llm import client as client_module

    source = inspect.getsource(client_module.OllamaClient.stream_chat)
    assert "keep_alive" in source


# ------------------------------------------------------ context window sizing
def test_the_context_window_leaves_room_for_the_prompt() -> None:
    """The schemas and system prompt alone are ~2,300 tokens."""
    settings = Settings(allowed_roots=["."])
    assert settings.context_token_budget <= settings.num_ctx - CONTEXT_RESERVE_TOKENS


def test_an_oversized_history_budget_is_refused() -> None:
    """Silently truncating the system prompt is worse than failing to start."""
    with pytest.raises(ValueError, match="leaves no room"):
        Settings(allowed_roots=["."], num_ctx=4096, context_token_budget=3500)


def test_a_large_budget_is_fine_with_a_large_window() -> None:
    settings = Settings(allowed_roots=["."], num_ctx=16384, context_token_budget=8000)
    assert settings.num_ctx == 16384


# ---------------------------------------------------------- schema slimming
def test_schemas_carry_no_decorative_titles() -> None:
    """Every schema is re-sent on every call, so each wasted key is paid often."""
    schemas = build_registry().schemas(Settings(allowed_roots=["."]))
    for schema in schemas:
        parameters = schema["function"]["parameters"]
        assert "title" not in parameters
        for prop in parameters.get("properties", {}).values():
            assert "title" not in prop


def test_slimming_never_removes_a_real_argument() -> None:
    """``notes_create`` has an argument literally named 'title'."""
    tool = build_registry().get("notes_create")
    assert tool is not None
    properties = tool.schema()["function"]["parameters"]["properties"]
    assert set(properties) == {"title", "body"}
    assert properties["title"]["description"]


def test_slimming_keeps_everything_the_model_needs() -> None:
    schema = {
        "title": "Decorative",
        "type": "object",
        "properties": {
            "title": {"title": "Title", "type": "string", "description": "keep me"},
            "nested": {
                "title": "Nested",
                "type": "object",
                "properties": {"title": {"title": "T", "type": "integer"}},
            },
        },
        "required": ["title"],
    }
    stripped = strip_schema_noise(schema)

    assert "title" not in stripped
    assert set(stripped["properties"]) == {"title", "nested"}
    assert stripped["properties"]["title"] == {"type": "string", "description": "keep me"}
    assert set(stripped["properties"]["nested"]["properties"]) == {"title"}
    assert stripped["required"] == ["title"]


def test_the_whole_tool_payload_stays_small() -> None:
    """A budget guard: 23 schemas should stay well under 12 KB of JSON."""
    schemas = build_registry().schemas(Settings(allowed_roots=["."]))
    assert len(json.dumps(schemas)) < 12_000


# ------------------------------------------------------------------ thinking
def test_thinking_is_off_by_default() -> None:
    assert Settings(allowed_roots=["."]).ollama_think is False
