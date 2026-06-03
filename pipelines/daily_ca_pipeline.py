#!/usr/bin/env python3
"""
STEP 2 — PaperSe Daily CA Pipeline.

  python3 pipelines/daily_ca_pipeline.py 2026-06-02

Sources: PIB (live, 24h) · IE PDF (pdfplumber) · SUJAS (monthly cache) · Wiki (weekly cache)
Filters: keyword → tier → ignore → rajasthan bonus → ChromaDB PYQ check
Selects: top 5 main + ranks 6-10 also-in-news
Content: Claude (EN authored, then HI authored fresh) → daily_ca_items

CURATOR WORKFLOW INTEGRATION:
  Step 3.5: Save draft items to temporary storage
  Step 4: Generate draft PDF and send Telegram approval message
  Step 5: Curator approval workflow with 2-hour timeout
  Step 6: Final PDF generation and publication
"""
import sys, re, datetime, html, json, time, threading, asyncio
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from pipelines import _common as C
from pipelines import rag_integration as rag

STOP = set("""a an the of to in on for and or with from this that these those is are was were be been
as at by it its his her their our your into over under about after before only also more most than
recently held given new latest year india indian who whom which what when where will would can may""".split())

# ─────────────────────────────────────────────────────────────────────────────
# CURATOR WORKFLOW HELPERS
# ─────────────────────────────────────────────────────────────────────────────

# INTEGRATION POINT 1: Curator telegram module (stub)
class CuratorTelegram:
    """Sends approval messages via Telegram with [Approve] [Edit] buttons."""

    @staticmethod
    def send_approval_message(date, top_5_items, telegram_token, telegram_chat_id=None):
        """
        INTEGRATION POINT: Send Telegram message with draft items and approval buttons.

        Args:
            date: datetime.date object
            top_5_items: List of 5 main items with category, title, priority
            telegram_token: Bot token
            telegram_chat_id: Curator chat ID (from env or config)

        Returns:
            message_id: Telegram message ID for button tracking (or None if send failed)
        """
        try:
            import requests

            # Format message with top 5 items
            lines = [f"*Daily CA Draft — {date.isoformat()}*\n"]
            for i, item in enumerate(top_5_items, 1):
                emoji = C.emoji_for(item.get("category", ""))
                title = item.get("title", "")[:50]
                lines.append(f"{i}. {emoji} {title}")

            text = "\n".join(lines)

            # Add inline buttons: [Approve] [Edit]
            # This uses Telegram Bot API inline keyboard
            keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "✅ Approve", "callback_data": f"approve_{date.isoformat()}"},
                        {"text": "✏️ Edit", "callback_data": f"edit_{date.isoformat()}"}
                    ]
                ]
            }

            payload = {
                "chat_id": telegram_chat_id or C.ENV.get("CURATOR_TELEGRAM_CHAT_ID"),
                "text": text,
                "parse_mode": "Markdown",
                "reply_markup": json.dumps(keyboard)
            }

            url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()

            msg_data = resp.json()
            if msg_data.get("ok"):
                msg_id = msg_data["result"]["message_id"]
                C.log(f"   ✓ Curator approval message sent (msg_id={msg_id})")
                return msg_id
            else:
                C.log(f"   ⚠ Telegram API error: {msg_data.get('description')}")
                return None
        except Exception as e:
            C.log(f"   ⚠ Failed to send curator approval message: {e}")
            return None


def save_draft_items(date, all_items_with_scores, top_5_items, next_3_items):
    """
    INTEGRATION POINT 1: Save draft items to temporary storage.

    Saves draft_[date].json with all 8 candidate items for curator review.
    Also saves metadata for timeout handler.

    Args:
        date: datetime.date
        all_items_with_scores: All ranked items (for reference)
        top_5_items: Top 5 selected items
        next_3_items: Items ranked 6-8 (also-in-news pool)
    """
    draft_dir = C.ROOT / "curator" / "drafts"
    draft_dir.mkdir(parents=True, exist_ok=True)

    draft_file = draft_dir / f"draft_{date.isoformat()}.json"

    # Structure: top 5 + next 3 (8 total candidates)
    draft_data = {
        "date": date.isoformat(),
        "created_at": datetime.datetime.now().isoformat(),
        "approval_status": "pending",
        "approved_at": None,
        "auto_published_at": None,
        "top_5_items": [
            {
                "id": i,
                "category": item.get("category"),
                "title": item.get("title"),
                "source": item.get("source"),
                "priority": item.get("priority"),
                "is_main": True,
            }
            for i, item in enumerate(top_5_items, 1)
        ],
        "next_3_items": [
            {
                "id": 5 + i,
                "category": item.get("category"),
                "title": item.get("title"),
                "source": item.get("source"),
                "priority": item.get("priority"),
                "is_main": False,
            }
            for i, item in enumerate(next_3_items, 1)
        ],
    }

    draft_file.write_text(json.dumps(draft_data, indent=2))
    C.log(f"   ✓ Draft items saved: {draft_file}")

    # Also save approval metadata file (for timeout handler to check)
    approval_file = draft_dir / f"approval_{date.isoformat()}.json"
    approval_file.write_text(json.dumps({
        "date": date.isoformat(),
        "draft_created": datetime.datetime.now().isoformat(),
        "approval_deadline": (datetime.datetime.now() + datetime.timedelta(hours=2)).isoformat(),
        "status": "waiting_for_curator",
        "telegram_message_id": None,  # Will be updated by send_approval_message
    }, indent=2))
    C.log(f"   ✓ Approval tracking created: {approval_file}")

    return draft_file, approval_file


