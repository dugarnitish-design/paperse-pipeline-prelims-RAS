// Usage: node upload-daily.js [YYYY-MM-DD]
// If no date provided, uses today's date
import * as dotenv from "dotenv";
dotenv.config({ override: true });

import { uploadDailyPDFs, uploadDailyMCQs } from "./upload-to-supabase.js";
import fs from "fs";

const dateStr = process.argv[2] || new Date().toISOString().split("T")[0];
const outDir = `./outputs/${dateStr}`;

if (!fs.existsSync(outDir)) {
  console.error(`No outputs found for ${dateStr} at ${outDir}`);
  process.exit(1);
}

// Check what's available
const hasMCQs = fs.existsSync(`${outDir}/mcqs.json`);

console.log(`\nUploading ${dateStr}...`);

// Load selected items for items_count
const selectedPath = `${outDir}/selected.json`;
const selected = fs.existsSync(selectedPath) ? JSON.parse(fs.readFileSync(selectedPath, "utf8")) : [];

// Upload PDFs
const { englishUrl, hindiUrl } = await uploadDailyPDFs({ dateStr, selected });

// Upload MCQs if available
if (hasMCQs) {
  await uploadDailyMCQs({ dateStr });
} else {
  console.log("→ No mcqs.json found — skipping MCQ upload");
}

console.log(`\nDone. Live at paperse.in/current-affairs`);
console.log(`  English: ${englishUrl}`);
console.log(`  Hindi:   ${hindiUrl}`);
