/**
 * Hindi extraction pipeline for RAS Prelims 2015, 2016, 2018, 2021, 2023
 * Phase 1: Extract Hindi question + options from PDF → upsert to DB
 * Phase 2: Generate fresh Hindi explanations → upsert to DB
 */

import Anthropic from '@anthropic-ai/sdk';
import { createClient } from '@supabase/supabase-js';
import { readFileSync, writeFileSync, existsSync } from 'fs';
import { config } from 'dotenv';
config({ path: new URL('.env', import.meta.url).pathname, override: true });

const anthropic = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });
const supabase = createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_KEY);

// ─── Year configs ─────────────────────────────────────────────────────────────
const YEAR_CONFIGS = [
  {
    year: 2023,
    pdf: '/Users/nitishdugar/Downloads/RAS mains/2023/RPSC RAS pre 2023.pdf',
    hindiSide: 'LEFT',   // Hindi on left column, English on right
  },
  {
    year: 2021,
    pdf: '/Users/nitishdugar/Downloads/RAS mains/2021/RPSC RAS 2021 prelims.pdf',
    hindiSide: 'RIGHT',  // English on left, Hindi on right (reversed!)
  },
  {
    year: 2018,
    pdf: '/Users/nitishdugar/Downloads/RAS mains/2018/RAS prelime 2018.pdf',
    hindiSide: 'LEFT',
  },
  {
    year: 2016,
    pdf: '/Users/nitishdugar/Downloads/RAS mains/2016/RPSC RAS prelims 2016.pdf',
    hindiSide: 'LEFT',
  },
  {
    year: 2015,
    pdf: '/Users/nitishdugar/Downloads/RAS mains/2012/RAS mains 2012 prelims 1C.pdf', // misnamed file
    hindiSide: 'LEFT',
  },
];

// 10 batches of 15 questions per year
const BATCHES = [
  [1,15],[16,30],[31,45],[46,56],[57,60],[61,75],[76,90],[91,105],[106,120],[121,135],[136,150]
];

