#!/usr/bin/env python3
"""
ONE-TIME (re-runnable) populator for topic_intelligence — the RPSC "exam brain".

  python3 pipelines/populate_topic_intelligence.py [--all]

Reads every row in `questions`, groups by subject+topic, and for each topic computes
pyq_count / last_asked_year / frequency (≥5 HIGH, ≥2 MEDIUM, 1 LOW) / trend (2021-24 vs
2015-20). For topics with ≥2 PYQs it asks Haiku to extract what RPSC tests / never tests
/ typical question types / capture keywords from the actual question texts; single-PYQ
(LOW) topics get a lightweight default (keywords from the topic name). Pass --all to run
Haiku on every topic including single-PYQ ones. Then seeds topic_coverage from it and
adds 12 manual HIGH-priority topics. Idempotent: upserts on `topic`.
"""
import sys, json, re, pathlib
from collections import Counter
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from pipelines import _common as C

RECENT = range(2021, 2025)   # 2021-2024
OLD    = range(2015, 2021)   # 2015-2020
_STOP = {"the","and","for","of","in","to","a","an","etc","other","its","by","on","with"}

EXTRACT_SYS = (
    "You analyse RPSC RAS (Rajasthan) past-exam questions for ONE topic and extract the "
    "exam pattern. Return ONLY JSON with keys: what_rpsc_tests (list of specific fact "
    "TYPES the exam asks, e.g. 'launch year','implementing ministry','coverage amount'), "
    "what_rpsc_never_tests (list of fact types the exam avoids), typical_question_types "
    "(subset of ['direct-factual','negative','multi-statement']), capture_keywords "
    "(5-10 lowercase keywords/phrases to spot this topic in news). Be specific, not broad. "
    "No text outside the JSON object."
)


def _kw_from_name(*parts):
    toks = re.findall(r"[a-z0-9][a-z0-9\-]{2,}", " ".join(p or "" for p in parts).lower())
    out = []
    for t in toks:
        if t not in _STOP and t not in out:
            out.append(t)
    return out[:10]


def _freq(n):
    return "HIGH" if n >= 5 else "MEDIUM" if n >= 2 else "LOW"


def _trend(years):
    recent = sum(1 for y in years if y in RECENT)
    old = sum(1 for y in years if y in OLD)
    return "RISING" if recent > old else "DECLINING" if recent < old else "STABLE"


def _haiku_extract(topic, subject, qs):
    """Extract the exam pattern for a topic from up to 12 of its question texts."""
    sample = "\n".join(f"- {(' '.join((q.get('question') or '').split()))[:240]}"
                       for q in qs[:12] if q.get("question"))
    user = (f"Topic: {topic}\nSubject: {subject}\nRPSC questions asked on this topic:\n{sample}\n\n"
            "Return the JSON.")
    try:
        data, _ = C.claude_json(EXTRACT_SYS, user, max_tokens=500,
                                model=C.HAIKU_MODEL, cache_system=True)
        if isinstance(data, dict):
            return data
    except Exception as e:
        C.log(f"   ⚠ extract failed for {topic[:40]!r}: {e}")
    return {}


# 12 manual HIGH-priority topics (present even if PYQ count is low — RPSC 2026 certainties)
MANUAL_HIGH = [
    ("VB-G RAM G / MGNREGS replacement", "National Schemes — Central Government",
     ["mgnregs","nrega","vb-g ram g","rural employment","employment guarantee"],
     ["scheme full name","launch year/Act","implementing ministry","days/wage guarantee"]),
    ("PM-JAY Ayushman Bharat", "National Schemes — Central Government",
     ["pm-jay","ayushman bharat","jan arogya","health insurance","nha"],
     ["coverage amount","implementing agency (NHA)","launch year","beneficiary scope"]),
    ("PMSMA maternal health", "National Schemes — Central Government",
     ["pmsma","surakshit matritva","maternal health","pregnant women","antenatal"],
     ["launch year","ministry","free services count","target beneficiaries"]),
    ("PMMVY maternity benefit", "National Schemes — Central Government",
     ["pmmvy","matru vandana","maternity benefit","cash incentive"],
     ["benefit amount","installments","eligibility","ministry"]),
    ("Tiger Reserves of Rajasthan", "Environment & Ecology",
     ["tiger reserve","ranthambore","sariska","mukundra","ramgarh vishdhari","dholpur karauli"],
     ["reserve name","district/location","designation year","total tiger reserves in India/Rajasthan"]),
    ("Ramsar sites in Rajasthan", "Environment & Ecology",
     ["ramsar","wetland","keoladeo","sambhar lake"],
     ["site name","district","designation year","total Ramsar sites in India"]),
    ("RBI repo rate and monetary policy", "Indian Economy",
     ["rbi","repo rate","reverse repo","monetary policy","mpc","crr","slr"],
     ["current rate value","what changed","MPC full form","RBI Governor"]),
    ("Constitutional bodies (CAG, EC, NHRC, UPSC)", "Indian Polity & Constitution",
     ["cag","election commission","nhrc","upsc","constitutional body","finance commission"],
     ["the Article that creates it","appointing authority","tenure","key power/function"]),
    ("Rajasthan rivers, lakes and dams", "Rajasthan Geography",
     ["chambal","banas","luni","jakham","bisalpur","mahi bajaj sagar","rana pratap sagar"],
     ["river/dam name","district","tributary/origin","which river it is on"]),
    ("Rajasthan state schemes", "State Schemes — Rajasthan",
     ["mukhyamantri","rajasthan scheme","indira gandhi","chiranjeevi","jan aadhaar"],
     ["scheme full name","department","benefit/target","launch year"]),
    ("ISRO missions 2025-2026", "Science & Technology",
     ["isro","chandrayaan","gaganyaan","aditya","spadex","nvs","satellite launch"],
     ["mission name","launch vehicle","objective","first/record"]),
    ("DRDO defence systems", "Science & Technology",
     ["drdo","missile","agni","akash","tejas","brahmos","defence system"],
     ["system name","range/capacity","indigenous","first/strategic"]),
]


