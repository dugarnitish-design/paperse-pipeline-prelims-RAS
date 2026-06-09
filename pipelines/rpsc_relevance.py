#!/usr/bin/env python3
"""
LAYER 3 — RPSC relevance check (Claude Sonnet).

One call per candidate (after global pre-reject + dedup), capped at MAX_CALLS/day.
Claude is the *real* relevance gate now — the keyword filter only scores/labels.
Each item gets a structured verdict (YES / MAYBE / NO) plus a one-line reason, the
specific testable exam fact, and which high-priority RPSC topic it maps to.

  from pipelines import rpsc_relevance as RPSC
  kept, log = RPSC.apply(items, max_calls=50)   # kept = YES + MAYBE

Model: Sonnet (C.CLAUDE_MODEL). The repo's configured Sonnet is used (there is no
"4.1" alias in the SDK); override via RPSC_MODEL env if a newer Sonnet is wired up.
"""
import re
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from pipelines import _common as C

MAX_CALLS = 85          # hard cap on Claude calls per run (cost guard)
# Sonnet for this layer: quality over cost. Haiku was too aggressive — it rejected
# legitimate RPSC stories (Venezuela visit, NEET) on over-literal reasoning. Default
# is the committed choice (reliable on Railway); override with RPSC_MODEL if needed.
MODEL = C.ENV.get("RPSC_MODEL", "claude-sonnet-4-20250514")
KEEP_VERDICTS = {"YES", "MAYBE"}   # NO is dropped

SYSTEM_PROMPT = """You are an RPSC RAS Prelims 2026 exam filter. Your only job is
to decide if a news item contains a fact that RPSC would test
in an objective MCQ.

RPSC tests SPECIFIC FACTS like:
- Who won which award/medal/prize
- Which scheme was launched, by whom, with what target/amount
- Which country/organisation India signed MoU with
- Who was appointed to which constitutional post (not party post)
- Which bill was passed and what does it do
- ISRO/DRDO mission name, payload, achievement
- Wildlife census numbers, new reserves, Ramsar sites
- India rank in global indices (HDI, GHI, GII)
- Rajasthan specific: schemes, appointments, records, geography

RPSC NEVER tests:
- Which politician got party ticket or nomination
- Which party won internal elections or gave Rajya Sabha seat
- Political statements or speeches
- Routine government meetings without concrete outcome
- State HC judgments unless constitutional significance
- Cricket match scores or squad selections for future tournaments
- Celebrity news
- Corporate deals or stock market news
- Foreign news without direct India angle

CRITICAL RULE — The MCQ test:
Before saying YES ask yourself: what would the MCQ question be?
If the question is "Which party nominated X?" → NO
If the question is "What is X's rank in HDI?" → YES
If the question is "Who won the Y award?" → YES
If the question is "Which politician got Rajya Sabha ticket?" → NO

A Rajasthan politician name + Rajya Sabha does NOT make it
exam relevant. RPSC tests Rajya Sabha composition and process
— not party nominations.

Respond exactly:
VERDICT: YES/MAYBE/NO
REASON: one sentence
EXAM_ANGLE: the specific MCQ-worthy fact (or "none" if NO)
TOPIC_MATCH: which RPSC topic this maps to (or "none" if NO)"""


def _user_prompt(item):
    return (
        f"Title: {item.get('title','')}\n"
        f"Category: {item.get('category')}\n"
        f"Source: {item.get('source','')}\n"
        f"Text: {(item.get('text') or '')[:400]}\n\n"
        "Respond exactly:\n"
        "VERDICT: YES/MAYBE/NO\n"
        "REASON: one sentence\n"
        "EXAM_ANGLE: specific testable fact (or \"none\" if NO)\n"
        "TOPIC_MATCH: which high-priority topic this maps to (or \"none\" if NO)"
    )


def _parse(reply):
    """Parse the fixed-format reply into a dict."""
    out = {"verdict": "NO", "reason": "", "exam_angle": "none", "topic_match": "none"}
    for line in (reply or "").splitlines():
        m = re.match(r"\s*(VERDICT|REASON|EXAM_ANGLE|TOPIC_MATCH)\s*:\s*(.*)", line, re.I)
        if not m:
            continue
        key, val = m.group(1).upper(), m.group(2).strip()
        if key == "VERDICT":
            v = val.upper()
            out["verdict"] = "YES" if v.startswith("YES") else "MAYBE" if v.startswith("MAYBE") else "NO"
        elif key == "REASON":
            out["reason"] = val
        elif key == "EXAM_ANGLE":
            out["exam_angle"] = val
        elif key == "TOPIC_MATCH":
            out["topic_match"] = val
    return out


def check(item):
    """Single Claude relevance call. Returns the parsed verdict dict."""
    # cache_system: SYSTEM_PROMPT is re-sent on every candidate (60-85×/run) — caching
    # it bills cache hits at ~10% input cost. Biggest single prompt-caching win.
    reply = C.claude_text(SYSTEM_PROMPT, _user_prompt(item), max_tokens=200, model=MODEL,
                          cache_system=True)
    return _parse(reply)


def apply(items, max_calls=MAX_CALLS):
    """Run the relevance check over `items` (already ranked best-first by the
    caller). Spends at most `max_calls` Claude calls; any items beyond the cap are
    dropped with verdict 'SKIPPED' (and logged, never silently). Returns:
        (kept, log)
      kept = items whose verdict is in KEEP_VERDICTS, each annotated with
             rpsc_verdict / rpsc_reason / rpsc_exam_angle / rpsc_topic.
      log  = list of per-item dicts for the rpsc_filter_log (covers ALL items).
    """
    kept, log = [], []
    for i, it in enumerate(items):
        if i >= max_calls:
            log.append({"title": it.get("title"), "category": it.get("category"),
                        "source": it.get("source"), "verdict": "SKIPPED",
                        "reason": f"exceeded {max_calls}-call/day cap",
                        "exam_angle": "none", "topic_match": "none"})
            continue
        try:
            v = check(it)
        except Exception as e:
            C.log(f"   ⚠ RPSC relevance call failed: {e}")
            v = {"verdict": "MAYBE", "reason": f"call error: {e}",
                 "exam_angle": "none", "topic_match": "none"}
        it.update({"rpsc_verdict": v["verdict"], "rpsc_reason": v["reason"],
                   "rpsc_exam_angle": v["exam_angle"], "rpsc_topic": v["topic_match"]})
        log.append({"title": it.get("title"), "category": it.get("category"),
                    "source": it.get("source"), **v})
        if v["verdict"] in KEEP_VERDICTS:
            kept.append(it)
    return kept, log
