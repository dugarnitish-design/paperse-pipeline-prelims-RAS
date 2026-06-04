#!/usr/bin/env bash
# PaperSe Daily CA — run the daily chain: scrape IE → daily CA → MCQs → PDFs → Telegram.
# Usage:   ./pipelines/run_daily.sh [YYYY-MM-DD]   (defaults to today)
# Stops immediately if any step fails.
set -euo pipefail

DATE="${1:-$(date +%F)}"
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
python3 pipelines/ie_scraper.py "$DATE" >/dev/null

echo; echo ">>> STEP 1: pib_scraper.py  (LOCAL headed scrape → Supabase pib_cache)"
# Headed Playwright scrape of yesterday's PIB releases, upserted to Supabase so
# the pipeline (and Railway) can read them without a browser. Best-effort: never
# blocks the chain (|| true) — fetch_pib falls back to local cache / RSS.
python3 pipelines/pib_scraper.py --date "$NEWS_DATE" --write-supabase >/dev/null || true

echo; echo ">>> STEP 2: daily_ca_pipeline.py"
python3 pipelines/daily_ca_pipeline.py "$DATE"

echo; echo ">>> STEP 3: mcq_generator.py"
python3 pipelines/mcq_generator.py "$DATE"

echo; echo ">>> STEP 4: pdf_generator.py"
python3 pipelines/pdf_generator.py "$DATE"

echo; echo ">>> STEP 5: curator_workflow.py  (save draft + send Telegram approval)"
python3 pipelines/curator_workflow.py "$DATE"

echo; echo ">>> STEP 6: pyq_poll_bot.py  (post 2 PYQ quiz polls to the channel)"
# Best-effort: never blocks the chain. Polls match the label date ($DATE = today's
# published PDF / daily_ca_items), not $NEWS_DATE (yesterday's news).
python3 pipelines/pyq_poll_bot.py --date "$DATE" || true

echo; echo "###############################################################"
echo "#  DONE — outputs/daily-ca/EN/$DATE.pdf  &  HI/$DATE.pdf"
echo "#  ✓ Draft sent for approval via Telegram"
echo "#  ✓ Auto-publishes at 8:30 AM IST if no response"
echo "#    Run manually: python3 pipelines/curator_auto_publish.py $DATE"
echo "###############################################################"
