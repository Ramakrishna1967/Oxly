# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""LangGraph auto-instrumentation via monkey-patching.

Automatically creates spans for each LangGraph node execution by wrapping
the node functions when the graph is compiled.

This is completely transparent  developers don't need to add @observe to
their node functions. Oxly captures:
    - Node name
    - Input state
    - Output state
    - Execution duration
    - Errors

Instrumentation is applied when instrument() is called during SDK init.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("oxly")

_instrumented = False


def instrument() -> None:
    """Apply LangGraph instrumentation by monkey-patching StateGraph.

    This modifies the LangGraph library to automatically create spans
    for node executions. Safe to call multiple times (idempotent).
    """
    global _instrumented
    if _instrumented:
        return

    try:
        # Import LangGraph components
        from langgraph.graph import StateGraph

        # Save the original compile method
        original_compile = StateGraph.compile

        def instrumented_compile(self, *args: Any, **kwargs: Any) -> Any:
            """Wrapped compile that instruments all nodes before compilation."""
            # Instrument each node in the graph
            for node_name, node_spec in self.nodes.items():
                # LangGraph 1.x uses NodeSpec objects
                if hasattr(node_spec, "runnable") and hasattr(node_spec, "ends"):
                    runnable = node_spec.runnable

                    # Sync
                    if (
                        hasattr(runnable, "func")
                        and runnable.func
                        and not hasattr(runnable.func, "_oxly_instrumented")
                    ):
                        runnable.func = _instrument_node(node_name, runnable.func)

                    # Async
                    if (
                        hasattr(runnable, "afunc")
                        and runnable.afunc
                        and not hasattr(runnable.afunc, "_oxly_instrumented")
                    ):
                        runnable.afunc = _instrument_node(node_name, runnable.afunc)

                # Fallback for older versions where nodes were direct functions
                elif not hasattr(node_spec, "_oxly_instrumented") and callable(node_spec):
                    self.nodes[node_name] = _instrument_node(node_name, node_spec)

            # Call the original compile
            return original_compile(self, *args, **kwargs)

        # Replace the compile method
        StateGraph.compile = instrumented_compile  # type: ignore[method-assign]
        _instrumented = True
        logger.debug("LangGraph instrumentation applied successfully")

    except ImportError:
        logger.debug("LangGraph not installed, skipping instrumentation")
    except Exception:
        logger.debug("Failed to instrument LangGraph", exc_info=True)


def _instrument_node(node_name: str, node_func: Callable) -> Callable:
    """Wrap a LangGraph node function to create spans.

    Supports both sync and async node functions.

    Args:
        node_name: Name of the node in the graph.
        node_func: The original node function.

    Returns:
        Wrapped function that creates spans.
    """
    import asyncio

    from oxly.context import span_context
    from oxly.tracer import Tracer

    if asyncio.iscoroutinefunction(node_func):

        @functools.wraps(node_func)
        async def async_wrapped(*args: Any, **kwargs: Any) -> Any:
            tracer = Tracer.get_tracer()
            span = tracer.start_span(f"langgraph.node.{node_name}")

            try:
                span.set_attribute("langgraph.node.name", node_name)
                span.set_attribute("framework", "langgraph")

                if args and isinstance(args[0], dict):
                    span.set_attribute("langgraph.input.keys", str(list(args[0].keys())))

                with span_context(span):
                    result = await node_func(*args, **kwargs)

                if isinstance(result, dict):
                    span.set_attribute("langgraph.output.keys", str(list(result.keys())))

                span.end()
                return result

            except Exception as exc:
                span.record_exception(exc)
                span.end()
                raise

        async_wrapped._oxly_instrumented = True  # type: ignore
        return async_wrapped

    @functools.wraps(node_func)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        tracer = Tracer.get_tracer()
        span = tracer.start_span(f"langgraph.node.{node_name}")

        try:
            span.set_attribute("langgraph.node.name", node_name)
            span.set_attribute("framework", "langgraph")

            # Capture input state (first arg is usually the state dict)
            if args and isinstance(args[0], dict):
                span.set_attribute("langgraph.input.keys", str(list(args[0].keys())))

            # Execute the node
            with span_context(span):
                result = node_func(*args, **kwargs)

            # Capture output state
            if isinstance(result, dict):
                span.set_attribute("langgraph.output.keys", str(list(result.keys())))

            span.end()
            return result

        except Exception as exc:
            span.record_exception(exc)
            span.end()
            raise

    # Mark as instrumented to avoid double-wrapping
    wrapped._oxly_instrumented = True  # type: ignore
    return wrapped
