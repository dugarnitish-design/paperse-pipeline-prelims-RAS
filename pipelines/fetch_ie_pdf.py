#!/usr/bin/env python3
"""
STEP 1 — Fetch Indian Express PDF from Gmail.

  python3 pipelines/fetch_ie_pdf.py 2026-06-03

Four strategies tried in order:
  1. Direct PDF attachment in the IE ePaper email (older format)
  2. Self-forwarded email from GMAIL_USER with ie-delhi-*.pdf attachment
  3. PDF already in ~/Downloads with the right filename — copy it in
  4. IE email has SSO download link — open it in Chrome (user clicks once),
     watch Downloads folder for up to 60 s, then copy the PDF in

The first strategy that succeeds writes inputs/ie-pdf/YYYY-MM-DD.pdf and exits.
"""
import sys, datetime, imaplib, email, re, shutil, time, subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipelines import _common as C

DOWNLOADS = Path.home() / "Downloads"


# ── helpers ───────────────────────────────────────────────────────────────────

def _connect_imap():
    gmail_user = C.ENV.get("GMAIL_USER")
    gmail_password = C.ENV.get("GMAIL_APP_PASSWORD")
    if not gmail_user or not gmail_password:
        return None, None
    mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    mail.login(gmail_user, gmail_password)
    mail.select("INBOX")
    return mail, gmail_user


def _save(content: bytes, outfile: Path) -> bool:
    outfile.write_bytes(content)
    C.log(f"   ✓ IE PDF saved: {outfile.name}  ({len(content)/1024/1024:.1f} MB)")
    return True


def _extract_sso_link(msg) -> str | None:
    """Return the first SSO download link found in the email HTML body."""
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            html = part.get_payload(decode=True).decode("utf-8", errors="ignore")
            links = re.findall(r'href="(https?://[^"]+)"', html)
            for l in links:
                if "pdf-behind-sso" in l or "medilogySDK" in l:
                    return l
    return None


