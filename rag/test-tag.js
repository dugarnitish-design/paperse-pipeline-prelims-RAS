import { tagWithRAG } from "./tag.js";

const sample = [
  { title: "Supreme Court delivers landmark constitutional verdict on Article 370", subject: "Indian Polity & Constitution", topic: "" },
  { title: "ISRO successfully launches new navigation satellite NVS-02", subject: "Science & Technology", topic: "" },
  { title: "Rajasthan government commissions large new solar park in Bikaner district", subject: "Rajasthan Economy", topic: "" },
  { title: "Bollywood actor wins international film award at Cannes", subject: "Current Affairs", topic: "" },
];

console.log("Tagging", sample.length, "sample items against the RAG...\n");
const tagged = await tagWithRAG(sample);
console.log("\n=== RESULT ===");
for (const t of tagged) {
  console.log(`\n• ${t.title}`);
  if (!t.rag) { console.log("   → no match (below threshold)"); continue; }
  console.log(`   topic:      ${t.rag.matched_topic} [${t.rag.topic_id}]`);
  console.log(`   similarity: ${t.rag.similarity}  tier: ${t.rag.tier}  freq: ${t.rag.frequency}/6`);
  console.log(`   rpsc_type:  ${t.rag.rpsc_question_type_likely}  mcqs: ${t.rag.mcq_count_suggested}`);
  console.log(`   trap:       ${(t.rag.trap_to_avoid || "").slice(0, 80)}`);
  console.log(`   evergreen:  ${t.rag.evergreen_link || "—"}  bridge_fires: ${t.rag.bridge_fires}`);
}
