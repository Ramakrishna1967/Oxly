# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Pydantic request/response schemas matching SDK models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class SpanStatus(str, Enum):
    """Span status enum."""

    OK = "OK"
    ERROR = "ERROR"
    UNSET = "UNSET"


class SpanEventSchema(BaseModel):
    """Span event schema."""

    name: str
    timestamp: int
    attributes: dict[str, str] = Field(default_factory=dict)


class SpanSchema(BaseModel):
    """Span schema matching SDK SpanModel."""

    span_id: str
    trace_id: str
    parent_span_id: str | None = None
    name: str
    start_time: int
    end_time: int
    duration_ms: int
    status: SpanStatus = SpanStatus.OK
    service_name: str = "default"
    attributes: dict[str, str] = Field(default_factory=dict)
    events: list[SpanEventSchema] = Field(default_factory=list)
    project_id: str | None = None
    api_key_hash: str | None = None


class TraceSchema(BaseModel):
    """Trace schema with aggregated span data."""

    trace_id: str
    project_id: str
    start_time: int
    end_time: int | None = None
    duration_ms: int | None = None
    status: SpanStatus = SpanStatus.OK
    span_count: int = 0


class TraceDetailSchema(BaseModel):
    """Trace detail with full span tree."""

    trace_id: str
    project_id: str
    start_time: int
    end_time: int | None = None
    duration_ms: int | None = None
    status: SpanStatus
    spans: list[SpanSchema]


class SecurityAlertSeverity(str, Enum):
    """Security alert severity."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SecurityAlertSchema(BaseModel):
    """Security alert schema."""

    id: str
    trace_id: str
    span_id: str
    project_id: str
    severity: SecurityAlertSeverity
    alert_type: str
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ProjectSchema(BaseModel):
    """Project schema."""

    id: str
    name: str
    created_at: datetime
    updated_at: datetime


class ProjectCreateRequest(BaseModel):
    """Create project request."""

    name: str = Field(..., min_length=1, max_length=100)


class ProjectCreateResponse(BaseModel):
    """Create project response with API key (shown once)."""

    project: ProjectSchema
    api_key: str  # Shown once, then hashed and stored


class UserSchema(BaseModel):
    """User schema for dashboard auth."""

    id: str
    email: str
    is_active: bool
    created_at: datetime


class UserRegisterRequest(BaseModel):
    """User registration request."""

    email: str = Field(..., pattern=r"^[\w\.-]+@[\w\.-]+\.\w+$")
    password: str = Field(
        ..., min_length=12, description="Min 12 chars, must include upper, lower, digit"
    )

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        """Validate password complexity."""
        import re

        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"[a-z]", v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r"\d", v):
            raise ValueError("Password must contain at least one digit")
        return v


class UserLoginRequest(BaseModel):
    """User login request."""

    email: str
    password: str


class TokenResponse(BaseModel):
    """JWT token response."""

    access_token: str
    token_type: str = "bearer"


class PaginatedResponse(BaseModel):
    """Generic paginated response."""

    items: list[Any]
    total: int
    page: int
    page_size: int
    total_pages: int
