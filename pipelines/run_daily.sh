#!/usr/bin/env bash
# PaperSe Daily CA — run the daily chain: fetch IE PDF → daily CA → MCQs → PDFs → Telegram.
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
echo "#  IE PDF date: $DATE (today) | PIB date: $NEWS_DATE (yesterday)"
echo "###############################################################"

echo; echo ">>> STEP 1: fetch_ie_pdf.py"
python3 pipelines/fetch_ie_pdf.py "$DATE"

echo; echo ">>> STEP 2: daily_ca_pipeline.py"
python3 pipelines/daily_ca_pipeline.py "$DATE"

echo; echo ">>> STEP 3: mcq_generator.py"
python3 pipelines/mcq_generator.py "$DATE"

echo; echo ">>> STEP 4: pdf_generator.py"
python3 pipelines/pdf_generator.py "$DATE"

echo; echo ">>> STEP 5: curator_workflow.py  (save draft + send Telegram approval)"
python3 pipelines/curator_workflow.py "$DATE"

echo; echo "###############################################################"
echo "#  DONE — outputs/daily-ca/EN/$DATE.pdf  &  HI/$DATE.pdf"
echo "#  ✓ Draft sent for approval via Telegram"
echo "#  ✓ Auto-publishes at 8:30 AM IST if no response"
echo "#    Run manually: python3 pipelines/curator_auto_publish.py $DATE"
echo "###############################################################"
