"""Environment-based configuration.

Reads from .env if present; all values are optional so the app runs in
sample-data mode even without any credentials.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
USER_DIR = DATA_DIR / "user"


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


@dataclass(frozen=True)
class Config:
    anthropic_api_key: str | None
    openai_api_key: str | None
    openrouter_api_key: str | None
    strava_client_id: str | None
    strava_client_secret: str | None
    strava_refresh_token: str | None
    llm_provider: str
    llm_model: str
    llm_temperature: float

    @property
    def has_anthropic(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def has_openrouter(self) -> bool:
        return bool(self.openrouter_api_key)

    @property
    def has_strava(self) -> bool:
        return bool(
            self.strava_client_id
            and self.strava_client_secret
            and self.strava_refresh_token
        )

    @property
    def has_llm(self) -> bool:
        if self.llm_provider == "anthropic":
            return self.has_anthropic
        if self.llm_provider == "openai":
            return self.has_openai
        if self.llm_provider == "openrouter":
            return self.has_openrouter
        return False


_DEFAULT_MODEL_BY_PROVIDER = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o-2024-08-06",
    # OpenRouter proxies many models — default to Claude via Anthropic's route.
    # Full catalog: https://openrouter.ai/models
    "openrouter": "anthropic/claude-sonnet-4.6",
}


def load_config() -> Config:
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    default_model = _DEFAULT_MODEL_BY_PROVIDER.get(provider, "claude-sonnet-4-6")
    return Config(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
        strava_client_id=os.getenv("STRAVA_CLIENT_ID") or None,
        strava_client_secret=os.getenv("STRAVA_CLIENT_SECRET") or None,
        strava_refresh_token=os.getenv("STRAVA_REFRESH_TOKEN") or None,
        llm_provider=provider,
        llm_model=os.getenv("LLM_MODEL", default_model),
        llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0")),
    )