def _wait_for_download(filename_pattern: str, timeout: int = 60) -> Path | None:
    """Poll Downloads for a new PDF matching filename_pattern (up to timeout s)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        matches = list(DOWNLOADS.glob(filename_pattern))
        if matches:
            latest = max(matches, key=lambda p: p.stat().st_mtime)
            # Must be modified in the last 2 minutes (just downloaded)
            if time.time() - latest.stat().st_mtime < 120:
                return latest
        time.sleep(2)
    return None


# ── strategies ────────────────────────────────────────────────────────────────

def _strategy_ie_attachment(mail, news_date: datetime.date, outfile: Path) -> bool:
    """Strategy 1: PDF is a direct attachment in the IE ePaper email."""
    search_date = news_date.strftime("%d-%b-%Y")
    status, ids = mail.search(None, f'FROM "Indian Express" SINCE {search_date}')
    if status != "OK" or not ids[0]:
        return False
    for eid in reversed(ids[0].split()):
        _, data = mail.fetch(eid, "(RFC822)")
        msg = email.message_from_bytes(data[0][1])
        for part in msg.walk():
            if part.get_content_type() == "application/pdf" and part.get_filename():
                return _save(part.get_payload(decode=True), outfile)
    return False


def _strategy_self_forwarded(mail, gmail_user: str, news_date: datetime.date, outfile: Path) -> bool:
    """Strategy 2: User forwarded/emailed themselves a PDF with ie-delhi-*.pdf filename."""
    search_date = news_date.strftime("%d-%b-%Y")
    # Search all mailboxes including Sent
    for folder in ('"[Gmail]/Sent Mail"', "INBOX"):
        try:
            mail.select(folder)
        except Exception:
            continue
        # Search from self OR to self
        status, ids = mail.search(None, f'FROM "{gmail_user}" SINCE {search_date}')
        if status != "OK":
            continue
        for eid in reversed(ids[0].split() if ids[0] else []):
            _, data = mail.fetch(eid, "(RFC822)")
            msg = email.message_from_bytes(data[0][1])
            for part in msg.walk():
                if part.get_content_type() == "application/pdf":
                    fn = part.get_filename() or ""
                    date_str = news_date.strftime("%d-%m-%Y")
                    if f"ie-delhi-{date_str}" in fn.lower() or f"ie-delhi-{date_str}" in fn.lower():
                        return _save(part.get_payload(decode=True), outfile)
                    # Also accept any IE PDF with today's date in the name
                    if news_date.strftime("%d-%m") in fn and fn.lower().endswith(".pdf"):
                        return _save(part.get_payload(decode=True), outfile)
    mail.select("INBOX")
    return False


def _strategy_downloads_folder(news_date: datetime.date, outfile: Path) -> bool:
    """Strategy 3: PDF already in ~/Downloads (user may have clicked download earlier)."""
    date_str = news_date.strftime("%d-%m-%Y")
    patterns = [
        f"ie-delhi-{date_str}.pdf",
        f"ie-delhi-{date_str}*.pdf",
        f"*{date_str}*.pdf",
        f"*{news_date.isoformat()}*.pdf",
    ]
    for pat in patterns:
        matches = list(DOWNLOADS.glob(pat))
        if matches:
            src = max(matches, key=lambda p: p.stat().st_mtime)
            shutil.copy2(src, outfile)
            C.log(f"   ✓ Copied from Downloads: {src.name} → {outfile.name}")
            return True
    return False


def _strategy_open_sso_link(mail, news_date: datetime.date, outfile: Path) -> bool:
    """Strategy 4: Open the SSO link in Chrome (unlocks server-side session),
    then download the PDF directly via requests after a short wait.
    Falls back to watching the Downloads folder if the direct fetch fails."""
    import base64, re as _re
    search_date = news_date.strftime("%d-%b-%Y")
    status, ids = mail.search(None, f'FROM "Indian Express" SINCE {search_date}')
    if status != "OK" or not ids[0]:
        return False

    sso_link, pdf_url_direct = None, None
    for eid in reversed(ids[0].split()):
        _, data = mail.fetch(eid, "(RFC822)")
        msg = email.message_from_bytes(data[0][1])
        sso_link = _extract_sso_link(msg)
        if sso_link:
            # Decode the filepath parameter to get the direct PDF URL
            m = _re.search(r'filepath=([A-Za-z0-9+/=]+)', sso_link)
            if m:
                try:
                    pdf_url_direct = base64.b64decode(m.group(1)).decode()
                except Exception:
                    pass
            break

    if not sso_link:
        return False

    C.log(f"   Found SSO link — opening Chrome to authenticate…")
    subprocess.Popen(["open", "-a", "Google Chrome", sso_link])
    # Wait for Chrome to complete the SSO handshake (unlocks server-side session)
    time.sleep(8)

    # Try to download the PDF directly (Chrome SSO usually creates a short-lived
    # server-side whitelist that allows unauthenticated download for a brief window)
    if pdf_url_direct:
        try:
            import requests as _req
            r = _req.get(pdf_url_direct, timeout=60,
                         headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
            if r.status_code == 200 and r.content[:4] == b'%PDF':
                outfile.write_bytes(r.content)
                C.log(f"   ✓ IE PDF downloaded after SSO auth: {outfile.name} ({len(r.content)/1024/1024:.1f} MB)")
                return True
        except Exception as e:
            C.log(f"   ⚠ Direct fetch after SSO failed: {e}")

    # Fallback: watch Downloads folder (user may have clicked download in Chrome)
    C.log(f"   Watching Downloads for up to 60 s…")
    date_str = news_date.strftime("%d-%m-%Y")
    patterns = [f"ie-delhi-{date_str}*.pdf", f"*{date_str}*.pdf", f"*{news_date.isoformat()}*.pdf"]
    deadline = time.time() + 60
    while time.time() < deadline:
        for pat in patterns:
            matches = [p for p in DOWNLOADS.glob(pat) if time.time() - p.stat().st_mtime < 120]
            if matches:
                src = max(matches, key=lambda p: p.stat().st_mtime)
                shutil.copy2(src, outfile)
                C.log(f"   ✓ Caught download: {src.name} → {outfile.name}")
                return True
        time.sleep(2)

    C.log("   ⚠ Timed out. Manually place the PDF in uploads/ and re-run.")
    return False


# ── main entry ────────────────────────────────────────────────────────────────

def fetch_ie_pdf(news_date: datetime.date) -> bool:
    outfile = C.IE_DIR / f"{news_date.isoformat()}.pdf"
    if outfile.exists():
        C.log(f"   ✓ IE PDF already exists: {outfile.name}")
        return True

    # Strategy 3 first — fastest, no network needed
    C.log(f"   Checking Downloads folder for {news_date.isoformat()} PDF…")
    if _strategy_downloads_folder(news_date, outfile):
        return True

    # Also check uploads/ folder
    date_str = news_date.strftime("%d-%m-%Y")
    upload_path = C.UPLOADS / f"ie-delhi-{date_str}.pdf"
    if upload_path.exists():
        shutil.copy2(upload_path, outfile)
        C.log(f"   ✓ Copied from uploads: {upload_path.name}")
        return True

    # Connect IMAP for the remaining strategies
    mail, gmail_user = _connect_imap()
    if not mail:
        C.log("   ⚠ Gmail credentials missing — skipping IE PDF fetch.")
        return False

    C.log(f"   Gmail connected. Trying strategies for {news_date.isoformat()}…")
    try:
        # Strategy 1: direct PDF attachment
        C.log("   [1/3] Looking for PDF attachment in IE email…")
        if _strategy_ie_attachment(mail, news_date, outfile):
            return True

        # Strategy 2: self-forwarded PDF
        C.log("   [2/3] Looking for self-forwarded PDF email…")
        if _strategy_self_forwarded(mail, gmail_user, news_date, outfile):
            return True

        # Strategy 4: SSO link → open Chrome
        C.log("   [3/3] SSO download link — will open Chrome…")
        if _strategy_open_sso_link(mail, news_date, outfile):
            return True

    finally:
        try:
            mail.close()
            mail.logout()
        except Exception:
            pass

    C.log(f"   ⚠ All strategies failed for {news_date.isoformat()}.")
    C.log(f"   → Manually download and place at: {outfile}")
    C.log(f"   → Or email yourself the PDF and re-run.")
    return False


if __name__ == "__main__":
    # ── DEPRECATED 2026-06-04 ───────────────────────────────────────────────
    # The Gmail ePaper-PDF fetch was replaced by direct website scraping.
    # Use:  python3 pipelines/ie_scraper.py <date>   (Step 0 of run_daily.sh)
    # This script is kept for reference only and no longer runs.
    C.log("⚠ fetch_ie_pdf.py is DEPRECATED — IE is now scraped from the website.")
    C.log("  Run instead:  python3 pipelines/ie_scraper.py <YYYY-MM-DD>")
    sys.exit(0)
