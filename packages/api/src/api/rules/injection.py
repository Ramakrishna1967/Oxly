# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Prompt injection detection rules with weighted scoring.

Uses a multi-tier pattern system:
- Tier 1 (HIGH confidence): Direct instruction override patterns
- Tier 2 (MEDIUM confidence): Indirect manipulation patterns
- Tier 3 (LOW confidence): Suspicious but potentially legitimate patterns

Each tier has different weights to reduce false positives while
maintaining high detection rates for genuine attacks.
"""

import re

# Tier 1: High-confidence injection patterns (direct instruction override)
# These are almost always malicious when found in LLM I/O
TIER1_PATTERNS = [
    (
        re.compile(
            r"ignore\s+(all\s+)?previous\s+(instructions|prompts|rules|directives)", re.IGNORECASE
        ),
        50.0,
    ),
    (
        re.compile(
            r"disregard\s+(all\s+)?(previous|above|prior)\s+(instructions|prompts|rules)",
            re.IGNORECASE,
        ),
        50.0,
    ),
    (
        re.compile(
            r"forget\s+(all\s+)?(previous|above|prior)\s+(instructions|prompts|rules)",
            re.IGNORECASE,
        ),
        45.0,
    ),
    (
        re.compile(
            r"you\s+are\s+now\s+(?:a|an)\s+(?:DAN|jailbroken|unrestricted|uncensored)",
            re.IGNORECASE,
        ),
        55.0,
    ),
    (re.compile(r"(?:DAN|jailbreak)\s+mode\s+(?:enabled|activated|on)", re.IGNORECASE), 55.0),
    (re.compile(r"system\s*:\s*override", re.IGNORECASE), 60.0),
    (re.compile(r"new\s+instructions?\s*:", re.IGNORECASE), 40.0),
    (
        re.compile(
            r"override\s+(safety|content|security)\s+(policy|filter|guidelines)", re.IGNORECASE
        ),
        55.0,
    ),
]

# Tier 2: Medium-confidence patterns (indirect manipulation)
# Could be legitimate in some contexts but suspicious in LLM I/O
TIER2_PATTERNS = [
    (re.compile(r"pretend\s+(you\s+are|to\s+be)\s+(?:a|an)\s+(?!human)\w+", re.IGNORECASE), 25.0),
    (re.compile(r"roleplay\s+as\s+(?:a|an)\s+(?!human|user|customer)\w+", re.IGNORECASE), 25.0),
    (
        re.compile(
            r"act\s+as\s+if\s+you\s+(?:are|have)\s+no\s+(?:restrictions|limits|rules)",
            re.IGNORECASE,
        ),
        35.0,
    ),
    (
        re.compile(
            r"do\s+not\s+(?:follow|adhere\s+to|comply\s+with)\s+(?:your|the)\s+(?:rules|guidelines|policy)",
            re.IGNORECASE,
        ),
        35.0,
    ),
    (
        re.compile(
            r"bypass\s+(?:the\s+)?(?:safety|content|security)\s+(?:filter|policy|check)",
            re.IGNORECASE,
        ),
        40.0,
    ),
    (
        re.compile(r"reveal\s+(?:your|the)\s+(?:system|initial|original)\s+prompt", re.IGNORECASE),
        35.0,
    ),
    (
        re.compile(
            r"what\s+(?:are|is)\s+your\s+(?:system|initial|original)\s+(?:prompt|instructions)",
            re.IGNORECASE,
        ),
        30.0,
    ),
]

# Tier 3: Low-confidence patterns (suspicious but common in legitimate use)
# Only contributes to score if combined with other patterns
TIER3_PATTERNS = [
    (re.compile(r"you\s+are\s+not\s+a\s+(?:helper|assistant|AI|chatbot)", re.IGNORECASE), 15.0),
    (re.compile(r"(?:developer|admin|root|system)\s+mode", re.IGNORECASE), 20.0),
    (re.compile(r"unrestricted\s+(?:mode|access|output)", re.IGNORECASE), 20.0),
    (
        re.compile(
            r"output\s+(?:the\s+)?(?:raw|unfiltered|uncensored)\s+(?:response|text|content)",
            re.IGNORECASE,
        ),
        20.0,
    ),
]


def check_injection(text: str) -> float:
    """Check text for prompt injection patterns using weighted multi-tier scoring.

    Returns:
        Threat score (0.0 to 100.0)

    Scoring logic:
        - Tier 1 matches add high weight (40-60 each)
        - Tier 2 matches add medium weight (25-40 each)
        - Tier 3 matches add low weight (15-20 each)
        - Multiple matches across tiers increase confidence
        - Score is capped at 100.0
    """
    if not text:
        return 0.0

    score = 0.0
    tier1_hits = 0
    tier2_hits = 0
    tier3_hits = 0

    # Check Tier 1 (highest confidence)
    for pattern, weight in TIER1_PATTERNS:
        if pattern.search(text):
            score += weight
            tier1_hits += 1

    # Check Tier 2 (medium confidence)
    for pattern, weight in TIER2_PATTERNS:
        if pattern.search(text):
            score += weight
            tier2_hits += 1

    # Check Tier 3 (low confidence  only adds weight if other tiers matched)
    if tier1_hits > 0 or tier2_hits > 0:
        for pattern, weight in TIER3_PATTERNS:
            if pattern.search(text):
                score += weight
                tier3_hits += 1

    # Multi-hit amplification: if multiple patterns match across tiers,
    # it's more likely a genuine attack
    total_hits = tier1_hits + tier2_hits + tier3_hits
    if total_hits >= 3:
        score *= 1.2  # 20% boost for multi-pattern matches
    if tier1_hits >= 2:
        score *= 1.3  # 30% boost for multiple high-confidence matches

    return min(score, 100.0)
