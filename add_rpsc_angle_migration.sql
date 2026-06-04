-- ============================================================
-- PaperSe — add rpsc_angle to daily_ca_items (Supabase)
-- Applied to project 'paperse' (nunbpwaxqqgfxrosqfhw) via migration
-- 'add_rpsc_angle_to_daily_ca_items'. Stores the exam-coach "RPSC Angle"
-- line authored by gen_main(); rendered as an italic line in the PDF.
-- ============================================================
ALTER TABLE public.daily_ca_items
    ADD COLUMN IF NOT EXISTS rpsc_angle TEXT;
