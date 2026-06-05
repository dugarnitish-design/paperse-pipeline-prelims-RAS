#!/usr/bin/env python3
"""
STEP 6 — Auto-publish fallback (run by Railway cron at 08:30 AM IST / 03:00 UTC).

  python3 pipelines/curator_auto_publish.py 2026-06-03

Checks if today's draft is still "pending" after the 2-hour window.
If so, publishes top 5 items as-is and logs auto_published=true.

If draft was already approved/published, logs "Late — already handled" and exits.
"""
import sys, datetime, json, pathlib, subprocess

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

    # Check timeout
    timeout_at_str = draft.get("timeout_at", "")
    if timeout_at_str:
        timeout_at = datetime.datetime.fromisoformat(timeout_at_str.replace("Z", "+00:00"))
        now = datetime.datetime.now(datetime.timezone.utc)
        if now < timeout_at:
            remaining = int((timeout_at - now).total_seconds() / 60)
            C.log(f"  ⏳ Timeout not reached — {remaining} min remaining. Exiting.")
            return

    # Timeout reached → auto-publish
    C.log(f"  ⏰ 2-hour timeout reached — auto-publishing top 5 items")

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

    # Mark draft as auto_published
    mark_draft_status(date_str, "auto_published", {
        "published_at": datetime.datetime.utcnow().isoformat() + "Z",
        "selected_indices": list(range(5)),
    })

    # Run telegram_delivery.py to post to channel
    C.log(f"  → Running telegram_delivery.py {date_str} ...")
    result = subprocess.run(
        [sys.executable, str(C.ROOT / "pipelines" / "telegram_delivery.py"), date_str],
        capture_output=True,
        text=True,
        cwd=str(C.ROOT),
    )
    if result.returncode == 0:
        C.log(f"  ✓ Published to Telegram channel")
        C.log(result.stdout[-500:] if result.stdout else "")
        # PDF first, PYQ polls immediately after (non-fatal)
        try:
            from pipelines.pyq_poll_bot import run_daily_pyq_polls
            C.log(f"  → Posting PYQ polls for {date_str} (after publish)")
            run_daily_pyq_polls(date_str)
        except Exception as e:
            C.log(f"  ⚠ PYQ poll posting failed (non-fatal): {e}")
    else:
        C.log(f"  ✗ telegram_delivery failed:\n{result.stderr[-300:]}")

    # Notify admin
    notify_auto_published(date_str)
    C.log(f"  ✓ Auto-publish complete — Log: 'Auto-published — no response'")


if __name__ == "__main__":
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat()
    auto_publish(date)
