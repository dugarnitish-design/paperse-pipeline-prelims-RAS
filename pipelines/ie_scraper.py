#!/usr/bin/env python3
"""
STEP 0 — Indian Express website scraper.

Replaces the old Gmail ePaper-PDF fetch (fetch_ie_pdf.py + pdfplumber/PyMuPDF
parsing). Logs into indianexpress.com with the subscriber credentials in .env,
persists the session cookie locally, and scrapes *yesterday's* UPSC-relevant
articles directly from the website — clean HTML text, no OCR.

  # scrape articles PUBLISHED on 2026-06-03 (the day before the pipeline date):
  python3 pipelines/ie_scraper.py 2026-06-04

As a library (how daily_ca_pipeline.py uses it):
  from pipelines import ie_scraper
  articles = ie_scraper.fetch_ie_articles(news_date)   # news_date = the day to scrape

Each article dict is compatible with the daily_ca_pipeline filters. Note `text`
is what the pipeline reads (keyword filter + Claude authoring); we set it to
headline + summary. `full_text` is kept alongside for reference:
  {title, source:"IE", section, published, summary, full_text, url, text}

CREDENTIALS / COOKIE
  .env:  IE_EMAIL, IE_PASSWORD   (leave blank until provided)
  Cookie persisted to inputs/ie_session.json and reused daily; we re-login only
  when the saved session no longer looks authenticated. The cookie file and .env
  are git-ignored — credentials never leave this machine.

NOTE ON LOGIN
  IE's exact login transport can change (it may move to a JS/AJAX endpoint). The
  login() helper does a best-effort form POST and verifies the result; if it
  can't confirm auth it logs a warning and proceeds (much IE current-affairs
  content is readable without login). If login breaks after credentials are
  added, that's the one place to adjust.
"""
import sys
import json
import time
import datetime
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from pipelines import _common as C

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError as e:  # pragma: no cover
    raise SystemExit(f"ie_scraper needs requests + beautifulsoup4: {e}")


BASE = "https://indianexpress.com"
LOGIN_PAGE = f"{BASE}/login/"
COOKIE_FILE = C.ROOT / "inputs" / "ie_session.json"
CACHE_DIR = C.ROOT / "inputs" / "ie_cache"
UA = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}

# How many listing pages to walk per section before giving up (yesterday's
# articles are near the top; we stop early once a page is entirely older).
MAX_PAGES = 5
MIN_WORDS = 100
REQUEST_GAP = 0.7   # politeness delay between requests (seconds)

# World: keep only India-relevant / diplomacy / governance headlines.
WORLD_KEYWORDS = [
    "india", "bilateral", "un ", "united nations", "election", "president",
    "prime minister", "treaty", "agreement", "minister",
]
# Cities: keep only Rajasthan-relevant.
CITY_KEYWORDS = ["jaipur", "rajasthan"]
# Explained: keep only data/report/policy-flavoured pieces.
EXPLAINED_KEYWORDS = [
    "survey", "report", "data", "health", "population", "economy", "index",
]

SECTIONS = [
    # PRIORITY 1 — scrape everything
    {"name": "sports", "path": "/section/sports/", "filter": None},
    {"name": "india",  "path": "/section/india/",  "filter": None},
    # PRIORITY 2 — world, India/diplomacy headlines only
    {"name": "world",  "path": "/section/world/",  "filter": WORLD_KEYWORDS},
    # PRIORITY 3 — cities, Jaipur/Rajasthan only
    {"name": "cities", "path": "/section/cities/", "filter": CITY_KEYWORDS},
    # PRIORITY 4 — explained, data/report headlines only
    {"name": "explained", "path": "/explained/",   "filter": EXPLAINED_KEYWORDS},
]

# URL fragments that mark non-article content we never want.
SKIP_URL_BITS = ("/photos/", "/photo-", "/videos/", "/video/", "/audio/",
                 "/podcast", "/gallery", "/lifestyle/", "/entertainment/",
                 "/business/market", "/opinion/", "/technology/")


# ──────────────────────────────────────────────────────────────────────────────
# session / cookie persistence
# ──────────────────────────────────────────────────────────────────────────────
def _new_session():
    s = requests.Session()
    s.headers.update(UA)
    return s


def _load_cookies(sess) -> bool:
    """Load saved cookies into the session. Returns True if a cookie file existed."""
    if not COOKIE_FILE.exists():
        return False
    try:
        data = json.loads(COOKIE_FILE.read_text())
        for k, v in data.items():
            sess.cookies.set(k, v, domain=".indianexpress.com")
        return True
    except Exception as e:
        C.log(f"   ⚠ IE cookie load failed: {e}")
        return False


def _save_cookies(sess) -> None:
    try:
        COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
        COOKIE_FILE.write_text(json.dumps(dict(sess.cookies)))
    except Exception as e:
        C.log(f"   ⚠ IE cookie save failed: {e}")


