# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Tests for SDK metrics collection."""

import threading

from oxly.metrics import SDKMetrics, get_metrics


def test_metrics_increment():
    """Test atomic increment of metric counters."""
    m = SDKMetrics()
    m.increment("spans_created")
    m.increment("spans_created")
    m.increment("spans_created", 5)
    assert m.spans_created == 7


def test_metrics_record_export_success():
    """Test recording a successful export."""
    m = SDKMetrics()
    m.record_export(span_count=10, latency_ms=150.0, success=True)
    assert m.export_attempts == 1
    assert m.export_successes == 1
    assert m.spans_exported == 10
    assert m.export_latency_ms_total == 150.0
    assert m.export_latency_ms_max == 150.0


def test_metrics_record_export_failure():
    """Test recording a failed export."""
    m = SDKMetrics()
    m.record_export(span_count=5, latency_ms=50.0, success=False)
    assert m.export_attempts == 1
    assert m.export_failures == 1
    assert m.spans_failed == 5
    assert m.spans_exported == 0


def test_metrics_snapshot():
    """Test snapshot returns consistent point-in-time view."""
    m = SDKMetrics()
    m.increment("spans_created", 100)
    m.record_export(span_count=50, latency_ms=200.0, success=True)
    m.record_export(span_count=10, latency_ms=100.0, success=True)

    snap = m.snapshot()
    assert snap["spans_created"] == 100
    assert snap["spans_exported"] == 60
    assert snap["export_latency_ms_avg"] == 150.0
    assert snap["export_latency_ms_max"] == 200.0


def test_metrics_thread_safety():
    """Test that metrics are thread-safe under concurrent increments."""
    m = SDKMetrics()

    def increment_many():
        for _ in range(1000):
            m.increment("spans_created")

    threads = [threading.Thread(target=increment_many) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert m.spans_created == 10000


def test_get_metrics_singleton():
    """Test that get_metrics returns the global singleton."""
    m1 = get_metrics()
    m2 = get_metrics()
    assert m1 is m2


def test_metrics_buffer_state():
    """Test recording buffer state."""
    m = SDKMetrics()
    m.record_buffer_state(current_size=100, capacity=2048, overflow=False)
    assert m.buffer_current_size == 100
    assert m.buffer_capacity == 2048
    assert m.buffer_overflow_count == 0

    m.record_buffer_state(current_size=2048, capacity=2048, overflow=True)
    assert m.buffer_overflow_count == 1
    assert m.spans_dropped == 1
