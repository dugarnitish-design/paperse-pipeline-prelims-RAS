#!/usr/bin/env python3
"""
PIB (Press Information Bureau) press-release scraper.

WHY PLAYWRIGHT (and headed)
  pib.gov.in's All-Releases list (Allrel.aspx) is JS / ASP.NET __doPostBack
  rendered AND sits behind an Akamai bot wall:
    · a plain `requests` GET returns only page nav furniture (the same ~7 junk
      links — "CPIOs Appellate Authority List", "RTI Transparency Audit…" — for
      every date), NOT the release list;
    · a *headless* Chromium gets a hard "Access Denied" from Akamai.
  So we drive a HEADED Chromium with Playwright: first hit the homepage to pick
  up Akamai's clearance cookies, then open Allrel.aspx?reg=3&lang=1 (reg=3 =
  National, lang=1 = English) and pick the target day from the year / month / day
  dropdowns. Each dropdown change fires an ASP.NET postback that re-renders the
  list filtered to that exact date. We then read each release's title + PRID and
  fetch its English body over the same authenticated session (ctx.request —
  bodies are server-rendered, no JS needed).

  This mirrors pipelines/ie_scraper.py (the IE Gmail-PDF → website migration):
  same module shape, same caching idea, same defensive logging. PIB needs no
  login; caching yes — once a day is captured to inputs/pib_cache/<date>.json it
  feeds future back-fills (the live page rotates / and old dates stay reachable
  only through the cache once captured).

HEADED BROWSER NOTE
  Akamai blocks headless Chromium, so we MUST launch headed (headless=False). On
  a desktop (macOS) this briefly opens a Chromium window — expected. On a
  headless Linux box (e.g. Railway) there's no display, so either run under
  `xvfb-run -a python3 ...` or rely on the locally-captured daily cache. Set
  PIB_HEADLESS=1 to force headless anyway (will usually hit Access Denied).

USAGE
  # scrape releases issued on news_date = pipeline_date - 1 (2026-06-03 here):
  python3 pipelines/pib_scraper.py 2026-06-04

  As a library (how daily_ca_pipeline.fetch_pib uses it):
    from pipelines import pib_scraper
    items = pib_scraper.scrape_pib(news_date)   # list[{source,title,text,url}]

Each item is the shape the pipeline expects (the filter reads item["text"]):
  {"source": "PIB", "title": <headline>, "text": <headline + body>, "url": <url>}
"""
import re
import sys
import json
import html
import datetime
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from pipelines import _common as C

try:
    from bs4 import BeautifulSoup
except ImportError as e:  # pragma: no cover
    raise SystemExit(f"pib_scraper needs beautifulsoup4: {e}")


BASE = "https://pib.gov.in"
# reg=3 → National, lang=1 → English. These persist through the postbacks.
LIST_URL = f"{BASE}/Allrel.aspx?reg=3&lang=1"
# RSS feed — UNLIKE Allrel.aspx, this is NOT behind Akamai (works via plain
# requests, incl. from Railway). It only exposes the ~20 latest PRIDs with no
# pubDate, so it's a partial last-resort fallback (fetch_via_rss).
RSS_URL = f"{BASE}/RssMain.aspx?ModId=6&Lang=1&Regid=3"
CACHE_DIR = C.ROOT / "inputs" / "pib_cache"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

MAX_ITEMS = 150          # safety cap on releases per day
NAV_TIMEOUT = 60_000     # ms
POSTBACK_SETTLE = 1_200  # ms to let an ASP.NET postback finish re-rendering
BODY_GAP = 0.25          # politeness delay (seconds) between body fetches
MIN_BODY_WORDS = 12      # below this a "release" is a stub / nav noise

# Dropdown element ids on Allrel.aspx (ASP.NET autopostback controls).
DDL_YEAR = "ContentPlaceHolder1_ddlYear"
DDL_MONTH = "ContentPlaceHolder1_ddlMonth"
DDL_DAY = "ContentPlaceHolder1_ddlday"

# Body container on PressReleasePage.aspx (note the site's own "innner" typo).
BODY_DIV_SEL = "div.innner-page-main-about-us-content-right-part"

_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}


# ──────────────────────────────────────────────────────────────────────────────
# cache (mirrors ie_scraper)
# ──────────────────────────────────────────────────────────────────────────────
def _cache_path(date):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{date.isoformat()}.json"


