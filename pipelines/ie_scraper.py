#!/usr/bin/env python3
"""
STEP 0 — Indian Express website scraper.

Replaces the old Gmail ePaper-PDF fetch (fetch_ie_pdf.py + pdfplumber/PyMuPDF
parsing). Logs into indianexpress.com with the subscriber credentials in .env,
persists the session cookie locally, and scrapes *yesterday's* UPSC-relevant
articles directly from the website — clean HTML text, no OCR.

  # scrape articles PUBLISHED on 2026-06-03 (the day before the pipeline date):
  python3 pipelines/ie_scraper.py 2026-06-04
  # or scrape an EXACT date directly (force re-scrape — for testing / back-fill):
  python3 pipelines/ie_scraper.py --date 2026-06-03

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
import re
import sys
import json
import time
import html
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

MIN_WORDS = 100
REQUEST_GAP = 0.7   # politeness delay between requests (seconds)

# Date-addressable archive sitemap. IE publishes one <urlset> per calendar day at
# sitemap.xml?yyyy=YYYY&mm=MM&dd=DD (90+ days back), so we fetch a target day's
# articles directly instead of walking the live section listings (which only
# surface the most recent ~25 stories and can't reach a 2-day-old back-fill).
SITEMAP_DATED = BASE + "/sitemap.xml?yyyy={y}&mm={m:02d}&dd={d:02d}"

# World: keep only India-relevant / diplomacy / governance headlines.
WORLD_KEYWORDS = [
    "india", "bilateral", "un ", "united nations", "election", "president",
    "prime minister", "treaty", "agreement", "minister",
]
# Cities: keep only Rajasthan-relevant.
CITY_KEYWORDS = ["jaipur", "rajasthan"]
# Explained: data/report/policy pieces + environment/wildlife (IE has no top-level
# 'environment' slug, so conservation/climate stories are kept here and via 'india').
EXPLAINED_KEYWORDS = [
    "survey", "report", "data", "health", "population", "economy", "index",
    "environment", "wildlife", "conservation", "climate", "forest", "tiger",
    "species", "ramsar", "biodiversity", "ecosystem", "pollution", "emission",
]

# Sports: FIX 2 — keep ONLY awards & records; drop match scores and squad selections.
SPORTS_AWARD_KEYWORDS = [
    "award", "awarded", "medal", "gold", "silver", "bronze", "champion", "championship",
    "record", "wins", " won", "winner", "title", "trophy", "rank", "ranking",
    "khel ratna", "arjuna", "dronacharya", "padma", "honour", "honoured", "felicitat",
    "first indian", "fastest", "youngest", "oldest", "player of the",
]

# Section policy, keyed by the <section> slug in /article/<section>/… URLs.
#   None        → keep every article in the section (RPSC-core sections)
#   [keywords]  → keep only articles whose headline matches one of the keywords
# Sections not listed here are skipped entirely (entertainment, lifestyle, business,
# legal-news, opinion, education, trending, cities, political-pulse, …).
# FIX 2: front-page top stories are covered by the dated sitemap across india/explained/
# world; cities + political-pulse dropped (party-politics noise); environment via
# india+explained (no IE 'environment' slug); science via technology + /science/ prefilter.
SECTION_POLICY = {
    "india": None,                       # full — national politics, governance, schemes, environment
    "explained": EXPLAINED_KEYWORDS,     # data/report/policy + environment/wildlife pieces
    "world": WORLD_KEYWORDS,             # India-diplomacy / agreements / MoUs only
    "sports": SPORTS_AWARD_KEYWORDS,     # FIX 2 — awards & records ONLY (no scores/squads)
    "technology": None,                  # FIX 2 — Science desk only (URL-prefiltered to /science/)
}
# NOTE: IE's "upsc-current-affairs" desk is deliberately EXCLUDED — it carries
# study aids (daily quizzes, "UPSC Key", "Knowledge Nugget", weekly snapshots),
# not discrete news events, so it's the wrong shape for per-item CA authoring.
# Its actual news (e.g. a PM foreign visit) is already covered via india/world.

# Rajasthan city/region slugs — used to pre-filter the (large) cities section by
# URL before fetching bodies, so we don't pull 60+ non-Rajasthan city stories.
RAJ_CITY_SLUGS = ("jaipur", "jodhpur", "udaipur", "kota", "ajmer", "bikaner",
                  "jaisalmer", "bharatpur", "alwar", "sikar", "bhilwara",
                  "pali", "nagaur", "jhunjhunu", "rajasthan")

# URL fragments that mark non-article content we never want.
SKIP_URL_BITS = ("/photos/", "/photo-", "/videos/", "/video/", "/audio/",
                 "/podcast", "/gallery", "/lifestyle/", "/entertainment/",
                 "/business/market", "/opinion/",
                 "/subscribe", "/profile/", "utm_source", "/web-stories/")


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


def _sitemap_candidates(sess, news_date):
    """Discover candidate article URLs for `news_date` from the dated archive
    sitemap, returning [(url, section)] for sections we keep (per SECTION_POLICY).

    The cities section is pre-filtered by URL to Rajasthan slugs so we don't fetch
    dozens of unrelated city stories. Headline keyword filters (world/explained)
    and the strict published-date check are applied later, after the body fetch."""
    url = SITEMAP_DATED.format(y=news_date.year, m=news_date.month, d=news_date.day)
    try:
        r = sess.get(url, timeout=30)
        if r.status_code != 200:
            C.log(f"   ⚠ IE sitemap {url} → HTTP {r.status_code}")
            return []
    except Exception as e:
        C.log(f"   ⚠ IE sitemap fetch failed {url}: {e}")
        return []

    out, seen = [], set()
    bysec = {}
    for loc in re.findall(r"<loc>(.*?)</loc>", r.text):
        href = html.unescape(loc.strip())
        if "indianexpress.com" not in href or any(bit in href for bit in SKIP_URL_BITS):
            continue
        m = re.search(r"/article/([^/]+)/", href)
        if not m:
            continue
        section = m.group(1)
        if section not in SECTION_POLICY:
            continue
        # FIX 2 — 'technology' includes gadget/consumer-tech; keep ONLY the science
        # desk (/technology/science/…), where ISRO/DRDO/space/research stories live.
        if section == "technology" and "/science/" not in href.lower():
            continue
        if href in seen:
            continue
        seen.add(href)
        out.append((href, section))
        bysec[section] = bysec.get(section, 0) + 1
    C.log(f"      sitemap candidates by section: {bysec}")
    return out


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
        body = html.unescape(body)
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
                txt = html.unescape(" ".join(p.get_text(" ", strip=True).split()))
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
    title = html.unescape(" ".join((title or "").split()))
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


# FIX 2 — editorial landing pages scraped IN ADDITION to the dated sitemap: the
# homepage front-page (top stories) and the Science & Technology desk. Same
# SECTION_POLICY / SKIP / science rules apply; the strict published-date filter at
# body-fetch keeps only target-date stories. (IE has no clean 'environment' section
# URL — environment/wildlife is covered via india + explained env-keywords + sitemap.)
LANDING_PAGES = (
    f"{BASE}/",                                # homepage / front page — top stories
    f"{BASE}/section/technology/science/",     # Science & Technology desk
)


def _landing_candidates(sess):
    """Scrape the editorial landing pages for /article/<section>/ links that pass the
    section policy. Returns [(url, section)]; the caller dedups against the sitemap."""
    out, seen = [], set()
    for page in LANDING_PAGES:
        try:
            r = sess.get(page, timeout=30)
            if r.status_code != 200:
                C.log(f"   ⚠ IE landing {page} → HTTP {r.status_code}")
                continue
        except Exception as e:
            C.log(f"   ⚠ IE landing fetch failed {page}: {e}")
            continue
        for href in re.findall(r"https://indianexpress\.com/article/[^\s\"'<>]+", r.text):
            href = html.unescape(href)
            if any(bit in href for bit in SKIP_URL_BITS):
                continue
            m = re.search(r"/article/([^/]+)/", href)
            if not m or m.group(1) not in SECTION_POLICY:
                continue
            section = m.group(1)
            if section == "technology" and "/science/" not in href.lower():
                continue
            if href in seen:
                continue
            seen.add(href)
            out.append((href, section))
    C.log(f"      landing-page candidates: {len(out)}")
    return out


def _cache_path(news_date):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{news_date.isoformat()}.json"


def write_supabase(news_date, articles):
    """Producer side (Mac night-scraper): upsert scraped IE articles into Supabase
    `ie_cache` (keyed by url) so the Railway pipeline reads them without scraping
    from a cloud IP. Mirrors pib_scraper.write_supabase. Returns rows upserted."""
    if isinstance(news_date, str):
        news_date = C.parse_date(news_date)
    rows = []
    for a in articles:
        if not a.get("url"):
            continue
        rows.append({
            "url": a.get("url"), "title": a.get("title"), "section": a.get("section"),
            "summary": a.get("summary"), "full_text": a.get("full_text"),
            "text": a.get("text"), "published_date": news_date.isoformat(),
        })
    if rows:
        try:
            C.sb_upsert("ie_cache", rows, on_conflict="url")
        except Exception as e:
            C.log(f"   ⚠ IE Supabase upsert failed: {e}")
            return 0
    C.log(f"   IE → Supabase ie_cache: upserted {len(rows)} rows for {news_date.isoformat()}")
    return len(rows)


def fetch_ie_articles(news_date, force=False):
    """Fetch IE articles published on `news_date` via the date-addressable archive
    sitemap, applying the per-section policy in SECTION_POLICY. Returns a list of
    article dicts (see module docstring).

    Discovery is sitemap-driven (sitemap.xml?yyyy&mm&dd) rather than walking live
    section listings, so any date — including multi-day back-fills — resolves
    directly and reliably. Each candidate body is fetched once; we keep it only if
    its JSON-LD published date matches `news_date` and its headline passes the
    section's keyword filter.

    Results are cached to inputs/ie_cache/<date>.json so run_daily.sh's Step 0
    scrape is reused by daily_ca_pipeline's in-process call (no double scrape).
    Pass force=True (or delete the cache file) to re-scrape."""
    if isinstance(news_date, str):
        news_date = C.parse_date(news_date)
    iso = news_date.isoformat()
    # 1 — Supabase ie_cache (PRIMARY; how Railway reads the Mac night-scrape, mirrors PIB)
    if not force:
        try:
            rows = C.sb_select("ie_cache", params={"published_date": f"eq.{iso}", "select": "*"})
            if rows:
                C.log(f"   IE: {len(rows)} articles for {iso} (Supabase ie_cache)")
                return [{"title": r.get("title"), "source": "IE", "section": r.get("section"),
                         "summary": r.get("summary"), "full_text": r.get("full_text"),
                         "url": r.get("url"), "text": r.get("text")} for r in rows]
        except Exception as e:
            C.log(f"   ⚠ ie_cache read failed (will try local/live): {e}")
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

    candidates = _sitemap_candidates(sess, news_date) + _landing_candidates(sess)
    C.log(f"   IE: {len(candidates)} candidate URLs (sitemap + landing pages); fetching bodies…")

    out = []
    seen_urls = set()
    kept_by_sec = {}
    for url, section in candidates:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        art = _parse_article(sess, url, section)
        time.sleep(REQUEST_GAP)
        if not art:
            continue
        pub = art.pop("_pub_date", None)
        if pub != news_date:           # strict: only articles actually published that day
            continue
        if not _headline_matches(art["title"], SECTION_POLICY.get(section)):
            continue
        out.append(art)
        kept_by_sec[section] = kept_by_sec.get(section, 0) + 1

    C.log(f"      kept by section: {kept_by_sec}")
    C.log(f"   IE web scrape → {len(out)} articles for {news_date.isoformat()}")
    try:
        cache.write_text(json.dumps(out, ensure_ascii=False))
    except Exception as e:
        C.log(f"   ⚠ IE cache write failed: {e}")
    return out


# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # --date YYYY-MM-DD scrapes that EXACT date (force re-scrape) — handy for
    # testing a specific day or a back-fill. A bare positional arg is the
    # pipeline/label date and scrapes the day before (news_date = arg - 1), which
    # is how run_daily.sh's Step 0 invokes this.
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
        arts = fetch_ie_articles(news_date, force=True)
    else:
        arg = next((a for a in sys.argv[1:] if not a.startswith("--")), None)
        pipeline_date = C.parse_date(arg) if arg else datetime.date.today()
        news_date = pipeline_date - datetime.timedelta(days=1)
        arts = fetch_ie_articles(news_date)
    # --write-supabase: upsert the scraped articles into Supabase ie_cache (used by
    # the Mac night-scraper so Railway can read them).
    if "--write-supabase" in sys.argv and arts:
        write_supabase(news_date, arts)
    # Print JSON so the step can be inspected / piped if desired.
    print(json.dumps(arts, ensure_ascii=False, indent=2))
    print(f"\n# {len(arts)} IE articles for {news_date.isoformat()}", file=sys.stderr)
    if not arts:
        print("⚠ ie_scraper: 0 articles fetched — STEP 2 will fall back to PIB-only", file=sys.stderr)
    # Always exit 0: STEP 0 is best-effort. A non-zero exit aborts the whole
    # pipeline via set -e. STEP 2 (daily_ca_pipeline) reads the cache directly.
    sys.exit(0)
