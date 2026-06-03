#!/usr/bin/env python3
"""PaperSe — English PDF Generator"""

import re
import sys
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.colors import HexColor, white, Color
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY

# ── Colors ────────────────────────────────────────────────────────────────────
NAVY   = HexColor('#1B2A4A')
BLUE   = HexColor('#2563EB')
ACCENT = HexColor('#DC2626')
GOLD   = HexColor('#D97706')
GREEN  = HexColor('#16A34A')
BORDER = HexColor('#CBD5E1')
TEXT   = HexColor('#1E293B')
MUTED  = HexColor('#64748B')
CREAM  = HexColor('#FFFBEB')

W = 180 * mm  # usable page width

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
    text = re.sub(r'`{1,3}.*?`{1,3}', '', text)
    text = re.sub(r'^#+\s*', '', text, flags=re.M)
    text = re.sub(r'^\s*>\s*', '', text, flags=re.M)
    # XML-escape first so < > & in content are safe
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    # Then convert **bold** → <b>bold</b> (after escaping, so tags are safe)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    # Strip remaining single asterisks
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    return text.strip()

# ── Parser ────────────────────────────────────────────────────────────────────
def extract_english(raw):
    m = re.search(r'OUTPUT_1 — ENGLISH:(.*?)(?:OUTPUT_2 — HINDI:|OUTPUT_3|$)',
                  raw, re.DOTALL)
    return m.group(1).strip() if m else raw

def _grab(pattern, text):
    m = re.search(pattern, text, re.DOTALL)
    return m.group(1).strip() if m else ''

def parse(english_text):
    # Date
    dm = re.search(r'📅\s*(.+)', english_text)
    date = dm.group(1).strip() if dm else ''

    # Today at a glance
    gm = re.search(r'TODAY AT A GLANCE\n(.*?)(?=━{5})', english_text, re.DOTALL)
    glance = []
    if gm:
        for line in gm.group(1).splitlines():
            line = re.sub(r'^\s*[-•*]\s*', '', line).strip()
            line = re.sub(r'\*\*(.+?)\*\*', r'\1', line)
            if line:
                glance.append(line)

    # Split on ━━━ dividers — alternating subject/body blocks
    blocks = [b.strip() for b in re.split(r'━{10,}', english_text) if b.strip()]

    news_items = []
    i = 0
    while i < len(blocks):
        block = blocks[i]

        if re.match(r'^🏛', block):
            subject_line = block.splitlines()[0]
            subject = re.sub(r'^🏛\s*', '', subject_line).strip()
            body = blocks[i + 1] if i + 1 < len(blocks) else ''

            brief     = (_grab(r'📰 WHY IN NEWS\n(.*?)(?=📌|💡|📎|\Z)', body) or
                         _grab(r'📰 NEWS IN BRIEF\n(.*?)(?=🎯|📌|💡|📎|\Z)', body))
            facts_raw = (_grab(r'📌 KEY FACTS FOR RPSC\n(.*?)(?=💡|📎|\Z)', body) or
                         _grab(r'📌 KEY FACTS TO REMEMBER\n(.*?)(?=💡|📎|\Z)', body))
            facts     = [f.lstrip('-').strip() for f in facts_raw.splitlines()
                         if f.strip().lstrip('-').strip()]
            hook      = (_grab(r'💡 MEMORY HOOK\n(.*?)(?=📎|\Z)', body) or
                         _grab(r'💡 Remember:\s*(.*)', body))
            pyq       = _grab(r'📎 PYQ VAULT\n(.*?)(?=━|\Z)', body)

            news_items.append(dict(subject=subject, brief=brief,
                                   facts=facts, hook=hook, pyq=pyq))
            i += 2
        elif 'PRACTICE' in block.upper() or 'paperse.in' in block:
            break
        else:
            i += 1

    return dict(date=date, glance=glance, news_items=news_items)

