#!/usr/bin/env python3
"""
STEP 5 — Student profile updater. Triggered (API) after a student submits a test.

  update_profile(student_id, attempts) where
     attempts = [{"mcq_id": int, "selected_option": "a", "time_taken_seconds": int}, ...]

Per MCQ → check correct_option, insert student_attempts, update topic_scores JSONB,
recompute accuracy_pct / weak_topics (<60%) / strong_topics (>80%), bump last_updated.

CLI self-test (synthetic student on today's MCQs):
  python3 pipelines/profile_updater.py 2026-06-02
"""
import sys, datetime, uuid
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from pipelines import _common as C

WEAK_BELOW, STRONG_ABOVE = 60.0, 80.0

def _now():
    # avoid bare datetime.now() issues; pass through an explicit UTC stamp
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def update_profile(student_id, attempts):
    """Core entrypoint. Returns the recomputed profile dict."""
    student_id = str(student_id)

    # fetch the MCQs being answered
    ids = sorted({int(a["mcq_id"]) for a in attempts})
    mcqs = C.sb_select("daily_mcqs", params={
        "id": f"in.({','.join(map(str, ids))})",
        "select": "id,correct_option,correct,category,date"}) if ids else []
    mcq_by_id = {m["id"]: m for m in mcqs}

    # load or seed profile
    rows = C.sb_select("student_profile", params={"student_id": f"eq.{student_id}", "limit": 1})
    prof = rows[0] if rows else None
    topic_scores = dict(prof["topic_scores"]) if prof and prof.get("topic_scores") else {}

    graded = []
    for a in attempts:
        m = mcq_by_id.get(int(a["mcq_id"]))
        if not m:
            C.log(f"   ⚠ mcq_id {a['mcq_id']} not found; skipping")
            continue
        correct_opt = (m.get("correct_option") or m.get("correct") or "").strip().lower()[:1]
        sel = (a.get("selected_option") or "").strip().lower()[:1]
        is_correct = bool(sel) and sel == correct_opt
        cat = m.get("category") or "General"

        # insert attempt
        C.sb_insert("student_attempts", {
            "student_id": student_id, "mcq_id": m["id"], "date": m.get("date"),
            "selected_option": sel, "is_correct": is_correct,
            "time_taken_seconds": int(a.get("time_taken_seconds") or 0),
        }, returning=False)

        # update topic bucket
        b = topic_scores.setdefault(cat, {"attempted": 0, "correct": 0})
        b["attempted"] += 1
        if is_correct:
            b["correct"] += 1
        graded.append({"mcq_id": m["id"], "category": cat, "is_correct": is_correct,
                       "correct_option": correct_opt, "selected_option": sel})

    # recompute aggregates from topic_scores (authoritative)
    total_att = sum(v["attempted"] for v in topic_scores.values())
    total_cor = sum(v["correct"] for v in topic_scores.values())
    acc = round(100.0 * total_cor / total_att, 2) if total_att else 0.0
    weak, strong = [], []
    for cat, v in topic_scores.items():
        if v["attempted"] == 0:
            continue
        a_pct = 100.0 * v["correct"] / v["attempted"]
        if a_pct < WEAK_BELOW:
            weak.append(cat)
        elif a_pct > STRONG_ABOVE:
            strong.append(cat)

    payload = {
        "student_id": student_id, "topic_scores": topic_scores,
        "total_attempted": total_att, "total_correct": total_cor,
        "accuracy_pct": acc, "weak_topics": weak, "strong_topics": strong,
        "last_updated": _now(),
    }
    C.sb_upsert("student_profile", payload, on_conflict="student_id")
    return {"profile": payload, "graded": graded}


# ── CLI self-test ─────────────────────────────────────────────────────────────
def _selftest(date):
    ds = date.isoformat()
    C.log("=" * 64)
    C.log(f"STEP 5 — Profile Updater (self-test) — {ds}")
    C.log("=" * 64)
    mcqs = C.sb_select("daily_mcqs", params={"date": f"eq.{ds}", "order": "q_no",
                                             "select": "id,correct_option,category,q_no"})
    if not mcqs:
        C.log("✗ No MCQs for this date; run mcq_generator.py first.")
        return None
    sid = str(uuid.uuid4())
    # simulate: get first one wrong, rest correct
    attempts = []
    for i, m in enumerate(mcqs):
        right = m["correct_option"]
        wrong = next((o for o in "abcd" if o != right), "a")
        attempts.append({"mcq_id": m["id"],
                         "selected_option": wrong if i == 0 else right,
                         "time_taken_seconds": 40 + i * 5})
    C.log(f"   synthetic student {sid[:8]}… submitting {len(attempts)} answers "
          f"(1 wrong, {len(attempts)-1} correct)")
    out = update_profile(sid, attempts)
    p = out["profile"]
    C.log(f"\n   total_attempted = {p['total_attempted']} | total_correct = {p['total_correct']}")
    C.log(f"   accuracy_pct    = {p['accuracy_pct']}%")
    C.log(f"   topic_scores    = {p['topic_scores']}")
    C.log(f"   weak_topics     = {p['weak_topics']}")
    C.log(f"   strong_topics   = {p['strong_topics']}")
    C.log(f"\n✓ STEP 5 complete — student_profile upserted, "
          f"{C.sb_count('student_attempts', {'student_id': f'eq.{sid}'})} attempts recorded")
    return sid

if __name__ == "__main__":
    d = C.parse_date(sys.argv[1]) if len(sys.argv) > 1 else datetime.date.today()
    sys.exit(0 if _selftest(d) else 1)
