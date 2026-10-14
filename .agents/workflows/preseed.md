---
description: Pre-scrape and score a batch of viral products into Supabase using free tiers
---

Run `scripts/preseed_catalog.py` for the category specified by the user. Use Reddit JSON + Tavily + Jina Reader + Groq/Gemini free tier with a 4-second delay between requests so we never trip the 15 RPM free-tier limit. Verify that embeddings and scores are written to Supabase.