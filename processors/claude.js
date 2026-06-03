import Anthropic from "@anthropic-ai/sdk";
import { createClient } from "@supabase/supabase-js";
import * as dotenv from "dotenv";
dotenv.config({ override: true });
import { tagWithRAG } from "../rag/tag.js";

// Lazy init so env vars are loaded first
function getAnthropic() {
  return new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });
}
function getSupabase() {
  return createClient(process.env.SUPABASE_URL, process.env.SUPABASE_KEY);
}

const TAXONOMY_SUBJECTS = [
  "Rajasthan History & Culture",
  "Rajasthan Geography",
  "Rajasthan Economy",
  "Rajasthan Polity & Administration",
  "Indian History — Ancient & Medieval",
  "Indian History — Modern",
  "Indian Polity & Constitution",
  "Indian Economy",
  "India & World Geography",
  "Science & Technology",
  "State Schemes — Rajasthan",
  "National Schemes — Central Government",
  "Current Affairs",
];

// ─── Fetch syllabus from Supabase ─────────────────────────────────────────────
async function fetchSyllabus() {
  const { data } = await getSupabase()
    .from("syllabus")
    .select("section_order, section, topic, subtopics")
    .order("section_order", { ascending: true })
    .order("topic_order", { ascending: true });
  if (!data || data.length === 0) return "";
  const grouped = {};
  data.forEach((r) => {
    if (!grouped[r.section_order]) grouped[r.section_order] = { section: r.section, topics: [] };
    grouped[r.section_order].topics.push(r.subtopics ? `${r.topic} (${r.subtopics})` : r.topic);
  });
  return Object.values(grouped)
    .map((s) => `${s.section}:\n${s.topics.map((t) => `  - ${t}`).join("\n")}`)
    .join("\n\n");
}

