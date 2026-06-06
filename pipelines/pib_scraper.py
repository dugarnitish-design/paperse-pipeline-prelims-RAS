#!/usr/bin/env python3
"""
PIB (Press Information Bureau) press-release scraper — PURE HTTP (no browser).

WHY HTTP NOW (was headed Playwright/Chromium)
  The English All-Releases list (Allrel.aspx?reg=3&lang=1) is now served as
  plain HTML over an ordinary GET — no Akamai wall and no JS/__doPostBack needed
  for the CURRENT day's releases. We fetch that list, read each release's PRID +
  title, then fetch its English body from PressReleasePage.aspx?PRID= over the
  same requests Session. No Chromium, no display — runs anywhere, incl. Railway.

  The RSS feeds (RssMain.aspx) are Hindi-only (no English ModId/Lang), so we do
  NOT use them — the English content lives only on the HTML list.

COVERAGE / WHY THIS IS AN END-OF-DAY PRODUCER
  A plain GET returns only the *current day's* releases; past dates need an
  ASP.NET cascading-dropdown postback that isn't reliable over HTTP. So run this
  once near end-of-day (Railway cron ~23:30 IST / 18:00 UTC) with --write-supabase
  to capture the full day into Supabase pib_cache (keyed by date). The 6:30 AM
  pipeline then reads *yesterday's* releases from that cache (fetch_pib reads
  pib_cache WHERE published_date == news_date).

USAGE
  python3 pipelines/pib_scraper.py --write-supabase            # capture TODAY (producer)
  python3 pipelines/pib_scraper.py --date 2026-06-07 --write-supabase
  python3 pipelines/pib_scraper.py 2026-06-08                  # pipeline date → news = day before

  As a library (daily_ca_pipeline.fetch_pib uses the cache; fetch_via_rss is the
  last-resort live fallback, now HTTP-based):
    from pipelines import pib_scraper
    items = pib_scraper.scrape_pib(date)   # list[{source,title,text,url}]

Item shape (filter reads item["text"]):
  {"source":"PIB","title":<headline>,"text":<headline + body>,"url":<url>}
"""
import re
import sys
import json
import html
import time
import datetime
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from pipelines import _common as C

try:
    import requests
except ImportError as e:  # pragma: no cover
    raise SystemExit(f"pib_scraper needs requests: {e}")
try:
    from bs4 import BeautifulSoup
except ImportError as e:  # pragma: no cover
    raise SystemExit(f"pib_scraper needs beautifulsoup4: {e}")


BASE = "https://pib.gov.in"
# reg=3 → National, lang=1 → English.
LIST_URL = f"{BASE}/Allrel.aspx?reg=3&lang=1"
CACHE_DIR = C.ROOT / "inputs" / "pib_cache"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

MAX_ITEMS = 150          # safety cap on releases per day
HTTP_TIMEOUT = 30        # seconds per request
BODY_GAP = 0.25          # politeness delay between body fetches
MIN_BODY_WORDS = 12      # below this a "release" is a stub / nav noise

# Body container on PressReleasePage.aspx (note the site's own "innner" typo).
BODY_DIV_SEL = "div.innner-page-main-about-us-content-right-part"

_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}


# ──────────────────────────────────────────────────────────────────────────────
# http helpers
# ──────────────────────────────────────────────────────────────────────────────
def _session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    return s


def _get_text(session, url):
    r = session.get(url, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.content.decode("utf-8", "ignore")


def _cache_path(date):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{date.isoformat()}.json"


# ──────────────────────────────────────────────────────────────────────────────
# parsing
# ──────────────────────────────────────────────────────────────────────────────
def _list_releases(list_html):
    """(prid, title, url) for every release link in the English list, deduped by PRID."""
    soup = BeautifulSoup(list_html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=re.compile(r"PRID=\d+")):
        m = re.search(r"PRID=(\d+)", a.get("href", ""))
        if not m:
            continue
        prid = m.group(1)
        if prid in seen:
            continue
        title = html.unescape(" ".join(a.get_text(" ", strip=True).split()))
        if len(title) < 15:                     # skip stray/empty anchors
            continue
        seen.add(prid)
        out.append({
            "prid": prid,
            "title": title,
            "url": f"{BASE}/PressReleasePage.aspx?PRID={prid}",
        })
        if len(out) >= MAX_ITEMS:
            break
    return out


def _parse_body(body_html):
    """Extract (title, body, item_date) from a PressReleasePage.aspx response.
    item_date is the parsed release date (datetime.date) or None if not found."""
    soup = BeautifulSoup(body_html, "html.parser")
    div = soup.select_one(BODY_DIV_SEL)
    if div is None:
        return None, "", None

    h2 = div.find("h2")
    title = html.unescape(" ".join(h2.get_text(" ", strip=True).split())) if h2 else ""

    # date stamp, e.g. "… 06 JUN 2026 7:11PM by PIB Delhi"
    item_date = None
    date_el = div.select_one("[class*='ReleaseDateSubHeaddateTime']") or div
    dm = re.search(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})", date_el.get_text(" ", strip=True))
    if dm:
        mon = _MONTHS.get(dm.group(2).upper())
        if mon:
            try:
                item_date = datetime.date(int(dm.group(3)), mon, int(dm.group(1)))
            except ValueError:
                pass

    paras = [html.unescape(" ".join(p.get_text(" ", strip=True).split()))
             for p in div.find_all("p")]
    body = " ".join(p for p in paras if p).strip()
    return title, body, item_date