// ─── Phase 1: Extract Hindi q + options ──────────────────────────────────────
async function extractBatch(pdfBase64, start, end, hindiSide) {
  const col = hindiSide === 'LEFT'
    ? 'LEFT column (Hindi), RIGHT column (English)'
    : 'LEFT column (English), RIGHT column (Hindi)';

  const response = await anthropic.messages.create({
    model: 'claude-haiku-4-5-20251001',
    max_tokens: 8192,
    messages: [{
      role: 'user',
      content: [
        {
          type: 'document',
          source: { type: 'base64', media_type: 'application/pdf', data: pdfBase64 }
        },
        {
          type: 'text',
          text: `This is a bilingual RAS Prelims exam paper.
Each page has TWO columns: ${col}.

Extract ONLY the Hindi text for questions ${start} to ${end}.

Output a JSON array where each element has:
{
  "q_no": <integer>,
  "question_hi": "<full Hindi question text, NO question number prefix>",
  "option_1_hi": "<answer option 1>",
  "option_2_hi": "<answer option 2>",
  "option_3_hi": "<answer option 3>",
  "option_4_hi": "<answer option 4>"
}

RULES:
1. question_hi = full question text including A./B./C. statements and any सूची/कूट sections
2. option_1_hi through option_4_hi = the FINAL (1)(2)(3)(4) answer choices only
3. SKIP option (5) "अनुत्तरित प्रश्न" entirely
4. For सूची (match-list) questions, include the full table in question_hi using pipe separator:
   "सूची-I | सूची-II | A. [text] | i. [text] | B. [text] | ii. [text] ..."
5. For (A)(B)(C) or (i)(ii)(iii) statement questions: include all statements + कूट in question_hi; option_1_hi through option_4_hi = the (1)(2)(3)(4) answer choices
6. Do NOT include any English text
7. Output ONLY raw JSON array — no markdown, no backticks, no explanation`
        }
      ]
    }]
  });

  let text = response.content[0].text.trim()
    .replace(/^```json\s*/i, '').replace(/^```\s*/i, '').replace(/\s*```$/i, '');
  try {
    return JSON.parse(text);
  } catch (e) {
    writeFileSync(`./debug-${Date.now()}.txt`, text);
    console.error('    Parse error, raw saved. Stop reason:', response.stop_reason);
    throw e;
  }
}

async function runExtraction(cfg) {
  const { year, pdf, hindiSide } = cfg;
  const cachePath = `./hindi-${year}-extracted.json`;

  let extracted = [];
  if (existsSync(cachePath)) {
    extracted = JSON.parse(readFileSync(cachePath, 'utf8'));
    console.log(`  [${year}] Loaded ${extracted.length} from cache`);
    if (extracted.length === 150) {
      console.log(`  [${year}] Already complete, skipping extraction`);
      return extracted;
    }
  }

  const extractedQnos = new Set(extracted.map(q => q.q_no));

  console.log(`  [${year}] Reading PDF...`);
  const pdfBase64 = readFileSync(pdf).toString('base64');

  for (const [start, end] of BATCHES) {
    if (extractedQnos.has(start) && extractedQnos.has(end)) {
      console.log(`  [${year}] Q${start}-${end}: cached`);
      continue;
    }
    console.log(`  [${year}] Extracting Q${start}-${end}...`);
    try {
      const batch = await extractBatch(pdfBase64, start, end, hindiSide);
      console.log(`  [${year}]   → ${batch.length} questions`);
      if (batch.length !== (end - start + 1)) {
        console.warn(`  [${year}]   ⚠ expected ${end - start + 1}, got ${batch.length}`);
      }
      extracted.push(...batch);
      writeFileSync(cachePath, JSON.stringify(extracted, null, 2));
    } catch (err) {
      console.error(`  [${year}] FAILED Q${start}-${end}:`, err.message);
      writeFileSync(cachePath, JSON.stringify(extracted, null, 2));
      throw err;
    }
  }

  extracted.sort((a, b) => a.q_no - b.q_no);

  const missing = [];
  for (let i = 1; i <= 150; i++) {
    if (!extracted.find(q => q.q_no === i)) missing.push(i);
  }
  if (missing.length > 0) console.warn(`  [${year}] Missing q_nos: ${missing.join(',')}`);
  else console.log(`  [${year}] All 150 questions extracted ✓`);

  writeFileSync(cachePath, JSON.stringify(extracted, null, 2));
  return extracted;
}

async function upsertExtracted(year, extracted) {
  const { data: rows } = await supabase
    .from('questions').select('id, q_no').eq('year', year);

  const idMap = {};
  for (const r of rows) idMap[parseInt(r.q_no)] = r.id;

  const updates = extracted.map(q => ({
    id: idMap[q.q_no],
    question_hi: q.question_hi,
    option_1_hi: q.option_1_hi,
    option_2_hi: q.option_2_hi,
    option_3_hi: q.option_3_hi,
    option_4_hi: q.option_4_hi,
  })).filter(u => u.id);

  let ok = 0, fail = 0;
  await Promise.all(updates.map(async ({ id, ...fields }) => {
    const { error } = await supabase.from('questions').update(fields).eq('id', id);
    if (error) { console.error(`  [${year}] update fail id=${id}:`, error.message); fail++; }
    else ok++;
  }));
  console.log(`  [${year}] DB update: ${ok} ok, ${fail} failed`);
}

// ─── Phase 2: Generate Hindi explanations ────────────────────────────────────
async function generateExplanationBatch(questions) {
  const questionsText = questions.map(q =>
    `Q${q.q_no}: ${q.question_hi || q.question}\nसही उत्तर: ${q.correct_text}\n(English context: ${q.explanation || 'N/A'})`
  ).join('\n\n');

  const response = await anthropic.messages.create({
    model: 'claude-haiku-4-5-20251001',
    max_tokens: 4096,
    messages: [{
      role: 'user',
      content: `तुम एक अनुभवी RPSC RAS परीक्षा शिक्षक हो। नीचे दिए गए प्रश्नों के लिए हिंदी में व्याख्या लिखो।

नियम:
- व्याख्या 3-4 वाक्यों में हो, सरल और स्पष्ट भाषा में
- अनुवाद मत करो, ताजा व्याख्या लिखो जैसे कोई शिक्षक समझाए
- एक याद करने की ट्रिक या memory hook जरूर शामिल करो
- भाषा आम हिंदी में हो, भारी शब्द कम उपयोग करो

प्रश्न:
${questionsText}

Output ONLY a JSON array, no markdown:
[{"q_no": <number>, "explanation_hi": "<व्याख्या>"}, ...]`
    }]
  });

  let text = response.content[0].text.trim()
    .replace(/^```json\s*/i, '').replace(/^```\s*/i, '').replace(/\s*```$/i, '');
  return JSON.parse(text);
}

