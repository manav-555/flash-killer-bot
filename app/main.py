"""
app/main.py — FastAPI application factory.

Startup/shutdown lifecycle:
  - Validate required env vars via Settings
  - Log all registered routes

Run locally:
  uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api.webhook import router as webhook_router

logging.basicConfig(
    level=logging.DEBUG if not settings.is_production else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Flash Killer Bot — Instagram Hype-Check API",
        description=(
            "AI-powered Instagram DM bot that instantly de-influences "
            "product hype with verified Reddit consensus, real prices, "
            "and a smarter alternative."
        ),
        version="1.0.0",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
    )

    # ── CORS (Meta webhook servers need no CORS; this is for local testing) ──
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(webhook_router, tags=["Meta Webhook"])

    @app.get("/health", tags=["Health"])
    async def health_check() -> dict:
        return {"status": "ok", "env": settings.app_env}

    logger.info("Flash Killer Bot API started (env=%s)", settings.app_env)
    return app


app = create_app()
