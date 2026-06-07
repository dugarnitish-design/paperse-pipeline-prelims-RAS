#!/usr/bin/env python3
"""
Curator Flask Server — Admin dashboard + Telegram webhook handler.

Run locally:
  python3 pipelines/curator_server.py

Railway: Set startCommand to "python3 pipelines/curator_server.py"
         Set env vars: CURATOR_PASSWORD, CURATOR_DASHBOARD_URL, FLASK_SECRET_KEY

Routes:
  GET  /                         → redirect to login
  GET  /curator/login            → login page
  POST /curator/login            → authenticate
  GET  /curator/<date>           → curator dashboard
  POST /curator/<date>/approve   → approve without edits → publish
  POST /curator/<date>/publish   → approve with edits → publish
  POST /telegram/callback        → Telegram webhook (button presses)
  GET  /set-webhook              → registers Railway URL as Telegram webhook (run once)
  GET  /health                   → health check

Setup:
  Add to .env:
    CURATOR_PASSWORD=your-secret-password
    CURATOR_CHAT_ID=your-personal-telegram-id
    CURATOR_DASHBOARD_URL=https://your-app.railway.app
    FLASK_SECRET_KEY=random-32-char-string
"""
import sys, os, json, datetime, subprocess, pathlib, threading
from functools import wraps

from flask import Flask, render_template, request, session, redirect, url_for, jsonify

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from pipelines import _common as C
from pipelines.curator_workflow import load_draft, mark_draft_status
from pipelines.curator_telegram import (
    answer_callback_query,
    edit_message_text,
    set_webhook,
    poll_updates,
    send_simple_message,
    notify_auto_published,
)
from pipelines.curator_learning import CuratorLearning


# ── Flask setup ───────────────────────────────────────────────────────────────

TEMPLATES_DIR = pathlib.Path(__file__).parent / "curator_templates"

app = Flask(__name__, template_folder=str(TEMPLATES_DIR))
app.secret_key = C.ENV.get("FLASK_SECRET_KEY", "dev-secret-key-change-in-prod")

CURATOR_PASSWORD = C.ENV.get("CURATOR_PASSWORD", "")
BOT_TOKEN        = C.ENV.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID    = C.ENV.get("CURATOR_CHAT_ID", "")

learning = CuratorLearning()


# ── auth helpers ──────────────────────────────────────────────────────────────

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("curator_authenticated"):
            return redirect(url_for("curator_login"))
        return f(*args, **kwargs)
    return decorated


# ── routes: auth ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return redirect(url_for("curator_login"))


@app.route("/curator/login", methods=["GET", "POST"])
def curator_login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if not CURATOR_PASSWORD:
            return render_template("login.html", error="CURATOR_PASSWORD not set in .env"), 500
        if password == CURATOR_PASSWORD:
            session["curator_authenticated"] = True
            date = request.form.get("date", datetime.date.today().isoformat())
            return redirect(url_for("curator_dashboard", date=date))
        return render_template("login.html", error="Incorrect password"), 401
    return render_template("login.html")


@app.route("/curator/logout")
def curator_logout():
    session.clear()
    return redirect(url_for("curator_login"))


# ── routes: dashboard ─────────────────────────────────────────────────────────

def _fetch_items(date: str, is_main: bool, limit: int = 5) -> list:
    """Load this day's EN items (main or also-in-news) straight from the DB."""
    try:
        return C.sb_select(
            "daily_ca_items",
            select="*",
            params={
                "date":     f"eq.{date}",
                "language": "eq.EN",
                "is_main":  f"eq.{'true' if is_main else 'false'}",
                "order":    "priority.desc.nullslast",
                "limit":    str(limit),
            },
        )
    except Exception as e:
        C.log(f"  ⚠ fetch {'main' if is_main else 'also'} items failed: {e}")
        return []


@app.route("/curator/<date>", methods=["GET"])
@require_auth
def curator_dashboard(date):
    draft  = load_draft(date)
    status = (draft or {}).get("status", "pending")

    # New flow: show the live TOP-5 (is_main) AND ALSO-IN-NEWS (5) side by side.
    main_items = _fetch_items(date, is_main=True,  limit=5)
    also_items = _fetch_items(date, is_main=False, limit=5)

    if not main_items and not also_items:
        return render_template(
            "dashboard.html",
            date=date, main_items=[], also_items=[], draft_status=status,
            error=f"No items found for {date}. Did the pipeline run?",
        ), 404

    return render_template(
        "dashboard.html",
        date=date,
        main_items=main_items,
        also_items=also_items,
        draft_status=status,
        error=None,
    )


