import Anthropic from '@anthropic-ai/sdk';
import { createClient } from '@supabase/supabase-js';
import { writeFileSync, existsSync, readFileSync } from 'fs';
import { config } from 'dotenv';
config({ path: new URL('.env', import.meta.url).pathname, override: true });

const anthropic = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });
const supabase = createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_KEY);

const CACHE_PATH = './hindi-explanations-2024.json';
const BATCH_SIZE = 10; // questions per API call

async function generateExplanations(questions) {
  const questionsText = questions.map((q, i) =>
    `Q${q.q_no}: ${q.question_hi || q.question}
सही उत्तर: ${q.correct_text}
(English context: ${q.explanation || 'Not available'})`
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

  let text = response.content[0].text.trim();
  text = text.replace(/^```json\s*/i, '').replace(/^```\s*/i, '').replace(/\s*```$/i, '');
  return JSON.parse(text);
}

async function main() {
  // Load cache
  let cached = {};
  if (existsSync(CACHE_PATH)) {
    const arr = JSON.parse(readFileSync(CACHE_PATH, 'utf8'));
    for (const e of arr) cached[e.q_no] = e.explanation_hi;
    console.log(`Loaded ${Object.keys(cached).length} explanations from cache`);
  }

  // Fetch all 2024 questions
  const { data: rows, error } = await supabase
    .from('questions')
    .select('id, q_no, question, question_hi, correct_text, explanation')
    .eq('year', 2024)
    .order('q_no');

  if (error) { console.error('DB error:', error); process.exit(1); }
  console.log(`Fetched ${rows.length} questions from DB`);

  // Filter questions that need explanations
  const todo = rows.filter(r => !cached[parseInt(r.q_no)]);
  console.log(`Need explanations for: ${todo.length} questions\n`);

  const allResults = { ...cached };

  // Process in batches
  for (let i = 0; i < todo.length; i += BATCH_SIZE) {
    const batch = todo.slice(i, i + BATCH_SIZE);
    const qNos = batch.map(q => q.q_no).join(', ');
    console.log(`Generating explanations for Q${batch[0].q_no}-Q${batch[batch.length-1].q_no}...`);

    try {
      const results = await generateExplanations(batch);
      for (const r of results) allResults[r.q_no] = r.explanation_hi;
      // Save cache after each batch
      writeFileSync(CACHE_PATH, JSON.stringify(
        Object.entries(allResults).map(([q_no, explanation_hi]) => ({ q_no: parseInt(q_no), explanation_hi })),
        null, 2
      ));
      console.log(`  Done (${results.length} generated)`);
    } catch (err) {
      console.error(`  Failed on batch starting Q${batch[0].q_no}:`, err.message);
      console.error('  Progress saved. Re-run to resume.');
      process.exit(1);
    }
  }

  console.log(`\nTotal explanations: ${Object.keys(allResults).length}`);

  // Build id map
  const idMap = {};
  for (const row of rows) idMap[parseInt(row.q_no)] = row.id;

  // Update DB
  console.log('Updating DB...');
  let success = 0, failed = 0;

  const updates = Object.entries(allResults).map(([q_no, explanation_hi]) => ({
    id: idMap[parseInt(q_no)],
    explanation_hi,
  })).filter(u => u.id);

  await Promise.all(updates.map(async ({ id, explanation_hi }) => {
    const { error: upErr } = await supabase
      .from('questions')
      .update({ explanation_hi })
      .eq('id', id);
    if (upErr) { console.error(`  Update failed id=${id}:`, upErr.message); failed++; }
    else success++;
  }));

  console.log(`Updated: ${success} | Failed: ${failed}`);
  console.log('\nDone! Hindi explanations added to DB.');
}

main().catch(console.error);
