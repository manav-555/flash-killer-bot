"""
app/services/research_engine.py — $0 Free-Tier Scraper & Scoring Waterfall.

Pipeline executed during Cold-Start (product not in Supabase):
  Step A: Reddit public JSON search (0 credits)
  Step B: Tavily API targeting reddit.com + incidecoder.com (1,000 free/mo)
  Step C: Jina Reader for static ingredient/review pages (500 RPM free)
  Step D: Firecrawl /scrape for anti-bot JS pages ONLY (Nykaa, Amazon, Tira, Sephora)
  Step E: Serper.dev Shopping — live Indian marketplace prices
  Step F: Gemini 2.5 Flash scoring → ProductVerdict (Groq fallback on HTTP 429)

Fix #3 applied: Firecrawl supports both scrape() and scrape_url() methods.
Fix #4 applied: All external calls gracefully return mock data when API keys are missing.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config import settings
from app.models.reel import ReelExtractionResult
from app.models.verdict import ProductVerdict, PriceEntry, SmarterAlternative

logger = logging.getLogger(__name__)

# ── Anti-bot domains that need Firecrawl (Step D) ────────────────────────────
_FIRECRAWL_DOMAINS = ("nykaa.com", "amazon.in", "tira.com", "sephora.in", "myntra.com")

# ── Placeholder check ─────────────────────────────────────────────────────────

def _is_placeholder(value: str) -> bool:
    return not value or value.startswith("your_") or len(value) < 8


# ══════════════════════════════════════════════════════════════════════════════
# Step A — Reddit Public JSON  (0 credits)
# ══════════════════════════════════════════════════════════════════════════════

async def _fetch_reddit_threads(product_query: str) -> str:
    """
    Query Reddit's public search JSON endpoint. Returns markdown-formatted thread text.
    """
    url = (
        f"https://www.reddit.com/search.json"
        f"?q={httpx.URL(product_query).params}&sort=relevance&limit=8&type=link"
    )
    headers = {"User-Agent": "FlashKillerBot/1.0 (hype-check research; contact@example.com)"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://www.reddit.com/search.json",
                params={"q": f"{product_query} review", "sort": "relevance", "limit": 8},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            posts = data.get("data", {}).get("children", [])
            lines: list[str] = []
            for post in posts:
                p = post.get("data", {})
                title = p.get("title", "")
                selftext = p.get("selftext", "")[:500]
                score = p.get("score", 0)
                url_val = p.get("url", "")
                lines.append(f"### [{title}]({url_val}) (↑{score})\n{selftext}\n")
            result = "\n".join(lines) if lines else "(no Reddit threads found)"
            logger.info("Reddit: fetched %d threads for '%s'", len(posts), product_query)
            return result
    except Exception as exc:
        logger.warning("Reddit fetch failed: %s", exc)
        return "(Reddit unavailable)"


# ══════════════════════════════════════════════════════════════════════════════
# Step B — Tavily API  (1,000 free requests/mo)
# ══════════════════════════════════════════════════════════════════════════════

async def _fetch_tavily_results(product_query: str) -> str:
    """Fetch targeted search results from Tavily, prioritising reddit + incidecoder."""
    if _is_placeholder(settings.tavily_api_key):
        logger.warning("[MOCK] TAVILY_API_KEY not set — skipping Tavily")
        return "(Tavily skipped — no API key)"
    try:
        from tavily import TavilyClient  # type: ignore

        client = TavilyClient(api_key=settings.tavily_api_key)
        # Run both queries concurrently
        reddit_task = asyncio.to_thread(
            client.search,
            query=f"{product_query} honest review",
            search_depth="basic",
            include_domains=["reddit.com"],
            max_results=5,
        )
        ingredient_task = asyncio.to_thread(
            client.search,
            query=f"{product_query} ingredients analysis",
            search_depth="basic",
            include_domains=["incidecoder.com", "cosdna.com"],
            max_results=3,
        )
        reddit_res, ingredient_res = await asyncio.gather(
            reddit_task, ingredient_task, return_exceptions=True
        )

        sections: list[str] = []
        for res in (reddit_res, ingredient_res):
            if isinstance(res, Exception):
                logger.warning("Tavily sub-query error: %s", res)
                continue
            for item in res.get("results", []):
                sections.append(
                    f"**{item.get('title', '')}** ({item.get('url', '')})\n{item.get('content', '')[:600]}\n"
                )
        result = "\n".join(sections) if sections else "(no Tavily results)"
        logger.info("Tavily: got %d sections", len(sections))
        return result
    except Exception as exc:
        logger.warning("Tavily failed: %s", exc)
        return "(Tavily unavailable)"


# ══════════════════════════════════════════════════════════════════════════════
# Step C — Jina Reader  (500 RPM free)
# ══════════════════════════════════════════════════════════════════════════════

async def _fetch_jina_page(url: str) -> str:
    """Convert a static page to clean Markdown via Jina Reader."""
    jina_url = f"https://r.jina.ai/{url}"
    headers: dict[str, str] = {"Accept": "text/markdown"}
    if not _is_placeholder(settings.jina_api_key):
        headers["Authorization"] = f"Bearer {settings.jina_api_key}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(jina_url, headers=headers)
            resp.raise_for_status()
            content = resp.text[:3000]
            logger.info("Jina: fetched %d chars from %s", len(content), url)
            return content
    except Exception as exc:
        logger.warning("Jina fetch failed for %s: %s", url, exc)
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# Step D — Firecrawl /scrape  (anti-bot pages ONLY)
# Fix #3: supports both scrape() and scrape_url() methods
# ══════════════════════════════════════════════════════════════════════════════

async def _fetch_firecrawl_page(url: str) -> str:
    """
    Scrape a JS-heavy page via Firecrawl. ONLY call this for known anti-bot
    domains (Nykaa, Amazon.in, Tira, Sephora). Uses /scrape (1 credit),
    NEVER /extract (5 credits).

    Fix #3: tries modern scrape() first, falls back to scrape_url().
    """
    if _is_placeholder(settings.firecrawl_api_key):
        logger.warning("[MOCK] FIRECRAWL_API_KEY not set — skipping Firecrawl for %s", url)
        return ""

    try:
        from firecrawl import FirecrawlApp  # type: ignore

        app = FirecrawlApp(api_key=settings.firecrawl_api_key)

        scrape_kwargs = {
            "formats": ["markdown"],
            "onlyMainContent": True,
        }

        # Fix #3: try modern method signature first
        markdown_content = await asyncio.to_thread(_firecrawl_scrape_sync, app, url, scrape_kwargs)
        logger.info("Firecrawl: scraped %d chars from %s", len(markdown_content), url)
        return markdown_content[:3000]

    except Exception as exc:
        logger.warning("Firecrawl failed for %s: %s", url, exc)
        return ""


def _firecrawl_scrape_sync(app: Any, url: str, kwargs: dict) -> str:
    """
    Sync wrapper (run in executor). Tries scrape() first (firecrawl-py >= 1.0),
    falls back to scrape_url() for older SDK versions.
    """
    try:
        # Modern API: app.scrape(url, formats=[...], only_main_content=True)
        result = app.scrape(
            url,
            formats=kwargs.get("formats", ["markdown"]),
            only_main_content=kwargs.get("onlyMainContent", True),
        )
    except (AttributeError, TypeError):
        # Fallback for older firecrawl-py versions
        result = app.scrape_url(
            url,
            params={"formats": kwargs.get("formats", ["markdown"]), "onlyMainContent": True},
        )

    if isinstance(result, dict):
        return result.get("markdown", result.get("content", ""))
    # Some SDK versions return an object with .markdown attribute
    if hasattr(result, "markdown"):
        return result.markdown or ""
    return str(result)


# ══════════════════════════════════════════════════════════════════════════════
# Step E — Serper.dev Shopping  (live prices)
# ══════════════════════════════════════════════════════════════════════════════

async def _fetch_prices_serper(brand: str, product_name: str) -> tuple[list[PriceEntry], dict[str, str]]:
    """
    Query Serper.dev Google Shopping for live Indian marketplace prices.
    Returns (prices_list, {store: buy_url}) tuple.
    """
    if _is_placeholder(settings.serper_api_key):
        logger.warning("[MOCK] SERPER_API_KEY not set — returning mock prices")
        mock_price = PriceEntry(store="Amazon.in", price=499.0, currency="INR", url="https://amazon.in")
        return [mock_price], {"Amazon.in": "https://amazon.in"}

    query = f"{brand} {product_name} buy india"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://google.serper.dev/shopping",
                headers={"X-API-KEY": settings.serper_api_key, "Content-Type": "application/json"},
                json={"q": query, "gl": "in", "hl": "en", "num": 10},
            )
            resp.raise_for_status()
            data = resp.json()
            shopping_results = data.get("shopping", [])

            prices: list[PriceEntry] = []
            buy_links: dict[str, str] = {}
            for item in shopping_results[:8]:
                store = item.get("source", "Unknown")
                price_str = item.get("price", "").replace("₹", "").replace(",", "").strip()
                link = item.get("link", "")
                try:
                    price_val = float(price_str)
                    prices.append(PriceEntry(store=store, price=price_val, currency="INR", url=link))
                    if store not in buy_links:
                        buy_links[store] = link
                except ValueError:
                    pass

            prices.sort(key=lambda p: p.price)
            logger.info("Serper: found %d price entries for '%s %s'", len(prices), brand, product_name)
            return prices, buy_links

    except Exception as exc:
        logger.warning("Serper fetch failed: %s", exc)
        return [], {}


# ══════════════════════════════════════════════════════════════════════════════
# Step F — LLM Scoring  (Gemini primary → Groq fallback on HTTP 429)
# ══════════════════════════════════════════════════════════════════════════════

_SCORING_PROMPT_TEMPLATE = """\
You are a rigorous, independent product analyst. You MUST ignore incentivised,
seeded, or obviously fake reviews. Analyse the following research data and return
a strict JSON verdict.