// ─── STEP 1: Filter & select items ───────────────────────────────────────────
async function filterAndSelect(pibArticles, sujasText, syllabusText) {
  console.log("→ Step 1: Filtering and selecting items with Claude...");

  const pibSummaries = pibArticles
    .map((a, i) => `[PIB-${i + 1}] ${a.title}\n${a.content.slice(0, 300)}`)
    .join("\n\n---\n\n");

  const sujasSummary = sujasText
    ? `SUJAS CONTENT (Rajasthan Hindi bulletin):\n${sujasText.slice(0, 3000)}`
    : "SUJAS: Not available today.";

  const syllabusBlock = syllabusText
    ? `━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nOFFICIAL RPSC RAS PRELIMS SYLLABUS (use this as the ground truth for relevance)\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n${syllabusText}\n`
    : "";

  const prompt = `You are a content curator for PaperSe — RPSC RAS Prelims Paper 1 exam prep platform.

Select the most exam-relevant items from today's news. A news item is relevant ONLY if it maps to the official RPSC RAS Prelims syllabus provided below. Think: "Would RPSC set a question on this based on the syllabus?"

${syllabusBlock}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SUJAS SELECTION RULES (Rajasthan bulletin)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Pick 0 items if: all news is routine minister visits, inaugurations, cultural events, file approvals
- Pick 1 item if: Rajasthan scheme with clear exam value, Rajasthan-first milestone, state appointment of significance
- Pick 2 items ONLY IF: major scheme launch (budget > 500 crore), Rajasthan national/international award, constitutional/administrative change at state level
- SKIP: routine inaugurations, minister speeches, administrative circulars, cultural events

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PIB SELECTION RULES (4-tier priority)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TIER 1 — Always pick if available (max 3):
  - New central scheme launch or major amendment with exam-testable details
  - Parliamentary act / rules notified
  - New committee or commission constituted with defined mandate

TIER 2 — Pick if TIER 1 slots not full (max 2):
  - India's international ranking, award, or global recognition
  - Science / technology with clear India angle (ISRO, DRDO, IIT, Indian institution)
  - Economic indicator revision or major policy (IIP, WPI, budget allocation)

TIER 3 — Pick only if still need to fill to 5 items:
  - International summit with specific India outcome
  - State-level policy with national implications

TIER 4 — SKIP:
  - Bilateral meetings with no concrete outcome
  - Minister's routine foreign visits
  - Administrative circulars or office orders
  - Infrastructure inaugurations with no syllabus link
  - Political statements

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TOTAL ITEMS: 5 default | 6 if clearly warranted | 7 absolute maximum
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TAG each selected item with:
- subject: from this list: ${TAXONOMY_SUBJECTS.join(" | ")}
- syllabus_topic: one of: History of India | History & Culture of Rajasthan | Geography of India & World | Geography of Rajasthan | Indian Constitution & Polity | Political & Administrative System of Rajasthan | Indian Economy | Economy of Rajasthan | Science & Technology | Current Affairs — National | Current Affairs — International | Current Affairs — Rajasthan
- pyq_pattern: the most likely RPSC question type for this topic — one of: direct-recall | statement-based | year-based | person-based | which-is-not | match-following

PIB ARTICLES:
${pibSummaries}

${sujasSummary}

Return ONLY valid JSON in this exact format (no markdown, no explanation):
{
  "selected": [
    {
      "source": "PIB" or "SUJAS",
      "ref": "PIB-3" or "SUJAS-1",
      "title": "...",
      "subject": "exact subject from taxonomy",
      "chapter": "relevant chapter",
      "topic": "relevant topic",
      "syllabus_topic": "...",
      "pyq_pattern": "...",
      "is_rajasthan": true or false,
      "priority": "high" or "medium",
      "content_snippet": "first 250 chars of the article"
    }
  ],
  "discarded": ["PIB-1 (reason)", "PIB-2 (reason)"]
}`;

  const response = await getAnthropic().messages.create({
    model: "claude-sonnet-4-6",
    max_tokens: 2000,
    messages: [{ role: "user", content: prompt }],
  });

  const raw = response.content[0].text;

  const jsonMatch = raw.match(/\{[\s\S]*\}/);
  if (!jsonMatch) throw new Error("Claude did not return valid JSON in filter step");

  const cleaned = jsonMatch[0]
    .replace(/[\u2018\u2019]/g, "'")
    .replace(/[\u201C\u201D]/g, '"')
    .replace(/\n/g, " ")
    .replace(/\t/g, " ");

  let result;
  try {
    result = JSON.parse(cleaned);
  } catch (e) {
    console.log("⚠️  JSON parse failed, retrying with stricter prompt...");
    const fix = await getAnthropic().messages.create({
      model: "claude-sonnet-4-6",
      max_tokens: 2000,
      messages: [
        { role: "user", content: `Fix this JSON so it is valid. Return ONLY the fixed JSON, nothing else:\n\n${jsonMatch[0].slice(0, 3000)}` }
      ],
    });
    result = JSON.parse(fix.content[0].text.match(/\{[\s\S]*\}/)[0]);
  }

  console.log(`→ Selected ${result.selected.length} items (discarded ${result.discarded?.length || 0})`);
  result.selected.forEach((s) => console.log(`  ✓ [${s.source}] ${s.subject} — ${s.title?.slice(0, 60)}`));
  return result;
}

// ─── STEP 2: Fetch relevant PYQs from Supabase ───────────────────────────────
async function fetchRelevantPYQs(selectedItems) {
  console.log("→ Step 2: Fetching relevant PYQs from Supabase...");

  const subjects = [...new Set(selectedItems.map((s) => s.subject))];
  const pyqs = [];

  for (const subject of subjects) {
    const { data, error } = await getSupabase()
      .from("questions")
      .select("id, year, q_no, question, option_1, option_2, option_3, option_4, correct_text, correct_ans, difficulty, explanation, subject, chapter, topic")
      .eq("subject", subject)
      .limit(6);

    if (data && data.length > 0) pyqs.push(...data);
  }

  console.log(`→ Fetched ${pyqs.length} relevant PYQs from ${subjects.length} subjects\n`);
  return pyqs;
}

