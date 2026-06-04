#!/usr/bin/env python3
"""
Re-embed the prelims_questions ChromaDB collection IN PLACE with topic-tagged text.

Why in place (upsert) rather than a full build_chroma rebuild:
  Railway uses the *committed* chroma_db (the tracked segment .bin files + the
  skip-worktree'd chroma.sqlite3). A full rebuild makes NEW collection-UUID dirs
  which are gitignored, orphaning the committed copy. Upserting the existing
  collection keeps the same UUID dir, so the tracked files update in place and
  stay committable.

What it does:
  1. Read all PYQs from Supabase (year, q_no, question, subject, chapter, topic).
  2. Build embedding text = "{chapter} | {topic} | {question}" (see
     build_chroma.question_embed_text).
  3. Upsert new embeddings + documents into the existing prelims_questions
     collection, keyed by the same ids (Q{year}_{q_no}). Existing metadata
     (year, q_no, topic, never_skipped, …) is preserved untouched.

Run:
  python3 rag/reembed_pyqs.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chromadb
from sentence_transformers import SentenceTransformer
from build_chroma import (
    EMBED_MODEL, CHROMA_PATH, BATCH, fetch_all, embed_texts, chunks,
    question_embed_text,
)

COLLECTION = "prelims_questions"


def main():
    print("=" * 60)
    print("PaperSe — re-embed prelims_questions with topic tags")
    print("=" * 60)

    # 1. Existing collection (keep same UUID dir → tracked files update in place)
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    names = [c.name for c in client.list_collections()]
    if COLLECTION not in names:
        print(f"✗ collection '{COLLECTION}' not found. Have: {names}")
        sys.exit(1)
    col = client.get_collection(COLLECTION)
    before = col.count()
    print(f"✓ opened '{COLLECTION}' ({before} docs)")

    # Existing ids + metadata (preserve metadata exactly)
    existing = col.get(include=["metadatas"])
    meta_by_id = dict(zip(existing["ids"], existing["metadatas"]))
    print(f"✓ loaded {len(meta_by_id)} existing ids/metadata")

    # 2. Fetch PYQs from Supabase and build new embedding text
    questions = fetch_all("questions", select="year,q_no,question,subject,chapter,topic")
    print(f"✓ fetched {len(questions)} questions from Supabase")

    ids, docs, metas = [], [], []
    skipped_blank, skipped_unknown = 0, 0
    for q in questions:
        text = (q.get("question") or "").strip()
        if not text:
            skipped_blank += 1
            continue
        doc_id = f"Q{q['year']}_{str(q['q_no']).zfill(3)}"
        if doc_id not in meta_by_id:
            skipped_unknown += 1
            continue
        ids.append(doc_id)
        docs.append(question_embed_text(q.get("chapter"), q.get("topic"), text, q.get("subject")))
        metas.append(meta_by_id[doc_id])   # preserve existing metadata

    print(f"  to re-embed: {len(ids)}  (blank: {skipped_blank}, not-in-collection: {skipped_unknown})")

    # 3. Embed and upsert in batches
    print(f"\nLoading embed model: {EMBED_MODEL} ...")
    model = SentenceTransformer(EMBED_MODEL)

    done = 0
    for id_b, doc_b, meta_b in zip(chunks(ids, BATCH), chunks(docs, BATCH), chunks(metas, BATCH)):
        vectors = embed_texts(model, doc_b)
        col.upsert(ids=id_b, embeddings=vectors, documents=doc_b, metadatas=meta_b)
        done += len(id_b)
        print(f"  upserted {done}/{len(ids)}", end="\r")

    print(f"\n✓ re-embedded {done} PYQs in place. Collection now: {col.count()} docs")

    # 4. Sanity sample — show the new embedded text for the Arab-traveller Q
    sample = col.get(ids=["Q2023_122"], include=["documents"])
    if sample["ids"]:
        print("\nExample new embedding text:")
        print(f"  {sample['documents'][0][:140]}")


if __name__ == "__main__":
    main()