Product: {brand} — {product_name} ({category})
Creator Claims: {creator_claims}
Sponsorship Signals: {sponsorship_signals}
Sponsorship Likelihood: {sponsorship_likelihood}

=== RESEARCH DATA (Reddit, reviews, ingredient analysis) ===
{research_markdown}

=== PRICING DATA ===
{pricing_markdown}

Return ONLY valid JSON matching this exact schema (no markdown fences):
{{
  "brand": "string",
  "product_name": "string",
  "category": "string",
  "verdict_score": integer (0-100, penalise heavily for hype/sponsorship),
  "fake_review_pct": integer (0-100),
  "verdict_summary": "2-3 sentence honest verdict",
  "creator_claim_verdict": "one-line: does science/community back the claim?",
  "sponsorship_likelihood": "high"|"medium"|"low",
  "pros": ["up to 4 evidence-backed pros"],
  "cons": ["up to 4 evidence-backed cons"],
  "active_ingredients": ["list"],
  "sources_used": ["list of source labels used"],
  "reddit_thread_count": integer
}}
"""

_MOCK_VERDICT_JSON = {
    "brand": "The Ordinary",
    "product_name": "Niacinamide 10% + Zinc 1%",
    "category": "skincare",
    "verdict_score": 74,
    "fake_review_pct": 28,
    "verdict_summary": (
        "Solid, science-backed niacinamide serum at a budget price point. "
        "Community consensus is positive for pore texture and oil control, "
        "but viral 'instant pore-erasing' claims are heavily exaggerated."
    ),
    "creator_claim_verdict": "Pore-shrinking is over-claimed; oil control is legitimate.",
    "sponsorship_likelihood": "high",
    "pros": ["Effective niacinamide concentration", "Budget-friendly", "Fragrance-free"],
    "cons": ["Glycerin can pill under SPF", "Creator claims are exaggerated"],
    "active_ingredients": ["Niacinamide 10%", "Zinc PCA 1%"],
    "sources_used": ["[MOCK DATA]"],
    "reddit_thread_count": 0,
}


async def _score_with_gemini(prompt: str) -> dict:
    """Primary scorer using Gemini 2.5 Flash (async via client.aio.models)."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.gemini_api_key)
    # Use client.aio.models for true async (google-genai v1.70+)
    response = await client.aio.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
        ),
    )
    return json.loads(response.text.strip())


