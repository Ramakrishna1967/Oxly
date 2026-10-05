# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Pydantic v2 data models for Oxly spans and traces.

These models define the canonical data shapes used throughout the SDK.
"""

from __future__ import annotations

import enum
import uuid
from typing import Any

from pydantic import BaseModel, Field


class SpanStatus(str, enum.Enum):
    """Status of a completed span."""

    OK = "OK"
    ERROR = "ERROR"
    UNSET = "UNSET"


class SpanEvent(BaseModel):
    """An event recorded during a span's lifetime.

    Events are discrete occurrences within a span, such as a log message,
    a streaming chunk arrival, or an exception.
    """

    name: str
    timestamp: int = Field(description="Wall-clock time in nanoseconds since epoch")
    attributes: dict[str, str] = Field(default_factory=dict)


class SpanModel(BaseModel):
    """Canonical Span data model.

    A Span represents a single unit of work within a Trace  an LLM call,
    a tool invocation, a memory read, or any @observe-decorated function.
    """

    trace_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="UUID grouping all spans in one agent execution",
    )
    span_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique ID for this operation",
    )
    parent_span_id: str | None = Field(
        default=None,
        description="Parent span ID forming the tree structure",
    )
    name: str = Field(description="Operation name: llm.chat, tool.call, memory.read, etc.")
    start_time: int = Field(
        default=0,
        description="Wall-clock start time in nanoseconds since epoch",
    )
    end_time: int = Field(
        default=0,
        description="Wall-clock end time in nanoseconds since epoch",
    )
    duration_ms: int = Field(
        default=0,
        description="Computed duration in milliseconds",
    )
    status: SpanStatus = Field(default=SpanStatus.OK)
    service_name: str = Field(default="default")
    attributes: dict[str, str] = Field(
        default_factory=dict,
        description="Key-value pairs: llm.model, llm.tokens.in, tool.name, etc.",
    )
    events: list[SpanEvent] = Field(default_factory=list)
    project_id: str = Field(default="")

    def to_export_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary suitable for JSON export / transport."""
        return self.model_dump(mode="json")
