# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Async-safe context propagation for trace and span linking.

Uses Python's `contextvars` module to maintain a per-task/per-thread context
that tracks the current trace_id and active span stack. This ensures that
nested @observe calls automatically link parent  child spans without any
manual wiring, even across async tasks.

Usage:
    with span_context(span):
        # span is the "current" span; any child spans created inside
        # will automatically have their parent_span_id set to span.span_id
        ...
"""

from __future__ import annotations

import contextvars
import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oxly.tracer import Span

logger = logging.getLogger("oxly.context")

#  Configuration
# Maximum span nesting depth to prevent stack overflow
_MAX_SPAN_DEPTH = 100

#  Context Variables
# These are the two core context vars that propagate across async boundaries.

_trace_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "oxly_trace_id", default=None
)

_span_stack_var: contextvars.ContextVar[list[Span]] = contextvars.ContextVar(
    "oxly_span_stack"
)


#  Public API


def get_current_trace_id() -> str | None:
    """Return the active trace_id for this context, or None if no trace is active."""
    return _trace_id_var.get()


def set_current_trace_id(trace_id: str) -> None:
    """Set the active trace_id for this context."""
    _trace_id_var.set(trace_id)


def get_current_span() -> Span | None:
    """Return the currently active span, or None if no span is active."""
    stack = _span_stack_var.get([])
    return stack[-1] if stack else None


def get_parent_span_id() -> str | None:
    """Return the span_id of the currently active span (i.e., the parent for new spans)."""
    current = get_current_span()
    return current.span_id if current else None


@contextmanager
def span_context(span: Span) -> Generator[Span, None, None]:
    """Context manager that pushes a span onto the context stack.

    While inside this context, any new spans created will automatically
    have their parent_span_id set to this span's span_id.

    SECURITY: Enforces max nesting depth to prevent stack overflow attacks.

    Args:
        span: The Span to make "current".

    Yields:
        The same span, for convenience.
    """
    # Get or create a new stack for this context (important for async copy-on-write)
    stack = _span_stack_var.get([])

    # SECURITY: Check max nesting depth to prevent stack overflow
    if len(stack) >= _MAX_SPAN_DEPTH:
        logger.warning(f"Span nesting depth limit ({_MAX_SPAN_DEPTH}) reached. "
                      f"New span will not be added to context stack. "
                      f"This may indicate infinite recursion or excessive nesting.")
        # Still yield the span but don't add to stack to prevent overflow.
        # No try/finally here: there's nothing to clean up (we never pushed
        # onto the stack), and a bare `return` in `finally` would silently
        # discard any exception the wrapped function raises.
        yield span
        return

    new_stack = stack.copy()
    new_stack.append(span)
    token_stack = _span_stack_var.set(new_stack)

    # Set trace_id if this is the root span
    token_trace: contextvars.Token[str | None] | None = None
    if _trace_id_var.get() is None:
        token_trace = _trace_id_var.set(span.trace_id)

    try:
        yield span
    finally:
        _span_stack_var.reset(token_stack)
        if token_trace is not None:
            _trace_id_var.reset(token_trace)


def clear_context() -> None:
    """Reset all context variables. Primarily for testing."""
    _trace_id_var.set(None)
    # Set to a fresh empty list
    _span_stack_var.set([])
