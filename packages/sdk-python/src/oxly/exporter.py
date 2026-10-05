# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""BatchSpanProcessor  collects spans and exports them in batches.

Runs a background daemon thread that periodically flushes accumulated spans
to the Collector via HTTP. If the Collector is unreachable, spans are saved
to the local SQLite store for later replay.

The processor is completely non-blocking  span.end() returns immediately
after queuing the span. An atexit hook ensures remaining spans are flushed
on process shutdown.

Flush triggers:
    - Ring buffer reaches batch_size (default 64)
    - Export interval timer fires (default 5 seconds)
    - Process exit (atexit hook)
    - Manual flush() call
"""

from __future__ import annotations

import atexit
import logging
import threading
import time
from typing import TYPE_CHECKING

from oxly._internal.buffer import RingBuffer
from oxly._internal.transport import HttpTransport
from oxly.config import get_config
from oxly.local_store import LocalStore, get_local_store

if TYPE_CHECKING:
    from oxly.tracer import Span

logger = logging.getLogger("oxly")


class BatchSpanProcessor:
    """Collects spans into a ring buffer and exports them in batches.

    The processor runs a background daemon thread that:
    1. Waits for batch_size spans OR export_interval to elapse
    2. Drains spans from the ring buffer
    3. Serializes and sends via HttpTransport
    4. Falls back to LocalStore on transport failure
    5. Periodically retries unsent spans from LocalStore

    Args:
        transport: HttpTransport instance (or None for local-only mode).
        local_store: LocalStore instance for fallback persistence.
        batch_size: Number of spans that triggers an immediate flush.
        export_interval_s: Maximum seconds between flushes.
        max_queue_size: Ring buffer capacity.
    """

    def __init__(
        self,
        transport: HttpTransport | None = None,
        local_store: LocalStore | None = None,
        batch_size: int = 64,
        export_interval_s: float = 5.0,
        max_queue_size: int = 2048,
    ) -> None:
        self._transport = transport
        self._local_store = local_store or get_local_store()
        self._batch_size = batch_size
        self._export_interval = export_interval_s
        self._buffer = RingBuffer(capacity=max_queue_size)

        # Background thread control
        self._shutdown_event = threading.Event()
        self._flush_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False
        self._lock = threading.Lock()

        # Stats
        self._exported_count = 0
        self._failed_count = 0
        self._fallback_count = 0

    def start(self) -> None:
        """Start the background export thread."""
        with self._lock:
            if self._started:
                return
            self._started = True
            self._thread = threading.Thread(
                target=self._export_loop,
                name="oxly-exporter",
                daemon=True,
            )
            self._thread.start()
            atexit.register(self.shutdown)

    def add(self, span: Span) -> None:
        """Queue a span for export.

        This method is called by Span.end() and returns immediately.
        If the background thread hasn't been started yet, it is started now.

        Args:
            span: The Span to export.
        """
        if not self._started:
            self.start()

        self._buffer.add(span)

        # Signal flush if batch size reached
        if self._buffer.size >= self._batch_size:
            self._flush_event.set()

    def flush(self) -> None:
        """Manually trigger a flush of all buffered spans.

        Blocks until the current buffer contents have been exported.
        """
        self._do_flush()

    def _export_loop(self) -> None:
        """Background loop that periodically exports buffered spans."""
        retry_counter = 0

        while not self._shutdown_event.is_set():
            # Wait for flush signal or timeout
            triggered = self._flush_event.wait(timeout=self._export_interval)
            self._flush_event.clear()

            # Drain and export
            self._do_flush()

            # Periodically retry unsent spans from local store (every ~30s)
            retry_counter += 1
            if retry_counter >= 6:  # ~30s at 5s intervals
                retry_counter = 0
                self._retry_unsent()

        # Final flush on shutdown
        self._do_flush()

    def _do_flush(self) -> None:
        """Drain the ring buffer and export all spans."""
        spans = self._buffer.drain()
        if not spans:
            return

        # Convert Span objects to SpanModel dicts
        span_models = []
        export_dicts = []
        for span in spans:
            try:
                model = span.to_model()
                span_models.append(model)
                export_dicts.append(model.to_export_dict())
            except Exception:
                logger.debug("Failed to serialize span", exc_info=True)

        if not export_dicts:
            return

        # Attempt remote export with metrics tracking
        if self._transport:
            start_time = time.monotonic()
            result = self._transport.send(export_dicts)
            latency_ms = (time.monotonic() - start_time) * 1000.0

            try:
                from oxly.metrics import get_metrics
                get_metrics().record_export(len(export_dicts), latency_ms, result.success)
                if not result.success:
                    get_metrics().increment("export_retries")
            except ImportError:
                pass

            if result.success:
                self._exported_count += len(export_dicts)
                logger.debug(
                    "Exported %d spans (total: %d, latency: %.1fms)",
                    len(export_dicts), self._exported_count, latency_ms
                )
                return
            else:
                logger.debug("Transport failed: %s  falling back to local store", result.error)
                self._failed_count += len(export_dicts)

        # Fallback: save to local SQLite
        saved = self._local_store.save_spans(span_models)
        self._fallback_count += saved
        try:
            from oxly.metrics import get_metrics
            get_metrics().increment("local_store_saves", saved)
        except ImportError:
            pass
        logger.debug("Saved %d spans to local store (total fallback: %d)", saved, self._fallback_count)

    def _retry_unsent(self) -> None:
        """Attempt to re-export spans saved in the local store."""
        if not self._transport:
            return

        try:
            unsent = self._local_store.get_unsent_spans(limit=100)
        except Exception:
            logger.debug("Failed to retrieve unsent spans from local store", exc_info=True)
            try:
                from oxly.metrics import get_metrics
                get_metrics().increment("local_store_errors")
            except ImportError:
                pass
            return

        if not unsent:
            return

        export_dicts = [s.to_export_dict() for s in unsent]
        result = self._transport.send(export_dicts)

        if result.success:
            span_ids = [s.span_id for s in unsent]
            self._local_store.mark_as_sent(span_ids)
            self._exported_count += len(span_ids)
            try:
                from oxly.metrics import get_metrics
                get_metrics().increment("local_store_retrievals", len(span_ids))
            except ImportError:
                pass
            logger.debug("Replayed %d unsent spans from local store", len(span_ids))

    def shutdown(self, timeout_s: float = 5.0) -> None:
        """Gracefully shut down: flush remaining spans and stop the background thread.

        Args:
            timeout_s: Maximum seconds to wait for the background thread to finish.
        """
        with self._lock:
            if not self._started:
                return
            self._started = False

        self._shutdown_event.set()
        self._flush_event.set()  # Wake the thread

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout_s)

        logger.debug(
            "Exporter shutdown: exported=%d, failed=%d, fallback=%d",
            self._exported_count, self._failed_count, self._fallback_count,
        )

    @property
    def stats(self) -> dict[str, int]:
        """Export statistics."""
        return {
            "exported": self._exported_count,
            "failed": self._failed_count,
            "fallback": self._fallback_count,
            "buffered": self._buffer.size,
            "dropped": self._buffer.dropped_count,
        }

    def __repr__(self) -> str:
        return (
            f"BatchSpanProcessor(batch_size={self._batch_size}, "
            f"interval={self._export_interval}s, buffered={self._buffer.size})"
        )


#  Module-level singleton

_processor: BatchSpanProcessor | None = None
_processor_lock = threading.Lock()


def get_processor() -> BatchSpanProcessor | None:
    """Return the global BatchSpanProcessor singleton.

    Lazy-initialized from config. Returns None if SDK is disabled.
    """
    global _processor
    if _processor is not None:
        return _processor

    with _processor_lock:
        if _processor is not None:
            return _processor

        config = get_config()
        if not config.enabled:
            return None

        # Create transport (None if no API key = local-only mode)
        transport = None
        if config.api_key:
            transport = HttpTransport(
                collector_url=config.collector_url,
                api_key=config.api_key,
            )

        _processor = BatchSpanProcessor(
            transport=transport,
            batch_size=config.batch_size,
            export_interval_s=config.export_interval_ms / 1000.0,
            max_queue_size=config.max_queue_size,
        )
        return _processor


def reset_processor() -> None:
    """Reset the global processor singleton. Primarily for testing."""
    global _processor
    if _processor is not None:
        _processor.shutdown()
    _processor = None