# ──────────────────────────────────────────────────────────────────────────────
# browser session
# ──────────────────────────────────────────────────────────────────────────────
def _new_context(p):
    """Launch Chromium (headed by default — see module docstring) and return a
    context already cleared by Akamai (homepage warm-up + webdriver masking)."""
    headless = C.ENV.get("PIB_HEADLESS", "").lower() in ("1", "true", "yes")
    browser = p.chromium.launch(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    )
    ctx = browser.new_context(
        user_agent=UA, locale="en-US", viewport={"width": 1366, "height": 900},
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    page = ctx.new_page()
    # Hide the headless/automation tell some bot walls sniff for.
    page.add_init_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    # Warm-up: the homepage hands out the Akamai clearance cookie the list needs.
    page.goto(BASE + "/", wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
    page.wait_for_timeout(2_500)
    return browser, ctx, page


def _select_date(page, date):
    """Pick year → month → day from the dropdowns, awaiting each postback.
    Order matters: year/month narrow first, the day select fires the final
    re-render of the date-filtered release list."""
    for ddl, value in ((DDL_YEAR, str(date.year)),
                       (DDL_MONTH, str(date.month)),
                       (DDL_DAY, str(date.day))):
        page.select_option(f"#{ddl}", value)
        try:
            page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:
            pass
        page.wait_for_timeout(POSTBACK_SETTLE)


# ──────────────────────────────────────────────────────────────────────────────
# scraping
# ──────────────────────────────────────────────────────────────────────────────
def _list_releases(page):
    """Read (prid, title, url) for every release link in the rendered list,
    deduped by PRID. Returns list[dict]."""
    raw = page.eval_on_selector_all(
        "a[href*='PRID']",
        "els => els.map(e => ({href: e.href, txt: (e.innerText||'').trim()}))")
    out, seen = [], set()
    for a in raw:
        m = re.search(r"PRID=(\d+)", a["href"])
        if not m:
            continue
        prid = m.group(1)
        if prid in seen:
            continue
        title = html.unescape(" ".join(a["txt"].split()))
        if len(title) < 15:           # skip stray/empty anchors
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


def _parse_body(html_text, target_date):
    """Extract (title, body, ok_date) from a PressReleasePage.aspx response.
    `ok_date` is False only when the page carries a parseable date that does NOT
    match target_date (defensive — the server filter is normally exact)."""
    soup = BeautifulSoup(html_text, "html.parser")
    div = soup.select_one(BODY_DIV_SEL)
    if div is None:
        return None, "", True

    h2 = div.find("h2")
    title = html.unescape(" ".join(h2.get_text(" ", strip=True).split())) if h2 else ""

    # date stamp lives in its own element, e.g. "… 03 JUN 2026 7:11PM by PIB Delhi"
    ok_date = True
    date_el = div.select_one("[class*='ReleaseDateSubHeaddateTime']")
    if date_el:
        dm = re.search(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})",
                       date_el.get_text(" ", strip=True))
        if dm:
            mon = _MONTHS.get(dm.group(2).upper())
            if mon:
                try:
                    found = datetime.date(int(dm.group(3)), mon, int(dm.group(1)))
                    ok_date = (found == target_date)
                except ValueError:
                    pass

    paras = []
    for p in div.find_all("p"):
        txt = html.unescape(" ".join(p.get_text(" ", strip=True).split()))
        if txt:
            paras.append(txt)
    body = " ".join(paras).strip()
    return title, body, ok_date


def _fetch_body(ctx, url, target_date):
    """Fetch one release body over the cleared session. Returns (title, body)
    or (None, None) on failure / date mismatch / too-short."""
    try:
        r = ctx.request.get(url, timeout=NAV_TIMEOUT)
        if r.status != 200:
            return None, None
        title, body, ok_date = _parse_body(r.text(), target_date)
        if not ok_date:
            return None, None
        if len(body.split()) < MIN_BODY_WORDS:
            return None, None
        return title, body
    except Exception as e:
        C.log(f"   ⚠ PIB body fetch failed {url}: {e}")
        return None, None


