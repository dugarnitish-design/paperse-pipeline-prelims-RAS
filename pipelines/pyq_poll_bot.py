#!/usr/bin/env python3
"""
STEP 6 — Telegram PYQ quiz-poll bot.

Posts 2 daily "PYQ of the Day" quiz polls to the channel:
  • relevant  — a past RPSC PYQ genuinely tied to today's news (vector match +
                Haiku relevance gate, reusing pdf_generator.find_linked_pyqs)
  • revision  — fallback when < 2 relevant: a high-priority topic_kb PYQ
                (rising/never-skipped, not polled in the last 30 days)

Always posts exactly 2. Each poll is a Telegram quiz (correct answer + Haiku
explanation). Logged to the pyq_polls table.

  python3 pipelines/pyq_poll_bot.py --date 2026-06-04            # post for real
  python3 pipelines/pyq_poll_bot.py --date 2026-06-04 --dry-run  # preview only

NOTE on trajectory: the spec says trajectory='RISING'; the topic_kb values are
ACCELERATING / STABLE-HIGH / SPORADIC / ONE-TIME / DECLINING. The rising-trend
label here is ACCELERATING, so we treat ACCELERATING (and a literal RISING) as
"rising".
"""
import sys, json, random, argparse, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import requests
from pipelines import _common as C
from pipelines import pdf_generator as pg   # reuse find_linked_pyqs (Haiku-gated)

CHANNEL = C.ENV.get("TELEGRAM_CHANNEL_ID")
BOT_TOKEN = C.ENV.get("TELEGRAM_BOT_TOKEN")
CTA_URL = "📖 paperse.in/rpsc/current-affairs"
RISING = ("ACCELERATING", "RISING")

# Telegram poll limits (UTF-16 code units; we keep a safety margin)
Q_MAX, OPT_MAX, EXPL_MAX = 300, 100, 200


# ── helpers ─────────────────────────────────────────────────────────────────────
def _trunc(s, n):
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"

def _correct_index(pyq):
    """0-based index of the correct option among the NON-EMPTY options (in order),
    or None if the PYQ isn't a clean 2-4 option MCQ with a valid answer."""
    ca = str(pyq.get("correct_ans") or "").strip()
    opts, correct = [], None
    for i in ("1", "2", "3", "4"):
        o = (pyq.get(f"option_{i}") or "").strip()
        if not o:
            continue
        if ca == i:
            correct = len(opts)
        opts.append(o)
    if len(opts) < 2 or correct is None:
        return None, opts
    return correct, opts

def _valid_pyq(pyq):
    if not (pyq.get("question") or "").strip():
        return False
    idx, _ = _correct_index(pyq)
    return idx is not None

def _correct_text(pyq):
    ca = str(pyq.get("correct_ans") or "").strip()
    if ca in "1234":
        return (pyq.get(f"option_{ca}") or "").strip() or (pyq.get("correct_text") or "").strip()
    return (pyq.get("correct_text") or "").strip()


# ── 1. selection ────────────────────────────────────────────────────────────────
def get_daily_pyqs(date_str):
    """Return exactly 2 PYQs for the date, each as
    {pyq, type: relevant|revision, news_story_title, score}.
    Primary: best-2 Haiku-confirmed matches to the top-5 stories.
    Fallback: high-priority topic_kb revision PYQs."""
    selected = []

    en_main = C.sb_select("daily_ca_items", params={
        "date": f"eq.{date_str}", "is_main": "eq.true",
        "language": "eq.EN", "order": "priority.desc", "limit": "5"})
    C.log(f"   {len(en_main or [])} main stories for {date_str}")

    if en_main:
        linked = pg.find_linked_pyqs(en_main)          # Haiku-gated YES matches
        linked.sort(key=lambda m: m["score"], reverse=True)  # best similarity first
        seen = set()
        for m in linked:
            pyq = m["pyq"]
            pid = pyq.get("id")
            if pid in seen or not _valid_pyq(pyq):
                continue
            seen.add(pid)
            title = (en_main[m["item_idx"]].get("title") or "").replace("**", "").strip()
            selected.append({"pyq": pyq, "type": "relevant",
                             "news_story_title": title, "score": m["score"]})
            if len(selected) == 2:
                break

    C.log(f"   relevant (news-matched) PYQs: {len(selected)}")

    if len(selected) < 2:
        need = 2 - len(selected)
        fallback = get_fallback_pyqs(need, exclude_ids={s["pyq"].get("id") for s in selected})
        for f in fallback:
            selected.append({"pyq": f, "type": "revision",
                             "news_story_title": None, "score": None})
        C.log(f"   revision (fallback) PYQs: {len(fallback)}")

    return selected[:2]


