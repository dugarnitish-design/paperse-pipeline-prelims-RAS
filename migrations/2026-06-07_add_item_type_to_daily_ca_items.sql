-- FIX 6 — content-type tag per daily CA item ('main' = full writeup, 'also' = one-liner).
-- Applied to Supabase project nunbpwaxqqgfxrosqfhw on 2026-06-07
-- (migration: add_item_type_to_daily_ca_items). Kept here for reproducibility.
ALTER TABLE public.daily_ca_items
  ADD COLUMN IF NOT EXISTS item_type text;

UPDATE public.daily_ca_items
  SET item_type = CASE WHEN is_main THEN 'main' ELSE 'also' END
  WHERE item_type IS NULL;
