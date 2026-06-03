import { fetchPIBArticles } from "./fetchers/pib.js";
import fs from "fs";

console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
console.log("  PaperSe — PIB Fetcher Test");
console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n");

const articles = await fetchPIBArticles();

if (articles.length === 0) {
  console.log("✗ No articles fetched. Check internet connection.");
  process.exit(1);
}

// Print preview of first 3 articles
console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
console.log("  PREVIEW (first 3 articles)");
console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n");

articles.slice(0, 3).forEach((a, i) => {
  console.log(`[${i + 1}] ${a.title || "No title"}`);
  console.log(`    Date: ${a.dateText || "unknown"}`);
  console.log(`    URL:  ${a.url}`);
  console.log(`    Content preview: ${a.content.slice(0, 200)}...`);
  console.log();
});

// Save full output to file
const outPath = "./outputs/pib-test.json";
fs.mkdirSync("./outputs", { recursive: true });
fs.writeFileSync(outPath, JSON.stringify(articles, null, 2));
console.log(`✓ Full output saved to ${outPath}`);
console.log(`✓ Total articles: ${articles.length}`);
