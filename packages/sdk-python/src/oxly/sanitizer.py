# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""PII Sanitizer  regex-based scrubber that runs on all span attributes before export.

Detects and replaces sensitive data with [REDACTED] tokens. This runs on the hot
path (every span), so all patterns are pre-compiled and the implementation is
optimized for speed (< 1ms per span).

Detected PII types:
    - Social Security Numbers (SSN): 123-45-6789
    - Email addresses: user@example.com
    - Credit card numbers: 4111-1111-1111-1111 or 4111111111111111
    - Phone numbers: +1-555-123-4567, (555) 123-4567, 555.123.4567
    - AWS access keys: AKIA...
    - AWS secret keys: 40-character base64 strings near AWS context
    - OpenAI API keys: sk-...
    - Generic API keys/tokens in common formats

Usage:
    from oxly.sanitizer import scrub_pii

    clean = scrub_pii({"prompt": "My SSN is 123-45-6789"})
    # {"prompt": "My SSN is [REDACTED_SSN]"}
"""

from __future__ import annotations

import re
from typing import Any

#  Redaction Tokens

REDACTED_SSN = "[REDACTED_SSN]"
REDACTED_EMAIL = "[REDACTED_EMAIL]"
REDACTED_CC = "[REDACTED_CC]"
REDACTED_PHONE = "[REDACTED_PHONE]"
REDACTED_AWS_KEY = "[REDACTED_AWS_KEY]"
REDACTED_OPENAI_KEY = "[REDACTED_OPENAI_KEY]"
REDACTED_API_KEY = "[REDACTED_API_KEY]"

#  Compiled Regex Patterns
# All patterns are compiled once at module import for maximum performance.

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # SSN: 123-45-6789 or 123 45 6789
    (
        re.compile(r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b"),
        REDACTED_SSN,
    ),
    # Credit Card: 16 digits with optional separators (-, space)
    # Matches: 4111-1111-1111-1111, 4111 1111 1111 1111, 4111111111111111
    (
        re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b"),
        REDACTED_CC,
    ),
    # OpenAI API keys: sk-... (48+ chars)
    (
        re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
        REDACTED_OPENAI_KEY,
    ),
    # AWS Access Key IDs: AKIA followed by 16 alphanumeric chars
    (
        re.compile(r"\b(?:AKIA|AIDA|AROA|ABIA|ACCA)[A-Z0-9]{16}\b"),
        REDACTED_AWS_KEY,
    ),
    # AWS Secret Access Keys: 40-char base64 strings near AWS context
    (
        re.compile(
            r"(?:aws[_-]?secret[_-]?access[_-]?key|secret[_-]?key|aws[_-]?secret)"
            r"[\s]*[=:][\s]*['\"]?([A-Za-z0-9/+=]{40})['\"]?",
            re.IGNORECASE,
        ),
        REDACTED_AWS_KEY,
    ),
    # Email addresses
    (
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        REDACTED_EMAIL,
    ),
    # Phone numbers: Require separators to reduce false positives
    (
        re.compile(
            r"(?:^|\s|\b)(\+?\d{1,3}[-.\s]?)?"
            r"(\(\d{3}\)|\d{3})"
            r"[-.\s]\d{3}"  # Require separators here
            r"[-.\s]\d{4}\b"
        ),
        REDACTED_PHONE,
    ),
    # Generic API key patterns: key=..., token=..., api_key=...
    (
        re.compile(
            r"(?:api[_-]?key|api[_-]?secret|access[_-]?token|auth[_-]?token|bearer)"
            r"[\s]*[=:]\s*['\"]?([A-Za-z0-9_\-./+=]{16,})['\"]?",
            re.IGNORECASE,
        ),
        REDACTED_API_KEY,
    ),
]


def _scrub_string(value: str) -> str:
    """Apply all PII patterns to a single string value.

    Args:
        value: The string to scan for PII.

    Returns:
        The string with all detected PII replaced by redaction tokens.
    """
    result = value
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def scrub_pii(attributes: dict[str, str]) -> dict[str, str]:
    """Scrub PII from a dictionary of span attributes.

    Scans each string value for PII patterns and replaces matches
    with typed [REDACTED_*] tokens. Non-string values and keys are
    left untouched. Returns a NEW dictionary  the input is not modified.

    Args:
        attributes: Span attributes dict (key  string value).

    Returns:
        A new dict with PII replaced by redaction tokens.

    Performance:
        Designed for hot-path use (< 1ms per span with typical attributes).
    """
    if not attributes:
        return {}

    result: dict[str, str] = {}
    for key, value in attributes.items():
        if isinstance(value, str) and value:
            result[key] = _scrub_string(value)
        else:
            result[key] = value
    return result


def scrub_value(value: Any) -> Any:
    """Scrub PII from a single value.

    Handles strings, lists, dicts, and nested structures recursively.
    Non-string leaf values are returned unchanged.
    """
    if isinstance(value, str):
        return _scrub_string(value)
    if isinstance(value, dict):
        return {k: scrub_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_value(item) for item in value]
    return value
