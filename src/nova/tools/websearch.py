"""Web search through DuckDuckGo (ddgs).

Results are third-party text, so they are returned wrapped as untrusted data.
The tool is classed as sensitive: it leaves the machine, so the user confirms
each search, and it can be switched off with ``NOVA_WEB_SEARCH_ENABLED=false``.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, ClassVar

from ddgs import DDGS
from pydantic import BaseModel, Field

from nova.config import Settings
from nova.logging_setup import get_logger
from nova.tools.base import (
    RiskTier,
    Tool,
    ToolContext,
    ToolResult,
    compact_json,
    wrap_untrusted,
)

log = get_logger(__name__)

SNIPPET_CHARS = 280
_WHITESPACE = re.compile(r"\s+")


class WebSearchArgs(BaseModel):
    model_config = {"extra": "forbid"}

    query: str = Field(min_length=2, max_length=200, description="What to search for.")
    max_results: int = Field(default=5, ge=1, le=10, description="How many results to return.")


def _tidy(text: str, limit: int = SNIPPET_CHARS) -> str:
    cleaned = _WHITESPACE.sub(" ", (text or "").strip())
    return cleaned[:limit]


def _search(query: str, max_results: int) -> list[dict[str, Any]]:
    """Blocking search call, executed in a worker thread."""
    with DDGS() as client:
        raw = client.text(query, max_results=max_results)
    results = []
    for item in raw or []:
        results.append(
            {
                "title": _tidy(str(item.get("title", "")), 120),
                "url": _tidy(str(item.get("href", "")), 200),
                "snippet": _tidy(str(item.get("body", ""))),
            }
        )
    return results


class WebSearch(Tool):
    name: ClassVar[str] = "web_search"
    description: ClassVar[str] = (
        "Search the web for current information. Returns short snippets with links."
    )
    args_model: ClassVar[type[BaseModel]] = WebSearchArgs
    risk: ClassVar[RiskTier] = RiskTier.SENSITIVE

    def is_enabled(self, settings: Settings) -> bool:
        return settings.web_search_enabled

    def confirmation_prompt(self, args: BaseModel, context: ToolContext) -> str:
        del context
        return f"Search the web for {getattr(args, 'query', '')!r}? This leaves your machine."

    async def run(self, args: WebSearchArgs, context: ToolContext) -> ToolResult:
        settings = context.settings
        if not settings.web_search_enabled:
            return ToolResult.failure("web search is disabled in the settings")

        limit = min(args.max_results, settings.web_search_max_results)
        try:
            results = await asyncio.wait_for(
                asyncio.to_thread(_search, args.query, limit),
                timeout=settings.tool_timeout_s,
            )
        except TimeoutError:
            return ToolResult.failure("the search timed out")
        except Exception as exc:  # ddgs raises a variety of transport errors
            log.warning("web_search.failed", error=str(exc))
            return ToolResult.failure("the search could not be completed")

        if not results:
            return ToolResult(ok=True, content=f"No web results for {args.query!r}.")

        return ToolResult(
            ok=True,
            content=wrap_untrusted("web_search", compact_json(results, limit=3000)),
            data={"results": results, "query": args.query},
            untrusted=True,
        )
