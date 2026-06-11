#!/usr/bin/env python3
"""
§11 — PYQ trend analysis & RPSC-2026 prediction. Run monthly (Railway cron, see below).

  python3 pipelines/analyse_pyq_trends.py [YYYY-MM-DD]   # defaults to today (IST)

Reads the full `questions` PYQ set + topic_intelligence + topic_coverage and produces:
  ANALYSIS 1 — topic frequency by year (2015-2025) → refresh topic_intelligence.frequency_trend
  ANALYSIS 2 — subject weight shifts (each subject's % share of the paper, by year)
  ANALYSIS 3 — predict 2026 hot topics: RISING + overdue (not asked recently) + currently
               covered often by PaperSe (a news-frequency proxy)
  ANALYSIS 4 — gap analysis: syllabus topics with ZERO PYQs (wild cards to monitor)

Output: frequency_trend is rewritten for every topic (the §6A scorer already boosts
HIGH + RISING via _intel_modifier, so keeping the trend current re-weights scoring). A
Telegram prediction report is sent to the curator (CURATOR_CHAT_ID).

Railway cron to add (dashboard → cron service):
    schedule (UTC):  30 2 1 * *          # 1st of month, 02:30 UTC = 08:00 IST
    command:         python3 pipelines/analyse_pyq_trends.py
"""
import sys, datetime, pathlib
from collections import defaultdict, Counter
import requests
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from pipelines import _common as C

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
RECENT = range(2021, 2025)   # 2021-2024 (mirror populate_topic_intelligence)
OLD    = range(2015, 2021)   # 2015-2020
RAJ_HINT = "rajasthan"


def _trend(years):
    recent = sum(1 for y in years if y in RECENT)
    old = sum(1 for y in years if y in OLD)
    return "RISING" if recent > old else "DECLINING" if recent < old else "STABLE"


def _send_admin(text):
    token = C.ENV.get("TELEGRAM_BOT_TOKEN"); chat = C.ENV.get("CURATOR_CHAT_ID")
    if not token or not chat:
        C.log("   ⚠ TELEGRAM_BOT_TOKEN / CURATOR_CHAT_ID missing — printing report instead.\n" + text)
        return
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": text, "disable_web_page_preview": True}, timeout=20)
        C.log("   ✓ report sent" if r.ok else f"   ⚠ report send failed: {r.text[:200]}")
    except Exception as e:
        C.log(f"   ⚠ report send error: {e}")


