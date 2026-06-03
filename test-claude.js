import * as dotenv from "dotenv";
dotenv.config({ override: true });

import { fetchPIBArticles } from "./fetchers/pib.js";
import { fetchSujasContent } from "./fetchers/sujas.js";
import { runClaudeProcessor } from "./processors/claude.js";
import fs from "fs";

console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
console.log("  PaperSe — Claude Processor Test");
console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n");

// Step 1: Fetch sources
const [pibArticles, sujasResult] = await Promise.all([
  fetchPIBArticles(),
  fetchSujasContent(),
]);

// Step 2: Run Claude
const today = new Date().toLocaleDateString("en-IN", {
  day: "numeric", month: "long", year: "numeric",
});

const { rawOutput, selected, discarded, pyqsUsed } = await runClaudeProcessor(
  pibArticles,
  sujasResult,
  today
);

// Step 3: Save output
const dateStr = new Date().toISOString().split("T")[0];
const outDir = `./outputs/${dateStr}`;
fs.mkdirSync(outDir, { recursive: true });
fs.writeFileSync(`${outDir}/raw-output.txt`, rawOutput);

console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
console.log("  SUMMARY");
console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
console.log(`✓ PIB articles fetched: ${pibArticles.length}`);
console.log(`✓ SUJAS available: ${sujasResult.available}`);
console.log(`✓ Items selected: ${selected.length}`);
console.log(`✓ Items discarded: ${discarded?.length || 0}`);
console.log(`✓ PYQs used: ${pyqsUsed}`);
console.log(`✓ Output saved: ${outDir}/raw-output.txt`);
console.log("\n--- FIRST 800 CHARS OF OUTPUT ---\n");
console.log(rawOutput.slice(0, 800));
