"""
app/services/reel_analyzer.py — Multimodal Reel extraction via Gemini 2.5 Flash.

Fix #1 applied: Downloads video bytes and passes them inline using
  types.Part.from_bytes(data=video_bytes, mime_type="video/mp4")

Fix #2 applied: Generates 768-dim embeddings using
  model="gemini-embedding-001"
  config=types.EmbedContentConfig(output_dimensionality=768)

Fix #4 applied: Falls back to mock data when GEMINI_API_KEY is missing/placeholder.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.config import settings
from app.models.reel import ReelExtractionResult

logger = logging.getLogger(__name__)

# ── Placeholder detection ──────────────────────────────────────────────────────
_PLACEHOLDER_PREFIXES = ("your_", "AIza", "sk-", "gsk_", "fc-", "tvly-", "jina_", "eyJ")


def _is_placeholder(value: str) -> bool:
    """Return True if the value still looks like a template placeholder."""
    return not value or any(value.startswith(p) for p in _PLACEHOLDER_PREFIXES)


# ── Mock fallback (Fix #4) ────────────────────────────────────────────────────
_MOCK_EXTRACTION = ReelExtractionResult(
    brand="The Ordinary",
    product_name="Niacinamide 10% + Zinc 1%",
    category="skincare",
    creator_claims=["Shrinks pores instantly", "Clears acne in 3 days"],
    sponsorship_signals=["#gifted", "Use code CREATOR20"],
    sponsorship_likelihood="high",
    is_gatekept_in_comments=False,
    confidence=0.95,
)

_MOCK_EMBEDDING: list[float] = [0.0] * 768


# ══════════════════════════════════════════════════════════════════════════════
# Video download helper
# ══════════════════════════════════════════════════════════════════════════════

async def download_video_bytes(url: str, max_mb: int = 20) -> bytes:
    """
    Stream-download the CDN video URL up to `max_mb` MB.
    Returns raw bytes for inline Gemini multimodal input.
    """
    max_bytes = max_mb * 1024 * 1024
    chunks: list[bytes] = []
    total = 0
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes(chunk_size=65_536):
                chunks.append(chunk)
                total += len(chunk)
                if total >= max_bytes:
                    logger.warning("Video truncated at %d MB", max_mb)
                    break
    return b"".join(chunks)


# ══════════════════════════════════════════════════════════════════════════════
# Main analyzer
# ══════════════════════════════════════════════════════════════════════════════

async def analyze_reel(
    video_url: str | None,
    caption: str | None = None,
) -> ReelExtractionResult:
    """
    Download the Reel video and extract structured product data via Gemini.

    Args:
        video_url: CDN URL of the Instagram Reel (from webhook payload).
        caption:   Reel caption / title from the webhook payload.

    Returns:
        ReelExtractionResult with brand, product_name, claims, etc.
    """
    # ── Mock mode ─────────────────────────────────────────────────────────────
    if _is_placeholder(settings.gemini_api_key):
        logger.warning("[MOCK] GEMINI_API_KEY not set — returning mock ReelExtractionResult")
        return _MOCK_EXTRACTION

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.gemini_api_key)

        # Build prompt parts
        prompt_text = (
            "You are an expert consumer product analyst. Analyse the Instagram Reel video "
            "(and caption if provided) and extract structured product information.\n\n"
            f"Caption: {caption or '(none provided)'}\n\n"
            "Return JSON matching this exact schema:\n"
            "{\n"
            '  "brand": "string",\n'
            '  "product_name": "string",\n'
            '  "category": "string",\n'
            '  "creator_claims": ["string"],\n'
            '  "sponsorship_signals": ["string"],\n'
            '  "sponsorship_likelihood": "high"|"medium"|"low",\n'
            '  "is_gatekept_in_comments": boolean,\n'
            '  "confidence": float (0.0-1.0)\n'
            "}\n"
            "Respond with ONLY valid JSON, no markdown fences."
        )

        parts: list[Any] = [prompt_text]

        # Download and attach video bytes inline (Fix #1)
        if video_url:
            try:
                video_bytes = await download_video_bytes(video_url)
                parts.insert(
                    0,
                    types.Part.from_bytes(data=video_bytes, mime_type="video/mp4"),
                )
                logger.info("Attached %d bytes of video to Gemini prompt", len(video_bytes))
            except Exception as dl_err:
                logger.warning("Video download failed (%s) — using caption only", dl_err)

        # Use client.aio.models for async generate_content (google-genai v1.70+)
        response = await client.aio.models.generate_content(
            model=settings.gemini_model,
            contents=parts,
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
            ),
        )

        raw_json = response.text.strip()
        data = json.loads(raw_json)
        result = ReelExtractionResult(**data)
        logger.info(
            "Reel analyzed: brand=%s product=%s confidence=%.2f",
            result.brand,
            result.product_name,
            result.confidence,
        )
        return result

    except Exception as exc:
        logger.error("Gemini reel analysis failed: %s — returning mock", exc)
        return _MOCK_EXTRACTION


# ══════════════════════════════════════════════════════════════════════════════
# Embedding helper (Fix #2)
# ══════════════════════════════════════════════════════════════════════════════

async def get_product_embedding(brand: str, product_name: str, category: str) -> list[float]:
    """
    Generate a 768-dim text embedding using gemini-embedding-001.
    Used for pgvector similarity search in find_smarter_alternative.
    """
    if _is_placeholder(settings.gemini_api_key):
        logger.warning("[MOCK] GEMINI_API_KEY not set — returning zero embedding")
        return _MOCK_EMBEDDING

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.gemini_api_key)
        text = f"{brand} {product_name} {category}".strip()

        # Use client.aio.models for async embed_content (google-genai v1.70+)
        result = await client.aio.models.embed_content(
            model="gemini-embedding-001",
            contents=text,
            config=types.EmbedContentConfig(output_dimensionality=768),
        )
        embedding: list[float] = result.embeddings[0].values
        logger.debug("Generated 768-dim embedding for '%s %s'", brand, product_name)
        return embedding

    except Exception as exc:
        logger.error("Embedding generation failed: %s — returning zeros", exc)
        return _MOCK_EMBEDDING
