"""
app/config.py — Centralised settings via pydantic-settings.

All values are loaded from environment variables (or a .env file).
Import `settings` singleton anywhere in the app:

    from app.config import settings
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Meta / Instagram ────────────────────────────────────────────────────
    ig_verify_token: str = Field(..., description="Webhook verify token set in Meta App Dashboard")
    ig_page_access_token: str = Field(..., description="Long-lived Instagram Page Access Token")
    ig_business_account_id: str = Field(..., description="Numeric Instagram Business Account ID")

    # ── Google Gemini ────────────────────────────────────────────────────────
    gemini_api_key: str = Field(..., description="Google AI Studio API key")
    gemini_model: str = Field(default="gemini-2.5-flash", description="Gemini model name")
    gemini_embedding_model: str = Field(
        default="models/text-embedding-004",
        description="Gemini embedding model (768-dim output)",
    )

    # ── LLM Fallbacks ────────────────────────────────────────────────────────
    groq_api_key: str = Field(..., description="Groq API key")
    groq_model: str = Field(default="llama-3.3-70b-versatile", description="Groq model name")
    openrouter_api_key: str = Field(..., description="OpenRouter API key")
    openrouter_model: str = Field(
        default="meta-llama/llama-3.1-8b-instruct:free",
        description="OpenRouter free model",
    )

    # ── Scraper Waterfall ────────────────────────────────────────────────────
    jina_api_key: str = Field(..., description="Jina Reader API key")
    tavily_api_key: str = Field(..., description="Tavily Search API key")
    serper_api_key: str = Field(..., description="Serper.dev API key")
    firecrawl_api_key: str = Field(..., description="Firecrawl API key")

    # ── Database & Cache ─────────────────────────────────────────────────────
    supabase_url: str = Field(..., description="Supabase project URL")
    supabase_key: str = Field(..., description="Supabase anon or service_role key")
    upstash_redis_rest_url: str = Field(..., description="Upstash Redis REST URL")
    upstash_redis_rest_token: str = Field(..., description="Upstash Redis REST token")

    # ── App ──────────────────────────────────────────────────────────────────
    app_env: str = Field(default="development")
    reel_cache_ttl_seconds: int = Field(
        default=86400, description="Redis TTL for cached verdicts (seconds)"
    )

    # ── Derived helpers ──────────────────────────────────────────────────────
    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def meta_graph_base_url(self) -> str:
        return "https://graph.instagram.com/v26.0"


# Single shared instance — import this everywhere
settings = Settings()
