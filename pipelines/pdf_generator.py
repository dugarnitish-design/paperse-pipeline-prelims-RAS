#!/usr/bin/env python3
"""
STEP 4 — PDF generator. Two PDFs/day:
  outputs/daily-ca/EN/YYYY-MM-DD.pdf
  outputs/daily-ca/HI/YYYY-MM-DD.pdf

  python3 pipelines/pdf_generator.py 2026-06-02

NOTE on engine: the spec asks for reportlab, but reportlab cannot shape Devanagari
(Hindi matras/conjuncts) and cannot render colour emoji — both are required here
(emojis, Hindi, clickable links, diagonal watermark). This repo already renders
Hindi via WeasyPrint (generate-pdf-hindi.py). So both PDFs are produced from one
HTML/CSS template through WeasyPrint, which renders Devanagari + emoji + hyperlinks
+ a diagonal watermark correctly. Set via env so the homebrew libs are found.

Hindi font: NotoSansDevanagari (bundled in ./fonts) is loaded via @font-face so the
text layer is searchable/copyable (the old Apple .ttc fallback produced garbled,
non-Unicode glyphs on copy/extract). This is environment-independent (works on
Railway too) because the font ships in the repo.
"""
import os
os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH",
                      "/opt/homebrew/lib:" + os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", ""))
import sys, html, datetime
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from pipelines import _common as C
from pipelines import rag_integration as rag

TEST_URL = "https://paperse.in/test/{date}"
SOCIAL = [("paperse.in", "https://paperse.in"),
          ("instagram.com/paperse.in", "https://instagram.com/paperse.in"),
          ("t.me/papersecivils", "https://t.me/papersecivils")]

# ── Category colour coding ──────────────────────────────────────────────────────
# label text + left border of each article use the matched colour.
DEFAULT_CAT_COLOR = "#11203a"
def category_color(category):
    c = (category or "").lower()
    if "rajasthan" in c:                              return "#F57F17"  # amber
    if "health" in c or "population" in c:            return "#2E7D32"  # green
    if "sport" in c:                                  return "#6A1B9A"  # purple
    if "book" in c:                                   return "#4E342E"  # brown
    if "science" in c or "tech" in c:                 return "#00695C"  # teal
    if "bill" in c or "legislation" in c or "law" in c:  return "#B71C1C"  # red
    if "international" in c:                           return "#1565C0"  # blue
    if "politic" in c or "election" in c or "polity" in c or "national" in c:
        return "#E65100"  # orange (national politics)
    return DEFAULT_CAT_COLOR

