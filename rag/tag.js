/**
 * PaperSe — RAG tagging layer for the PRELIMS daily pipeline.
 *
 * Given the items the curator selected, this:
 *   1. embeds each item's text with the SAME model the KB was built with
 *      (via rag/embed.py running in the project venv), and
 *   2. cosine-matches each against topic_kb (RPC match_topic_kb), attaching the
 *      best static topic + its real RPSC question type, trap, multi-stmt pattern,
 *      evergreen flag and Rajasthan hook — plus a frequency-based TIER and any
 *      bridge that fires for the matched topic.
 *
 * Output: the same array, each item gaining an `item.rag` object (or null if no
 * topic clears the similarity threshold).
 */
import { spawnSync } from "child_process";
import { createClient } from "@supabase/supabase-js";
import * as dotenv from "dotenv";
dotenv.config({ override: true });

import path from "path";
import { fileURLToPath } from "url";
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, "..");
const ROOT = path.resolve(PROJECT_ROOT, "..");

// venv python that has sentence-transformers (created during RAG load)
const VENV_PY = path.join(ROOT, ".rag_venv", "bin", "python");
const EMBED_PY = path.join(__dirname, "embed.py");

const MATCH_THRESHOLD = 0.45; // below this, the news isn't really "about" any topic
const MATCH_COUNT = 3;

function getSupabase() {
  return createClient(process.env.SUPABASE_URL, process.env.SUPABASE_KEY);
}

// ── Embed an array of strings → array of 384-dim vectors (one python call) ──────
function embedTexts(texts) {
  const res = spawnSync(VENV_PY, [EMBED_PY], {
    input: JSON.stringify(texts),
    encoding: "utf-8",
    maxBuffer: 64 * 1024 * 1024,
  });
  if (res.status !== 0) {
    throw new Error(`embed.py failed: ${res.stderr || res.error?.message || "unknown"}`);
  }
  return JSON.parse(res.stdout.trim());
}

// ── Frequency → TIER (mirrors rag_schema tier_definitions) ──────────────────────
function tierFor(frequency, bridgeFires) {
  if (frequency >= 4 && bridgeFires) return "TIER1";
  if (frequency >= 4) return "TIER1";
  if (frequency >= 2) return "TIER2";
  if (frequency === 1) return "TIER3";
  return "TIER4";
}
const MCQ_COUNT_BY_TIER = { TIER1: 5, TIER2: 3, TIER3: 1, TIER4: 0 };

// ── Main: tag selected items with their matched static topic ────────────────────
export async function tagWithRAG(selected) {
  if (!selected || selected.length === 0) return selected;

  const texts = selected.map(
    (s) => `${s.title || ""}. ${s.subject || ""} ${s.topic || ""}`.trim()
  );

  let vectors;
  try {
    vectors = embedTexts(texts);
  } catch (e) {
    console.log(`⚠️  RAG tagging skipped (embed failed): ${e.message}`);
    return selected.map((s) => ({ ...s, rag: null }));
  }

  const supabase = getSupabase();

  for (let i = 0; i < selected.length; i++) {
    const { data, error } = await supabase.rpc("match_topic_kb", {
      query_embedding: vectors[i],
      match_count: MATCH_COUNT,
      match_threshold: MATCH_THRESHOLD,
    });

    if (error || !data || data.length === 0) {
      selected[i].rag = null;
      console.log(`  · ${selected[i].title?.slice(0, 50)} → no topic match`);
      continue;
    }

    const best = data[0];

    // Does a bridge fire for this matched topic?
    const { data: bridges } = await supabase
      .from("bridge_map")
      .select("bridge_id, typical_qtype, exam_probability, rajasthan_filter")
      .eq("topic_id", best.topic_id);
    const bridgeFires = !!(bridges && bridges.length);

    const tier = tierFor(best.frequency, bridgeFires);

    selected[i].rag = {
      topic_id: best.topic_id,
      matched_topic: best.topic,
      matched_chapter: best.chapter,
      matched_subject: best.subject,
      similarity: Number(best.similarity.toFixed(3)),
      frequency: best.frequency,
      rpsc_question_type_likely: best.dominant_qtype,
      multi_stmt_correct_pattern: best.multi_stmt_pattern,
      trap_to_avoid: best.trap_to_avoid,
      evergreen_link: best.evergreen_flag === "EVERGREEN" ? best.topic : null,
      rajasthan_angle: best.rajasthan_angle,
      rajasthan_hook: best.rajasthan_hook,
      tier,
      mcq_count_suggested: MCQ_COUNT_BY_TIER[tier],
      bridge_fires: bridgeFires,
      alternates: data.slice(1).map((d) => ({ topic: d.topic, similarity: Number(d.similarity.toFixed(3)) })),
    };

    console.log(
      `  · ${selected[i].title?.slice(0, 46)} → ${best.topic} ` +
      `(${selected[i].rag.similarity}, ${tier}, ${best.dominant_qtype})`
    );
  }

  return selected;
}
