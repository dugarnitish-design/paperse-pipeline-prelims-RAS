import { SMTPClient } from "https://deno.land/x/denomailer@1.6.0/mod.ts";

const GMAIL_USER = "dugarnitish@gmail.com";
const GMAIL_PASS = "xmqypvmvkkcjygii";
const TO = "contact@paperse.in";

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
const clip = (s: unknown, n: number) => String(s ?? "").slice(0, n);

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  try {
    const { from, subject, message } = await req.json();
    if (!from || !message) {
      return new Response(JSON.stringify({ error: "missing fields" }),
        { status: 400, headers: { ...cors, "Content-Type": "application/json" } });
    }
    const client = new SMTPClient({
      connection: { hostname: "smtp.gmail.com", port: 465, tls: true,
        auth: { username: GMAIL_USER, password: GMAIL_PASS } },
    });
    await client.send({
      from: `PaperSe Contact <${GMAIL_USER}>`,
      to: TO,
      replyTo: clip(from, 200),
      subject: `[PaperSe Contact] ${clip(subject, 150) || "New message"}`,
      content:
        `New contact-form submission on paperse.in\n\n` +
        `From: ${clip(from, 200)}\n` +
        `Subject: ${clip(subject, 150) || "(none)"}\n\n` +
        `Message:\n${clip(message, 5000)}\n`,
    });
    await client.close();
    return new Response(JSON.stringify({ ok: true }),
      { headers: { ...cors, "Content-Type": "application/json" } });
  } catch (e) {
    return new Response(JSON.stringify({ error: String(e) }),
      { status: 500, headers: { ...cors, "Content-Type": "application/json" } });
  }
});
