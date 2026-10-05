# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Tests for Tracer and Span classes."""

from oxly.context import clear_context, get_current_trace_id, span_context
from oxly.models import SpanStatus
from oxly.tracer import Tracer


def test_tracer_singleton():
    """Test that Tracer is a singleton."""
    t1 = Tracer.get_tracer()
    t2 = Tracer.get_tracer()
    assert t1 is t2


def test_span_creation():
    """Test basic span creation with Tracer."""
    tracer = Tracer.get_tracer()
    span = tracer.start_span("test.operation")

    assert span.name == "test.operation"
    assert span.span_id is not None
    assert span.trace_id is not None
    assert span.status == SpanStatus.OK
    assert not span.is_ended


def test_span_set_attribute():
    """Test setting attributes on a span."""
    tracer = Tracer.get_tracer()
    span = tracer.start_span("test")

    span.set_attribute("key1", "value1")
    span.set_attribute("key2", 123)

    assert span.attributes["key1"] == "value1"
    assert span.attributes["key2"] == "123"  # Converted to string


def test_span_add_event():
    """Test adding events to a span."""
    tracer = Tracer.get_tracer()
    span = tracer.start_span("test")

    span.add_event("event1", {"detail": "info"})
    span.add_event("event2")

    assert len(span.events) == 2
    assert span.events[0].name == "event1"
    assert span.events[0].attributes["detail"] == "info"
    assert span.events[1].name == "event2"


def test_span_record_exception():
    """Test recording an exception on a span."""
    tracer = Tracer.get_tracer()
    span = tracer.start_span("test")

    try:
        raise ValueError("test error")
    except ValueError as e:
        span.record_exception(e)

    assert span.status == SpanStatus.ERROR
    assert span.attributes["error.type"] == "ValueError"
    assert "test error" in span.attributes["error.message"]
    assert len(span.events) == 1
    assert span.events[0].name == "exception"


def test_span_end():
    """Test ending a span."""
    tracer = Tracer.get_tracer()
    span = tracer.start_span("test")
    span.set_attribute("key", "value")

    assert not span.is_ended
    span.end()
    assert span.is_ended

    # Double-end is safe
    span.end()
    assert span.is_ended


def test_span_to_model():
    """Test converting Span to SpanModel."""
    tracer = Tracer.get_tracer()
    span = tracer.start_span("test.span")
    span.set_attribute("llm.model", "gpt-4")
    span.end()

    model = span.to_model()
    assert model.name == "test.span"
    assert model.attributes["llm.model"] == "gpt-4"
    assert model.duration_ms >= 0


def test_span_parent_child_linking():
    """Test that parent-child relationship is captured."""
    clear_context()
    tracer = Tracer.get_tracer()

    parent = tracer.start_span("parent")
    with span_context(parent):
        child = tracer.start_span("child")
        assert child.parent_span_id == parent.span_id
        assert child.trace_id == parent.trace_id
        child.end()
    parent.end()


def test_span_trace_id_propagation():
    """Test that trace_id is set in context."""
    clear_context()
    assert get_current_trace_id() is None

    tracer = Tracer.get_tracer()
    span = tracer.start_span("root")

    # After creating a span, trace_id should be in context
    with span_context(span):
        assert get_current_trace_id() == span.trace_id
        child = tracer.start_span("child")
        assert child.trace_id == span.trace_id
        child.end()

    span.end()
