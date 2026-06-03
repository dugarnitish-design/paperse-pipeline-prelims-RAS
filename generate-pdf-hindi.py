#!/usr/bin/env python3
"""PaperSe — Hindi PDF Generator (WeasyPrint for proper Devanagari rendering)"""

import re
import sys
import os
from weasyprint import HTML, CSS

# ── Text cleaner ──────────────────────────────────────────────────────────────
EMOJI_RE = re.compile(
    "["
    u"\U0001F300-\U0001F9FF"
    u"\U0001FA00-\U0001FAFF"
    u"\U00002702-\U000027B0"
    u"\U000024C2-\U0001F251"
    u"\u2600-\u26FF\u2700-\u27BF\u25A0-\u25FF"
    "]+",
    flags=re.UNICODE,
)

def clean(text):
    text = EMOJI_RE.sub('', str(text))
    text = re.sub(r'[━─═╔╗╚╝║│┌┐└┘├┤┬┴┼▶◀]', '', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'`{1,3}.*?`{1,3}', '', text)
    text = re.sub(r'^#+\s*', '', text, flags=re.M)
    return text.strip()

def esc(text):
    return clean(text).replace('&', '&amp;').replace('<b>', '\x00B').replace('</b>', '\x00E').replace('<', '&lt;').replace('>', '&gt;').replace('\x00B', '<b>').replace('\x00E', '</b>')

# ── Parser ────────────────────────────────────────────────────────────────────
def extract_hindi(raw):
    m = re.search(r'OUTPUT_2 — HINDI:(.*?)(?:OUTPUT_3|$)', raw, re.DOTALL)
    return m.group(1).strip() if m else raw

def _grab(pattern, text):
    m = re.search(pattern, text, re.DOTALL)
    return m.group(1).strip() if m else ''

def parse(hindi_text):
    dm = re.search(r'📅\s*(.+)', hindi_text)
    date = dm.group(1).strip() if dm else ''

    gm = re.search(r'आज की प्रमुख बातें\n(.*?)(?=━{5})', hindi_text, re.DOTALL)
    glance = []
    if gm:
        for line in gm.group(1).splitlines():
            line = re.sub(r'^\s*[-•*]\s*', '', line).strip()
            if line:
                glance.append(line)

    blocks = [b.strip() for b in re.split(r'━{10,}', hindi_text) if b.strip()]

    news_items = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        if re.match(r'^🏛', block):
            subject_line = block.splitlines()[0]
            subject = re.sub(r'^🏛\s*', '', subject_line).strip()
            body = blocks[i + 1] if i + 1 < len(blocks) else ''

            brief     = (_grab(r'📰 समाचार में क्यों\n(.*?)(?=📌|💡|📎|\Z)', body) or
                         _grab(r'📰 [^\n]+\n(.*?)(?=📌|💡|📎|\Z)', body))
            facts_raw = (_grab(r'📌 RPSC के लिए प्रमुख तथ्य\n(.*?)(?=💡|📎|\Z)', body) or
                         _grab(r'📌 [^\n]+\n(.*?)(?=💡|📎|\Z)', body))
            facts     = [f.lstrip('-').strip() for f in facts_raw.splitlines()
                         if f.strip().lstrip('-').strip()]
            hook      = (_grab(r'💡 स्मरण सूत्र\n(.*?)(?=📎|\Z)', body) or
                         _grab(r'💡 [^\n]+\n(.*?)(?=📎|\Z)', body))
            pyq       = _grab(r'📎 PYQ VAULT\n(.*?)(?=━|\Z)', body)

            news_items.append(dict(subject=subject, brief=brief,
                                   facts=facts, hook=hook, pyq=pyq))
            i += 2
        elif 'paperse.in' in block:
            break
        else:
            i += 1

    return dict(date=date, glance=glance, news_items=news_items)

