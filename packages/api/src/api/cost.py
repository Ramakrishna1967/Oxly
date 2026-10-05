# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""LLM cost calculation — ported from workers/cost_calculator.py's calculate_cost().

Pure pricing lookup + arithmetic, no Redis/ClickHouse coupling.
"""

from __future__ import annotations

# Simple Pricing Catalog (USD per 1K tokens)
# In production, this should be fetched from an API or DB
PRICING = {
    # OpenAI
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4o": {"input": 0.005, "output": 0.015},
    "gpt-4-turbo": {"input": 0.01, "output": 0.03},
    "gpt-4": {"input": 0.03, "output": 0.06},
    "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
    "text-embedding-3-small": {"input": 0.00002, "output": 0.0},
    "text-embedding-3-large": {"input": 0.00013, "output": 0.0},
    # Anthropic
    "claude-opus-4": {"input": 0.015, "output": 0.075},
    "claude-sonnet-4": {"input": 0.003, "output": 0.015},
    "claude-haiku-4": {"input": 0.0008, "output": 0.004},
    "claude-3-5-sonnet": {"input": 0.003, "output": 0.015},
    "claude-3-5-haiku": {"input": 0.0008, "output": 0.004},
    "claude-3-opus": {"input": 0.015, "output": 0.075},
    "claude-3-sonnet": {"input": 0.003, "output": 0.015},
    "claude-3-haiku": {"input": 0.00025, "output": 0.00125},
    # Google
    "gemini-2.0-flash": {"input": 0.0001, "output": 0.0004},
    "gemini-1.5-pro": {"input": 0.00125, "output": 0.005},
    "gemini-1.5-flash": {"input": 0.000075, "output": 0.0003},
}


def calculate_cost(span: dict) -> dict | None:
    """Extract usage and calculate cost for an LLM span.

    Returns a cost_metrics row dict, or None if the span isn't a priced
    LLM call (no model, zero tokens, or unknown model).
    """
    attrs = span.get("attributes", {})

    # Check if span is an LLM call
    # We look for model name or usage stats
    model = attrs.get("llm.model", attrs.get("model", "")).lower()
    if not model:
        return None  # Skip non-LLM spans

    # Extract tokens — support both OpenTelemetry-style (llm.usage.*) and SDK-style (llm.tokens.*)
    prompt_tokens = int(attrs.get("llm.usage.prompt_tokens", attrs.get("llm.tokens.in", 0)))
    completion_tokens = int(
        attrs.get("llm.usage.completion_tokens", attrs.get("llm.tokens.out", 0))
    )
    total_tokens = int(attrs.get("llm.usage.total_tokens", prompt_tokens + completion_tokens))

    if total_tokens == 0:
        return None

    # Find price
    # Normalize model name (e.g. gpt-4-0613 -> gpt-4)
    price_info = None
    for key in PRICING:
        if key in model:
            price_info = PRICING[key]
            break

    if not price_info:
        return None

    # Calculate Cost
    input_cost = (prompt_tokens / 1000) * price_info["input"]
    output_cost = (completion_tokens / 1000) * price_info["output"]
    total_cost = input_cost + output_cost

    return {
        "project_id": span.get("project_id", "unknown"),
        "model": model,
        "span_kind": "llm",
        "timestamp": span.get("start_time", 0) // 1_000_000_000,  # Convert ns to seconds
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cost_usd": total_cost,
    }