@app.route("/curator/<date>/reject", methods=["POST"])
@require_auth
def reject_item(date):
    """
    Immediate per-item rejection (the curator unchecked a main item and picked a
    reason). Records to curator_feedback AND lowers ca_categories.tier_weight +
    topic_kb scores NOW — so RAG learns the moment the curator acts, not at publish.
    """
    data   = request.get_json(silent=True) or {}
    reason = (data.get("reason") or "").strip()
    if not reason:
        return jsonify({"error": "rejection reason required"}), 400

    result = learning.log_rejection(
        item_title=data.get("title", ""),
        category=data.get("category", ""),
        rejected_text=data.get("summary", ""),
        topic=data.get("topic", ""),
        rejection_reason=reason,
        item_id=data.get("id"),     # FIX 7: dashboard already sends the item id
        session_id=date,            # FIX 7: one RAG write per item per draft (date)
    )
    C.log(f"  ✓ immediate reject learned: {(data.get('title') or '')[:40]} · {reason}")
    return jsonify({"ok": True, "learning": result}), 200


@app.route("/curator/<date>/approve", methods=["POST"])
@require_auth
def approve_without_edits(date):
    """Approve top 5 as-is and publish to Telegram channel."""
    draft = load_draft(date)
    if not draft:
        return jsonify({"error": "Draft not found"}), 404

    items = draft.get("items", [])[:5]

    # Log approvals
    for item in items:
        learning.log_approval(
            item_title=item.get("title") or item.get("title_en", ""),
            category=item.get("category", ""),
            item_id=str(item.get("id") or "") or None,
            session_id=date,
        )

    # Mark approved
    mark_draft_status(date, "approved", {
        "approved_at": datetime.datetime.utcnow().isoformat() + "Z",
        "approval_method": "dashboard",
        "selected_indices": list(range(len(items))),
    })

    # Channel post is best-effort — approval is already recorded above.
    published = _publish(date)
    return jsonify({"status": "approved", "channel_posted": published, "date": date}), 200


def _set_status_with_hi(date: str, item: dict, status: str, is_main=None) -> None:
    """
    Set status (and optionally is_main) on an EN item by id AND its HI counterpart,
    so the two languages NEVER drift apart (e.g. EN promotes a story to main but HI
    is left as also-in-news). The HI row is matched on group_key — a stable shared
    key (source URL) stamped on both rows at insert. Legacy rows without group_key
    fall back to the old date+category+priority match. Never deletes — FIX 6 keeps
    every item so un-promoted also-in-news and rejected mains survive curation.
    Status values: 'published' | 'also_in_news' | 'rejected' (default 'pending').
    """
    patch = {"status": status}
    if is_main is not None:
        patch["is_main"] = is_main
    try:
        if item.get("id"):
            C.sb_update("daily_ca_items", patch, {"id": str(item["id"])})
        gk = item.get("group_key")
        if gk:
            # robust: HI row sharing the same source-story key
            C.sb_update("daily_ca_items", patch, {
                "date": date, "language": "HI", "group_key": gk})
        else:
            # legacy fallback (rows inserted before group_key existed)
            C.sb_update("daily_ca_items", patch, {
                "date": date, "language": "HI",
                "category": item.get("category", ""), "priority": item.get("priority"),
            })
    except Exception as e:
        C.log(f"  ⚠ could not set status={status} for {item.get('id')}: {e}")