def main(today=None):
    today = C.parse_date(today) if isinstance(today, str) else (today or datetime.datetime.now(IST).date())
    year_now = today.year
    C.log("=" * 64); C.log(f"PYQ TREND ANALYSIS — {today.isoformat()}"); C.log("=" * 64)

    pyqs = C.sb_select("questions", select="year,subject,topic", params={"limit": "5000"}) or []
    C.log(f"   {len(pyqs)} PYQs loaded")
    ti_rows = C.sb_select("topic_intelligence", params={"limit": "3000"}) or []
    cov_rows = {c["topic"]: c for c in (C.sb_select("topic_coverage", params={"limit": "3000"}) or [])}
    ti_by_topic = {r["topic"]: r for r in ti_rows}

    # ── ANALYSIS 1 — topic frequency by year → refresh frequency_trend ─────────
    years_by_topic = defaultdict(list)
    for q in pyqs:
        t, y = q.get("topic"), q.get("year")
        if t and y:
            years_by_topic[t].append(y)
    updated = 0
    for topic, years in years_by_topic.items():
        if topic not in ti_by_topic:
            continue
        new_trend = _trend(years)
        if ti_by_topic[topic].get("frequency_trend") != new_trend:
            try:
                C.sb_update("topic_intelligence", {"frequency_trend": new_trend}, {"topic": topic})
                ti_by_topic[topic]["frequency_trend"] = new_trend
                updated += 1
            except Exception as e:
                C.log(f"   ⚠ trend update {topic[:30]}: {e}")
    C.log(f"   ANALYSIS 1: refreshed frequency_trend on {updated} topic(s)")

    # ── ANALYSIS 2 — subject weight shifts (% share by year) ───────────────────
    per_year = defaultdict(Counter)
    for q in pyqs:
        s, y = (q.get("subject") or "Unknown"), q.get("year")
        if y:
            per_year[y][s] += 1
    raj_share = {}                      # Rajasthan-subject % share trend
    for y in sorted(per_year):
        tot = sum(per_year[y].values()) or 1
        raj = sum(n for s, n in per_year[y].items() if RAJ_HINT in s.lower())
        raj_share[y] = round(100 * raj / tot)
    recent_yrs = sorted(raj_share)[-3:]
    raj_trend_txt = " · ".join(f"{y}:{raj_share[y]}%" for y in recent_yrs) if recent_yrs else "—"
    C.log(f"   ANALYSIS 2: Rajasthan share by year — {raj_trend_txt}")

    # ── ANALYSIS 3 — predict 2026 hot topics ──────────────────────────────────
    def _hot_score(r):
        freq = r.get("rpsc_frequency")
        s = 2.0 if freq == "HIGH" else 1.0 if freq == "MEDIUM" else 0.0
        if r.get("frequency_trend") == "RISING":
            s += 2.0
        last = r.get("last_asked_year")
        yrs_since = (year_now - last) if last else 6        # never-asked treated as long overdue
        if yrs_since >= 1:                                  # overdue for a return
            s += min(yrs_since, 5) * 0.6
        cov = cov_rows.get(r["topic"]) or {}
        s += min(cov.get("coverage_count") or 0, 6) * 0.3   # PaperSe news-frequency proxy
        return s, yrs_since

    scored = []
    for r in ti_rows:
        if (r.get("pyq_count") or 0) < 1:                   # real PYQ topics only (wild cards handled in A4)
            continue
        sc, yrs = _hot_score(r)
        # must be either RISING or genuinely overdue to qualify as "hot"
        if r.get("frequency_trend") == "RISING" or yrs >= 2:
            scored.append((sc, yrs, r))
    scored.sort(key=lambda x: -x[0])
    top10 = scored[:10]
    C.log(f"   ANALYSIS 3: {len(scored)} candidate hot topics, top 10 selected")

    # ── ANALYSIS 4 — gap analysis (syllabus topics with ZERO PYQs) ─────────────
    wild = [r for r in ti_rows if (r.get("pyq_count") or 0) == 0]
    wild.sort(key=lambda r: 0 if r.get("rpsc_frequency") == "HIGH" else 1)
    C.log(f"   ANALYSIS 4: {len(wild)} wild-card (zero-PYQ) syllabus topics")

    # ── COVERAGE RECOMMENDATION — overdue HIGH topics to chase next month ──────
    def _days_since(c):
        lcd = c.get("last_covered_date")
        if not lcd:
            return 9999
        try: return (today - datetime.date.fromisoformat(lcd[:10])).days
        except Exception: return 9999
    high_overdue = [r for r in ti_rows if r.get("rpsc_frequency") == "HIGH"]
    high_overdue.sort(key=lambda r: -_days_since(cov_rows.get(r["topic"]) or {}))
    rec5 = high_overdue[:5]

    # ── Telegram prediction report ─────────────────────────────────────────────
    def _hot_line(i, item):
        sc, yrs, r = item
        rising = r.get("frequency_trend") == "RISING"
        last = r.get("last_asked_year")
        why = []
        if rising: why.append("rising trend")
        if last: why.append(f"last asked {last}")
        else: why.append("never asked (overdue)")
        if yrs >= 2 and last: why.append(f"{yrs}y overdue")
        return f"{i}. {r['topic'][:46]} — {', '.join(why)}"

    month = today.strftime("%B %Y")
    msg = (
        f"🔮 RPSC 2026 PREDICTION REPORT — {month}\n"
        f"(from {len(pyqs)} PYQs · {len(ti_rows)} topics)\n\n"
        f"📊 RAJASTHAN SHARE OF PAPER: {raj_trend_txt}\n\n"
        f"🎯 TOP {len(top10)} HIGHEST-PROBABILITY TOPICS:\n"
        + ("\n".join(_hot_line(i + 1, it) for i, it in enumerate(top10)) or "  —") + "\n\n"
        f"🃏 WILD CARDS (syllabus topics, zero PYQs):\n"
        + ("\n".join(f"  • {r['topic'][:46]}" for r in wild[:8]) or "  —") + "\n\n"
        f"📌 COVERAGE RECOMMENDATION — prioritise news on:\n"
        + ("\n".join(f"  {i+1}. {r['topic'][:46]} ({_days_since(cov_rows.get(r['topic']) or {})}d since covered)"
                     for i, r in enumerate(rec5)) or "  —")
    )
    _send_admin(msg)
    C.log("\n" + msg)
    return {"trend_updated": updated, "hot": len(top10), "wild": len(wild)}


if __name__ == "__main__":
    arg = next((a for a in sys.argv[1:] if not a.startswith("-")), None)
    main(arg)
