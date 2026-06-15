"""
PaperSe Daily CA Pipeline — shared helpers.
Loads env, exposes Supabase REST helpers, Claude client, ChromaDB collection,
and shared config (paths, model, category emojis).

SKIPS everything mains-related.
"""
import os, re, json, datetime, pathlib, functools
import requests

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT        = pathlib.Path(__file__).resolve().parent.parent          # paperse-pipeline/
INPUTS      = ROOT / "inputs"
IE_DIR      = INPUTS / "ie-pdf"
SUJAS_DIR   = INPUTS / "sujas"
UPLOADS     = ROOT / "uploads"
OUT_EN      = ROOT / "outputs" / "daily-ca" / "EN"
OUT_HI      = ROOT / "outputs" / "daily-ca" / "HI"
for _d in (IE_DIR, SUJAS_DIR, UPLOADS, OUT_EN, OUT_HI):
    _d.mkdir(parents=True, exist_ok=True)

# ── Env ───────────────────────────────────────────────────────────────────────
def _load_env():
    """Load env vars from .env file first, then overlay with OS env vars.
    OS env vars (Railway, shell exports) always take priority over .env file.
    This makes the code work both locally (via .env) and on Railway (via OS env).
    """
    env = {}
    # 1. Load from .env file (local dev)
    envpath = ROOT / ".env"
    if envpath.exists():
        for line in envpath.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if v:                       # skip blank placeholders
                env[k] = v
    # 2. Overlay with real OS environment variables (Railway injects these)
    import os
    for k, v in os.environ.items():
        if v:
            env[k] = v
    return env

ENV          = _load_env()
SUPABASE_URL = ENV.get("SUPABASE_URL", "https://nunbpwaxqqgfxrosqfhw.supabase.co").rstrip("/")
SERVICE_KEY  = ENV.get("SUPABASE_SERVICE_KEY")
ANTHROPIC_KEY = ENV.get("ANTHROPIC_API_KEY")

# ── Config ────────────────────────────────────────────────────────────────────
CLAUDE_MODEL     = "claude-sonnet-4-5-20250929"  # Sonnet 4.5 (claude-sonnet-4-20250514 retired 2026-06-15)
HAIKU_MODEL      = "claude-haiku-4-5-20251001"  # cheap/fast — PYQ relevance YES/NO filter
EMBED_MODEL      = "paraphrase-multilingual-MiniLM-L12-v2"
# NOTE: spec says collection 'paperse_prelims_pyq'; the actual built collection
# is 'prelims_questions' (899 PYQs). We use the real one, with fallback.
CHROMA_PATH      = ROOT / "chroma_db"
CHROMA_COLLECTION_CANDIDATES = ["paperse_prelims_pyq", "prelims_questions"]

CATEGORY_EMOJI = {
    "sports": "🏆", "national sports & awards": "🏆", "global sports & awards": "🏆",
    "books": "📖", "books, awards & personalities": "📖", "books & personalities": "📖",
    "national": "🇮🇳", "national politics & governance": "🇮🇳", "bills & legislation": "📜",
    "international": "🌍", "international politics & elections": "🌍",
    "international organisations & reports": "🌍", "global": "🌍",
    "rajasthan": "🗺️", "science": "🔬", "science & technology": "🔬",
    "national science & technology": "🔬",
    "wildlife & environment": "🌿",
    "national schemes & governance": "🏛️",
    "monetary policy & rbi": "🏦",
}

def emoji_for(category: str) -> str:
    if not category:
        return "📰"
    c = category.lower()
    if c in CATEGORY_EMOJI:
        return CATEGORY_EMOJI[c]
    for key, em in CATEGORY_EMOJI.items():
        if key in c:
            return em
    if "rajasthan" in c: return "🗺️"
    if "sport" in c:     return "🏆"
    if "book" in c or "award" in c or "personalit" in c: return "📖"
    if "internationa" in c or "global" in c: return "🌍"
    if "scien" in c or "tech" in c: return "🔬"
    if "bill" in c or "legisl" in c or "act" in c: return "📜"
    return "🇮🇳"

# ── Supabase REST helpers (service role) ──────────────────────────────────────
def _headers(extra=None):
    h = {"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}",
         "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h

def sb_select(table, select="*", params=None):
    p = {"select": select}
    if params:
        p.update(params)
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", params=p, headers=_headers())
    r.raise_for_status()
    return r.json()

def sb_count(table, params=None):
    p = {"select": "*"}
    if params:
        p.update(params)
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", params=p,
                     headers=_headers({"Prefer": "count=exact", "Range": "0-0"}))
    cr = r.headers.get("content-range", "0-0/0")
    return int(cr.split("/")[-1])