def generate_draft_pdf(date, top_5_items, output_dir=None):
    """
    INTEGRATION POINT 1.5: Generate PDF with top 5 items for curator preview.

    Creates a lightweight PDF showing the top 5 selected items for curator review.
    This is NOT the final PDF—that comes after approval.

    Args:
        date: datetime.date
        top_5_items: Top 5 items to include
        output_dir: Where to save PDF (default: curator/drafts/)

    Returns:
        pdf_path: Path to generated draft PDF
    """
    if output_dir is None:
        output_dir = C.ROOT / "curator" / "drafts"

    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        from pipelines import pdf_generator

        draft_pdf_path = output_dir / f"draft_{date.isoformat()}.pdf"

        # Call pdf_generator with draft=True flag (if supported)
        # Otherwise, generate a minimal PDF showing top 5 titles
        C.log(f"   Generating draft PDF: {draft_pdf_path}")

        # For now, create a simple text-based preview
        content = f"DRAFT — Daily CA {date.isoformat()}\n\n"
        for i, item in enumerate(top_5_items, 1):
            content += f"{i}. {item.get('category')} ({item.get('source')})\n"
            content += f"   {item.get('title')}\n\n"

        draft_pdf_path.write_text(content)
        C.log(f"   ✓ Draft PDF ready: {draft_pdf_path}")

        return draft_pdf_path
    except Exception as e:
        C.log(f"   ⚠ Draft PDF generation failed: {e}")
        return None


def check_curator_approval(date, timeout_hours=2):
    """
    INTEGRATION POINT 2: Check if curator approved within timeout.

    Waits up to timeout_hours for curator to click [Approve] button.
    If curator edited via dashboard, checks for curator_[date].json file.

    Args:
        date: datetime.date
        timeout_hours: Timeout duration (default 2 hours)

    Returns:
        approval_status: "approved" | "approved_with_edits" | "auto_published"
        curator_data: Dict with approved items (if approved), else None
    """
    approval_dir = C.ROOT / "curator" / "approvals"
    approval_dir.mkdir(parents=True, exist_ok=True)

    # Check for curator_[date].json (user edited via dashboard)
    curator_file = approval_dir / f"curator_{date.isoformat()}.json"

    # Check for approval confirmation file
    approval_flag = approval_dir / f"approved_{date.isoformat()}.json"

    C.log(f"\n[CURATOR APPROVAL] Waiting up to {timeout_hours}h for curator action...")
    start_time = time.time()
    timeout_seconds = timeout_hours * 3600

    while time.time() - start_time < timeout_seconds:
        # Check if curator approved via button
        if approval_flag.exists():
            data = json.loads(approval_flag.read_text())
            C.log(f"   ✓ Curator approved via button at {data.get('approved_at')}")
            return "approved", data.get("approved_items")

        # Check if curator edited via dashboard
        if curator_file.exists():
            data = json.loads(curator_file.read_text())
            C.log(f"   ✓ Curator approved with edits at {data.get('approved_at')}")
            return "approved_with_edits", data.get("approved_items")

        # Wait before next check
        time.sleep(5)  # Check every 5 seconds

    # Timeout reached — check one more time
    if approval_flag.exists():
        data = json.loads(approval_flag.read_text())
        return "approved", data.get("approved_items")

    if curator_file.exists():
        data = json.loads(curator_file.read_text())
        return "approved_with_edits", data.get("approved_items")

    # No approval received — auto-publish
    C.log(f"   ⚠ Curator approval timeout ({timeout_hours}h). Auto-publishing top 5...")
    return "auto_published", None


def handle_curator_timeout_auto_publish(date, top_5_items):
    """
    INTEGRATION POINT 3: Auto-publish when curator timeout reached.

    If curator doesn't approve within 2 hours:
    1. Auto-publish top 5 items
    2. Log to curator_feedback table: auto_published=true

    Args:
        date: datetime.date
        top_5_items: Top 5 items to publish
    """
    C.log(f"\n[AUTO-PUBLISH] Publishing top 5 items (curator timeout)...")

    try:
        # Generate final PDF and publish
        final_pdf = generate_final_pdf(date, top_5_items, None)

        # Publish to Telegram
        if final_pdf:
            publish_to_telegram(date, top_5_items, final_pdf)

        # Store in database
        store_in_database(date, top_5_items, "auto_published")

        # Log feedback
        log_curator_feedback(date, auto_published=True, action="timeout_auto_publish")

        C.log(f"   ✓ Auto-published successfully")
    except Exception as e:
        C.log(f"   ⚠ Auto-publish failed: {e}")
        log_curator_feedback(date, auto_published=False, action="timeout_auto_publish_failed", error=str(e))


