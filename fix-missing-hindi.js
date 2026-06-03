/**
 * Fix missing Hindi questions for 2021 (Q86-90) and 2016 (Q87-90)
 */

import Anthropic from '@anthropic-ai/sdk';
import { createClient } from '@supabase/supabase-js';
import { readFileSync, writeFileSync } from 'fs';
import { config } from 'dotenv';
config({ path: new URL('.env', import.meta.url).pathname, override: true });

const anthropic = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });
const supabase = createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_KEY);

const MISSING = [
  {
    year: 2021,
    pdf: '/Users/nitishdugar/Downloads/RAS mains/2021/RPSC RAS 2021 prelims.pdf',
    hindiSide: 'RIGHT',
    qnos: [86, 87, 88, 89, 90],
  },
  {
    year: 2016,
    pdf: '/Users/nitishdugar/Downloads/RAS mains/2016/RPSC RAS prelims 2016.pdf',
    hindiSide: 'LEFT',
    qnos: [87, 88, 89, 90],
  },
];

async function extractSpecific(pdfBase64, qnos, hindiSide) {
  const col = hindiSide === 'LEFT'
    ? 'LEFT column (Hindi), RIGHT column (English)'
    : 'LEFT column (English), RIGHT column (Hindi)';

  const start = Math.min(...qnos);
  const end = Math.max(...qnos);

  const response = await anthropic.messages.create({
    model: 'claude-haiku-4-5-20251001',
    max_tokens: 4096,
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
4. For सूची (match-list) questions, include the full table in question_hi using pipe separator
5. Do NOT include any English text
6. Output ONLY raw JSON array — no markdown, no backticks, no explanation`
        }
      ]
    }]
  });

  let text = response.content[0].text.trim()
    .replace(/^```json\s*/i, '').replace(/^```\s*/i, '').replace(/\s*```$/i, '');
  return JSON.parse(text);
}

async function fixYear(cfg) {
  const { year, pdf, hindiSide, qnos } = cfg;
  console.log(`\n── Fixing year ${year}: Q${qnos.join(',')} ──`);

  console.log(`  Reading PDF...`);
  const pdfBase64 = readFileSync(pdf).toString('base64');

  console.log(`  Extracting Q${Math.min(...qnos)}-${Math.max(...qnos)}...`);
  const batch = await extractSpecific(pdfBase64, qnos, hindiSide);
  console.log(`  Got ${batch.length} questions:`, batch.map(q => q.q_no).join(','));

  // Load cache and merge
  const cachePath = `./hindi-${year}-extracted.json`;
  const existing = JSON.parse(readFileSync(cachePath, 'utf8'));
  const existingNos = new Set(existing.map(q => q.q_no));
  for (const q of batch) {
    if (!existingNos.has(q.q_no)) existing.push(q);
  }
  existing.sort((a, b) => a.q_no - b.q_no);
  writeFileSync(cachePath, JSON.stringify(existing, null, 2));
  console.log(`  Cache updated: ${existing.length} total`);

  // Upsert to DB
  const { data: rows } = await supabase
    .from('questions').select('id, q_no').eq('year', year);
  const idMap = {};
  for (const r of rows) idMap[parseInt(r.q_no)] = r.id;

  let ok = 0, fail = 0;
  for (const q of batch) {
    const id = idMap[q.q_no];
    if (!id) { console.warn(`  No DB row for Q${q.q_no}`); continue; }
    const { error } = await supabase.from('questions').update({
      question_hi: q.question_hi,
      option_1_hi: q.option_1_hi,
      option_2_hi: q.option_2_hi,
      option_3_hi: q.option_3_hi,
      option_4_hi: q.option_4_hi,
    }).eq('id', id);
    if (error) { console.error(`  fail Q${q.q_no}:`, error.message); fail++; }
    else ok++;
  }
  console.log(`  DB: ${ok} ok, ${fail} failed`);
}

// Generate explanations for the newly fixed questions
async function generateExplanationsForMissing(year, qnos) {
  console.log(`\n  Generating explanations for year ${year} Q${qnos.join(',')}...`);

  const { data: rows } = await supabase
    .from('questions')
    .select('id, q_no, question, question_hi, correct_text, explanation')
    .eq('year', year)
    .in('q_no', qnos);

  if (!rows || rows.length === 0) { console.log('  No rows found'); return; }

  const questionsText = rows.map(q =>
    `Q${q.q_no}: ${q.question_hi || q.question}\nसही उत्तर: ${q.correct_text}\n(English context: ${q.explanation || 'N/A'})`
  ).join('\n\n');

  const response = await anthropic.messages.create({
    model: 'claude-haiku-4-5-20251001',
    max_tokens: 2048,
    messages: [{
      role: 'user',
      content: `तुम एक अनुभवी RPSC RAS परीक्षा शिक्षक हो। नीचे दिए गए प्रश्नों के लिए हिंदी में व्याख्या लिखो।

नियम:
- व्याख्या 3-4 वाक्यों में हो, सरल और स्पष्ट भाषा में
- अनुवाद मत करो, ताजा व्याख्या लिखो जैसे कोई शिक्षक समझाए
- एक याद करने की ट्रिक या memory hook जरूर शामिल करो

प्रश्न:
${questionsText}

Output ONLY a JSON array, no markdown:
[{"q_no": <number>, "explanation_hi": "<व्याख्या>"}, ...]`
    }]
  });

  let text = response.content[0].text.trim()
    .replace(/^```json\s*/i, '').replace(/^```\s*/i, '').replace(/\s*```$/i, '');
  const results = JSON.parse(text);

  const idMap = {};
  for (const r of rows) idMap[parseInt(r.q_no)] = r.id;

  let ok = 0;
  for (const r of results) {
    const id = idMap[r.q_no];
    if (!id) continue;
    const { error } = await supabase.from('questions').update({ explanation_hi: r.explanation_hi }).eq('id', id);
    if (!error) ok++;
  }
  console.log(`  Explanations: ${ok} ok`);
}

async function main() {
  for (const cfg of MISSING) {
    await fixYear(cfg);
    await generateExplanationsForMissing(cfg.year, cfg.qnos);
  }
  console.log('\nDone! All missing questions fixed.');
}

main().catch(console.error);
