# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Tracer singleton and Span class  the heart of the Oxly SDK.

The Tracer is responsible for creating Spans. Each Span represents a unit
of work (LLM call, tool use, memory read, etc.) and is the primary data
structure that flows through the entire Oxly pipeline.

Usage:
    tracer = Tracer.get_tracer()
    span = tracer.start_span("llm.chat")
    span.set_attribute("llm.model", "gpt-4")
    span.end()  # sanitizes PII, serializes, queues for export
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from oxly._internal.clock import duration_ms, monotonic_ns, wall_clock_ns
from oxly.config import get_config
from oxly.context import get_current_trace_id, get_parent_span_id, set_current_trace_id
from oxly.models import SpanEvent, SpanModel, SpanStatus

logger = logging.getLogger("oxly")


class Span:
    """A single unit of work within a Trace.

    Spans are created by the Tracer and track timing, attributes, events,
    and status for one operation. On `.end()`, the span is sanitized for
    PII and dispatched to the BatchSpanProcessor for export.

    This class is NOT a Pydantic model  it is a mutable, lightweight
    wrapper used during the span's lifetime. On `.end()`, it is converted
    to a `SpanModel` for serialization.
    """

    __slots__ = (
        "trace_id",
        "span_id",
        "parent_span_id",
        "name",
        "start_time_ns",
        "end_time_ns",
        "_start_mono_ns",
        "_end_mono_ns",
        "attributes",
        "events",
        "status",
        "service_name",
        "_ended",
    )

    def __init__(
        self,
        name: str,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        service_name: str | None = None,
    ) -> None:
        # Identity
        self.trace_id: str = trace_id or get_current_trace_id() or str(uuid.uuid4())
        self.span_id: str = str(uuid.uuid4())
        self.parent_span_id: str | None = parent_span_id or get_parent_span_id()
        self.name: str = name

        # Timing  wall-clock for absolute timestamps, monotonic for duration
        self.start_time_ns: int = wall_clock_ns()
        self._start_mono_ns: int = monotonic_ns()
        self.end_time_ns: int = 0
        self._end_mono_ns: int = 0

        # Data
        self.attributes: dict[str, str] = {}
        self.events: list[SpanEvent] = []
        self.status: SpanStatus = SpanStatus.OK

        # Service name from config
        config = get_config()
        self.service_name: str = service_name or config.service_name

        # Guard against double-end
        self._ended: bool = False

        # Ensure trace_id is set in context for child spans
        if get_current_trace_id() is None:
            set_current_trace_id(self.trace_id)

    def set_attribute(self, key: str, value: Any) -> None:
        """Set a key-value attribute on this span.

        Values are converted to strings. Common keys include:
        llm.model, llm.tokens.in, llm.tokens.out, tool.name, error.message
        """
        if self._ended:
            logger.warning("Attempting to set attribute on ended span %s", self.span_id)
            return
        self.attributes[key] = str(value)

    def set_status(self, status: SpanStatus, message: str | None = None) -> None:
        """Set the span status (OK or ERROR).

        Args:
            status: SpanStatus.OK or SpanStatus.ERROR
            message: Optional error message (stored as error.message attribute)
        """
        self.status = status
        if message:
            self.attributes["error.message"] = message

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        """Record a timestamped event within this span.

        Events represent discrete occurrences like log messages, streaming
        chunks, or exceptions.

        Args:
            name: Event name (e.g., "stream_chunk", "exception")
            attributes: Optional key-value pairs for the event
        """
        if self._ended:
            logger.warning("Attempting to add event to ended span %s", self.span_id)
            return
        event_attrs = {k: str(v) for k, v in (attributes or {}).items()}
        self.events.append(
            SpanEvent(
                name=name,
                timestamp=wall_clock_ns(),
                attributes=event_attrs,
            )
        )

    def record_exception(self, exc: BaseException) -> None:
        """Convenience method to record an exception as an event + set ERROR status.

        Args:
            exc: The exception to record.
        """
        self.set_status(SpanStatus.ERROR)
        self.set_attribute("error.type", type(exc).__name__)
        self.set_attribute("error.message", str(exc))
        self.add_event(
            "exception",
            {
                "exception.type": type(exc).__name__,
                "exception.message": str(exc),
            },
        )

    def end(self) -> None:
        """End this span: record timing, sanitize PII, and queue for export.

        This method is idempotent  calling it multiple times has no effect
        after the first call.
        """
        if self._ended:
            return
        self._ended = True

        # Record end timing
        self.end_time_ns = wall_clock_ns()
        self._end_mono_ns = monotonic_ns()

        # Validate timing: ensure end is after start
        if self.end_time_ns < self.start_time_ns:
            logger.warning(f"Span {self.span_id[:8]}... has end_time before start_time, correcting")
            self.end_time_ns = self.start_time_ns
        if self._end_mono_ns < self._start_mono_ns:
            self._end_mono_ns = self._start_mono_ns

        # Sanitize PII before export
        try:
            from oxly.sanitizer import scrub_pii

            self.attributes = scrub_pii(self.attributes)
        except Exception:
            pass

        # Queue for export (import here to avoid circular import)
        try:
            from oxly.exporter import get_processor

            processor = get_processor()
            if processor is not None:
                processor.add(self)
        except Exception:
            logger.debug("Failed to queue span for export", exc_info=True)

    def to_model(self) -> SpanModel:
        """Convert this mutable Span to an immutable SpanModel for serialization."""
        config = get_config()
        return SpanModel(
            trace_id=self.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            name=self.name,
            start_time=self.start_time_ns,
            end_time=self.end_time_ns,
            duration_ms=duration_ms(self._start_mono_ns, self._end_mono_ns)
            if self._end_mono_ns
            else 0,
            status=self.status,
            service_name=self.service_name,
            attributes=self.attributes,
            events=self.events,
            project_id=config.project_id,
        )

    @property
    def is_ended(self) -> bool:
        """Whether this span has been ended."""
        return self._ended

    def __repr__(self) -> str:
        return (
            f"Span(name={self.name!r}, trace_id={self.trace_id[:8]}..., "
            f"span_id={self.span_id[:8]}..., status={self.status.value})"
        )