# ── Stylesheet (interpolates bundled font paths + running-footer date) ──────────
def build_css(pretty_date):
    reg  = (C.ROOT / "fonts" / "NotoSansDevanagari-Regular.ttf").as_uri()
    bold = (C.ROOT / "fonts" / "NotoSansDevanagari-Bold.ttf").as_uri()
    return f"""
@font-face {{ font-family:'NotoDeva'; src:url('{reg}'); font-weight:normal; font-style:normal; }}
@font-face {{ font-family:'NotoDeva'; src:url('{bold}'); font-weight:bold;  font-style:normal; }}

@page {{
  size: A4;
  margin: 20mm;
  @bottom-left   {{ content:"paperse.in"; font-family:'Helvetica Neue',Arial,sans-serif; font-size:8pt; color:#9ca3af; }}
  @bottom-center {{ content:"{pretty_date}"; font-family:'Helvetica Neue',Arial,sans-serif; font-size:8pt; color:#9ca3af; }}
  @bottom-right  {{ content:"Page " counter(page); font-family:'Helvetica Neue',Arial,sans-serif; font-size:8pt; color:#9ca3af; }}
}}
* {{ box-sizing:border-box; }}
html, body {{ background:#ffffff; }}
body {{ max-width:170mm; margin:0; color:#1f2937; font-size:12px; line-height:1.4;
        font-family:'Helvetica Neue', Arial, sans-serif; }}
body.hi, .hi {{ font-family:'NotoDeva','Noto Sans Devanagari', sans-serif; }}

.watermark {{ position:fixed; top:42%; left:8%; transform:rotate(-35deg);
              font-size:60pt; color:rgba(244,98,42,0.05); font-weight:800; z-index:-1; }}

.head {{ border-bottom:3px solid #f4622a; padding-bottom:8px; margin-bottom:8px; }}
.head h1 {{ font-size:28px; margin:0; color:#000000; font-weight:700; letter-spacing:-0.01em; }}
.head .sub {{ font-size:11px; color:#6b7280; margin-top:3px; }}

.item {{ border-left:4px solid {DEFAULT_CAT_COLOR}; padding-left:12px; margin-bottom:12px; }}
.cat {{ font-size:10px; font-weight:800; letter-spacing:0.12em; text-transform:uppercase;
        color:{DEFAULT_CAT_COLOR}; margin-bottom:3px; }}
.title {{ font-size:18px; font-weight:700; color:#1a1a1a; margin:1px 0 2px; line-height:1.2; }}
.summary {{ font-size:14px; font-weight:600; font-style:italic; color:#4b5563; margin-bottom:2px; }}
.context {{ font-size:12px; color:#1f2937; line-height:1.4; margin:0 0 3px; }}
ul {{ margin:4px 0 5px 0; padding-left:18px; }}
li {{ font-size:12px; color:#1f2937; margin-bottom:2px; line-height:1.4; }}
li b, li strong {{ color:#000000; font-weight:700; }}

.rpsc-angle {{ background:#F3E5F5; border-left:3px solid #7B1FA2; padding:5px 10px;
               font-size:12px; font-style:italic; color:#6A1B9A; margin:4px 0; border-radius:0 4px 4px 0; }}

.static {{ font-size:12px; color:#6b7280; font-variant:small-caps; letter-spacing:0.03em; margin-top:3px; }}
.static b {{ color:#374151; }}

.tags {{ margin-top:4px; }}
.badge {{ display:inline-block; font-size:8.5px; font-weight:700; padding:2px 7px;
          border-radius:3px; margin-right:5px; }}

.also h2, .sec h2 {{ font-size:13px; font-weight:800; color:#11203a; border-left:4px solid #f4622a;
                     padding-left:8px; margin:8px 0 6px; text-transform:uppercase; letter-spacing:.04em; }}
.also li {{ font-size:12.5px; margin-bottom:3px; color:#374151; }}
.also .t {{ font-weight:700; color:#1a1a1a; }}

.pyqbox {{ background:#f3f4f6; border:1px solid #e5e7eb; border-radius:8px; padding:7px 10px; }}
.pyqitem {{ margin:1px 0 4px; }}
.pyqfrom {{ font-size:8.5px; font-weight:700; color:#f4622a; text-transform:uppercase;
            letter-spacing:.03em; margin-bottom:2px; }}
.pyqq {{ font-size:12.5px; font-weight:700; color:#11203a; margin-bottom:3px; }}
.pyqopt {{ font-size:12px; margin:0 0 0 6px; color:#374151; }}
.pyqans {{ font-size:12px; margin-top:3px; font-weight:700; color:#15803d; }}
.yearbadge {{ float:right; background:#11203a; color:#ffffff; font-size:8.5px; font-weight:700;
              padding:2px 9px; border-radius:10px; margin-left:8px; }}
.pyqsrc {{ font-size:8.3px; color:#6b7280; margin-top:2px; }}
.pyqdiv {{ border:none; border-top:1px solid #e5e7eb; margin:5px 0; }}

.connects {{ font-size:12px; color:#374151; background:#f8fafc; border:1px solid #e5e7eb;
             border-radius:7px; padding:6px 10px; margin-top:8px; }}
.cta {{ margin-top:8px; padding:7px 11px; background:#fff7ed; border:1px solid #fed7aa;
        border-radius:8px; font-size:12px; }}
.cta a {{ color:#c2410c; font-weight:800; text-decoration:none; }}
.footer {{ margin-top:9px; border-top:2px solid #11203a; padding-top:7px; text-align:center; font-size:12px; }}
.footer a {{ color:#11203a; text-decoration:none; margin:0 7px; }}
a {{ color:#2563eb; }}
"""

import re as _re
def esc(s):
    return html.escape(str(s or ""))

def md_bold(s):
    """Convert **text** markdown → <b>text</b> HTML (safe: escape first, then convert)."""
    return _re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', html.escape(str(s or "")))

NUM2LET = {"1": "A", "2": "B", "3": "C", "4": "D"}

