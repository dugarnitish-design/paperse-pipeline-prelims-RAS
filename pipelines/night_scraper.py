#!/usr/bin/env python3
"""
Combined nightly producer — run on the Mac at 23:30 IST (via launchd). It does ALL
the heavy work so the Railway services stay slim (no torch/chromadb → no OOM):

  1. Scrape the day's PIB + IE content.
  2. Precompute PYQ candidates for each article (ChromaDB + sentence-transformers,
     Mac-only) and upsert to Supabase pib_cache.pyq / ie_cache.pyq.
  3. Run the full scoring pipeline (Layer 2 pre-reject → keyword scoring → Layer 3
     Claude relevance → RAG enrichment → rank → MAIN/ALSO selection) and write the
     ranked candidates to Supabase daily_scored_items (keyed by label_date = day+1).

The Railway pipeline (00:00 IST / 18:30 UTC cron) then reads daily_scored_items and
only authors + formats + publishes — it never loads an embedding model.

  python3 pipelines/night_scraper.py [YYYY-MM-DD]   # defaults to today (IST)

Never crashes — a failure in one stage is logged and the rest still runs; exits 0.
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


def _attach_pyq(items):
    """Precompute PYQ candidates (C.pyq_lookup_many) for each scraped item and set
    it["pyq"]. Best-first, up to 3 within distance 0.70 — the superset every
    downstream consumer (scorer @0.33, PDF link @0.70, RAG boost @0.35) reads from.
    Mac-only (loads the embedding model); failures are non-fatal (pyq → None)."""
    done = 0
    for it in items:
        try:
            text = f"{it.get('title') or ''} {it.get('summary') or it.get('text') or it.get('full_text') or ''}".strip()
            it["pyq"] = C.pyq_lookup_many(text, n=3, max_distance=0.70) if text else []
            done += 1
        except Exception as e:
            it["pyq"] = None
            C.log(f"   ⚠ pyq precompute failed for one item: {e}")
    return done


def main(date=None):
    date = C.parse_date(date) if isinstance(date, str) else (date or _today_ist())
    label_date = date + datetime.timedelta(days=1)   # the brief is published next day
    C.log("=" * 64)
    C.log(f"NIGHT SCRAPER — scraping {date.isoformat()} "
          f"(now IST {datetime.datetime.now(IST):%Y-%m-%d %H:%M})")
    C.log("=" * 64)

    pib_n = pib_saved = ie_n = ie_saved = 0

    # ── PIB ─────────────────────────────────────────────────────────────────── #
    try:
        from pipelines import pib_scraper
        items = pib_scraper.scrape_pib(date)
        pib_n = len(items)
        if items:
            _attach_pyq(items)                       # precompute PYQ → it["pyq"]
            pib_saved = pib_scraper.write_supabase(date, items)
        C.log(f"   ✓ PIB: scraped {pib_n}, saved {pib_saved} → pib_cache (with PYQ)")
    except Exception as e:
        C.log(f"   ✗ PIB failed (continuing with IE): {e}")
        traceback.print_exc()

    # ── IE ────────────────────────────────────────────────────────────────────
    try:
        from pipelines import ie_scraper
        items = ie_scraper.fetch_ie_articles(date, force=True)   # live scrape (ignore cache)
        ie_n = len(items)
        if items:
            _attach_pyq(items)
            ie_saved = ie_scraper.write_supabase(date, items)
        C.log(f"   ✓ IE: scraped {ie_n}, saved {ie_saved} → ie_cache (with PYQ)")
    except Exception as e:
        C.log(f"   ✗ IE failed: {e}")
        traceback.print_exc()

    # ── SCORE + RANK → daily_scored_items ──────────────────────────────────────
    # Heavy half (Layer 2/3 + RAG/ChromaDB) runs HERE on the Mac so Railway doesn't.
    scored = 0
    try:
        from pipelines import daily_ca_pipeline as P
        sel = P.score_and_select(date, label_date)
        if sel and sel.get("main_items"):
            payload = {
                "main_items": _jsonable(sel["main_items"]),
                "also_items": _jsonable(sel["also_items"]),
                "approved":   _jsonable(sel["approved"]),
                "news_date":  date.isoformat(),
            }
            C.sb_upsert("daily_scored_items",
                        {"date": label_date.isoformat(), "payload": payload},
                        on_conflict="date")
            scored = len(sel["main_items"]) + len(sel["also_items"])
            C.log(f"   ✓ SCORED: {len(sel['main_items'])} MAIN + {len(sel['also_items'])} ALSO "
                  f"→ daily_scored_items[{label_date.isoformat()}]")
        else:
            C.log("   ⚠ scoring produced no items — daily_scored_items not written")
    except Exception as e:
        C.log(f"   ✗ scoring failed (Railway will have no pre-scored items): {e}")
        traceback.print_exc()

    C.log("-" * 64)
    C.log(f"NIGHT SCRAPER DONE — PIB {pib_saved}/{pib_n} · IE {ie_saved}/{ie_n} · "
          f"scored {scored} for {date.isoformat()} → publish {label_date.isoformat()}")
    return {"date": date.isoformat(), "pib": pib_saved, "ie": ie_saved, "scored": scored}


def _jsonable(items):
    """Keep only JSON-serializable values so the payload upserts cleanly into jsonb
    (drops any stray sets/objects a filter may have stashed on an item)."""
    import json
    out = []
    for it in items:
        clean = {}
        for k, v in it.items():
            try:
                json.dumps(v)
                clean[k] = v
            except (TypeError, ValueError):
                continue
        out.append(clean)
    return out


if __name__ == "__main__":
    arg = next((a for a in sys.argv[1:] if not a.startswith("-")), None)
    try:
        main(arg)
    except Exception as e:
        C.log(f"NIGHT SCRAPER fatal: {e}")
        traceback.print_exc()
    sys.exit(0)   # always 0 — launchd should not flag a partial-source failure
