"""The tool registry.

Tool names are kept short and unambiguous: a 4B model picks the right tool far
more reliably from ``todos_add`` than from a long prose description.
"""

from __future__ import annotations

from nova.tools.apps import ListApps, OpenApp
from nova.tools.base import (
    NoArgs,
    RiskTier,
    Tool,
    ToolContext,
    ToolRegistry,
    ToolResult,
    format_validation_error,
    wrap_untrusted,
)
from nova.tools.calculator import Calculator, CalculatorError, evaluate
from nova.tools.files import ListAllowedFolders, ReadFile, SearchFiles
from nova.tools.memory import Forget, ListMemory, Remember
from nova.tools.notes import CreateNote, DeleteNote, ListNotes, SearchNotes
from nova.tools.reminders import CancelReminder, ListReminders, SetReminder
from nova.tools.system import SystemInfo
from nova.tools.todos import AddTodo, CompleteTodo, DeleteTodo, ListTodos
from nova.tools.weather import Weather
from nova.tools.websearch import WebSearch

__all__ = [
    "Calculator",
    "CalculatorError",
    "NoArgs",
    "RiskTier",
    "Tool",
    "ToolContext",
    "ToolRegistry",
    "ToolResult",
    "build_registry",
    "evaluate",
    "format_validation_error",
    "wrap_untrusted",
]


def build_registry() -> ToolRegistry:
    """Create a registry holding one instance of every tool."""
    return ToolRegistry(
        [
            # notes
            CreateNote(),
            ListNotes(),
            SearchNotes(),
            DeleteNote(),
            # todos
            AddTodo(),
            ListTodos(),
            CompleteTodo(),
            DeleteTodo(),
            # reminders
            SetReminder(),
            ListReminders(),
            CancelReminder(),
            # files
            SearchFiles(),
            ReadFile(),
            ListAllowedFolders(),
            # apps
            ListApps(),
            OpenApp(),
            # local system
            SystemInfo(),
            Calculator(),
            # memory
            Remember(),
            ListMemory(),
            Forget(),
            # network (both toggleable)
            Weather(),
            WebSearch(),
        ]
    )