# ── HTML builder ──────────────────────────────────────────────────────────────
def build_html(data):
    date = esc(data['date'])

    glance_items = ''
    for b in data['glance']:
        glance_items += f'<li>{esc(b)}</li>\n'

    cards = ''
    for idx, item in enumerate(data['news_items'], 1):
        parts = [p.strip() for p in item['subject'].split('›')]
        if len(parts) >= 3:
            subj_html = f'<b>{esc(parts[0])}</b> › {esc(parts[1])} › {esc(parts[2])}'
        else:
            subj_html = f'<b>{esc(item["subject"])}</b>'

        facts_html = ''
        for f in item['facts']:
            if f:
                facts_html += f'<li>{esc(f)}</li>\n'

        pyq_html = ''
        if item['pyq']:
            lines = [l.strip() for l in item['pyq'].split('\n') if l.strip()]
            if lines:
                pyq_html = '<div class="label navy">PYQ VAULT</div><div class="pyq">'
                for line in lines:
                    cls = 'pyq-link' if line.startswith('↳') else 'pyq-line'
                    pyq_html += f'<p class="{cls}">{esc(line)}</p>'
                pyq_html += '</div>'

        hook_html = f'<div class="hook"><b>स्मरण सूत्र:</b> {esc(item["hook"])}</div>' if item['hook'] else ''
        brief_html = f'<div class="label blue">समाचार में क्यों</div><p class="body">{esc(item["brief"])}</p>' if item['brief'] else ''
        facts_block = f'<div class="label red">RPSC के लिए प्रमुख तथ्य</div><ul class="facts">{facts_html}</ul>' if facts_html else ''

        cards += f'''
        <div class="card">
          <div class="card-header">
            <div class="card-num">#{idx}</div>
            <div class="card-subj">{subj_html}</div>
          </div>
          {brief_html}
          {facts_block}
          {hook_html}
          {pyq_html}
          <hr class="divider">
        </div>'''

    return f'''<!DOCTYPE html>
<html lang="hi">
<head>
<meta charset="UTF-8">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+Devanagari:wght@400;600;700&display=swap');

  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    font-family: 'Noto Sans Devanagari', sans-serif;
    font-size: 8pt;
    color: #1E293B;
    line-height: 1.5;
  }}

  @page {{
    size: A4;
    margin: 39mm 15mm 18mm 15mm;
    @top-center {{
      content: element(header);
    }}
    @bottom-center {{
      content: element(footer);
    }}
  }}

  #page-header {{
    position: running(header);
    background: #1B2A4A;
    color: white;
    text-align: center;
    padding: 8px 0 6px;
    width: 100%;
  }}
  #page-header .logo {{ font-size: 18pt; font-weight: 700; font-family: sans-serif; }}
  #page-header .sub {{ font-size: 7pt; color: #93C5FD; font-family: sans-serif; margin-top: 2px; }}
  #page-header .date {{ font-size: 9pt; font-weight: 700; color: #FCD34D; font-family: sans-serif; margin-top: 3px; }}
  #page-header .site {{ font-size: 7pt; color: #93C5FD; font-family: sans-serif; margin-top: 2px; }}
  #page-header .blue-bar {{ height: 3px; background: #2563EB; margin-top: 4px; }}

  #page-footer {{
    position: running(footer);
    background: #1B2A4A;
    color: white;
    display: flex;
    justify-content: space-between;
    padding: 4px 10px;
    font-size: 7pt;
    font-family: sans-serif;
    width: 100%;
  }}

  .glance-box {{
    background: #FFFBEB;
    border: 1px solid #D97706;
    padding: 8px 10px;
    margin-bottom: 10px;
  }}
  .glance-box .glance-title {{
    font-size: 8pt;
    font-weight: 700;
    color: #D97706;
    margin-bottom: 4px;
  }}
  .glance-box ul {{
    list-style: disc;
    padding-left: 14px;
    color: #1E293B;
    font-size: 8pt;
  }}
  .glance-box li {{ margin-bottom: 2px; }}

  .card {{ margin-bottom: 4px; }}

  .card-header {{
    background: #1B2A4A;
    color: white;
    display: flex;
    align-items: center;
    padding: 5px 8px;
    gap: 8px;
    page-break-inside: avoid;
  }}
  .card-num {{
    font-family: sans-serif;
    font-size: 10pt;
    font-weight: 700;
    min-width: 22px;
    text-align: center;
  }}
  .card-subj {{ font-size: 7.5pt; line-height: 1.3; }}

  .label {{
    font-family: sans-serif;
    font-size: 6.5pt;
    font-weight: 700;
    margin-top: 5px;
    margin-bottom: 2px;
  }}
  .blue {{ color: #2563EB; }}
  .red  {{ color: #DC2626; }}
  .navy {{ color: #1B2A4A; }}

  .body {{ font-size: 8pt; text-align: justify; margin-bottom: 2px; }}

  .facts {{
    list-style: disc;
    padding-left: 14px;
    font-size: 7.5pt;
  }}
  .facts li {{ margin-bottom: 2px; }}

  .hook {{
    font-size: 7.5pt;
    color: #1D4ED8;
    margin-top: 4px;
    margin-bottom: 4px;
  }}

  .pyq {{ font-size: 7.5pt; color: #374151; }}
  .pyq-line {{ margin-bottom: 2px; }}
  .pyq-link {{ color: #64748B; font-size: 7pt; margin-bottom: 2px; }}

  .divider {{
    border: none;
    border-top: 0.5px solid #CBD5E1;
    margin: 4px 0;
  }}

  .watermark {{
    position: fixed;
    top: 45%;
    left: 50%;
    transform: translate(-50%, -50%) rotate(40deg);
    font-size: 52pt;
    font-family: sans-serif;
    font-weight: bold;
    color: rgba(46, 82, 171, 0.06);
    z-index: -1;
    white-space: nowrap;
    pointer-events: none;
  }}
</style>
</head>
<body>

<div id="page-header">
  <div class="logo">PaperSe</div>
  <div class="sub">दैनिक समसामयिकी &nbsp;·&nbsp; RPSC RAS प्रारंभिक परीक्षा</div>
  <div class="date">{date}</div>
  <div class="site">paperse.in</div>
  <div class="blue-bar"></div>
</div>

<div id="page-footer">
  <span>paperse.in</span>
  <span>{date}</span>
</div>

<div class="watermark">paperse.in</div>

<div class="glance-box">
  <div class="glance-title">आज की प्रमुख बातें</div>
  <ul>{glance_items}</ul>
</div>

{cards}

</body>
</html>'''

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    date_str    = sys.argv[1] if len(sys.argv) > 1 else '2026-05-27'
    input_file  = f'./outputs/{date_str}/raw-output.txt'
    output_file = f'./outputs/{date_str}/hindi.pdf'

    if not os.path.exists(input_file):
        print(f'Error: {input_file} not found'); sys.exit(1)

    with open(input_file, encoding='utf-8') as f:
        raw = f.read()

    data = parse(extract_hindi(raw))

    print(f"Date       : {data['date']}")
    print(f"Glance     : {len(data['glance'])} bullets")
    print(f"News items : {len(data['news_items'])}")

    html = build_html(data)
    HTML(string=html).write_pdf(output_file)
    print(f'\n✓ Hindi PDF saved → {output_file}')

if __name__ == '__main__':
    main()
