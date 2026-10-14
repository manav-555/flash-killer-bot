"""
app/models/verdict.py — Pydantic models for the product scoring output.

ProductVerdict is the final structured output from the Scoring LLM
(Gemini primary / Groq fallback) after ingesting all scraped research.
"""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class PriceEntry(BaseModel):
    store: str
    price: float
    currency: str = "INR"
    url: str | None = None


class SmarterAlternative(BaseModel):
    brand: str
    product_name: str
    verdict_score: int
    lowest_price: float | None = None
    buy_url: str | None = None
    reason: str = Field(description="One-line reason why this is a smarter buy")


class ProductVerdict(BaseModel):
    """Complete AI verdict for a product — stored in Supabase `products`."""

    brand: str
    product_name: str
    category: str

    # ── Scoring ──────────────────────────────────────────────────────────────
    verdict_score: int = Field(ge=0, le=100, description="Real consensus score 0–100")
    fake_review_pct: int = Field(
        ge=0, le=100, description="Estimated % of incentivized / seeded reviews"
    )

    # ── Analysis ─────────────────────────────────────────────────────────────
    verdict_summary: str = Field(description="2–3 sentence honest verdict")
    creator_claim_verdict: str = Field(
        description="One-line: does the science/community back the creator's claim?"
    )
    sponsorship_likelihood: Literal["high", "medium", "low"]
    pros: list[str] = Field(default_factory=list, description="Up to 4 evidence-backed pros")
    cons: list[str] = Field(default_factory=list, description="Up to 4 evidence-backed cons")
    active_ingredients: list[str] = Field(default_factory=list)

    # ── Pricing ──────────────────────────────────────────────────────────────
    prices: list[PriceEntry] = Field(default_factory=list)
    lowest_price: float | None = None
    buy_links: dict[str, str] = Field(default_factory=dict, description="{store: url}")

    # ── Smarter Buy ──────────────────────────────────────────────────────────
    smarter_alternative: SmarterAlternative | None = None

    # ── Sources ──────────────────────────────────────────────────────────────
    sources_used: list[str] = Field(
        default_factory=list, description="URLs / labels of sources scraped"
    )
    reddit_thread_count: int = 0
