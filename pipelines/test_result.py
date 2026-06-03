#!/usr/bin/env python3
"""
STEP 7 — Test result builder (API endpoint payload).

  build_test_result(student_id, attempts, also_update_profile=True) -> dict

Returns the all-at-once result structure (Option A) the portal renders after submit:
  { score, total, accuracy_pct, time_taken, questions[...], summary{...} }

CLI self-test:
  python3 pipelines/test_result.py 2026-06-02
"""
import sys, datetime, uuid
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from pipelines import _common as C
from pipelines.profile_updater import update_profile


def build_test_result(student_id, attempts, also_update_profile=True):
    student_id = str(student_id)
    ids = sorted({int(a["mcq_id"]) for a in attempts})
    mcqs = C.sb_select("daily_mcqs", params={
        "id": f"in.({','.join(map(str, ids))})",
        "select": "id,question,option_a,option_b,option_c,option_d,"
                  "correct_option,correct,explanation,category"}) if ids else []
    by_id = {m["id"]: m for m in mcqs}

    questions, score, total_time = [], 0, 0
    cat_right, cat_wrong = {}, {}
    for a in attempts:
        m = by_id.get(int(a["mcq_id"]))
        if not m:
            continue
        correct = (m.get("correct_option") or m.get("correct") or "").strip().lower()[:1]
        sel = (a.get("selected_option") or "").strip().lower()[:1]
        ok = bool(sel) and sel == correct
        score += 1 if ok else 0
        total_time += int(a.get("time_taken_seconds") or 0)
        cat = m.get("category") or "General"
        (cat_right if ok else cat_wrong).setdefault(cat, 0)
        cat_right[cat] = cat_right.get(cat, 0) + (1 if ok else 0)
        cat_wrong[cat] = cat_wrong.get(cat, 0) + (0 if ok else 1)
        questions.append({
            "question": m.get("question"),
            "options": {"a": m.get("option_a"), "b": m.get("option_b"),
                        "c": m.get("option_c"), "d": m.get("option_d")},
            "student_answer": sel, "correct_answer": correct,
            "is_correct": ok, "explanation": m.get("explanation"),
            "category": cat,
        })

    total = len(questions)
    accuracy = round(100.0 * score / total, 2) if total else 0.0
    mins = max(1, round(total_time / 60)) if total_time else 0

    strong_today = sorted([c for c in cat_right if cat_right[c] > 0 and cat_wrong.get(c, 0) == 0])
    weak_today = sorted([c for c in cat_wrong if cat_wrong[c] > 0])
    if weak_today:
        worst = max(weak_today, key=lambda c: cat_wrong[c])
        recommendation = f"Focus on {worst} this week"
    elif total:
        recommendation = "Strong all-round today — keep up the daily streak"
    else:
        recommendation = "No questions attempted"

    if also_update_profile:
        try:
            update_profile(student_id, attempts)
        except Exception as e:
            C.log(f"   ⚠ profile update failed (result still returned): {e}")

    return {
        "score": score, "total": total, "accuracy_pct": accuracy,
        "time_taken": f"{mins} minutes",
        "questions": questions,
        "summary": {"strong_today": strong_today, "weak_today": weak_today,
                    "recommendation": recommendation},
    }


def _selftest(date):
    import json
    ds = date.isoformat()
    C.log("=" * 64); C.log(f"STEP 7 — Test Result (self-test) — {ds}"); C.log("=" * 64)
    mcqs = C.sb_select("daily_mcqs", params={"date": f"eq.{ds}", "order": "q_no",
                                             "select": "id,correct_option,q_no"})
    if not mcqs:
        C.log("✗ No MCQs for this date."); return None
    sid = str(uuid.uuid4())
    attempts = []
    for i, m in enumerate(mcqs):
        right = m["correct_option"]; wrong = next((o for o in "abcd" if o != right), "a")
        attempts.append({"mcq_id": m["id"],
                         "selected_option": wrong if i == 0 else right,
                         "time_taken_seconds": 45 + i * 8})
    res = build_test_result(sid, attempts, also_update_profile=True)
    # print a trimmed view
    view = dict(res)
    view["questions"] = f"[{len(res['questions'])} question objects]"
    C.log(json.dumps(view, indent=2, ensure_ascii=False))
    C.log("\n   first question object:")
    C.log(json.dumps(res["questions"][0], indent=2, ensure_ascii=False)[:700])
    C.log("\n✓ STEP 7 complete")
    return res

if __name__ == "__main__":
    d = C.parse_date(sys.argv[1]) if len(sys.argv) > 1 else datetime.date.today()
    sys.exit(0 if _selftest(d) else 1)
