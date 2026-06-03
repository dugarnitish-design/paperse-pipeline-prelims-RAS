#!/usr/bin/env python3
"""
STEP 2 — PaperSe Daily CA Pipeline.

  python3 pipelines/daily_ca_pipeline.py 2026-06-02

Sources: PIB (live, 24h) · IE PDF (pdfplumber) · SUJAS (monthly cache) · Wiki (weekly cache)
Filters: keyword → tier → ignore → rajasthan bonus → ChromaDB PYQ check
Selects: top 5 main + ranks 6-10 also-in-news
Content: Claude (EN authored, then HI authored fresh) → daily_ca_items
"""
import sys, re, datetime, html, json
from collections import defaultdict

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from pipelines import _common as C
from pipelines import rag_integration as rag

STOP = set("""a an the of to in on for and or with from this that these those is are was were be been
as at by it its his her their our your into over under about after before only also more most than
recently held given new latest year india indian who whom which what when where will would can may""".split())

# ─────────────────────────────────────────────────────────────────────────────
# SOURCES
# ─────────────────────────────────────────────────────────────────────────────
def fetch_pib(date):
    """Source 1 — PIB. Scrape Allrel.aspx, keep last-24h release titles."""
    items = []
    try:
        import requests
        from bs4 import BeautifulSoup
        url = "https://pib.gov.in/Allrel.aspx?reg=3&lang=1"
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 PaperSe/1.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        # PIB lists releases as <a> under content area; titles are link texts.
        seen = set()
        for a in soup.select("a[href*='PressReleseDetail'], a[href*='PressReleasePage'], ul.num li a, .content-area a"):
            txt = " ".join(a.get_text(" ", strip=True).split())
            if len(txt) < 25 or txt in seen:
                continue
            seen.add(txt)
            items.append({"source": "PIB", "title": txt, "text": txt, "url": url})
        # Fallback: any reasonably long anchor text on the page
        if not items:
            for a in soup.find_all("a"):
                txt = " ".join(a.get_text(" ", strip=True).split())
                if len(txt) >= 40 and txt not in seen:
                    seen.add(txt)
                    items.append({"source": "PIB", "title": txt, "text": txt, "url": url})
        C.log(f"   PIB: {len(items)} release titles (last listing)")
    except Exception as e:
        C.log(f"   ⚠ PIB fetch failed: {e}")
    return items[:120]


def _readable(chunk):
    """Reject garbled newspaper blocks (concatenated words from multi-column extract)."""
    words = chunk.split()
    wc = len(words)
    if not (10 <= wc <= 140):
        return False
    if max((len(w) for w in words), default=0) > 22:      # joined words like 'businessesandindividuals'
        return False
    if sum(len(w) for w in words) / wc > 10.5:            # mean token length
        return False
    if sum(1 for w in words if len(w) > 15) / wc > 0.18:  # tolerate a few joins, reject heavy garble
        return False
    # camelCase joins (TueSday, TheIndIanexPreSS, Jammumansays) signal masthead/garble
    if sum(1 for w in words if re.search(r"[a-z][A-Z]", w)) / wc > 0.10:
        return False
    alpha = sum(c.isalpha() or c.isspace() for c in chunk) / max(1, len(chunk))
    return alpha >= 0.78