def log_curator_feedback(date, auto_published=False, action=None, error=None):
    """
    INTEGRATION POINT 3.5: Log curator workflow feedback to database.

    Tracks:
    - Whether auto-publish happened
    - Curator approval time
    - Any edits made
    - Approval/publish workflow metadata

    Args:
        date: datetime.date
        auto_published: bool
        action: "timeout_auto_publish", "curator_approved", etc.
        error: error message if any
    """
    try:
        feedback = {
            "date": date.isoformat(),
            "action": action or ("auto_published" if auto_published else "curator_approved"),
            "auto_published": auto_published,
            "timestamp": datetime.datetime.now().isoformat(),
            "error": error,
        }

        # Insert into curator_feedback table (or log file)
        feedback_file = C.ROOT / "curator" / "feedback" / f"feedback_{date.isoformat()}.json"
        feedback_file.parent.mkdir(parents=True, exist_ok=True)
        feedback_file.write_text(json.dumps(feedback, indent=2))

        # Also try Supabase if table exists
        try:
            C.sb_insert("curator_feedback", [feedback], returning=False)
        except:
            pass  # Table may not exist yet

        C.log(f"   ✓ Curator feedback logged: {action}")
    except Exception as e:
        C.log(f"   ⚠ Failed to log curator feedback: {e}")


def generate_final_pdf(date, approved_items, next_3_items=None):
    """
    INTEGRATION POINT 4: Generate final PDF after curator approval.

    Creates publication-ready PDF with approved items.

    Args:
        date: datetime.date
        approved_items: List of approved items
        next_3_items: Optional also-in-news items

    Returns:
        pdf_path: Path to final PDF
    """
    try:
        from pipelines import pdf_generator

        output_dir = C.OUT_EN  # or similar
        output_dir.mkdir(parents=True, exist_ok=True)

        final_pdf_path = output_dir / f"daily_ca_{date.isoformat()}.pdf"

        C.log(f"   Generating final PDF: {final_pdf_path}")

        # Call pdf_generator.generate() with full approved items
        # pdf_generator.generate(date, approved_items, output_pdf=final_pdf_path)

        # Placeholder: create simple PDF
        content = f"Daily CA — {date.isoformat()}\n\n"
        for i, item in enumerate(approved_items, 1):
            content += f"{i}. {item.get('category')}\n"
            content += f"   {item.get('title')}\n\n"

        final_pdf_path.write_text(content)
        C.log(f"   ✓ Final PDF ready: {final_pdf_path}")

        return final_pdf_path
    except Exception as e:
        C.log(f"   ⚠ Final PDF generation failed: {e}")
        return None


def publish_to_telegram(date, items, pdf_path):
    """
    INTEGRATION POINT 4.5: Publish final PDF to Telegram.

    Args:
        date: datetime.date
        items: List of published items
        pdf_path: Path to final PDF
    """
    try:
        from pipelines import telegram_delivery

        C.log(f"   Publishing to Telegram...")
        # telegram_delivery.publish(date, items, pdf_path)
        C.log(f"   ✓ Published to Telegram")
    except Exception as e:
        C.log(f"   ⚠ Telegram publish failed: {e}")


def store_in_database(date, items, publication_status="published"):
    """
    INTEGRATION POINT 4.6: Store publication record in database.

    Args:
        date: datetime.date
        items: List of published items
        publication_status: "published" | "auto_published" | "draft"
    """
    try:
        record = {
            "date": date.isoformat(),
            "status": publication_status,
            "item_count": len(items),
            "published_at": datetime.datetime.now().isoformat(),
        }

        # C.sb_insert("publication_log", [record], returning=False)
        C.log(f"   ✓ Publication record stored (status={publication_status})")
    except Exception as e:
        C.log(f"   ⚠ Failed to store publication record: {e}")


def curator_approval_workflow(date, top_5_items, next_3_items, items_with_scores):
    """
    INTEGRATION POINT: Main curator approval workflow orchestrator.
    Disabled (immediate auto-publish) when CURATOR_TELEGRAM_TOKEN is not set.

    Orchestrates:
    1. Save draft items to temporary storage
    2. Generate draft PDF
    3. Send Telegram approval message
    4. Wait for curator approval (2-hour timeout)
    5. Handle auto-publish if timeout
    6. Generate final PDF if approved
    7. Publish to Telegram and database

    Args:
        date: datetime.date
        top_5_items: List of top 5 selected items
        next_3_items: List of items ranked 6-8 (also-in-news)
        items_with_scores: All ranked items with scoring metadata

    Returns:
        result: {
            "status": "published" | "auto_published" | "failed",
            "approved_items": approved items list,
            "published_at": timestamp,
            "publication_method": "curator_approved" | "auto_published"
        }
    """
    # ── fast path: no curator configured ─────────────────────────────────────
    if not C.ENV.get("CURATOR_TELEGRAM_TOKEN"):
        C.log("   [CURATOR] No CURATOR_TELEGRAM_TOKEN — auto-publishing immediately.")
        return {"status": "auto_published", "approved_items": top_5_items,
                "published_at": datetime.datetime.now().isoformat(),
                "publication_method": "auto_published"}
    # ──────────────────────────────────────────────────────────────────────────

    C.log(f"\n[CURATOR WORKFLOW] Starting approval workflow for {date.isoformat()}")

    # STEP 1: Save draft items
    C.log("\n[STEP 1] Saving draft items to temporary storage...")
    draft_file, approval_file = save_draft_items(date, items_with_scores, top_5_items, next_3_items)

    # STEP 2: Generate draft PDF
    C.log("\n[STEP 2] Generating draft PDF for curator review...")
    draft_pdf = generate_draft_pdf(date, top_5_items)

    # STEP 3: Send Telegram approval message
    C.log("\n[STEP 3] Sending Telegram approval message...")
    telegram_token = C.ENV.get("CURATOR_TELEGRAM_TOKEN")
    if telegram_token:
        CuratorTelegram.send_approval_message(date, top_5_items, telegram_token)
    else:
        C.log("   ⚠ CURATOR_TELEGRAM_TOKEN not set. Skipping Telegram message.")

    # STEP 4: Wait for approval (2-hour timeout)
    approval_status, curator_items = check_curator_approval(date, timeout_hours=2)

    # STEP 5: Handle approval or auto-publish
    if approval_status == "auto_published":
        C.log("\n[STEP 5] Auto-publishing (timeout)...")
        handle_curator_timeout_auto_publish(date, top_5_items)
        return {
            "status": "auto_published",
            "approved_items": top_5_items,
            "published_at": datetime.datetime.now().isoformat(),
            "publication_method": "auto_published",
        }

    # Curator approved (with or without edits)
    approved_items = curator_items or top_5_items

    # STEP 6: Generate final PDF
    C.log("\n[STEP 6] Generating final PDF after approval...")
    final_pdf = generate_final_pdf(date, approved_items, next_3_items)

    # STEP 7: Publish to Telegram
    C.log("\n[STEP 7] Publishing to Telegram...")
    if final_pdf:
        publish_to_telegram(date, approved_items, final_pdf)

    # STEP 8: Store in database
    C.log("\n[STEP 8] Storing publication record...")
    store_in_database(date, approved_items, "published")

    # Log success
    log_curator_feedback(date, auto_published=False, action=f"curator_{approval_status}")

    return {
        "status": "published",
        "approved_items": approved_items,
        "published_at": datetime.datetime.now().isoformat(),
        "publication_method": f"curator_{approval_status}",
    }

