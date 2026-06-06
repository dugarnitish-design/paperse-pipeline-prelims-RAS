-- FIX 7 — session-dedup RAG guard.
-- Applied to Supabase project nunbpwaxqqgfxrosqfhw on 2026-06-06
-- (migration name: add_item_id_session_to_curator_feedback). Kept here for reproducibility.
--
-- Lets curator_learning skip a duplicate RAG write (category tier_weight + topic
-- rejection_count) when the same item is rejected more than once within the same
-- curation session. curator_session_id = the draft date, so the guard survives
-- page reloads and multiple sittings on the same draft.

ALTER TABLE public.curator_feedback
  ADD COLUMN IF NOT EXISTS item_id text,
  ADD COLUMN IF NOT EXISTS curator_session_id text;

CREATE INDEX IF NOT EXISTS idx_curator_feedback_session_item
  ON public.curator_feedback (curator_session_id, item_id, action);
