#!/usr/bin/env python3
"""
Build ChromaDB collections for PaperSe prelims RAG.

Collections:
  prelims_questions  — 900 English PYQs (from Supabase questions table)
                       metadata: year, subject, chapter, topic, priority_score,
                                 frequency, trajectory, never_skipped, rajasthan_angle,
                                 dominant_qtype, estimated_hours, question_hi (Hindi)
  prelims_topics     — 289 topics (from Supabase topic_kb table)
                       metadata: all topic_kb fields

Embed model: paraphrase-multilingual-MiniLM-L12-v2  (384-dim, multilingual)
"""
import json, os, sys, requests, chromadb
from pathlib import Path
from sentence_transformers import SentenceTransformer

# ── Config ───────────────────────────────────────────────────────────────────
SUPABASE_URL     = "https://nunbpwaxqqgfxrosqfhw.supabase.co"
SERVICE_KEY      = "sb_secret_4DHbpgTolrECyhonQUDFRQ_H7vB513d"
EMBED_MODEL      = "paraphrase-multilingual-MiniLM-L12-v2"
CHROMA_PATH      = Path(__file__).parent.parent / "chroma_db"
PIPELINE_DIR     = Path(__file__).parent.parent
BATCH            = 100   # embed batch size

HINDI_FILES = {
    2015: PIPELINE_DIR / "hindi-2015-extracted.json",
    2016: PIPELINE_DIR / "hindi-2016-extracted.json",
    2018: PIPELINE_DIR / "hindi-2018-extracted.json",
    2021: PIPELINE_DIR / "hindi-2021-extracted.json",
    2023: PIPELINE_DIR / "hindi-2023-extracted.json",
    2024: PIPELINE_DIR / "hindi-2024-extracted.json",
}

# ── Helpers ──────────────────────────────────────────────────────────────────
def sb_get(table, select="*", limit=1000, offset=0):
    """Fetch rows from Supabase REST API (handles pagination)."""
    headers = {
        "apikey": SERVICE_KEY,
        "Authorization": f"Bearer {SERVICE_KEY}",
        "Range": f"{offset}-{offset+limit-1}",
        "Range-Unit": "items",
    }
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        params={"select": select, "limit": limit, "offset": offset},
        headers=headers,
    )
    resp.raise_for_status()
    return resp.json()

def fetch_all(table, select="*"):
    """Fetch all rows with pagination."""
    rows, offset = [], 0
    while True:
        batch = sb_get(table, select=select, limit=1000, offset=offset)
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return rows

def embed_texts(model, texts):
    """Embed a list of texts, returning list of float lists."""
    vectors = model.encode(texts, show_progress_bar=False, batch_size=BATCH)
    return vectors.tolist()

def question_embed_text(chapter, topic, question, subject=""):
    """Embedding text for a PYQ: "{topic} | {subtopic} | {question}".

    The topic tags anchor each question's vector to its syllabus area so a news
    story only matches PYQs from the same domain (raw-question embeddings produced
    false positives — e.g. an Arab-traveller history Q matching a Venezuela story).
    We map the syllabus hierarchy to the requested two-tag format:
        topic    = chapter  (broad area, e.g. "Ancient India")
        subtopic = topic     (specific, e.g. "Post-Mauryan Dynasties")
    Falls back to `subject` if `chapter` is blank; empty parts are dropped."""
    broad    = (chapter or subject or "").strip()
    specific = (topic or "").strip()
    parts = [p for p in (broad, specific, (question or "").strip()) if p]
    return " | ".join(parts)

def chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

