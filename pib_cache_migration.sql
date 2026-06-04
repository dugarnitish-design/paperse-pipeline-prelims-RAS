-- ============================================================
-- PaperSe — PIB cache table (Supabase)
-- Applied to project 'paperse' (nunbpwaxqqgfxrosqfhw) via migration
-- 'create_pib_cache'. Kept here for version control / re-apply.
-- Run in Supabase SQL Editor if you need to recreate it.
-- ============================================================
-- PIB releases are scraped LOCALLY (headed Playwright on Allrel.aspx — Akamai
-- blocks headless / datacenter IPs, so Railway cannot scrape). The local producer
-- (pipelines/pib_scraper.py --write-supabase) upserts each release here; the
-- pipeline's fetch_pib() reads this table first so Railway gets PIB without a
-- browser. PK on prid makes re-runs idempotent and collapses same-day duplicates.

CREATE TABLE IF NOT EXISTS public.pib_cache (
    prid           TEXT        PRIMARY KEY,   -- PRID parsed from the release URL
    title          TEXT        NOT NULL,
    text           TEXT,                        -- title + body (pipeline filters/authors on this)
    url            TEXT,
    published_date DATE,                         -- the release date (news_date)
    scraped_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS pib_cache_published_date_idx
    ON public.pib_cache (published_date);