def main(run_all=False):
    C.log("=" * 64); C.log("POPULATE topic_intelligence"); C.log("=" * 64)
    rows = C.sb_select("questions", select=(
        "year,subject,topic,question,option_1,option_2,option_3,option_4,correct_text,correct_ans"),
        params={"limit": "2000"})
    C.log(f"   {len(rows)} questions fetched")

    # Group by TOPIC alone (topic is the UNIQUE key). A topic that spans multiple
    # subjects is merged; its subject = the most common one among its questions.
    groups = {}
    for r in rows:
        topic = r.get("topic") or ""
        if topic:
            groups.setdefault(topic, []).append(r)
    C.log(f"   {len(groups)} distinct topics")

    out, n_haiku = [], 0
    for topic, qs in groups.items():
        subject = Counter((q.get("subject") or "") for q in qs).most_common(1)[0][0]
        years = [q.get("year") for q in qs if q.get("year")]
        n = len(qs)
        intel = {"topic": topic, "subject": subject,
                 "rpsc_frequency": _freq(n), "pyq_count": n,
                 "last_asked_year": max(years) if years else None,
                 "frequency_trend": _trend(years)}
        # jsonb columns take raw Python lists (sb_upsert json-encodes the whole body;
        # pre-dumping here would store a JSON *string* scalar, not an array).
        if n >= 2 or run_all:
            ex = _haiku_extract(topic, subject, qs); n_haiku += 1
            intel["what_rpsc_tests"] = ex.get("what_rpsc_tests") or []
            intel["what_rpsc_never_tests"] = ex.get("what_rpsc_never_tests") or []
            intel["typical_question_types"] = ex.get("typical_question_types") or []
            intel["capture_keywords"] = ex.get("capture_keywords") or _kw_from_name(topic)
        else:  # single-PYQ LOW: lightweight default, no Haiku
            intel["what_rpsc_tests"] = []
            intel["what_rpsc_never_tests"] = []
            intel["typical_question_types"] = []
            intel["capture_keywords"] = _kw_from_name(topic)
        out.append(intel)

    C.sb_upsert("topic_intelligence", out, on_conflict="topic")
    C.log(f"   ✓ upserted {len(out)} topics ({n_haiku} via Haiku, {len(out)-n_haiku} default)")

    # Manual HIGH-priority topics (insert if absent; do not clobber Haiku-derived rows)
    existing = {r["topic"] for r in C.sb_select("topic_intelligence", select="topic", params={"limit": "2000"})}
    manual = []
    for topic, subject, kws, tests in MANUAL_HIGH:
        if topic in existing:
            continue
        manual.append({"topic": topic, "subject": subject, "rpsc_frequency": "HIGH",
                       "pyq_count": 0, "frequency_trend": "RISING",
                       "what_rpsc_tests": tests,
                       "what_rpsc_never_tests": [],
                       "typical_question_types": ["direct-factual"],
                       "capture_keywords": kws})
    if manual:
        C.sb_upsert("topic_intelligence", manual, on_conflict="topic")
    C.log(f"   ✓ {len(manual)} manual HIGH-priority topics added")

    C.log(f"\n✓ topic_intelligence populated. Now seed topic_coverage separately.")
    return len(out) + len(manual)


if __name__ == "__main__":
    main(run_all="--all" in sys.argv)
