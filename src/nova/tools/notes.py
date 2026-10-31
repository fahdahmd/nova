"""Note tools: create, list, search, delete."""

from __future__ import annotations

import asyncio
from typing import ClassVar

from pydantic import BaseModel, Field

from nova.tools.base import RiskTier, Tool, ToolContext, ToolResult, compact_json


class CreateNoteArgs(BaseModel):
    model_config = {"extra": "forbid"}

    title: str = Field(min_length=1, max_length=120, description="Short title for the note.")
    body: str = Field(default="", max_length=8000, description="Note contents.")


class ListNotesArgs(BaseModel):
    model_config = {"extra": "forbid"}

    limit: int = Field(default=10, ge=1, le=50, description="How many notes to return.")


class SearchNotesArgs(BaseModel):
    model_config = {"extra": "forbid"}

    query: str = Field(min_length=1, max_length=120, description="Text to look for.")


class DeleteNoteArgs(BaseModel):
    model_config = {"extra": "forbid"}

    note_id: int = Field(ge=1, description="Numeric id of the note to delete.")


class CreateNote(Tool):
    name: ClassVar[str] = "notes_create"
    description: ClassVar[str] = "Save a note. Use when the user wants something written down."
    args_model: ClassVar[type[BaseModel]] = CreateNoteArgs
    risk: ClassVar[RiskTier] = RiskTier.SAFE

    async def run(self, args: CreateNoteArgs, context: ToolContext) -> ToolResult:
        note = await asyncio.to_thread(context.db.create_note, args.title, args.body)
        return ToolResult(
            ok=True,
            content=f"Saved note #{note.id}: {note.title}",
            data={"note": note.model_dump(mode="json")},
            refresh=("notes",),
        )


class ListNotes(Tool):
    name: ClassVar[str] = "notes_list"
    description: ClassVar[str] = "List the most recent saved notes."
    args_model: ClassVar[type[BaseModel]] = ListNotesArgs

    async def run(self, args: ListNotesArgs, context: ToolContext) -> ToolResult:
        notes = await asyncio.to_thread(context.db.list_notes, args.limit)
        if not notes:
            return ToolResult(ok=True, content="There are no saved notes.")
        summary = [{"id": n.id, "title": n.title, "body": n.body[:160]} for n in notes]
        return ToolResult(
            ok=True,
            content=compact_json(summary),
            data={"notes": [n.model_dump(mode="json") for n in notes]},
        )


class SearchNotes(Tool):
    name: ClassVar[str] = "notes_search"
    description: ClassVar[str] = "Search saved notes by text in the title or body."
    args_model: ClassVar[type[BaseModel]] = SearchNotesArgs

    async def run(self, args: SearchNotesArgs, context: ToolContext) -> ToolResult:
        notes = await asyncio.to_thread(context.db.search_notes, args.query)
        if not notes:
            return ToolResult(ok=True, content=f"No notes match {args.query!r}.")
        summary = [{"id": n.id, "title": n.title, "body": n.body[:160]} for n in notes]
        return ToolResult(
            ok=True,
            content=compact_json(summary),
            data={"notes": [n.model_dump(mode="json") for n in notes]},
        )


class DeleteNote(Tool):
    name: ClassVar[str] = "notes_delete"
    description: ClassVar[str] = "Delete a note by its numeric id. Deletion cannot be undone."
    args_model: ClassVar[type[BaseModel]] = DeleteNoteArgs
    risk: ClassVar[RiskTier] = RiskTier.DESTRUCTIVE

    def confirmation_prompt(self, args: BaseModel, context: ToolContext) -> str:
        note_id = getattr(args, "note_id", "?")
        note = context.db.get_note(int(note_id)) if isinstance(note_id, int) else None
        label = f'"{note.title}"' if note else f"#{note_id}"
        return f"Permanently delete note {label}?"

    async def run(self, args: DeleteNoteArgs, context: ToolContext) -> ToolResult:
        deleted = await asyncio.to_thread(context.db.delete_note, args.note_id)
        if not deleted:
            return ToolResult.failure(f"no note with id {args.note_id}")
        return ToolResult(
            ok=True,
            content=f"Deleted note #{args.note_id}.",
            refresh=("notes",),
        )
