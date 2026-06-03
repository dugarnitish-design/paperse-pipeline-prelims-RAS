import { fetchSujasContent } from "./fetchers/sujas.js";

console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
console.log("  PaperSe — Sujas Fetcher Test");
console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n");

const result = await fetchSujasContent();

if (!result.available) {
  console.log("ℹ️  Drop a Sujas PDF into sujas-input/ folder and re-run.");
  process.exit(0);
}

console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
console.log("  PREVIEW (first 500 characters)");
console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n");
console.log(result.text.slice(0, 500));
console.log("\n...");
console.log(`\n✓ File: ${result.filename}`);
console.log(`✓ Pages: ${result.pages}`);
console.log(`✓ Total characters: ${result.text.length}`);
