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

SYSTEM_PROMPT = """You are an RPSC RAS Prelims 2026 exam pattern expert.

Based on analysis of 873 RPSC questions from 2015-2024:

HIGHEST PRIORITY TOPICS (appeared every year, must cover):
- National Sports & Awards (19 Qs)
- Books, Awards & Personalities (14 Qs)
- International Politics & Elections (10 Qs)
- Rajasthan schemes & governance
- Bills & Legislation (accelerating)
- ISRO/DRDO/Space (6/6 years)
- Environment & Wildlife (consistent)

QUESTION TYPES RPSC uses:
- 60% direct factual (who won, what is, which article, headquarters of, launched by)
- 30% not-which-of-these
- 10% multi-statement true/false

A news story is exam-worthy if it contains at least one specific testable fact:
- A name (person, place, scheme, award)
- A number (rank, date, amount, target)
- A first/largest/only/new fact
- A constitutional/legal provision
- A Rajasthan-specific detail

RPSC never tests:
- Incidents without policy angle
- Foreign sports without India win
- Internal party politics
- Corporate M&A
- Celebrities
- Ceremonial events without data"""


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
    reply = C.claude_text(SYSTEM_PROMPT, _user_prompt(item), max_tokens=200, model=MODEL)
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
