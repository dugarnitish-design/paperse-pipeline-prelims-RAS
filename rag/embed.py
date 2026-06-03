"""
PaperSe — embed helper for the RAG.
Reads a JSON array of strings on stdin, prints a JSON array of 384-dim vectors.
Uses the SAME model the KB was embedded with, so cosine similarity is comparable.

Invoked from Node (processors/claude.js) via the project venv:
    echo '["text one","text two"]' | .rag_venv/bin/python rag/embed.py
"""
import sys, json
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")  # 384-dim

texts = json.load(sys.stdin)
if isinstance(texts, str):
    texts = [texts]
vectors = model.encode(texts).tolist()
print(json.dumps(vectors))
