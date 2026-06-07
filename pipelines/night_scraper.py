#!/usr/bin/env python3
"""
Combined nightly scraper — run on the Mac at 23:30 IST (via launchd) to capture the
day's PIB + IE content into Supabase, so the Railway pipeline (00:00 IST / 18:30 UTC
cron) reads from cache instead of scraping from a cloud IP (which IE/PIB block).

  python3 pipelines/night_scraper.py [YYYY-MM-DD]   # defaults to today (IST)

For each source it scrapes the given day's articles and upserts to Supabase:
  • PIB → pib_cache   (English National releases)
  • IE  → ie_cache    (india / explained / world / sports[awards] / science + homepage)
Logs how many were captured/saved per source. Never crashes — a failure in one
source is logged and the other still runs; always exits 0.
"""
import sys
import datetime
import pathlib
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from pipelines import _common as C

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _today_ist():
    return datetime.datetime.now(IST).date()


def main(date=None):
    date = C.parse_date(date) if isinstance(date, str) else (date or _today_ist())
    C.log("=" * 64)
    C.log(f"NIGHT SCRAPER — scraping {date.isoformat()} "
          f"(now IST {datetime.datetime.now(IST):%Y-%m-%d %H:%M})")
    C.log("=" * 64)

    pib_n = pib_saved = ie_n = ie_saved = 0

    # ── PIB ───────────────────────────────────────────────────────────────────
    try:
        from pipelines import pib_scraper
        items = pib_scraper.scrape_pib(date)
        pib_n = len(items)
        pib_saved = pib_scraper.write_supabase(date, items) if items else 0
        C.log(f"   ✓ PIB: scraped {pib_n}, saved {pib_saved} → pib_cache")
    except Exception as e:
        C.log(f"   ✗ PIB failed (continuing with IE): {e}")
        traceback.print_exc()

    # ── IE ────────────────────────────────────────────────────────────────────
    try:
        from pipelines import ie_scraper
        items = ie_scraper.fetch_ie_articles(date, force=True)   # live scrape (ignore cache)
        ie_n = len(items)
        ie_saved = ie_scraper.write_supabase(date, items) if items else 0
        C.log(f"   ✓ IE: scraped {ie_n}, saved {ie_saved} → ie_cache")
    except Exception as e:
        C.log(f"   ✗ IE failed: {e}")
        traceback.print_exc()

    C.log("-" * 64)
    C.log(f"NIGHT SCRAPER DONE — PIB {pib_saved}/{pib_n} · IE {ie_saved}/{ie_n} "
          f"for {date.isoformat()}")
    return {"date": date.isoformat(), "pib": pib_saved, "ie": ie_saved}


if __name__ == "__main__":
    arg = next((a for a in sys.argv[1:] if not a.startswith("-")), None)
    try:
        main(arg)
    except Exception as e:
        C.log(f"NIGHT SCRAPER fatal: {e}")
        traceback.print_exc()
    sys.exit(0)   # always 0 — launchd should not flag a partial-source failure
