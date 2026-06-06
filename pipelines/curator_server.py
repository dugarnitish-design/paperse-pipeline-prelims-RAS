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


def _delete_with_hi(date: str, item: dict) -> None:
    """Delete an EN item by id + its HI counterpart (matched on date+category+priority)."""
    try:
        if item.get("id"):
            C.sb_delete("daily_ca_items", {"id": str(item["id"])})
        C.sb_delete("daily_ca_items", {
            "date": date, "language": "HI",
            "category": item.get("category", ""), "priority": item.get("priority"),
        })
    except Exception as e:
        C.log(f"  ⚠ could not remove item {item.get('id')}: {e}")


def _set_main_with_hi(date: str, item: dict, is_main: bool) -> None:
    """Flip is_main on an EN item + its HI counterpart (for promote/demote)."""
    flag = "true" if is_main else "false"
    try:
        if item.get("id"):
            C.sb_update("daily_ca_items", {"is_main": is_main}, {"id": str(item["id"])})
        C.sb_update("daily_ca_items", {"is_main": is_main}, {
            "date": date, "language": "HI",
            "category": item.get("category", ""), "priority": item.get("priority"),
        })
    except Exception as e:
        C.log(f"  ⚠ could not set is_main={flag} for {item.get('id')}: {e}")


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

    sel_set      = set(selected_ids)
    selected     = [by_id[i] for i in selected_ids if i in by_id]
    promoted     = [it for it in also_items if str(it["id"]) in sel_set]   # also → main
    removed      = [it for it in (main_items + also_items) if str(it["id"]) not in sel_set]

    # 1. Promote selected also-in-news items into the main set.
    for it in promoted:
        _set_main_with_hi(date, it, is_main=True)
        learning.log_replacement(
            old_item_title="(also-in-news promoted)",
            new_item_title=it.get("title") or it.get("title_en", ""),
            category=it.get("category", ""),
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

    # 3. Remove every unselected item (rejected mains + un-promoted also) so the
    #    PDF + website + channel carry the SELECTED items only. Rejections were
    #    already learned via /reject, so we only delete here (no double-learning).
    for it in removed:
        _delete_with_hi(date, it)
        C.log(f"  ✓ removed unselected item: {(it.get('title') or '')[:45]}")

    # 4. Log approvals for the published set.
    for it in selected:
        learning.log_approval(
            item_title=it.get("title") or it.get("title_en", ""),
            category=it.get("category", ""),
        )

    # 5. Regenerate the PDF from the now-curated DB (selected items only).
    env = {**os.environ,
           "DYLD_FALLBACK_LIBRARY_PATH": "/opt/homebrew/lib:" + os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")}
    C.log(f"  → Regenerating PDF for {date} ({len(selected)} selected, "
          f"{len(promoted)} promoted, {len(removed)} removed, {edits_made} edited)")
    rgen = subprocess.run([sys.executable, str(C.ROOT / "pipelines" / "pdf_generator.py"), date],
                          cwd=str(C.ROOT), env=env, capture_output=True, text=True)
    if rgen.returncode != 0:
        C.log(f"  ⚠ PDF regen failed:\n{rgen.stderr[-300:]}")

    mark_draft_status(date, "approved", {
        "approved_at": datetime.datetime.utcnow().isoformat() + "Z",
        "approval_method": "dashboard_selection",
        "selected_ids": selected_ids,
    })

    # 6. Channel post (regenerated PDF) + PYQ polls (fired inside _publish).
    published = _publish(date)
    return jsonify({
        "status": "approved",
        "channel_posted": published,
        "date": date,
        "selected": len(selected),
        "promoted": len(promoted),
        "removed": len(removed),
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