async def _score_with_groq(prompt: str) -> dict:
    """Fallback scorer using Groq llama-3.3-70b-versatile."""
    from groq import AsyncGroq  # type: ignore

    client = AsyncGroq(api_key=settings.groq_api_key)
    response = await client.chat.completions.create(
        model=settings.groq_model,
        messages=[
            {"role": "system", "content": "You are a rigorous consumer product analyst. Return only valid JSON."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=1500,
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


async def _score_with_openrouter(prompt: str) -> dict:
    """Last-resort scorer via OpenRouter free models."""
    from openai import AsyncOpenAI  # type: ignore

    client = AsyncOpenAI(
        api_key=settings.openrouter_api_key,
        base_url="https://openrouter.ai/api/v1",
    )
    response = await client.chat.completions.create(
        model=settings.openrouter_model,
        messages=[
            {"role": "system", "content": "You are a rigorous consumer product analyst. Return only valid JSON."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )
    return json.loads(response.choices[0].message.content)


async def _score_with_llm(prompt: str) -> dict:
    """
    Try Gemini → Groq → OpenRouter in sequence.
    Falls back to mock if all fail or keys are missing.
    """
    scorers = []

    if not _is_placeholder(settings.gemini_api_key):
        scorers.append(("Gemini", _score_with_gemini))
    if not _is_placeholder(settings.groq_api_key):
        scorers.append(("Groq", _score_with_groq))
    if not _is_placeholder(settings.openrouter_api_key):
        scorers.append(("OpenRouter", _score_with_openrouter))

    for name, scorer in scorers:
        try:
            logger.info("Scoring with %s...", name)
            result = await scorer(prompt)
            logger.info("Score from %s: verdict_score=%s", name, result.get("verdict_score"))
            return result
        except Exception as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code == 429:
                logger.warning("%s rate-limited (429) — trying next LLM", name)
            else:
                logger.error("%s scoring error: %s — trying next LLM", name, exc)

    logger.warning("[MOCK] All LLMs failed or no keys set — returning mock verdict")
    return _MOCK_VERDICT_JSON


# ══════════════════════════════════════════════════════════════════════════════
# Main orchestrator
# ══════════════════════════════════════════════════════════════════════════════

async def run_research_pipeline(reel_data: ReelExtractionResult) -> ProductVerdict:
    """
    Full cold-start research pipeline. Runs all scraper steps concurrently
    where possible, then scores with the LLM waterfall.

    Returns a fully populated ProductVerdict ready for Supabase upsert.
    """
    brand = reel_data.brand
    product = reel_data.product_name
    category = reel_data.category
    product_query = f"{brand} {product}"

    logger.info("Starting research pipeline for: %s", product_query)

    # ── Steps A + B + E run concurrently ─────────────────────────────────────
    reddit_task = asyncio.create_task(_fetch_reddit_threads(product_query))
    tavily_task = asyncio.create_task(_fetch_tavily_results(product_query))
    price_task = asyncio.create_task(_fetch_prices_serper(brand, product))

    reddit_md, tavily_md, (prices, buy_links) = await asyncio.gather(
        reddit_task, tavily_task, price_task
    )

    # ── Steps C + D: Jina/Firecrawl for ingredient pages ─────────────────────
    ingredient_pages: list[str] = []
    incidecoder_url = f"https://incidecoder.com/search?query={brand}+{product}".replace(" ", "+")
    jina_content = await _fetch_jina_page(incidecoder_url)
    if jina_content:
        ingredient_pages.append(jina_content)

    # Firecrawl fallback for anti-bot price pages (cheapest option from Serper)
    for price_entry in prices[:2]:
        url = price_entry.url or ""
        if url and any(domain in url for domain in _FIRECRAWL_DOMAINS):
            fc_content = await _fetch_firecrawl_page(url)
            if fc_content:
                ingredient_pages.append(fc_content)
            break  # Only scrape one anti-bot page to conserve credits

    # ── Aggregate research markdown ───────────────────────────────────────────
    research_md_parts = ["## Reddit Threads\n", reddit_md, "\n\n## Tavily Results\n", tavily_md]
    if ingredient_pages:
        research_md_parts.extend(["\n\n## Ingredient Analysis\n", "\n".join(ingredient_pages)])
    research_markdown = "\n".join(research_md_parts)

    pricing_markdown = "\n".join(
        f"- {p.store}: ₹{p.price:,.0f} ({p.url})" for p in prices
    ) or "(no prices found)"

    # ── Build scoring prompt ──────────────────────────────────────────────────
    prompt = _SCORING_PROMPT_TEMPLATE.format(
        brand=brand,
        product_name=product,
        category=category,
        creator_claims=", ".join(reel_data.creator_claims) or "none",
        sponsorship_signals=", ".join(reel_data.sponsorship_signals) or "none",
        sponsorship_likelihood=reel_data.sponsorship_likelihood,
        research_markdown=research_markdown[:8000],   # stay within context window
        pricing_markdown=pricing_markdown,
    )

    # ── Score with LLM waterfall ──────────────────────────────────────────────
    verdict_raw = await _score_with_llm(prompt)

    # ── Merge pricing data into verdict ──────────────────────────────────────
    lowest = prices[0].price if prices else None

    verdict = ProductVerdict(
        **verdict_raw,
        prices=prices,
        lowest_price=lowest,
        buy_links=buy_links,
    )

    logger.info(
        "Research pipeline complete: score=%d fake_pct=%d%%",
        verdict.verdict_score,
        verdict.fake_review_pct,
    )
    return verdict
