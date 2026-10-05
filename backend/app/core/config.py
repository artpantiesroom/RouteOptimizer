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
    FRONTEND_ORIGIN: str = "http://localhost:5173"
    CORS_ALLOWED_ORIGINS: str = "http://localhost:5173"

    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]


def get_settings() -> Settings:
    settings = Settings()
    if not settings.NOMINATIM_USER_AGENT or settings.NOMINATIM_USER_AGENT.strip() == "":
        raise RuntimeError("NOMINATIM_USER_AGENT is not configured. Set a meaningful user agent in environment.")
    return settings
