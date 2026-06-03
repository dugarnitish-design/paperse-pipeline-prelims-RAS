-- ============================================================
-- PaperSe Curator Layer — Supabase Migrations
-- Run in Supabase SQL Editor (Dashboard → SQL Editor → New Query)
-- ============================================================

-- ── 1. curator_drafts (stores pipeline draft state) ─────────
CREATE TABLE IF NOT EXISTS curator_drafts (
    date             DATE        PRIMARY KEY,
    items            JSONB       NOT NULL DEFAULT '[]',
    status           TEXT        NOT NULL DEFAULT 'pending'
                                 CHECK (status IN ('pending','approved','published','auto_published')),
    timeout_at       TIMESTAMPTZ,
    telegram_message_id BIGINT,
    telegram_chat_id TEXT,
    approval_method  TEXT,
    selected_indices JSONB,
    created_at       TIMESTAMPTZ DEFAULT now(),
    updated_at       TIMESTAMPTZ DEFAULT now(),
    approved_at      TIMESTAMPTZ,
    published_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_curator_drafts_status ON curator_drafts(status);


-- ── 2. curator_feedback (stores per-item approvals/rejections) ──
CREATE TABLE IF NOT EXISTS curator_feedback (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    date            DATE        NOT NULL,
    item_title      TEXT        NOT NULL,
    category        TEXT        NOT NULL DEFAULT '',
    action          TEXT        NOT NULL
                                CHECK (action IN ('approved','rejected','replaced')),
    auto_published  BOOLEAN     NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_curator_feedback_date     ON curator_feedback(date);
CREATE INDEX IF NOT EXISTS idx_curator_feedback_category ON curator_feedback(category);
CREATE INDEX IF NOT EXISTS idx_curator_feedback_action   ON curator_feedback(action);


-- ── 3. Add tier_weight to ca_categories ──────────────────────
ALTER TABLE ca_categories
    ADD COLUMN IF NOT EXISTS tier_weight        DECIMAL(4,3) NOT NULL DEFAULT 1.0
        CHECK (tier_weight BETWEEN 0.0 AND 1.0);

ALTER TABLE ca_categories
    ADD COLUMN IF NOT EXISTS rejection_examples JSONB DEFAULT '{"examples": [], "count": 0}';


-- ── 4. Add rejection tracking to topic_kb ────────────────────
ALTER TABLE topic_kb
    ADD COLUMN IF NOT EXISTS rejection_count    INTEGER      NOT NULL DEFAULT 0;

ALTER TABLE topic_kb
    ADD COLUMN IF NOT EXISTS last_rejection_date TIMESTAMPTZ DEFAULT NULL;


-- ── 5. Seed initial values ────────────────────────────────────
UPDATE ca_categories
SET tier_weight = 1.0
WHERE tier_weight IS NULL;

UPDATE ca_categories
SET rejection_examples = '{"examples": [], "count": 0}'
WHERE rejection_examples IS NULL;

UPDATE topic_kb
SET rejection_count = 0
WHERE rejection_count IS NULL;


-- ── 6. Verification (run this to confirm) ─────────────────────
/*
SELECT 'curator_drafts'    AS tbl, COUNT(*) FROM curator_drafts
UNION ALL
SELECT 'curator_feedback'  AS tbl, COUNT(*) FROM curator_feedback
UNION ALL
SELECT 'ca_categories.tier_weight exists' AS tbl,
       COUNT(*) FROM information_schema.columns
       WHERE table_name='ca_categories' AND column_name='tier_weight'
UNION ALL
SELECT 'topic_kb.rejection_count exists' AS tbl,
       COUNT(*) FROM information_schema.columns
       WHERE table_name='topic_kb' AND column_name='rejection_count';
*/