# ──────────────────────────────────────────────────────────────────────────────
# scraping (HTTP only)
# ──────────────────────────────────────────────────────────────────────────────
def scrape_pib(date, max_items=MAX_ITEMS):
    """Scrape PIB National/English press releases dated `date` from the live
    English list over plain HTTP. Returns list[{source,title,text,url}] — the
    shape daily_ca_pipeline expects (filter reads item['text']).

    NOTE: only the CURRENT day is reachable over HTTP, so calling for a past date
    returns []. Run as an end-of-day producer (see module docstring). Returns []
    on any failure so the pipeline degrades gracefully."""
    if isinstance(date, str):
        date = C.parse_date(date)

    s = _session()
    C.log(f"   PIB HTTP scrape — releases dated {date.isoformat()}")
    try:
        listing = _list_releases(_get_text(s, LIST_URL))
    except Exception as e:
        C.log(f"   ⚠ PIB list fetch failed: {e}")
        return []

    C.log(f"      · {len(listing)} release links listed; fetching bodies…")
    items = []
    for rel in listing[:max_items]:
        try:
            title, body, item_date = _parse_body(_get_text(s, rel["url"]))
        except Exception as e:
            C.log(f"   ⚠ PIB body fetch failed {rel['url']}: {e}")
            continue
        time.sleep(BODY_GAP)
        # Keep releases whose stamped date matches the target. If the date can't
        # be parsed (item_date is None) we keep it — the list is already scoped
        # to the current day, so this only affects odd/unstamped pages.
        if item_date is not None and item_date != date:
            continue
        if len(body.split()) < MIN_BODY_WORDS:
            continue
        title = title or rel["title"]
        items.append({
            "source": "PIB",
            "title": title,
            "text": f"{title}. {body}",
            "url": rel["url"],
        })

    C.log(f"   PIB HTTP scrape → {len(items)} releases for {date.isoformat()}")
    return items


def fetch_via_rss(date, max_items=MAX_ITEMS):
    """Backward-compat alias kept for daily_ca_pipeline.fetch_pib's last-resort
    live fallback. The Hindi RSS feed was dropped; this now delegates to the HTTP
    scrape (current day only)."""
    return scrape_pib(date, max_items=max_items)


# ──────────────────────────────────────────────────────────────────────────────
def write_supabase(date, items):
    """Producer side: upsert a freshly-scraped day into Supabase `pib_cache`
    (keyed by PRID) so the pipeline / Railway can read it. Returns rows upserted."""
    rows = []
    for it in items:
        m = re.search(r"PRID=(\d+)", it.get("url", ""))
        if not m:
            continue
        rows.append({
            "prid": m.group(1),
            "title": it.get("title") or "",
            "text": it.get("text") or "",
            "url": it.get("url") or "",
            "published_date": date.isoformat(),
        })
    if rows:
        try:
            C.sb_upsert("pib_cache", rows, on_conflict="prid")
        except Exception as e:
            C.log(f"   ⚠ PIB Supabase upsert failed: {e}")
            return 0
    C.log(f"   PIB → Supabase pib_cache: upserted {len(rows)} rows for {date.isoformat()}")
    return len(rows)


if __name__ == "__main__":
    # End-of-day producer. Default (no date) = TODAY, since HTTP only reaches the
    # current day. --date YYYY-MM-DD overrides; a bare positional is treated as a
    # pipeline date (news = day before) for backward compat with run_daily.sh.
    date_flag = None
    if "--date" in sys.argv:
        i = sys.argv.index("--date")
        if i + 1 < len(sys.argv):
            date_flag = sys.argv[i + 1]
    if date_flag is None:
        date_flag = next((a.split("=", 1)[1] for a in sys.argv[1:]
                          if a.startswith("--date=")), None)
    if date_flag:
        news_date = C.parse_date(date_flag)
    else:
        arg = next((a for a in sys.argv[1:] if not a.startswith("--")), None)
        news_date = (C.parse_date(arg) - datetime.timedelta(days=1)) if arg \
            else datetime.date.today()

    rels = scrape_pib(news_date)
    if "--write-supabase" in sys.argv and rels:
        write_supabase(news_date, rels)
    print(json.dumps(rels, ensure_ascii=False, indent=2))
    print(f"\n# {len(rels)} PIB releases for {news_date.isoformat()}", file=sys.stderr)
    sys.exit(0 if rels else 1)