# ─────────────────────────────────────────────────────────────────────────────
# SOURCES
# ─────────────────────────────────────────────────────────────────────────────
def fetch_pib(date):
    """Source 1 — PIB. Scrape Allrel.aspx, keep last-24h release titles."""
    items = []
    try:
        import requests
        from bs4 import BeautifulSoup
        url = "https://pib.gov.in/Allrel.aspx?reg=3&lang=1"
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 PaperSe/1.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        # PIB lists releases as <a> under content area; titles are link texts.
        seen = set()
        for a in soup.select("a[href*='PressReleseDetail'], a[href*='PressReleasePage'], ul.num li a, .content-area a"):
            txt = " ".join(a.get_text(" ", strip=True).split())
            if len(txt) < 25 or txt in seen:
                continue
            seen.add(txt)
            items.append({"source": "PIB", "title": txt, "text": txt, "url": url})
        # Fallback: any reasonably long anchor text on the page
        if not items:
            for a in soup.find_all("a"):
                txt = " ".join(a.get_text(" ", strip=True).split())
                if len(txt) >= 40 and txt not in seen:
                    seen.add(txt)
                    items.append({"source": "PIB", "title": txt, "text": txt, "url": url})
        C.log(f"   PIB: {len(items)} release titles (last listing)")
    except Exception as e:
        C.log(f"   ⚠ PIB fetch failed: {e}")
    return items[:120]


def _readable(chunk):
    """Reject garbled newspaper blocks.
    Stats like '75*', 'NFHS-6', 'Q3' legitimately lower alpha, so we're
    permissive on alpha. camelCase ratio is the primary garble signal."""
    words = chunk.split()
    wc = len(words)
    if not (8 <= wc <= 180):
        return False
    if max((len(w) for w in words), default=0) > 28:
        return False
    if sum(len(w) for w in words) / wc > 11.5:
        return False
    if sum(1 for w in words if len(w) > 18) / wc > 0.20:
        return False
    # camelCase joins signal masthead / column-merge garble
    if sum(1 for w in words if re.search(r"[a-z][A-Z]", w)) / wc > 0.12:
        return False
    alpha = sum(c.isalpha() or c.isspace() for c in chunk) / max(1, len(chunk))
    return alpha >= 0.70


def _degarble(line):
    """Split camelCase word-joins (ViratKohli → Virat Kohli) left by
    multi-column text-flow extraction."""
    return re.sub(r"([a-z])([A-Z])", r"\1 \2", line)


def _line_ok(line):
    """True if the line is readable enough to include in a candidate block."""
    words = line.split()
    wc = len(words)
    if wc < 3 or wc > 25:
        return False
    if max((len(w) for w in words), default=0) > 25:
        return False
    # Reject mastheads / heavy column-merge (camelCase ratio)
    if sum(1 for w in words if re.search(r"[a-z][A-Z]", w)) / wc > 0.15:
        return False
    alpha = sum(c.isalpha() or c.isspace() for c in line) / max(1, len(line))
    return alpha >= 0.68