def _build_tag_html(item):
    """Return tag spans for this item based on topic_kb + PYQ data.
    If the attributes are already on the dict (in-pipeline), use them.
    If reading from Supabase (build_pdf path), do a quick topic_kb lookup."""
    # --- enrich from topic_kb if not already on item ---
    if "topic_kb_never_skipped" not in item and item.get("category"):
        try:
            rows = C.sb_select("topic_kb", params={
                "topic": f"ilike.%{item['category']}%", "limit": "1"})
            if rows:
                kb = rows[0]
                item["topic_kb_never_skipped"] = bool(kb.get("never_skipped"))
                item["topic_kb_trajectory"] = kb.get("trajectory", "")
        except Exception:
            pass
    # --- enrich PYQ tag if not already set ---
    if "pyq_match_year" not in item:
        text = f"{(item.get('title') or '').replace('**', '')} {item.get('summary') or ''}".strip()
        if text:
            m = C.pyq_lookup(text, n=1, max_distance=0.35)
            if m:
                item["pyq_match_year"] = m.get("year")
    # --- build HTML (badge = bg colour, text colour) ---
    tags = []
    if item.get("pyq_match_year"):
        tags.append((f"RPSC {item['pyq_match_year']}", "#FFE0B2", "#9A3412"))
    if item.get("topic_kb_never_skipped"):
        # spec: yellow background, bold, small
        tags.append(("Never skipped in 6 yrs", "#FEF08A", "#713F12"))
    if item.get("topic_kb_trajectory") == "rising":
        tags.append(("Rising trend", "#DBEAFE", "#1D4ED8"))
    if not tags:
        return ""
    spans = "".join(
        f'<span class="badge" style="background:{bg};color:{fg};">{t}</span>'
        for t, bg, fg in tags)
    return f'<div class="tags">{spans}</div>'

def _haiku_pyq_relevant(article_title, pyq_text, pyq_topic):
    """Ask Claude Haiku whether a PYQ is genuinely relevant to the news story.
    Returns True only on a YES. Any error → False (drop the candidate)."""
    user = (f"News story topic: {article_title}\n"
            f"PYQ question: {pyq_text}\n"
            f"PYQ topic tag: {pyq_topic}\n\n"
            "Is this PYQ genuinely relevant to the news story?\n"
            "Answer only: YES or NO")
    try:
        ans = C.claude_text(
            "You judge whether a past exam question is genuinely relevant to a news "
            "story for an exam-prep daily. Reply with ONLY 'YES' or 'NO'.",
            user, max_tokens=4, model=C.HAIKU_MODEL).strip().upper()
        return ans.startswith("YES")
    except Exception as e:
        C.log(f"   ⚠ Haiku relevance check failed: {e}")
        return False

def find_linked_pyqs(en_items, candidate_max_distance=0.70, candidate_n=3, per_article=2):
    """For each EN main item, pull top vector candidates from ChromaDB, then keep
    only those a Claude Haiku relevance check confirms (YES). Max `per_article`
    kept per item. Returns [{item_idx, score, pyq}] in news order; an empty list
    means the PYQ section is skipped entirely."""
    out = []
    for i, it in enumerate(en_items or []):
        title = (it.get("title") or "").replace("**", "").strip()
        text = f"{title} {it.get('summary') or ''}".strip()
        if not text:
            continue
        cands = C.pyq_lookup_many(text, n=candidate_n, max_distance=candidate_max_distance)
        C.log(f"   PYQ search: {title[:70]}")
        if not cands:
            C.log(f"      → no vector candidates within {candidate_max_distance}")
            continue
        kept = 0
        for c in cands:
            if kept >= per_article:
                break
            try:
                rows = C.sb_select("questions", params={
                    "year": f"eq.{c['year']}", "q_no": f"eq.{c['q_no']}", "limit": "1"})
            except Exception as e:
                C.log(f"   ⚠ PYQ fetch failed for {c['year']} Q{c['q_no']}: {e}")
                rows = None
            if not rows:
                continue
            pyq = rows[0]
            pyq_topic = " / ".join(p for p in (c.get("subject"), c.get("topic")) if p)
            relevant = _haiku_pyq_relevant(title, pyq.get("question") or "", pyq_topic)
            C.log(f"      cand RPSC RAS {c['year']} Q{c['q_no']} score={c['score']:.3f} "
                  f"[{c.get('topic')}] → Haiku: {'YES' if relevant else 'NO'}")
            if relevant:
                out.append({"item_idx": i, "score": c["score"], "pyq": pyq})
                kept += 1
    return out   # already in news order (outer loop is item order)

