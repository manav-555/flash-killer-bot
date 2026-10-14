"""
app/db/supabase_client.py — Supabase async client wrapper.

Provides typed helpers for every DB operation used by the bot:
  - Product lookups (text + pgvector similarity)
  - Reel cache reads/writes
  - Product upserts with embeddings
  - find_smarter_alternative RPC call
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from supabase import AsyncClient, acreate_client

from app.config import settings

logger = logging.getLogger(__name__)

# ── Module-level lazy singleton ───────────────────────────────────────────────
_client: AsyncClient | None = None


async def get_client() -> AsyncClient:
    """Return (and lazily create) the shared async Supabase client."""
    global _client
    if _client is None:
        _client = await acreate_client(settings.supabase_url, settings.supabase_key)
    return _client


# ══════════════════════════════════════════════════════════════════════════════
# Reel Cache helpers
# ══════════════════════════════════════════════════════════════════════════════

async def get_cached_reel(reel_video_id: str) -> dict[str, Any] | None:
    """
    Look up a reel_video_id in the reel_cache table.
    Returns the row dict or None if not found.
    """
    try:
        client = await get_client()
        result = (
            await client.table("reel_cache")
            .select("*")
            .eq("reel_video_id", reel_video_id)
            .limit(1)
            .execute()
        )
        if result.data:
            logger.info("DB reel cache hit for %s", reel_video_id)
            return result.data[0]
    except Exception as exc:
        logger.warning("get_cached_reel error: %s", exc)
    return None


async def cache_reel_verdict(
    reel_video_id: str,
    product_id: str | UUID | None,
    creator_claim: str | None,
    sponsorship_likelihood: str | None,
    formatted_dm_response: str,
) -> None:
    """Upsert a reel verdict into reel_cache (idempotent on reel_video_id)."""
    try:
        client = await get_client()
        await client.table("reel_cache").upsert(
            {
                "reel_video_id": reel_video_id,
                "product_id": str(product_id) if product_id else None,
                "creator_claim": creator_claim,
                "sponsorship_likelihood": sponsorship_likelihood,
                "formatted_dm_response": formatted_dm_response,
            },
            on_conflict="reel_video_id",
        ).execute()
        logger.info("Cached reel verdict for %s", reel_video_id)
    except Exception as exc:
        logger.warning("cache_reel_verdict error: %s", exc)


# ══════════════════════════════════════════════════════════════════════════════
# Product helpers
# ══════════════════════════════════════════════════════════════════════════════

async def get_product_by_name(brand: str, name: str) -> dict[str, Any] | None:
    """
    Text search for a product by brand + name (case-insensitive partial match).
    Returns the first matching row or None.
    """
    try:
        client = await get_client()
        result = (
            await client.table("products")
            .select("*")
            .ilike("brand", f"%{brand}%")
            .ilike("name", f"%{name}%")
            .limit(1)
            .execute()
        )
        if result.data:
            logger.info("Supabase product hit: %s %s", brand, name)
            return result.data[0]
    except Exception as exc:
        logger.warning("get_product_by_name error: %s", exc)
    return None


async def upsert_product(product_data: dict[str, Any]) -> dict[str, Any] | None:
    """
    Insert or update a product row. If a row with the same brand+name exists,
    update it; otherwise insert. Returns the upserted row.

    product_data keys (all optional except brand/name):
      brand, name, category, verdict_score, fake_review_pct,
      pros, cons, active_ingredients, lowest_price, buy_links, embedding
    """
    try:
        client = await get_client()
        # Convert embedding list to pgvector string format if present
        if "embedding" in product_data and isinstance(product_data["embedding"], list):
            product_data["embedding"] = product_data["embedding"]  # supabase-py handles lists

        result = (
            await client.table("products")
            .upsert(product_data, on_conflict="brand,name")
            .execute()
        )
        if result.data:
            logger.info("Upserted product: %s %s", product_data.get("brand"), product_data.get("name"))
            return result.data[0]
    except Exception as exc:
        logger.error("upsert_product error: %s", exc)
    return None


async def find_smarter_alternative(
    embedding: list[float],
    category: str,
    current_score: int,
    current_price: float | None,
) -> list[dict[str, Any]]:
    """
    Call the PostgreSQL RPC function `find_smarter_alternative`.
    Returns up to 3 alternative products ranked by cosine similarity.
    """
    try:
        client = await get_client()
        result = await client.rpc(
            "find_smarter_alternative",
            {
                "target_embedding": embedding,
                "target_category": category,
                "current_score": current_score,
                "current_price": current_price,
            },
        ).execute()
        return result.data or []
    except Exception as exc:
        logger.warning("find_smarter_alternative RPC error: %s", exc)
        return []
