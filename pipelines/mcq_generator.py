#!/usr/bin/env python3
"""
STEP 3 — MCQ generator. Runs after daily_ca_pipeline.py.

  python3 pipelines/mcq_generator.py 2026-06-02

Takes today's main items and generates 1-2 high-quality MCQs PER ITEM (capped at 8
for the day) using an expert-RPSC-question-setter prompt with strict quality rules
(no ministerial positions, no family/procedural trivia, plausible same-type
distractors, RPSC 60/30/10 direct/negative/multi-statement pattern). Stores in
daily_mcqs (FK source_item_id) and keeps the live-site columns populated.
"""
import sys, re, datetime
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from pipelines import _common as C

# ── System prompt — expert RPSC RAS question setter, strict quality rules ───────
SYS = """You are an expert RPSC RAS Prelims question setter with 10 years of experience. You generate MCQs exactly matching the RPSC exam pattern.

RPSC QUESTION PATTERN (follow strictly):
- 60% Direct factual: "Which/What/Where/When/Who"
- 30% Negative: "Which of the following is NOT correct"
- 10% Multi-statement: "Which of the following statements are correct: 1. ___ 2. ___ 3. ___"

STRICT QUALITY RULES:
1. Question must test ONE specific fact from the news
2. The fact must be: name, number, date, place, scheme name, policy name, record, award name
3. All 4 options must be plausible and similar
   - If answer is a number → all options are numbers
   - If answer is a place → all options are places
   - If answer is a name → all options are similar names
4. Never use obviously wrong distractors
5. Difficulty = medium (not trivial, not obscure)
6. Each question has exactly one correct answer

NEVER generate questions about:
- Who holds a ministerial position (changes frequently)
- Family members of news subjects
- Reactions, quotes or opinions
- Procedural details (how many days, which flight)
- Facts that cannot be verified from the news item
- Anything a student could guess without studying

GOOD QUESTION EXAMPLES:
"Under which policy was E85 ethanol-blended fuel launched in India?
A) National Biofuel Policy 2018
B) National Energy Policy 2020
C) Ethanol Blending Programme 2015
D) Green Fuel Initiative 2019
Answer: A"

"R Praggnanandhaa won the Norway Chess 2026 tournament by defeating which player in the final round?
A) Magnus Carlsen
B) Fabiano Caruana
C) Alireza Firouzja
D) Hikaru Nakamura
Answer: C"

BAD QUESTION EXAMPLES (never generate these):
"Who is the Union Minister for Petroleum and Natural Gas?" — ministerial position, changes
"Which city did the Nepal FM fly from?" — irrelevant procedural detail
"What did India's mother think about the chess tournament?" — family/personal angle

RAJASTHAN RULE:
At least 1 out of every 5 MCQs must have a Rajasthan angle if any Rajasthan news is in today's top 5.

OUTPUT FORMAT (strict JSON):
{
  "questions": [
    {
      "question": "question text here",
      "options": ["A) option1", "B) option2", "C) option3", "D) option4"],
      "correct": "A",
      "explanation": "one line explanation of why this is correct",
      "difficulty": "medium",
      "type": "direct-factual/negative/multi-statement"
    }
  ]
}"""

# new-format → DB mappings
DIFF_MAP = {"easy": 1, "medium": 2, "hard": 3}
TYPE_MAP = {"direct-factual": "DIRECT-FACTUAL", "direct": "DIRECT-FACTUAL",
            "negative": "NOT-NEGATIVE", "not-negative": "NOT-NEGATIVE",
            "multi-statement": "MULTI-STATEMENT", "multi": "MULTI-STATEMENT"}
TYPES = ["DIRECT-FACTUAL", "NOT-NEGATIVE", "MULTI-STATEMENT"]

# Per-question directive used to STEER each MCQ to a planned type, so the day's set
# actually hits the RPSC 60/30/10 mix (the system prompt states it, but per-call the
# model defaults to direct-factual). The system prompt itself is unchanged.
TYPE_DIRECTIVE = {
    "DIRECT-FACTUAL":  "a DIRECT FACTUAL question (Which/What/Where/When/Who)",
    "NOT-NEGATIVE":    "a NEGATIVE question phrased 'Which of the following is NOT correct'",
    "MULTI-STATEMENT": "a MULTI-STATEMENT question (2-3 numbered statements, ask which are correct)",
}