// ─── STEP 3: Generate full content ───────────────────────────────────────────
async function generateContent(selectedItems, pibArticles, sujasText, pyqs, date, syllabusText) {
  console.log("→ Step 3: Generating English + Hindi content with Claude...");

  const itemsDetail = selectedItems.map((item, idx) => {
    let snippet = item.content_snippet || '';
    if (item.source === "PIB") {
      const i = parseInt(item.ref.replace("PIB-", "")) - 1;
      const article = pibArticles[i];
      snippet = article?.content?.slice(0, 250) || snippet;
    } else {
      snippet = sujasText?.slice(0, 400) || snippet;
    }
    return `[ITEM ${idx + 1} — ${item.ref}]
NEWS TRIGGER: ${item.title}
SUBJECT: ${item.subject} > ${item.chapter} > ${item.topic}
SYLLABUS TOPIC: ${item.syllabus_topic || item.subject}
PYQ PATTERN: ${item.pyq_pattern || 'direct-recall'}
IS RAJASTHAN: ${item.is_rajasthan ? 'Yes — Rajasthan-specific content' : 'No'}
ARTICLE CONTEXT (for understanding the topic only — do NOT copy facts from here): ${snippet}`;
  }).join("\n\n---\n\n");

  const pyqContext = pyqs.length > 0
    ? `RELEVANT PYQs FROM DATABASE:\n${pyqs.map((q) =>
        `[${q.year} Q${q.q_no}] [${q.subject} > ${q.chapter}] [${q.difficulty}]\n${q.question}\n(a) ${q.option_1}  (b) ${q.option_2}  (c) ${q.option_3}  (d) ${q.option_4}\nAnswer: ${q.correct_text}\nExplanation: ${q.explanation || ''}`
      ).join("\n\n")}`
    : "No matching PYQs in database for today's topics.";

  const today = date || new Date().toLocaleDateString("en-IN", {
    day: "numeric", month: "long", year: "numeric"
  });

  const syllabusRef = syllabusText
    ? `━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nRPSC SYLLABUS REFERENCE (every fact must map to one of these topics)\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n${syllabusText.slice(0, 3000)}\n`
    : "";

  const prompt = `You are generating daily exam-prep content for PaperSe — RPSC RAS Prelims Paper 1 aspirants.

${syllabusRef}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CRITICAL RULES — READ FIRST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. The NEWS TRIGGER tells you WHAT TOPIC is in the news. It does NOT tell you what to write.
2. Use your comprehensive knowledge about the topic. Do NOT summarize the article.
3. Think: "What would RPSC ask in an MCQ about this topic?" — write to answer that.
4. Stay strictly within RPSC Paper 1 syllabus. Every fact must be syllabus-grounded and PYQ-pattern matched.
5. The PYQ PATTERN tag tells you the question style. Write KEY FACTS accordingly:
   - direct-recall → state exact facts: full form, year, ministry, name
   - statement-based → write facts as verifiable true/false statements
   - year-based → emphasize launch/establishment year and what changed
   - person-based → emphasize full name, designation, why appointed
   - which-is-not → include the common wrong assumption explicitly
   - match-following → write as clear pairs: Term = Definition/Ministry/Location

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BOLD FORMATTING RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Use **bold** (double asterisks) ONLY for:
- Key names (scheme names, act titles, committee names)
- Critical numbers (years, budget figures, ranks)
- Acronyms being defined for the first time
- The most exam-critical term in each bullet
Do NOT bold entire sentences. Bold 1-3 words maximum per bullet.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EXAM RELEVANCE PROTOCOL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TYPE A — SCHEMES & PROGRAMS:
  Full form | Launch year + who launched | Nodal Ministry | Objective | Target beneficiary | Key distinguishing feature

TYPE B — COMMITTEES & COMMISSIONS:
  Chairman full name + background | Total members | Mandate in one line | Under which Ministry | Why constituted (constitutional/statutory/executive power)

TYPE C — ACTS & LEGISLATION:
  Year of enactment | Key provisions (2-3) | Implementing body | Constitutional basis (which article/schedule)

TYPE D — ECONOMIC INDICATORS:
  Full form | What it measures exactly | Published by (specific org) | Current base year + previous base year | Frequency | Sector coverage

TYPE E — SCIENCE & TECHNOLOGY:
  Full form | What it does (plain language) | Developed by (country/institution) | Key application | India angle | How it differs from existing tech

TYPE F — SPORTS / AWARDS / RANKINGS:
  Awarding body | India's specific rank or achievement | History (when started) | Criteria

TYPE G — RAJASTHAN SPECIFIC:
  District/location | Scheme official name | Funding (state/central/ratio) | Implementing department | Key target numbers | Rajasthan-first angle

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RAJASTHAN ANGLE RULE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
For EVERY national news item, check: does Rajasthan have a specific connection?
- If YES: add it as a bullet: "- **Rajasthan angle:** [specific connection — district, scheme, data]"
- If NO: skip (do not add a forced generic bullet)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MEMORY HOOK RULES — STRICT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ONLY include if the hook GENUINELY aids recall. Default is to SKIP.
The hook MUST be shorter than the fact itself.
If it requires explanation, it is NOT a hook — skip it.

Allowed types:
- Acronym: "**WANI** = Wi-Fi for All Nationally by India"
- Number pattern: "IIP base years: **93-94 → 04-05 → 11-12 → 22-23** (every ~11 yrs)"
- Rhyme: "NSB runs the game, **NST** settles the claim"
- Visual story: "PM chairs it, illegal immigration drives it — executive power, not constitutional"

NEVER write:
- Restatements: "Remember: X was launched in 2020" (already in facts)
- Generic: "This is important for exam" / "Key point to remember"
- Anything that needs 2 sentences to explain

If no strong hook exists → OMIT the MEMORY HOOK section entirely for that item.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FACT-CHECK PROTOCOL — MANDATORY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Before finalising each item, verify:
- All numbers (budget figures, capacity, years) — flag uncertain ones with [VERIFY]
- All proper nouns (scheme names, committee names, act titles) — exact official spelling
- All rankings and positions — only include if you are confident
- If unsure about any fact → write [VERIFY] next to it rather than stating it confidently

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NEVER INCLUDE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Circular or notification numbers
- Implementation deadlines from press releases
- Administrative details (who presided, venue, meeting agenda)
- Names of officers below Secretary level
- Generic exam advice ("this is important", "study this")
- Practice MCQs in OUTPUT_1 or OUTPUT_2 (they go in OUTPUT_3 only)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DATE: ${today}
ITEMS: ${selectedItems.length}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

${pyqContext}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TODAY'S TOPICS:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
${itemsDetail}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FORMAT RULES — STRICT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Use **bold** for key terms only (as per BOLD FORMATTING RULES above)
- WHY IN NEWS: exactly 1-2 lines of plain text
- KEY FACTS: exactly 4 bullets (5 if Rajasthan angle bullet added), starting with "- "
- MEMORY HOOK: only if genuinely useful (may be omitted)
- PYQ VAULT: include ONLY if a PYQ from the database directly matches this topic's subject+chapter
- Rajasthan items FIRST in all outputs
- Total must fit in 2 printed A4 pages (3 only in exceptional circumstances)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT_1 — ENGLISH:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PaperSe | Daily Current Affairs
📅 ${today}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📋 TODAY AT A GLANCE
• [Rajasthan item first — topic: one exam-critical fact]
• [Item 2 — topic: fact]
• [Item 3 — topic: fact]
• [Item 4 — topic: fact]
• [Item 5 — topic: fact]

[For each item — Rajasthan items FIRST:]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏛 [Subject] › [Chapter] › [Topic Name]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📰 WHY IN NEWS
[1-2 lines. The trigger. What happened. No generic phrases.]

📌 KEY FACTS FOR RPSC
- [Fact 1 — use **bold** on the key term]
- [Fact 2]
- [Fact 3]
- [Fact 4]
- [Rajasthan angle bullet if applicable]

💡 MEMORY HOOK
[One-line hook — OMIT THIS SECTION if no strong hook exists]

[Only if a PYQ from database directly matches subject+chapter:]
📎 PYQ VAULT
[Year] Q[No]. [Full question text]
(a) [option] (b) [option] (c) [option] (d) [option]
Answer: ([letter]) [correct text]
↳ [One line connecting to today's topic]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
paperse.in | ${today}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

OUTPUT_2 — HINDI:
[Same structure in Hindi. Fresh writing — not translation. Technical terms in English with Hindi in brackets on first use. Rajasthan items first. Same 4-5 facts with same bold markers, memory hook only if strong, same PYQ if applicable.]

OUTPUT_3 — PRACTICE TEST JSON:
Generate 5-8 MCQs from today's topics in RPSC PYQ style.
Questions must be grounded in RPSC syllabus and PYQ patterns — not generic GK.

Question style mix (do not use only one type):
- Direct recall: "X stands for:", "Y is published by:", "Which ministry launched Z?"
- Statement-based: "Consider the following statements — which is/are correct?"
- Year-based: "In which year was X established?"
- Person-based: "Who chairs/heads Y?"
- Which-is-NOT: "Which of the following is NOT a feature of X?"
- At least 1 match-the-following style if 6+ questions

Difficulty: 60% easy-medium, 40% medium-hard
Options: 3 plausible wrong answers + 1 correct (options must not be obviously wrong)
At least 1 question per news item.

Return ONLY this JSON (no markdown, no other text):
{
  "date": "${today}",
  "practice_test": {
    "total": NUMBER,
    "time_per_question_seconds": 60,
    "questions": [
      {
        "num": 1,
        "type": "direct-recall",
        "question": "...",
        "options": {"a": "...", "b": "...", "c": "...", "d": "..."},
        "correct": "b",
        "difficulty": "easy",
        "syllabus_topic": "...",
        "news_topic": "...",
        "explanation": "..."
      }
    ]
  }
}`;

  const response = await getAnthropic().messages.create({
    model: "claude-sonnet-4-6",
    max_tokens: 10000,
    messages: [{ role: "user", content: prompt }],
  });

  const raw = response.content[0].text;
  console.log("→ Content generated successfully\n");
  return raw;
}