async function runExplanations(year) {
  const cachePath = `./hindi-explanations-${year}.json`;

  let cached = {};
  if (existsSync(cachePath)) {
    const arr = JSON.parse(readFileSync(cachePath, 'utf8'));
    for (const e of arr) cached[e.q_no] = e.explanation_hi;
    console.log(`  [${year}] Loaded ${Object.keys(cached).length} explanations from cache`);
  }

  const { data: rows } = await supabase
    .from('questions')
    .select('id, q_no, question, question_hi, correct_text, explanation')
    .eq('year', year)
    .order('q_no');

  const todo = rows.filter(r => !cached[parseInt(r.q_no)]);
  console.log(`  [${year}] Need explanations for ${todo.length} questions`);

  for (let i = 0; i < todo.length; i += 10) {
    const batch = todo.slice(i, i + 10);
    console.log(`  [${year}] Generating explanations Q${batch[0].q_no}-Q${batch[batch.length-1].q_no}...`);
    try {
      const results = await generateExplanationBatch(batch);
      for (const r of results) cached[r.q_no] = r.explanation_hi;
      writeFileSync(cachePath, JSON.stringify(
        Object.entries(cached).map(([q_no, explanation_hi]) => ({ q_no: parseInt(q_no), explanation_hi })),
        null, 2
      ));
    } catch (err) {
      console.error(`  [${year}] Explanation batch failed:`, err.message);
      writeFileSync(cachePath, JSON.stringify(
        Object.entries(cached).map(([q_no, explanation_hi]) => ({ q_no: parseInt(q_no), explanation_hi })),
        null, 2
      ));
      throw err;
    }
  }

  // Upsert explanations
  const idMap = {};
  for (const r of rows) idMap[parseInt(r.q_no)] = r.id;

  let ok = 0, fail = 0;
  await Promise.all(
    Object.entries(cached).map(async ([q_no, explanation_hi]) => {
      const id = idMap[parseInt(q_no)];
      if (!id) return;
      const { error } = await supabase.from('questions').update({ explanation_hi }).eq('id', id);
      if (error) fail++; else ok++;
    })
  );
  console.log(`  [${year}] Explanations updated: ${ok} ok, ${fail} failed`);
}

// ─── Main ─────────────────────────────────────────────────────────────────────
async function main() {
  console.log('=== Phase 1: Extract Hindi questions + options ===\n');
  for (const cfg of YEAR_CONFIGS) {
    console.log(`\n── Year ${cfg.year} (Hindi ${cfg.hindiSide}) ──`);
    try {
      const extracted = await runExtraction(cfg);
      await upsertExtracted(cfg.year, extracted);
    } catch (err) {
      console.error(`Year ${cfg.year} FAILED:`, err.message, '— continuing with next year');
    }
  }

  console.log('\n\n=== Phase 2: Generate Hindi explanations ===\n');
  for (const cfg of YEAR_CONFIGS) {
    console.log(`\n── Year ${cfg.year} explanations ──`);
    try {
      await runExplanations(cfg.year);
    } catch (err) {
      console.error(`Year ${cfg.year} explanations FAILED:`, err.message, '— continuing');
    }
  }

  console.log('\n\n✓ All done! 2015, 2016, 2018, 2021, 2023 Hindi content added to DB.');
}

main().catch(console.error);