def plan_types(total):
    """60/30/10 Direct/Negative/Multi split across `total` MCQs, round-robin spread
    so the types are distributed across the day rather than clustered."""
    n_df = round(total * 0.60)
    n_neg = round(total * 0.30)
    n_ms = max(0, total - n_df - n_neg)
    lists = [["DIRECT-FACTUAL"] * n_df, ["NOT-NEGATIVE"] * n_neg, ["MULTI-STATEMENT"] * n_ms]
    seq = []
    while any(lists):
        for L in lists:
            if L:
                seq.append(L.pop())
    return seq[:total] or ["DIRECT-FACTUAL"] * total


def richness(item):
    """1 or 2 MCQs per item based on how much testable content it has."""
    bl = item.get("bullets") or []
    body = len((item.get("context") or "")) + sum(len(b) for b in bl)
    return 2 if (len(bl) >= 4 and body > 250) else 1


def _news_text(item):
    """The news the MCQ must be drawn from. Prefer an explicit 'text' (test harness);
    else assemble from the authored title + summary + context + bullets."""
    if item.get("text"):
        return item["text"].strip()
    parts = [(item.get("title") or "").replace("**", ""),
             item.get("summary") or "", item.get("context") or "",
             " ".join(item.get("bullets") or [])]
    return " ".join(p for p in parts if p).strip()


def _strip_prefix(s):
    """'A) National Biofuel Policy' → 'National Biofuel Policy' (handles A) A. A- A:)."""
    return re.sub(r"^\s*[A-Da-d]\s*[\).\-:]\s*", "", (s or "").strip()).strip()


def _parse_options(opts):
    """Accept the new list form ['A) ...', 'B) ...'] or a legacy dict {a:..,b:..};
    return {'a','b','c','d'} with the letter prefix stripped."""
    out = {"a": None, "b": None, "c": None, "d": None}
    if isinstance(opts, dict):
        for k, v in opts.items():
            kk = (k or "").strip().lower()[:1]
            if kk in out:
                out[kk] = _strip_prefix(v)
    elif isinstance(opts, list):
        for i, v in enumerate(opts[:4]):
            out["abcd"[i]] = _strip_prefix(v)
    return out


def _norm_correct(c):
    c = (c or "a").strip().lower()
    return c[:1] if c[:1] in "abcd" else "a"


def gen_mcq(item, types):
    """Generate len(types) MCQs (1-2) for ONE item, each STEERED to a planned RPSC
    type so the day hits the 60/30/10 mix. `types` is a list of canonical type names
    (DIRECT-FACTUAL / NOT-NEGATIVE / MULTI-STATEMENT), one per requested question.
    Returns the model's question dicts (new format), in the requested order."""
    n = len(types)
    spec = "; ".join(f"#{i + 1} must be {TYPE_DIRECTIVE.get(t, TYPE_DIRECTIVE['DIRECT-FACTUAL'])}"
                     for i, t in enumerate(types))
    user = (f"NEWS ITEM:\n{_news_text(item)}\n\n"
            f"Category: {item.get('category')}\n"
            f"RPSC Angle (what this item should test): {item.get('rpsc_angle') or '—'}\n\n"
            f"Generate exactly {n} high-quality MCQ{'s' if n > 1 else ''} from THIS item only "
            f"(do not draw on any other news), in this order: {spec}. "
            f"Follow the STRICT QUALITY RULES and the OUTPUT FORMAT exactly. "
            f"Return ONLY the JSON object.")
    data, _ = C.claude_json(SYS, user, max_tokens=1300, model=C.HAIKU_MODEL)  # cost-opt: Haiku
    if not isinstance(data, dict):
        return []
    qs = data.get("questions")
    if not qs and data.get("question"):     # tolerate a single-object reply
        qs = [data]
    return [q for q in (qs or []) if isinstance(q, dict) and q.get("question")][:n]


