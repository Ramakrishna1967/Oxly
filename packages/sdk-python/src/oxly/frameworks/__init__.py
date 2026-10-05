# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Framework auto-detection and instrumentation.

Detects which agent frameworks are installed (LangGraph, CrewAI, AutoGen)
and automatically applies instrumentation hooks to create spans without
requiring manual @observe decorators.

This module is imported lazily when the user calls init() with auto_instrument=True.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

logger = logging.getLogger("oxly")


def detect_frameworks() -> dict[str, bool]:
    """Detect which frameworks are available in the current environment.

    Returns:
        Dict mapping framework name to availability (True if installed).
    """
    frameworks = {}

    # Check for LangGraph
    try:
        import langgraph  # noqa: F401
        frameworks["langgraph"] = True
    except ImportError:
        frameworks["langgraph"] = False

    # Check for CrewAI
    try:
        import crewai  # noqa: F401
        frameworks["crewai"] = True
    except ImportError:
        frameworks["crewai"] = False

    # Check for AutoGen
    try:
        import autogen  # noqa: F401
        frameworks["autogen"] = True
    except ImportError:
        frameworks["autogen"] = False

    return frameworks


def auto_instrument() -> dict[str, bool]:
    """Automatically instrument all detected frameworks.

    This function should be called once during SDK initialization.
    It detects available frameworks and applies instrumentation.

    Returns:
        Dict mapping framework name to instrumentation success.
    """
    detected = detect_frameworks()
    results = {}

    if detected.get("langgraph"):
        try:
            from oxly.frameworks import langgraph
            langgraph.instrument()
            results["langgraph"] = True
            logger.debug("LangGraph auto-instrumentation applied")
        except Exception:
            results["langgraph"] = False
            logger.debug("Failed to instrument LangGraph", exc_info=True)

    if detected.get("crewai"):
        try:
            from oxly.frameworks import crewai
            crewai.instrument()
            results["crewai"] = True
            logger.debug("CrewAI auto-instrumentation applied")
        except Exception:
            results["crewai"] = False
            logger.debug("Failed to instrument CrewAI", exc_info=True)

    if detected.get("autogen"):
        try:
            from oxly.frameworks import autogen
            autogen.instrument()
            results["autogen"] = True
            logger.debug("AutoGen auto-instrumentation applied")
        except Exception:
            results["autogen"] = False
            logger.debug("Failed to instrument AutoGen", exc_info=True)

    return results
