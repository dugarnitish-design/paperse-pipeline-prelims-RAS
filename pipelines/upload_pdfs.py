#!/usr/bin/env python3
"""
STEP 6 — Upload the day's EN + HI PDFs to Supabase Storage and register them in
the `daily_pdfs` table so paperse.in can serve them.

  python3 pipelines/upload_pdfs.py 2026-06-06

Uploads to the public `daily-pdfs` bucket at EN/<date>.pdf and HI/<date>.pdf,
then upserts one row into daily_pdfs (date, english_url, hindi_url, items_count).
Idempotent: re-running overwrites the files (x-upsert) and updates the row
(on_conflict=date). Best-effort — never raises so it can't break the chain.

Also called from curator_server._publish() after a curator publish, so the
website always serves the CURATED (regenerated) PDF, never the pre-curation draft.
"""
import sys, datetime, pathlib
import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from pipelines import _common as C

BUCKET = "daily-pdfs"


def _storage_upload(local_path: pathlib.Path, dest: str):
    """Upload one file to the bucket (overwrite if present). Returns public URL or None."""
    if not local_path.exists():
        C.log(f"  ⚠ PDF not found, skipping: {local_path}")
        return None
    url = f"{C.SUPABASE_URL}/storage/v1/object/{BUCKET}/{dest}"
    headers = {
        "apikey": C.SERVICE_KEY,
        "Authorization": f"Bearer {C.SERVICE_KEY}",
        "Content-Type": "application/pdf",
        "x-upsert": "true",
    }
    try:
        r = requests.post(url, headers=headers, data=local_path.read_bytes())
    except Exception as e:
        C.log(f"  ⚠ upload error for {dest}: {e}")
        return None
    if r.status_code not in (200, 201):
        C.log(f"  ⚠ upload failed [{r.status_code}] for {dest}: {r.text[:200]}")
        return None
    public_url = f"{C.SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{dest}"
    C.log(f"  ✓ uploaded {dest}")
    return public_url


def main(date_str: str) -> None:
    C.log(f"\n>>> upload_pdfs.py — {date_str}")
    en_url = _storage_upload(C.OUT_EN / f"{date_str}.pdf", f"EN/{date_str}.pdf")
    hi_url = _storage_upload(C.OUT_HI / f"{date_str}.pdf", f"HI/{date_str}.pdf")

    if not en_url and not hi_url:
        C.log("  ✗ No PDFs uploaded — nothing to register in daily_pdfs.")
        return

    # items_count = number of published main items for the date (best-effort).
    try:
        items_count = C.sb_count("daily_ca_items", params={
            "date": f"eq.{date_str}", "language": "eq.EN", "is_main": "eq.true"})
    except Exception:
        items_count = None

    row = {
        "date": date_str,
        "english_url": en_url,
        "hindi_url": hi_url,
        "items_count": items_count,
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
    }
    try:
        C.sb_upsert("daily_pdfs", row, on_conflict="date")
        C.log(f"  ✓ daily_pdfs row upserted for {date_str} "
              f"(EN={'ok' if en_url else 'none'}, HI={'ok' if hi_url else 'none'}, items={items_count})")
    except Exception as e:
        C.log(f"  ⚠ daily_pdfs upsert failed: {e}")


if __name__ == "__main__":
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat()
    main(date)
