/**
 * Update topic_hi in the syllabus table using the official RPSC Hindi syllabus PDF
 */

import Anthropic from '@anthropic-ai/sdk';
import { createClient } from '@supabase/supabase-js';
import { readFileSync } from 'fs';
import { config } from 'dotenv';
config({ path: new URL('.env', import.meta.url).pathname, override: true });

const anthropic = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });
const supabase = createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_KEY);

const PDF_PATH = '/Users/nitishdugar/Downloads/72EB70FF-69B4-4E9E-A7EE-8D71543BCE34.pdf';

async function main() {
  // 1. Fetch all existing syllabus topics from DB
  console.log('Fetching syllabus topics from DB...');
  const { data: rows, error } = await supabase
    .from('syllabus')
    .select('id, section, section_order, topic, topic_hi, topic_order')
    .order('section_order', { ascending: true })
    .order('topic_order', { ascending: true });

  if (error) { console.error('DB fetch error:', error.message); process.exit(1); }
  console.log(`Found ${rows.length} topics`);

  // Show current state
  const grouped = {};
  for (const r of rows) {
    if (!grouped[r.section]) grouped[r.section] = [];
    grouped[r.section].push(r);
  }
  console.log('\nSections:');
  for (const [sec, topics] of Object.entries(grouped)) {
    const withHi = topics.filter(t => t.topic_hi).length;
    console.log(`  ${sec}: ${topics.length} topics, ${withHi} with Hindi`);
  }

  // 2. Use Claude to match English topics to Hindi from the PDF
  console.log('\nReading PDF...');
  const pdfBase64 = readFileSync(PDF_PATH).toString('base64');

  const topicsJson = JSON.stringify(rows.map(r => ({
    id: r.id,
    section: r.section,
    section_order: r.section_order,
    topic_order: r.topic_order,
    topic: r.topic,
  })));

  console.log('Asking Claude to match topics to Hindi PDF content...');
  const response = await anthropic.messages.create({
    model: 'claude-sonnet-4-6',
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
          text: `This PDF is the official RPSC RAS Prelims syllabus in Hindi.

Below is a JSON array of existing syllabus topics in English from our database. Each has an id, section name, and topic text.

Your task: For each topic, find the corresponding Hindi text from the PDF and return it as topic_hi.

Rules:
1. Match by meaning/content — the Hindi and English describe the same syllabus point
2. Use EXACTLY the Hindi text from the PDF, word for word — do not rephrase or translate
3. If a topic has no clear match in the PDF, set topic_hi to null
4. Return ONLY a JSON array: [{"id": <id>, "topic_hi": "<exact Hindi text from PDF>"}, ...]
5. No markdown, no explanation

Existing topics:
${topicsJson}`
        }
      ]
    }]
  });

  let text = response.content[0].text.trim()
    .replace(/^```json\s*/i, '').replace(/^```\s*/i, '').replace(/\s*```$/i, '');

  const matches = JSON.parse(text);
  console.log(`Claude returned ${matches.length} matches`);

  // 3. Update DB
  let ok = 0, skipped = 0, fail = 0;
  for (const m of matches) {
    if (!m.topic_hi) { skipped++; continue; }
    const { error } = await supabase
      .from('syllabus')
      .update({ topic_hi: m.topic_hi })
      .eq('id', m.id);
    if (error) { console.error(`  fail id=${m.id}:`, error.message); fail++; }
    else ok++;
  }

  console.log(`\nDone: ${ok} updated, ${skipped} skipped (no match), ${fail} failed`);

  // Final check
  const { data: final } = await supabase.from('syllabus').select('id, topic_hi');
  const withHi = final.filter(r => r.topic_hi).length;
  console.log(`Total with topic_hi: ${withHi} / ${final.length}`);
}

main().catch(console.error);
