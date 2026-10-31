"""The conversation engine.

One turn runs as a bounded loop: stream from the model, and if it asks for a
tool, validate the arguments, get the user's confirmation when the tool is not
safe, run it under a timeout, record it in the audit log, and feed the result
back. The loop is capped, every failure has a defined fallback, and the model
can never approve its own sensitive actions.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from nova.config import Settings
from nova.core.events import (
    ConfirmRequest,
    ErrorOut,
    Refresh,
    ServerEvent,
    Status,
    StreamReset,
    Token,
    ToolEnd,
    ToolStart,
)
from nova.db.database import Database
from nova.db.models import AuditStatus
from nova.db.models import RiskTier as DbRiskTier
from nova.llm.client import LlmError, OllamaClient, ToolCall
from nova.llm.context import (
    assistant_tool_message,
    build_messages,
    tool_result_message,
)
from nova.llm.prompts import (
    DENIED_NOTICE,
    FALLBACK_NOTICE,
    RETRY_HINT,
    TOOL_BUDGET_NOTICE,
)
from nova.logging_setup import get_logger
from nova.security.sandbox import PathSandbox
from nova.tools.base import (
    RiskTier,
    Tool,
    ToolContext,
    ToolRegistry,
    ToolResult,
    format_validation_error,
)

log = get_logger(__name__)

Emit = Callable[[ServerEvent], Awaitable[None]]
Confirm = Callable[[ConfirmRequest], Awaitable[bool]]

MAX_CALLS_PER_ITERATION = 3
MAX_VALIDATION_RETRIES = 1


@dataclass(slots=True)
class TurnResult:
    """What a completed turn produced."""

    text: str = ""
    cancelled: bool = False
    tool_calls: list[str] = field(default_factory=list)
    error: str | None = None


class Orchestrator:
    """Runs one user turn at a time against the model and the tools."""

    def __init__(
        self,
        settings: Settings,
        db: Database,
        registry: ToolRegistry,
        llm: OllamaClient,
        sandbox: PathSandbox,
        scheduler: Any = None,
    ) -> None:
        self._settings = settings
        self._db = db
        self._registry = registry
        self._llm = llm
        self._context = ToolContext(settings=settings, db=db, sandbox=sandbox, scheduler=scheduler)

    # ------------------------------------------------------------------ turn
    async def run_turn(
        self,
        user_text: str,
        *,
        emit: Emit,
        confirm: Confirm,
        cancelled: asyncio.Event,
        think: bool | None = None,
    ) -> TurnResult:
        """Handle one user message end to end."""
        settings = self._settings
        user_text = user_text.strip()
        if not user_text:
            return TurnResult(text="")

        await asyncio.to_thread(self._db.add_message, "user", user_text)
        history = await asyncio.to_thread(self._db.recent_messages, settings.history_turns * 2)
        memories = await asyncio.to_thread(self._db.list_memories, 25)

        messages = build_messages(
            app_name=settings.app_name,
            user_text=user_text,
            # The row just inserted is already in `history`; drop the duplicate.
            history=history[:-1],
            memories=memories,
            budget_tokens=settings.context_token_budget,
            max_turns=settings.history_turns,
        )

        schemas = self._registry.schemas(settings)
        used_tools: list[str] = []
        validation_retries = 0
        tainted = False

        try:
            # Tool phase: bounded, so a confused model cannot loop forever.
            for _ in range(settings.max_tool_iterations):
                await emit(Status(state="thinking"))
                text, calls = await self._stream(
                    messages,
                    tools=schemas,
                    emit=emit,
                    cancelled=cancelled,
                    think=think,
                )

                if cancelled.is_set():
                    return TurnResult(text=text, cancelled=True, tool_calls=used_tools)

                if not calls:
                    return await self._finalise(text, used_tools)

                # The model asked for tools, so any streamed preamble is not the
                # answer: tell the UI to drop it.
                if text.strip():
                    await emit(StreamReset())

                messages.append(
                    assistant_tool_message(
                        text,
                        [
                            {"function": {"name": call.name, "arguments": call.arguments}}
                            for call in calls[:MAX_CALLS_PER_ITERATION]
                        ],
                    )
                )

                invalid = False
                for call in calls[:MAX_CALLS_PER_ITERATION]:
                    outcome = await self._handle_call(
                        call,
                        emit=emit,
                        confirm=confirm,
                        tainted=tainted,
                        cancelled=cancelled,
                    )
                    messages.append(tool_result_message(call.name, outcome.content))
                    if outcome.invalid_arguments:
                        invalid = True
                    if outcome.ran:
                        used_tools.append(call.name)
                    tainted = tainted or outcome.untrusted

                if invalid:
                    validation_retries += 1
                    if validation_retries > MAX_VALIDATION_RETRIES:
                        # Retried once already: drop the tools and just answer.
                        log.info("turn.tool_fallback", tools=used_tools)
                        return await self._answer_without_tools(
                            messages,
                            FALLBACK_NOTICE,
                            used_tools,
                            emit=emit,
                            cancelled=cancelled,
                            think=think,
                            detail="answering directly",
                        )

            # The tool budget is spent. Force one plain answer so the user is
            # never left with silence.
            log.info("turn.budget_exhausted", tools=used_tools)
            return await self._answer_without_tools(
                messages,
                TOOL_BUDGET_NOTICE,
                used_tools,
                emit=emit,
                cancelled=cancelled,
                think=think,
            )

        except LlmError as exc:
            log.warning("turn.llm_error", code=exc.code, error=str(exc))
            await emit(ErrorOut(message=exc.message, code=exc.code, hint=exc.hint))
            return TurnResult(error=exc.message, tool_calls=used_tools)

    # ---------------------------------------------------------- answer paths
    async def _finalise(self, text: str, used_tools: list[str]) -> TurnResult:
        """Persist and return the assistant's answer for this turn."""
        answer = text.strip()
        if answer:
            await asyncio.to_thread(self._db.add_message, "assistant", answer)
        return TurnResult(text=answer, tool_calls=used_tools)

    async def _answer_without_tools(
        self,
        messages: list[dict[str, Any]],
        notice: str,
        used_tools: list[str],
        *,
        emit: Emit,
        cancelled: asyncio.Event,
        think: bool | None,
        detail: str = "",
    ) -> TurnResult:
        """Ask for a plain answer with no tools offered at all."""
        messages.append({"role": "system", "content": notice})
        await emit(Status(state="thinking", detail=detail))
        text, _ = await self._stream(
            messages, tools=None, emit=emit, cancelled=cancelled, think=think
        )
        return await self._finalise(text, used_tools)

    # --------------------------------------------------------------- streaming
    async def _stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        emit: Emit,
        cancelled: asyncio.Event,
        think: bool | None,
    ) -> tuple[str, list[ToolCall]]:
        """Consume one streamed completion, emitting tokens as they arrive."""
        pieces: list[str] = []
        calls: list[ToolCall] = []

        async for chunk in self._llm.stream_chat(messages, tools=tools, think=think):
            if cancelled.is_set():
                break
            if chunk.tool_calls:
                calls.extend(chunk.tool_calls)
            if chunk.text:
                pieces.append(chunk.text)
                await emit(Token(text=chunk.text))
            if chunk.done:
                break

        return "".join(pieces), calls

    # ----------------------------------------------------------- tool calling
    @dataclass(slots=True)
    class _CallOutcome:
        content: str
        ran: bool = False
        invalid_arguments: bool = False
        untrusted: bool = False

    async def _handle_call(
        self,
        call: ToolCall,
        *,
        emit: Emit,
        confirm: Confirm,
        tainted: bool,
        cancelled: asyncio.Event,
    ) -> Orchestrator._CallOutcome:
        """Validate, confirm, run and audit a single tool call."""
        tool = self._registry.get(call.name)
        if tool is None or not tool.is_enabled(self._settings):
            available = ", ".join(self._registry.names(self._settings))
            await self._audit(call.name, call.arguments, RiskTier.SAFE, "error", "unknown tool")
            return self._CallOutcome(
                content=f"Error: no tool named {call.name!r}. Available tools: {available}",
                invalid_arguments=True,
            )

        try:
            args = tool.parse_args(call.arguments)
        except ValidationError as exc:
            detail = format_validation_error(exc)
            log.info("tool.invalid_args", tool=call.name, detail=detail)
            await self._audit(call.name, call.arguments, tool.risk, "error", detail)
            await emit(ToolEnd(tool=call.name, ok=False, detail="invalid arguments"))
            return self._CallOutcome(
                content=f"Error: {RETRY_HINT.format(error=detail)}",
                invalid_arguments=True,
            )

        risk = tool.risk_for(args, self._context)

        if risk is not RiskTier.SAFE:
            approved = await self._request_confirmation(
                tool, args, risk, emit=emit, confirm=confirm, tainted=tainted
            )
            if not approved:
                await self._audit(call.name, args.model_dump(), risk, "denied", "user declined")
                await emit(ToolEnd(tool=call.name, ok=False, detail="declined"))
                return self._CallOutcome(content=f"The action was not approved. {DENIED_NOTICE}")

        if cancelled.is_set():
            return self._CallOutcome(content="The user stopped this turn.")

        await emit(
            ToolStart(
                tool=tool.name,
                risk=str(risk),
                summary=tool.confirmation_prompt(args, self._context),
            )
        )

        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                tool.run(args, self._context), timeout=self._settings.tool_timeout_s
            )
        except TimeoutError:
            elapsed = int((time.perf_counter() - started) * 1000)
            await self._audit(tool.name, args.model_dump(), risk, "timeout", "", elapsed)
            await emit(ToolEnd(tool=tool.name, ok=False, detail="timed out", duration_ms=elapsed))
            return self._CallOutcome(content=f"Error: {tool.name} timed out.")
        except Exception as exc:  # a tool must never take the server down
            elapsed = int((time.perf_counter() - started) * 1000)
            log.exception("tool.crashed", tool=tool.name)
            await self._audit(tool.name, args.model_dump(), risk, "error", str(exc), elapsed)
            await emit(ToolEnd(tool=tool.name, ok=False, detail="failed", duration_ms=elapsed))
            return self._CallOutcome(content=f"Error: {tool.name} failed unexpectedly.")

        elapsed = int((time.perf_counter() - started) * 1000)
        await self._finish_call(tool, args, risk, result, elapsed, emit=emit)
        return self._CallOutcome(content=result.content, ran=result.ok, untrusted=result.untrusted)

    async def _finish_call(
        self,
        tool: Tool,
        args: BaseModel,
        risk: RiskTier,
        result: ToolResult,
        elapsed_ms: int,
        *,
        emit: Emit,
    ) -> None:
        await self._audit(
            tool.name,
            args.model_dump(),
            risk,
            "ok" if result.ok else "error",
            "" if result.ok else result.content[:200],
            elapsed_ms,
        )
        await emit(
            ToolEnd(
                tool=tool.name,
                ok=result.ok,
                detail=result.content[:160] if not result.ok else "",
                duration_ms=elapsed_ms,
            )
        )
        if result.refresh:
            await emit(Refresh(collections=list(result.refresh)))

    async def _request_confirmation(
        self,
        tool: Tool,
        args: BaseModel,
        risk: RiskTier,
        *,
        emit: Emit,
        confirm: Confirm,
        tainted: bool,
    ) -> bool:
        """Ask the user. The model has no way to answer this for itself."""
        request = ConfirmRequest(
            request_id=secrets.token_urlsafe(16),
            tool=tool.name,
            risk=str(risk),
            prompt=tool.confirmation_prompt(args, self._context),
            arguments=args.model_dump(mode="json"),
            from_untrusted_content=tainted,
        )
        await emit(Status(state="idle", detail="waiting for your confirmation"))
        approved = await confirm(request)
        log.info("tool.confirmation", tool=tool.name, risk=str(risk), approved=approved)
        return approved

    async def _audit(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        risk: RiskTier | str,
        status: AuditStatus,
        detail: str = "",
        duration_ms: int = 0,
    ) -> None:
        tier: DbRiskTier = str(risk)  # type: ignore[assignment]
        await asyncio.to_thread(
            self._db.log_tool_call,
            tool_name,
            arguments,
            risk=tier,
            status=status,
            detail=detail,
            duration_ms=duration_ms,
        )
