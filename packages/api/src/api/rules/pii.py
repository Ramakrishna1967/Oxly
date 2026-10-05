# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""PII detection rules for the Security Engine.

Uses regex patterns with validation functions to reduce false positives.
Credit card detection includes Luhn algorithm validation.
"""

import re
from collections.abc import Callable


def _luhn_check(number: str) -> bool:
    """Validate a number using the Luhn algorithm.

    Returns True if the number passes the Luhn check (valid card number).
    """
    digits = [int(d) for d in number if d.isdigit()]
    if len(digits) < 2:
        return False
    checksum = 0
    reverse_digits = digits[::-1]
    for i, d in enumerate(reverse_digits):
        if i % 2 == 1:  # Odd positions from right (0-indexed = even from left)
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def _validate_credit_card(match: re.Match) -> bool:
    """Validate a credit card match using Luhn algorithm and known IIN ranges."""
    digits = match.group().replace("-", "").replace(" ", "")
    if len(digits) < 13 or len(digits) > 19:
        return False
    # Check IIN (Issuer Identification Number) - first 1-2 digits
    first_two = int(digits[:2])
    known_ranges = {
        # Visa
        range(40, 50),
        # Mastercard
        range(51, 56),
        range(2221, 2721),
        # Amex
        range(34, 38),
        # Discover
        range(60, 66),
        range(622126, 622926),
        # JCB
        range(3528, 3589),
    }
    # Check if first digits fall in known ranges
    first_six = int(digits[:6]) if len(digits) >= 6 else 0
    first_four = int(digits[:4]) if len(digits) >= 4 else 0

    in_range = any(first_two in r or first_four in r or first_six in r for r in known_ranges)
    if not in_range:
        return False

    return _luhn_check(digits)


# PII Patterns with optional validation functions
# Validation functions reduce false positives by checking additional constraints
PATTERNS: dict[str, tuple[re.Pattern, Callable[[re.Match], bool] | None]] = {
    "EMAIL": (
        re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
        None,  # Email regex is specific enough
    ),
    "SSN": (
        re.compile(r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"),
        None,  # Excludes invalid SSN ranges (000, 666, 900-999)
    ),
    "CREDIT_CARD": (
        re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
        _validate_credit_card,  # Luhn + IIN validation
    ),
    "AWS_KEY": (
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        None,  # AWS access key format is very specific
    ),
    "OPENAI_KEY": (
        re.compile(r"\bsk-[a-zA-Z0-9]{20}T3BlbkFJ[a-zA-Z0-9]{24}\b"),
        None,
    ),
    "OPENAI_KEY_V2": (
        re.compile(r"\bsk-(?:proj|svcacct)-[A-Za-z0-9_-]{40,}\b"),
        None,  # Current OpenAI project/service-account key format
    ),
    "ANTHROPIC_KEY": (
        re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
        None,  # Anthropic API key (sk-ant-api03-...)
    ),
    "HUGGINGFACE_TOKEN": (
        re.compile(r"\bhf_[A-Za-z0-9]{34}\b"),
        None,  # HuggingFace access token
    ),
    "GCP_KEY": (
        re.compile(r"\bAIzaSy[a-zA-Z0-9_-]{33}\b"),
        None,  # Google Cloud API key format
    ),
    "PRIVATE_KEY": (
        re.compile(r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----"),
        None,  # Private key header
    ),
}


def check_pii(text: str) -> list[str]:
    """Check text for PII with validation to reduce false positives.

    Returns:
        List of detected PII types.
    """
    if not text:
        return []

    detected = []
    for name, (pattern, validator) in PATTERNS.items():
        for match in pattern.finditer(text):
            if validator is None:
                # No validator  pattern is specific enough
                detected.append(name)
                break
            elif validator(match):
                # Validator confirmed the match
                detected.append(name)
                break
            # Validator rejected  continue searching for other matches

    return detected
