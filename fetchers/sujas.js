import fs from "fs";
import path from "path";
import { execSync } from "child_process";
import os from "os";
import Anthropic from "@anthropic-ai/sdk";
import * as dotenv from "dotenv";
dotenv.config({ override: true });

const SUJAS_FOLDER = path.resolve("./sujas-input");

function getAnthropic() {
  return new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });
}

function getLatestPDF() {
  if (!fs.existsSync(SUJAS_FOLDER)) {
    fs.mkdirSync(SUJAS_FOLDER, { recursive: true });
  }

  const files = fs.readdirSync(SUJAS_FOLDER)
    .filter((f) => f.toLowerCase().endsWith(".pdf"))
    .map((f) => ({
      name: f,
      path: path.join(SUJAS_FOLDER, f),
      mtime: fs.statSync(path.join(SUJAS_FOLDER, f)).mtime,
    }))
    .sort((a, b) => b.mtime - a.mtime);

  return files.length > 0 ? files[0] : null;
}

async function ocrPageWithClaude(imagePath, pageNum, totalPages) {
  const imageData = fs.readFileSync(imagePath);
  const base64 = imageData.toString("base64");

  const response = await getAnthropic().messages.create({
    model: "claude-sonnet-4-6",
    max_tokens: 2000,
    messages: [
      {
        role: "user",
        content: [
          {
            type: "image",
            source: {
              type: "base64",
              media_type: "image/png",
              data: base64,
            },
          },
          {
            type: "text",
            text: `This is page ${pageNum} of ${totalPages} from the SUJAS (Suchana evam Jan Sampark Vibhag) daily Hindi current affairs bulletin published by the Rajasthan government. Extract ALL the Hindi/Devanagari text from this image exactly as written. Include all headings, subheadings, bullet points, dates, numbers, and body text. Preserve the structure as much as possible. Return only the extracted text, nothing else.`,
          },
        ],
      },
    ],
  });

  return response.content[0].text;
}

export async function fetchSujasContent() {
  console.log("→ Checking sujas-input folder for PDF...");

  const latest = getLatestPDF();

  if (!latest) {
    console.log("⚠️  No SUJAS PDF found in sujas-input/ — will run PIB only.");
    return { available: false, text: null, filename: null };
  }

  const sizeKB = (fs.statSync(latest.path).size / 1024).toFixed(1);
  console.log(`→ Found: ${latest.name} (${sizeKB} KB)`);
  console.log("→ Converting PDF pages to images for OCR...");

  const tempDir = path.join(os.tmpdir(), `sujas-ocr-${Date.now()}`);
  fs.mkdirSync(tempDir, { recursive: true });

  try {
    // Convert PDF to PNG images (200 DPI — good balance of quality vs file size)
    const outputPrefix = path.join(tempDir, "page");
    try {
      execSync(`pdftoppm -r 200 -png "${latest.path}" "${outputPrefix}"`, {
        stdio: "pipe",
      });
    } catch {
      throw new Error(
        "pdftoppm not found. Install it with: brew install poppler"
      );
    }

    // Collect and sort generated page images
    const pageFiles = fs
      .readdirSync(tempDir)
      .filter((f) => f.endsWith(".png"))
      .sort();

    if (pageFiles.length === 0) {
      throw new Error("pdftoppm produced no images — PDF may be corrupted.");
    }

    console.log(
      `→ Converted ${pageFiles.length} pages. Running Claude Vision OCR...`
    );

    const pageTexts = [];
    for (let i = 0; i < pageFiles.length; i++) {
      const imagePath = path.join(tempDir, pageFiles[i]);
      console.log(`  → OCR page ${i + 1}/${pageFiles.length}...`);
      const text = await ocrPageWithClaude(imagePath, i + 1, pageFiles.length);
      pageTexts.push(text);
    }

    const text = pageTexts
      .join("\n\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();

    console.log(
      `→ OCR complete. Extracted ${text.length} characters from ${pageFiles.length} pages\n`
    );

    return {
      available: true,
      text,
      filename: latest.name,
      pages: pageFiles.length,
    };
  } finally {
    // Always clean up temp images
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
}
