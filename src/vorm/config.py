"""Environment-based configuration.

Resolution order for every key:
1. Process environment / `.env` (via python-dotenv) — local-dev path.
2. `streamlit.secrets` — populated from Streamlit Community Cloud's
   *App settings → Secrets* TOML editor.

All values are optional so the app runs in sample-data mode even without
any credentials.
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


def _is_source_checkout() -> bool:
    """True when this module is imported from a checked-out repo (editable
    install or `streamlit run` against the source tree). False when imported
    from a wheel installed into site-packages — in that case PROJECT_ROOT
    points inside the venv and is read-only on hosts like Streamlit Cloud.
    """
    return (PROJECT_ROOT / "pyproject.toml").is_file()


if _is_source_checkout():
    DATA_DIR = PROJECT_ROOT / "data"
else:
    # Streamlit Cloud / any installed-package deployment. `~` resolves to a
    # writable per-user directory (`/home/adminuser` on Streamlit Cloud).
    DATA_DIR = Path.home() / ".vorm"

CACHE_DIR = DATA_DIR / "cache"
USER_DIR = DATA_DIR / "user"


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _from_streamlit_secrets(key: str) -> str | None:
    """Look up `key` in `st.secrets` if Streamlit is importable AND a
    `secrets.toml` is configured. Returns None on any failure so CLI use
    (`scripts/validate.py`) doesn't crash when Streamlit isn't running."""
    try:
        import streamlit as st  # noqa: PLC0415 — defer import; CLI path may not have streamlit installed
    except ImportError:
        return None
    try:
        secrets = st.secrets
    except Exception:
        # streamlit raises StreamlitSecretNotFoundError when no secrets.toml exists.
        return None
    try:
        value = secrets[key]
    except (KeyError, FileNotFoundError):
        return None
    return str(value) if value else None


def _get(key: str, default: str | None = None) -> str | None:
    """Env-first, Streamlit-secrets-fallback lookup."""
    value = os.getenv(key)
    if value:
        return value
    secret = _from_streamlit_secrets(key)
    if secret:
        return secret
    return default


@dataclass(frozen=True)
class Config:
    anthropic_api_key: str | None
    openai_api_key: str | None
    openrouter_api_key: str | None
    google_api_key: str | None
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
    def has_google(self) -> bool:
        return bool(self.google_api_key)

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
        if self.llm_provider == "google":
            return self.has_google
        return False


_DEFAULT_MODEL_BY_PROVIDER = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o-2024-08-06",
    # OpenRouter proxies many models — default to Claude via Anthropic's route.
    # Full catalog: https://openrouter.ai/models
    "openrouter": "anthropic/claude-sonnet-4.6",
    # Google AI Studio: free tier 15 RPM, 1500 RPD on Flash-Lite models.
    # Get key from https://aistudio.google.com/app/apikey
    "google": "gemini-3.1-flash-lite",
}


def load_config() -> Config:
    provider = (_get("LLM_PROVIDER") or "anthropic").lower()
    default_model = _DEFAULT_MODEL_BY_PROVIDER.get(provider, "claude-sonnet-4-6")
    # Accept either GOOGLE_API_KEY (AI Studio's canonical name) or GEMINI_API_KEY
    # (the genai SDK's auto-pickup name) — both are common in the wild.
    google_key = _get("GOOGLE_API_KEY") or _get("GEMINI_API_KEY")
    return Config(
        anthropic_api_key=_get("ANTHROPIC_API_KEY") or None,
        openai_api_key=_get("OPENAI_API_KEY") or None,
        openrouter_api_key=_get("OPENROUTER_API_KEY") or None,
        google_api_key=google_key or None,
        strava_client_id=_get("STRAVA_CLIENT_ID") or None,
        strava_client_secret=_get("STRAVA_CLIENT_SECRET") or None,
        strava_refresh_token=_get("STRAVA_REFRESH_TOKEN") or None,
        llm_provider=provider,
        llm_model=_get("LLM_MODEL", default_model),
        llm_temperature=float(_get("LLM_TEMPERATURE", "0") or "0"),
    )
