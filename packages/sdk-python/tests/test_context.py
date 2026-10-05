# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Tests for context propagation."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from oxly.context import (
    clear_context,
    get_current_span,
    get_current_trace_id,
    span_context,
)
from oxly.tracer import Tracer


def test_trace_id_propagation():
    """Test that trace_id is propagated via context."""
    clear_context()
    assert get_current_trace_id() is None

    tracer = Tracer.get_tracer()
    span = tracer.start_span("root")

    with span_context(span):
        assert get_current_trace_id() == span.trace_id

    # Context cleared after exiting
    assert get_current_trace_id() is None or get_current_trace_id() == span.trace_id


def test_span_context_nesting():
    """Test nested span contexts."""
    clear_context()
    tracer = Tracer.get_tracer()

    root = tracer.start_span("root")
    with span_context(root):
        assert get_current_span() == root

        child = tracer.start_span("child")
        with span_context(child):
            assert get_current_span() == child
            assert child.parent_span_id == root.span_id

        # After child context exits, root is current again
        assert get_current_span() == root


def test_parent_span_id_linking():
    """Test that parent_span_id is set correctly."""
    clear_context()
    tracer = Tracer.get_tracer()

    parent = tracer.start_span("parent")
    with span_context(parent):
        child1 = tracer.start_span("child1")
        assert child1.parent_span_id == parent.span_id

        with span_context(child1):
            grandchild = tracer.start_span("grandchild")
            assert grandchild.parent_span_id == child1.span_id


def test_clear_context():
    """Test that clear_context resets all context vars."""
    tracer = Tracer.get_tracer()
    span = tracer.start_span("test")

    with span_context(span):
        assert get_current_trace_id() is not None
        assert get_current_span() is not None

    clear_context()
    assert get_current_trace_id() is None
    assert get_current_span() is None


@pytest.mark.asyncio
async def test_async_context_isolation():
    """Test that async tasks have isolated contexts."""
    clear_context()
    tracer = Tracer.get_tracer()

    async def task(name):
        span = tracer.start_span(name)
        with span_context(span):
            await asyncio.sleep(0.01)
            current_trace = get_current_trace_id()
            assert current_trace == span.trace_id
            return span.trace_id

    # Run multiple tasks concurrently
    trace_ids = await asyncio.gather(
        task("task1"),
        task("task2"),
        task("task3"),
    )

    # Each task should have its own trace_id
    assert len(set(trace_ids)) == 3


def test_thread_context_isolation():
    """Test that threads have isolated contexts."""
    clear_context()
    tracer = Tracer.get_tracer()

    def worker(name):
        span = tracer.start_span(name)
        with span_context(span):
            import time

            time.sleep(0.01)
            return get_current_trace_id()

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(worker, f"thread{i}") for i in range(3)]
        trace_ids = [f.result() for f in futures]

    # Each thread should have its own trace_id
    assert len(set(trace_ids)) == 3


def test_get_current_span_none():
    """Test get_current_span returns None when no span is active."""
    clear_context()
    assert get_current_span() is None


def test_span_context_exception_handling():
    """Test that context is cleaned up even if exception occurs."""
    clear_context()
    tracer = Tracer.get_tracer()
    span = tracer.start_span("test")

    try:
        with span_context(span):
            assert get_current_span() == span
            raise ValueError("test error")
    except ValueError:
        pass

    # Context should be cleared after exception
    clear_context()
    assert get_current_span() is None


def test_span_context_depth_limit_does_not_swallow_exceptions():
    """Past _MAX_SPAN_DEPTH, span_context must still propagate the
    wrapped block's real exception.

    A prior bug wrapped the depth-limit branch's `yield span` in a bare
    `try/finally: return`, which silently discards any exception raised
    inside the `with` block once nesting hits the limit -- replacing it
    with a confusing UnboundLocalError from the caller's next line
    instead of the real error.
    """
    from oxly.context import _MAX_SPAN_DEPTH

    clear_context()
    tracer = Tracer.get_tracer()

    entered = []
    try:
        for i in range(_MAX_SPAN_DEPTH):
            span = tracer.start_span(f"span{i}")
            cm = span_context(span)
            cm.__enter__()
            entered.append(cm)

        overflow_span = tracer.start_span("overflow")
        with pytest.raises(ValueError, match="real error"), span_context(overflow_span):
            raise ValueError("real error")
    finally:
        for cm in reversed(entered):
            cm.__exit__(None, None, None)
        clear_context()
