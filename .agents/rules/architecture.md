---
trigger: always_on
---

---
trigger: always_on
description: Core architecture, $0 free-tier waterfall rules, and latency SLA for the Instagram DM AI Shopping Bot (Flash.co competitor).
---

# Project Context: Instagram/WhatsApp DM AI De-Influencing & Product Verdict Bot

## 1. Core Product Vision
We are building an AI shopping research competitor to Flash.co ("Shop the top 1%"). Instead of forcing users to visit a website, users share an Instagram Reel directly to our Instagram DM Bot. Within 5 seconds, the bot replies with:
- Identified Brand & Product Name
- Creator Claim vs. Reality Check + Sponsorship/Hype Warning
- Real Consensus Score (0-100) from Reddit & verified buyers
- "The Smarter Buy" (higher-rated or cheaper alternative via pgvector similarity)
- Live lowest price & affiliate buy buttons

## 2. Strict Latency SLA (< 5.0 Seconds Total)
- **Fast Path (< 1.0s):** Check Upstash Redis for `ig_reel_video_id`. If cached, immediately dispatch the cached verdict JSON via Meta Graph API (`0` LLM calls, `0` scraper calls).
- **Live DB Hit Path (< 3.0s):** Extract product from Reel video/caption using `gemini-2.5-flash`. Query Supabase `products` table (hybrid text + `pgvector`). If pre-indexed, format response deterministically and send DM.
- **Cold-Start Product Path (< 10.0s with immediate typing/status feedback):** Send immediate DM acknowledgement ("New product spotted! Checking Reddit & lab reviews... 🧪"), then execute the Free-Tier Scraper Waterfall asynchronously.

## 3. Strict $0 Free-Tier Waterfall (DO NOT use paid-only endpoints)
1. **Multimodal Video/Audio Extraction:** Use `google-genai` SDK with `gemini-2.5-flash` (AI Studio Free Tier). Pass the Instagram CDN video URL / frames + `title` caption to extract structured Pydantic JSON.
2. **Review & Forum Grounding Waterfall:**
   - **Step A (0 Credits):** Query Reddit Public JSON directly (`https://www.reddit.com/search.json?q={product}+review&limit=8`) with a custom `User-Agent` header.
   - **Step B (Free 1,000/mo):** Query Tavily Search API (`tvly-...`) targeting `reddit.com` and `incidecoder.com`.
   - **Step C (Free 500 RPM):** Use Jina Reader (`https://r.jina.ai/{url}`) to convert static blog/ingredient pages into clean Markdown.
   - **Step D (Heavy JS Fallback Only):** Use `firecrawl-py` (`app.scrape_url(url, params={'formats': ['markdown'], 'onlyMainContent': True})`) ONLY for anti-bot e-commerce product pages (Nykaa, Amazon, Tira, Sephora). **NEVER** use Firecrawl's `/extract` endpoint (costs 5 credits); always use `/scrape` (1 credit) and parse with Gemini/Groq.
3. **Live Price Lookup:** Use `Serper.dev` Google Shopping endpoint (`https://google.serper.dev/shopping`) to fetch multi-store prices in 1 API call.
4. **LLM Scoring & Fallback:** Use `gemini-2.5-flash` (primary) and `Groq` (`llama-3.3-70b-versatile`) or `OpenRouter` (`:free` models) as automatic rate-limit fallbacks when calculating the 0-100 score.

## 4. Coding & Stack Conventions
- **Framework:** Python 3.11+, FastAPI (async endpoints only), `httpx` for async HTTP calls, Pydantic v2 for strict structured outputs.
- **Database:** Supabase (`supabase-py` + PostgreSQL `pgvector` for semantic dupe/alternative matching).
- **Cache:** Upstash Redis (`upstash-redis` REST client).
- **Meta API:** Instagram API with Instagram Login (`v26.0`, `https://graph.instagram.com/v26.0/me/messages`).