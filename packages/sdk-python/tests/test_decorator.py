# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Tests for the @observe decorator."""

import asyncio

import pytest

from oxly.decorator import observe


def test_observe_sync_function():
    """Test @observe on a synchronous function."""

    @observe
    def add(x, y):
        return x + y

    result = add(3, 4)
    assert result == 7


def test_observe_custom_name():
    """Test @observe with custom span name."""

    @observe(name="custom.operation")
    def multiply(x, y):
        return x * y

    result = multiply(5, 6)
    assert result == 30


def test_observe_async_function():
    """Test @observe on an async function."""

    @observe
    async def async_add(x, y):
        await asyncio.sleep(0.01)
        return x + y

    result = asyncio.run(async_add(10, 20))
    assert result == 30


def test_observe_exception_reraised():
    """Test that exceptions are re-raised after recording."""

    @observe
    def fail():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        fail()


def test_observe_async_exception_reraised():
    """Test that async exceptions are re-raised."""

    @observe
    async def async_fail():
        raise RuntimeError("async boom")

    with pytest.raises(RuntimeError, match="async boom"):
        asyncio.run(async_fail())


def test_observe_capture_args_false():
    """Test capture_args=False option."""

    @observe(capture_args=False)
    def secret_func(password):
        return "ok"

    result = secret_func("my_secret")
    assert result == "ok"


def test_observe_capture_result_false():
    """Test capture_result=False option."""

    @observe(capture_result=False)
    def compute():
        return 42

    result = compute()
    assert result == 42


def test_observe_nested_calls():
    """Test that nested @observe calls create parent-child spans."""

    @observe(name="outer")
    def outer():
        return inner()

    @observe(name="inner")
    def inner():
        return 123

    result = outer()
    assert result == 123


def test_observe_with_return_value():
    """Test function return value is preserved."""

    @observe
    def get_data():
        return {"key": "value"}

    result = get_data()
    assert result == {"key": "value"}
    assert isinstance(result, dict)


def test_observe_with_multiple_args():
    """Test capturing multiple arguments."""

    @observe
    def multi_arg(a, b, c=10, d="test"):
        return a + b + c

    result = multi_arg(1, 2, c=3)
    assert result == 6