def render_pyq_section(linked_pyqs, lang, main_items, L):
    """Render the 'PYQs Linked to Today's News' block. Empty string if no matches.
    Each Q is in a light-grey box with a year badge floated to the right."""
    if not linked_pyqs:
        return ""
    is_hi = (lang == "HI")
    blocks = []
    for k, lp in enumerate(linked_pyqs):
        idx = lp["item_idx"]
        headline = ""
        if 0 <= idx < len(main_items):
            headline = str(main_items[idx].get("title") or "").replace("**", "")
        q = lp["pyq"]
        qtext = (q.get("question_hi") if is_hi and q.get("question_hi") else q.get("question")) or ""
        corr_let = NUM2LET.get(str(q.get("correct_ans") or "").strip(), "")
        opt_html, corr_text = "", ""
        for n in ("1", "2", "3", "4"):
            txt = (q.get(f"option_{n}_hi") if is_hi and q.get(f"option_{n}_hi") else q.get(f"option_{n}"))
            if not txt:
                continue
            let = NUM2LET[n]
            is_corr = (let == corr_let)
            if is_corr:
                corr_text = txt
            mark = " &#10003;" if is_corr else ""        # ✓ green check
            style = "color:#15803d; font-weight:700;" if is_corr else ""
            opt_html += f'<div class="pyqopt" style="{style}">({let}) {esc(txt)}{mark}</div>'
        year_badge = f'<span class="yearbadge">RPSC RAS {esc(q.get("year"))}</span>'
        from_html = f'<div class="pyqfrom">{L["from"]}: {esc(headline)}</div>' if headline else ""
        ans_html = f'<div class="pyqans">{L["answer"]}: ({corr_let}) {esc(corr_text)}</div>'
        div = "" if k == len(linked_pyqs) - 1 else '<hr class="pyqdiv">'
        blocks.append(
            f'<div class="pyqitem">{year_badge}{from_html}'
            f'<div class="pyqq">{esc(qtext)}</div>{opt_html}{ans_html}</div>{div}')
    return (f'<div class="sec"><h2>{L["pyq_head"]}</h2>'
            f'<div class="pyqbox">{"".join(blocks)}</div></div>')

def render_html(date, lang, main_items, also_items, labels, linked_pyqs=None):
    ds = date.isoformat()
    pretty = date.strftime("%d %B %Y")
    L = labels
    items_html = []
    for it in main_items:
        color = category_color(it.get("category"))
        # Bullets: **text** → <b>text</b> (key names/numbers/dates rendered bold black)
        bullets = "".join(f"<li>{md_bold(b)}</li>" for b in (it.get("bullets") or []))

        # RAG tags (from item attributes set during enrichment, or live topic_kb lookup)
        tags_html = _build_tag_html(it)

        # Static connect: just the chapter name (Claude now returns short form)
        static_val = (it.get("static_connect") or "").split("—")[0].split(":")[0].strip()

        rpsc_html = (f'<div class="rpsc-angle">{L["rpsc_angle"]}: {esc(it.get("rpsc_angle"))}</div>'
                     if it.get("rpsc_angle") else "")
        static_html = (f'<div class="static">{L["static"]}: <b>{esc(static_val)}</b></div>'
                       if static_val else "")

        items_html.append(f"""
        <div class="item" style="border-left-color:{color};">
          <div class="cat" style="color:{color};">{esc(it.get('category'))}</div>
          <div class="title">{esc(it.get('title')).replace('**','')}</div>
          <div class="summary">{esc(it.get('summary'))}</div>
          <div class="context">{esc(it.get('context'))}</div>
          <ul>{bullets}</ul>
          {rpsc_html}
          {static_html}
          {tags_html}
        </div>""")

    also_html = ""
    if also_items:
        lis = "".join(
            f"<li><span class='t'>{esc(a.get('title')).replace('**','')}</span> — {esc(a.get('one_liner'))}</li>"
            for a in also_items)
        also_html = f"<div class='also'><h2>{L['also']}</h2><ul>{lis}</ul></div>"

    connects = [it.get("static_connect") for it in main_items if it.get("static_connect")]
    connects_html = ""
    if connects:
        connects_html = (f"<div class='connects'><b>{L['connects']}:</b> "
                         + " · ".join(esc(c) for c in connects) + "</div>")

    pyq_html = render_pyq_section(linked_pyqs or [], lang, main_items, L)

    social = " ".join(f"<a href='{u}'>{esc(t)}</a>" for t, u in SOCIAL)
    body_cls = "hi" if lang == "HI" else "en"
    css = build_css(pretty)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head>
