#!/usr/bin/env python3
"""
Curator Telegram helpers — send approval notifications and handle callbacks.
Uses plain `requests` (synchronous), no python-telegram-bot dependency needed.

Called from:
  curator_workflow.py   → send_approval_message()
  curator_server.py     → answer_callback_query(), edit_message_text(), poll_callbacks()
"""
import sys, requests, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from pipelines import _common as C


# ── helpers ───────────────────────────────────────────────────────────────────

def _bot_url(endpoint: str) -> str:
    token = C.ENV.get("TELEGRAM_BOT_TOKEN", "")
    return f"https://api.telegram.org/bot{token}/{endpoint}"

def _admin_chat() -> str:
    return C.ENV.get("CURATOR_CHAT_ID", "")


# ── outbound ──────────────────────────────────────────────────────────────────

def send_approval_message(date_str: str, items: list, auto_publish_at=None) -> dict:
    """
    Send draft-ready notification to admin with [Approve & Publish] [Edit & Approve] buttons.
    `items` should be the top-5 selected items (dicts with title_en, final_priority_score, source).
    `auto_publish_at` (UTC datetime) = the fixed daily slot (08:30 IST); shown as the auto-publish time.
    Returns {"message_id": int, "chat_id": str} or {"error": str}.
    """
    admin_chat = _admin_chat()
    if not admin_chat:
        C.log("  ⚠ CURATOR_CHAT_ID not set — skipping approval message")
        return {"error": "CURATOR_CHAT_ID not set"}

    dashboard_url = C.ENV.get(
        "CURATOR_DASHBOARD_URL",
        f"http://localhost:5000/curator/{date_str}"
    )

    # Build HTML message (safe — no escaping headaches like MarkdownV2)
    lines = [
        f"📋 <b>Daily CA Draft Ready — {date_str}</b>",
        f"",
        f"<b>Top 5 selected items:</b>",
    ]
    for i, item in enumerate(items[:5], 1):
        title = (item.get("title") or item.get("title_en", "Untitled"))[:70]
        title = title.replace("**", "").strip()           # strip markdown bold
        score = item.get("priority") or item.get("final_priority_score", "?")
        source = item.get("source", "?")
        lines.append(f"{i}. {title}")
        lines.append(f"   📊 Score: <code>{score}</code> | 📰 {source}")

    ap_label = C.ist_label(auto_publish_at) if auto_publish_at else "6:00 AM IST"
    lines += [
        "",
        "Items 6–8 available on dashboard for replacement.",
        f"⏰ Auto-publishes at {ap_label} if no response.",
        "",
        f'🔗 <a href="{dashboard_url}">Open Dashboard</a>',
    ]

    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Approve & Publish", "callback_data": f"approve_{date_str}"},
            {"text": "✏️ Edit & Approve",   "callback_data": f"edit_{date_str}"},
        ]]
    }

    resp = requests.post(
        _bot_url("sendMessage"),
        json={
            "chat_id": admin_chat,
            "text": "\n".join(lines),
            "parse_mode": "HTML",
            "reply_markup": keyboard,
            "disable_web_page_preview": True,
        },
        timeout=15,
    )

    if resp.ok:
        data = resp.json()
        msg_id = data["result"]["message_id"]
        C.log(f"  ✓ Telegram approval sent (message_id={msg_id})")
        return {"message_id": msg_id, "chat_id": admin_chat}
    else:
        C.log(f"  ✗ Telegram send failed [{resp.status_code}]: {resp.text[:200]}")
        return {"error": resp.text[:200]}


def send_simple_message(chat_id: str, text: str) -> None:
    """Send a plain text message to any chat."""
    requests.post(
        _bot_url("sendMessage"),
        json={"chat_id": chat_id, "text": text},
        timeout=10,
    )


def notify_auto_published(date_str: str) -> None:
    """Notify admin that auto-publish fired (no response by the 6:00 AM IST deadline)."""
    admin_chat = _admin_chat()
    if not admin_chat:
        return
    send_simple_message(
        admin_chat,
        f"⏰ Auto-published {date_str} — no response by 6:00 AM IST.\n"
        f"Log: curator_feedback (auto_published=true)\n"
        f"Top 5 items published as-is."
    )


# ── callback plumbing ─────────────────────────────────────────────────────────

def answer_callback_query(callback_query_id: str, text: str = "") -> None:
    """Dismiss the loading spinner on the Telegram button."""
    requests.post(
        _bot_url("answerCallbackQuery"),
        json={"callback_query_id": callback_query_id, "text": text, "show_alert": False},
        timeout=5,
    )


def edit_message_text(chat_id: str, message_id: int, text: str) -> None:
    """Replace the approval message text after action taken."""
    requests.post(
        _bot_url("editMessageText"),
        json={
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        },
        timeout=10,
    )


def set_webhook(webhook_url: str) -> dict:
    """Register webhook URL with Telegram. Call once after deployment."""
    resp = requests.get(
        _bot_url("setWebhook"),
        params={"url": webhook_url, "allowed_updates": '["callback_query"]'},
        timeout=10,
    )
    return resp.json()


def delete_webhook() -> dict:
    """Remove webhook (switches Telegram to polling mode)."""
    resp = requests.get(_bot_url("deleteWebhook"), timeout=10)
    return resp.json()


def poll_updates(offset: int = 0, timeout_secs: int = 30) -> tuple:
    """
    Long-poll Telegram for updates (callback_query only).
    Returns (list_of_updates, next_offset).
    Use in a background thread when webhook is not configured.
    """
    resp = requests.get(
        _bot_url("getUpdates"),
        params={
            "offset": offset,
            "timeout": timeout_secs,
            "allowed_updates": '["callback_query"]',
        },
        timeout=timeout_secs + 5,
    )
    if resp.ok:
        updates = resp.json().get("result", [])
        next_offset = (updates[-1]["update_id"] + 1) if updates else offset
        return updates, next_offset
    return [], offset