class NoOpSpan(Span):
    """A lightweight stub when the SDK is disabled.

    Subclasses Span (without calling its __init__) so that disabled-mode
    spans remain type-compatible with every Span consumer.
    """

    __slots__ = ()

    def __init__(self, name: str, parent_span_id: str | None = None) -> None:
        self.trace_id = get_current_trace_id() or "0" * 32
        self.span_id = "0" * 16
        self.parent_span_id = parent_span_id
        self.name = name
        self._ended = False

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_status(self, status: SpanStatus, message: str | None = None) -> None:
        pass

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        pass

    def record_exception(self, exc: BaseException) -> None:
        pass

    def end(self) -> None:
        self._ended = True

    def to_model(self) -> SpanModel:
        raise NotImplementedError("NoOpSpan cannot be serialized")

    @property
    def is_ended(self) -> bool:
        return self._ended

    def __repr__(self) -> str:
        return f"NoOpSpan(name={self.name!r})"


class Tracer:
    """Singleton Tracer that creates Spans.

    Access via Tracer.get_tracer(). The tracer is the entry point for
    all span creation in the SDK.
    """

    _instance: Tracer | None = None

    def __init__(self) -> None:
        self._config = get_config()

    @classmethod
    def get_tracer(cls) -> Tracer:
        """Return the singleton Tracer instance.

        Thread-safe via Python's GIL for simple attribute assignment.
        """
        if cls._instance is None:
            cls._instance = Tracer()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton. Primarily for testing."""
        cls._instance = None

    def start_span(
        self,
        name: str,
        parent_span_id: str | None = None,
    ) -> Span:
        """Create and return a new Span.

        If there is an active span in the current context, the new span
        will automatically be linked as a child span.

        Args:
            name: Operation name (e.g., "llm.chat", "tool.search", "memory.read")
            parent_span_id: Explicit parent span ID. If None, auto-detected from context.

        Returns:
            A new Span instance (not yet ended). Call span.end() when done.
        """
        if not self._config.enabled:
            return NoOpSpan(name=name, parent_span_id=parent_span_id)

        return Span(
            name=name,
            parent_span_id=parent_span_id,
        )

    @property
    def is_enabled(self) -> bool:
        """Whether the SDK is enabled per configuration."""
        return self._config.enabled