def _looks_authenticated(sess) -> bool:
    """Heuristic: fetch the homepage and look for a signed-in marker
    (account/logout link) rather than a 'Sign In' prompt."""
    try:
        r = sess.get(BASE, timeout=30)
        html = r.text.lower()
        if "logout" in html or "my account" in html or "ie-myaccount" in html:
            return True
        # If the only auth affordance is a Sign In / subscribe CTA, treat as not logged in.
        return False
    except Exception:
        return False


def login(sess) -> bool:
    """Best-effort form login to indianexpress.com.

    Returns True if we end up looking authenticated. IE's login may be a JS/AJAX
    flow; if this form POST stops working after credentials are added, this is
    the single function to adjust (or drop in a manually-exported cookie)."""
    email = C.ENV.get("IE_EMAIL")
    password = C.ENV.get("IE_PASSWORD")
    if not email or not password:
        C.log("   ⚠ IE_EMAIL / IE_PASSWORD not set in .env — scraping anonymously "
              "(free articles only).")
        return False
    try:
        # Fetch the login page to pick up any hidden nonce/csrf fields.
        r = sess.get(LOGIN_PAGE, timeout=30)
        soup = BeautifulSoup(r.text, "html.parser")
        form = soup.find("form")
        payload, action = {}, LOGIN_PAGE
        if form:
            action = form.get("action") or LOGIN_PAGE
            if action.startswith("/"):
                action = BASE + action
            for inp in form.find_all("input"):
                name = inp.get("name")
                if name:
                    payload[name] = inp.get("value", "")
        # Map credentials onto the most common WordPress/IE field names.
        for k in ("log", "username", "email", "user_email", "user_login"):
            payload.setdefault(k, email)
            payload[k] = email if k in payload or k in ("email", "log") else payload.get(k, email)
        payload["email"] = email
        payload["log"] = email
        for k in ("pwd", "password", "user_pass"):
            payload[k] = password
        sess.post(action, data=payload, timeout=30,
                  headers={"Referer": LOGIN_PAGE})
        time.sleep(REQUEST_GAP)
        if _looks_authenticated(sess):
            _save_cookies(sess)
            C.log("   ✓ IE login succeeded; session cookie saved.")
            return True
        C.log("   ⚠ IE login could not be confirmed; proceeding anonymously. "
              "(Adjust ie_scraper.login() if premium articles are missing.)")
        return False
    except Exception as e:
        C.log(f"   ⚠ IE login error: {e}; proceeding anonymously.")
        return False


def _ensure_session():
    """Return an authenticated-as-possible session: reuse saved cookie, else login."""
    sess = _new_session()
    had_cookie = _load_cookies(sess)
    if had_cookie and _looks_authenticated(sess):
        C.log("   ✓ Reusing saved IE session cookie.")
        return sess
    login(sess)
    return sess


# ──────────────────────────────────────────────────────────────────────────────
# scraping
# ──────────────────────────────────────────────────────────────────────────────
def _get_soup(sess, url):
    try:
        r = sess.get(url, timeout=30)
        if r.status_code != 200:
            return None
        return BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        C.log(f"   ⚠ IE GET failed {url}: {e}")
        return None


def _section_page_url(path, page):
    if page <= 1:
        return BASE + path
    return f"{BASE}{path}page/{page}/"


def _article_links(soup):
    """Collect candidate article URLs from a section listing page."""
    links = []
    seen = set()
    for a in soup.select("div.articles a, div.nation a, h2 a, h3 a, .title a, a"):
        href = a.get("href") or ""
        if not href.startswith("http"):
            continue
        if "indianexpress.com" not in href:
            continue
        if any(bit in href for bit in SKIP_URL_BITS):
            continue
        # IE article URLs look like '/<section>/<slug>-<numeric-id>/'. Require a
        # trailing numeric id to skip nav/landing links; date+parse reject the rest.
        slug = href.rstrip("/").rsplit("/", 1)[-1]
        if not any(ch.isdigit() for ch in slug):
            continue
        if href in seen:
            continue
        seen.add(href)
        links.append(href)
    return links


def _extract_jsonld(soup):
    """Return the first NewsArticle/Article JSON-LD object on the page, or {}."""
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "{}")
        except Exception:
            continue
        candidates = data if isinstance(data, list) else [data]
        # @graph wrapper (common on IE)
        for c in list(candidates):
            if isinstance(c, dict) and "@graph" in c:
                candidates.extend(c["@graph"])
        for c in candidates:
            if not isinstance(c, dict):
                continue
            t = c.get("@type", "")
            t = " ".join(t) if isinstance(t, list) else str(t)
            if "Article" in t or "NewsArticle" in t:
                return c
    return {}


def _parse_published(ld, soup):
    """Return a datetime.date for the article, or None."""
    raw = ld.get("datePublished") or ld.get("dateModified")
    if not raw:
        m = soup.find("meta", property="article:published_time")
        raw = m.get("content") if m else None
    if not raw:
        return None
    try:
        # e.g. '2026-06-03T22:15:33+05:30'
        return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except Exception:
        try:
            return datetime.date.fromisoformat(raw[:10])
        except Exception:
            return None


