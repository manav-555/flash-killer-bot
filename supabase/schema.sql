-- ══════════════════════════════════════════════════════════════════════════════
-- Flash Killer Bot — Supabase Schema Migration
-- Run this in: Supabase Dashboard → SQL Editor
-- ══════════════════════════════════════════════════════════════════════════════

-- 1. Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- ══════════════════════════════════════════════════════════════════════════════
-- 2. products table
--    Stores indexed products with their AI-generated verdict data
--    and a 768-dim embedding for pgvector similarity search.
-- ══════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS products (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    brand               TEXT            NOT NULL,
    name                TEXT            NOT NULL,
    category            TEXT            NOT NULL DEFAULT 'skincare',
    verdict_score       SMALLINT        NOT NULL CHECK (verdict_score BETWEEN 0 AND 100),
    fake_review_pct     SMALLINT        NOT NULL DEFAULT 0 CHECK (fake_review_pct BETWEEN 0 AND 100),
    pros                JSONB           NOT NULL DEFAULT '[]'::JSONB,  -- TEXT[]
    cons                JSONB           NOT NULL DEFAULT '[]'::JSONB,  -- TEXT[]
    active_ingredients  TEXT[]          NOT NULL DEFAULT '{}',
    lowest_price        NUMERIC(10, 2)  NULL,
    buy_links           JSONB           NOT NULL DEFAULT '{}'::JSONB,  -- {store: url}
    embedding           vector(768)     NULL,   -- gemini-embedding-001, 768-dim
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- Indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_products_brand_name   ON products (brand, name);
CREATE INDEX IF NOT EXISTS idx_products_category     ON products (category);
CREATE INDEX IF NOT EXISTS idx_products_score        ON products (verdict_score DESC);
-- IVFFlat index for cosine similarity search (tune lists=100 for ~100k rows)
CREATE INDEX IF NOT EXISTS idx_products_embedding    ON products
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 50);

-- Auto-update updated_at on row change
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_products_updated_at ON products;
CREATE TRIGGER trg_products_updated_at
    BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ══════════════════════════════════════════════════════════════════════════════
-- 3. reel_cache table
--    Maps an Instagram Reel video_id to a pre-computed verdict response.
--    Fast Path: if reel_video_id exists here → send cached DM immediately.
-- ══════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS reel_cache (
    reel_video_id           TEXT        PRIMARY KEY,
    product_id              UUID        REFERENCES products(id) ON DELETE SET NULL,
    creator_claim           TEXT        NULL,
    sponsorship_likelihood  TEXT        NULL,  -- "high" | "medium" | "low"
    formatted_dm_response   TEXT        NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reel_cache_product_id  ON reel_cache (product_id);
CREATE INDEX IF NOT EXISTS idx_reel_cache_created_at  ON reel_cache (created_at DESC);

-- ══════════════════════════════════════════════════════════════════════════════
-- 4. RPC: find_smarter_alternative
--    Finds a product in the same category with:
--      - higher verdict_score than the current product
--      - lower price than the current product (if price is available)
--    Uses pgvector cosine similarity to pick the closest match semantically.
-- ══════════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION find_smarter_alternative(
    target_embedding    vector(768),
    target_category     TEXT,
    current_score       INT,
    current_price       NUMERIC
)
RETURNS TABLE (
    id              UUID,
    brand           TEXT,
    name            TEXT,
    verdict_score   SMALLINT,
    lowest_price    NUMERIC,
    buy_links       JSONB,
    similarity      FLOAT
)
LANGUAGE sql STABLE AS $$
    SELECT
        p.id,
        p.brand,
        p.name,
        p.verdict_score,
        p.lowest_price,
        p.buy_links,
        1 - (p.embedding <=> target_embedding) AS similarity
    FROM products p
    WHERE
        p.category = target_category
        AND p.verdict_score > current_score
        AND (
            current_price IS NULL
            OR p.lowest_price IS NULL
            OR p.lowest_price < current_price
        )
        AND p.embedding IS NOT NULL
    ORDER BY
        similarity DESC,
        p.verdict_score DESC
    LIMIT 3;
$$;

-- ══════════════════════════════════════════════════════════════════════════════
-- 5. Row Level Security (RLS) — enable but allow service_role bypass
-- ══════════════════════════════════════════════════════════════════════════════
ALTER TABLE products    ENABLE ROW LEVEL SECURITY;
ALTER TABLE reel_cache  ENABLE ROW LEVEL SECURITY;

-- Service role (used by our backend) bypasses RLS automatically in Supabase.
-- Public read policy so the anon key can SELECT (for future public API use):
CREATE POLICY "Public read products" ON products
    FOR SELECT USING (true);

CREATE POLICY "Public read reel_cache" ON reel_cache
    FOR SELECT USING (true);
