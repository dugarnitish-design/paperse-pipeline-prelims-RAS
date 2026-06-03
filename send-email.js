import * as dotenv from "dotenv";
dotenv.config({ override: true });

import nodemailer from "nodemailer";
import fs from "fs";
import path from "path";

export async function sendDailyEmail({ dateStr, selected, pibCount, sujasAvailable }) {
  const pdfPath = path.resolve(`./outputs/${dateStr}/english.pdf`);
  const hindiPdfPath = path.resolve(`./outputs/${dateStr}/hindi.pdf`);
  if (!fs.existsSync(pdfPath)) throw new Error(`PDF not found: ${pdfPath}`);

  const transporter = nodemailer.createTransport({
    service: "gmail",
    auth: {
      user: process.env.GMAIL_USER,
      pass: process.env.GMAIL_APP_PASSWORD,
    },
  });

  const itemList = selected.map((s, i) => `  ${i + 1}. [${s.source}] ${s.subject} — ${s.title}`).join("\n");

  const info = await transporter.sendMail({
    from: `PaperSe Pipeline <${process.env.GMAIL_USER}>`,
    to: process.env.NOTIFICATION_EMAIL,
    subject: `PaperSe Daily — ${dateStr}`,
    text: `PaperSe daily PDFs are ready for review.

Date: ${dateStr}
PIB articles fetched: ${pibCount}
SUJAS available: ${sujasAvailable}
Items selected: ${selected.length}

${itemList}

English + Hindi PDFs attached. Review and tell Claude "upload today's" to publish.`,
    attachments: [
      {
        filename: `paperse-${dateStr}-english.pdf`,
        path: pdfPath,
        contentType: "application/pdf",
      },
      ...(fs.existsSync(hindiPdfPath) ? [{
        filename: `paperse-${dateStr}-hindi.pdf`,
        path: hindiPdfPath,
        contentType: "application/pdf",
      }] : []),
    ],
  });

  console.log(`→ Email sent: ${info.messageId}`);
}
