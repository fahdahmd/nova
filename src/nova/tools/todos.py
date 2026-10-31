"""Todo tools: add, list, complete, delete."""

from __future__ import annotations

import asyncio
from typing import ClassVar

from pydantic import BaseModel, Field

from nova.tools.base import RiskTier, Tool, ToolContext, ToolResult, compact_json


class AddTodoArgs(BaseModel):
    model_config = {"extra": "forbid"}

    text: str = Field(min_length=1, max_length=300, description="What needs doing.")


class ListTodosArgs(BaseModel):
    model_config = {"extra": "forbid"}

    include_done: bool = Field(default=False, description="Also include completed items.")


class TodoIdArgs(BaseModel):
    model_config = {"extra": "forbid"}

    todo_id: int = Field(ge=1, description="Numeric id of the todo.")


class AddTodo(Tool):
    name: ClassVar[str] = "todos_add"
    description: ClassVar[str] = "Add a task to the todo list."
    args_model: ClassVar[type[BaseModel]] = AddTodoArgs

    async def run(self, args: AddTodoArgs, context: ToolContext) -> ToolResult:
        todo = await asyncio.to_thread(context.db.add_todo, args.text)
        return ToolResult(
            ok=True,
            content=f"Added todo #{todo.id}: {todo.text}",
            data={"todo": todo.model_dump(mode="json")},
            refresh=("todos",),
        )


class ListTodos(Tool):
    name: ClassVar[str] = "todos_list"
    description: ClassVar[str] = "List todo items, open ones by default."
    args_model: ClassVar[type[BaseModel]] = ListTodosArgs

    async def run(self, args: ListTodosArgs, context: ToolContext) -> ToolResult:
        todos = await asyncio.to_thread(
            context.db.list_todos, include_done=args.include_done, limit=50
        )
        if not todos:
            return ToolResult(ok=True, content="The todo list is empty.")
        summary = [{"id": t.id, "text": t.text, "done": t.done} for t in todos]
        return ToolResult(
            ok=True,
            content=compact_json(summary),
            data={"todos": [t.model_dump(mode="json") for t in todos]},
        )


class CompleteTodo(Tool):
    name: ClassVar[str] = "todos_complete"
    description: ClassVar[str] = "Mark a todo as done by its numeric id."
    args_model: ClassVar[type[BaseModel]] = TodoIdArgs

    async def run(self, args: TodoIdArgs, context: ToolContext) -> ToolResult:
        done = await asyncio.to_thread(context.db.complete_todo, args.todo_id)
        if not done:
            return ToolResult.failure(f"todo {args.todo_id} is missing or already complete")
        return ToolResult(
            ok=True,
            content=f"Completed todo #{args.todo_id}.",
            refresh=("todos",),
        )


class DeleteTodo(Tool):
    name: ClassVar[str] = "todos_delete"
    description: ClassVar[str] = "Delete a todo by its numeric id. Deletion cannot be undone."
    args_model: ClassVar[type[BaseModel]] = TodoIdArgs
    risk: ClassVar[RiskTier] = RiskTier.DESTRUCTIVE

    def confirmation_prompt(self, args: BaseModel, context: ToolContext) -> str:
        todo_id = getattr(args, "todo_id", "?")
        return f"Permanently delete todo #{todo_id}?"

    async def run(self, args: TodoIdArgs, context: ToolContext) -> ToolResult:
        deleted = await asyncio.to_thread(context.db.delete_todo, args.todo_id)
        if not deleted:
            return ToolResult.failure(f"no todo with id {args.todo_id}")
        return ToolResult(
            ok=True,
            content=f"Deleted todo #{args.todo_id}.",
            refresh=("todos",),
        )