def scrape_pib(date, max_items=MAX_ITEMS):
    """Scrape PIB National/English press releases issued on `date` (a
    datetime.date). Returns list[{source,title,text,url}] — the shape
    daily_ca_pipeline expects (filter reads item['text']). Returns [] on any
    failure (missing Playwright, headless Access-Denied, no releases) so the
    pipeline degrades gracefully to its other sources."""
    if isinstance(date, str):
        date = C.parse_date(date)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        C.log("   ⚠ PIB: playwright not installed "
              "(`pip install playwright && playwright install chromium`); skipping.")
        return []

    C.log(f"   PIB web scrape — releases issued {date.isoformat()}")
    items = []
    try:
        with sync_playwright() as p:
            browser, ctx, page = _new_context(p)
            try:
                page.goto(LIST_URL, wait_until="networkidle", timeout=NAV_TIMEOUT)
                page.wait_for_timeout(2_000)
                if "Access Denied" in (page.title() or ""):
                    C.log("   ⚠ PIB: Akamai 'Access Denied' (headless?). "
                          "Run headed / under xvfb. Skipping.")
                    return []
                _select_date(page, date)
                # The day-select postback re-navigates; wait for the refreshed
                # release list to actually appear before reading it (else the JS
                # execution context gets destroyed mid-read).
                try:
                    page.wait_for_selector("a[href*='PRID']", timeout=20_000)
                except Exception:
                    pass
                page.wait_for_timeout(1_500)
                releases = _list_releases(page)
                C.log(f"      · {len(releases)} release links listed; fetching bodies…")
                for rel in releases[:max_items]:
                    title, body = _fetch_body(ctx, rel["url"], date)
                    page.wait_for_timeout(int(BODY_GAP * 1000))
                    if body is None:
                        continue
                    title = title or rel["title"]
                    items.append({
                        "source": "PIB",
                        "title": title,
                        "text": f"{title}. {body}",
                        "url": rel["url"],
                    })
            finally:
                browser.close()
    except Exception as e:
        C.log(f"   ⚠ PIB scrape failed: {e}")
        return items

    C.log(f"   PIB web scrape → {len(items)} releases for {date.isoformat()}")
    return items


# ──────────────────────────────────────────────────────────────────────────────
def write_supabase(date, items):
    """Producer side: upsert a freshly-scraped day into Supabase `pib_cache`
    (keyed by PRID) so the pipeline / Railway can read it without a browser.
    Returns the number of rows upserted."""
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


def fetch_via_rss(date, max_items=MAX_ITEMS):
    """FINAL fallback (Railway-safe — no browser, RSS + body pages aren't behind
    Akamai). RSS exposes the ~20 latest PRIDs; we fetch each ENGLISH body via
    PressReleasePage.aspx and keep only those whose date stamp == `date`. Coverage
    is partial (latest ~20, current-only) — it cannot reach a full past day."""
    import requests
    if isinstance(date, str):
        date = C.parse_date(date)
    try:
        r = requests.get(RSS_URL, headers={"User-Agent": UA}, timeout=30)
        if r.status_code != 200:
            C.log(f"   ⚠ PIB RSS → HTTP {r.status_code}")
            return []
        prids = list(dict.fromkeys(re.findall(r"PRID=(\d+)", r.text)))  # dedup, keep order
    except Exception as e:
        C.log(f"   ⚠ PIB RSS fetch failed: {e}")
        return []

    out = []
    for prid in prids[:max_items]:
        url = f"{BASE}/PressReleasePage.aspx?PRID={prid}"
        try:
            br = requests.get(url, headers={"User-Agent": UA}, timeout=30)
            if br.status_code != 200:
                continue
            title, body, ok_date = _parse_body(br.text, date)
            if not ok_date or len(body.split()) < MIN_BODY_WORDS:
                continue
            title = title or ""
            out.append({"source": "PIB", "title": title,
                        "text": f"{title}. {body}", "url": url})
        except Exception:
            continue
        time.sleep(BODY_GAP)
    C.log(f"   PIB RSS fallback → {len(out)} English releases dated {date.isoformat()}")
    return out


if __name__ == "__main__":
    # --date YYYY-MM-DD scrapes that EXACT date (used by run_daily.sh's producer
    # step). A bare positional arg is the pipeline date → scrapes the day before.
    # --write-supabase upserts the scraped day into the pib_cache table.
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
        pipeline_date = C.parse_date(arg) if arg else datetime.date.today()
        news_date = pipeline_date - datetime.timedelta(days=1)

    rels = scrape_pib(news_date)
    if "--write-supabase" in sys.argv and rels:
        write_supabase(news_date, rels)
    print(json.dumps(rels, ensure_ascii=False, indent=2))
    print(f"\n# {len(rels)} PIB releases for {news_date.isoformat()}", file=sys.stderr)
    sys.exit(0 if rels else 1)