@app.route("/curator/<date>/publish", methods=["POST"])
@require_auth
def publish_with_edits(date):
    """
    Publish a curator-chosen selection of items (the new flow).

    Form fields:
      selected_ids[]      — ids of items to PUBLISH (kept main + promoted also-in-news)
      title_edit_<id>     — edited title for a selected item (if changed)
      summary_edit_<id>   — edited summary for a selected item (if changed)

    Rejection reasons are NOT sent here — they are learned immediately when the
    curator unchecks an item (POST /curator/<date>/reject). This route applies the
    final selection to the DB, regenerates the PDF with the selected items only,
    and posts to the channel + PYQ polls.
    """
    selected_ids = [str(i) for i in request.form.getlist("selected_ids")]
    if len(selected_ids) < 3:
        return jsonify({"error": "Select at least 3 items to publish"}), 400

    # Load the live main + also items so we can act by id.
    main_items = _fetch_items(date, is_main=True,  limit=5)
    also_items = _fetch_items(date, is_main=False, limit=5)
    by_id = {str(it["id"]): it for it in (main_items + also_items)}

    sel_set    = set(selected_ids)
    selected   = [by_id[i] for i in selected_ids if i in by_id]              # checked (main + promoted)
    promoted   = [it for it in also_items if str(it["id"]) in sel_set]       # also → published main
    rejected   = [it for it in main_items if str(it["id"]) not in sel_set]   # unchecked main → rejected
    # FIX 2: un-promoted bench → also_in_news, BUT never resurrect an already-rejected
    # item. A previously-rejected story can sit in the is_main=false bucket on a re-publish;
    # without this guard it would be flipped back to 'also_in_news' and reappear in the PDF.
    kept_also  = [it for it in also_items if str(it["id"]) not in sel_set
                  and (it.get("status") or "") != "rejected"]

    # 1. Publish set (selected mains + promoted also-in-news) → status=published, is_main=true.
    for it in selected:
        _set_status_with_hi(date, it, status="published", is_main=True)
    for it in promoted:
        learning.log_replacement(
            old_item_title="(also-in-news promoted)",
            new_item_title=it.get("title") or it.get("title_en", ""),
            category=it.get("category", ""),
            item_id=str(it.get("id") or ""),
            session_id=date,
        )

    # 2. Apply title/summary edits to the selected items (EN rows).
    edits_made = 0
    for it in selected:
        iid = str(it.get("id") or "")
        if not iid:
            continue
        patch = {}
        nt = request.form.get(f"title_edit_{iid}", "").strip()
        ns = request.form.get(f"summary_edit_{iid}", "").strip()
        orig_title   = (it.get("title") or it.get("title_en", "")).replace("**", "").strip()
        orig_summary = (it.get("summary") or it.get("summary_en", "")).strip()
        if nt and nt != orig_title:   patch["title"] = nt
        if ns and ns != orig_summary: patch["summary"] = ns
        if patch:
            try:
                C.sb_update("daily_ca_items", patch, {"id": iid})
                edits_made += 1
            except Exception as e:
                C.log(f"  ⚠ edit update failed for id {iid}: {e}")

    # 3. Preserve the rest instead of deleting (FIX 6 — nothing is ever hard-deleted):
    #    - un-promoted also-in-news stay visible (status=also_in_news, is_main=false);
    #    - unchecked mains are demoted (is_main=false) out of every is_main=true
    #      consumer — website, channel, MCQs, PYQ polls, PDF main — and marked
    #      status=rejected; the PDF also-section filters status=rejected so they don't
    #      resurface there. RAG already learned the rejection at /reject (no re-learn).
    for it in kept_also:
        _set_status_with_hi(date, it, status="also_in_news", is_main=False)
    for it in rejected:
        _set_status_with_hi(date, it, status="rejected", is_main=False)
        C.log(f"  ✓ rejected (kept, demoted): {(it.get('title') or '')[:45]}")

    # 4. Log approvals for the published set.
    for it in selected:
        learning.log_approval(
            item_title=it.get("title") or it.get("title_en", ""),
            category=it.get("category", ""),
            item_id=str(it.get("id") or ""),
            session_id=date,
        )

    # 5. Regenerate the PDF from the now-curated DB (selected items only).
    env = {**os.environ,
           "DYLD_FALLBACK_LIBRARY_PATH": "/opt/homebrew/lib:" + os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")}
    C.log(f"  → Regenerating PDF for {date} ({len(selected)} selected, "
          f"{len(promoted)} promoted, {len(rejected)} rejected, "
          f"{len(kept_also)} also-kept, {edits_made} edited)")
    rgen = subprocess.run([sys.executable, str(C.ROOT / "pipelines" / "pdf_generator.py"), date],
                          cwd=str(C.ROOT), env=env, capture_output=True, text=True)
    if rgen.returncode != 0:
        # Don't post a stale/missing PDF and don't report false success — surface it.
        C.log(f"  ✗ PDF regen failed:\n{rgen.stderr[-600:]}")
        return jsonify({"error": "PDF regeneration failed — not published. Check server logs.",
                        "stage": "pdf_regen"}), 500

    mark_draft_status(date, "approved", {
        "approved_at": datetime.datetime.utcnow().isoformat() + "Z",
        "approval_method": "dashboard_selection",
        "selected_indices": selected_ids,
    })

    # 6. Channel post (regenerated PDF) + PYQ polls (fired inside _publish).
    published = _publish(date)
    payload = {
        "status": "approved" if published else "publish_failed",
        "channel_posted": published,
        "date": date,
        "selected": len(selected),
        "promoted": len(promoted),
        "rejected": len(rejected),
        "also_kept": len(kept_also),
        "edited": edits_made,
    }
    if not published:
        # Items + PDF are saved, but the channel post failed — report it, don't fake success.
        payload["error"] = "Channel post failed — items saved + PDF regenerated, but not sent. Check logs."
        return jsonify(payload), 502
    return jsonify(payload), 200


# ── routes: Telegram webhook ──────────────────────────────────────────────────

@app.route("/telegram/callback", methods=["POST"])
def telegram_callback():
    """
    Telegram sends a POST here when user presses an inline button.
    Register this URL via GET /set-webhook (once after deployment).
    """
    update = request.get_json(silent=True) or {}
    cq = update.get("callback_query", {})
    if not cq:
        return "ok"

    cq_id   = cq.get("id", "")
    cq_data = cq.get("data", "")    # "approve_2026-06-03" or "edit_2026-06-03"
    chat_id = str(cq.get("message", {}).get("chat", {}).get("id", ""))
    msg_id  = cq.get("message", {}).get("message_id")

    _handle_callback(cq_id, cq_data, chat_id, msg_id)
    return "ok", 200


@app.route("/set-webhook", methods=["GET"])
@require_auth
def setup_webhook():
    """Call once to register Railway URL as Telegram webhook."""
    webhook_url = C.ENV.get("CURATOR_DASHBOARD_URL", "")
    if not webhook_url:
        return "Set CURATOR_DASHBOARD_URL in .env first", 400
    result = set_webhook(f"{webhook_url.rstrip('/')}/telegram/callback")
    return jsonify(result)


# ── routes: misc ─────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.datetime.utcnow().isoformat()}), 200


# ── callback logic ────────────────────────────────────────────────────────────

def _handle_callback(cq_id: str, cq_data: str, chat_id: str, msg_id):
    """Process an inline-button callback (called from webhook OR polling thread)."""

    if cq_data.startswith("approve_"):
        date_str = cq_data.replace("approve_", "")
        draft = load_draft(date_str)
        if not draft:
            answer_callback_query(cq_id, "Draft not found")
            return

        status = draft.get("status", "pending")
        if status in ("published", "auto_published", "approved"):
            answer_callback_query(cq_id, f"Already {status}")
            edit_message_text(chat_id, msg_id, f"✅ {date_str} — already {status}.")
            return

        # Log approvals
        for item in (draft.get("items") or [])[:5]:
            learning.log_approval(
                item_title=item.get("title") or item.get("title_en", ""),
                category=item.get("category", ""),
            )

        mark_draft_status(date_str, "approved", {
            "approved_at": datetime.datetime.utcnow().isoformat() + "Z",
            "approval_method": "telegram",
        })

        answer_callback_query(cq_id, "Publishing now...")
        ok = _publish(date_str)
        text = f"✅ {date_str} published to channel!" if ok else f"⚠ {date_str} — publish failed. Check logs."
        edit_message_text(chat_id, msg_id, text)

    elif cq_data.startswith("edit_"):
        date_str = cq_data.replace("edit_", "")
        dashboard_url = C.ENV.get("CURATOR_DASHBOARD_URL", "http://localhost:5000")
        url = f"{dashboard_url.rstrip('/')}/curator/{date_str}"
        answer_callback_query(cq_id, "Opening dashboard...")
        draft = load_draft(date_str)
        ap_label = C.ist_label_from_iso((draft or {}).get("timeout_at", ""))
        ap_text = (f"⏰ Auto-publishes at {ap_label}." if ap_label
                   else "⏰ Auto-publishes at 6:00 AM IST if no response.")
        edit_message_text(
            chat_id,
            msg_id,
            f"✏️ Edit draft for {date_str}:\n{url}\n\n{ap_text}",
        )


def _post_pyq_polls(date_str: str) -> None:
    """Post the 2 daily PYQ quiz polls — called IMMEDIATELY after a successful
    publish so the PDF always lands in the channel first. Non-fatal on error."""
    try:
        from pipelines.pyq_poll_bot import run_daily_pyq_polls
        C.log(f"  → Posting PYQ polls for {date_str} (after publish)")
        run_daily_pyq_polls(date_str)
    except Exception as e:
        C.log(f"  ⚠ PYQ poll posting failed (non-fatal): {e}")


def _publish(date_str: str) -> bool:
    """Run telegram_delivery.py to post to the public channel. Returns True on success."""
    script = C.ROOT / "pipelines" / "telegram_delivery.py"
    result = subprocess.run(
        [sys.executable, str(script), date_str],
        capture_output=True,
        text=True,
        cwd=str(C.ROOT),
    )
    if result.returncode == 0:
        C.log(f"  ✓ Published {date_str} to channel")
        mark_draft_status(date_str, "published", {
            "published_at": datetime.datetime.utcnow().isoformat() + "Z",
        })
        # Re-upload the now-curated (regenerated) PDF to Supabase so paperse.in
        # serves exactly what went to the channel — never the pre-curation draft.
        try:
            from pipelines import upload_pdfs
            upload_pdfs.main(date_str)
        except Exception as e:
            C.log(f"  ⚠ PDF re-upload after publish failed (non-fatal): {e}")
        _post_pyq_polls(date_str)        # PDF first, polls immediately after
        return True
    else:
        C.log(f"  ✗ Publish failed:\n{result.stderr[-300:]}")
        return False


# ── Telegram polling thread (fallback when no webhook configured) ─────────────

def _register_webhook_on_startup():
    """Re-register the Telegram webhook on every server startup so the Approve/Edit
    buttons keep working after Railway restarts (the webhook is lost on restart)."""
    webhook_url = C.ENV.get("CURATOR_DASHBOARD_URL", "")
    if not webhook_url:
        C.log("  ⚠ CURATOR_DASHBOARD_URL not set — cannot register Telegram webhook on startup")
        return
    try:
        result = set_webhook(f"{webhook_url.rstrip('/')}/telegram/callback")
        ok = result.get("ok") if isinstance(result, dict) else result
        C.log(f"  ✓ Telegram webhook registered on startup → {ok}")
    except Exception as e:
        C.log(f"  ⚠ Webhook registration on startup failed: {e}")


def _start_telegram_polling():
    """
    Startup Telegram setup: always (re)register the webhook (so buttons survive
    Railway restarts), then optionally start a polling thread for local/no-webhook
    dev (CURATOR_POLLING=true).
    """
    _register_webhook_on_startup()

    if C.ENV.get("CURATOR_POLLING", "").lower() != "true":
        return

    C.log("  → Starting Telegram polling thread")
    offset = 0

    def poll():
        nonlocal offset
        while True:
            try:
                updates, offset = poll_updates(offset, timeout_secs=30)
                for upd in updates:
                    cq = upd.get("callback_query", {})
                    if cq:
                        _handle_callback(
                            cq.get("id", ""),
                            cq.get("data", ""),
                            str(cq.get("message", {}).get("chat", {}).get("id", "")),
                            cq.get("message", {}).get("message_id"),
                        )
            except Exception as e:
                C.log(f"  ⚠ Polling error: {e}")
                import time; time.sleep(5)

    t = threading.Thread(target=poll, daemon=True)
    t.start()


def _start_autopublish_scheduler():
    """
    Daily fixed-time auto-publish. Fires once per IST-day at AUTOPUBLISH_HOUR_UTC:
    AUTOPUBLISH_MIN_UTC (default 00:30 UTC = 6:00 AM IST) and calls
    curator_auto_publish.auto_publish(<today's IST date>).

    Replaces the old 2-hour rolling window. The fixed clock time IS the gate; the
    publish itself is idempotent (auto_publish no-ops if the draft was already
    approved/published), so a restart after the fire time re-checks safely rather
    than skipping or double-posting.
    """
    import time
    hour = int(C.ENV.get("AUTOPUBLISH_HOUR_UTC", "0"))     # 00:30 UTC = 6:00 AM IST
    minute = int(C.ENV.get("AUTOPUBLISH_MIN_UTC", "30"))
    IST = datetime.timedelta(hours=5, minutes=30)

    C.log(f"  → Starting auto-publish scheduler (daily at {hour:02d}:{minute:02d} UTC "
          f"= 6:00 AM IST)")

    def run():
        from pipelines.curator_auto_publish import auto_publish
        last_run_ist_date = None
        while True:
            try:
                now = datetime.datetime.now(datetime.timezone.utc)
                ist_date = (now + IST).date().isoformat()
                target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                # Fire when we're at/past today's target time and haven't run for
                # this IST date yet. Late-start-safe: a restart after the target
                # still fires (auto_publish no-ops if already handled).
                if now >= target and last_run_ist_date != ist_date:
                    last_run_ist_date = ist_date
                    C.log(f"  ⏰ [autopublish-scheduler] firing for {ist_date}")
                    auto_publish(ist_date)
            except Exception as e:
                C.log(f"  ⚠ [autopublish-scheduler] error: {e}")
            time.sleep(30)

    t = threading.Thread(target=run, daemon=True)
    t.start()


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _start_telegram_polling()
    port  = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    print(f"🚀 Curator server starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