def sb_insert(table, rows, returning=True):
    if isinstance(rows, dict):
        rows = [rows]
    prefer = "return=representation" if returning else "return=minimal"
    r = requests.post(f"{SUPABASE_URL}/rest/v1/{table}",
                      headers=_headers({"Prefer": prefer}), data=json.dumps(rows))
    if r.status_code not in (200, 201, 204):
        raise RuntimeError(f"insert {table} failed [{r.status_code}]: {r.text[:300]}")
    return r.json() if (returning and r.text) else None

def sb_upsert(table, rows, on_conflict, returning=False):
    if isinstance(rows, dict):
        rows = [rows]
    prefer = "resolution=merge-duplicates," + ("return=representation" if returning else "return=minimal")
    r = requests.post(f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}",
                      headers=_headers({"Prefer": prefer}), data=json.dumps(rows))
    if r.status_code not in (200, 201, 204):
        raise RuntimeError(f"upsert {table} failed [{r.status_code}]: {r.text[:300]}")
    return r.json() if (returning and r.text) else None

def sb_update(table, patch, match):
    params = {k: f"eq.{v}" for k, v in match.items()}
    r = requests.patch(f"{SUPABASE_URL}/rest/v1/{table}", params=params,
                       headers=_headers({"Prefer": "return=representation"}),
                       data=json.dumps(patch))
    if r.status_code not in (200, 204):
        raise RuntimeError(f"update {table} failed [{r.status_code}]: {r.text[:300]}")
    return r.json() if r.text else None

def sb_delete(table, match):
    params = {k: f"eq.{v}" for k, v in match.items()}
    r = requests.delete(f"{SUPABASE_URL}/rest/v1/{table}", params=params, headers=_headers())
    if r.status_code not in (200, 204):
        raise RuntimeError(f"delete {table} failed [{r.status_code}]: {r.text[:300]}")
    return True

# ── Claude ────────────────────────────────────────────────────────────────────
@functools.lru_cache(maxsize=1)
def claude():
    import anthropic
    return anthropic.Anthropic(api_key=ANTHROPIC_KEY)

def _sys_param(system, cache_system):
    """Build the `system` argument. When cache_system=True, wrap the prompt in a
    content block with cache_control=ephemeral so Anthropic prompt-caching kicks in:
    a system prompt re-sent within 5 min (e.g. Layer-3 across 60-85 calls, gen_main,
    MCQ) is billed at ~10% input cost on cache hits. Pure billing change — same model,
    same prompt, identical output. Below the model's min cacheable length the flag is
    silently ignored (no error)."""
    if cache_system and system:
        return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    return system

# ── self-healing model selection ────────────────────────────────────────────────
# A pinned model id (e.g. claude-sonnet-4-20250514) eventually retires and 404s, which
# previously killed the whole pipeline. If the requested model is unavailable we resolve
# the newest same-family model from the live /v1/models list once, cache it for the
# process, and retry — so a model retirement can NEVER silently break authoring again.
_MODEL_OVERRIDE = {}                       # requested id → resolved replacement (per process)
_FALLBACK_CHAIN = {                        # offline fallback if /v1/models can't be reached
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-5-20250929",
    "opus": "claude-opus-4-5-20251101",
}

def _family(model):
    m = (model or "").lower()
    return "haiku" if "haiku" in m else "opus" if "opus" in m else "sonnet"

def _resolve_fallback(requested):
    """Pick the newest available model in the same family as `requested`."""
    fam = _family(requested)
    try:
        data = requests.get("https://api.anthropic.com/v1/models",
                            headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01"},
                            timeout=20).json().get("data", [])
        ids = [m["id"] for m in data if m.get("id")]     # API returns newest first
        same = [i for i in ids if fam in i.lower()]
        return same[0] if same else (next((i for i in ids if "sonnet" in i.lower()), None) or (ids[0] if ids else None))
    except Exception as e:
        log(f"   ⚠ /v1/models lookup failed ({e}); using offline fallback")
        return _FALLBACK_CHAIN.get(fam)

def _create(model, **kwargs):
    """claude().messages.create with auto-fallback if `model` was retired/renamed (404
    not_found_error). Resolves once per requested id, caches, retries, and logs loudly."""
    use = _MODEL_OVERRIDE.get(model, model)
    try:
        return claude().messages.create(model=use, **kwargs)
    except Exception as e:
        emsg = str(e).lower()
        is_model_gone = ("not_found" in emsg or "model:" in emsg) and model not in _MODEL_OVERRIDE
        if not is_model_gone:
            raise
        fb = _resolve_fallback(model)
        if not fb or fb == use:
            raise
        _MODEL_OVERRIDE[model] = fb
        log(f"   ⚠ MODEL '{model}' unavailable (retired?) → auto-healed to '{fb}'. "
            f"Update CLAUDE_MODEL/HAIKU_MODEL in _common.py to pin it.")
        return claude().messages.create(model=fb, **kwargs)

