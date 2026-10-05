# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Tests for BatchSpanProcessor and HTTP transport."""

import time

from oxly._internal.buffer import RingBuffer
from oxly._internal.transport import HttpTransport
from oxly.exporter import BatchSpanProcessor, get_processor, reset_processor
from oxly.tracer import Span


def test_ring_buffer_basic():
    """Test basic ring buffer operations."""
    buf = RingBuffer(capacity=5)
    assert buf.is_empty
    assert buf.size == 0

    buf.add(1)
    buf.add(2)
    buf.add(3)
    assert buf.size == 3
    assert not buf.is_empty


def test_ring_buffer_drain():
    """Test draining the buffer."""
    buf = RingBuffer(capacity=10)
    for i in range(5):
        buf.add(i)

    items = buf.drain()
    assert items == [0, 1, 2, 3, 4]
    assert buf.is_empty


def test_ring_buffer_overflow():
    """Test buffer overflow behavior."""
    buf = RingBuffer(capacity=3)
    for i in range(5):
        buf.add(i)

    assert buf.size == 3
    assert buf.dropped_count == 2
    items = buf.drain()
    # Should have the last 3 items
    assert items == [2, 3, 4]


def test_http_transport_empty_payload():
    """Test transport with empty payload."""
    transport = HttpTransport(
        collector_url="http://localhost:19999",
        api_key="test",
        max_retries=1,
    )
    result = transport.send([])
    assert result.success
    assert result.status_code == 200


def test_http_transport_connection_failure():
    """Test transport handles connection failures gracefully."""
    transport = HttpTransport(
        collector_url="http://localhost:19999",  # Non-existent server
        api_key="test",
        timeout_s=1,
        max_retries=1,
    )
    result = transport.send([{"name": "test"}])
    assert not result.success
    assert result.retries_used >= 0


def test_batch_span_processor_local_mode(local_store):
    """Test BatchSpanProcessor in local-only mode (no transport)."""
    processor = BatchSpanProcessor(
        transport=None,
        local_store=local_store,
        batch_size=5,
        export_interval_s=1.0,
    )
    processor.start()

    # Add spans
    for i in range(5):
        span = Span(name=f"test.{i}")
        span.end()
        processor.add(span)

    # Flush
    processor.flush()
    time.sleep(0.2)

    # Check stats
    stats = processor.stats
    assert "exported" in stats
    assert "buffered" in stats
    assert "dropped" in stats

    processor.shutdown()


def test_batch_span_processor_stats(local_store):
    """Test processor statistics tracking."""
    processor = BatchSpanProcessor(
        transport=None,
        local_store=local_store,
        batch_size=10,
    )
    processor.start()

    stats = processor.stats
    assert stats["exported"] == 0
    assert stats["failed"] == 0
    assert stats["fallback"] == 0
    assert stats["buffered"] == 0
    assert stats["dropped"] == 0

    processor.shutdown()


def test_batch_span_processor_graceful_shutdown(local_store):
    """Test processor shuts down gracefully."""
    processor = BatchSpanProcessor(
        transport=None,
        local_store=local_store,
    )
    processor.start()

    for i in range(3):
        span = Span(name=f"test.{i}")
        span.end()
        processor.add(span)

    # Should not hang
    processor.shutdown(timeout_s=2.0)
    assert True  # If we reach here, shutdown worked


def test_get_processor_singleton():
    """Test get_processor returns a singleton."""
    import os

    os.environ["OXLY_ENABLED"] = "true"

    reset_processor()
    p1 = get_processor()
    p2 = get_processor()

    assert p1 is not None
    assert p1 is p2

    reset_processor()
    del os.environ["OXLY_ENABLED"]


def test_get_processor_disabled():
    """Test get_processor returns None when SDK is disabled."""
    import os

    os.environ["OXLY_ENABLED"] = "false"

    reset_processor()
    from oxly.config import reset_config

    reset_config()

    processor = get_processor()
    assert processor is None

    reset_processor()
    del os.environ["OXLY_ENABLED"]
