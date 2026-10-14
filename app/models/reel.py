"""
app/models/reel.py — Pydantic models for the Reel extraction step.

gemini-2.5-flash uses structured outputs to fill ReelExtractionResult
from the Instagram Reel video bytes + caption text.
"""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class ReelExtractionResult(BaseModel):
    """Structured output returned by the Gemini multimodal reel analyzer."""

    brand: str = Field(description="Brand name of the product shown in the reel (e.g. 'The Ordinary')")
    product_name: str = Field(description="Specific product name (e.g. 'Niacinamide 10% + Zinc 1%')")
    category: str = Field(
        description="Product category (e.g. 'skincare', 'haircare', 'makeup', 'supplement', 'food')"
    )
    creator_claims: list[str] = Field(
        default_factory=list,
        description="List of specific benefit claims made by the creator (verbatim or paraphrased)",
    )
    sponsorship_signals: list[str] = Field(
        default_factory=list,
        description=(
            "Signals suggesting a paid partnership: #ad, #sponsored, 'gifted', "
            "unnatural enthusiasm, referral codes, etc."
        ),
    )
    sponsorship_likelihood: Literal["high", "medium", "low"] = Field(
        default="low",
        description="Estimated likelihood this is a paid/sponsored post",
    )
    is_gatekept_in_comments: bool = Field(
        default=False,
        description="True if creator hides product details in comments to boost engagement",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Model confidence in the brand/product extraction (0.0–1.0)",
    )