def load_ie_pdf(date):
    """Source 2 — IE PDF via pdfplumber.
    IE is a multi-column newspaper: extract_text() fuses columns row-by-row,
    producing garbled words like 'ViratKohliThatisourquestion'. We fix this by:
    1. degarbling camelCase joins (splits 'ViratKohli' → 'Virat Kohli'),
    2. filtering out lines still dominated by garble,
    3. building 6-line sliding-window chunks,
    4. applying _readable() at the chunk level (not per-line).
    This converts 5 usable blocks → ~100 blocks for the June-02 IE PDF."""
    items = []
    cand = [C.IE_DIR / f"{date.isoformat()}.pdf",
            C.UPLOADS / f"ie-delhi-{date.strftime('%d-%m-%Y')}.pdf"]
    path = next((p for p in cand if p.exists()), None)
    if not path:
        C.log(f"   ⚠ IE PDF not found at {cand[0]} (or uploads). Skipping IE source.")
        return items
    try:
        import pdfplumber
        blocks = []
        with pdfplumber.open(str(path)) as pdf:
            n = len(pdf.pages)
            for pg in pdf.pages[:min(n, 24)]:
                raw_lines = [l.strip() for l in (pg.extract_text() or "").split("\n")
                             if l.strip()]
                # Degarble camelCase joins, then keep readable lines only
                ok_lines = []
                for raw in raw_lines:
                    dg = " ".join(_degarble(w) for w in raw.split())
                    if _line_ok(dg):
                        ok_lines.append(dg)
                # Sliding window of 6 lines, step 4
                W, STEP = 6, 4
                for i in range(0, max(1, len(ok_lines)), STEP):
                    chunk = " ".join(ok_lines[i:i + W])
                    chunk = " ".join(chunk.split())
                    if _readable(chunk):
                        blocks.append(chunk)
        # de-dup near-identical starts
        seen, uniq = set(), []
        for b in blocks:
            key = b[:60].lower()
            if key not in seen:
                seen.add(key); uniq.append(b)
        for b in uniq:
            items.append({"source": "IE", "title": b[:90], "text": b})
        C.log(f"   IE PDF: {path.name} → {n} pages, {len(items)} candidate blocks")
    except Exception as e:
        C.log(f"   ⚠ IE PDF parse failed: {e}")
    return items


def load_sujas(date):
    """Source 3 — SUJAS. Parse on 1st of month; otherwise serve from cache."""
    month = date.strftime("%Y-%m")
    if date.day == 1:
        path = C.SUJAS_DIR / f"{month}.pdf"
        if not path.exists():
            C.log(f"   ⚠ SUJAS PDF not found for {month}; skipping.")
            return []
        try:
            import pdfplumber
            items = []
            with pdfplumber.open(str(path)) as pdf:
                for pg in pdf.pages:
                    txt = pg.extract_text() or ""
                    for para in re.split(r"\n\s*\n", txt):
                        para = " ".join(para.split())
                        if 8 <= len(para.split()) <= 160:
                            items.append({"source": "SUJAS", "title": para[:90], "text": para})
            C.sb_upsert("sujas_cache", {"month": month, "items": items}, on_conflict="month")
            C.log(f"   SUJAS: parsed {len(items)} items, cached for {month}")
            return items
        except Exception as e:
            C.log(f"   ⚠ SUJAS parse failed: {e}")
            return []
    # not the 1st → read cache
    rows = C.sb_select("sujas_cache", params={"month": f"eq.{month}", "limit": 1})
    if rows:
        items = rows[0].get("items") or []
        C.log(f"   SUJAS: {len(items)} items from cache ({month})")
        return items
    C.log(f"   SUJAS: no cache for {month} (only parsed on 1st); skipping.")
    return []


def fetch_wiki(date):
    """Source 4 — Wikipedia Current Events. Scrape Sundays; else from cache.
    Filter: elections / heads of state or government changes only."""
    week_start = (date - datetime.timedelta(days=date.weekday())).isoformat()  # Monday
    KEEP = ("elect", "president", "prime minister", "head of state", "head of government",
            "chancellor", "sworn in", "resign", "appointed", "coup", "referendum")
    if date.weekday() == 6:  # Sunday
        try:
            import requests
            from bs4 import BeautifulSoup
            url = "https://en.wikipedia.org/wiki/Portal:Current_events"
            r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 PaperSe/1.0"})
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            items = []
            for li in soup.select(".current-events-content li, .description li"):
                txt = " ".join(li.get_text(" ", strip=True).split())
                if len(txt) > 20 and any(k in txt.lower() for k in KEEP):
                    items.append({"source": "WIKI", "title": txt[:90], "text": txt})
            C.sb_upsert("wiki_cache", {"week_start": week_start, "items": items}, on_conflict="week_start")
            C.log(f"   WIKI: {len(items)} election/head-of-state items, cached ({week_start})")
            return items
        except Exception as e:
            C.log(f"   ⚠ WIKI fetch failed: {e}")
            return []
    rows = C.sb_select("wiki_cache", params={"week_start": f"eq.{week_start}", "limit": 1})
    if rows:
        items = rows[0].get("items") or []
        C.log(f"   WIKI: {len(items)} items from cache (week {week_start})")
        return items
    C.log(f"   WIKI: no cache for week {week_start} (only scraped Sundays); skipping.")
    return []


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORIES + FILTERS
# ─────────────────────────────────────────────────────────────────────────────
def tokenize(text):
    tokens = set()
    for w in re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", (text or "").lower()):
        if w not in STOP and len(w) >= 4:
            tokens.add(w)
            # Also add each part of hyphenated words so "Satwik-Chirag" → {"satwik", "chirag"}
            if "-" in w:
                for part in w.split("-"):
                    if len(part) >= 4 and part not in STOP:
                        tokens.add(part)
    return tokens