# ── Styles ────────────────────────────────────────────────────────────────────
def make_styles():
    def S(n, **kw):
        return ParagraphStyle(n, **kw)
    return {
        'glance_hdr':  S('gh', fontName='Helvetica-Bold', fontSize=8, textColor=GOLD, spaceAfter=2),
        'glance_item': S('gi', fontName='Helvetica', fontSize=8, textColor=TEXT, leading=12, leftIndent=4, spaceAfter=1),
        'subj':        S('su', fontName='Helvetica-Bold', fontSize=7.5, textColor=white, leading=11),
        'num':         S('nu', fontName='Helvetica-Bold', fontSize=10, textColor=white, alignment=TA_CENTER),
        'lbl_blue':    S('lb', fontName='Helvetica-Bold', fontSize=6.5, textColor=BLUE, spaceBefore=4, spaceAfter=1),
        'lbl_red':     S('lr', fontName='Helvetica-Bold', fontSize=6.5, textColor=ACCENT, spaceBefore=3, spaceAfter=1),
        'lbl_navy':    S('ln', fontName='Helvetica-Bold', fontSize=6.5, textColor=NAVY, spaceBefore=3, spaceAfter=1),
        'body':        S('bo', fontName='Helvetica', fontSize=8, textColor=TEXT, leading=12, alignment=TA_JUSTIFY, spaceAfter=1),
        'fact':        S('fa', fontName='Helvetica', fontSize=7.5, textColor=TEXT, leading=11, leftIndent=6, spaceAfter=1),
        'hook':        S('ho', fontName='Helvetica-Oblique', fontSize=7.5, textColor=HexColor('#1D4ED8'), leading=11, spaceBefore=2, spaceAfter=2),
        'pyq_line':    S('pl', fontName='Helvetica', fontSize=7.5, textColor=HexColor('#374151'), leading=11, leftIndent=4, spaceAfter=1),
        'pyq_link':    S('pk', fontName='Helvetica-Oblique', fontSize=7, textColor=MUTED, leading=10, leftIndent=4),
    }

# ── Header/Footer ─────────────────────────────────────────────────────────────
def make_hf(date):
    def hf(c, doc):
        c.saveState()
        pw, ph = A4

        # Header band — compact 35mm
        c.setFillColor(NAVY)
        c.rect(0, ph - 35*mm, pw, 35*mm, fill=1, stroke=0)
        c.setFillColor(BLUE)
        c.rect(0, ph - 36.5*mm, pw, 1.5*mm, fill=1, stroke=0)

        c.setFillColor(white)
        c.setFont('Helvetica-Bold', 18)
        c.drawCentredString(pw/2, ph - 12*mm, 'PaperSe')

        c.setFont('Helvetica', 8)
        c.setFillColor(HexColor('#93C5FD'))
        c.drawCentredString(pw/2, ph - 19*mm, 'Daily Current Affairs  \u00b7  RPSC RAS Prelims Paper 1')

        c.setFont('Helvetica-Bold', 9)
        c.setFillColor(HexColor('#FCD34D'))
        c.drawCentredString(pw/2, ph - 26*mm, date)

        c.setFont('Helvetica', 7)
        c.setFillColor(HexColor('#93C5FD'))
        c.drawCentredString(pw/2, ph - 32.5*mm, 'paperse.in')

        # Watermark
        c.saveState()
        c.setFillColor(Color(0.18, 0.32, 0.67, alpha=0.06))
        c.setFont('Helvetica-Bold', 58)
        c.translate(pw/2, ph/2)
        c.rotate(40)
        c.drawCentredString(0, 0, 'paperse.in')
        c.restoreState()

        # Footer
        c.setFillColor(NAVY)
        c.rect(0, 0, pw, 9*mm, fill=1, stroke=0)
        c.setFillColor(white)
        c.setFont('Helvetica', 7)
        c.drawString(15*mm, 3*mm, 'paperse.in')
        c.drawCentredString(pw/2, 3*mm, date)
        c.drawRightString(pw - 15*mm, 3*mm, f'Page {doc.page}')

        c.restoreState()
    return hf

