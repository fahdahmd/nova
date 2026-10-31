"""Durable memory tools.

Facts are stored only when the user explicitly asks for them to be remembered.
Forgetting is destructive, so it needs confirmation like any other deletion.
"""

from __future__ import annotations

import asyncio
from typing import ClassVar

from pydantic import BaseModel, Field

from nova.tools.base import RiskTier, Tool, ToolContext, ToolResult, compact_json


class RememberArgs(BaseModel):
    model_config = {"extra": "forbid"}

    fact: str = Field(
        min_length=3,
        max_length=400,
        description="A single durable fact, written in the third person, e.g. 'Prefers tea'.",
    )


class ForgetArgs(BaseModel):
    model_config = {"extra": "forbid"}

    about: str = Field(
        min_length=2,
        max_length=200,
        description="Text matching the memory to delete, or 'everything' to clear all memory.",
    )


class Remember(Tool):
    name: ClassVar[str] = "memory_remember"
    description: ClassVar[str] = (
        "Store a durable fact about the user. Only use it when the user asks you to remember."
    )
    args_model: ClassVar[type[BaseModel]] = RememberArgs
    risk: ClassVar[RiskTier] = RiskTier.SAFE

    async def run(self, args: RememberArgs, context: ToolContext) -> ToolResult:
        fact = await asyncio.to_thread(context.db.remember, args.fact.strip(), "user")
        return ToolResult(
            ok=True,
            content=f"Remembered: {fact.content}",
            data={"memory": fact.model_dump(mode="json")},
            refresh=("memory",),
        )


class ListMemory(Tool):
    name: ClassVar[str] = "memory_list"
    description: ClassVar[str] = "List the durable facts currently remembered."
    risk: ClassVar[RiskTier] = RiskTier.SAFE

    async def run(self, args: BaseModel, context: ToolContext) -> ToolResult:
        del args
        facts = await asyncio.to_thread(context.db.list_memories, 100)
        if not facts:
            return ToolResult(ok=True, content="Nothing is stored in memory yet.")
        summary = [{"id": f.id, "fact": f.content} for f in facts]
        return ToolResult(
            ok=True,
            content=compact_json(summary),
            data={"memory": [f.model_dump(mode="json") for f in facts]},
        )


class Forget(Tool):
    name: ClassVar[str] = "memory_forget"
    description: ClassVar[str] = (
        "Delete remembered facts matching some text, or everything. Cannot be undone."
    )
    args_model: ClassVar[type[BaseModel]] = ForgetArgs
    risk: ClassVar[RiskTier] = RiskTier.DESTRUCTIVE

    def confirmation_prompt(self, args: BaseModel, context: ToolContext) -> str:
        del context
        about = str(getattr(args, "about", "")).strip().lower()
        if about in {"everything", "all", "*"}:
            return "Delete every remembered fact? This cannot be undone."
        return f"Delete remembered facts matching {about!r}?"

    async def run(self, args: ForgetArgs, context: ToolContext) -> ToolResult:
        target = args.about.strip()
        if target.lower() in {"everything", "all", "*"}:
            removed = await asyncio.to_thread(context.db.clear_memories)
            return ToolResult(
                ok=True,
                content=f"Cleared all memory ({removed} fact(s)).",
                refresh=("memory",),
            )

        removed = await asyncio.to_thread(context.db.forget_matching, target)
        if removed == 0:
            return ToolResult.failure(f"nothing in memory matches {target!r}")
        return ToolResult(
            ok=True,
            content=f"Forgot {removed} fact(s) matching {target!r}.",
            refresh=("memory",),
        )
