# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Oxly SDK  Chrome DevTools for AI Agents.

Provides observability for AI Agents built on LangGraph, CrewAI, AutoGen,
and custom Python implementations.

Quick Start:
    from oxly import observe

    @observe
    def my_agent(query: str) -> str:
        result = llm.chat(query)
        return result

Public API:
    - observe: Decorator to instrument functions (sync + async)
    - init: Initialize the SDK with custom configuration
    - Tracer: Low-level tracer for manual span creation
    - Span: Individual span representing a unit of work
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = [
    "observe",
    "init",
    "Tracer",
    "Span",
    "OxlyConfig",
    "__version__",
]

import logging

from oxly.config import OxlyConfig, get_config, reset_config
from oxly.decorator import observe
from oxly.tracer import Span, Tracer

logger = logging.getLogger(__name__)


def init(
    *,
    api_key: str | None = None,
    collector_url: str | None = None,
    enabled: bool | None = None,
    service_name: str | None = None,
    debug: bool | None = None,
    auto_instrument: bool = True,
) -> None:
    """Initialize the Oxly SDK with custom configuration.

    Any provided arguments override the corresponding OXLY_* environment
    variables. Call this before any @observe decorators execute.

    Args:
        api_key: API key for the Collector (overrides OXLY_API_KEY).
        collector_url: Collector URL (overrides OXLY_COLLECTOR_URL).
        enabled: Enable/disable the SDK (overrides OXLY_ENABLED).
        service_name: Service name for spans (overrides OXLY_SERVICE_NAME).
        debug: Enable debug logging (overrides OXLY_DEBUG).
    """
    import os

    if api_key is not None:
        os.environ["OXLY_API_KEY"] = api_key
    if collector_url is not None:
        os.environ["OXLY_COLLECTOR_URL"] = collector_url
    if enabled is not None:
        os.environ["OXLY_ENABLED"] = str(enabled).lower()
    if service_name is not None:
        os.environ["OXLY_SERVICE_NAME"] = service_name
    if debug is not None:
        os.environ["OXLY_DEBUG"] = str(debug).lower()

    # Apply auto-instrumentation if requested
    if auto_instrument:
        try:
            from oxly.frameworks import auto_instrument as perform_auto_instrument

            perform_auto_instrument()
        except Exception:
            logger.debug("Auto-instrumentation failed", exc_info=True)

    # Reset singletons so they pick up new env vars
    reset_config()
    Tracer.reset()

    # Configure logging
    config = get_config()
    log_level = logging.DEBUG if config.debug else getattr(logging, config.log_level, logging.INFO)
    logging.getLogger("oxly").setLevel(log_level)

    if config.debug:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("[oxly] %(levelname)s %(name)s: %(message)s"))
        oxly_logger = logging.getLogger("oxly")
        if not oxly_logger.handlers:
            oxly_logger.addHandler(handler)