def load_ie_pdf(date):
    """Source 2 — IE PDF via pdfplumber. Segment into candidate blocks."""
    items = []
    cand = [C.IE_DIR / f"{date.isoformat()}.pdf",
            C.UPLOADS / f"ie-delhi-{date.strftime('%d-%m-%Y')}.pdf"]
    path = next((p for p in cand if p.exists()), None)
    if not path:
        C.log(f"   ⚠ IE PDF not found at {cand[0]} (or uploads). Skipping IE source.")
        return items
    try:
        import pdfplumber
        blocks = []
        with pdfplumber.open(str(path)) as pdf:
            n = len(pdf.pages)
            for pg in pdf.pages[:min(n, 24)]:
                txt = pg.extract_text() or ""
                lines = [l.strip() for l in txt.split("\n") if len(l.strip()) >= 12]
                # sliding windows of ~4 lines → article-sized candidate blocks
                W, STEP = 4, 3
                for i in range(0, max(1, len(lines)), STEP):
                    chunk = " ".join(lines[i:i + W])
                    chunk = " ".join(chunk.split())
                    if _readable(chunk):
                        blocks.append(chunk)
        # de-dup near-identical
        seen, uniq = set(), []
        for b in blocks:
            key = b[:60].lower()
            if key not in seen:
                seen.add(key); uniq.append(b)
        for b in uniq:
            items.append({"source": "IE", "title": b[:90], "text": b})
        C.log(f"   IE PDF: {path.name} → {n} pages, {len(items)} candidate blocks")
    except Exception as e:
        C.log(f"   ⚠ IE PDF parse failed: {e}")
    return items


def load_sujas(date):
    """Source 3 — SUJAS. Parse on 1st of month; otherwise serve from cache."""
    month = date.strftime("%Y-%m")
    if date.day == 1:
        path = C.SUJAS_DIR / f"{month}.pdf"
        if not path.exists():
            C.log(f"   ⚠ SUJAS PDF not found for {month}; skipping.")
            return []
        try:
            import pdfplumber
            items = []
            with pdfplumber.open(str(path)) as pdf:
                for pg in pdf.pages:
                    txt = pg.extract_text() or ""
                    for para in re.split(r"\n\s*\n", txt):
                        para = " ".join(para.split())
                        if 8 <= len(para.split()) <= 160:
                            items.append({"source": "SUJAS", "title": para[:90], "text": para})
            C.sb_upsert("sujas_cache", {"month": month, "items": items}, on_conflict="month")
            C.log(f"   SUJAS: parsed {len(items)} items, cached for {month}")
            return items
        except Exception as e:
            C.log(f"   ⚠ SUJAS parse failed: {e}")
            return []
    # not the 1st → read cache
    rows = C.sb_select("sujas_cache", params={"month": f"eq.{month}", "limit": 1})
    if rows:
        items = rows[0].get("items") or []
        C.log(f"   SUJAS: {len(items)} items from cache ({month})")
        return items
    C.log(f"   SUJAS: no cache for {month} (only parsed on 1st); skipping.")
    return []


def fetch_wiki(date):
    """Source 4 — Wikipedia Current Events. Scrape Sundays; else from cache.
    Filter: elections / heads of state or government changes only."""
    week_start = (date - datetime.timedelta(days=date.weekday())).isoformat()  # Monday
    KEEP = ("elect", "president", "prime minister", "head of state", "head of government",
            "chancellor", "sworn in", "resign", "appointed", "coup", "referendum")
    if date.weekday() == 6:  # Sunday
        try:
            import requests
            from bs4 import BeautifulSoup
            url = "https://en.wikipedia.org/wiki/Portal:Current_events"
            r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 PaperSe/1.0"})
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            items = []
            for li in soup.select(".current-events-content li, .description li"):
                txt = " ".join(li.get_text(" ", strip=True).split())
                if len(txt) > 20 and any(k in txt.lower() for k in KEEP):
                    items.append({"source": "WIKI", "title": txt[:90], "text": txt})
            C.sb_upsert("wiki_cache", {"week_start": week_start, "items": items}, on_conflict="week_start")
            C.log(f"   WIKI: {len(items)} election/head-of-state items, cached ({week_start})")
            return items
        except Exception as e:
            C.log(f"   ⚠ WIKI fetch failed: {e}")
            return []
    rows = C.sb_select("wiki_cache", params={"week_start": f"eq.{week_start}", "limit": 1})
    if rows:
        items = rows[0].get("items") or []
        C.log(f"   WIKI: {len(items)} items from cache (week {week_start})")
        return items
    C.log(f"   WIKI: no cache for week {week_start} (only scraped Sundays); skipping.")
    return []


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORIES + FILTERS
# ─────────────────────────────────────────────────────────────────────────────
def tokenize(text):
    return {w for w in re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", (text or "").lower())
            if w not in STOP and len(w) >= 4}

