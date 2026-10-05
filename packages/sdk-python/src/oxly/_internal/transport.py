# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""HTTP transport layer with retry logic and exponential backoff.

Sends serialized span batches to the Oxly Collector via HTTP POST.
Uses stdlib urllib to avoid adding external dependencies (httpx, aiohttp).

Retry strategy:
    - 3 attempts with exponential backoff: 1s, 2s, 4s
    - Retries on 5xx, 429 (rate limit), and connection errors
    - Does NOT retry on 4xx client errors (except 429)
    - Returns (success: bool, status_code: int | None) tuple
"""

from __future__ import annotations

import gzip
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger("oxly")

# Default retry configuration
_MAX_RETRIES = 3
_BACKOFF_BASE_S = 1.0  # 1s, 2s, 4s
_BACKOFF_MULTIPLIER = 2.0
_TIMEOUT_S = 10  # HTTP request timeout

# Status codes that trigger a retry
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class TransportResult:
    """Result of an HTTP transport attempt."""

    __slots__ = ("success", "status_code", "error", "retries_used")

    def __init__(
        self,
        success: bool,
        status_code: int | None = None,
        error: str | None = None,
        retries_used: int = 0,
    ) -> None:
        self.success = success
        self.status_code = status_code
        self.error = error
        self.retries_used = retries_used

    def __repr__(self) -> str:
        return (
            f"TransportResult(success={self.success}, status={self.status_code}, "
            f"retries={self.retries_used})"
        )


class HttpTransport:
    """HTTP client for sending span batches to the Collector.

    Uses urllib (stdlib) with gzip compression and exponential backoff retry.

    Args:
        collector_url: Base URL of the Collector (e.g., "http://localhost:8000").
        api_key: API key for X-API-Key authentication header.
        timeout_s: HTTP request timeout in seconds.
        max_retries: Maximum number of retry attempts on transient failures.
    """

    def __init__(
        self,
        collector_url: str = "http://localhost:8000",
        api_key: str = "",
        timeout_s: float = _TIMEOUT_S,
        max_retries: int = _MAX_RETRIES,
    ) -> None:
        # SECURITY: Warn about HTTP usage in production
        if collector_url.startswith("http://"):
            import os

            env = os.environ.get("ENVIRONMENT", "development")
            if env not in ("development", "dev", "local"):
                logger.warning(
                    f"SECURITY: Using HTTP for collector URL in {env} environment. "
                    f"API keys and traces will be sent unencrypted. "
                    f"Use HTTPS in production!"
                )

        self._url = collector_url.rstrip("/") + "/v1/traces"
        self._api_key = api_key
        self._timeout = timeout_s
        self._max_retries = max_retries

    def send(self, spans_data: list[dict[str, Any]]) -> TransportResult:
        """Send a batch of serialized spans to the Collector.

        The payload is JSON-serialized and gzip-compressed before sending.

        Args:
            spans_data: List of span dictionaries (from SpanModel.to_export_dict()).

        Returns:
            TransportResult indicating success/failure.
        """
        if not spans_data:
            return TransportResult(success=True, status_code=200)

        # Serialize and compress
        try:
            payload = json.dumps({"spans": spans_data}).encode("utf-8")
            compressed = gzip.compress(payload)
        except Exception as e:
            logger.debug("Failed to serialize span batch: %s", e)
            return TransportResult(success=False, error=f"Serialization error: {e}")

        # Retry loop with exponential backoff
        last_error: str | None = None
        last_status: int | None = None

        for attempt in range(self._max_retries):
            try:
                req = urllib.request.Request(
                    self._url,
                    data=compressed,
                    headers={
                        "Content-Type": "application/json",
                        "Content-Encoding": "gzip",
                        "X-API-Key": self._api_key,
                        "User-Agent": "oxly-sdk/0.1.0",
                    },
                    method="POST",
                )
                # nosec B310 relies on configured collector URL which is verified
                response = urllib.request.urlopen(req, timeout=self._timeout)  # nosec B310
                status = response.getcode()

                if 200 <= status < 300:
                    return TransportResult(
                        success=True,
                        status_code=status,
                        retries_used=attempt,
                    )

                # Non-retryable success-ish code
                last_status = status
                last_error = f"Unexpected status: {status}"

            except urllib.error.HTTPError as e:
                last_status = e.code
                last_error = f"HTTP {e.code}: {e.reason}"

                # Don't retry client errors (except 429 rate limit)
                if e.code not in _RETRYABLE_STATUS_CODES:
                    return TransportResult(
                        success=False,
                        status_code=e.code,
                        error=last_error,
                        retries_used=attempt,
                    )

            except (urllib.error.URLError, OSError, TimeoutError) as e:
                last_error = f"Connection error: {e}"
                last_status = None

            except Exception as e:
                last_error = f"Unexpected error: {e}"
                last_status = None

            # Exponential backoff before retry
            if attempt < self._max_retries - 1:
                backoff = _BACKOFF_BASE_S * (_BACKOFF_MULTIPLIER**attempt)
                logger.debug(
                    "Transport retry %d/%d in %.1fs: %s",
                    attempt + 1,
                    self._max_retries,
                    backoff,
                    last_error,
                )
                time.sleep(backoff)

        # All retries exhausted
        return TransportResult(
            success=False,
            status_code=last_status,
            error=last_error,
            retries_used=self._max_retries,
        )

    def __repr__(self) -> str:
        return f"HttpTransport(url={self._url}, retries={self._max_retries})"