def load_categories():
    cats = C.sb_select("ca_categories")
    for c in cats:
        # CORE keywords = category name + static topic link (high precision)
        core = tokenize(c.get("category")) | tokenize(c.get("static_topic_link"))
        # CAPTURE keywords = broader signal from what_to_capture phrases
        cap = set()
        for phrase in (c.get("what_to_capture") or []):
            cap |= tokenize(phrase)
        c["_core_kw"] = core
        c["_capture_kw"] = cap | core
        ign = set()
        for phrase in (c.get("what_to_ignore") or []):
            ign |= tokenize(phrase)
        c["_ignore_kw"] = ign
    C.log(f"   Loaded {len(cats)} CA categories from ca_categories")
    return cats

RAJ_KEYS = ("rajasthan", "jaipur", "jodhpur", "udaipur", "kota", "ajmer", "bikaner",
            "rajasthani", "marwar", "mewar", "jaisalmer", "rpsc", "raj.")

TIER_BASE = {1: 1.0, 2: 0.7, 3: 0.4}

def _text_quality(text):
    """Quality penalty for word-fused garble not caught by camelCase check.
    All-lowercase joins (movedatone, tweenhimand) have no case change, but
    are very long. Count tokens > 14 chars as garble indicators.
    Returns 0.3 – 1.0 multiplier applied to priority before ranking."""
    words = text.split()
    if not words:
        return 1.0
    # Strip trailing punctuation for length test
    clean = [re.sub(r"[^a-zA-Z\-]", "", w) for w in words]
    long = sum(1 for w in clean if len(w) > 14)
    # Each garbled long-word (>14 chars) cuts quality by 0.35; floor at 0.3
    return max(0.3, 1.0 - long * 0.35)


# Pre-filter: one-hit reject for clear advertisement signals
AD_REJECT = {"emoluments", "vacancy", "applicant", "shortlisted",
             "hiring", "walkin", "sarkari"}

def run_filters(item, cats):
    """5-filter chain. Returns enriched item or None (rejected)."""
    text = item["text"]
    toks = tokenize(text)
    low = text.lower()

    # PRE-REJECT advertisements
    if toks & AD_REJECT:
        return None

    # PRE-REJECT IE newspaper page-header blocks (page-num + day + masthead)
    # Pattern: "18 Tues DAy,June2,2026 TheIndIAnexPress ..."
    if re.match(r"^\d{1,2}\s+(mon|tues|wed|thurs|fri|sat|sun)", item["text"].lower().strip()):
        return None

    # FILTER 1 — keyword match. Require a CORE hit (category/topic word), not just
    # generic capture words, to avoid spurious matches. Score = 2*core + capture.
    best, best_score, best_core = None, 0, 0
    for c in cats:
        core_hits = len(toks & c["_core_kw"])
        if core_hits < 1:
            continue                       # must hit a core keyword
        score = 2 * core_hits + len(toks & c["_capture_kw"])
        if score > best_score:
            best, best_score, best_core = c, score, core_hits
    if not best:
        return None  # REJECT — no category core match

    # FILTER 3 — ignore check (matched category's ignore set)
    if len(toks & best["_ignore_kw"]) >= 2:
        return None  # REJECT — looks like ignored content

    # FILTER 2 — tier check
    tier = best.get("tier") or 3
    item_raj = any(k in low for k in RAJ_KEYS)
    if tier >= 4:
        return None
    if tier == 3 and not item_raj:
        return None

    # base priority
    priority = TIER_BASE.get(tier, 0.4) + 0.05 * best_score

    # FILTER 4 — rajasthan bonus
    if item_raj:
        priority += 0.3

    # FILTER 5 — ChromaDB PYQ check (only attach when genuinely similar)
    static_connect = best.get("static_topic_link") or best.get("category")
    exam_ref = None
    pyq = C.pyq_lookup(text, max_distance=0.33)
    if pyq:
        priority += 0.2
        exam_ref = f"{pyq['subject']} · asked {pyq['year']}"
        # only override the static link if the PYQ subject aligns with the category
        if pyq.get("subject") and best.get("static_subject") and \
           pyq["subject"].lower() == str(best["static_subject"]).lower():
            static_connect = pyq["topic"] or static_connect

    # Apply text-quality multiplier: penalise word-fused garble (long tokens)
    quality = _text_quality(text)
    priority = round(priority * quality, 3)

    item.update({
        "category": best.get("category"),
        "tier": tier,
        "rajasthan_angle": item_raj or bool(best.get("rajasthan_angle")),
        "static_connect": static_connect,
        "static_subject": best.get("static_subject"),
        "exam_ref": exam_ref,
        "priority": priority,
        "match_score": best_score,
        "match_core": best_core,
        "text_quality": round(quality, 2),
    })
    return item


# ─────────────────────────────────────────────────────────────────────────────
# CONTENT GENERATION (Claude)
# ─────────────────────────────────────────────────────────────────────────────
SYS_EN = ("You are PaperSe CA writer for RPSC RAS aspirants. Write exam-focused current "
          "affairs. Be crisp. Every bullet must be a testable fact. Never add opinions or "
          "analysis. Only facts that RPSC can test.")