def load_categories():
    cats = C.sb_select("ca_categories")
    for c in cats:
        # CORE keywords = category name + static topic link (high precision)
        core = tokenize(c.get("category")) | tokenize(c.get("static_topic_link"))
        # CAPTURE keywords = broader signal from what_to_capture phrases
        cap = set()
        for phrase in (c.get("what_to_capture") or []):
            cap |= tokenize(phrase)
        c["_core_kw"] = core
        c["_capture_kw"] = cap | core
        ign = set()
        for phrase in (c.get("what_to_ignore") or []):
            ign |= tokenize(phrase)
        c["_ignore_kw"] = ign
    C.log(f"   Loaded {len(cats)} CA categories from ca_categories")
    return cats

RAJ_KEYS = ("rajasthan", "jaipur", "jodhpur", "udaipur", "kota", "ajmer", "bikaner",
            "rajasthani", "marwar", "mewar", "jaisalmer", "rpsc", "raj.")

TIER_BASE = {1: 1.0, 2: 0.7, 3: 0.4}

def run_filters(item, cats):
    """5-filter chain. Returns enriched item or None (rejected)."""
    text = item["text"]
    toks = tokenize(text)
    low = text.lower()

    # FILTER 1 — keyword match. Require a CORE hit (category/topic word), not just
    # generic capture words, to avoid spurious matches. Score = 2*core + capture.
    best, best_score, best_core = None, 0, 0
    for c in cats:
        core_hits = len(toks & c["_core_kw"])
        if core_hits < 1:
            continue                       # must hit a core keyword
        score = 2 * core_hits + len(toks & c["_capture_kw"])
        if score > best_score:
            best, best_score, best_core = c, score, core_hits
    if not best:
        return None  # REJECT — no category core match

    # FILTER 3 — ignore check (matched category's ignore set)
    if len(toks & best["_ignore_kw"]) >= 2:
        return None  # REJECT — looks like ignored content

    # FILTER 2 — tier check
    tier = best.get("tier") or 3
    item_raj = any(k in low for k in RAJ_KEYS)
    if tier >= 4:
        return None
    if tier == 3 and not item_raj:
        return None

    # base priority
    priority = TIER_BASE.get(tier, 0.4) + 0.05 * best_score

    # FILTER 4 — rajasthan bonus
    if item_raj:
        priority += 0.3

    # FILTER 5 — ChromaDB PYQ check (only attach when genuinely similar)
    static_connect = best.get("static_topic_link") or best.get("category")
    exam_ref = None
    pyq = C.pyq_lookup(text, max_distance=0.33)
    if pyq:
        priority += 0.2
        exam_ref = f"{pyq['subject']} · asked {pyq['year']}"
        # only override the static link if the PYQ subject aligns with the category
        if pyq.get("subject") and best.get("static_subject") and \
           pyq["subject"].lower() == str(best["static_subject"]).lower():
            static_connect = pyq["topic"] or static_connect

    item.update({
        "category": best.get("category"),
        "tier": tier,
        "rajasthan_angle": item_raj or bool(best.get("rajasthan_angle")),
        "static_connect": static_connect,
        "static_subject": best.get("static_subject"),
        "exam_ref": exam_ref,
        "priority": round(priority, 3),
        "match_score": best_score,
        "match_core": best_core,
    })
    return item


# ─────────────────────────────────────────────────────────────────────────────
# CONTENT GENERATION (Claude)
# ─────────────────────────────────────────────────────────────────────────────
SYS_EN = ("You are PaperSe CA writer for RPSC RAS aspirants. Write exam-focused current "
          "affairs. Be crisp. Every bullet must be a testable fact. Never add opinions or "
          "analysis. Only facts that RPSC can test.")
