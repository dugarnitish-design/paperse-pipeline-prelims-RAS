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
    return C.ENV.get("TELEGRAM_ADMIN_CHAT_ID", "")


# ── outbound ──────────────────────────────────────────────────────────────────

def send_approval_message(date_str: str, items: list) -> dict:
    """
    Send draft-ready notification to admin with [Approve & Publish] [Edit & Approve] buttons.
    `items` should be the top-5 selected items (dicts with title_en, final_priority_score, source).
    Returns {"message_id": int, "chat_id": str} or {"error": str}.
    """
    admin_chat = _admin_chat()
    if not admin_chat:
        C.log("  ⚠ TELEGRAM_ADMIN_CHAT_ID not set — skipping approval message")
        return {"error": "TELEGRAM_ADMIN_CHAT_ID not set"}

    dashboard_url = C.ENV.get(
        "CURATOR_DASHBOARD_URL",
        f"http://localhost:5000/curator/{date_str}"
    )

    lines = [
        f"📋 *Daily CA Draft Ready — {date_str}*",
        f"",
        f"Top 5 selected items:",
    ]
    for i, item in enumerate(items[:5], 1):
        title = (item.get("title_en") or item.get("title", "Untitled"))[:70]
        score = item.get("final_priority_score") or item.get("score", "?")
        source = item.get("source", "?")
        lines.append(f"{i}\\. {title}")
        lines.append(f"   Score: `{score}` | Source: {source}")

    lines += [
        "",
        f"Items 6\\-8 available on dashboard for replacement\\.",
        f"⏰ Auto\\-publishes in 2 hours if no response\\.",
        f"",
        f"[Open Dashboard]({dashboard_url})",
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
            "parse_mode": "MarkdownV2",
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
    """Notify admin that auto-publish fired (no response in 2 hours)."""
    admin_chat = _admin_chat()
    if not admin_chat:
        return
    send_simple_message(
        admin_chat,
        f"⏰ Auto-published {date_str} — no response in 2 hours.\n"
        f"Log: curator_feedback (auto_published=true)"
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
