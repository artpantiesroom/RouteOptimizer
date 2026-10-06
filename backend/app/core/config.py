from __future__ import annotations

import os
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    NOMINATIM_BASE_URL: str = "https://nominatim.openstreetmap.org/search"
    NOMINATIM_USER_AGENT: str = ""
    NOMINATIM_COUNTRY_CODES: Optional[str] = None
    NOMINATIM_TIMEOUT_SECONDS: float = 10.0
    NOMINATIM_RATE_LIMIT_DELAY_SECONDS: float = 1.0
    OSRM_BASE_URL: str = "https://router.project-osrm.org"
    OSRM_TIMEOUT_SECONDS: float = 10.0
    OSRM_RATE_LIMIT_DELAY_SECONDS: float = 1.0
    MATRIX_MAX_POINTS: int = 50
    FRONTEND_ORIGIN: str = "http://localhost:5173"
    CORS_ALLOWED_ORIGINS: str = "http://localhost:5173"

    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]


# Identifiers that are clearly template values. Both public Nominatim and public
# OSRM reject (or may reject) requests that identify themselves this way.
_PLACEHOLDER_UA_MARKERS = (
    "example.com",
    "example.org",
    "example.net",
    "your-email",
    "your_email",
    "user@example",
    "@example.com",
    "changeme",
)


def _user_agent_is_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_UA_MARKERS)


def get_settings() -> Settings:
    settings = Settings()
    ua = settings.NOMINATIM_USER_AGENT or ""
    if not ua.strip():
        raise RuntimeError("NOMINATIM_USER_AGENT is not configured. Set a meaningful user agent in environment.")
    if _user_agent_is_placeholder(ua):
        raise RuntimeError(
            "NOMINATIM_USER_AGENT looks like a template value (e.g. example.com). "
            "Set a real identifying user agent in environment."
        )
    return settings
