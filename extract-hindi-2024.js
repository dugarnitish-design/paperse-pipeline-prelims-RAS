import Anthropic from '@anthropic-ai/sdk';
import { createClient } from '@supabase/supabase-js';
import { readFileSync, writeFileSync, existsSync } from 'fs';
import { config } from 'dotenv';
config({ path: new URL('.env', import.meta.url).pathname, override: true });

const anthropic = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });
const supabase = createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_KEY);

const PDF_PATH = '/Users/nitishdugar/Downloads/RAS mains/2024/RAS prelims 2024.pdf';
const CACHE_PATH = './hindi-2024-extracted.json';

const BATCHES = [
  { start: 1,   end: 15  },
  { start: 16,  end: 30  },
  { start: 31,  end: 45  },
  { start: 46,  end: 56  },
  { start: 57,  end: 60  },
  { start: 61,  end: 75  },
  { start: 76,  end: 90  },
  { start: 91,  end: 105 },
  { start: 106, end: 120 },
  { start: 121, end: 135 },
  { start: 136, end: 150 },
];

async function extractBatch(pdfBase64, start, end) {
  const response = await anthropic.messages.create({
    model: 'claude-haiku-4-5-20251001',
    max_tokens: 8192,
    messages: [{
      role: 'user',
      content: [
        {
          type: 'document',
          source: {
            type: 'base64',
            media_type: 'application/pdf',
            data: pdfBase64,
          }
        },
        {
          type: 'text',
          text: `This is a bilingual RAS Prelims 2024 exam paper (Hindi-English).
Each page has TWO columns: Hindi on the LEFT, English on the RIGHT.

Extract ONLY the LEFT column (Hindi text) for questions ${start} to ${end}.

Output a JSON array where each element has:
{
  "q_no": <integer>,
  "question_hi": "<full Hindi question text, NO question number prefix>",
  "option_1_hi": "<answer option 1>",
  "option_2_hi": "<answer option 2>",
  "option_3_hi": "<answer option 3>",
  "option_4_hi": "<answer option 4>"
}

IMPORTANT RULES:
1. question_hi must contain the full question text including any A./B./C. statements and कूट section
2. option_1_hi through option_4_hi are the FINAL answer choices labeled (1)(2)(3)(4)
3. SKIP option (5) "अनुत्तरित प्रश्न" entirely
4. For सूची (match-list) questions, include the full table in question_hi like:
   "सूची-I को सूची-II से सुमेलित... | सूची-I (हेडर) | सूची-II (हेडर) | A. [text] | i. [text] | B. [text] | ii. [text] | ..."
   Then option_1_hi through option_4_hi are the कूट answer options
5. For कथन (statement) questions: include all A., B., C. statements in question_hi, then the कूट in question_hi too (like "नीचे दिए गए कूट का प्रयोग कर सही उत्तर चुनिए : (1) केवल A सही है। ..."), and option_1_hi through option_4_hi = the (1)(2)(3)(4) choices
6. Do NOT include English text
7. Output ONLY the raw JSON array — no markdown, no backticks, no explanation`
        }
      ]
    }]
  });

  let text = response.content[0].text.trim();
  // Strip markdown code fences if model adds them
  text = text.replace(/^```json\s*/i, '').replace(/^```\s*/i, '').replace(/\s*```$/i, '');
  try {
    return JSON.parse(text);
  } catch (e) {
    const { writeFileSync } = await import('fs');
    writeFileSync('./debug-raw.txt', text);
    console.error('  Parse error, raw response saved to debug-raw.txt');
    console.error('  Stop reason:', response.stop_reason, '| Text length:', text.length);
    throw e;
  }
}

async function upsertToSupabase(extracted) {
  const { data: rows, error } = await supabase
    .from('questions')
    .select('id, q_no')
    .eq('year', 2024);

  if (error) { console.error('DB fetch error:', error); process.exit(1); }

  const idMap = {};
  for (const row of rows) idMap[String(row.q_no)] = row.id;

  const updates = [];
  let matched = 0, unmatched = 0;

  for (const q of extracted) {
    const id = idMap[String(q.q_no)];
    if (!id) { console.warn(`  No DB row for q_no ${q.q_no}`); unmatched++; continue; }
    updates.push({
      id,
      question_hi: q.question_hi,
      option_1_hi: q.option_1_hi,
      option_2_hi: q.option_2_hi,
      option_3_hi: q.option_3_hi,
      option_4_hi: q.option_4_hi,
    });
    matched++;
  }

  console.log(`\nMatched: ${matched} | Unmatched: ${unmatched}`);

  // Use update (not upsert) to avoid nulling out existing required columns
  let success = 0, failed = 0;
  await Promise.all(updates.map(async (row) => {
    const { id, ...fields } = row;
    const { error: updateErr } = await supabase
      .from('questions')
      .update(fields)
      .eq('id', id);
    if (updateErr) { console.error(`  Update error q_no match id=${id}:`, updateErr.message); failed++; }
    else success++;
  }));
  console.log(`  Updated: ${success} | Failed: ${failed}`);
}

async function main() {
  // Resume from cache if partial run exists
  let allExtracted = [];
  if (existsSync(CACHE_PATH)) {
    allExtracted = JSON.parse(readFileSync(CACHE_PATH, 'utf8'));
    console.log(`Resumed from cache: ${allExtracted.length} questions already extracted`);
  }

  const extractedQnos = new Set(allExtracted.map(q => q.q_no));

  if (allExtracted.length < 150) {
    console.log('Reading PDF...');
    const pdfBase64 = readFileSync(PDF_PATH).toString('base64');
    console.log(`PDF base64 size: ${(pdfBase64.length / 1024 / 1024).toFixed(1)} MB\n`);

    for (const { start, end } of BATCHES) {
      // Skip batch if all questions already extracted
      if (extractedQnos.has(start) && extractedQnos.has(end)) {
        console.log(`Q${start}-${end}: skipped (cached)`);
        continue;
      }

      console.log(`Extracting Q${start}-${end}...`);
      try {
        const batch = await extractBatch(pdfBase64, start, end);
        console.log(`  Got ${batch.length} questions`);
        if (batch.length !== (end - start + 1)) {
          console.warn(`  Warning: expected ${end - start + 1}, got ${batch.length}`);
        }
        allExtracted.push(...batch);
        writeFileSync(CACHE_PATH, JSON.stringify(allExtracted, null, 2));
      } catch (err) {
        console.error(`  Failed on Q${start}-${end}:`, err.message);
        console.error('  Saving progress and exiting. Re-run to resume.');
        writeFileSync(CACHE_PATH, JSON.stringify(allExtracted, null, 2));
        process.exit(1);
      }
    }

    console.log(`\nTotal extracted: ${allExtracted.length} questions`);
  }

  // Sort by q_no for clean output
  allExtracted.sort((a, b) => a.q_no - b.q_no);

  // Verify completeness
  const missing = [];
  for (let i = 1; i <= 150; i++) {
    if (!allExtracted.find(q => q.q_no === i)) missing.push(i);
  }
  if (missing.length > 0) {
    console.warn(`Missing q_nos: ${missing.join(', ')}`);
  } else {
    console.log('All 150 questions present.');
  }

  console.log('\nUpserting to Supabase...');
  await upsertToSupabase(allExtracted);
  console.log('\nDone! Run generate-hindi-explanations-2024.js next for explanations.');
}

main().catch(console.error);