def claude_json(system, user, max_tokens=1000, model=CLAUDE_MODEL, cache_system=False):
    """Call Claude and parse a JSON object from the reply (robust to fences)."""
    msg = _create(model, max_tokens=max_tokens, system=_sys_param(system, cache_system),
                  messages=[{"role": "user", "content": user}])
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    return _parse_json(text), text

def claude_text(system, user, max_tokens=1000, model=CLAUDE_MODEL, cache_system=False):
    msg = _create(model, max_tokens=max_tokens, system=_sys_param(system, cache_system),
                  messages=[{"role": "user", "content": user}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()

def _parse_json(text):
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
        t = t.strip()
    # find first { ... last }
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end != -1:
        t = t[start:end + 1]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        # Lenient retry: strip trailing commas before } or ] (common LLM slip,
        # esp. Haiku) — e.g. {"a":1,} → {"a":1}.
        return json.loads(re.sub(r",\s*([}\]])", r"\1", t))

# ── ChromaDB (lazy; heavy import) ─────────────────────────────────────────────
@functools.lru_cache(maxsize=1)
def chroma_collection():
    import chromadb
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    names = [c.name for c in client.list_collections()]
    for cand in CHROMA_COLLECTION_CANDIDATES:
        if cand in names:
            return client.get_collection(cand)
    raise RuntimeError(f"No prelims collection found. Have: {names}")

@functools.lru_cache(maxsize=1)
def embedder():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBED_MODEL)

def pyq_lookup(text, n=3, max_distance=0.45):
    """FILTER 5: has RPSC tested this before? Returns best match meta or None."""
    try:
        col = chroma_collection()
        vec = embedder().encode([text]).tolist()
        res = col.query(query_embeddings=vec, n_results=n,
                        include=["metadatas", "distances", "documents"])
        if not res["ids"] or not res["ids"][0]:
            return None
        dist = res["distances"][0][0]
        if dist > max_distance:
            return None
        meta = res["metadatas"][0][0]
        return {"distance": round(dist, 3), "score": round(1 - dist, 3),
                "topic": meta.get("topic"), "subject": meta.get("subject"),
                "year": meta.get("year"), "q_no": meta.get("q_no")}
    except Exception as e:
        print(f"   ⚠ pyq_lookup failed: {e}")
        return None

def pyq_lookup_many(text, n=3, max_distance=0.70):
    """Return up to `n` PYQ candidates (best first) within max_distance.
    Looser bound than pyq_lookup — these are CANDIDATES for a downstream
    relevance filter (Haiku), not final picks. Each item has year/q_no/topic/
    subject/score/document."""
    try:
        col = chroma_collection()
        vec = embedder().encode([text]).tolist()
        res = col.query(query_embeddings=vec, n_results=n,
                        include=["metadatas", "distances", "documents"])
        if not res["ids"] or not res["ids"][0]:
            return []
        out = []
        for meta, dist, doc in zip(res["metadatas"][0], res["distances"][0],
                                   res["documents"][0]):
            if dist > max_distance:
                continue
            out.append({"distance": round(dist, 3), "score": round(1 - dist, 3),
                        "topic": meta.get("topic"), "subject": meta.get("subject"),
                        "year": meta.get("year"), "q_no": meta.get("q_no"),
                        "document": doc})
        return out
    except Exception as e:
        print(f"   ⚠ pyq_lookup_many failed: {e}")
        return []

# ── Misc ──────────────────────────────────────────────────────────────────────
def log(msg=""):
    print(msg, flush=True)

def parse_date(s):
    return datetime.date.fromisoformat(s)

# ── IST time labels (for curator auto-publish messaging) ──────────────────────
IST_OFFSET = datetime.timedelta(hours=5, minutes=30)

def ist_label(dt_utc):
    """Format a UTC datetime (naive or aware) as 'HH:MM IST, DD Mon'."""
    if getattr(dt_utc, "tzinfo", None) is not None:
        dt_utc = dt_utc.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return (dt_utc + IST_OFFSET).strftime("%H:%M IST, %d %b")

def ist_label_from_iso(iso_str):
    """Format a UTC ISO string ('...Z' or offset) as an IST label, or '' on failure."""
    try:
        return ist_label(datetime.datetime.fromisoformat((iso_str or "").replace("Z", "+00:00")))
    except Exception:
        return ""
