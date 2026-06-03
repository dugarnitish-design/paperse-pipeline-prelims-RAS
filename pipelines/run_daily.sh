#!/usr/bin/env bash
# PaperSe Daily CA — run the daily chain: fetch IE PDF → daily CA → MCQs → PDFs → Telegram.
# Usage:   ./pipelines/run_daily.sh [YYYY-MM-DD]   (defaults to today)
# Stops immediately if any step fails.
set -euo pipefail

DATE="${1:-$(date +%F)}"
cd "$(dirname "$0")/.."

# Calculate yesterday's date for IE PDF fetch
# macOS: date -v-1d +%F  |  Linux: date -d 'yesterday' +%F
NEWS_DATE=$(date -v-1d +%F 2>/dev/null || date -d 'yesterday' +%F)

# WeasyPrint (step 4) needs the Homebrew libs on the dynamic-loader path.
export DYLD_FALLBACK_LIBRARY_PATH="/opt/homebrew/lib:${DYLD_FALLBACK_LIBRARY_PATH:-}"

echo "###############################################################"
echo "#  PaperSe Daily CA Pipeline"
echo "#  Label date (for output): $DATE"
echo "#  News date (for fetching): $NEWS_DATE"
echo "###############################################################"

echo; echo ">>> STEP 1: fetch_ie_pdf.py"
python3 pipelines/fetch_ie_pdf.py "$NEWS_DATE"

echo; echo ">>> STEP 2: daily_ca_pipeline.py"
python3 pipelines/daily_ca_pipeline.py "$DATE"

echo; echo ">>> STEP 3: mcq_generator.py"
python3 pipelines/mcq_generator.py "$DATE"

echo; echo ">>> STEP 4: pdf_generator.py"
python3 pipelines/pdf_generator.py "$DATE"

echo; echo ">>> STEP 5: telegram_delivery.py"
python3 pipelines/telegram_delivery.py "$DATE"

echo; echo "###############################################################"
echo "#  DONE — outputs/daily-ca/EN/$DATE.pdf  &  HI/$DATE.pdf"
echo "#  ✓ Telegram delivery complete"
echo "###############################################################"