# ── Glance box ────────────────────────────────────────────────────────────────
def glance_box(bullets, st):
    rows = [Paragraph('TODAY AT A GLANCE', st['glance_hdr'])]
    for b in bullets:
        rows.append(Paragraph(f'&#8226;  {clean(b)}', st['glance_item']))
    tbl = Table([[rows]], colWidths=[W])
    tbl.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (-1,-1), CREAM),
        ('BOX',           (0,0), (-1,-1), 1, GOLD),
        ('LEFTPADDING',   (0,0), (-1,-1), 10),
        ('RIGHTPADDING',  (0,0), (-1,-1), 10),
        ('TOPPADDING',    (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    return tbl

# ── News card — returns a LIST of flowables so pages fill completely ───────────
def news_card(item, num, st):
    elements = []

    # Subject header bar
    parts = item['subject'].split('›')
    if len(parts) >= 3:
        subj_str = (f'<b>{clean(parts[0].strip())}</b>  \u203a  '
                    f'{clean(parts[1].strip())}  \u203a  {clean(parts[2].strip())}')
    else:
        subj_str = f'<b>{clean(item["subject"])}</b>'

    hdr = Table(
        [[Paragraph(f'<b>#{num}</b>', st['num']),
          Paragraph(subj_str, st['subj'])]],
        colWidths=[9*mm, 169*mm],
    )
    hdr.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (-1,-1), NAVY),
        ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING',    (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING',   (0,0), (0,0), 5),
        ('LEFTPADDING',   (1,0), (1,0), 8),
        ('RIGHTPADDING',  (1,0), (1,0), 8),
    ]))

    # Keep header + WHY IN NEWS together so header is never orphaned at page bottom
    anchor = [hdr]
    if item['brief']:
        anchor.append(Paragraph('WHY IN NEWS', st['lbl_blue']))
        anchor.append(Paragraph(clean(item['brief']), st['body']))
    elements.append(KeepTogether(anchor))

    # KEY FACTS — flow freely to fill page
    if item['facts']:
        elements.append(Paragraph('KEY FACTS FOR RPSC', st['lbl_red']))
        for f in item['facts']:
            if f:
                elements.append(Paragraph(f'&#8226;  {clean(f)}', st['fact']))

    # MEMORY HOOK
    if item['hook']:
        elements.append(Paragraph(f'<b>Memory Hook:</b>  {clean(item["hook"])}', st['hook']))

    # PYQ VAULT
    if item['pyq']:
        pyq_lines = [l.strip() for l in item['pyq'].split('\n') if l.strip()]
        if pyq_lines:
            elements.append(Paragraph('PYQ VAULT', st['lbl_navy']))
            for line in pyq_lines:
                style = st['pyq_link'] if line.startswith('\u21b3') else st['pyq_line']
                elements.append(Paragraph(clean(line), style))

    # Thin separator between cards
    elements.append(HRFlowable(width='100%', thickness=0.5, color=BORDER,
                                spaceBefore=3, spaceAfter=3))
    return elements

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else '2026-05-27'
    input_file  = f'./outputs/{date_str}/raw-output.txt'
    output_file = f'./outputs/{date_str}/english.pdf'

    if not os.path.exists(input_file):
        print(f'Error: {input_file} not found'); sys.exit(1)

    with open(input_file, encoding='utf-8') as f:
        raw = f.read()

    data = parse(extract_english(raw))
    st   = make_styles()

    print(f"Date       : {data['date']}")
    print(f"Glance     : {len(data['glance'])} bullets")
    print(f"News items : {len(data['news_items'])}")
    for item in data['news_items']:
        print(f"  - {item['subject'][:60]}  facts={len(item['facts'])}")

    # topMargin=39mm matches the 36.5mm header + small gap
    doc = SimpleDocTemplate(
        output_file, pagesize=A4,
        topMargin=39*mm, bottomMargin=13*mm,
        leftMargin=15*mm, rightMargin=15*mm,
    )

    story = []

    if data['glance']:
        story.append(glance_box(data['glance'], st))
        story.append(Spacer(1, 3*mm))

    for i, item in enumerate(data['news_items']):
        for el in news_card(item, i + 1, st):
            story.append(el)

    hf = make_hf(data['date'] or date_str)
    doc.build(story, onFirstPage=hf, onLaterPages=hf)
    print(f'\n✓ PDF saved → {output_file}')

if __name__ == '__main__':
    main()