# ── Load Hindi question map: (year, q_no_str) → question_hi ─────────────────
def load_hindi_map():
    hi_map = {}
    for year, path in HINDI_FILES.items():
        if not path.exists():
            print(f"  ⚠ Hindi file not found: {path}")
            continue
        with open(path) as f:
            qs = json.load(f)
        for q in qs:
            key = (year, str(q.get("q_no", "")))
            hi_map[key] = q.get("question_hi", "")
    print(f"  Hindi map loaded: {len(hi_map)} entries")
    return hi_map

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("PaperSe ChromaDB Builder")
    print("=" * 60)

    # ── 1. Delete existing chroma_db ─────────────────────────────────────────
    if CHROMA_PATH.exists():
        import shutil
        shutil.rmtree(CHROMA_PATH)
        print(f"✓ Deleted existing chroma_db at {CHROMA_PATH}")
    CHROMA_PATH.mkdir(parents=True)
    print(f"✓ Created fresh chroma_db at {CHROMA_PATH}")

    # ── 2. Load embedding model ───────────────────────────────────────────────
    print(f"\nLoading embed model: {EMBED_MODEL} ...")
    model = SentenceTransformer(EMBED_MODEL)
    print(f"✓ Model loaded (dim={model.get_sentence_embedding_dimension()})")

    # ── 3. Init ChromaDB client ───────────────────────────────────────────────
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))

    # ── 4. Fetch data from Supabase ───────────────────────────────────────────
    print("\nFetching from Supabase...")
    questions = fetch_all("questions",
        select="year,q_no,question,subject,chapter,topic,is_current")
    print(f"  questions: {len(questions)} rows")

    topics = fetch_all("topic_kb",
        select="topic_id,topic,subject,chapter,frequency,trajectory,"
               "never_skipped,priority_score,dominant_qtype,rajasthan_angle,"
               "estimated_hours,how_to_prepare,trap_to_avoid,ca_news_to_watch,"
               "evergreen_flag,week_bucket")
    print(f"  topic_kb:  {len(topics)} rows")

    hindi_map = load_hindi_map()

    # Build topic_id lookup: (topic, subject) → topic metadata
    topic_meta = {}
    for t in topics:
        key = (t["topic"], t["subject"])
        topic_meta[key] = t

    # ── 5. Build prelims_questions collection ─────────────────────────────────
    print("\n── Building prelims_questions ──────────────────────────────")
    col_q = client.create_collection(
        name="prelims_questions",
        metadata={"hnsw:space": "cosine"},
    )

    ids_q, texts_q, metas_q = [], [], []
    no_topic_match = 0

    for q in questions:
        year    = q["year"]
        q_no    = str(q["q_no"])
        text    = (q.get("question") or "").strip()
        subject = q.get("subject") or ""
        chapter = q.get("chapter") or ""
        topic   = q.get("topic")   or ""

        if not text:
            continue

        # Look up enriched metadata from topic_kb
        tm = topic_meta.get((topic, subject)) or topic_meta.get((topic, ""))
        if not tm:
            no_topic_match += 1

        # Hindi text
        question_hi = hindi_map.get((year, q_no), "")

        doc_id = f"Q{year}_{q_no.zfill(3)}"
        ids_q.append(doc_id)
        # Embed with topic context ("{chapter} | {topic} | {question}"), not raw text.
        texts_q.append(question_embed_text(chapter, topic, text, subject))
        metas_q.append({
            "year":           year,
            "q_no":           q_no,
            "subject":        subject,
            "chapter":        chapter,
            "topic":          topic,
            "topic_id":       tm["topic_id"]       if tm else "",
            "priority_score": float(tm["priority_score"] or 0)  if tm else 0.0,
            "frequency":      int(tm["frequency"]   or 0)        if tm else 0,
            "trajectory":     tm["trajectory"]      if tm else "",
            "never_skipped":  bool(tm["never_skipped"])          if tm else False,
            "rajasthan_angle":bool(tm["rajasthan_angle"])        if tm else False,
            "dominant_qtype": tm["dominant_qtype"]  if tm else "",
            "estimated_hours":int(tm["estimated_hours"] or 0)    if tm else 0,
            "week_bucket":    tm["week_bucket"]     if tm else "",
            "is_current":     bool(q.get("is_current", False)),
            "question_hi":    question_hi,
        })

    # Embed in batches and add to collection
    total_q = 0
    for i, (id_batch, text_batch, meta_batch) in enumerate(
        zip(chunks(ids_q, BATCH), chunks(texts_q, BATCH), chunks(metas_q, BATCH))
    ):
        vectors = embed_texts(model, text_batch)
        col_q.add(
            ids=id_batch,
            embeddings=vectors,
            documents=text_batch,
            metadatas=meta_batch,
        )
        total_q += len(id_batch)
        print(f"  Inserted batch {i+1}: {total_q}/{len(ids_q)} questions", end="\r")

    print(f"\n✓ prelims_questions: {total_q} docs inserted  "
          f"(topic-matched: {total_q - no_topic_match}, unmatched: {no_topic_match})")

    # ── 6. Build prelims_topics collection ────────────────────────────────────
    print("\n── Building prelims_topics ─────────────────────────────────")
    col_t = client.create_collection(
        name="prelims_topics",
        metadata={"hnsw:space": "cosine"},
    )

    ids_t, texts_t, metas_t = [], [], []
    for t in topics:
        # Embed a rich description so cosine search returns meaningful topics
        prep   = (t.get("how_to_prepare") or "")[:200]
        trap   = (t.get("trap_to_avoid")  or "")[:150]
        ca_watch = "; ".join(t.get("ca_news_to_watch") or [])[:150]
        embed_text = (
            f"{t['subject']} | {t['chapter']} | {t['topic']}. "
            f"{prep} {trap} {ca_watch}"
        ).strip()

        ids_t.append(t["topic_id"])
        texts_t.append(embed_text)
        metas_t.append({
            "topic_id":       t["topic_id"],
            "topic":          t["topic"],
            "subject":        t["subject"],
            "chapter":        t["chapter"] or "",
            "frequency":      int(t["frequency"] or 0),
            "trajectory":     t["trajectory"] or "",
            "never_skipped":  bool(t["never_skipped"]),
            "priority_score": float(t["priority_score"] or 0),
            "dominant_qtype": t["dominant_qtype"] or "",
            "rajasthan_angle":bool(t["rajasthan_angle"]),
            "estimated_hours":int(t["estimated_hours"] or 0),
            "evergreen_flag": t["evergreen_flag"] or "",
            "week_bucket":    t["week_bucket"] or "",
        })

    total_t = 0
    for i, (id_batch, text_batch, meta_batch) in enumerate(
        zip(chunks(ids_t, BATCH), chunks(texts_t, BATCH), chunks(metas_t, BATCH))
    ):
        vectors = embed_texts(model, text_batch)
        col_t.add(
            ids=id_batch,
            embeddings=vectors,
            documents=text_batch,
            metadatas=meta_batch,
        )
        total_t += len(id_batch)
        print(f"  Inserted batch {i+1}: {total_t}/{len(ids_t)} topics", end="\r")

    print(f"\n✓ prelims_topics: {total_t} docs inserted")

    # ── 7. Summary ────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("ChromaDB Build Complete")
    print(f"  Path:               {CHROMA_PATH}")
    print(f"  prelims_questions:  {col_q.count()} chunks")
    print(f"  prelims_topics:     {col_t.count()} chunks")
    print("=" * 60)

    # ── 8. Quick smoke-test ───────────────────────────────────────────────────
    print("\nSmoke test — querying 'ISRO satellites Rajasthan' in prelims_questions:")
    results = col_q.query(
        query_embeddings=embed_texts(model, ["ISRO satellites Rajasthan"]),
        n_results=3,
        include=["documents", "metadatas", "distances"],
    )
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        score = round(1 - dist, 3)
        print(f"  [{score}] {meta['year']} Q{meta['q_no']} | {meta['topic']} | {meta['subject']}")
        print(f"         {doc[:100]}...")

    print("\nSmoke test — querying 'ISRO satellite mission' in prelims_topics:")
    results2 = col_t.query(
        query_embeddings=embed_texts(model, ["ISRO satellite mission"]),
        n_results=3,
        include=["documents", "metadatas", "distances"],
    )
    for meta, dist in zip(results2["metadatas"][0], results2["distances"][0]):
        score = round(1 - dist, 3)
        print(f"  [{score}] {meta['topic_id']} | {meta['topic']} | {meta['subject']}")

if __name__ == "__main__":
    main()
