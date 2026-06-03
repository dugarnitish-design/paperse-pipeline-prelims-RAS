#!/usr/bin/env python3
"""
Load ca_intelligence_layer.json into Supabase bridge_map table.
Maps 13 ca_categories (+ 10 static_ca_bridge_map entries) into bridge_map rows.
"""
import json, os, sys, requests

SUPABASE_URL = "https://nunbpwaxqqgfxrosqfhw.supabase.co"
SERVICE_KEY  = "sb_secret_4DHbpgTolrECyhonQUDFRQ_H7vB513d"

CA_FILE = "/Users/nitishdugar/Downloads/ca_intelligence_layer.json"

# ── topic_id lookup: (topic_name, subject) → topic_id
# Key = (static_topic_link, static_subject) from ca_intelligence_layer.json
# Derived from full topic_kb SELECT (289 rows). Prefer static PYQ subject over CA_* when choice exists.
TOPIC_ID_MAP = {
    # (static_topic_link, static_subject) → topic_id
    ("National Sports & Awards",                        "Current Affairs"):                        "CA_004",
    ("Books, Awards & Personalities",                   "Current Affairs"):                        "CA_006",
    ("International Politics & Elections",              "Current Affairs"):                        "CA_002",
    ("International Organisations & Reports",           "Current Affairs"):                        "CA_021",
    ("National Politics & Governance",                  "Current Affairs"):                        "CA_014",
    ("Rajasthan Sports & Awards",                       "Current Affairs"):                        "CA_012",
    ("Computer & Information Technology",               "Science & Technology"):                   "SCI_012",
    ("Rajasthani Literature & Folk Literature",         "Rajasthan History & Culture"):            "RHC_004",
    ("Constitutional Amendments",                       "Indian Polity & Constitution"):           "POL_002",
    ("Women, Child, SC/ST & Minority Welfare Schemes",  "State Schemes & Rajasthan"):              "RSCH_005",
    ("National Income & GDP",                           "Indian Economy"):                         "ECO_004",
    ("State Human Rights Commission",                   "Rajasthan Polity & Administration"):      "RPOL_003",
    # Bridge map specific static links
    ("Defence Equipment, Weapons & DRDO",               "Science & Technology"):                   "SCI_007",
    ("Wildlife Sanctuaries & National Parks",           "Rajasthan Geography"):                    "RGEO_014",
    ("Energy Sector — Solar & Renewable",               "Rajasthan Economy"):                      "RECO_004",
    ("Agriculture — Crops & Production",                "Rajasthan Economy"):                      "RECO_001",
    ("Monetary Policy & RBI",                           "Indian Economy"):                         "ECO_005",
    ("Framing of Constitution & Preamble",              "Indian Polity & Constitution"):           "POL_003",
}

def get_topic_id(topic_link, subject):
    """Look up topic_id using (static_topic_link, static_subject) as composite key."""
    if not topic_link:
        return None
    key = (topic_link, subject or "")
    if key in TOPIC_ID_MAP:
        return TOPIC_ID_MAP[key]
    # Fallback: topic name only
    for (k_topic, k_subj), v in TOPIC_ID_MAP.items():
        if k_topic.lower() == topic_link.lower():
            return v
    return None

def insert_rows(rows):
    url = f"{SUPABASE_URL}/rest/v1/bridge_map"
    headers = {
        "apikey": SERVICE_KEY,
        "Authorization": f"Bearer {SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    resp = requests.post(url, json=rows, headers=headers)
    if resp.status_code not in (200, 201):
        print(f"  ✗ Insert failed: {resp.status_code} — {resp.text[:300]}")
        return 0
    return len(resp.json()) if resp.json() else len(rows)

def main():
    with open(CA_FILE) as f:
        ca = json.load(f)

    cats    = ca.get("ca_categories", [])
    bridges = ca.get("static_ca_bridge_map", [])

    # Build ca_category → bridge_entry lookup
    bridge_by_cat = {b["ca_category"]: b for b in bridges}

    rows = []

    # ── 13 ca_categories → bridge_map rows ─────────────────────────────────────
    for i, cat in enumerate(cats, 1):
        cat_name   = cat["category"]
        bridge_ref = bridge_by_cat.get(cat_name, {})
        topic_link = cat.get("static_topic_link", cat_name)
        topic_id   = get_topic_id(topic_link, cat.get("static_subject"))

        # ca_hook_description: prefer fires_when from bridge, else join what_to_capture
        if bridge_ref.get("fires_when"):
            hook_desc = bridge_ref["fires_when"]
        else:
            wc = cat.get("what_to_capture", [])
            hook_desc = "; ".join(wc) if isinstance(wc, list) else str(wc)

        # news_sources: combine primary + secondary
        sources = list(cat.get("source_primary", []))
        sources += list(cat.get("source_secondary", []))

        # rpsc_conversion_pattern: bridge_strength if available, else question type
        if bridge_ref.get("bridge_strength"):
            pattern = bridge_ref["bridge_strength"]
        else:
            pattern = cat.get("question_type_likely", "DIRECT-FACTUAL")

        rows.append({
            "bridge_id":                f"CA_{i:03d}",
            "topic_id":                 topic_id,
            "subject":                  cat.get("static_subject"),
            "chapter":                  None,           # will be resolved via topic_id join at runtime
            "ca_hook_category":         cat_name,
            "ca_hook_description":      hook_desc,
            "news_sources":             sources,
            "rpsc_conversion_pattern":  pattern,
            "typical_qtype":            cat.get("question_type_likely"),
            "exam_probability":         cat.get("2026_probability"),
            "rajasthan_specific":       bool(cat.get("rajasthan_angle", False)),
            "rajasthan_filter":         cat.get("rajasthan_note"),
            "times_fired":              0,
            "times_predicted_correct":  0,
            "precision_score":          0.0,
        })

    # ── 10 static_ca_bridge_map entries not already covered ─────────────────────
    ca_cat_names = {cat["category"] for cat in cats}
    extra_idx = 1
    for b in bridges:
        if b["ca_category"] in ca_cat_names:
            continue    # already handled via ca_categories above
        topic_id = get_topic_id(b.get("static_topic", ""), b.get("static_subject", ""))
        rows.append({
            "bridge_id":                f"BR_{extra_idx:03d}",
            "topic_id":                 topic_id,
            "subject":                  b.get("static_subject"),
            "chapter":                  None,
            "ca_hook_category":         b["ca_category"],
            "ca_hook_description":      b.get("fires_when", ""),
            "news_sources":             [],
            "rpsc_conversion_pattern":  b.get("bridge_strength", ""),
            "typical_qtype":            None,
            "exam_probability":         None,
            "rajasthan_specific":       False,
            "rajasthan_filter":         None,
            "times_fired":              0,
            "times_predicted_correct":  0,
            "precision_score":          0.0,
        })
        extra_idx += 1

    print(f"Total rows to insert: {len(rows)}")
    for r in rows:
        print(f"  {r['bridge_id']:8s}  {r['ca_hook_category'][:40]:40s}  topic_id={r['topic_id']}")

    print("\nInserting into Supabase bridge_map...")
    inserted = insert_rows(rows)
    print(f"✓ Inserted {inserted} rows")
    return inserted

if __name__ == "__main__":
    main()
