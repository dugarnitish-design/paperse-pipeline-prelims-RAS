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
1. Test ONE stable recurring topic fact that will be valid next year too — per the
   CATEGORY PATTERNS for this item's category. Use the news as the topic hook only. Do
   NOT test the volatile news event (which state did X, how many years was it
   suspended, this week's change).
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

CATEGORY PATTERNS — what RPSC tests vs NEVER tests, by category:

Wildlife & Environment:
  TESTS: reserve full name, state location, species involved, designation (Tiger Reserve/
    Ramsar/Biosphere/National Park), total count of that reserve type in India
  NEVER: individual animal age, exact entry dates, individual animal behaviour, population of one specific animal

National Science & Technology:
  TESTS: organisation name (ISRO/DRDO/CSIR), mission/weapon/technology name, first/largest/only
    achievement, key number (range/altitude/capacity)
  NEVER: internal structural design, material composition, technical specifications, engineering details

National Sports & Awards:
  TESTS: winner full name, award exact name, tournament name, defeated whom, edition/year
  NEVER: match scores, team compositions, training details, player statistics

National Schemes & Governance:
  TESTS: scheme full name, launch year, implementing ministry, key provision (days/amount),
    target beneficiaries, unique feature
  NEVER: state-level implementation, administrative meeting outcomes, budget allocation for specific states

International Politics & Elections:
  TESTS: agreement name, partner country, India rank in global indices, key provision, which organisation published report
  NEVER: nuclear warhead counts, missile deployment details, diplomatic visit itinerary, number of days of visit

Books, Awards & Personalities:
  TESTS: award name, winner name, category, which institution gives the award, year
  NEVER: book plot details, personal biography details, childhood stories

Bills & Legislation:
  TESTS: bill exact name, Article it relates to, what it changes, who passed it, key provision
  NEVER: voting margins, debate details, committee member names

Monetary Policy & RBI:
  TESTS: rate name (repo/reverse repo/CRR/SLR), new rate value, what changed from before, RBI Governor name, MPC full form
  NEVER: technical banking operations, individual bank performance, stock market impact

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


def _pyq_examples(item, k=3):
    """Real RPSC PYQs matched to this news item → 'question … Answer: …' style anchors.
    daily_ca_items.pyq gives year+q_no (ChromaDB match, computed on the Mac); the full
    question + correct answer come from the `questions` table (Supabase REST read, so
    this works on Railway too). Used so generated MCQs mirror genuine RPSC pattern."""
    out = []
    for c in (item.get("pyq") or [])[:k]:
        yr, qno = c.get("year"), str(c.get("q_no") or "").strip()
        if not (yr and qno):
            continue
        try:
            rows = C.sb_select("questions", params={
                "year": f"eq.{yr}", "q_no": f"eq.{qno}", "limit": "1"})
        except Exception as e:
            C.log(f"   ⚠ PYQ fetch failed ({yr} Q{qno}): {e}")
            rows = None
        if not rows:
            continue
        q = rows[0]
        qt = " ".join((q.get("question") or "").split())
        ans = (q.get("correct_text") or "").strip()
        if not ans:                                  # fall back to the lettered option
            ca = str(q.get("correct_ans") or "").strip()
            ans = (q.get(f"option_{ca}") or "").strip() if ca in "1234" else ""
        if qt:
            out.append(f"(RPSC RAS {yr}) {qt}" + (f"  Answer: {ans}" if ans else ""))
    return out


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
    # PYQ connection — feed the item's matched real RPSC questions (full text + answer)
    # so generated MCQs are modelled on genuine RPSC pattern/difficulty.
    examples = _pyq_examples(item)
    ex_block = ""
    if examples:
        ex_block = ("\nHere are real RPSC questions asked about this topic in past exams:\n"
                    + "\n".join(f"- {e}" for e in examples) + "\n"
                    "Generate NEW questions in the SAME style, difficulty and type as these "
                    "real RPSC questions. Do NOT repeat them — model your questions on their pattern.\n")
    user = (f"NEWS ITEM:\n{_news_text(item)}\n\n"
            f"Category: {item.get('category')}\n"
            f"RPSC Angle (what this item should test): {item.get('rpsc_angle') or '—'}\n"
            f"{ex_block}\n"
            f"Use the news ONLY as the topic hook. Ask about the underlying scheme/reserve/"
            f"organisation per the CATEGORY PATTERNS — NOT about today's specific event.\n"
            f"  GOOD: Under which Act was MGNREGS launched?\n"
            f"  GOOD: How many days of employment does MGNREGS guarantee?\n"
            f"  BAD:  After how many years did the Centre resume MGNREGS?\n"
            f"  BAD:  Which state recently resumed MGNREGS?\n\n"
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


# ── FIX B: post-generation quality gate ─────────────────────────────────────────
QGATE_SYS = """You are an RPSC RAS Prelims MCQ quality checker.

For each MCQ check:
1. Is the answer a STABLE fact that will be true next year and 5 years from now?
2. Does this test KNOWLEDGE of a topic or just memory of one news article?
3. Would this question make sense in an exam without reading today's news?
4. Has RPSC asked similar questions before about this topic?

Return KEEP if all 4 are true. Return REJECT if any 1 is false.

ALWAYS REJECT:
- Questions about animal ages or individual counts
- Questions about nuclear warhead numbers
- Questions about which state did what today
- Questions about visit durations or itineraries
- Questions where the answer changes every year
- Questions testing article reading, not knowledge

ALWAYS KEEP:
- Questions about scheme names and launch years
- Questions about reserve names and states
- Questions about award winners and tournaments
- Questions about constitutional provisions
- Questions about organisation names and achievements"""


def quality_gate(mcqs):
    """One Haiku call that judges each MCQ → returns a list[bool] (keep) aligned to
    `mcqs` (each: {question, options[list], correct, category}). Fail-open: on a gate
    error or a missing verdict, KEEP (never silently wipe the brief)."""
    if not mcqs:
        return []
    lines = []
    for i, m in enumerate(mcqs, 1):
        opts = " | ".join(m.get("options") or [])
        lines.append(f"{i}. [{m.get('category')}] Q: {m.get('question')} | OPTIONS: {opts} "
                     f"| ANSWER: {m.get('correct')}")
    user = ('Judge each MCQ below. Return ONLY JSON: '
            '{"results":[{"n":1,"decision":"KEEP|REJECT"}, ...]} — one entry per MCQ, in order.\n\n'
            + "\n".join(lines))
    try:
        data, _ = C.claude_json(QGATE_SYS, user, max_tokens=900, model=C.HAIKU_MODEL)
        verdicts = {int(r["n"]): str(r.get("decision", "KEEP")).strip().upper().startswith("KEEP")
                    for r in (data.get("results") or []) if "n" in r}
    except Exception as e:
        C.log(f"   ⚠ quality gate failed — keeping all ({e})")
        return [True] * len(mcqs)
    return [verdicts.get(i, True) for i in range(1, len(mcqs) + 1)]


def _gate_keep(cands):
    """Filter candidate dicts {q,item,type} through the quality gate."""
    keep = quality_gate([{"question": c["q"].get("question"),
                          "options": c["q"].get("options"),
                          "correct": c["q"].get("correct"),
                          "category": c["item"].get("category")} for c in cands])
    return [c for c, k in zip(cands, keep) if k]


def _qkey(q):
    return re.sub(r"\W+", " ", (q.get("question") or "").lower()).strip()


def _generate_candidates(items, alloc, type_seq, total):
    """Per-item generation (FIX C): one Haiku call per item, 1-2 type-steered MCQs each.
    Returns candidate dicts {q, item, type}."""
    cands, ti = [], 0
    for it, n in zip(items, alloc):
        if ti >= total:
            break
        n = min(n, total - ti)
        types = type_seq[ti:ti + n]
        ti += n
        try:
            qs = gen_mcq(it, types)
        except Exception as e:
            C.log(f"   ⚠ MCQ gen failed for item {it.get('id')}: {e}")
            continue
        for k, q in enumerate(qs):
            cands.append({"q": q, "item": it, "type": types[k] if k < len(types) else None})
    return cands


def _select_spread(cands, cap=8, max_per_item=2):
    """FIX C: keep ≤max_per_item per source item, cap total; preserve order."""
    out, per = [], {}
    for c in cands:
        iid = c["item"].get("id")
        if per.get(iid, 0) >= max_per_item:
            continue
        out.append(c)
        per[iid] = per.get(iid, 0) + 1
        if len(out) >= cap:
            break
    return out


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

    # Generate (per item) → quality-gate → regenerate if <3 survive (max 2 extra
    # attempts) → spread (≤2/item, cap 8). FIX B + C.
    kept, seen = [], set()
    for attempt in range(3):                       # 1 initial + up to 2 regenerations
        cands = _generate_candidates(items, alloc, type_seq, total)
        cands = [c for c in cands if _qkey(c["q"]) and _qkey(c["q"]) not in seen]
        for c in cands:
            seen.add(_qkey(c["q"]))
        passed = _gate_keep(cands)
        C.log(f"   gate attempt {attempt + 1}: {len(cands)} generated → {len(passed)} kept")
        kept.extend(passed)
        if len(kept) >= 3:
            break
        if attempt < 2:
            C.log(f"   ↻ only {len(kept)} quality MCQs — regenerating…")

    final = _select_spread(kept, cap=cap, max_per_item=2)
    if not final:
        C.log("✗ No MCQs passed the quality gate.")
        return None
    items_covered = len({c['item'].get('id') for c in final})
    if items_covered < 3:
        C.log(f"   ⚠ only {items_covered} distinct news item(s) covered (target ≥3) — "
              f"limited by available/quality items today")

    C.sb_delete("daily_mcqs", {"date": ds})  # idempotent re-run for this date
    rows, q_no = [], 1
    for c in final:
        row = _to_row(c["q"], c["item"], ds, q_no, planned_type=c.get("type"))
        if not row:
            continue
        rows.append(row)
        C.log(f"      Q{q_no} [{row['question_type']} · d{row['difficulty']}] "
              f"{(c['item'].get('category') or 'General')[:26]:26s} correct={row['correct']} "
              f":: {str(row['question'])[:58]}")
        q_no += 1

    if not rows:
        C.log("✗ No MCQs generated.")
        return None
    C.sb_insert("daily_mcqs", rows, returning=False)
    cnt = C.sb_count("daily_mcqs", {"date": f"eq.{ds}"})
    C.log(f"\n✓ STEP 3 complete — {cnt} MCQs in daily_mcqs for {ds} "
          f"(across {items_covered} news items)")
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
