import * as dotenv from "dotenv";
dotenv.config({ override: true });

import { createClient } from "@supabase/supabase-js";
import fs from "fs";
import path from "path";

export async function uploadDailyMCQs({ dateStr }) {
  const supabase = createClient(
    process.env.SUPABASE_URL,
    process.env.SUPABASE_SERVICE_KEY
  );

  const mcqPath = path.resolve(`./outputs/${dateStr}/mcqs.json`);
  if (!fs.existsSync(mcqPath)) throw new Error(`MCQs not found: ${mcqPath}`);

  const mcqs = JSON.parse(fs.readFileSync(mcqPath, "utf8"));

  // Delete existing MCQs for this date first
  await supabase.from("daily_mcqs").delete().eq("date", dateStr);

  const rows = mcqs.map((q) => ({
    date:        dateStr,
    q_no:        q.q_no,
    question:    q.question,
    option_a:    q.option_a,
    option_b:    q.option_b,
    option_c:    q.option_c,
    option_d:    q.option_d,
    correct:     q.correct,
    explanation: q.explanation || null,
    subject:     q.subject || null,
  }));

  const { error } = await supabase.from("daily_mcqs").insert(rows);
  if (error) throw new Error(`MCQ insert failed: ${error.message}`);

  console.log(`→ Uploaded ${rows.length} MCQs for ${dateStr}`);
  return rows.length;
}

export async function uploadDailyPDFs({ dateStr, selected }) {
  const supabase = createClient(
    process.env.SUPABASE_URL,
    process.env.SUPABASE_SERVICE_KEY  // service role key needed for storage uploads
  );

  const englishPath = path.resolve(`./outputs/${dateStr}/english.pdf`);
  const hindiPath   = path.resolve(`./outputs/${dateStr}/hindi.pdf`);

  if (!fs.existsSync(englishPath)) throw new Error(`English PDF not found: ${englishPath}`);
  if (!fs.existsSync(hindiPath))   throw new Error(`Hindi PDF not found: ${hindiPath}`);

  // Upload English PDF
  const englishFile = fs.readFileSync(englishPath);
  const { error: engErr } = await supabase.storage
    .from("daily-pdfs")
    .upload(`${dateStr}-english.pdf`, englishFile, {
      contentType: "application/pdf",
      upsert: true,
    });
  if (engErr) throw new Error(`English upload failed: ${engErr.message}`);

  // Upload Hindi PDF
  const hindiFile = fs.readFileSync(hindiPath);
  const { error: hinErr } = await supabase.storage
    .from("daily-pdfs")
    .upload(`${dateStr}-hindi.pdf`, hindiFile, {
      contentType: "application/pdf",
      upsert: true,
    });
  if (hinErr) throw new Error(`Hindi upload failed: ${hinErr.message}`);

  // Get public URLs
  const { data: engUrl } = supabase.storage.from("daily-pdfs").getPublicUrl(`${dateStr}-english.pdf`);
  const { data: hinUrl } = supabase.storage.from("daily-pdfs").getPublicUrl(`${dateStr}-hindi.pdf`);

  // Insert/upsert record in daily_pdfs table
  const { error: dbErr } = await supabase
    .from("daily_pdfs")
    .upsert({
      date:         dateStr,
      english_url:  engUrl.publicUrl,
      hindi_url:    hinUrl.publicUrl,
      items_count:  selected.length,
    }, { onConflict: "date" });
  if (dbErr) throw new Error(`DB insert failed: ${dbErr.message}`);

  console.log(`→ Uploaded to Supabase:`);
  console.log(`  English: ${engUrl.publicUrl}`);
  console.log(`  Hindi:   ${hinUrl.publicUrl}`);

  return { englishUrl: engUrl.publicUrl, hindiUrl: hinUrl.publicUrl };
}