// ─── STEP 4: Verify & fact-check ─────────────────────────────────────────────
async function verifyContent(rawOutput) {
  console.log("→ Step 4: Fact-checking with Claude...");

  const prompt = `You are a fact-checker for an RPSC exam prep platform. Review the content below and:

1. Verify all numbers (years, budget figures, ranks, counts)
2. Verify all proper nouns (scheme names, act titles, committee names) — check official spelling
3. Verify all institutional attributions (ministry names, body names)
4. Check that no fact is contradicted by another in the same content

Rules:
- If a fact is CORRECT → leave it unchanged
- If a fact is UNCERTAIN → add [VERIFY] immediately after it
- If a fact is WRONG → correct it and add [CORRECTED] after
- Do NOT rewrite structure, formatting, or wording — only fix specific facts
- Do NOT add new content
- Return the full corrected text

CONTENT TO VERIFY:
${rawOutput.slice(0, 8000)}`;

  const response = await getAnthropic().messages.create({
    model: "claude-sonnet-4-6",
    max_tokens: 10000,
    messages: [{ role: "user", content: prompt }],
  });

  const verified = response.content[0].text;
  const verifyCount = (verified.match(/\[VERIFY\]/g) || []).length;
  const correctedCount = (verified.match(/\[CORRECTED\]/g) || []).length;
  console.log(`→ Verification done: ${verifyCount} uncertain, ${correctedCount} corrected\n`);
  return verified;
}

