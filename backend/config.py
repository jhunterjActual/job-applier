import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Paths
BASE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = BASE_DIR.parent
DATA_DIR = WORKSPACE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "output"
DB_PATH = DATA_DIR / "jobapplier.db"

# Ensure folders exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    debug: bool = False
    posthog_project_token: str | None = None
    posthog_host: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Return cached settings for application dependencies and lifecycle hooks."""
    return Settings()


# API Keys
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")

def get_gemini_api_key() -> str:
    """
    Retrieve the Gemini API key from environment variables or global configuration.
    
    Returns:
        str: The Gemini API key, or empty string if not configured.
    """
    return os.environ.get("GEMINI_API_KEY") or GEMINI_API_KEY

def get_google_maps_api_key() -> str:
    """
    Retrieve the Google Maps/Places API key from environment variables or global configuration.
    
    Returns:
        str: The Google Maps API key, or empty string if not configured.
    """
    return os.environ.get("GOOGLE_MAPS_API_KEY") or GOOGLE_MAPS_API_KEY

