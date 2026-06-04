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
"""
import os
os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH",
                      "/opt/homebrew/lib:" + os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", ""))
import sys, html, datetime
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from pipelines import _common as C
from pipelines import rag_integration as rag

TEST_URL = "https://paperse.in/test/{date}"
# NOTE: colour emoji (SBIX) don't render in WeasyPrint/cairo on this box (no B/W
# emoji font installed), so we use clean typographic markers instead of tofu boxes.
# Install Noto Emoji and add it to the font stack to show literal emojis.
SOCIAL = [("paperse.in", "https://paperse.in"),
          ("instagram.com/paperse.in", "https://instagram.com/paperse.in"),
          ("t.me/papersecivils", "https://t.me/papersecivils")]

CSS = """
@page { size: A4; margin: 14mm 13mm 16mm 13mm; }
* { box-sizing: border-box; }
body { font-family: 'Helvetica Neue', Arial, 'Noto Sans Devanagari', 'Kohinoor Devanagari', 'Apple Color Emoji', sans-serif;
       color: #1f2937; font-size: 10.3pt; line-height: 1.5; }
.hi { font-family: 'Noto Sans Devanagari','Kohinoor Devanagari','Devanagari Sangam MN','Apple Color Emoji', sans-serif; }
.cat, .static, .cta { font-family: 'Helvetica Neue', Arial, 'Apple Color Emoji', 'Noto Sans Devanagari','Kohinoor Devanagari', sans-serif; }
.watermark { position: fixed; top: 42%; left: 8%; transform: rotate(-35deg);
             font-size: 60pt; color: rgba(244,98,42,0.06); font-weight: 800; z-index: -1; }
.head { border-bottom: 3px solid #f4622a; padding-bottom: 7px; margin-bottom: 12px; }
.head h1 { font-size: 18pt; margin: 0; color: #11203a; font-weight: 800; }
.head .sub { font-size: 9pt; color: #6b7280; margin-top: 2px; }
.item { margin-bottom: 13px; padding-bottom: 11px; border-bottom: 1px solid #eceef2; }
.cat { font-size: 8.8pt; font-weight: 800; letter-spacing: .05em; text-transform: uppercase;
       color: #f4622a; }
.cat .dot { color: #f4622a; font-size: 7pt; vertical-align: middle; }
.title { font-size: 12pt; font-weight: 800; color: #11203a; margin: 2px 0 3px; }
.summary { font-weight: 600; color: #111827; }
.context { color: #374151; margin: 3px 0 5px; }
ul { margin: 4px 0 5px 0; padding-left: 17px; }
li { margin-bottom: 2px; }
.static { font-size: 9pt; color: #2563eb; }
.static b { color: #1e40af; }
.also h2, .sec h2 { font-size: 11.5pt; font-weight: 900; color: #11203a;
                    border-left: 4px solid #f4622a; padding-left: 7px;
                    margin: 14px 0 7px; text-transform: uppercase; letter-spacing:.04em; }
.also li { margin-bottom: 4px; }
.also .t { font-weight: 700; }
.connects { font-size: 9pt; color: #374151; background:#f8fafc; border:1px solid #e5e7eb;
            border-radius:7px; padding:8px 10px; margin-top:10px; }
.cta { margin-top: 11px; padding: 9px 11px; background:#fff7ed; border:1px solid #fed7aa;
       border-radius:8px; font-size:9.4pt; }
.cta a { color:#c2410c; font-weight:800; text-decoration:none; }
.footer { margin-top: 12px; border-top: 2px solid #11203a; padding-top: 7px; text-align:center;
          font-size: 9pt; }
.footer a { color:#11203a; text-decoration:none; margin: 0 7px; }
a { color:#2563eb; }
.pyqitem { margin: 6px 0; }
.pyqfrom { font-size: 8.3pt; font-weight:700; color:#f4622a; text-transform:uppercase; letter-spacing:.03em; margin-bottom:3px; }
.pyqq { font-weight:700; color:#11203a; margin-bottom:4px; }
.pyqopt { margin: 1px 0 1px 6px; color:#374151; font-size:9.6pt; }
.pyqans { margin-top:4px; font-weight:700; color:#15803d; }
.pyqsrc { font-size:8.3pt; color:#6b7280; margin-top:2px; }
.pyqdiv { border:none; border-top:1px solid #e5e7eb; margin:8px 0; }
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
    # --- build HTML ---
    tags = []
    if item.get("pyq_match_year"):
        tags.append(("&#127919; RPSC " + str(item["pyq_match_year"]), "#c2410c"))
    if item.get("topic_kb_never_skipped"):
        tags.append(("&#9889; Never skipped in 6 yrs", "#15803d"))
    if item.get("topic_kb_trajectory") == "rising":
        tags.append(("&#128200; Rising trend", "#1d4ed8"))
    if not tags:
        return ""
    spans = "".join(
        f'<span style="background:{col};color:white;padding:1px 6px;border-radius:3px;'
        f'font-size:8pt;margin-right:5px;">{t}</span>'
        for t, col in tags)
    return f'<div style="margin-top:3px;">{spans}</div>'

def find_linked_pyqs(en_items, threshold=0.60, max_n=3):
    """For each EN main item, find the single best-matching prelims PYQ in ChromaDB.
    Keep only matches with cosine similarity > threshold, max `max_n` total, one per item.
    Returns [{item_idx, score, pyq(row from questions table)}] in news order."""
    cands = []
    for i, it in enumerate(en_items or []):
        text = f"{(it.get('title') or '').replace('**', '')} {it.get('summary') or ''}".strip()
        if not text:
            continue
        m = C.pyq_lookup(text, n=1, max_distance=2.0)   # top match; we filter by score
        score = (m.get("score") or 0) if m else 0
        # DEBUG: surface why PYQs do/don't show
        C.log(f"   PYQ search: {text[:80]}")
        if m:
            C.log(f"   Top match: {score:.3f} - RPSC RAS {m.get('year')} Q{m.get('q_no')} "
                  f"(threshold {threshold})")
        else:
            C.log(f"   Top match: none (threshold {threshold})")
        if m and score > threshold:
            cands.append({"item_idx": i, "score": m["score"], "year": m["year"], "q_no": m["q_no"]})
    cands.sort(key=lambda x: x["score"], reverse=True)   # best matches first
    cands = cands[:max_n]                                  # cap at 3 (already one-per-item)
    out = []
    for c in cands:
        try:
            rows = C.sb_select("questions", params={
                "year": f"eq.{c['year']}", "q_no": f"eq.{c['q_no']}", "limit": "1"})
        except Exception as e:
            C.log(f"   ⚠ PYQ fetch failed for {c['year']} Q{c['q_no']}: {e}")
            rows = None
        if rows:
            out.append({"item_idx": c["item_idx"], "score": c["score"], "pyq": rows[0]})
    out.sort(key=lambda x: x["item_idx"])                 # render in news order
    return out

def render_pyq_section(linked_pyqs, lang, main_items, L):
    """Render the '📝 PYQs Linked to Today's News' block. Empty string if no matches."""
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
        from_html = f'<div class="pyqfrom">{L["from"]}: {esc(headline)}</div>' if headline else ""
        ans_html = f'<div class="pyqans">{L["answer"]}: ({corr_let}) {esc(corr_text)}</div>'
        src_html = f'<div class="pyqsrc">&#8212; RPSC RAS {esc(q.get("year"))}</div>'
        div = "" if k == len(linked_pyqs) - 1 else '<hr class="pyqdiv">'
        blocks.append(
            f'<div class="pyqitem">{from_html}'
            f'<div class="pyqq">{esc(qtext)}</div>{opt_html}{ans_html}{src_html}</div>{div}')
    return f'<div class="sec"><h2>&#9998; {L["pyq_head"]}</h2>{"".join(blocks)}</div>'

def render_html(date, lang, main_items, also_items, labels, linked_pyqs=None):
    ds = date.isoformat()
    pretty = date.strftime("%d %B %Y")
    L = labels
    items_html = []
    for it in main_items:
        # Bullets: **text** → <b>text</b>
        bullets = "".join(f"<li>{md_bold(b)}</li>" for b in (it.get("bullets") or []))

        # RAG tags (from item attributes set during enrichment, or live topic_kb lookup)
        tags_html = _build_tag_html(it)

        # Static connect: just the chapter name (Claude now returns short form)
        static_val = (it.get("static_connect") or "").split("—")[0].split(":")[0].strip()

        items_html.append(f"""
        <div class="item">
          <div class="cat"><span class="dot">&#8226;</span> {esc(it.get('category'))}</div>
          <div class="title">{esc(it.get('title')).replace('**','')}</div>
          <div class="summary">{esc(it.get('summary'))}</div>
          <div class="context">{esc(it.get('context'))}</div>
          <ul>{bullets}</ul>
          <div class="static">&#8226; {L['static']}: <b>{esc(static_val)}</b></div>
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
        connects_html = (f"<div class='connects'>&#8226; <b>{L['connects']}:</b> "
                         + " · ".join(esc(c) for c in connects) + "</div>")

    pyq_html = render_pyq_section(linked_pyqs or [], lang, main_items, L)

    social = " ".join(f"<a href='{u}'>{esc(t)}</a>" for t, u in SOCIAL)
    body_cls = "hi" if lang == "HI" else "en"
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head>
<body class="{body_cls}">
  <div class="watermark">paperse.in</div>
  <div class="head"><h1>PaperSe {L['daily']} — {pretty}</h1>
     <div class="sub">{L['sub']}</div></div>
  {''.join(items_html) if items_html else f"<p>{L['none']}</p>"}
  {also_html}
  {pyq_html}
  {connects_html}
  <div class="cta">&#9998; <b>{L['test']}</b> &nbsp;
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
           "pyq_head": "PYQs Linked to Today's News", "from": "From", "answer": "Answer"},
    "HI": {"daily": "डेली करेंट अफेयर्स", "sub": "RPSC RAS — परीक्षा-केंद्रित करेंट अफेयर्स",
           "static": "स्टैटिक कनेक्ट", "also": "अन्य प्रमुख समाचार",
           "connects": "आज के स्टैटिक कनेक्ट", "test": "आज के CA पर खुद को परखें",
           "test_sub": "टाइम्ड टेस्ट • 5-8 प्रश्न • प्रति प्रश्न 1 मिनट",
           "follow": "PaperSe को फॉलो करें", "none": "आज कोई आइटम नहीं।",
           "pyq_head": "आज की खबरों से जुड़े PYQ", "from": "स्रोत", "answer": "उत्तर"},
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