def get_fallback_pyqs(need, exclude_ids=None):
    """High-priority revision PYQs: from topic_kb topics that are rising
    (ACCELERATING) or never-skipped, ranked rising→never_skipped→priority, take
    top 10 qualifying topics, pick random answerable PYQs not polled in 30 days."""
    exclude_ids = set(exclude_ids or [])

    # PYQs polled in the last 30 days (skip them)
    recent_ids = set()
    try:
        since = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
        recent = C.sb_select("pyq_polls", select="pyq_id",
                             params={"posted_at": f"gte.{since}"})
        recent_ids = {r["pyq_id"] for r in (recent or []) if r.get("pyq_id")}
    except Exception as e:
        C.log(f"   ⚠ pyq_polls recent-lookup skipped: {e}")

    topics = C.sb_select("topic_kb", select="topic,trajectory,never_skipped,priority_score")
    def rising(t):  return (t.get("trajectory") or "").upper() in RISING
    qualifying = [t for t in topics if rising(t) or t.get("never_skipped")]
    qualifying.sort(key=lambda t: (rising(t), bool(t.get("never_skipped")),
                                   t.get("priority_score") or 0), reverse=True)
    top_topics = [t["topic"] for t in qualifying[:10] if t.get("topic")]
    C.log(f"   fallback pool: top {len(top_topics)} qualifying topics")
    random.shuffle(top_topics)                          # random pick among top 10

    out, used = [], set(exclude_ids) | recent_ids
    # pass 1: honour the 30-day exclusion
    for topic in top_topics:
        if len(out) >= need:
            break
        rows = C.sb_select("questions", params={"topic": f"eq.{topic}"})
        cand = [r for r in rows if r.get("id") not in used and _valid_pyq(r)]
        if cand:
            pick = random.choice(cand)
            out.append(pick); used.add(pick.get("id"))
    # pass 2: relax the 30-day rule if we still came up short
    if len(out) < need:
        used2 = exclude_ids | {o.get("id") for o in out}
        for topic in top_topics:
            if len(out) >= need:
                break
            rows = C.sb_select("questions", params={"topic": f"eq.{topic}"})
            cand = [r for r in rows if r.get("id") not in used2 and _valid_pyq(r)]
            if cand:
                pick = random.choice(cand)
                out.append(pick); used2.add(pick.get("id"))
    return out[:need]


# ── 2/3. formatting ──────────────────────────────────────────────────────────────
def _haiku_explanation(pyq, correct_text):
    user = (f"RPSC RAS exam question: {pyq.get('question')}\n"
            f"Correct answer: {correct_text}\n\n"
            "In ONE short sentence (max 20 words), state why this answer is correct. "
            "Factual only. No preamble, no 'The answer is'.")
    try:
        return C.claude_text(
            "You write one-line factual exam explanations. No preamble.",
            user, max_tokens=70, model=C.HAIKU_MODEL).strip()
    except Exception as e:
        C.log(f"   ⚠ Haiku explanation failed: {e}")
        return (pyq.get("memory_tip") or "").strip()

def _fit_explanation(correct_text, year, expl):
    """Assemble the quiz explanation within Telegram's 200-char limit.
       ✅ {correct} — RPSC RAS {year}\n\n{explanation}\n\n📖 paperse.in/..."""
    ct = _trunc(correct_text, 70)
    head = f"✅ {ct} — RPSC RAS {year}"
    budget = EXPL_MAX - 6   # safety margin (emoji can cost 2 UTF-16 units)
    fixed = len(head) + 2 + 2 + len(CTA_URL)   # two "\n\n" separators
    expl = (expl or "").strip()
    avail = budget - fixed
    if avail <= 4:                              # no room for both expl and URL → drop URL
        avail2 = budget - len(head) - 2
        if avail2 <= 0:
            return head[:EXPL_MAX]
        return f"{head}\n\n{_trunc(expl, avail2)}"
    return f"{head}\n\n{_trunc(expl, avail)}\n\n{CTA_URL}"

def _lang_field(pyq, base, lang):
    """Value of `base` in `lang`; HI falls back to EN per-field when the Hindi
    column is empty (so a poll still posts even if a PYQ lacks a translation)."""
    if lang == "HI":
        return (pyq.get(base + "_hi") or pyq.get(base) or "")
    return (pyq.get(base) or "")


def format_poll(pyq, lang="EN", explanation=None):
    """Return {question, options, correct_option_id, explanation} for sendPoll, in
    `lang`. Pass `explanation` to reuse a previously-built one (so posting the SAME
    PYQ in EN and HI costs only ONE Haiku explanation call)."""
    ca = str(pyq.get("correct_ans") or "").strip()
    opts, correct = [], None
    for i in ("1", "2", "3", "4"):
        o = _lang_field(pyq, f"option_{i}", lang).strip()
        if not o:
            continue
        if ca == i:
            correct = len(opts)
        opts.append(_trunc(o, OPT_MAX))
    if explanation is None:
        explanation = _fit_explanation(_correct_text(pyq), pyq.get("year"),
                                        _haiku_explanation(pyq, _correct_text(pyq)))
    return {
        "question": _trunc(_lang_field(pyq, "question", lang), Q_MAX),
        "options": opts,
        "correct_option_id": (correct if len(opts) >= 2 else None),
        "explanation": explanation,
    }


# ── 4/5. posting ─────────────────────────────────────────────────────────────────
def _tg(method, payload):
    r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
                      json=payload, timeout=30)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {json.dumps(data)[:300]}")
    return data["result"]