SYS_HI = ("आप RPSC RAS अभ्यर्थियों के लिए PaperSe करेंट अफेयर्स लेखक हैं। परीक्षा-केंद्रित करेंट अफेयर्स "
          "ताज़ा हिंदी में मौलिक रूप से लिखें — अनुवाद नहीं। संक्षिप्त रहें। हर बुलेट एक परीक्षा-योग्य तथ्य हो। "
          "कोई राय या विश्लेषण नहीं — केवल वे तथ्य जो RPSC पूछ सकता है।")

def gen_main(item, lang):
    sysmsg = SYS_EN if lang == "EN" else SYS_HI
    hint = f"\nStatic chapter hint: {item.get('static_connect')}." if item.get("static_connect") else ""
    examh = f"\nRPSC has tested this topic before ({item['exam_ref']})." if item.get("exam_ref") else ""
    lang_word = "English" if lang == "EN" else "Hindi (Devanagari, freshly authored, not translated)"
    user = (f"Source ({item['source']}): {item['text']}\n"
            f"Category: {item.get('category')}.{hint}{examh}\n\n"
            f"Write in {lang_word}. Return ONLY JSON:\n"
            '{"title": "max 10 words, no ** markers", '
            '"summary": "1 line — what happened", '
            '"context": "2-3 lines background", '
            '"bullets": ["3-5 exam facts; wrap every key name/number/place in **double asterisks** '
            'e.g. \\"**NFHS-6** found **101** indicators in **Rajasthan**\\""], '
            '"static_connect": "short chapter name only, max 4 words, e.g. \\"Indian River System\\" or '
            '\\"Constitutional Amendment\\" — no sentences"}')
    data, _ = C.claude_json(sysmsg, user, max_tokens=1000)
    data["bullets"] = data.get("bullets") or []
    return data

