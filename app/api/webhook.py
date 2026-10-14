"""
app/api/webhook.py — Meta Instagram Webhook router.

GET  /webhook  → hub.challenge verification (Meta handshake)
POST /webhook  → handles ig_reel messages via BackgroundTasks (200 OK < 500ms)

Strict routing order (Fix #1):
  1. Check Upstash Redis by reel_video_id       → cache hit: send DM (<1s)
  2. Check Supabase reel_cache table             → DB hit: send cached DM (<1s)
  3. Run reel_analyzer (Gemini) to extract brand/product
  4. Query Supabase products by brand+name       → DB hit: format + send DM (<3s)
  5. Cold-start: send "New product..." ack DM → run research_engine waterfall

Fix #4: All sub-services gracefully mock when API keys are missing.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.config import settings
from app.models.webhook import WebhookPayload, MessagingEvent

logger = logging.getLogger(__name__)

router = APIRouter()


# ══════════════════════════════════════════════════════════════════════════════
# GET /webhook — Meta hub.challenge verification
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/webhook")
async def verify_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> PlainTextResponse:
    """
    Meta sends a GET request to verify the webhook URL.
    We must echo back hub.challenge if the token matches.
    """
    if hub_mode == "subscribe" and hub_verify_token == settings.ig_verify_token:
        logger.info("Webhook verified successfully")
        return PlainTextResponse(content=hub_challenge or "")
    logger.warning(
        "Webhook verification failed (mode=%s, token_match=%s)",
        hub_mode,
        hub_verify_token == settings.ig_verify_token,
    )
    raise HTTPException(status_code=403, detail="Verification failed")


# ══════════════════════════════════════════════════════════════════════════════
# POST /webhook — Incoming message handler
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/webhook")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """
    Meta posts all DM events here. We MUST return 200 OK within 20s
    (ideally < 500ms). All heavy work is dispatched via BackgroundTasks.
    """
    try:
        raw_body = await request.json()
        payload = WebhookPayload(**raw_body)
    except Exception as exc:
        logger.warning("Failed to parse webhook payload: %s", exc)
        # Always return 200 so Meta doesn't retry endlessly
        return JSONResponse(content={"status": "ignored"}, status_code=200)

    if payload.object != "instagram":
        return JSONResponse(content={"status": "ignored"}, status_code=200)

    for entry in payload.entry:
        for messaging_event in entry.messaging:
            # Only handle ig_reel attachments (and quick reply postbacks)
            if messaging_event.message and messaging_event.message.reel_attachment:
                background_tasks.add_task(_handle_reel_event, messaging_event)
            elif messaging_event.postback:
                background_tasks.add_task(_handle_postback, messaging_event)

    return JSONResponse(content={"status": "ok"}, status_code=200)


# ══════════════════════════════════════════════════════════════════════════════
# Background task: handle ig_reel
# ══════════════════════════════════════════════════════════════════════════════

async def _handle_reel_event(event: MessagingEvent) -> None:
    """
    Full routing pipeline for an ig_reel message.

    Routing order (Fix #1):
      1. Redis cache hit  → immediate DM
      2. Supabase reel_cache hit → immediate DM
      3. Gemini reel analysis  → extract brand/product
      4. Supabase products hit → format + send DM
      5. Cold-start → ack DM + async research waterfall
    """
    from app.services.ig_messenger import (
        send_typing_on,
        send_text_message,
        send_hype_check_dm,
        format_verdict_as_text,
    )
    from app.services.reel_analyzer import analyze_reel, get_product_embedding
    from app.services.research_engine import run_research_pipeline
    from app.db.supabase_client import (
        get_cached_reel,
        cache_reel_verdict,
        get_product_by_name,
        upsert_product,
        find_smarter_alternative,
    )
    from app.models.verdict import ProductVerdict, PriceEntry, SmarterAlternative

    sender_id = event.sender.id
    message = event.message
    att = message.reel_attachment
    reel_video_id = message.reel_video_id
    video_url = att.payload.url if att else None
    caption = att.payload.title if att else None

    logger.info(
        "Processing reel event: sender=%s reel_id=%s",
        sender_id,
        reel_video_id,
    )

    # ── Send typing indicator immediately ────────────────────────────────────
    await send_typing_on(sender_id)

    # ═══════════════════════════════════════════════════════════════════════
    # STEP 1: Redis fast path
    # ═══════════════════════════════════════════════════════════════════════
    try:
        from upstash_redis import AsyncRedis  # type: ignore

        redis = AsyncRedis(
            url=settings.upstash_redis_rest_url,
            token=settings.upstash_redis_rest_token,
        )
        cached_dm = await redis.get(f"reel:{reel_video_id}")
        if cached_dm:
            logger.info("FAST PATH (Redis): reel_id=%s", reel_video_id)
            await send_text_message(sender_id, cached_dm)
            return
    except Exception as exc:
        logger.warning("Redis lookup failed (non-fatal): %s", exc)

    # ═══════════════════════════════════════════════════════════════════════
    # STEP 2: Supabase reel_cache check
    # ═══════════════════════════════════════════════════════════════════════
    try:
        db_cached = await get_cached_reel(reel_video_id)
        if db_cached and db_cached.get("formatted_dm_response"):
            cached_text = db_cached["formatted_dm_response"]
            logger.info("DB PATH (reel_cache): reel_id=%s", reel_video_id)
            # Back-fill Redis so next hit is truly <1s
            try:
                await redis.setex(
                    f"reel:{reel_video_id}",
                    settings.reel_cache_ttl_seconds,
                    cached_text,
                )
            except Exception:
                pass
            await send_text_message(sender_id, cached_text)
            return
    except Exception as exc:
        logger.warning("Supabase reel_cache lookup failed (non-fatal): %s", exc)

    # ═══════════════════════════════════════════════════════════════════════
    # STEP 3: Gemini multimodal extraction
    # ═══════════════════════════════════════════════════════════════════════
    reel_data = await analyze_reel(video_url=video_url, caption=caption)
    logger.info(
        "Reel analyzed: %s %s (confidence=%.2f)",
        reel_data.brand,
        reel_data.product_name,
        reel_data.confidence,
    )

    # ═══════════════════════════════════════════════════════════════════════
    # STEP 4: Supabase products table lookup
    # ═══════════════════════════════════════════════════════════════════════
    try:
        existing_product = await get_product_by_name(reel_data.brand, reel_data.product_name)
    except Exception as exc:
        logger.warning("Supabase products lookup failed (non-fatal): %s", exc)
        existing_product = None

    if existing_product:
        logger.info("DB PATH (products): %s %s", reel_data.brand, reel_data.product_name)

        # Reconstruct a ProductVerdict from the stored row
        verdict = ProductVerdict(
            brand=existing_product.get("brand", reel_data.brand),
            product_name=existing_product.get("name", reel_data.product_name),
            category=existing_product.get("category", reel_data.category),
            verdict_score=existing_product.get("verdict_score", 50),
            fake_review_pct=existing_product.get("fake_review_pct", 0),
            verdict_summary="Verdict retrieved from database.",
            creator_claim_verdict="See full report.",
            sponsorship_likelihood=reel_data.sponsorship_likelihood,
            pros=existing_product.get("pros") or [],
            cons=existing_product.get("cons") or [],
            active_ingredients=existing_product.get("active_ingredients") or [],
            lowest_price=existing_product.get("lowest_price"),
            buy_links=existing_product.get("buy_links") or {},
        )

        # Try to find smarter alternative via pgvector
        try:
            embedding = await get_product_embedding(
                reel_data.brand, reel_data.product_name, reel_data.category
            )
            alternatives = await find_smarter_alternative(
                embedding=embedding,
                category=reel_data.category,
                current_score=verdict.verdict_score,
                current_price=verdict.lowest_price,
            )
            if alternatives:
                best = alternatives[0]
                verdict.smarter_alternative = SmarterAlternative(
                    brand=best["brand"],
                    product_name=best["name"],
                    verdict_score=best["verdict_score"],
                    lowest_price=best.get("lowest_price"),
                    reason="Higher community score at a lower price.",
                )
        except Exception as exc:
            logger.warning("find_smarter_alternative failed (non-fatal): %s", exc)

        dm_text = format_verdict_as_text(
            verdict,
            creator_claims=reel_data.creator_claims,
            sponsorship_likelihood=reel_data.sponsorship_likelihood,
        )

        # Cache in Redis + Supabase reel_cache
        await _cache_verdict(
            redis, reel_video_id,
            existing_product.get("id"),
            reel_data, dm_text,
        )

        await send_hype_check_dm(
            sender_id, verdict,
            creator_claims=reel_data.creator_claims,
            sponsorship_likelihood=reel_data.sponsorship_likelihood,
        )
        return

    # ═══════════════════════════════════════════════════════════════════════
    # STEP 5: Cold-start — new product not in Supabase
    # ═══════════════════════════════════════════════════════════════════════
    logger.info("COLD START: new product '%s %s'", reel_data.brand, reel_data.product_name)

    # Send immediate acknowledgement so user knows we're working
    await send_text_message(
        sender_id,
        f"🔍 New product spotted! Checking Reddit & lab reviews for "
        f"{reel_data.brand} {reel_data.product_name}… 🧪\n"
        f"(This may take ~10 seconds for first-time checks)"
    )

    # Run the full research pipeline
    verdict = await run_research_pipeline(reel_data)

    # Find smarter alternative via pgvector (best-effort)
    try:
        embedding = await get_product_embedding(
            reel_data.brand, reel_data.product_name, reel_data.category
        )
        alternatives = await find_smarter_alternative(
            embedding=embedding,
            category=reel_data.category,
            current_score=verdict.verdict_score,
            current_price=verdict.lowest_price,
        )
        if alternatives:
            best = alternatives[0]
            verdict.smarter_alternative = SmarterAlternative(
                brand=best["brand"],
                product_name=best["name"],
                verdict_score=best["verdict_score"],
                lowest_price=best.get("lowest_price"),
                reason="Higher community score at a lower price.",
            )
    except Exception as exc:
        logger.warning("find_smarter_alternative cold-start failed (non-fatal): %s", exc)

    # Upsert product into Supabase
    try:
        embedding = await get_product_embedding(
            reel_data.brand, reel_data.product_name, reel_data.category
        )
        product_row = {
            "brand": verdict.brand,
            "name": verdict.product_name,
            "category": verdict.category,
            "verdict_score": verdict.verdict_score,
            "fake_review_pct": verdict.fake_review_pct,
            "pros": verdict.pros,
            "cons": verdict.cons,
            "active_ingredients": verdict.active_ingredients,
            "lowest_price": verdict.lowest_price,
            "buy_links": verdict.buy_links,
            "embedding": embedding,
        }
        upserted = await upsert_product(product_row)
        product_id = upserted.get("id") if upserted else None
    except Exception as exc:
        logger.error("Supabase upsert failed (non-fatal): %s", exc)
        product_id = None

    dm_text = format_verdict_as_text(
        verdict,
        creator_claims=reel_data.creator_claims,
        sponsorship_likelihood=reel_data.sponsorship_likelihood,
    )

    # Cache verdict
    await _cache_verdict(redis, reel_video_id, product_id, reel_data, dm_text)

    # Send the full Hype Check DM
    await send_hype_check_dm(
        sender_id, verdict,
        creator_claims=reel_data.creator_claims,
        sponsorship_likelihood=reel_data.sponsorship_likelihood,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Background task: handle quick reply postbacks
# ══════════════════════════════════════════════════════════════════════════════

async def _handle_postback(event: MessagingEvent) -> None:
    from app.services.ig_messenger import send_text_message

    sender_id = event.sender.id
    payload = (event.postback or {}).get("payload", "")
    logger.info("Postback from %s: %s", sender_id, payload)

    if payload == "SHOW_PRICES":
        await send_text_message(sender_id, "💰 Price feature coming soon! Use the Hype Check first.")
    elif payload == "SHOW_ALTERNATIVE":
        await send_text_message(sender_id, "🔄 Share a Reel and I'll find you a smarter buy automatically!")
    elif payload == "SHOW_FULL_REPORT":
        await send_text_message(sender_id, "📋 Full detailed reports are coming in the next update!")
    else:
        await send_text_message(sender_id, "👋 Share an Instagram Reel to get a Hype Check!")


# ══════════════════════════════════════════════════════════════════════════════
# Cache helper — writes to both Redis + Supabase reel_cache
# ══════════════════════════════════════════════════════════════════════════════

async def _cache_verdict(
    redis,
    reel_video_id: str,
    product_id,
    reel_data,
    dm_text: str,
) -> None:
    from app.db.supabase_client import cache_reel_verdict

    # Write to Redis
    try:
        await redis.setex(
            f"reel:{reel_video_id}",
            settings.reel_cache_ttl_seconds,
            dm_text,
        )
        logger.info("Cached in Redis: reel_id=%s ttl=%ds", reel_video_id, settings.reel_cache_ttl_seconds)
    except Exception as exc:
        logger.warning("Redis write failed (non-fatal): %s", exc)

    # Write to Supabase reel_cache
    try:
        await cache_reel_verdict(
            reel_video_id=reel_video_id,
            product_id=product_id,
            creator_claim=", ".join(reel_data.creator_claims[:2]) if reel_data.creator_claims else None,
            sponsorship_likelihood=reel_data.sponsorship_likelihood,
            formatted_dm_response=dm_text,
        )
    except Exception as exc:
        logger.warning("Supabase reel_cache write failed (non-fatal): %s", exc)
