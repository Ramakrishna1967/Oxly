# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""@observe decorator  the primary developer-facing API of Oxly.

Wraps sync and async functions to automatically create spans that capture
function arguments, return values, exceptions, and timing. The decorator
is designed to be completely transparent  it NEVER crashes the user's
application, even if the SDK itself encounters an internal error.

Usage:
    @observe
    def my_function(query: str) -> str:
        return llm.chat(query)

    @observe(name="custom_span_name")
    async def my_async_function(query: str) -> str:
        return await llm.achat(query)

    @observe(capture_args=False)
    def sensitive_function(secret: str) -> str:
        return process(secret)
"""

from __future__ import annotations

import functools
import inspect
import logging
import reprlib
from collections.abc import Callable
from typing import Any, TypeVar, overload

from oxly.context import span_context
from oxly.models import SpanStatus
from oxly.tracer import Tracer

logger = logging.getLogger("oxly")

F = TypeVar("F", bound=Callable[..., Any])

# Truncate large repr strings for span attributes
_repr = reprlib.Repr()
_repr.maxstring = 256
_repr.maxother = 256
_safe_repr = _repr.repr


def _safe_str(value: Any) -> str:
    """Safely convert any value to a bounded string for span attributes."""
    try:
        return _safe_repr(value)
    except Exception:
        return "<unrepresentable>"


def _capture_arguments(func: Callable, args: tuple, kwargs: dict) -> dict[str, str]:
    """Capture function arguments as span attributes.

    Returns a dict of string key-value pairs suitable for span.set_attribute().
    Argument values are truncated to avoid bloating span data.
    """
    attrs: dict[str, str] = {}
    try:
        sig = inspect.signature(func)
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        for i, (param_name, value) in enumerate(bound.arguments.items()):
            if param_name == "self" or param_name == "cls":
                continue
            attrs[f"func.arg.{param_name}"] = _safe_str(value)
    except Exception:
        # If signature binding fails, capture positional args by index
        for i, arg in enumerate(args):
            attrs[f"func.arg.{i}"] = _safe_str(arg)
        for k, v in kwargs.items():
            attrs[f"func.kwarg.{k}"] = _safe_str(v)
    return attrs


def _make_span_name(func: Callable, custom_name: str | None) -> str:
    """Determine the span name for a decorated function."""
    if custom_name:
        return custom_name
    module = getattr(func, "__module__", "")
    qualname = getattr(func, "__qualname__", func.__name__)
    if module and module != "__main__":
        return f"{module}.{qualname}"
    return qualname


def _wrap_sync(
    func: Callable,
    span_name: str,
    capture_args: bool,
    capture_result: bool,
) -> Callable:
    """Create a synchronous wrapper that creates a span around the function."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # If SDK throws internally, the user's function MUST still run
        try:
            tracer = Tracer.get_tracer()
            span = tracer.start_span(span_name)
        except Exception:
            logger.debug("Failed to create span for %s", span_name, exc_info=True)
            return func(*args, **kwargs)

        try:
            # Set function metadata
            span.set_attribute("func.name", func.__name__)
            span.set_attribute("func.module", getattr(func, "__module__", "unknown"))

            # Capture arguments
            if capture_args:
                try:
                    for k, v in _capture_arguments(func, args, kwargs).items():
                        span.set_attribute(k, v)
                except Exception:
                    logger.debug("Failed to capture args for %s", span_name, exc_info=True)

            # Execute the actual function within the span context
            with span_context(span):
                result = func(*args, **kwargs)

            # Capture return value
            if capture_result:
                try:
                    span.set_attribute("func.result", _safe_str(result))
                except Exception:
                    pass

            span.set_status(SpanStatus.OK)
            span.end()
            return result

        except Exception as exc:
            # Record the exception but ALWAYS re-raise it
            try:
                span.record_exception(exc)
                span.end()
            except Exception:
                logger.debug("Failed to record exception in span", exc_info=True)
            raise

    return wrapper


def _wrap_async(
    func: Callable,
    span_name: str,
    capture_args: bool,
    capture_result: bool,
) -> Callable:
    """Create an asynchronous wrapper that creates a span around the function."""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        # If SDK throws internally, the user's function MUST still run
        try:
            tracer = Tracer.get_tracer()
            span = tracer.start_span(span_name)
        except Exception:
            logger.debug("Failed to create span for %s", span_name, exc_info=True)
            return await func(*args, **kwargs)

        try:
            # Set function metadata
            span.set_attribute("func.name", func.__name__)
            span.set_attribute("func.module", getattr(func, "__module__", "unknown"))

            # Capture arguments
            if capture_args:
                try:
                    for k, v in _capture_arguments(func, args, kwargs).items():
                        span.set_attribute(k, v)
                except Exception:
                    logger.debug("Failed to capture args for %s", span_name, exc_info=True)

            # Execute the actual function within the span context
            with span_context(span):
                result = await func(*args, **kwargs)

            # Capture return value
            if capture_result:
                try:
                    span.set_attribute("func.result", _safe_str(result))
                except Exception:
                    pass

            span.set_status(SpanStatus.OK)
            span.end()
            return result

        except Exception as exc:
            # Record the exception but ALWAYS re-raise it
            try:
                span.record_exception(exc)
                span.end()
            except Exception:
                logger.debug("Failed to record exception in span", exc_info=True)
            raise

    return wrapper


#  Public API


@overload
def observe(func: F) -> F: ...


@overload
def observe(
    func: None = None,
    *,
    name: str | None = None,
    capture_args: bool = True,
    capture_result: bool = True,
) -> Callable[[F], F]: ...


def observe(
    func: F | None = None,
    *,
    name: str | None = None,
    capture_args: bool = True,
    capture_result: bool = True,
) -> F | Callable[[F], F]:
    """Decorator to instrument a function for Oxly observability.

    Creates a Span for each function call, capturing arguments, return values,
    exceptions, and timing. Works on both sync and async functions.

    The decorator NEVER crashes the user's application. All internal SDK errors
    are caught and logged silently.

    Args:
        func: The function to decorate (auto-filled when used as @observe).
        name: Custom span name. Defaults to "module.qualname".
        capture_args: Whether to capture function arguments. Default True.
        capture_result: Whether to capture the return value. Default True.

    Returns:
        The decorated function with identical signature and behavior.

    Examples:
        @observe
        def my_function(x):
            return x * 2

        @observe(name="llm.chat", capture_args=False)
        async def call_llm(prompt: str) -> str:
            return await client.chat(prompt)
    """

    def decorator(fn: F) -> F:
        span_name = _make_span_name(fn, name)

        if inspect.iscoroutinefunction(fn):
            wrapped = _wrap_async(fn, span_name, capture_args, capture_result)
        else:
            wrapped = _wrap_sync(fn, span_name, capture_args, capture_result)

        return wrapped  # type: ignore[return-value]

    # Support both @observe and @observe(name="x") syntax
    if func is not None:
        return decorator(func)
    return decorator
