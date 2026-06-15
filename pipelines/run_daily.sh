#!/usr/bin/env bash
# PaperSe Daily CA — run the daily chain: scrape IE → daily CA → MCQs → PDFs → Telegram.
# Usage:   ./pipelines/run_daily.sh [YYYY-MM-DD]   (defaults to today)
# Stops immediately if any step fails.
set -euo pipefail

# IST so the 18:30-UTC (00:00 IST) cron picks the correct calendar day. Plain UTC
# `date` would still read "yesterday" at 18:30 UTC and shift news_date by one.
DATE="${1:-$(TZ=Asia/Kolkata date +%F)}"
cd "$(dirname "$0")/.."

# Calculate yesterday's date based on the label date (not system date)
# Use Python for cross-platform compatibility
NEWS_DATE=$(python3 -c "
import datetime
date_obj = datetime.datetime.strptime('$DATE', '%Y-%m-%d').date()
yesterday = date_obj - datetime.timedelta(days=1)
print(yesterday.isoformat())
")

# WeasyPrint (step 4) needs the Homebrew libs on the dynamic-loader path.
export DYLD_FALLBACK_LIBRARY_PATH="/opt/homebrew/lib:${DYLD_FALLBACK_LIBRARY_PATH:-}"

echo "###############################################################"
echo "#  PaperSe Daily CA Pipeline"
echo "#  Label date (for output): $DATE"
echo "#  IE scrape date: $NEWS_DATE (yesterday) | PIB date: $NEWS_DATE (yesterday)"
echo "###############################################################"

echo; echo ">>> STEP 0: ie_scraper.py  (scrape yesterday's IE articles from the website)"
# Best-effort: if IE blocks Railway's cloud IP or returns 0 articles, we log a
# warning and continue — daily_ca_pipeline (STEP 2) calls fetch_ie_articles()
# directly and falls back to PIB-only if the IE cache is empty.
python3 pipelines/ie_scraper.py "$DATE" >/dev/null || \
    echo "⚠ ie_scraper exited non-zero (IE may have blocked this IP or returned 0 articles — PIB-only fallback)"

echo; echo ">>> STEP 1: pib_scraper.py  (LOCAL headed scrape → Supabase pib_cache)"
# Headed Playwright scrape of yesterday's PIB releases, upserted to Supabase so
# the pipeline (and Railway) can read them without a browser. Best-effort: never
# blocks the chain (|| true) — fetch_pib falls back to local cache / RSS.
python3 pipelines/pib_scraper.py --date "$NEWS_DATE" --write-supabase >/dev/null || true

echo; echo ">>> STEP 2: daily_ca_pipeline.py"
python3 pipelines/daily_ca_pipeline.py "$DATE"

# NOTE: MCQ generation moved to PUBLISH time (curator_server._publish /
# curator_auto_publish), so MCQs are built from the final curated + published set
# (post-edit/promote/reject) instead of the pre-curation draft. No STEP 3 here.

echo; echo ">>> STEP 4: pdf_generator.py"
python3 pipelines/pdf_generator.py "$DATE"

echo; echo ">>> STEP 5: curator_workflow.py  (save draft + send Telegram approval)"
python3 pipelines/curator_workflow.py "$DATE"

echo; echo ">>> STEP 6: upload_pdfs.py  (upload EN+HI PDFs to Supabase → daily_pdfs)"
# Best-effort: registers the draft PDFs so paperse.in has something to serve even
# before curation. On curator publish, curator_server._publish() re-uploads the
# regenerated (curated) PDF so the website always matches the channel.
python3 pipelines/upload_pdfs.py "$DATE" || \
    echo "⚠ upload_pdfs exited non-zero (website PDF links may be stale — non-fatal)"

# NOTE: PYQ polls are NOT posted here anymore. They post from the curator
# publish path (curator_server._publish / curator_auto_publish) IMMEDIATELY
# after the PDF is delivered to the channel — so the PDF always lands first.

echo; echo ">>> STEP 7: scheduled_jobs.py  (Sun → weekly coverage report, 1st → monthly trends)"
python3 pipelines/scheduled_jobs.py || echo "⚠ scheduled_jobs exited non-zero (non-fatal)"

echo; echo "###############################################################"
echo "#  DONE — outputs/daily-ca/EN/$DATE.pdf  &  HI/$DATE.pdf"
echo "#  ✓ Draft sent for approval via Telegram"
echo "#  ✓ Auto-publishes at 6:00 AM IST if no response"
echo "#    Run manually: python3 pipelines/curator_auto_publish.py $DATE"
echo "###############################################################"