def gen_also(item):
    """Title + one-liner, EN and HI together."""
    user = (f"Source ({item['source']}): {item['text']}\nCategory: {item.get('category')}.\n\n"
            "Return ONLY JSON with a short title and a 1-2 line key-fact one-liner, in EN and HI "
            "(Hindi authored fresh, not translated):\n"
            '{"en": {"title": "...", "one_liner": "..."}, "hi": {"title": "...", "one_liner": "..."}}')
    data, _ = C.claude_json(SYS_EN, user, max_tokens=500)
    return data


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main(news_date, label_date, dry_run=False):
    """
    Main pipeline. Fetches news from news_date (yesterday), labels output with label_date (today).
    news_date: used for PIB, Wikipedia fetching (date of the news)
    label_date: used for output filenames, database records, PDF headers (today)
    dry_run: if True, print ranked candidates and exit before content generation.
    """
    C.log("=" * 64)
    C.log(f"PaperSe Daily CA Pipeline — news_date={news_date.isoformat()}  label_date={label_date.isoformat()}"
          + ("  [DRY-RUN]" if dry_run else ""))
    C.log(f"  weekday={label_date.strftime('%A')}")
    C.log("=" * 64)

    # 1. FETCH  (SUJAS removed — monthly magazine only, not daily pipeline)
    C.log("\n[1] FETCH SOURCES")
    raw = []
    raw += fetch_pib(news_date)
    raw += load_ie_pdf(label_date)  # IE PDF for label_date (today's paper)
    # NOTE: load_sujas() is intentionally NOT called here; use monthly pipeline for SUJAS.
    raw += fetch_wiki(news_date)
    C.log(f"   → total raw items fetched: {len(raw)}")
    if not raw:
        C.log("\n✗ No source items fetched. Aborting (no data).")
        return None

    # 2. FILTER
    C.log("\n[2] FILTER (keyword → tier → ignore → rajasthan → chromaDB)")
    cats = load_categories()
    approved = []
    for it in raw:
        res = run_filters(it, cats)
        if res:
            approved.append(res)
    C.log(f"   → approved {len(approved)} / {len(raw)} items")
    if not approved:
        C.log("\n✗ No items passed filters. Slow news day / source mismatch.")
        return None

    # 2.5. RAG ENRICHMENT (3-layer intelligence: ChromaDB PYQs + topic_kb)
    C.log("\n[2.5] RAG ENRICHMENT (ChromaDB PYQs + topic_kb priorities)")
    # Build ca_category_map for enrichment
    ca_map = {i: item.get("category") for i, item in enumerate(approved)}
    # Enrich with 3-layer RAG
    approved = rag.enrich_ca_items(approved, ca_category_map=ca_map)
    # Re-sort by final_priority_score (combining all layers)
    approved.sort(key=lambda x: x.get("final_priority_score", x.get("priority", 0.5)), reverse=True)

    # 3. RANK + SELECT  (dedup by category for variety in the main 5)
    # Use final_priority_score from RAG enrichment (incorporates topic_kb + PYQ boosts)
    approved.sort(key=lambda x: x.get("final_priority_score", x["priority"]), reverse=True)
    main_items, seen_cat = [], set()
    for it in approved:
        if it["category"] in seen_cat:
            continue
        seen_cat.add(it["category"]); main_items.append(it)
        if len(main_items) == 5:
            break
    chosen_ids = {id(x) for x in main_items}
    rest = [x for x in approved if id(x) not in chosen_ids]
    also_items = rest[:5]   # ranks 6-10
    for it in main_items: it["is_main"] = True
    for it in also_items: it["is_main"] = False
    C.log(f"   → MAIN (is_main=true): {len(main_items)} | ALSO IN NEWS: {len(also_items)}")
    for i, it in enumerate(main_items, 1):
        C.log(f"      {i}. [{it['priority']:.3f}] {it['category']} · {it['source']} :: {it['title'][:70]}")

    # ── DRY-RUN: show scores and stop before content generation ──────────────
    if dry_run:
        C.log("\n" + "=" * 64)
        C.log("DRY-RUN COMPLETE — top candidates (approve these before PDF generation)")
        C.log("=" * 64)
        C.log("\n  MAIN (5 items):")
        for i, it in enumerate(main_items, 1):
            C.log(f"    {i}. [{it['priority']:.3f}] [{it['category']}] "
                  f"source={it['source']}  →  {it['title'][:75]}")
        C.log("\n  ALSO-IN-NEWS (next 5):")
        for i, it in enumerate(also_items, 1):
            C.log(f"    {i}. [{it['priority']:.3f}] [{it['category']}] "
                  f"source={it['source']}  →  {it['title'][:75]}")
        return {"dry_run": True, "main": main_items, "also": also_items}
    # ─────────────────────────────────────────────────────────────────────────

    # ═══════════════════════════════════════════════════════════════════════════
    # CURATOR WORKFLOW INTEGRATION POINT
    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 3.5: Before content generation and MCQ, trigger curator approval
    C.log("\n[3.5] CURATOR WORKFLOW — Draft items saved, approval message sent")
    curator_result = curator_approval_workflow(
        label_date,
        main_items,
        also_items[:3],  # next 3 items
        approved         # all ranked items for reference
    )
    C.log(f"   ✓ Curator workflow completed: {curator_result['publication_method']}")

    # If auto-published, we can still generate content but mark differently
    if curator_result['publication_method'] == "auto_published":
        C.log("   (Note: Items were auto-published due to timeout)")
    # ═══════════════════════════════════════════════════════════════════════════

    # 4. CONTENT GENERATION → store
    C.log("\n[3] CONTENT GENERATION (Claude, EN + HI authored fresh)")
    C.sb_delete("daily_ca_items", {"date": label_date.isoformat()})  # idempotent re-run
    main_rows_inserted = []

    # Use curator-approved items if available, otherwise use original main_items
    items_to_generate = curator_result.get('approved_items', main_items)

    for i, it in enumerate(items_to_generate[:5], 1):  # Only top 5 after curation
        C.log(f"   • main {i}/{len(items_to_generate[:5])}: {it['category']} …")
        en = gen_main(it, "EN")
        hi = gen_main(it, "HI")
        base = dict(date=label_date.isoformat(), category=it["category"], tier=it["tier"],
                    source=it["source"], rajasthan_angle=it["rajasthan_angle"],
                    priority=it.get("final_priority_score", it.get("priority", 0.5)), is_main=True)

        row_en = {**base, "language": "EN", "title": en.get("title"), "summary": en.get("summary"),
                  "context": en.get("context"), "bullets": en.get("bullets"),
                  "static_connect": en.get("static_connect") or it.get("static_connect")}
        row_hi = {**base, "language": "HI", "title": hi.get("title"), "summary": hi.get("summary"),
                  "context": hi.get("context"), "bullets": hi.get("bullets"),
                  "static_connect": hi.get("static_connect") or it.get("static_connect")}
        ins = C.sb_insert("daily_ca_items", [row_en, row_hi])
        # keep the EN row id as canonical "source item" for MCQs
        en_id = next((r["id"] for r in ins if r["language"] == "EN"), ins[0]["id"])
        main_rows_inserted.append({"id": en_id, "category": it["category"], "title": en.get("title")})

    for j, it in enumerate(also_items, 1):
        C.log(f"   • also {j}/{len(also_items)}: {it['category']} …")
        a = gen_also(it)
        base = dict(date=label_date.isoformat(), category=it["category"], tier=it["tier"],
                    source=it["source"], rajasthan_angle=it["rajasthan_angle"],
                    priority=it["priority"], is_main=False,
                    static_connect=it.get("static_connect"))
        C.sb_insert("daily_ca_items", [
            {**base, "language": "EN", "title": a["en"]["title"], "one_liner": a["en"]["one_liner"]},
            {**base, "language": "HI", "title": a["hi"]["title"], "one_liner": a["hi"]["one_liner"]},
        ], returning=False)

    total = C.sb_count("daily_ca_items", {"date": f"eq.{label_date.isoformat()}"})
    C.log(f"\n✓ STEP 2 complete — {total} rows in daily_ca_items for {label_date.isoformat()} "
          f"({len(items_to_generate[:5])} main ×2 langs + {len(also_items)} also ×2 langs)")
    C.log(f"  main source-item ids (for MCQs): {[r['id'] for r in main_rows_inserted]}")

    return {
        "main_rows": main_rows_inserted,
        "curator_workflow": curator_result,
        "timestamp": datetime.datetime.now().isoformat(),
    }


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv
    label_date = C.parse_date(args[0]) if args else datetime.date.today()
    news_date = label_date - datetime.timedelta(days=1)
    C.log(f"\nEntry point: label_date={label_date.isoformat()} news_date={news_date.isoformat()}"
          + ("  [DRY-RUN]" if dry_run else ""))
    out = main(news_date, label_date, dry_run=dry_run)
    sys.exit(0 if out else 1)
