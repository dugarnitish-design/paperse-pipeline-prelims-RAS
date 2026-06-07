#!/usr/bin/env python3
"""
RAG Integration: 3-layer intelligence system for Daily CA items.

LAYER 1 — ca_categories (existing)
  Filter news items by RAS prelims relevance categories

LAYER 2 — ChromaDB prelims_questions
  Match news items against 900+ past-year questions
  Boost priority if similar PYQ found (similarity > 0.65)

LAYER 3 — Supabase topic_kb
  Enrich items with topic intelligence:
    - priority_score, frequency, trajectory, never_skipped
    - Boost priority based on topic intelligence
"""
from pathlib import Path
import sys, datetime
# NOTE: chromadb is imported lazily inside get_chroma_client() so this module can
# be imported on the slim Railway image (which has NO torch/chromadb/sentence-
# transformers). On Railway the heavy path never runs — items arrive with PYQ
# matches already computed on the Mac (item["pyq_candidates"]).

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipelines import _common as C


# ─────────────────────────────────────────────────────────────────────────────
# SETUP: ChromaDB Client
# ─────────────────────────────────────────────────────────────────────────────

CHROMA_PATH = Path(__file__).resolve().parent.parent / "chroma_db"

def get_chroma_client():
    """Initialize ChromaDB persistent client (Mac only — chromadb is imported
    lazily here). Returns None if chromadb isn't installed (slim Railway image)
    or the local collection is missing."""
    if not CHROMA_PATH.exists():
        C.log(f"⚠ ChromaDB path not found: {CHROMA_PATH}")
        return None
    try:
        import chromadb
    except ImportError:
        C.log("⚠ chromadb not installed (slim image) — using precomputed PYQ only")
        return None
    return chromadb.PersistentClient(path=str(CHROMA_PATH))


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 2: ChromaDB PYQ Matching
# ─────────────────────────────────────────────────────────────────────────────

def _best_candidate(item, client):
    """Return the single best PYQ candidate dict (highest similarity, best-first)
    for an item, or None.

    Prefers item["pyq_candidates"] — the list precomputed on the Mac night job
    (C.pyq_lookup_many output: each has score/distance/year/q_no/topic/subject).
    Only if that key is ABSENT (never set) does it fall back to a live ChromaDB
    query via `client` (Mac-only). On Railway pyq_candidates is always present
    (it may be an empty list ⇒ no match), so ChromaDB is never touched."""
    cands = item.get("pyq_candidates")
    if cands is not None:
        return cands[0] if cands else None
    # Live fallback (Mac, when not precomputed) ───────────────────────────────
    if not client:
        return None
    try:
        col = client.get_collection(name="prelims_questions")
        search_text = f"{item.get('title', '')} {item.get('summary', '')}".strip()
        if not search_text:
            return None
        res = col.query(query_texts=[search_text], n_results=3,
                        include=["metadatas", "distances"])
        if not res or not res.get("metadatas") or not res["metadatas"][0]:
            return None
        meta = res["metadatas"][0][0]
        dist = res["distances"][0][0]
        return {"score": round(1 - dist, 3), "distance": round(dist, 3),
                "year": meta.get("year"), "topic": meta.get("topic"),
                "subject": meta.get("subject"), "q_no": meta.get("q_no")}
    except Exception as e:
        C.log(f"⚠ ChromaDB query failed: {e}")
        return None


def enrich_with_pyq_matches(item, client, similarity_threshold=0.65):
    """Attach a PYQ-match boost from the best candidate (precomputed on the Mac,
    or a live ChromaDB query as Mac-only fallback).

    Returns item updated with pyq_match_year / pyq_match_topic /
    pyq_similarity_score / pyq_boost.
    """
    best = _best_candidate(item, client)
    similarity = best.get("score", 0) if best else 0

    if best and similarity > similarity_threshold:
        item["pyq_match_year"] = best.get("year")
        item["pyq_match_topic"] = best.get("topic")
        item["pyq_similarity_score"] = round(similarity, 3)
        item["pyq_boost"] = 0.2  # Boost priority by 0.2
        C.log(f"   🎯 PYQ match found: {best.get('year')} "
              f"({item['pyq_match_topic']}) - similarity: {item['pyq_similarity_score']}")
    else:
        item["pyq_boost"] = 0

    return item


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 3: Supabase topic_kb Enrichment
# ─────────────────────────────────────────────────────────────────────────────

def enrich_with_topic_kb(item, ca_category):
    """
    Query topic_kb table for topic intelligence.

    Args:
        item: dict with item data
        ca_category: string, the ca_categories match (e.g., "Wildlife Conservation")

    Returns:
        item dict updated with:
          - topic_kb_priority_score
          - topic_kb_frequency
          - topic_kb_never_skipped
          - topic_kb_trajectory
          - topic_kb_boost
    """
    if not ca_category:
        item["topic_kb_boost"] = 0
        return item

    try:
        # Query topic_kb for matching topic/subject
        # The ca_category from our earlier categorization matches a topic
        results = C.sb_select(
            "topic_kb",
            params={
                "topic": f"ilike.%{ca_category}%"
            }
        )

        if not results or len(results) == 0:
            item["topic_kb_boost"] = 0
            return item

        # Use the first match
        kb = results[0]

        # Extract values
        priority_score = float(kb.get("priority_score") or 0)
        never_skipped = bool(kb.get("never_skipped", False))
        trajectory = kb.get("trajectory", "")

        # Store in item
        item["topic_kb_priority_score"] = priority_score
        item["topic_kb_frequency"] = int(kb.get("frequency") or 0)
        item["topic_kb_never_skipped"] = never_skipped
        item["topic_kb_trajectory"] = trajectory

        # Calculate boost
        boost = (priority_score * 0.3)
        if never_skipped:
            boost += 0.2
        if trajectory == "rising":
            boost += 0.1

        item["topic_kb_boost"] = round(boost, 3)

        C.log(f"   📊 Topic KB enriched: priority={priority_score:.2f}, "
              f"never_skipped={never_skipped}, trajectory={trajectory}")

    except Exception as e:
        C.log(f"   ⚠ Topic KB lookup failed: {e}")
        item["topic_kb_boost"] = 0

    return item


