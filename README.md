<div align="center">

# 🔍 Flash Killer Bot

### The AI Instagram DM "Hype-Check" Bot

**Users share an Instagram Reel → Bot replies in <5 seconds with a verified verdict**

*Identified brand · Creator claim reality-check · Reddit consensus score · Live prices · Smarter alternative*

---

[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Gemini 2.5 Flash](https://img.shields.io/badge/Gemini-2.5%20Flash-4285F4?logo=google&logoColor=white)](https://aistudio.google.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![$0 API Cost](https://img.shields.io/badge/API%20Cost-$0%20Free%20Tier-brightgreen)](https://github.com)

</div>

---

## What It Does

Flash Killer Bot is a direct competitor to [Flash.co](https://flash.co) ("Shop the top 1%"). Instead of making users visit a website, they simply **share any Instagram Reel** to the bot's DM inbox. Within **5 seconds**, the bot replies with a full de-influencing verdict:

```
🛍️ HYPE CHECK — The Ordinary Niacinamide 10% + Zinc 1%
──────────────────────────────────────────
🟡 Real Score: 74/100
⚠️ ~28% reviews may be incentivised

🚨 Sponsorship Alert: #gifted, Use code CREATOR20

🔬 Claim Check: Pore-shrinking is over-claimed; oil control is legitimate.

✅ Pros:
  ✅ Effective niacinamide concentration
  ✅ Budget-friendly
  ✅ Fragrance-free

❌ Cons:
  ❌ Glycerin can pill under SPF
  ❌ Creator claims are exaggerated

💰 Lowest Price: ₹499 on Amazon.in

🔄 Smarter Buy: Paula's Choice CLEAR Niacinamide (Score: 84/100, ₹1,299)
   → Higher community score, dermatologist-tested formula

📊 Based on 8 Reddit threads + verified reviews.
🤖 Powered by Flash Killer Bot
```

---

## Architecture

### Latency SLA

```
User shares Reel
      │
      ▼
[POST /webhook] ──── 200 OK immediately (FastAPI BackgroundTasks)
      │
      ▼
┌─────────────────────────────────────────────────────────────┐
│                    ROUTING WATERFALL                         │
├─────────────────────────────────────────────────────────────┤
│ Step 1 │ Upstash Redis  (reel_video_id key)    │  < 1.0s  │
│        │ → Cache HIT: send cached DM instantly  │          │
├────────┼────────────────────────────────────────┼──────────┤
│ Step 2 │ Supabase reel_cache table              │  < 1.0s  │
│        │ → DB HIT: send cached DM, back-fill    │          │
│        │   Redis for next request               │          │
├────────┼────────────────────────────────────────┼──────────┤
│ Step 3 │ Gemini 2.5 Flash multimodal extraction │  < 2.0s  │
│        │ → Video bytes + caption → brand/product │          │
├────────┼────────────────────────────────────────┼──────────┤
│ Step 4 │ Supabase products table lookup         │  < 3.0s  │
│        │ → DB HIT: format + send verdict DM     │          │
├────────┼────────────────────────────────────────┼──────────┤
│ Step 5 │ Cold-Start: send "New product spotted" │  < 10s   │
│        │ ack DM → run full research pipeline     │          │
└─────────────────────────────────────────────────┴──────────┘
```

### $0 Free-Tier Research Pipeline (Cold-Start)

```
Step A ──→ Reddit public JSON search (0 credits, always free)
           reddit.com/search.json?q={product}+review&limit=8

Step B ──→ Tavily Search API (1,000 free requests/month)
           Targets: reddit.com + incidecoder.com

Step C ──→ Jina Reader (500 RPM free)
           r.jina.ai/{url} → clean Markdown from static pages

Step D ──→ Firecrawl /scrape (anti-bot pages ONLY)
           Nykaa · Amazon.in · Tira · Sephora
           ⚠️  NEVER use /extract endpoint (costs 5 credits)

Step E ──→ Serper.dev Shopping API (live Indian marketplace prices)
           Nykaa · Amazon.in · Flipkart · Tira · Myntra

Step F ──→ LLM Scoring Waterfall
           Primary:  Gemini 2.5 Flash
           Fallback: Groq llama-3.3-70b-versatile (HTTP 429)
           Last:     OpenRouter :free models
```

---

## Project Structure

```
flash-killer-bot/
│
├── .env.example                  # All 14 env vars with comments
├── requirements.txt              # Pinned production dependencies
│
├── supabase/
│   └── schema.sql                # pgvector migration + tables + RPC
│
├── app/
│   ├── main.py                   # FastAPI app factory
│   ├── config.py                 # pydantic-settings Settings singleton
│   │
│   ├── api/
│   │   └── webhook.py            # GET + POST /webhook routes
│   │
│   ├── db/
│   │   └── supabase_client.py    # Async Supabase helpers
│   │
│   ├── models/
│   │   ├── reel.py               # ReelExtractionResult (Gemini output)
│   │   ├── verdict.py            # ProductVerdict (scoring output)
│   │   └── webhook.py            # Meta webhook payload models
│   │
│   └── services/
│       ├── reel_analyzer.py      # Gemini multimodal video extraction
│       ├── research_engine.py    # $0 free-tier scraper waterfall
│       └── ig_messenger.py       # Meta Graph API DM sender
│
└── tests/
    └── simulate_reel_webhook.py  # End-to-end webhook simulation (5 tests)
```

---

## Tech Stack

| Layer | Technology | Cost |
|-------|-----------|------|
| API Framework | FastAPI + Uvicorn | Free |
| Multimodal AI | Gemini 2.5 Flash (`google-genai`) | Free tier |
| Embeddings | `gemini-embedding-001` (768-dim) | Free tier |
| LLM Fallback 1 | Groq `llama-3.3-70b-versatile` | Free tier |
| LLM Fallback 2 | OpenRouter `:free` models | Free |
| Review Search | Reddit public JSON API | Free (0 credits) |
| Targeted Search | Tavily API | 1,000 req/mo free |
| Content Reader | Jina Reader (`r.jina.ai`) | 500 RPM free |
| JS Page Scraper | Firecrawl `/scrape` | Free tier |
| Price Lookup | Serper.dev Google Shopping | Free tier |
| Vector DB | Supabase + pgvector | Free tier |
| Cache | Upstash Redis (REST) | Free tier |
| Webhook | Meta Graph API v26.0 | Free |
| HTTP Client | `httpx` (async) | Free |

**Total Monthly Infrastructure Cost: $0** (using all free tiers)

---

## Quick Start

### Prerequisites

- Python 3.11+
- A [Meta App](https://developers.facebook.com) with Instagram API enabled
- Free accounts on: Supabase, Upstash, Google AI Studio, Groq, Tavily, Serper.dev

### 1. Clone & Install

```bash
git clone https://github.com/yourname/flash-killer-bot.git
cd flash-killer-bot

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Open .env and fill in your API keys
```

| Variable | Where to get it |
|----------|----------------|
| `IG_VERIFY_TOKEN` | Any random string you set in Meta App Dashboard |
| `IG_PAGE_ACCESS_TOKEN` | [Meta Developers → Your App → Instagram → Generate Token](https://developers.facebook.com) |
| `IG_BUSINESS_ACCOUNT_ID` | Meta App Dashboard → Instagram → Account ID |
| `GEMINI_API_KEY` | [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) |
| `GROQ_API_KEY` | [console.groq.com/keys](https://console.groq.com/keys) |
| `OPENROUTER_API_KEY` | [openrouter.ai/keys](https://openrouter.ai/keys) |
| `JINA_API_KEY` | [jina.ai](https://jina.ai) (optional — boosts quality) |
| `TAVILY_API_KEY` | [tavily.com](https://tavily.com) |
| `SERPER_API_KEY` | [serper.dev](https://serper.dev) |
| `FIRECRAWL_API_KEY` | [firecrawl.dev](https://firecrawl.dev) |
| `SUPABASE_URL` | Supabase Dashboard → Project Settings → API |
| `SUPABASE_KEY` | Supabase Dashboard → Project Settings → API |
| `UPSTASH_REDIS_REST_URL` | [console.upstash.com](https://console.upstash.com) → Redis → REST API |
| `UPSTASH_REDIS_REST_TOKEN` | Same dashboard as above |

### 3. Set Up the Database

Run the migration in your **Supabase SQL Editor**:

```bash
# Copy the contents of supabase/schema.sql and paste into:
# Supabase Dashboard → SQL Editor → New Query → Run
```

This creates:
- `products` table with `vector(768)` column + IVFFlat cosine index
- `reel_cache` table for fast repeated-reel lookups
- `find_smarter_alternative()` PostgreSQL RPC function

### 4. Run Locally

```bash
uvicorn app.main:app --reload --port 8000
```

Visit [http://localhost:8000/docs](http://localhost:8000/docs) for the interactive API docs.

### 5. Expose Webhook (for local testing)

Meta needs to reach your `/webhook` endpoint. Use [ngrok](https://ngrok.com) or [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps):

```bash
# ngrok
ngrok http 8000

# Then in Meta App Dashboard → Webhooks:
# Callback URL: https://your-ngrok-id.ngrok-free.app/webhook
# Verify Token: (whatever you set as IG_VERIFY_TOKEN in .env)
```

---

## Testing

### Run the Simulation Suite

No real API keys needed — all services fall back to realistic mocks when keys are placeholders.

```bash
# Terminal 1 — start the server
uvicorn app.main:app --port 8000

# Terminal 2 — run the 5-test simulation
python tests/simulate_reel_webhook.py
```

**Expected output:**

```
[FLASH KILLER BOT] Webhook Simulation
  Target: http://localhost:8000

  TEST 5: GET /health
  ✅ Health endpoint OK

  TEST 1: GET /webhook — Hub Challenge Verification
  ✅ Challenge echoed correctly — webhook verified!

  TEST 2: GET /webhook — Invalid Token → 403
  ✅ Correctly rejected invalid token with 403

  TEST 3: POST /webhook — ig_reel Event (Full Pipeline)
  ✅ 200 OK received — webhook acknowledged within time limit
  ✅ Response time 371ms — within production SLA (<500ms)
  ✅ Response body contains {status: ok}

  TEST 4: POST /webhook — Non-Instagram Event → ignored
  ✅ Non-Instagram event correctly ignored with 200

  ALL TESTS PASSED — 5/5
```

### Test a Real Reel

Once your ngrok URL is registered as a Meta webhook:

1. Open Instagram on any account
2. Find any product-review Reel
3. Tap **Share → Send to [Your Bot's IG Username]**
4. Watch the DM reply arrive in <5 seconds

---

## Database Schema

```sql
-- Products with pgvector embeddings (768-dim, gemini-embedding-001)
products (
  id              UUID PRIMARY KEY,
  brand           TEXT,
  name            TEXT,
  category        TEXT,
  verdict_score   SMALLINT,      -- 0–100 real consensus score
  fake_review_pct SMALLINT,      -- % of incentivised reviews detected
  pros            JSONB,         -- evidence-backed positives
  cons            JSONB,         -- evidence-backed negatives
  active_ingredients TEXT[],
  lowest_price    NUMERIC,
  buy_links       JSONB,         -- {store: url}
  embedding       vector(768)    -- for pgvector similarity search
)

-- Fast cache: maps reel_video_id → pre-computed DM response
reel_cache (
  reel_video_id           TEXT PRIMARY KEY,
  product_id              UUID,
  creator_claim           TEXT,
  sponsorship_likelihood  TEXT,  -- "high" | "medium" | "low"
  formatted_dm_response   TEXT,
  created_at              TIMESTAMPTZ
)
```

**RPC function:** `find_smarter_alternative(embedding, category, current_score, current_price)` — finds a product in the same category with a higher score and lower price, ranked by cosine similarity.

---

## Deployment

### Deploy to Railway / Render / Fly.io

```bash
# Render: add a render.yaml or use the dashboard
# Start command:
uvicorn app.main:app --host 0.0.0.0 --port $PORT

# Environment: set APP_ENV=production to disable /docs
```

### Deploy to Google Cloud Run

```dockerfile
# Dockerfile (minimal)
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

```bash
gcloud run deploy flash-killer-bot \
  --source . \
  --region asia-south1 \
  --allow-unauthenticated \
  --set-env-vars "$(cat .env | tr '\n' ',')"
```

---

## Design Decisions

### Why not a website?
Instagram DMs have **zero friction**. Users don't need to install an app, visit a URL, or create an account. The bot meets them exactly where they already are, watching Reels.

### Why $0 infrastructure?
This is a bootstrapped product. All services used have free tiers generous enough for an MVP with thousands of monthly active users. The architecture is designed to scale into paid tiers only when revenue justifies it.

### Why pgvector for alternatives?
Text search (`ILIKE`) for the "Smarter Buy" feature would miss semantically similar products with different names. pgvector cosine similarity finds alternatives like *"CeraVe AM Facial Moisturiser"* when searching for *"SPF moisturiser for oily skin"* even if the exact words don't match.

### Why multiple LLM fallbacks?
Gemini 2.5 Flash has a generous free quota but can hit rate limits at scale. The Groq → OpenRouter chain ensures scoring never fails due to a single provider's limits, with zero additional cost.

---

## Roadmap

- [ ] **WhatsApp integration** — same bot, different channel via WhatsApp Business API
- [ ] **Pre-seeded product database** — use `/preseed` workflow to pre-index viral products
- [ ] **Affiliate link injection** — monetise buy buttons via Amazon Associates + Nykaa affiliate
- [ ] **Multi-language support** — Hindi, Tamil, Telugu verdict messages
- [ ] **Creator scoring** — per-creator hype score based on historical sponsorship rate
- [ ] **Web dashboard** — analytics on most-checked products, scores, and user trends

---

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss what you'd like to change.

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/smarter-alternative-v2`)
3. Commit your changes (`git commit -m 'feat: improve pgvector similarity scoring'`)
4. Push to the branch (`git push origin feature/smarter-alternative-v2`)
5. Open a Pull Request

---

## License

[MIT](LICENSE) — feel free to use this commercially.

---

<div align="center">
  Built with ❤️ to fight influencer hype with actual data.
</div>