<body class="{body_cls}">
  <div class="watermark">paperse.in</div>
  <div class="head"><h1>PaperSe {L['daily']} — {pretty}</h1>
     <div class="sub">{L['sub']}</div></div>
  {''.join(items_html) if items_html else f"<p>{L['none']}</p>"}
  {also_html}
  {pyq_html}
  {connects_html}
  <div class="cta"><b>{L['test']}</b> &nbsp;
     <a href="{TEST_URL.format(date=ds)}">paperse.in/test/{ds}</a><br>
     {L['test_sub']}</div>
  <div class="footer"><b>{L['follow']}</b><br>{social}</div>
</body></html>"""

LABELS = {
    "EN": {"daily": "Daily CA", "sub": "RPSC RAS — exam-focused current affairs",
           "static": "Static connect", "also": "Also in the News",
           "connects": "Static connects today", "test": "Test yourself on today's CA",
           "test_sub": "Timed test • 5-8 questions • 1 min per question",
           "follow": "Follow PaperSe", "none": "No items today.",
           "pyq_head": "&#9998; PYQ of the Day", "from": "From", "answer": "Answer",
           "rpsc_angle": "RPSC Angle"},
    "HI": {"daily": "डेली करेंट अफेयर्स", "sub": "RPSC RAS — परीक्षा-केंद्रित करेंट अफेयर्स",
           "static": "स्टैटिक कनेक्ट", "also": "अन्य प्रमुख समाचार",
           "connects": "आज के स्टैटिक कनेक्ट", "test": "आज के CA पर खुद को परखें",
           "test_sub": "टाइम्ड टेस्ट • 5-8 प्रश्न • प्रति प्रश्न 1 मिनट",
           "follow": "PaperSe को फॉलो करें", "none": "आज कोई आइटम नहीं।",
           "pyq_head": "&#9998; आज का PYQ", "from": "स्रोत", "answer": "उत्तर",
           "rpsc_angle": "RPSC कोण"},
}

def build_pdf(date, lang, linked_pyqs=None):
    ds = date.isoformat()
    main_items = C.sb_select("daily_ca_items", params={
        "date": f"eq.{ds}", "is_main": "eq.true", "language": f"eq.{lang}", "order": "priority.desc"})
    also_items = C.sb_select("daily_ca_items", params={
        "date": f"eq.{ds}", "is_main": "eq.false", "language": f"eq.{lang}", "order": "priority.desc"})
    if not main_items:
        C.log(f"   ⚠ no {lang} main items for {ds}; skipping {lang} PDF")
        return None
    html_str = render_html(date, lang, main_items, also_items, LABELS[lang], linked_pyqs)
    from weasyprint import HTML
    outdir = C.OUT_EN if lang == "EN" else C.OUT_HI
    out = outdir / f"{ds}.pdf"
    doc = HTML(string=html_str).render()
    npages = len(doc.pages)
    doc.write_pdf(str(out))
    C.log(f"   ✓ {lang} PDF → {out}  ({len(main_items)} main + {len(also_items)} also, {npages} page(s))")
    if npages > 2:
        C.log(f"     ⚠ {npages} pages (>2). Trim items or shrink fonts for the 2-page cap.")
    return out

def main(date):
    C.log("=" * 64)
    C.log(f"STEP 4 — PDF Generator — {date.isoformat()}  (engine: WeasyPrint)")
    C.log("=" * 64)
    # Compute PYQ matches once from EN main items; both PDFs share them
    # (EN & HI main items align by index — both ordered priority.desc).
    ds = date.isoformat()
    en_main = C.sb_select("daily_ca_items", params={
        "date": f"eq.{ds}", "is_main": "eq.true", "language": "eq.EN", "order": "priority.desc"})
    linked_pyqs = find_linked_pyqs(en_main)
    C.log(f"   → {len(linked_pyqs)} PYQ(s) linked to today's news")
    en = build_pdf(date, "EN", linked_pyqs)
    hi = build_pdf(date, "HI", linked_pyqs)
    if en or hi:
        C.log(f"\n✓ STEP 4 complete — EN={'ok' if en else 'skip'} · HI={'ok' if hi else 'skip'}")
        return [p for p in (en, hi) if p]
    C.log("\n✗ STEP 4 produced no PDFs (no items).")
    return None

if __name__ == "__main__":
    d = C.parse_date(sys.argv[1]) if len(sys.argv) > 1 else datetime.date.today()
    sys.exit(0 if main(d) else 1)
