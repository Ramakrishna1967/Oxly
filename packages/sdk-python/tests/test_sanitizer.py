# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Tests for PII sanitizer."""

from oxly.sanitizer import (
    REDACTED_AWS_KEY,
    REDACTED_CC,
    REDACTED_EMAIL,
    REDACTED_OPENAI_KEY,
    REDACTED_PHONE,
    REDACTED_SSN,
    scrub_pii,
    scrub_value,
)


def test_scrub_ssn():
    """Test SSN detection and redaction."""
    attrs = {"prompt": "My SSN is 123-45-6789"}
    result = scrub_pii(attrs)
    assert REDACTED_SSN in result["prompt"]
    assert "123-45-6789" not in result["prompt"]


def test_scrub_email():
    """Test email detection and redaction."""
    attrs = {"contact": "Email me at john.doe@example.com for more"}
    result = scrub_pii(attrs)
    assert REDACTED_EMAIL in result["contact"]
    assert "john.doe@example.com" not in result["contact"]


def test_scrub_credit_card():
    """Test credit card detection and redaction."""
    attrs = {"payment": "Card: 4111-1111-1111-1111"}
    result = scrub_pii(attrs)
    assert REDACTED_CC in result["payment"]
    assert "4111-1111-1111-1111" not in result["payment"]


def test_scrub_phone():
    """Test phone number detection and redaction."""
    test_cases = [
        ("Call (555) 123-4567", REDACTED_PHONE),
        ("Phone: 555-123-4567", REDACTED_PHONE),
        ("Contact: +1-555-123-4567", REDACTED_PHONE),
    ]
    for text, expected_token in test_cases:
        result = scrub_pii({"text": text})
        assert expected_token in result["text"]


def test_scrub_aws_key():
    """Test AWS key detection and redaction."""
    attrs = {"key": "AWS Key: AKIAIOSFODNN7EXAMPLE"}
    result = scrub_pii(attrs)
    assert REDACTED_AWS_KEY in result["key"]
    assert "AKIAIOSFODNN7EXAMPLE" not in result["key"]


def test_scrub_openai_key():
    """Test OpenAI API key detection and redaction."""
    attrs = {"token": "Token: sk-abc123def456ghi789jklmno"}
    result = scrub_pii(attrs)
    assert REDACTED_OPENAI_KEY in result["token"]
    assert "sk-abc123def456ghi789jklmno" not in result["token"]


def test_scrub_multiple_pii_types():
    """Test multiple PII types in one string."""
    attrs = {"data": "SSN: 123-45-6789, Email: test@example.com, Card: 4111111111111111"}
    result = scrub_pii(attrs)
    assert REDACTED_SSN in result["data"]
    assert REDACTED_EMAIL in result["data"]
    assert REDACTED_CC in result["data"]


def test_scrub_empty_dict():
    """Test scrubbing an empty dict."""
    result = scrub_pii({})
    assert result == {}


def test_scrub_non_pii_passthrough():
    """Test that non-PII content passes through unchanged."""
    attrs = {"safe": "hello world", "number": "42"}
    result = scrub_pii(attrs)
    assert result["safe"] == "hello world"
    assert result["number"] == "42"


def test_scrub_value_nested():
    """Test scrub_value on nested structures."""
    data = {
        "user": {
            "name": "John",
            "ssn": "123-45-6789",
            "contacts": [
                {"email": "john@example.com"},
                {"phone": "555-123-4567"},
            ],
        }
    }
    result = scrub_value(data)
    assert REDACTED_SSN in result["user"]["ssn"]
    assert REDACTED_EMAIL in result["user"]["contacts"][0]["email"]
    assert REDACTED_PHONE in result["user"]["contacts"][1]["phone"]
    assert result["user"]["name"] == "John"


def test_scrub_value_string():
    """Test scrub_value on a plain string."""
    result = scrub_value("My SSN is 123-45-6789")
    assert REDACTED_SSN in result
    assert "123-45-6789" not in result


def test_scrub_value_list():
    """Test scrub_value on a list."""
    result = scrub_value(
        [
            "Email: test@example.com",
            "Phone: 555-123-4567",
            "Safe text",
        ]
    )
    assert REDACTED_EMAIL in result[0]
    assert REDACTED_PHONE in result[1]
    assert result[2] == "Safe text"
