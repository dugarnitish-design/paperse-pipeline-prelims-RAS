#!/usr/bin/env python3
"""
§5D — Weekly coverage report. Run Sundays 08:00 IST (Railway cron, see below).

  python3 pipelines/weekly_coverage_report.py [YYYY-MM-DD]   # defaults to today (IST)

1. Refresh topic_coverage.days_since_covered (= today − last_covered_date; 9999 if never).
2. Mark is_overdue=true for HIGH-priority topics not covered in 30+ days.
3. Send a Telegram coverage report to the curator (CURATOR_CHAT_ID) with covered-this-
   week, overdue HIGH, next-week watch-list, and a basic RPSC-2026 readiness score
   (the full readiness page is §12).

Railway cron to add (dashboard → cron service):
    schedule (UTC):  30 2 * * 0          # Sunday 02:30 UTC = 08:00 IST
    command:         python3 pipelines/weekly_coverage_report.py
"""
import sys, datetime, pathlib
import requests
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from pipelines import _common as C

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

# §12 — map a topic_coverage.subject onto the six headline RPSC buckets for the
# readiness breakdown. Rajasthan is checked first so "Rajasthan Economy/Polity/
# History" all roll up under Rajasthan (the 40%-weight subject), per the spec.
SUBJECT_BUCKETS = ["Rajasthan", "Economy", "Environment", "Polity", "History", "Science"]


def _bucket(subject):
    s = (subject or "").lower()
    if "rajasthan" in s:                       return "Rajasthan"
    if "environment" in s or "ecology" in s:   return "Environment"
    if "econom" in s:                          return "Economy"
    if "polit" in s or "constitution" in s:    return "Polity"
    if "history" in s:                         return "History"
    if "science" in s or "technolog" in s:     return "Science"
    return None                                # outside the six headline buckets


def _send_admin(text):
    token = C.ENV.get("TELEGRAM_BOT_TOKEN")
    chat = C.ENV.get("CURATOR_CHAT_ID")
    if not token or not chat:
        C.log("   ⚠ TELEGRAM_BOT_TOKEN / CURATOR_CHAT_ID missing — printing report instead.\n" + text)
        return
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": text, "disable_web_page_preview": True},
                          timeout=20)
        C.log("   ✓ report sent" if r.ok else f"   ⚠ report send failed: {r.text[:200]}")
    except Exception as e:
        C.log(f"   ⚠ report send error: {e}")


def main(today=None):
    today = C.parse_date(today) if isinstance(today, str) else (today or datetime.datetime.now(IST).date())
    C.log("=" * 64); C.log(f"WEEKLY COVERAGE REPORT — {today.isoformat()}"); C.log("=" * 64)

    cov = C.sb_select("topic_coverage", params={"limit": "2000"}) or []

    # 1+2 — refresh recency + overdue flags (update only changed rows).
    refreshed = 0
    for c in cov:
        lcd = c.get("last_covered_date")
        if lcd:
            try:
                days = (today - datetime.date.fromisoformat(lcd[:10])).days
            except Exception:
                days = 9999
        else:
            days = 9999
        overdue = (c.get("priority") == "HIGH" and days >= 30)
        if days != c.get("days_since_covered") or overdue != c.get("is_overdue"):
            try:
                C.sb_update("topic_coverage", {"days_since_covered": days, "is_overdue": overdue},
                            {"topic": c["topic"]})
                refreshed += 1
            except Exception as e:
                C.log(f"   ⚠ update {c.get('topic','')[:30]}: {e}")
        c["days_since_covered"], c["is_overdue"] = days, overdue   # local copy for the report
    C.log(f"   refreshed {refreshed} rows")

    week_ago = today - datetime.timedelta(days=7)
    covered_week = [c for c in cov if c.get("last_covered_date")
                    and c["last_covered_date"][:10] >= week_ago.isoformat()]
    overdue_high = sorted([c for c in cov if c.get("is_overdue")],
                          key=lambda c: -(c.get("days_since_covered") or 0))
    high_total = [c for c in cov if c.get("priority") == "HIGH"]
    high_covered_month = [c for c in high_total if (c.get("days_since_covered") or 9999) <= 30]
    high_covered_year = [c for c in high_total if (c.get("days_since_covered") or 9999) <= 365]
    n_high = max(1, len(high_total))
    monthly = len(high_covered_month) / n_high
    yearly = len(high_covered_year) / n_high
    readiness = round((monthly * 0.4 + yearly * 0.6) * 100)

    def _lines(items, fmt, n=10):
        return "\n".join(fmt(c) for c in items[:n]) or "  —"

    # §12 — per-subject readiness breakdown (HIGH topics covered this year ÷ total HIGH).
    subj_lines = []
    for b in SUBJECT_BUCKETS:
        in_b = [c for c in high_total if _bucket(c.get("subject")) == b]
        if not in_b:
            continue
        cov_b = [c for c in in_b if (c.get("days_since_covered") or 9999) <= 365]
        pct = round(100 * len(cov_b) / len(in_b))
        subj_lines.append(f"  {b}: {pct}% ({len(cov_b)}/{len(in_b)})")
    subj_block = "\n".join(subj_lines) or "  —"

    msg = (
        f"📊 PAPERSE WEEKLY COVERAGE REPORT\nWeek of {today.isoformat()}\n\n"
        f"✅ COVERED THIS WEEK ({len(covered_week)}):\n"
        + _lines(covered_week, lambda c: f"  • {c['topic'][:48]} ({c.get('last_covered_date','')[:10]})") + "\n\n"
        f"⚠️ OVERDUE HIGH PRIORITY ({len(overdue_high)}):\n"
        + _lines(overdue_high, lambda c: f"  • {c['topic'][:48]} — {c.get('days_since_covered','?')}d") + "\n\n"
        f"🎯 WATCH NEXT WEEK:\n"
        + _lines(overdue_high, lambda c: f"  • {c['topic'][:48]}", n=5) + "\n\n"
        f"📈 RPSC 2026 READINESS: {readiness}%\n"
        f"  HIGH covered this month: {len(high_covered_month)}/{len(high_total)}\n"
        f"  HIGH covered this year:  {len(high_covered_year)}/{len(high_total)}\n\n"
        f"📚 SUBJECT-WISE READINESS (HIGH, this year):\n"
        + subj_block
    )
    _send_admin(msg)
    C.log("\n" + msg)
    return readiness


if __name__ == "__main__":
    arg = next((a for a in sys.argv[1:] if not a.startswith("-")), None)
    main(arg)