SYS_HI = ("आप RPSC RAS अभ्यर्थियों के लिए PaperSe करेंट अफेयर्स लेखक हैं। परीक्षा-केंद्रित करेंट अफेयर्स "
          "ताज़ा हिंदी में मौलिक रूप से लिखें — अनुवाद नहीं। संक्षिप्त रहें। हर बुलेट एक परीक्षा-योग्य तथ्य हो। "
          "कोई राय या विश्लेषण नहीं — केवल वे तथ्य जो RPSC पूछ सकता है।")

def gen_main(item, lang):
    sysmsg = SYS_EN if lang == "EN" else SYS_HI
    hint = f"\nStatic chapter hint: {item.get('static_connect')}." if item.get("static_connect") else ""
    examh = f"\nRPSC has tested this topic before ({item['exam_ref']})." if item.get("exam_ref") else ""
    lang_word = "English" if lang == "EN" else "Hindi (Devanagari, freshly authored, not translated)"
    user = (f"Source ({item['source']}): {item['text']}\n"
            f"Category: {item.get('category')}.{hint}{examh}\n\n"
            f"Write in {lang_word}. Return ONLY JSON:\n"
            '{"title": "bold, max 10 words", "summary": "1 line what happened", '
            '"context": "2-3 lines background", "bullets": ["3-5 exam facts, specific numbers/names"], '
            '"static_connect": "which static chapter this links to"}')
    data, _ = C.claude_json(sysmsg, user, max_tokens=1000)
    data["bullets"] = data.get("bullets") or []
    return data