// ─── STEP 5: Generate MCQs (2-pass: generate → verify) ───────────────────────
export async function generateMCQs(selected, rawOutput) {
  console.log("→ Step 5a: Generating MCQs (RPSC PYQ style)...");

  const itemSummaries = selected
    .map((s) => {
      const base = `- ${s.title || s.source} [${s.subject || ""}]`;
      if (!s.rag) return base;
      const r = s.rag;
      return base +
        `\n    ↳ RAG topic: ${r.matched_topic} | TIER ${r.tier} | rpsc_type: ${r.rpsc_question_type_likely}` +
        (r.multi_stmt_correct_pattern ? ` | multi-stmt pattern: ${r.multi_stmt_correct_pattern}` : "") +
        (r.evergreen_link ? ` | evergreen twin: ${r.evergreen_link}` : "") +
        (r.rajasthan_hook ? ` | Rajasthan hook: ${r.rajasthan_hook}` : "") +
        `\n      trap: ${r.trap_to_avoid || ""}`;
    })
    .join("\n");

  // ── PASS 1: Generate ─────────────────────────────────────────────────────────
  const generatePrompt = `You are a senior question setter for RPSC RAS Prelims exam with 10 years of experience. Study the current affairs content and generate 5–8 MCQs exactly in the style of actual RPSC PYQs.

━━━ RPSC PYQ STYLE RULES ━━━
QUESTION DESIGN:
- Test one specific, unambiguous fact per question
- Prefer: who/which/when/where over "which of the following is correct"
- Avoid statement-based (Statement 1/2) questions — RPSC Prelims rarely uses them
- Stem must be complete — the student should know exactly what is being asked
- Difficulty: moderate to hard — the answer should not be obvious from the question itself

OPTION DESIGN (critical):
- All 4 options must be plausible — no obviously wrong distractors
- Distractors must be: same category, similar length, grammatically parallel
- Wrong options should be real but incorrect facts (e.g., wrong year, wrong ministry, wrong city)
- Never use vague options like "None of these" or "All of the above"
- Correct answer position: distribute across a/b/c/d — do NOT default to "a" or "b"

WHAT TO TEST (priority order):
1. Constitutional/legal provisions — Article numbers, Act names, year of enactment
2. Institutional facts — which ministry, which body, chairperson, headquarters
3. Numbers/statistics — base years, percentages, rankings, targets
4. Chronology — sequence of events, launch dates, revision timelines
5. Rajasthan angle — any Rajasthan connection in the news item

WHAT TO AVOID:
- Do NOT ask trivial definitional questions ("What is IIP?")
- Do NOT ask questions where 2+ options could be correct
- Do NOT fabricate facts not present in the content
- Do NOT create questions that test reading comprehension rather than knowledge

━━━ RAG FORMAT MATCHING (obey the "↳ RAG topic" hints on each item) ━━━
Each item may carry a RAG hint derived from how RPSC has historically tested that topic.
When an item has one:
- rpsc_type: write at least one MCQ in THIS exact format (e.g. NOT-MATCHED, MATCH-THE-PAIRS,
  MULTI-STATEMENT, SEQUENCE, ASSERTION-REASON). Do NOT silently default to direct-factual.
- multi-stmt pattern: if MULTI-STATEMENT, follow the stated correct-pattern and plant exactly
  ONE false statement (wrong number, date, or over-broad claim). "All correct" is bait — avoid it
  unless the pattern explicitly says so.
- trap: steer clear of the named trap when writing distractors.
- evergreen twin: if given, include ONE MCQ testing that durable static concept itself, not only
  the news event (news is the hook; the static fact is the point).
- TIER: weight effort by tier — TIER1 items deserve the most and hardest questions, TIER3 the fewest.

SUBJECT TAGS — pick one per MCQ:
Rajasthan History & Culture | Rajasthan Geography | Rajasthan Economy | Rajasthan Polity & Administration | Indian History | Indian Polity & Constitution | Indian Economy | India & World Geography | Science & Technology | Current Affairs

OUTPUT: Return ONLY a valid JSON array, no explanation, no markdown:
[
  {
    "q_no": 1,
    "question": "...",
    "option_a": "...",
    "option_b": "...",
    "option_c": "...",
    "option_d": "...",
    "correct": "b",
    "explanation": "...",
    "subject": "..."
  }
]

TODAY'S ITEMS:
${itemSummaries}

CONTENT:
${rawOutput.slice(0, 7000)}`;

  const r1 = await getAnthropic().messages.create({
    model: "claude-sonnet-4-6",
    max_tokens: 4000,
    messages: [{ role: "user", content: generatePrompt }],
  });

  const raw = r1.content[0].text.trim();
  const m1 = raw.match(/\[[\s\S]*\]/);
  if (!m1) throw new Error("MCQ generation returned no JSON");
  const draft = JSON.parse(m1[0]);
  console.log(`→ Step 5a: Generated ${draft.length} draft MCQs`);

  // ── PASS 2: Verify ───────────────────────────────────────────────────────────
  console.log("→ Step 5b: Cross-checking MCQ accuracy...");

  const verifyPrompt = `You are a strict RPSC exam auditor. Below are draft MCQs generated from current affairs content. Cross-check each MCQ against the SOURCE CONTENT and fix any issues.

━━━ AUDIT CHECKLIST FOR EACH MCQ ━━━
1. CORRECT ANSWER: Verify the marked correct answer is actually correct per the source content
2. WRONG OPTIONS: Verify no wrong option is accidentally also correct
3. FACTUAL ACCURACY: Check all specific facts in question stem and options (numbers, names, dates, articles)
4. AMBIGUITY: If question has 2 valid answers → rewrite question to be specific
5. DISTRACTORS: If any option is obviously wrong (not in same category) → replace with a plausible alternative
6. OPTION BALANCE: If correct answer is always "a" or "b" → redistribute

ACTIONS:
- If MCQ is accurate and well-formed → keep as-is
- If a fact is wrong → correct it
- If correct answer is wrong → fix the "correct" field
- If question is ambiguous → rewrite to remove ambiguity
- If MCQ cannot be fixed (source content is insufficient) → REMOVE it
- Renumber q_no sequentially after removals

Return ONLY the corrected JSON array. No explanation text outside the JSON.

SOURCE CONTENT:
${rawOutput.slice(0, 7000)}

DRAFT MCQs:
${JSON.stringify(draft, null, 2)}`;

  const r2 = await getAnthropic().messages.create({
    model: "claude-sonnet-4-6",
    max_tokens: 4000,
    messages: [{ role: "user", content: verifyPrompt }],
  });

  const verified = r2.content[0].text.trim();
  const m2 = verified.match(/\[[\s\S]*\]/);
  if (!m2) throw new Error("MCQ verification returned no JSON");
  const finalMCQs = JSON.parse(m2[0]);

  const removed = draft.length - finalMCQs.length;
  console.log(`→ Step 5b: ${finalMCQs.length} MCQs passed audit${removed > 0 ? ` (${removed} removed)` : ""}\n`);
  return finalMCQs;
}

// ─── MAIN EXPORT ─────────────────────────────────────────────────────────────
export async function runClaudeProcessor(pibArticles, sujasResult, date) {
  const syllabusText = await fetchSyllabus();
  console.log(`→ Syllabus loaded: ${syllabusText.split("\n").length} lines`);

  const { selected, discarded } = await filterAndSelect(
    pibArticles,
    sujasResult.text,
    syllabusText
  );

  // ── RAG: tag each selected item to its static topic (embedding cosine match) ──
  // Attaches item.rag = { matched_topic, tier, rpsc_question_type_likely, trap,
  // evergreen_link, multi_stmt pattern, rajasthan_hook } from topic_kb + bridge_map.
  console.log("→ Step 1b: Tagging selected items to the RAG (topic_kb)...");
  await tagWithRAG(selected);

  const pyqs = await fetchRelevantPYQs(selected);

  const rawOutput = await generateContent(
    selected,
    pibArticles,
    sujasResult.text,
    pyqs,
    date,
    syllabusText
  );

  const verifiedOutput = await verifyContent(rawOutput);
  const mcqs = await generateMCQs(selected, verifiedOutput);

  return { rawOutput: verifiedOutput, selected, discarded, pyqsUsed: pyqs.length, mcqs };
}
