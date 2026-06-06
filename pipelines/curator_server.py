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

@app.route("/curator/<date>", methods=["GET"])
@require_auth
def curator_dashboard(date):
    draft = load_draft(date)
    if not draft:
        return render_template(
            "dashboard.html",
            date=date,
            selected_items=[],
            candidate_items=[],
            error=f"No draft found for {date}. Did the pipeline run?",
        ), 404

    items = draft.get("items", [])
    status = draft.get("status", "pending")

    selected  = items[:5]
    candidates = items[5:8] if len(items) > 5 else []

    return render_template(
        "dashboard.html",
        date=date,
        selected_items=selected,
        candidate_items=candidates,
        draft_status=status,
        error=None,
    )


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


@app.route("/curator/<date>/publish", methods=["POST"])
@require_auth
def publish_with_edits(date):
    """
    Approve with custom selection + optional edits + rejection reasons.
    Form fields:
      selected_items[]        — indices of top-5 items to KEEP (0-4)
      replacement_items[]     — indices from items 5-7 to ADD
      title_edit_N            — edited title for item N (if changed)
      summary_edit_N          — edited summary for item N (if changed)
      rejection_reason_N      — rejection reason for unchecked item N
    """
    draft = load_draft(date)
    if not draft:
        return jsonify({"error": "Draft not found"}), 404

    all_items = draft.get("items", [])
    kept_idx  = [int(i) for i in request.form.getlist("selected_items")]
    added_idx = [int(i) for i in request.form.getlist("replacement_items")]

    kept_items  = [all_items[i] for i in kept_idx if i < len(all_items)]
    added_items = [all_items[i] for i in added_idx if i < len(all_items)]

    # Apply edits to kept items (update title/summary if changed)
    edits_made = 0
    for i, item in enumerate(all_items[:5]):
        if i in kept_idx:
            new_title   = request.form.get(f"title_edit_{i}", "").strip()
            new_summary = request.form.get(f"summary_edit_{i}", "").strip()
            orig_title   = (item.get("title") or item.get("title_en", "")).replace("**", "").strip()
            orig_summary = (item.get("summary") or item.get("summary_en", "")).strip()
            if new_title and new_title != orig_title:
                item["title"] = new_title
                edits_made += 1
            if new_summary and new_summary != orig_summary:
                item["summary"] = new_summary
                edits_made += 1

    # Rejected items → log with reason → RAG learns
    for i, item in enumerate(all_items[:5]):
        if i not in kept_idx:
            reason = request.form.get(f"rejection_reason_{i}", "")
            learning.log_rejection(
                item_title=item.get("title") or item.get("title_en", ""),
                category=item.get("category", ""),
                rejected_text=item.get("summary") or item.get("summary_en", ""),
                topic=item.get("topic", ""),
                rejection_reason=reason,
            )

    # Candidates added → log replacement
    for item in added_items:
        learning.log_replacement(
            old_item_title="(candidate added)",
            new_item_title=item.get("title") or item.get("title_en", ""),
            category=item.get("category", ""),
        )

    # Approve kept items
    for item in kept_items + added_items:
        learning.log_approval(
            item_title=item.get("title") or item.get("title_en", ""),
            category=item.get("category", ""),
        )

    # ── Apply the curation to the DB + regenerate the PDF so REJECTED items are
    #    truly gone from the website + PDF + channel, and EDITS are reflected. ──
    rejected_items = [all_items[i] for i in range(min(5, len(all_items))) if i not in kept_idx]
    changed = bool(rejected_items) or edits_made > 0

    # 1. Persist title/summary edits to the kept items (EN rows)
    for i in kept_idx:
        if i >= len(all_items):
            continue
        item = all_items[i]
        if not item.get("id"):
            continue
        patch = {}
        nt = request.form.get(f"title_edit_{i}", "").strip()
        ns = request.form.get(f"summary_edit_{i}", "").strip()
        if nt: patch["title"] = nt
        if ns: patch["summary"] = ns
        if patch:
            try:
                C.sb_update("daily_ca_items", patch, {"id": str(item["id"])})
            except Exception as e:
                C.log(f"  ⚠ edit update failed for id {item.get('id')}: {e}")

    # 2. Remove rejected items — EN row by id + the HI counterpart (matched on
    #    date+category+priority, which is unique among the day's main items).
    for item in rejected_items:
        try:
            if item.get("id"):
                C.sb_delete("daily_ca_items", {"id": str(item["id"])})
            C.sb_delete("daily_ca_items", {
                "date": date, "language": "HI", "is_main": "true",
                "category": item.get("category", ""), "priority": item.get("priority"),
            })
            C.log(f"  ✓ removed rejected item: {(item.get('title') or '')[:45]}")
        except Exception as e:
            C.log(f"  ⚠ could not remove rejected item: {e}")

    # 3. Regenerate the PDF from the now-curated DB (only if something changed)
    if changed:
        env = {**os.environ,
               "DYLD_FALLBACK_LIBRARY_PATH": "/opt/homebrew/lib:" + os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")}
        C.log(f"  → Regenerating PDF for {date} ({len(kept_items)} kept, {len(rejected_items)} removed)")
        rgen = subprocess.run([sys.executable, str(C.ROOT / "pipelines" / "pdf_generator.py"), date],
                              cwd=str(C.ROOT), env=env, capture_output=True, text=True)
        if rgen.returncode != 0:
            C.log(f"  ⚠ PDF regen failed:\n{rgen.stderr[-300:]}")

    final_indices = kept_idx + added_idx
    mark_draft_status(date, "approved", {
        "approved_at": datetime.datetime.utcnow().isoformat() + "Z",
        "approval_method": "dashboard_edited",
        "selected_indices": final_indices,
    })

    # Channel post — now posts the regenerated PDF (kept items only).
    published = _publish(date)
    return jsonify({
        "status": "approved",
        "channel_posted": published,
        "date": date,
        "kept": len(kept_items),
        "added": len(added_items),
        "rejected": 5 - len(kept_items),
        "edited": edits_made,
    }), 200


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
                   else "⏰ Auto-publishes 2 hours after the draft was sent.")
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


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _start_telegram_polling()
    port  = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    print(f"🚀 Curator server starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