def gen_also(item):
    """Title + one-liner, EN and HI together."""
    user = (f"Source ({item['source']}): {item['text']}\nCategory: {item.get('category')}.\n\n"
            "Return ONLY JSON with a short title and a 1-2 line key-fact one-liner, in EN and HI "
            "(Hindi authored fresh, not translated):\n"
            '{"en": {"title": "...", "one_liner": "..."}, "hi": {"title": "...", "one_liner": "..."}}')
    data, _ = C.claude_json(SYS_EN, user, max_tokens=500)
    return data


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main(news_date, label_date):
    """
    Main pipeline. Fetches news from news_date (yesterday), labels output with label_date (today).
    news_date: used for PIB, IE PDF, SUJAS, Wikipedia fetching (the date of the news)
    label_date: used for output filenames, database records, PDF headers, Telegram captions (today)
    """
    C.log("=" * 64)
    C.log(f"PaperSe Daily CA Pipeline — news_date={news_date.isoformat()}  label_date={label_date.isoformat()}")
    C.log(f"  weekday={label_date.strftime('%A')}")
    C.log("=" * 64)

    # 1. FETCH
    C.log("\n[1] FETCH SOURCES")
    raw = []
    raw += fetch_pib(news_date)
    raw += load_ie_pdf(news_date)
    raw += load_sujas(news_date)
    raw += fetch_wiki(news_date)
    C.log(f"   → total raw items fetched: {len(raw)}")
    if not raw:
        C.log("\n✗ No source items fetched. Aborting (no data).")
        return None

    # 2. FILTER
    C.log("\n[2] FILTER (keyword → tier → ignore → rajasthan → chromaDB)")
    cats = load_categories()
    approved = []
    for it in raw:
        res = run_filters(it, cats)
        if res:
            approved.append(res)
    C.log(f"   → approved {len(approved)} / {len(raw)} items")
    if not approved:
        C.log("\n✗ No items passed filters. Slow news day / source mismatch.")
        return None

    # 2.5. RAG ENRICHMENT (3-layer intelligence: ChromaDB PYQs + topic_kb)
    C.log("\n[2.5] RAG ENRICHMENT (ChromaDB PYQs + topic_kb priorities)")
    # Build ca_category_map for enrichment
    ca_map = {i: item.get("category") for i, item in enumerate(approved)}
    # Enrich with 3-layer RAG
    approved = rag.enrich_ca_items(approved, ca_category_map=ca_map)
    # Re-sort by final_priority_score (combining all layers)
    approved.sort(key=lambda x: x.get("final_priority_score", x.get("priority", 0.5)), reverse=True)

    # 3. RANK + SELECT  (dedup by category for variety in the main 5)
    approved.sort(key=lambda x: x["priority"], reverse=True)
    main_items, seen_cat = [], set()
    for it in approved:
        if it["category"] in seen_cat:
            continue
        seen_cat.add(it["category"]); main_items.append(it)
        if len(main_items) == 5:
            break
    chosen_ids = {id(x) for x in main_items}
    rest = [x for x in approved if id(x) not in chosen_ids]
    also_items = rest[:5]   # ranks 6-10
    for it in main_items: it["is_main"] = True
    for it in also_items: it["is_main"] = False
    C.log(f"   → MAIN (is_main=true): {len(main_items)} | ALSO IN NEWS: {len(also_items)}")
    for i, it in enumerate(main_items, 1):
        C.log(f"      {i}. [{it['priority']}] {it['category']} · {it['source']} :: {it['title'][:70]}")

    # 4. CONTENT GENERATION → store
    C.log("\n[3] CONTENT GENERATION (Claude, EN + HI authored fresh)")
    C.sb_delete("daily_ca_items", {"date": label_date.isoformat()})  # idempotent re-run
    main_rows_inserted = []
    for i, it in enumerate(main_items, 1):
        C.log(f"   • main {i}/{len(main_items)}: {it['category']} …")
        en = gen_main(it, "EN")
        hi = gen_main(it, "HI")
        base = dict(date=label_date.isoformat(), category=it["category"], tier=it["tier"],
                    source=it["source"], rajasthan_angle=it["rajasthan_angle"],
                    priority=it.get("final_priority_score", it.get("priority", 0.5)), is_main=True)

        row_en = {**base, "language": "EN", "title": en.get("title"), "summary": en.get("summary"),
                  "context": en.get("context"), "bullets": en.get("bullets"),
                  "static_connect": en.get("static_connect") or it.get("static_connect")}
        row_hi = {**base, "language": "HI", "title": hi.get("title"), "summary": hi.get("summary"),
                  "context": hi.get("context"), "bullets": hi.get("bullets"),
                  "static_connect": hi.get("static_connect") or it.get("static_connect")}
        ins = C.sb_insert("daily_ca_items", [row_en, row_hi])
        # keep the EN row id as canonical "source item" for MCQs
        en_id = next((r["id"] for r in ins if r["language"] == "EN"), ins[0]["id"])
        main_rows_inserted.append({"id": en_id, "category": it["category"], "title": en.get("title")})

    for j, it in enumerate(also_items, 1):
        C.log(f"   • also {j}/{len(also_items)}: {it['category']} …")
        a = gen_also(it)
        base = dict(date=label_date.isoformat(), category=it["category"], tier=it["tier"],
                    source=it["source"], rajasthan_angle=it["rajasthan_angle"],
                    priority=it["priority"], is_main=False,
                    static_connect=it.get("static_connect"))
        C.sb_insert("daily_ca_items", [
            {**base, "language": "EN", "title": a["en"]["title"], "one_liner": a["en"]["one_liner"]},
            {**base, "language": "HI", "title": a["hi"]["title"], "one_liner": a["hi"]["one_liner"]},
        ], returning=False)

    total = C.sb_count("daily_ca_items", {"date": f"eq.{label_date.isoformat()}"})
    C.log(f"\n✓ STEP 2 complete — {total} rows in daily_ca_items for {label_date.isoformat()} "
          f"({len(main_items)} main ×2 langs + {len(also_items)} also ×2 langs)")
    C.log(f"  main source-item ids (for MCQs): {[r['id'] for r in main_rows_inserted]}")
    return main_rows_inserted


if __name__ == "__main__":
    label_date = C.parse_date(sys.argv[1]) if len(sys.argv) > 1 else datetime.date.today()
    news_date = label_date - datetime.timedelta(days=1)
    C.log(f"\nEntry point: label_date={label_date.isoformat()} news_date={news_date.isoformat()}")
    out = main(news_date, label_date)
    sys.exit(0 if out else 1)