# Bug 2/3 — two clearly-separated PYQ sections, each with its own fixed header:
# an English block (all EN polls) followed by a Hindi block (all HI polls).
SECTION_HEADER = {
    "EN": "RPSC 📚 PYQ of the Day | High-Priority Revision Topic 🇬🇧",
    "HI": "RPSC 📚 PYQ of the Day | उच्च-प्राथमिकता विषय 🇮🇳",
}

def header_text(selected):
    if selected["type"] == "relevant":
        return f"📚 PYQ of the Day\nRelated to: {selected['news_story_title']}"
    return "📚 PYQ of the Day\nHigh-priority revision topic"

def post_section_header(lang):
    return _tg("sendMessage", {"chat_id": CHANNEL, "text": SECTION_HEADER[lang],
                               "disable_notification": True})

def post_quiz_poll(selected, poll=None):
    """Telegram sendPoll as a quiz. Returns the poll message_id."""
    poll = poll or format_poll(selected["pyq"])
    res = _tg("sendPoll", {
        "chat_id": CHANNEL,
        "question": poll["question"],
        "options": [{"text": o} for o in poll["options"]],
        "type": "quiz",
        "correct_option_id": poll["correct_option_id"],
        "explanation": poll["explanation"],
        "is_anonymous": True,
    })
    return res.get("message_id")

def run_daily_pyq_polls(date_str, dry_run=False):
    if not BOT_TOKEN or not CHANNEL:
        C.log("   ⚠ TELEGRAM_BOT_TOKEN / TELEGRAM_CHANNEL_ID missing — cannot post.")
        if not dry_run:
            return []
    selected = get_daily_pyqs(date_str)
    if not selected:
        C.log("   ⚠ no PYQs selected — nothing to post.")
        return []

    # Build each PYQ once in EN + HI (one Haiku explanation, reused for the HI poll).
    prepared = []
    for n, s in enumerate(selected, 1):
        en = format_poll(s["pyq"], "EN")
        if en["correct_option_id"] is None:
            C.log(f"   ⚠ poll {n} skipped — unusable options (Q{s['pyq'].get('q_no')})")
            continue
        hi = format_poll(s["pyq"], "HI", explanation=en["explanation"])
        prepared.append((s, en, hi))

    if not prepared:
        C.log("   ⚠ no usable PYQ polls — nothing to post.")
        return []

    if dry_run:
        for n, (s, en, hi) in enumerate(prepared, 1):
            _print_preview(n, s, en)
        return selected

    def _log(s, mid):
        try:
            C.sb_insert("pyq_polls", {
                "date": date_str, "pyq_id": s["pyq"].get("id"),
                "news_story_title": s.get("news_story_title"),
                "type": s["type"], "poll_message_id": mid})
        except Exception as e:
            C.log(f"   ⚠ pyq_polls log failed: {e}")

    # ── Section 1: English ──────────────────────────────────────────────────
    post_section_header("EN")
    for s, en, hi in prepared:
        mid = post_quiz_poll(s, en)
        _log(s, mid)
        C.log(f"   ✓ posted EN {s['type']} poll (message_id={mid})")

    # ── Section 2: Hindi ────────────────────────────────────────────────────
    post_section_header("HI")
    for s, en, hi in prepared:
        mid = post_quiz_poll(s, hi)
        _log(s, mid)
        C.log(f"   ✓ posted HI {s['type']} poll (message_id={mid})")

    return selected


def _print_preview(n, s, poll):
    pyq = s["pyq"]
    why = (f"matched news: “{s['news_story_title']}” (score {s['score']:.3f})"
           if s["type"] == "relevant" else "high-priority revision (fallback)")
    letters = "ABCD"
    print(f"\n┌─ POLL {n}  [{s['type'].upper()}] — {why}")
    print(f"│  source: RPSC RAS {pyq.get('year')} Q{pyq.get('q_no')} · topic: {pyq.get('topic')}")
    print(f"│  HEADER ▸ " + header_text(s).replace("\n", "\n│           "))
    print(f"│  Q ▸ {poll['question']}")
    for i, o in enumerate(poll["options"]):
        mark = " ✅" if i == poll["correct_option_id"] else ""
        print(f"│     ({letters[i]}) {o}{mark}")
    print(f"│  EXPLANATION ▸ " + poll["explanation"].replace("\n", "\n│                "))
    print(f"└─ (explanation length: {len(poll['explanation'])} chars)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="PaperSe daily PYQ quiz-poll bot")
    ap.add_argument("--date", default=datetime.date.today().isoformat(),
                    help="YYYY-MM-DD (daily_ca_items date to source stories from)")
    ap.add_argument("--dry-run", action="store_true", help="preview only, no posting/logging")
    args = ap.parse_args()

    C.log("=" * 64)
    C.log(f"STEP 6 — PYQ Poll Bot — {args.date}" + ("  (DRY RUN)" if args.dry_run else ""))
    C.log("=" * 64)
    run_daily_pyq_polls(args.date, dry_run=args.dry_run)
