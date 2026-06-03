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
    TELEGRAM_ADMIN_CHAT_ID=your-personal-telegram-id
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
ADMIN_CHAT_ID    = C.ENV.get("TELEGRAM_ADMIN_CHAT_ID", "")

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
            item_title=item.get("title_en") or item.get("title", ""),
            category=item.get("category", ""),
        )

    # Mark approved
    mark_draft_status(date, "approved", {
        "approved_at": datetime.datetime.utcnow().isoformat() + "Z",
        "approval_method": "dashboard",
        "selected_indices": list(range(len(items))),
    })

    # Publish
    ok = _publish(date)
    return jsonify({"status": "published" if ok else "error", "date": date}), 200 if ok else 500


@app.route("/curator/<date>/publish", methods=["POST"])
@require_auth
def publish_with_edits(date):
    """
    Approve with custom selection (some items deselected, replacements added).
    Form fields:
      selected_items[]  — indices of top-5 items to KEEP (from 0-4)
      replacement_items[] — indices from items 5-7 to ADD
    """
    draft = load_draft(date)
    if not draft:
        return jsonify({"error": "Draft not found"}), 404

    all_items = draft.get("items", [])

    kept_idx   = [int(i) for i in request.form.getlist("selected_items")]
    added_idx  = [int(i) for i in request.form.getlist("replacement_items")]

    kept_items  = [all_items[i] for i in kept_idx if i < len(all_items)]
    added_items = [all_items[i] for i in added_idx if i < len(all_items)]

    # Items that were in top-5 but NOT in kept_items → rejected
    for i, item in enumerate(all_items[:5]):
        if i not in kept_idx:
            learning.log_rejection(
                item_title=item.get("title_en") or item.get("title", ""),
                category=item.get("category", ""),
                rejected_text=item.get("summary_en") or item.get("summary", ""),
                topic=item.get("topic", ""),
            )

    # Items added from candidates → log as "replaced" (original removed)
    for item in added_items:
        learning.log_replacement(
            old_item_title="(candidate added)",
            new_item_title=item.get("title_en") or item.get("title", ""),
            category=item.get("category", ""),
        )

    # Approve retained items
    for item in kept_items + added_items:
        learning.log_approval(
            item_title=item.get("title_en") or item.get("title", ""),
            category=item.get("category", ""),
        )

    # Save final selection to draft
    final_indices = kept_idx + added_idx
    mark_draft_status(date, "approved", {
        "approved_at": datetime.datetime.utcnow().isoformat() + "Z",
        "approval_method": "dashboard_edited",
        "selected_indices": final_indices,
    })

    # Publish
    ok = _publish(date)
    return jsonify({
        "status": "published" if ok else "error",
        "date": date,
        "kept": len(kept_items),
        "added": len(added_items),
        "rejected": 5 - len(kept_items),
    }), 200 if ok else 500


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
                item_title=item.get("title_en") or item.get("title", ""),
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
        edit_message_text(
            chat_id,
            msg_id,
            f"✏️ Edit draft for {date_str}:\n{url}\n\n⏰ Auto-publishes at 8:30 AM IST.",
        )


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
        return True
    else:
        C.log(f"  ✗ Publish failed:\n{result.stderr[-300:]}")
        return False


# ── Telegram polling thread (fallback when no webhook configured) ─────────────

def _start_telegram_polling():
    """
    Background thread that polls Telegram for button callbacks.
    Only active if CURATOR_POLLING=true in env (useful for local dev or no webhook).
    """
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
