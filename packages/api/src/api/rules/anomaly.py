# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Anomaly detection rules for the Security Engine.

Detects operational anomalies in span data:
- Excessive duration (stuck/hung calls)
- Token explosion (cost anomalies)
- Error patterns (repeated failures)
- Empty outputs (silent failures)
- Unusual model parameters
"""

from typing import Any


def _safe_int(value: Any, default: int = 0) -> int:
    """Safely convert a value to int."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def check_anomaly(span: dict) -> list[str]:
    """Check span for anomalies.

    Returns:
        List of anomaly descriptions.
    """
    anomalies = []
    attrs = span.get("attributes", {}) or {}
    status = span.get("status", "UNSET")

    # 1. Excessive duration (> 5 minutes is suspicious for most LLM calls)
    duration = span.get("duration_ms", 0) or 0
    if duration > 300_000:  # 5 minutes
        anomalies.append(f"Excessive duration: {duration}ms")
    elif duration > 60_000:  # 1 minute  note but don't alert
        pass  # Could log at debug level if needed

    # 2. Token explosion (cost anomaly)
    total_tokens = _safe_int(attrs.get("llm.usage.total_tokens", 0))
    if total_tokens == 0:
        # Try alternate attribute names
        prompt_tokens = _safe_int(
            attrs.get("llm.usage.prompt_tokens", attrs.get("llm.tokens.in", 0))
        )
        completion_tokens = _safe_int(
            attrs.get("llm.usage.completion_tokens", attrs.get("llm.tokens.out", 0))
        )
        total_tokens = prompt_tokens + completion_tokens

    if total_tokens > 128_000:
        anomalies.append(f"Extreme token usage: {total_tokens}")
    elif total_tokens > 32_000:
        anomalies.append(f"High token usage: {total_tokens}")

    # 3. Error status with no exception recorded
    if status == "ERROR":
        events = span.get("events", [])
        has_exception = any(isinstance(e, dict) and e.get("name") == "exception" for e in events)
        if not has_exception:
            anomalies.append("Error status without exception event recorded")

    # 4. Empty output for LLM calls (silent failure)
    model = attrs.get("llm.model", attrs.get("model", ""))
    if model:
        output = attrs.get("llm.completions.0.content", attrs.get("output_payload", ""))
        if (output == "" or output is None) and status != "ERROR":
            # Only flag if not already an error
            anomalies.append("LLM call returned empty output without error status")

    # 5. Unusually high temperature (potential misuse)
    temperature = attrs.get("llm.temperature")
    if temperature is not None:
        try:
            temp_val = float(temperature)
            if temp_val > 2.0:
                anomalies.append(f"Unusually high temperature: {temp_val}")
        except (ValueError, TypeError):
            pass

    # 6. Negative duration (data integrity issue)
    if duration < 0:
        anomalies.append(f"Negative duration detected: {duration}ms")

    return anomalies
