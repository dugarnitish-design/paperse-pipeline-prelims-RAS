#!/usr/bin/env python3
"""
STEP 6 — FLT (Full-Length Test) builder. MANUAL trigger. Available after 90 days of MCQs.

  python3 pipelines/flt_builder.py <student_id> [ref_date]

Pulls 30 MCQs honouring (as far as the available pool allows):
  Date    : last30=15, 31-60d=10, 61-90d=5
  Topic   : Nat Sports 5, Books&Pers 4, Intl Politics 3, Intl Orgs 2, Nat Politics 2,
            Rajasthan 8, Others 6
  Diff    : easy(1)=10, medium(2)=15, hard(3)=5
  Q-type  : Direct-Factual 18, Not/Negative 6, Multi-Statement 6
Never repeats an MCQ the student already attempted. Stores in flt_tests.

With <90 days of data the quotas can't be met; the builder fills what it can and
logs every shortfall (it does NOT pad with out-of-spec questions silently).
"""
import sys, datetime
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from pipelines import _common as C

TARGET = 30
DATE_QUOTA = {"last30": 15, "31-60": 10, "61-90": 5}
TOPIC_QUOTA = {"National Sports & Awards": 5, "Books, Awards & Personalities": 4,
               "International Politics & Elections": 3, "International Organisations & Reports": 2,
               "National Politics & Governance": 2, "__rajasthan__": 8, "__others__": 6}
DIFF_QUOTA = {1: 10, 2: 15, 3: 5}
TYPE_QUOTA = {"DIRECT-FACTUAL": 18, "NOT-NEGATIVE": 6, "MULTI-STATEMENT": 6}

def date_bucket(d, ref):
    days = (ref - datetime.date.fromisoformat(d)).days
    if 0 <= days <= 30:  return "last30"
    if 31 <= days <= 60: return "31-60"
    if 61 <= days <= 90: return "61-90"
    return None

def topic_bucket(cat):
    cat = cat or ""
    if cat in TOPIC_QUOTA:
        return cat
    if "rajasthan" in cat.lower():
        return "__rajasthan__"
    return "__others__"

def build_flt(student_id, ref):
    student_id = str(student_id)
    # candidate pool: all MCQs with the metadata we need
    pool = C.sb_select("daily_mcqs", params={
        "select": "id,date,category,difficulty,question_type", "order": "date.desc"})
    # exclude already-attempted by this student
    done = C.sb_select("student_attempts", params={
        "student_id": f"eq.{student_id}", "select": "mcq_id"})
    done_ids = {d["mcq_id"] for d in done}
    cand = [m for m in pool if m["id"] not in done_ids
            and date_bucket(m.get("date"), ref) is not None]

    # data-age check
    all_dates = [m["date"] for m in pool if m.get("date")]
    span = (ref - datetime.date.fromisoformat(min(all_dates))).days if all_dates else 0
    C.log(f"   candidate pool: {len(cand)} (of {len(pool)} total MCQs; "
          f"{len(done_ids)} already attempted) · history span ≈ {span} days")
    if span < 90:
        C.log(f"   ⚠ FLT is meant to run after 90 days of MCQs; only ~{span} days exist. "
              f"Building a best-effort test from the available pool.")

    # greedy fill honouring quotas — score each candidate by how many still-needed quotas it fills
    need_date = dict(DATE_QUOTA); need_topic = dict(TOPIC_QUOTA)
    need_diff = dict(DIFF_QUOTA); need_type = dict(TYPE_QUOTA)
    chosen = []
    def fills(m):
        s = 0
        if need_date.get(date_bucket(m["date"], ref), 0) > 0: s += 1
        if need_topic.get(topic_bucket(m.get("category")), 0) > 0: s += 1
        if need_diff.get(m.get("difficulty"), 0) > 0: s += 1
        if need_type.get(m.get("question_type"), 0) > 0: s += 1
        return s
    remaining = cand[:]
    while len(chosen) < TARGET and remaining:
        remaining.sort(key=fills, reverse=True)
        m = remaining.pop(0)
        if fills(m) == 0 and len(chosen) >= 0 and all(
                v <= 0 for v in list(need_date.values()) + list(need_topic.values())
                + list(need_diff.values()) + list(need_type.values())):
            break  # all quotas satisfied
        chosen.append(m)
        for d, k in ((need_date, date_bucket(m["date"], ref)),
                     (need_topic, topic_bucket(m.get("category"))),
                     (need_diff, m.get("difficulty")),
                     (need_type, m.get("question_type"))):
            if d.get(k, 0) > 0:
                d[k] -= 1

    # report
    def report(name, quota, need):
        got = {k: quota[k] - need.get(k, quota[k]) for k in quota}
        line = ", ".join(f"{k}:{got[k]}/{quota[k]}" for k in quota)
        short = {k: quota[k] - got[k] for k in quota if got[k] < quota[k]}
        C.log(f"   {name:7s} {line}" + (f"   ⚠ short: {short}" if short else "   ✓"))
    C.log(f"\n   selected {len(chosen)}/{TARGET} MCQs")
    report("date", DATE_QUOTA, need_date)
    report("topic", TOPIC_QUOTA, need_topic)
    report("diff", DIFF_QUOTA, need_diff)
    report("type", TYPE_QUOTA, need_type)

    if not chosen:
        C.log("   ✗ no eligible MCQs to build an FLT.")
        return None
    rec = C.sb_insert("flt_tests", {
        "student_id": student_id, "mcq_ids": [m["id"] for m in chosen],
        "status": "created" if len(chosen) == TARGET else "partial",
    })
    flt_id = rec[0]["id"]
    C.log(f"\n✓ STEP 6 complete — flt_tests row id={flt_id}, "
          f"{len(chosen)} MCQs, status={'created' if len(chosen)==TARGET else 'partial'}")
    return flt_id

if __name__ == "__main__":
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        # self-test: make a fresh student so nothing is excluded
        import uuid
        sid = str(uuid.uuid4())
        print(f"(no student_id given — using synthetic {sid[:8]}…)")
    else:
        sid = sys.argv[1]
    ref = C.parse_date(sys.argv[2]) if len(sys.argv) > 2 else datetime.date.today()
    C.log("=" * 64); C.log(f"STEP 6 — FLT Builder — ref {ref.isoformat()}"); C.log("=" * 64)
    sys.exit(0 if build_flt(sid, ref) else 1)
