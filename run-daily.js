import * as dotenv from "dotenv";
dotenv.config({ override: true });

import { fetchPIBArticles } from "./fetchers/pib.js";
import { fetchSujasContent } from "./fetchers/sujas.js";
import { runClaudeProcessor } from "./processors/claude.js";
import { sendDailyEmail } from "./send-email.js";
import { execSync } from "child_process";
import fs from "fs";

const dateStr = new Date().toISOString().split("T")[0];
const logFile = `./logs/${dateStr}.log`;
fs.mkdirSync("./logs", { recursive: true });

function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}`;
  console.log(line);
  fs.appendFileSync(logFile, line + "\n");
}

try {
  log("=== PaperSe Daily Run Started ===");

  // Step 1: Fetch sources
  log("Fetching PIB articles...");
  const [pibArticles, sujasResult] = await Promise.all([
    fetchPIBArticles(),
    fetchSujasContent(),
  ]);
  log(`PIB: ${pibArticles.length} articles | SUJAS: ${sujasResult.available}`);

  // Step 2: Run Claude processor
  const today = new Date().toLocaleDateString("en-IN", {
    day: "numeric", month: "long", year: "numeric",
  });
  const { rawOutput, selected, discarded, pyqsUsed, mcqs } = await runClaudeProcessor(
    pibArticles,
    sujasResult,
    today
  );
  log(`Selected: ${selected.length} | Discarded: ${discarded?.length || 0} | PYQs: ${pyqsUsed} | MCQs: ${mcqs?.length || 0}`);

  // Step 3: Save raw output + MCQs
  const outDir = `./outputs/${dateStr}`;
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(`${outDir}/raw-output.txt`, rawOutput);
  fs.writeFileSync(`${outDir}/mcqs.json`, JSON.stringify(mcqs, null, 2));
  fs.writeFileSync(`${outDir}/selected.json`, JSON.stringify(selected, null, 2));
  log(`Raw output saved: ${outDir}/raw-output.txt`);
  log(`MCQs saved: ${outDir}/mcqs.json (${mcqs?.length || 0} questions)`);

  // Step 4: Generate PDFs
  const py = '/Library/Frameworks/Python.framework/Versions/3.14/bin/python3';
  execSync(`${py} generate-pdf.py ${dateStr}`, { stdio: "inherit", env: { ...process.env, DYLD_LIBRARY_PATH: '/opt/homebrew/lib' } });
  log(`English PDF generated: ${outDir}/english.pdf`);
  execSync(`${py} generate-pdf-hindi.py ${dateStr}`, { stdio: "inherit", env: { ...process.env, DYLD_LIBRARY_PATH: '/opt/homebrew/lib' } });
  log(`Hindi PDF generated: ${outDir}/hindi.pdf`);

  // Step 5: Send email for review (upload happens only after approval)
  await sendDailyEmail({
    dateStr,
    selected,
    pibCount: pibArticles.length,
    sujasAvailable: sujasResult.available,
  });
  log("Email sent for review. Awaiting approval before upload.");
  log("=== Done ===");

} catch (err) {
  log(`ERROR: ${err.message}`);
  process.exit(1);
}