def _to_row(q, item, ds, q_no, planned_type=None):
    """Map a new-format question dict → daily_mcqs row, or None if malformed.
    planned_type (the steered type) is authoritative; fall back to the model's
    self-reported type when not steered."""
    opts = _parse_options(q.get("options"))
    if sum(1 for v in opts.values() if v) < 4:
        return None                          # need all four options
    correct = _norm_correct(q.get("correct"))
    diff = DIFF_MAP.get(str(q.get("difficulty") or "medium").strip().lower(), 2)
    qtype = planned_type or TYPE_MAP.get(str(q.get("type") or "").strip().lower(), "DIRECT-FACTUAL")
    return {
        "date": ds, "q_no": q_no,
        "question": q.get("question"),
        "option_a": opts["a"], "option_b": opts["b"],
        "option_c": opts["c"], "option_d": opts["d"],
        "correct": correct,                 # live-site column (lowercase letter)
        "correct_option": correct,          # spec column
        "explanation": q.get("explanation"),
        "question_type": qtype,
        "category": item.get("category"),
        "subject": item.get("category"),    # live-site column
        "difficulty": diff,
        "source_item_id": item.get("id"),
    }


def main(date):
    ds = date.isoformat()
    C.log("=" * 64)
    C.log(f"STEP 3 — MCQ Generator — {ds}")
    C.log("=" * 64)

    items = C.sb_select("daily_ca_items", params={
        "date": f"eq.{ds}", "is_main": "eq.true", "language": "eq.EN", "order": "id"})
    if not items:
        C.log("✗ No main items found for this date. Run daily_ca_pipeline.py first.")
        return None
    C.log(f"   {len(items)} main items today")

    # 1-2 MCQs per item (by richness), capped at 8 for the day.
    alloc = [richness(it) for it in items]
    cap = 8
    while sum(alloc) > cap:                  # trim the richest first until under cap
        i = max(range(len(alloc)), key=lambda k: alloc[k])
        if alloc[i] <= 1:
            break
        alloc[i] -= 1
    total = min(sum(alloc), cap)
    # Plan the day's RPSC 60/30/10 type mix, then hand each item its slice of types.
    type_seq = plan_types(total)
    C.log(f"   Generating up to {total} MCQs · type plan: "
          f"{ {t: type_seq.count(t) for t in TYPES} }")

    C.sb_delete("daily_mcqs", {"date": ds})  # idempotent re-run for this date

    rows, q_no, ti = [], 1, 0
    for it, n in zip(items, alloc):
        if q_no > cap or ti >= total:
            break
        n = min(n, total - ti)
        types = type_seq[ti:ti + n]
        ti += n
        try:
            qs = gen_mcq(it, types)
        except Exception as e:
            C.log(f"   ⚠ MCQ gen failed for item {it['id']}: {e}")
            continue
        for k, q in enumerate(qs):
            if q_no > cap:
                break
            planned = types[k] if k < len(types) else None
            row = _to_row(q, it, ds, q_no, planned_type=planned)
            if not row:
                C.log(f"   ⚠ skipped malformed MCQ for item {it['id']}")
                continue
            rows.append(row)
            C.log(f"      Q{q_no} [{row['question_type']} · d{row['difficulty']}] "
                  f"{(it.get('category') or 'General')[:26]:26s} correct={row['correct']} "
                  f":: {str(row['question'])[:58]}")
            q_no += 1

    if not rows:
        C.log("✗ No MCQs generated.")
        return None
    C.sb_insert("daily_mcqs", rows, returning=False)
    cnt = C.sb_count("daily_mcqs", {"date": f"eq.{ds}"})
    C.log(f"\n✓ STEP 3 complete — {cnt} MCQs in daily_mcqs for {ds}")
    return cnt


if __name__ == "__main__":
    d = C.parse_date(sys.argv[1]) if len(sys.argv) > 1 else datetime.date.today()
    # Safety net: refuse absurd future dates (e.g. a fat-fingered arg a month
    # ahead, which once wrote a 2026-07-03 batch). Today and past-date back-fill
    # are always allowed; only dates beyond tomorrow are rejected.
    if d > datetime.date.today() + datetime.timedelta(days=1):
        sys.exit(f"✗ Refusing future date {d.isoformat()} "
                 f"(today is {datetime.date.today().isoformat()}). Pass a real date.")
    sys.exit(0 if main(d) else 1)