# ─────────────────────────────────────────────────────────────────────────────
# FINAL PRIORITY CALCULATION
# ─────────────────────────────────────────────────────────────────────────────

def calculate_final_priority(item):
    """
    Calculate final priority score combining all layers.

    final_score =
        base priority (from run_filters — already includes tier + rajasthan + PYQ + quality penalty)
        + pyq_boost (from ChromaDB re-check in enrich_with_pyq_matches)
        + topic_kb_boost (from topic_kb enrichment)

    Using the run_filters priority as base ensures the text-quality penalty
    (applied in run_filters via _text_quality) flows through to the final score.

    Returns:
        item dict with 'final_priority_score'
    """
    # Priority from run_filters already encodes tier + rajasthan + text-quality penalty
    score = item.get("priority", item.get("ca_tier_score", 0.5))
    score += item.get("pyq_boost", 0)
    score += item.get("topic_kb_boost", 0)
    # FIX 1 — Layer-3 RPSC-relevance verdict drives the final score (so exam-relevance,
    # not just a keyword-category match, differentiates items — esp. uncategorised ones
    # that otherwise tied at 0.2). YES clearly outranks MAYBE.
    score += {"YES": 0.4, "MAYBE": 0.15}.get((item.get("rpsc_verdict") or "").upper(), 0.0)

    item["final_priority_score"] = round(score, 3)
    return item


# ─────────────────────────────────────────────────────────────────────────────
# MAIN: Enrich CA Items with 3-Layer RAG
# ─────────────────────────────────────────────────────────────────────────────

def enrich_ca_items(items, ca_category_map=None):
    """
    Enrich CA items with 3-layer RAG integration.

    Args:
        items: list of dict, each with 'title', 'summary', 'ca_category'
        ca_category_map: dict mapping item index to ca_category string

    Returns:
        items list with enriched priority scores
    """
    C.log("\n" + "=" * 70)
    C.log("RAG INTEGRATION: 3-Layer Intelligence")
    C.log("=" * 70)

    # Initialize ChromaDB client
    client = get_chroma_client()
    if client:
        C.log(f"✓ ChromaDB client initialized at {CHROMA_PATH}")
    else:
        C.log(f"⚠ ChromaDB not available, skipping LAYER 2")

    for i, item in enumerate(items):
        C.log(f"\n[{i+1}/{len(items)}] {item.get('title', 'Untitled')[:60]}")

        # LAYER 2: ChromaDB PYQ matching
        if client:
            item = enrich_with_pyq_matches(item, client)
        else:
            item["pyq_boost"] = 0

        # LAYER 3: Supabase topic_kb enrichment
        ca_cat = ca_category_map.get(i) if ca_category_map else item.get("ca_category")
        item = enrich_with_topic_kb(item, ca_cat)

        # Calculate final priority
        item = calculate_final_priority(item)

        items[i] = item

    # Rank items by final_priority_score
    items.sort(key=lambda x: x.get("final_priority_score", 0), reverse=True)

    C.log("\n" + "=" * 70)
    C.log("RANKING (by final_priority_score):")
    C.log("=" * 70)
    for i, item in enumerate(items, 1):
        score = item.get("final_priority_score", 0)
        tier = "MAIN" if i <= 5 else "ALSO-IN-NEWS" if i <= 10 else "DROPPED"
        C.log(f"  {i:2d}. [{score:.3f}] {tier:13s} | {item.get('title', '')[:55]}")

    return items


# ─────────────────────────────────────────────────────────────────────────────
# HELPER: Extract PDF Tags
# ─────────────────────────────────────────────────────────────────────────────

def get_pdf_tags(item):
    """
    Extract visual tags to display in PDF below item title.

    Returns:
        list of dicts with:
          - text: tag text (e.g., "🎯 RPSC 2023")
          - color: tag color (orange, green, blue)
    """
    tags = []

    # PYQ match tag
    if item.get("pyq_match_year"):
        tags.append({
            "text": f"🎯 RPSC {item['pyq_match_year']}",
            "color": "orange"
        })

    # Never skipped tag
    if item.get("topic_kb_never_skipped"):
        tags.append({
            "text": "⚡ Never skipped",
            "color": "green"
        })

    # Rising trend tag
    if item.get("topic_kb_trajectory") == "rising":
        tags.append({
            "text": "📈 Rising trend",
            "color": "blue"
        })

    return tags


if __name__ == "__main__":
    # Test enrichment
    test_items = [
        {
            "title": "India launches new digital ID system",
            "summary": "Digital identity program launched in multiple states",
            "ca_category": "Technology & Innovation"
        },
        {
            "title": "ISRO launches Chandrayaan-3 lunar mission",
            "summary": "Moon mission achieves soft landing",
            "ca_category": "Space & Science"
        }
    ]

    enriched = enrich_ca_items(test_items)
    print("\nEnriched items:")
    for item in enriched:
        print(f"  {item['title']}: {item['final_priority_score']}")
