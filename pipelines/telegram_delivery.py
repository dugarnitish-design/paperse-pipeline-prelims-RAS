#!/usr/bin/env python3
"""
STEP 5 — Post Daily CA PDFs to Telegram Channel.

  python3 pipelines/telegram_delivery.py 2026-06-02
  python3 pipelines/telegram_delivery.py 2026-06-02 --dry-run

Posts English and Hindi PDFs to t.me/papersecivils with formatted captions
including top 5 headlines and link to test.
"""
import sys, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipelines import _common as C


def build_caption(label_date, main_items):
    """Build Telegram caption with headline, top 5 items, and test URL.

    Args:
        label_date: datetime.date object
        main_items: list of dicts with 'title' and 'category' fields

    Returns:
        Formatted caption string
    """
    # Date header
    date_str = label_date.strftime("%d %B %Y")
    caption = f"📰 Current Affairs — {date_str}\n\n"

    # Top 5 main items with emojis
    for item in main_items[:5]:
        emoji = C.emoji_for(item.get("category", ""))
        title = item.get("title", "").strip()
        caption += f"{emoji} {title}\n"

    # Test URL
    test_url = f"paperse.in/test/{label_date.isoformat()}"
    caption += f"\n📖 Complete analysis: {test_url}"

    return caption


def post_to_telegram(label_date, dry_run=False):
    """Post EN and HI PDFs to Telegram channel with formatted captions.

    Args:
        label_date: datetime.date object (today's date for labeling)
        dry_run: if True, show caption without posting

    Returns:
        True if successful (or successful dry-run)
    """
    # Get Telegram credentials from .env
    bot_token = C.ENV.get("TELEGRAM_BOT_TOKEN")
    channel_id = C.ENV.get("TELEGRAM_CHANNEL_ID")

    if not bot_token or not channel_id:
        C.log(f"   ⚠ Telegram credentials missing (.env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID). Skipping.")
        return False

    # Get main items (top 5)
    try:
        # Main items = is_main=true. tier is a priority bucket (NOT main/also) —
        # main items carry tier 2/3, so the old tier=1 filter matched nothing.
        main_items = C.sb_select("daily_ca_items",
                                 params={
                                     "date": f"eq.{label_date.isoformat()}",
                                     "is_main": "eq.true",
                                     "language": "eq.EN",
                                     "order": "priority.desc",
                                     "limit": 5
                                 })
    except Exception as e:
        C.log(f"   ⚠ Failed to fetch main items from Supabase: {e}")
        return False

    if not main_items:
        C.log(f"   ⚠ No main items found for {label_date.isoformat()}")
        return False

    # Build caption
    caption = build_caption(label_date, main_items)

    # DRY RUN: just show caption
    if dry_run:
        C.log(f"\n   ─── DRY RUN: Would post to Telegram ───")
        C.log(f"\n   EN PDF caption:")
        C.log(caption)
        C.log(f"\n   HI PDF caption:")
        C.log(caption)
        C.log(f"\n   ─────────────────────────────────────\n")
        return True

    # Actually post to Telegram using requests
    try:
        import requests

        # Prepare file paths
        en_pdf = C.OUT_EN / f"{label_date.isoformat()}.pdf"
        hi_pdf = C.OUT_HI / f"{label_date.isoformat()}.pdf"

        if not en_pdf.exists():
            C.log(f"   ⚠ EN PDF not found: {en_pdf}")
            return False

        if not hi_pdf.exists():
            C.log(f"   ⚠ HI PDF not found: {hi_pdf}")
            return False

        # Telegram Bot API endpoint
        api_url = f"https://api.telegram.org/bot{bot_token}/sendDocument"

        # Post EN PDF
        C.log(f"   Posting EN PDF to Telegram...")
        with open(en_pdf, "rb") as f:
            files = {"document": f}
            data = {
                "chat_id": channel_id,
                "caption": caption,
                "parse_mode": "Markdown"
            }
            response = requests.post(api_url, files=files, data=data, timeout=30)
            if response.status_code != 200:
                C.log(f"   ⚠ EN PDF post failed: {response.text}")
                return False
        C.log(f"   ✓ EN PDF posted")

        # Post HI PDF with same caption
        C.log(f"   Posting HI PDF to Telegram...")
        with open(hi_pdf, "rb") as f:
            files = {"document": f}
            data = {
                "chat_id": channel_id,
                "caption": caption,
                "parse_mode": "Markdown"
            }
            response = requests.post(api_url, files=files, data=data, timeout=30)
            if response.status_code != 200:
                C.log(f"   ⚠ HI PDF post failed: {response.text}")
                return False
        C.log(f"   ✓ HI PDF posted")

        return True

    except Exception as e:
        C.log(f"   ⚠ Telegram posting failed: {e}")
        return False


if __name__ == "__main__":
    label_date = C.parse_date(sys.argv[1]) if len(sys.argv) > 1 else datetime.date.today()
    dry_run = "--dry-run" in sys.argv

    C.log("=" * 64)
    C.log(f"PaperSe Telegram Delivery — {label_date.isoformat()}")
    if dry_run:
        C.log("(DRY RUN MODE - no actual posting)")
    C.log("=" * 64)

    success = post_to_telegram(label_date, dry_run=dry_run)
    sys.exit(0 if success else 1)
