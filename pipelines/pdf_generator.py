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
.cat { font-size: 8.2pt; font-weight: 700; letter-spacing: .04em; text-transform: uppercase;
       color: #f4622a; }
.cat .dot { color: #f4622a; font-size: 7pt; vertical-align: middle; }
.title { font-size: 12pt; font-weight: 800; color: #11203a; margin: 2px 0 3px; }
.summary { font-weight: 600; color: #111827; }
.context { color: #374151; margin: 3px 0 5px; }
ul { margin: 4px 0 5px 0; padding-left: 17px; }
li { margin-bottom: 2px; }
.static { font-size: 9pt; color: #2563eb; }
.static b { color: #1e40af; }
.also h2, .sec h2 { font-size: 10.5pt; color: #11203a; border-left: 4px solid #f4622a;
                    padding-left: 7px; margin: 12px 0 6px; text-transform: uppercase; letter-spacing:.03em;}
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
"""

def esc(s):
    return html.escape(str(s or ""))

def render_html(date, lang, main_items, also_items, labels):
    ds = date.isoformat()
    pretty = date.strftime("%d %B %Y")
    L = labels
    items_html = []
    for it in main_items:
        bullets = "".join(f"<li>{esc(b)}</li>" for b in (it.get("bullets") or []))

        # Get RAG tags
        tags = rag.get_pdf_tags(it)
        tags_html = ""
        if tags:
            tag_items = "".join(
                f'<span class="tag" style="background:{c}; color:white; padding:2px 6px; '
                f'border-radius:3px; font-size:11px; margin-right:6px;">{esc(t)}</span>'
                for t, c in [(tag['text'], tag['color']) for tag in tags]
            )
            tags_html = f'<div class="tags" style="margin-top:4px;">{tag_items}</div>'

        items_html.append(f"""
        <div class="item">
          <div class="cat"><span class="dot">&#9679;</span> {esc(it.get('category'))}</div>
          <div class="title">{esc(it.get('title')).replace('**','')}</div>
          <div class="summary">{esc(it.get('summary'))}</div>
          <div class="context">{esc(it.get('context'))}</div>
          <ul>{bullets}</ul>
          <div class="static">&#9656; {L['static']}: <b>{esc(it.get('static_connect'))}</b></div>
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
        connects_html = (f"<div class='connects'>&#9656; <b>{L['connects']}:</b> "
                         + " · ".join(esc(c) for c in connects) + "</div>")

    social = " ".join(f"<a href='{u}'>{esc(t)}</a>" for t, u in SOCIAL)
    body_cls = "hi" if lang == "HI" else "en"
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head>
<body class="{body_cls}">
  <div class="watermark">paperse.in</div>
  <div class="head"><h1>PaperSe {L['daily']} — {pretty}</h1>
     <div class="sub">{L['sub']}</div></div>
  {''.join(items_html) if items_html else f"<p>{L['none']}</p>"}
  {also_html}
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
           "follow": "Follow PaperSe", "none": "No items today."},
    "HI": {"daily": "डेली करेंट अफेयर्स", "sub": "RPSC RAS — परीक्षा-केंद्रित करेंट अफेयर्स",
           "static": "स्टैटिक कनेक्ट", "also": "अन्य प्रमुख समाचार",
           "connects": "आज के स्टैटिक कनेक्ट", "test": "आज के CA पर खुद को परखें",
           "test_sub": "टाइम्ड टेस्ट • 5-8 प्रश्न • प्रति प्रश्न 1 मिनट",
           "follow": "PaperSe को फॉलो करें", "none": "आज कोई आइटम नहीं।"},
}

def build_pdf(date, lang):
    ds = date.isoformat()
    main_items = C.sb_select("daily_ca_items", params={
        "date": f"eq.{ds}", "is_main": "eq.true", "language": f"eq.{lang}", "order": "priority.desc"})
    also_items = C.sb_select("daily_ca_items", params={
        "date": f"eq.{ds}", "is_main": "eq.false", "language": f"eq.{lang}", "order": "priority.desc"})
    if not main_items:
        C.log(f"   ⚠ no {lang} main items for {ds}; skipping {lang} PDF")
        return None
    html_str = render_html(date, lang, main_items, also_items, LABELS[lang])
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
    en = build_pdf(date, "EN")
    hi = build_pdf(date, "HI")
    if en or hi:
        C.log(f"\n✓ STEP 4 complete — EN={'ok' if en else 'skip'} · HI={'ok' if hi else 'skip'}")
        return [p for p in (en, hi) if p]
    C.log("\n✗ STEP 4 produced no PDFs (no items).")
    return None

if __name__ == "__main__":
    d = C.parse_date(sys.argv[1]) if len(sys.argv) > 1 else datetime.date.today()
    sys.exit(0 if main(d) else 1)
