#!/usr/bin/env python3
"""
STEP 6 — Auto-publish fallback.

  python3 pipelines/curator_auto_publish.py 2026-06-03

Triggered at a FIXED time (08:30 AM IST = 03:00 UTC) by the daily scheduler thread
inside the curator service (see curator_server._start_autopublish_scheduler). The
scheduler's clock IS the gate — this function no longer checks a rolling timeout.

If today's draft is still "pending" when called, publishes the top 5 items as-is and
logs auto_published=true. If it was already approved/published/auto_published, it's a
no-op (so a service restart or manual re-run never double-publishes).
"""
import os, sys, datetime, json, pathlib, subprocess

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from pipelines import _common as C
from pipelines.curator_workflow import load_draft, mark_draft_status
from pipelines.curator_telegram import notify_auto_published
from pipelines.curator_learning import CuratorLearning


def auto_publish(date_str: str) -> None:
    C.log(f"\n>>> curator_auto_publish.py — {date_str}")

    draft = load_draft(date_str)
    if not draft:
        C.log(f"  ✗ No draft found for {date_str}. Nothing to do.")
        return

    status = draft.get("status", "pending")

    if status in ("approved", "published", "auto_published"):
        C.log(f"  ✓ Draft already {status} — no action needed.")
        return

    # Fixed-time trigger (08:30 IST). Draft is still pending → auto-publish top 5.
    C.log(f"  ⏰ Fixed auto-publish time reached — publishing top 5 items")

    items = draft.get("items", [])
    if not items:
        C.log(f"  ✗ No items in draft. Cannot publish.")
        return

    # Log auto-publish to curator_feedback
    learning = CuratorLearning()
    for item in items[:5]:
        try:
            C.sb_insert("curator_feedback", {
                "date": date_str,
                "item_title": item.get("title_en") or item.get("title", ""),
                "category": item.get("category", ""),
                "action": "approved",
                "auto_published": True,
            }, returning=False)
        except Exception as e:
            C.log(f"  ⚠ feedback log failed: {e}")

    # ── CRITICAL FIX: regenerate the PDF BEFORE delivery (mirror the manual /publish
    #    endpoint). Railway containers do NOT persist local PDF files between runs, so
    #    by the time this scheduler fires, outputs/daily-ca/{EN,HI}/<date>.pdf is gone
    #    and telegram_delivery silently posts nothing. Regenerate from the DB first.
    #    Also: mark the draft published ONLY AFTER a confirmed channel post, so a
    #    failure leaves it 'pending' for a retry instead of masking as 'auto_published'
    #    (that masking is exactly why June 7 + June 8 never reached the channel).
    env = {**os.environ,
           "DYLD_FALLBACK_LIBRARY_PATH": "/opt/homebrew/lib:" + os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")}
    C.log(f"  → Regenerating PDF for {date_str} before delivery …")
    rgen = subprocess.run(
        [sys.executable, str(C.ROOT / "pipelines" / "pdf_generator.py"), date_str],
        capture_output=True, text=True, cwd=str(C.ROOT), env=env)
    if rgen.returncode != 0:
        C.log(f"  ✗ PDF regen failed — NOT publishing (draft stays pending for retry):\n{rgen.stderr[-400:]}")
        return
    C.log(f"  ✓ PDF regenerated")

    # Deliver to the Telegram channel.
    C.log(f"  → Running telegram_delivery.py {date_str} …")
    result = subprocess.run(
        [sys.executable, str(C.ROOT / "pipelines" / "telegram_delivery.py"), date_str],
        capture_output=True, text=True, cwd=str(C.ROOT))
    if result.returncode != 0:
        C.log(f"  ✗ telegram_delivery failed — draft stays pending for retry:\n{result.stderr[-400:]}")
        return
    C.log(f"  ✓ Published to Telegram channel")
    C.log(result.stdout[-500:] if result.stdout else "")

    # Confirmed channel post → NOW mark auto_published (never before delivery).
    mark_draft_status(date_str, "auto_published", {
        "published_at": datetime.datetime.utcnow().isoformat() + "Z",
        "selected_indices": list(range(min(5, len(items)))),
    })

    # Re-upload the fresh PDF to the website + post PYQ polls (match manual _publish).
    try:
        from pipelines import upload_pdfs
        upload_pdfs.main(date_str)
    except Exception as e:
        C.log(f"  ⚠ PDF upload failed (non-fatal): {e}")
    try:                              # §5C — record topic coverage on publish
        from pipelines.daily_ca_pipeline import record_topic_coverage
        record_topic_coverage(date_str)
    except Exception as e:
        C.log(f"  ⚠ coverage update failed (non-fatal): {e}")
    try:                              # §7B — learn the curator standard into content_rag
        from pipelines.daily_ca_pipeline import record_content_rag
        record_content_rag(date_str)
    except Exception as e:
        C.log(f"  ⚠ content_rag update failed (non-fatal): {e}")
    try:
        from pipelines.pyq_poll_bot import run_daily_pyq_polls
        C.log(f"  → Posting PYQ polls for {date_str} (after publish)")
        run_daily_pyq_polls(date_str)
    except Exception as e:
        C.log(f"  ⚠ PYQ poll posting failed (non-fatal): {e}")

    # Notify admin
    notify_auto_published(date_str)
    C.log(f"  ✓ Auto-publish complete")


if __name__ == "__main__":
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat()
    auto_publish(date)
