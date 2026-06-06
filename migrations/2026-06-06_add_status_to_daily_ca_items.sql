-- FIX 6 — also-in-news / rejected item preservation.
-- Applied to Supabase project nunbpwaxqqgfxrosqfhw on 2026-06-06
-- (migration name: add_status_to_daily_ca_items). Kept here for reproducibility.
--
-- Lifecycle of a daily_ca_items row:
--   'pending'       — default, fresh from the pipeline (pre-curation)
--   'published'     — curator kept/promoted it; is_main=true; goes to channel + site
--   'also_in_news'  — un-promoted also-in-news; is_main=false; kept (was deleted before)
--   'rejected'      — curator unchecked a main; is_main=false; kept for audit/RAG,
--                     filtered out of the PDF also-section
-- Nothing is ever hard-deleted from daily_ca_items anymore.

ALTER TABLE public.daily_ca_items
  ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'pending';

CREATE INDEX IF NOT EXISTS idx_daily_ca_items_date_status
  ON public.daily_ca_items (date, status);