def _paragraphs(ld, soup):
    """Return article paragraphs (list[str]). Prefer JSON-LD articleBody,
    fall back to the story body container."""
    body = ld.get("articleBody")
    if body and isinstance(body, str) and len(body.split()) >= MIN_WORDS:
        parts = [p.strip() for p in body.split("\n") if p.strip()]
        if len(parts) < 2:  # articleBody sometimes has no newlines
            parts = [s.strip() + "." for s in body.split(". ") if s.strip()]
        return parts
    paras = []
    for sel in ("div.story-details p", "div.full-details p",
                "div[itemprop='articleBody'] p", "article p", ".ie-first-publish ~ p"):
        ps = soup.select(sel)
        if ps:
            for p in ps:
                txt = " ".join(p.get_text(" ", strip=True).split())
                if txt and not txt.lower().startswith(("also read", "read more")):
                    paras.append(txt)
            if paras:
                break
    return paras


def _parse_article(sess, url, section):
    """Fetch and parse one article. Returns an article dict or None."""
    soup = _get_soup(sess, url)
    if soup is None:
        return None
    ld = _extract_jsonld(soup)

    # headline
    title = ld.get("headline")
    if not title:
        og = soup.find("meta", property="og:title")
        title = og.get("content") if og else (soup.title.string if soup.title else "")
    title = " ".join((title or "").split())
    if not title:
        return None

    published = _parse_published(ld, soup)
    paras = _paragraphs(ld, soup)
    full_text = " ".join(paras).strip()
    if len(full_text.split()) < MIN_WORDS:
        return None  # too short — likely a gallery/video/stub

    summary = " ".join(paras[:3]).strip()
    return {
        "title": title,
        "source": "IE",
        "section": section,
        "published": published.isoformat() if published else None,
        "summary": summary,
        "full_text": full_text,
        "url": url,
        # `text` is what daily_ca_pipeline reads (keyword filter + authoring):
        "text": f"{title}. {summary}",
        "_pub_date": published,  # internal: used for date filtering, stripped before return
    }


def _headline_matches(title, keywords):
    if not keywords:
        return True
    low = title.lower()
    return any(k in low for k in keywords)


def _cache_path(news_date):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{news_date.isoformat()}.json"


def fetch_ie_articles(news_date, force=False):
    """Scrape IE for articles published on `news_date` across the configured
    sections, applying the per-section headline filters. Returns a list of
    article dicts (see module docstring).

    Results are cached to inputs/ie_cache/<date>.json so run_daily.sh's Step 0
    scrape is reused by daily_ca_pipeline's in-process call (no double scrape).
    Pass force=True (or delete the cache file) to re-scrape."""
    if isinstance(news_date, str):
        news_date = C.parse_date(news_date)
    cache = _cache_path(news_date)
    if not force and cache.exists():
        try:
            cached = json.loads(cache.read_text())
            if cached:
                C.log(f"   IE web scrape: {len(cached)} articles for "
                      f"{news_date.isoformat()} (from cache)")
                return cached
        except Exception:
            pass

    C.log(f"   IE web scrape — articles published {news_date.isoformat()}")
    sess = _ensure_session()

    out = []
    seen_urls = set()
    for sec in SECTIONS:
        kept_here = 0
        for page in range(1, MAX_PAGES + 1):
            soup = _get_soup(sess, _section_page_url(sec["path"], page))
            time.sleep(REQUEST_GAP)
            if soup is None:
                break
            links = _article_links(soup)
            if not links:
                break
            page_had_target = False
            for url in links:
                if url in seen_urls:
                    continue
                # Cheap headline pre-filter is done after parse (need the title);
                # parse first so we also get the published date.
                art = _parse_article(sess, url, sec["name"])
                time.sleep(REQUEST_GAP)
                if not art:
                    continue
                pub = art.pop("_pub_date", None)
                if pub != news_date:
                    # Older-than-target on a reverse-chron page → stop paging soon.
                    continue
                page_had_target = True
                if not _headline_matches(art["title"], sec["filter"]):
                    continue
                seen_urls.add(url)
                out.append(art)
                kept_here += 1
            # If this listing page yielded nothing from the target date, the
            # rest are older — stop walking this section.
            if not page_had_target and page > 1:
                break
        C.log(f"      · {sec['name']}: kept {kept_here}")
    C.log(f"   IE web scrape → {len(out)} articles for {news_date.isoformat()}")
    try:
        cache.write_text(json.dumps(out, ensure_ascii=False))
    except Exception as e:
        C.log(f"   ⚠ IE cache write failed: {e}")
    return out


# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    arg = next((a for a in sys.argv[1:] if not a.startswith("--")), None)
    pipeline_date = C.parse_date(arg) if arg else datetime.date.today()
    news_date = pipeline_date - datetime.timedelta(days=1)
    arts = fetch_ie_articles(news_date)
    # Print JSON so the step can be inspected / piped if desired.
    print(json.dumps(arts, ensure_ascii=False, indent=2))
    print(f"\n# {len(arts)} IE articles for {news_date.isoformat()}", file=sys.stderr)
    sys.exit(0 if arts else 1)
