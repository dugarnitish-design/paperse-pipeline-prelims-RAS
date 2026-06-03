#!/usr/bin/env python3
"""
STEP 1 — Fetch Indian Express PDF from Gmail.

  python3 pipelines/fetch_ie_pdf.py 2026-06-01

Searches Gmail inbox for Indian Express emails from the given date,
downloads PDF attachment, and saves as YYYY-MM-DD.pdf to inputs/ie-pdf/.
"""
import sys, datetime, imaplib, email, base64
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipelines import _common as C


def fetch_ie_pdf(news_date):
    """Fetch Indian Express PDF from Gmail for the given date.
    Returns True if successful, False if no attachment found.

    Args:
        news_date: datetime.date object for the date to fetch

    Returns:
        True if PDF found and saved, False if not found (logs warning)
    """
    # Get Gmail credentials from .env
    gmail_user = C.ENV.get("GMAIL_USER")
    gmail_password = C.ENV.get("GMAIL_APP_PASSWORD")

    if not gmail_user or not gmail_password:
        C.log(f"   ⚠ Gmail credentials missing (.env: GMAIL_USER, GMAIL_APP_PASSWORD). Skipping IE PDF fetch.")
        return False

    outfile = C.IE_DIR / f"{news_date.isoformat()}.pdf"
    if outfile.exists():
        C.log(f"   ✓ IE PDF already exists: {outfile.name}")
        return True

    try:
        # Connect to Gmail IMAP
        C.log(f"   Connecting to Gmail IMAP for {news_date.isoformat()}...")
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(gmail_user, gmail_password)
        mail.select("INBOX")

        # Search for emails from Indian Express with "ePaper" or "pdf" in subject
        # Since: searches from midnight of that day
        # Format: "DD-MMM-YYYY" (e.g., "01-Jun-2026")
        search_date = news_date.strftime("%d-%b-%Y")
        C.log(f"   Searching for Indian Express emails from {search_date}...")

        # Search criteria: from Indian Express, with ePaper or pdf in subject, since the date
        search_criteria = f'FROM "Indian Express" SINCE {search_date}'
        status, email_ids = mail.search(None, search_criteria)

        if status != "OK" or not email_ids[0]:
            C.log(f"   ⚠ No emails from Indian Express found for {search_date}")
            mail.close()
            mail.logout()
            return False

        # Get the most recent email (last in the list)
        email_list = email_ids[0].split()
        if not email_list:
            C.log(f"   ⚠ No emails from Indian Express found for {search_date}")
            mail.close()
            mail.logout()
            return False

        email_id = email_list[-1]  # Most recent
        C.log(f"   Found email ID {email_id.decode()}, fetching...")

        status, msg_data = mail.fetch(email_id, "(RFC822)")
        if status != "OK":
            C.log(f"   ⚠ Failed to fetch email")
            mail.close()
            mail.logout()
            return False

        # Parse email
        msg = email.message_from_bytes(msg_data[0][1])

        # Extract PDF attachment
        pdf_found = False
        for part in msg.walk():
            if part.get_content_type() == "application/pdf":
                filename = part.get_filename()
                if filename:
                    pdf_content = part.get_payload(decode=True)
                    outfile.write_bytes(pdf_content)
                    C.log(f"   ✓ IE PDF saved: {outfile.name} ({len(pdf_content) / 1024 / 1024:.1f} MB)")
                    pdf_found = True
                    break

        mail.close()
        mail.logout()

        if not pdf_found:
            C.log(f"   ⚠ No PDF attachment found in Indian Express email for {search_date}")
            return False

        return True

    except imaplib.IMAP4.error as e:
        C.log(f"   ⚠ Gmail IMAP error: {e}")
        return False
    except Exception as e:
        C.log(f"   ⚠ IE PDF fetch failed: {e}")
        return False


if __name__ == "__main__":
    news_date = C.parse_date(sys.argv[1]) if len(sys.argv) > 1 else (datetime.date.today() - datetime.timedelta(days=1))
    C.log("=" * 64)
    C.log(f"PaperSe Fetch IE PDF — {news_date.isoformat()}")
    C.log("=" * 64)
    success = fetch_ie_pdf(news_date)
    sys.exit(0 if success or (C.IE_DIR / f"{news_date.isoformat()}.pdf").exists() else 1)
