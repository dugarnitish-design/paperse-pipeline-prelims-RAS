#!/usr/bin/env python3
"""
STEP 3 — MCQ generator. Runs after daily_ca_pipeline.py.

  python3 pipelines/mcq_generator.py 2026-06-02

Takes today's main items, generates 5-8 MCQs (1-2 per item by richness) with the
RPSC distribution 60% Direct-Factual / 20% Not-Negative / 20% Multi-Statement,
stores in daily_mcqs (FK source_item_id) and keeps the live-site columns populated.
"""
import sys, datetime, math
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from pipelines import _common as C

SYS = ("Generate RPSC RAS pattern MCQ from this current affairs item. "
       "Follow this distribution: 60% Direct Factual, 20% Not/Negative, 20% Multi-Statement. "
       "4 options, 1 correct only. Wrong options must be plausible near-neighbours, not "
       "obviously wrong. Explanation max 2 lines. Never make the question too easy.")

TYPES = ["DIRECT-FACTUAL", "NOT-NEGATIVE", "MULTI-STATEMENT"]

def plan_types(total):
    """60/20/20 split across `total` MCQs."""
    n_df = round(total * 0.60)
    n_nn = round(total * 0.20)
    n_ms = total - n_df - n_nn
    seq = (["DIRECT-FACTUAL"] * n_df) + (["NOT-NEGATIVE"] * n_nn) + (["MULTI-STATEMENT"] * n_ms)
    return seq or ["DIRECT-FACTUAL"] * total

def richness(item):
    """1 or 2 MCQs per item based on how much testable content it has."""
    bl = item.get("bullets") or []
    body = len((item.get("context") or "")) + sum(len(b) for b in bl)
    return 2 if (len(bl) >= 4 and body > 250) else 1

def gen_mcq(item, qtype, avoid=None):
    facts = "\n".join(f"- {b}" for b in (item.get("bullets") or []))
    avoid_txt = ""
    if avoid:
        avoid_txt = ("\nDo NOT repeat or paraphrase these already-asked questions; "
                     "test a DIFFERENT fact:\n" + "\n".join(f"- {q}" for q in avoid) + "\n")
    user = (f"CA item:\nTitle: {item.get('title')}\nSummary: {item.get('summary')}\n"
            f"Context: {item.get('context')}\nFacts:\n{facts}\n{avoid_txt}\n"
            f"Make ONE {qtype} question. Return ONLY JSON:\n"
            '{"question":"...","options":{"a":"...","b":"...","c":"...","d":"..."},'
            '"correct":"a|b|c|d","explanation":"max 2 lines","difficulty":1}')
    data, _ = C.claude_json(SYS, user, max_tokens=900)
    return data

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

    # plan counts
    per = [(it, richness(it)) for it in items]
    total = sum(n for _, n in per)
    total = max(min(total, 8), 1)             # cap 8; never pad beyond items' richness
    type_seq = plan_types(total)
    C.log(f"   Generating {total} MCQs · type plan: "
          f"{ {t: type_seq.count(t) for t in TYPES} }")

    C.sb_delete("daily_mcqs", {"date": ds})   # idempotent re-run for this date

    # next q_no base (avoid clashing with other dates is fine; q_no is per-date)
    rows, ti, q_no = [], 0, 1
    for it, n in per:
        asked = []
        for _ in range(n):
            if ti >= total:
                break
            qtype = type_seq[ti]; ti += 1
            try:
                m = gen_mcq(it, qtype, avoid=asked)
                asked.append(m.get("question"))
            except Exception as e:
                C.log(f"   ⚠ MCQ gen failed for item {it['id']} ({qtype}): {e}")
                continue
            opts = m.get("options", {})
            correct = (m.get("correct") or "a").strip().lower()[:1]
            diff = int(m.get("difficulty") or 2)
            diff = min(3, max(1, diff))
            rows.append({
                "date": ds, "q_no": q_no,
                "question": m.get("question"),
                "option_a": opts.get("a"), "option_b": opts.get("b"),
                "option_c": opts.get("c"), "option_d": opts.get("d"),
                "correct": correct,                 # live-site column (lowercase letter)
                "correct_option": correct,          # spec column
                "explanation": m.get("explanation"),
                "question_type": qtype,
                "category": it.get("category"),
                "subject": it.get("category"),      # live-site column
                "difficulty": diff,
                "source_item_id": it["id"],
            })
            C.log(f"      Q{q_no} [{qtype} · d{diff}] {it['category'][:28]:28s} "
                  f"correct={correct} :: {str(m.get('question'))[:60]}")
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
    sys.exit(0 if main(d) else 1)
