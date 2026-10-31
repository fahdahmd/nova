"""Row models for the SQLite tables."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

RiskTier = Literal["safe", "sensitive", "destructive"]
AuditStatus = Literal["ok", "error", "denied", "timeout", "pending"]


class Note(BaseModel):
    id: int
    title: str
    body: str
    created_at: datetime
    updated_at: datetime


class Todo(BaseModel):
    id: int
    text: str
    done: bool
    created_at: datetime
    completed_at: datetime | None = None


class Reminder(BaseModel):
    id: int
    text: str
    due_at: datetime
    created_at: datetime
    status: Literal["pending", "fired", "cancelled"] = "pending"


class MemoryFact(BaseModel):
    id: int
    content: str
    source: Literal["user", "assistant"] = "user"
    created_at: datetime


class ChatMessage(BaseModel):
    id: int
    role: Literal["user", "assistant", "tool", "system"]
    content: str
    created_at: datetime


class AuditEntry(BaseModel):
    id: int
    tool: str
    args_preview: str
    risk: RiskTier
    status: AuditStatus
    detail: str = ""
    duration_ms: int = 0
    created_at: datetime


class Preference(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    value: str = Field(max_length=512)
