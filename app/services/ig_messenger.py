"""
app/services/ig_messenger.py — Meta Graph API message sender.

All calls target:
  https://graph.instagram.com/v26.0/me/messages

Provides:
  - send_typing_on()        — immediate typing indicator
  - send_text_message()     — plain text DM
  - send_hype_check_dm()    — formatted verdict card with quick replies

Fix #4: Gracefully no-ops when IG_PAGE_ACCESS_TOKEN is a placeholder.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings
from app.models.verdict import ProductVerdict

logger = logging.getLogger(__name__)

# ── Score → emoji band ────────────────────────────────────────────────────────
def _score_emoji(score: int) -> str:
    if score >= 80:
        return "🟢"
    if score >= 60:
        return "🟡"
    if score >= 40:
        return "🟠"
    return "🔴"


def _placeholder_token() -> bool:
    token = settings.ig_page_access_token
    return not token or token.startswith("your_") or len(token) < 20


# ══════════════════════════════════════════════════════════════════════════════
# Low-level sender
# ══════════════════════════════════════════════════════════════════════════════

async def _send_message(recipient_id: str, payload: dict) -> bool:
    """
    POST a message payload to the Meta Graph API.
    Returns True on success, False on failure.
    """
    if _placeholder_token():
        logger.warning("[MOCK] IG_PAGE_ACCESS_TOKEN not set — logging message instead of sending")
        logger.info("MOCK DM to %s: %s", recipient_id, payload)
        return True

    url = f"{settings.meta_graph_base_url}/me/messages"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                params={"access_token": settings.ig_page_access_token},
                json={"recipient": {"id": recipient_id}, **payload},
            )
            if resp.status_code not in (200, 201):
                logger.error(
                    "Meta API error %d for recipient %s: %s",
                    resp.status_code,
                    recipient_id,
                    resp.text[:500],
                )
                return False
            logger.info("Message sent to %s", recipient_id)
            return True
    except Exception as exc:
        logger.error("send_message exception for %s: %s", recipient_id, exc)
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

async def send_typing_on(recipient_id: str) -> None:
    """Send a typing_on indicator so the user sees the bot is 'thinking'."""
    await _send_message(
        recipient_id,
        {"sender_action": "typing_on"},
    )


async def send_text_message(recipient_id: str, text: str) -> bool:
    """Send a plain text DM."""
    return await _send_message(
        recipient_id,
        {"message": {"text": text}},
    )


async def send_hype_check_dm(
    recipient_id: str,
    verdict: ProductVerdict,
    creator_claims: list[str] | None = None,
    sponsorship_likelihood: str | None = None,
) -> bool:
    """
    Send the full formatted "Hype Check" verdict card DM.

    Uses a text + quick replies template since Instagram DM supports
    generic templates only for approved business use cases.
    """
    score = verdict.verdict_score
    emoji = _score_emoji(score)
    fake_warn = f"⚠️ ~{verdict.fake_review_pct}% reviews may be incentivised" if verdict.fake_review_pct > 20 else ""

    # ── Format pros / cons ────────────────────────────────────────────────────
    pros_text = "\n".join(f"  ✅ {p}" for p in verdict.pros[:3]) or "  (none found)"
    cons_text = "\n".join(f"  ❌ {c}" for c in verdict.cons[:3]) or "  (none found)"

    # ── Price line ────────────────────────────────────────────────────────────
    if verdict.lowest_price:
        cheapest_store = verdict.prices[0].store if verdict.prices else "Online"
        price_line = f"💰 Lowest Price: ₹{verdict.lowest_price:,.0f} on {cheapest_store}"
    else:
        price_line = "💰 Price: Not found"

    # ── Smarter buy ───────────────────────────────────────────────────────────
    smarter_line = ""
    if verdict.smarter_alternative:
        alt = verdict.smarter_alternative
        smarter_line = (
            f"\n\n🔄 *Smarter Buy:* {alt.brand} {alt.product_name} "
            f"(Score: {alt.verdict_score}/100"
            + (f", ₹{alt.lowest_price:,.0f}" if alt.lowest_price else "")
            + f")\n   → {alt.reason}"
        )

    # ── Sponsorship warning ───────────────────────────────────────────────────
    sponsor_text = ""
    if (sponsorship_likelihood or verdict.sponsorship_likelihood) in ("high", "medium"):
        signals = ", ".join((creator_claims or [])[:2]) or "multiple signals detected"
        sponsor_text = f"\n\n🚨 *Sponsorship Alert:* {signals}"

    # ── Claim vs reality ──────────────────────────────────────────────────────
    claim_text = ""
    if verdict.creator_claim_verdict:
        claim_text = f"\n\n🔬 *Claim Check:* {verdict.creator_claim_verdict}"

    message_text = (
        f"🛍️ HYPE CHECK — {verdict.brand} {verdict.product_name}\n"
        f"{'─' * 38}\n"
        f"{emoji} Real Score: {score}/100\n"
        f"{fake_warn}\n"
        f"{claim_text}"
        f"{sponsor_text}\n\n"
        f"✅ Pros:\n{pros_text}\n\n"
        f"❌ Cons:\n{cons_text}\n\n"
        f"{price_line}"
        f"{smarter_line}\n\n"
        f"📊 Based on {verdict.reddit_thread_count} Reddit threads + verified reviews.\n"
        f"🤖 Powered by Flash Killer Bot"
    ).strip()

    # Instagram DM text limit is 1000 chars — truncate gracefully
    if len(message_text) > 980:
        message_text = message_text[:977] + "…"

    # ── Quick replies so user can ask for alternatives / prices ───────────────
    quick_replies = [
        {"content_type": "text", "title": "💰 All Prices", "payload": "SHOW_PRICES"},
        {"content_type": "text", "title": "🔄 Smarter Buy", "payload": "SHOW_ALTERNATIVE"},
        {"content_type": "text", "title": "📋 Full Report", "payload": "SHOW_FULL_REPORT"},
    ]

    return await _send_message(
        recipient_id,
        {
            "message": {
                "text": message_text,
                "quick_replies": quick_replies,
            }
        },
    )


def format_verdict_as_text(
    verdict: ProductVerdict,
    creator_claims: list[str] | None = None,
    sponsorship_likelihood: str | None = None,
) -> str:
    """
    Return the formatted DM text without sending it.
    Used for caching and test verification.
    """
    score = verdict.verdict_score
    emoji = _score_emoji(score)
    fake_warn = f"⚠️ ~{verdict.fake_review_pct}% reviews may be incentivised" if verdict.fake_review_pct > 20 else ""
    pros_text = "\n".join(f"  ✅ {p}" for p in verdict.pros[:3]) or "  (none found)"
    cons_text = "\n".join(f"  ❌ {c}" for c in verdict.cons[:3]) or "  (none found)"

    if verdict.lowest_price:
        cheapest_store = verdict.prices[0].store if verdict.prices else "Online"
        price_line = f"💰 Lowest Price: ₹{verdict.lowest_price:,.0f} on {cheapest_store}"
    else:
        price_line = "💰 Price: Not found"

    smarter_line = ""
    if verdict.smarter_alternative:
        alt = verdict.smarter_alternative
        smarter_line = (
            f"\n\n🔄 Smarter Buy: {alt.brand} {alt.product_name} "
            f"(Score: {alt.verdict_score}/100"
            + (f", ₹{alt.lowest_price:,.0f}" if alt.lowest_price else "")
            + f")\n   → {alt.reason}"
        )

    return (
        f"🛍️ HYPE CHECK — {verdict.brand} {verdict.product_name}\n"
        f"{'─' * 38}\n"
        f"{emoji} Real Score: {score}/100\n"
        f"{fake_warn}\n\n"
        f"✅ Pros:\n{pros_text}\n\n"
        f"❌ Cons:\n{cons_text}\n\n"
        f"{price_line}"
        f"{smarter_line}"
    ).strip()
