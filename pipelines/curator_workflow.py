#!/usr/bin/env python3
"""
STEP 5 — Curator Approval Workflow.

  python3 pipelines/curator_workflow.py 2026-06-03

Saves draft items to Supabase `curator_drafts` table, then sends a Telegram
notification to the admin with [Approve & Publish] and [Edit & Approve] buttons.

The pipeline exits immediately. Publishing happens either when:
  a) Admin clicks "Approve" → curator_server.py webhook handles it
  b) Admin edits on dashboard → curator_server.py /publish handles it
  c) No response by 6:00 AM IST → curator_auto_publish.py, fired by the daily
     scheduler thread in curator_server (_start_autopublish_scheduler)
"""
import sys, json, datetime, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from pipelines import _common as C
from pipelines.curator_telegram import send_approval_message


# ── draft helpers ─────────────────────────────────────────────────────────────

def next_autopublish_utc(after=None):
    """
    The next fixed daily auto-publish slot (default 00:30 UTC = 6:00 AM IST) as a UTC
    datetime. Matches curator_server._start_autopublish_scheduler. Used only for the
    "auto-publishes at …" label shown to the curator (Telegram + dashboard).
    """
    after = after or datetime.datetime.utcnow()
    hour = int(C.ENV.get("AUTOPUBLISH_HOUR_UTC", "0"))     # 00:30 UTC = 6:00 AM IST
    minute = int(C.ENV.get("AUTOPUBLISH_MIN_UTC", "30"))
    slot = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if slot <= after:
        slot += datetime.timedelta(days=1)
    return slot


def save_draft(date_str: str, items: list, msg_result: dict, auto_publish_at=None) -> None:
    """
    Upsert draft record into Supabase `curator_drafts` table.
    Also saves a local JSON copy for fallback.
    """
    if auto_publish_at is None:
        auto_publish_at = next_autopublish_utc()
    timeout_at = auto_publish_at.isoformat() + "Z"

    row = {
        "date": date_str,
        "items": json.dumps(items, ensure_ascii=False),   # JSONB stored as string, Supabase handles it
        "status": "pending",
        "timeout_at": timeout_at,
        "telegram_message_id": msg_result.get("message_id"),
        "telegram_chat_id": msg_result.get("chat_id"),
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
    }

    try:
        C.sb_upsert("curator_drafts", row, on_conflict="date")
        C.log(f"  ✓ Draft saved to Supabase for {date_str}")
    except Exception as e:
        C.log(f"  ⚠ Supabase save failed: {e} — saving locally")

    # Local JSON fallback
    local_dir = C.ROOT / "outputs" / "curator" / "drafts"
    local_dir.mkdir(parents=True, exist_ok=True)
    (local_dir / f"{date_str}.json").write_text(
        json.dumps(row, indent=2, ensure_ascii=False)
    )


def load_draft(date_str: str) -> dict:
    """Load draft from Supabase (or local fallback). Returns {} if not found."""
    try:
        rows = C.sb_select("curator_drafts", params={"date": f"eq.{date_str}"})
        if rows:
            d = rows[0]
            # Parse items if stored as string
            if isinstance(d.get("items"), str):
                d["items"] = json.loads(d["items"])
            return d
    except Exception as e:
        C.log(f"  ⚠ Could not load draft from Supabase: {e}")

    # Local fallback
    local = C.ROOT / "outputs" / "curator" / "drafts" / f"{date_str}.json"
    if local.exists():
        return json.loads(local.read_text())
    return {}


def mark_draft_status(date_str: str, status: str, extra: dict = None) -> None:
    """Update draft status in Supabase."""
    patch = {"status": status, "updated_at": datetime.datetime.utcnow().isoformat() + "Z"}
    if extra:
        patch.update(extra)
    try:
        C.sb_update("curator_drafts", patch=patch, match={"date": date_str})
    except Exception as e:
        C.log(f"  ⚠ Could not update draft status: {e}")


# ── main ──────────────────────────────────────────────────────────────────────

def main(date_str: str) -> None:
    C.log(f"\n>>> curator_workflow.py — {date_str}")

    # Fetch top 8 items for this date (EN main, ordered by priority)
    try:
        items = C.sb_select(
            "daily_ca_items",
            select="*",
            params={
                "date":     f"eq.{date_str}",
                "language": "eq.EN",
                "is_main":  "eq.true",
                "order":    "priority.desc.nullslast",
                "limit":    "8",
            },
        )
    except Exception as e:
        C.log(f"  ✗ Cannot fetch items: {e}")
        sys.exit(1)

    if not items:
        C.log(f"  ✗ No EN items found for {date_str} — did daily_ca_pipeline.py run?")
        sys.exit(1)

    # Guard against duplicate curator messages: if a draft already exists for this
    # date in curator_drafts (e.g. a re-run, or another sender), skip sending.
    try:
        existing = C.sb_select("curator_drafts", select="date,status",
                               params={"date": f"eq.{date_str}"})
    except Exception as e:
        existing = None
        C.log(f"  ⚠ duplicate-check query failed (will proceed): {e}")
    if existing:
        C.log(f"  ⚠ Draft already exists for {date_str}, skipping duplicate.")
        return

    C.log(f"  Found {len(items)} items (will send top 5 for approval, items 6-8 as candidates)")

    # Auto-publish time = the fixed daily slot (6:00 AM IST = 00:30 UTC), single source
    # of truth for the Telegram message + draft.timeout_at (display only — the curator
    # service scheduler is what actually fires the publish).
    send_time = datetime.datetime.utcnow()
    auto_publish_at = next_autopublish_utc(send_time)

    # Send Telegram approval message
    msg_result = send_approval_message(date_str, items[:5], auto_publish_at=auto_publish_at)

    # Save draft (top 8 items)
    save_draft(date_str, items, msg_result, auto_publish_at=auto_publish_at)

    dashboard_url = C.ENV.get(
        "CURATOR_DASHBOARD_URL",
        f"http://localhost:5000/curator/{date_str}"
    )
    C.log(f"  ✓ Curator workflow initiated")
    C.log(f"  → Dashboard: {dashboard_url}")
    C.log(f"  → Auto-publishes at: {C.ist_label(auto_publish_at)} if no response")


if __name__ == "__main__":
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat()
    main(date)
