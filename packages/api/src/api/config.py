# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Configuration module using pydantic-settings."""

import sys

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    # Security
    JWT_SECRET_KEY: str = ""
    API_SALT: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 1 day (reduced from 7)

    # Database
    DATABASE_URL: str = "oxly.db"

    # Environment
    ENVIRONMENT: str = "development"
    DEMO_MODE: bool = False  # SECURITY: Must be explicitly enabled for demo mode

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()

#  Security gate: refuse to start without a real secret key
_INSECURE_DEFAULTS = {"", "your-secret-key-change-in-production", "changeme"}
if settings.JWT_SECRET_KEY in _INSECURE_DEFAULTS:
    if settings.ENVIRONMENT != "development":
        print(
            "FATAL: JWT_SECRET_KEY is not set or uses an insecure default.\n"
            "       Set the JWT_SECRET_KEY environment variable before starting in production.",
            file=sys.stderr,
        )
        sys.exit(1)
    else:
        # Allow development mode with a generated key, but warn
        import secrets
        settings.JWT_SECRET_KEY = secrets.token_hex(32)
        print("WARNING: Using auto-generated JWT secret (development mode only).", file=sys.stderr)
