import axios from "axios";
import * as cheerio from "cheerio";

const PIB_BASE = "https://pib.gov.in";
const PIB_ALL_REL = "https://www.pib.gov.in/allRel.aspx?reg=3&lang=1";

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// Get yesterday's date (IST) — PIB publishes articles during the day,
// so running at night means today may have 0 articles
function getTargetDate() {
  const now = new Date();
  // If before 8 AM IST (2:30 UTC), use yesterday
  const istHour = (now.getUTCHours() + 5) % 24 + (now.getUTCMinutes() >= 30 ? 0 : 0);
  const istOffset = 5.5 * 60 * 60 * 1000;
  const ist = new Date(now.getTime() + istOffset);
  if (ist.getUTCHours() < 8) {
    ist.setUTCDate(ist.getUTCDate() - 1);
  }
  return {
    day: ist.getUTCDate(),
    month: ist.getUTCMonth() + 1,
    year: ist.getUTCFullYear(),
  };
}

async function fetchArticleContent(prid) {
  try {
    const url = `${PIB_BASE}/PressReleasePage.aspx?PRID=${prid}`;
    const res = await axios.get(url, {
      timeout: 10000,
      headers: {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
      },
    });
    const $ = cheerio.load(res.data);

    $("script, style, nav, footer, header, .menu, .navbar").remove();

    const title = $("h2").first().text().trim();

    let dateText = "";
    const fullText = $.text();
    const dateMatch = fullText.match(/Posted On:\s*(\d{1,2}\s+\w+\s+\d{4}\s+\d{1,2}:\d{2}[AP]M)/i);
    if (dateMatch) dateText = dateMatch[1];

    const paragraphs = [];
    $("p").each((_, el) => {
      const text = $(el).text().trim();
      if (
        text.length > 60 &&
        !text.includes("Posted On:") &&
        !text.includes("Release ID:") &&
        !text.includes("Visitor Counter") &&
        !text.includes("Read this release in") &&
        !text.includes("Follow us on") &&
        !text.includes("Visit us at") &&
        !text.includes("pic.twitter.com") &&
        !text.toLowerCase().includes("cookie") &&
        !text.toLowerCase().includes("javascript") &&
        !/^[*\-_\s]+$/.test(text)
      ) {
        const cleaned = text
          .replace(/https?:\/\/t\.co\/\S+/g, "")
          .replace(/@\w+/g, "")
          .replace(/\s{2,}/g, " ")
          .trim();
        if (cleaned.length > 60) paragraphs.push(cleaned);
      }
    });

    const content = paragraphs.join("\n\n");
    if (!content || content.length < 100) return null;

    return { prid, url, title, dateText, content: content.trim() };
  } catch (err) {
    console.error(`  ✗ Failed to fetch PRID ${prid}: ${err.message}`);
    return null;
  }
}

export async function fetchPIBArticles(overrideDate = null) {
  console.log("→ Fetching PIB article list...");

  const target = overrideDate || getTargetDate();
  console.log(`→ Target date: ${target.day}/${target.month}/${target.year}`);

  // Step 1: GET the page to obtain ViewState tokens
  const getRes = await axios.get(PIB_ALL_REL, {
    timeout: 15000,
    headers: {
      "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    },
  });

  const $get = cheerio.load(getRes.data);
  const viewstate     = $get("#__VIEWSTATE").val() || "";
  const viewstateGen  = $get("#__VIEWSTATEGENERATOR").val() || "";
  const eventVal      = $get("#__EVENTVALIDATION").val() || "";
  const vsEncrypted   = $get("input[name='__VIEWSTATEENCRYPTED']").val() || "";

  // Step 2: POST with date filter to load articles for target date
  const params = new URLSearchParams({
    "__VIEWSTATE":          viewstate,
    "__VIEWSTATEGENERATOR": viewstateGen,
    "__EVENTVALIDATION":    eventVal,
    "__VIEWSTATEENCRYPTED": vsEncrypted,
    "__EVENTTARGET":        "ctl00$ContentPlaceHolder1$ddlday",
    "__EVENTARGUMENT":      "",
    "__LASTFOCUS":          "",
    "script_HiddenField":   "",
    "ctl00$Bar1$ddlregion": "3",
    "ctl00$Bar1$ddlLang":   "1",
    "ctl00$ContentPlaceHolder1$hydregionid": "3",
    "ctl00$ContentPlaceHolder1$hydLangid":   "1",
    "ctl00$ContentPlaceHolder1$ddlMinistry": "0",
    "ctl00$ContentPlaceHolder1$ddlday":      String(target.day),
    "ctl00$ContentPlaceHolder1$ddlMonth":    String(target.month),
    "ctl00$ContentPlaceHolder1$ddlYear":     String(target.year),
  });

  const postRes = await axios.post(PIB_ALL_REL, params.toString(), {
    timeout: 20000,
    headers: {
      "User-Agent":   "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
      "Content-Type": "application/x-www-form-urlencoded",
      "Referer":      PIB_ALL_REL,
    },
  });

  const $ = cheerio.load(postRes.data);

  // Extract all PRID links
  const prids = [];
  $("a").each((_, el) => {
    const href = $(el).attr("href") || "";
    const match = href.match(/PRID=(\d+)/i);
    if (match) prids.push(match[1]);
  });

  const uniquePrids = [...new Set(prids)];
  console.log(`→ Found ${uniquePrids.length} articles on PIB`);

  // If 0 results and no override, try the day before the target as fallback
  if (uniquePrids.length === 0 && !overrideDate) {
    const fallback = new Date(Date.UTC(target.year, target.month - 1, target.day - 1));
    console.log("→ 0 articles found, falling back to previous day...");
    return fetchPIBArticles({
      day: fallback.getUTCDate(),
      month: fallback.getUTCMonth() + 1,
      year: fallback.getUTCFullYear(),
    });
  }

  // Fetch each article
  const articles = [];
  for (const prid of uniquePrids) {
    process.stdout.write(`  Fetching PRID ${prid}...`);
    const article = await fetchArticleContent(prid);
    if (article) {
      articles.push(article);
      console.log(` ✓ "${article.title?.slice(0, 60)}"`);
    } else {
      console.log(" skipped");
    }
    await sleep(300);
  }

  console.log(`\n→ Successfully fetched ${articles.length} PIB articles\n`);
  return articles;
}
