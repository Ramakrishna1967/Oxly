# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Thread-safe ring buffer for in-memory span batching.

The buffer holds spans in memory until the BatchSpanProcessor flushes them.
When the buffer is full (capacity reached), the oldest spans are silently
dropped  this prevents unbounded memory growth under high load.

Thread safety is provided via a threading.Lock wrapping all mutations.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Generic, TypeVar

T = TypeVar("T")


class RingBuffer(Generic[T]):
    """Fixed-capacity, thread-safe ring buffer using collections.deque.

    When the buffer is at capacity, new items silently overwrite the oldest.
    This is the desired behavior for observability  we'd rather lose old
    spans than crash or block the user's application.

    Args:
        capacity: Maximum number of items the buffer can hold.
    """

    __slots__ = ("_buffer", "_lock", "_capacity", "_dropped")

    def __init__(self, capacity: int = 2048) -> None:
        self._capacity = capacity
        self._buffer: deque[T] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._dropped: int = 0

    def add(self, item: T) -> None:
        """Add an item to the buffer.

        If the buffer is full, the oldest item is silently dropped and
        the drop counter is incremented. Metrics are updated if available.
        """
        with self._lock:
            overflow = len(self._buffer) >= self._capacity
            if overflow:
                self._dropped += 1
            self._buffer.append(item)

        # Update metrics outside lock to avoid deadlock
        if overflow:
            try:
                from oxly.metrics import get_metrics
                metrics = get_metrics()
                metrics.record_buffer_state(len(self._buffer), self._capacity, overflow=True)
            except ImportError:
                pass

    def drain(self) -> list[T]:
        """Remove and return ALL items from the buffer.

        Returns an empty list if the buffer is empty. This is the primary
        method used by the BatchSpanProcessor to collect spans for export.
        """
        with self._lock:
            items = list(self._buffer)
            self._buffer.clear()
            return items

    def peek(self, n: int | None = None) -> list[T]:
        """Return up to n items without removing them. Returns all if n is None."""
        with self._lock:
            if n is None:
                return list(self._buffer)
            return list(self._buffer)[:n]

    @property
    def size(self) -> int:
        """Current number of items in the buffer."""
        with self._lock:
            return len(self._buffer)

    @property
    def capacity(self) -> int:
        """Maximum buffer capacity."""
        return self._capacity

    @property
    def is_full(self) -> bool:
        """Whether the buffer is at capacity."""
        with self._lock:
            return len(self._buffer) >= self._capacity

    @property
    def is_empty(self) -> bool:
        """Whether the buffer has no items."""
        with self._lock:
            return len(self._buffer) == 0

    @property
    def dropped_count(self) -> int:
        """Total number of items dropped due to buffer overflow."""
        return self._dropped

    def clear(self) -> None:
        """Remove all items from the buffer."""
        with self._lock:
            self._buffer.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)

    def __repr__(self) -> str:
        return f"RingBuffer(size={self.size}, capacity={self._capacity}, dropped={self._dropped})"
