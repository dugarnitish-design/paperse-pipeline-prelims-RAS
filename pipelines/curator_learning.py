#!/usr/bin/env python3
"""
Curator RAG Learning Feedback Loop.

When items are rejected or replaced in the curator dashboard, this module:
  1. Logs the action to `curator_feedback` table
  2. Updates `ca_categories.tier_weight` (lower = less likely to appear)
  3. Updates `topic_kb.rejection_count` and optionally `priority_score`

Uses _common.py REST helpers (NOT the supabase Python library).
"""
import sys, datetime, json, pathlib
from typing import Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from pipelines import _common as C


# ── constants ─────────────────────────────────────────────────────────────────

TIER_WEIGHT_DECREMENT   = 0.1    # Per rejection (up to 3 times)
MAX_DECREMENTS          = 3      # Max 0.3 total decrease per category
MIN_TIER_WEIGHT         = 0.0
MAX_TIER_WEIGHT         = 1.0
PRIORITY_REJECT_THRESH  = 3      # Decrease priority_score after N rejections
PRIORITY_DECREMENT      = 0.1

# §8 — precision rejection learning. Haiku turns the curator's free-text reason into a
# reusable rule (pattern + lesson + scope), optionally a blacklist the scorer penalises.
REJECT_EXTRACT_SYS = (
    "You analyse why an RPSC RAS exam-prep current-affairs item was REJECTED by an expert "
    "curator. From the item and the curator's reason, extract a REUSABLE rejection rule. "
    "Return ONLY JSON with keys:\n"
    '"pattern": a 3-8 word content pattern describing the KIND of item to suppress, using '
    "concrete nouns from the item (e.g. 'minor district-level sports award', 'routine MoU "
    "signing'); empty string if the reason is one-off and not generalisable.\n"
    '"lesson": one-line instruction to the author/scorer to prevent this in future.\n'
    '"scope": "pattern_wide" if this rejection should suppress ALL future similar items '
    '(needs a clear, generalisable content pattern), or "item_only" if it is a one-off '
    "specific to this item.\n"
    '"penalise_category": true only if the WHOLE category is low-value/over-represented.\n'
    "Be precise. Use item_only for vague, subjective or one-off reasons. No text outside the JSON."
)


class CuratorLearning:
    """
    Main class for curator feedback learning.
    All Supabase calls use _common.py helpers (sb_select, sb_update, sb_insert).
    """

    # ── feedback logging ──────────────────────────────────────────────────────

    def _already_learned(self, item_id, session_id) -> bool:
        """
        FIX 7 — session-dedup guard. True if this item already produced a
        rejection/replacement RAG write in this curation session, so we don't
        penalize its category/topic twice when the curator toggles reject more
        than once (or reloads and re-rejects) within the same session.
        """
        if not item_id or not session_id:
            return False
        try:
            rows = C.sb_select("curator_feedback", select="id", params={
                "item_id": f"eq.{item_id}",
                "curator_session_id": f"eq.{session_id}",
                "action": "in.(rejected,replaced)",
            })
            return bool(rows)
        except Exception as e:
            C.log(f"  ⚠ dedup check failed (will proceed): {e}")
            return False

    def log_rejection(
        self,
        item_title: str,
        category: str,
        rejected_text: str = "",
        topic: str = "",
        auto_published: bool = False,
        rejection_reason: str = "",
        item_id=None,
        session_id=None,
    ) -> dict:
        """
        Log a curator rejection. Updates category + topic intelligence.
        FIX 7: at most one RAG write per (item_id, session_id) — a repeat reject
        of the same item in the same session is a no-op.

        Returns: dict with success status and learning results.
        """
        date_str = datetime.date.today().isoformat()
        session_id = session_id or date_str   # default session = today's draft

        # FIX 7 — skip the duplicate RAG write (and insert) for a re-reject.
        if self._already_learned(item_id, session_id):
            C.log(f"  ⊘ RAG dedup — item {item_id} already learned this session ({session_id})")
            return {"success": True, "deduped": True, "action": "rejected", "item_title": item_title}

        # 1. Insert into curator_feedback
        try:
            C.sb_insert("curator_feedback", {
                "date": date_str,
                "item_title": item_title,
                "category": category or "Uncategorized",   # column is NOT NULL
                "action": "rejected",
                "auto_published": auto_published,
                "item_id": str(item_id) if item_id else None,
                "curator_session_id": session_id,
            }, returning=False)
        except Exception as e:
            C.log(f"  ⚠ curator_feedback insert failed: {e}")

        # 2. Update category intelligence
        cat_result = self.update_category_intelligence(category, rejected_text, rejection_reason)

        # 3. Update topic intelligence (if topic provided)
        topic_result = {}
        if topic:
            topic_result = self.update_topic_intelligence(topic)

        # 4. §8 — precision learning from the curator's free-text reason.
        learn_result = self.learn_from_free_text(
            item_title=item_title, free_text=rejection_reason, category=category,
            item_id=item_id, rejected_text=rejected_text, date_str=date_str)

        return {
            "success": True,
            "action": "rejected",
            "item_title": item_title,
            "category_update": cat_result,
            "topic_update": topic_result,
            "rejection_learning": learn_result,
        }

    def learn_from_free_text(self, item_title, free_text, category="", item_id=None,
                             news_type="", rejected_text="", date_str=None) -> dict:
        """§8 — precision rejection learning. Haiku turns the curator's free-text reason
        into a reusable rule: always records rejection_learnings, and (only when the rule
        generalises) writes/bumps a topic_blacklist pattern that the scorer (§6A) penalises
        by -0.3. Best-effort — a model/DB failure logs and returns {} but never raises, so
        the curator's reject click is never blocked."""
        free_text = (free_text or "").strip()
        if not free_text:
            return {}
        date_str = date_str or datetime.date.today().isoformat()
        user = (f"Item title: {item_title}\nCategory: {category}\nNews type: {news_type}\n"
                f"Item text: {(rejected_text or '')[:500]}\n"
                f"Curator's rejection reason: {free_text}\n\nReturn the JSON.")
        data = {}
        try:
            data, _ = C.claude_json(REJECT_EXTRACT_SYS, user, max_tokens=300,
                                    model=C.HAIKU_MODEL, cache_system=True)
            if not isinstance(data, dict):
                data = {}
        except Exception as e:
            C.log(f"  ⚠ §8 rejection extraction failed: {e}")
        pattern  = (data.get("pattern") or "").strip()
        lesson   = (data.get("lesson") or free_text).strip()
        scope    = (data.get("scope") or "item_only").strip().lower()
        if scope not in ("item_only", "pattern_wide"):    # CHECK constraint guard
            scope = "pattern_wide" if pattern else "item_only"
        pen_cat  = bool(data.get("penalise_category"))
        do_black = (scope == "pattern_wide") and bool(pattern)

        # rejection_learnings — always record the reason + extracted rule.
        try:
            C.sb_insert("rejection_learnings", {
                "date": date_str, "item_id": str(item_id) if item_id else None,
                "item_title": item_title, "free_text_reason": free_text,
                "extracted_pattern": pattern, "lesson": lesson,
                "penalise_category": pen_cat, "scope": scope,
                "category": category, "news_type": news_type,
            }, returning=False)
        except Exception as e:
            C.log(f"  ⚠ rejection_learnings insert failed: {e}")

        # topic_blacklist — only generalisable patterns; dedup by pattern, bump count.
        if do_black:
            try:
                existing = C.sb_select("topic_blacklist", select="id,pattern,rejection_count",
                                       params={"limit": "2000"}) or []
                match = next((r for r in existing
                              if (r.get("pattern") or "").strip().lower() == pattern.lower()), None)
                now = datetime.datetime.utcnow().isoformat() + "Z"
                if match:
                    C.sb_update("topic_blacklist", {
                        "rejection_count": (match.get("rejection_count") or 1) + 1,
                        "last_rejected": now, "lesson": lesson, "penalise_category": pen_cat,
                    }, {"id": match["id"]})
                else:
                    C.sb_insert("topic_blacklist", {
                        "pattern": pattern, "lesson": lesson, "penalise_category": pen_cat,
                        "rejection_count": 1, "last_rejected": now,
                    }, returning=False)
                C.log(f"  ✓ §8 blacklist learned: {pattern!r}")
            except Exception as e:
                C.log(f"  ⚠ topic_blacklist write failed: {e}")

        C.log(f"  ✓ §8 rejection learned — scope={scope} pattern={pattern!r} blacklist={do_black}")
        return {"pattern": pattern, "lesson": lesson, "scope": scope,
                "penalise_category": pen_cat, "blacklisted": do_black}

    def log_replacement(
        self,
        old_item_title: str,
        new_item_title: str,
        category: str,
        old_item_text: str = "",
        topic: str = "",
        auto_published: bool = False,
        item_id=None,
        session_id=None,
    ) -> dict:
        """
        Log when an item is replaced by a candidate item.
        Treated as a rejection of the original item.
        FIX 7: deduped per (item_id, session_id) like log_rejection.
        """
        date_str = datetime.date.today().isoformat()
        session_id = session_id or date_str

        if self._already_learned(item_id, session_id):
            C.log(f"  ⊘ RAG dedup — item {item_id} already learned this session ({session_id})")
            return {"success": True, "deduped": True, "action": "replaced", "old_item": old_item_title}

        try:
            C.sb_insert("curator_feedback", {
                "date": date_str,
                "item_title": old_item_title,
                "category": category or "Uncategorized",   # column is NOT NULL
                "action": "replaced",
                "auto_published": auto_published,
                "item_id": str(item_id) if item_id else None,
                "curator_session_id": session_id,
            }, returning=False)
        except Exception as e:
            C.log(f"  ⚠ curator_feedback insert failed: {e}")

        cat_result = self.update_category_intelligence(
            category, old_item_text, f"Replaced with: {new_item_title}"
        )
        topic_result = self.update_topic_intelligence(topic) if topic else {}

        return {
            "success": True,
            "action": "replaced",
            "old_item": old_item_title,
            "new_item": new_item_title,
            "category_update": cat_result,
            "topic_update": topic_result,
        }

    def log_approval(
        self,
        item_title: str,
        category: str,
        auto_published: bool = False,
        item_id=None,
        session_id=None,
    ) -> None:
        """Log a curator approval (for auditing). No RAG write, so no dedup —
        but we stamp item_id/session_id for a consistent audit trail."""
        try:
            C.sb_insert("curator_feedback", {
                "date": datetime.date.today().isoformat(),
                "item_title": item_title,
                "category": category or "Uncategorized",   # column is NOT NULL
                "action": "approved",
                "auto_published": auto_published,
                "item_id": str(item_id) if item_id else None,
                "curator_session_id": session_id,
            }, returning=False)
        except Exception as e:
            C.log(f"  ⚠ approval log failed: {e}")

    # ── category learning ─────────────────────────────────────────────────────

    def update_category_intelligence(
        self,
        category: str,
        rejected_text: str = "",
        rejection_reason: str = "",
    ) -> dict:
        """
        Find category in ca_categories, add rejection example, possibly decrease tier_weight.
        Max 3 decrements (0.1 each) = max -0.3 total.
        """
        if not category:
            return {"skipped": "no category"}

        # Fetch current row
        try:
            rows = C.sb_select(
                "ca_categories",
                select="id,category,tier_weight,rejection_examples",
                params={"category": f"ilike.{category}"},
            )
        except Exception as e:
            return {"error": f"fetch failed: {e}"}

        if not rows:
            C.log(f"  ⚠ Category '{category}' not found in ca_categories")
            return {"skipped": f"category '{category}' not found"}

        row = rows[0]
        cat_id = row["id"]
        old_weight = float(row.get("tier_weight") or 1.0)

        # Update rejection_examples (JSONB stored as dict)
        examples = row.get("rejection_examples") or {"examples": [], "count": 0}
        if isinstance(examples, str):
            examples = json.loads(examples)
        if "examples" not in examples:
            examples["examples"] = []
        if "count" not in examples:
            examples["count"] = 0

        if rejected_text:
            examples["examples"].append({
                "text": rejected_text[:200],
                "reason": rejection_reason,
                "date": datetime.date.today().isoformat(),
            })
            examples["examples"] = examples["examples"][-10:]  # keep last 10

        examples["count"] = examples.get("count", 0) + 1
        examples["last_updated"] = datetime.datetime.utcnow().isoformat() + "Z"

        # Calculate new tier_weight (decrement once per 3 rejections, max MAX_DECREMENTS times)
        total_rejections = examples["count"]
        decrements = min(total_rejections // 3, MAX_DECREMENTS)
        new_weight = max(1.0 - decrements * TIER_WEIGHT_DECREMENT, MIN_TIER_WEIGHT)

        try:
            C.sb_update(
                "ca_categories",
                patch={"rejection_examples": examples, "tier_weight": new_weight},
                match={"id": cat_id},
            )
        except Exception as e:
            return {"error": f"update failed: {e}"}

        C.log(f"  ✓ Category '{category}' tier_weight {old_weight:.1f} → {new_weight:.1f}")
        return {
            "category": category,
            "old_tier_weight": old_weight,
            "new_tier_weight": new_weight,
            "rejection_count": total_rejections,
        }

    # ── topic learning ────────────────────────────────────────────────────────

    def update_topic_intelligence(self, topic_name: str) -> dict:
        """
        Find topic in topic_kb, increment rejection_count.
        If rejection_count >= 3 (and is a multiple of 3), decrease priority_score by 0.1.
        """
        if not topic_name:
            return {"skipped": "no topic"}

        try:
            rows = C.sb_select(
                "topic_kb",
                select="topic_id,topic,priority_score,rejection_count",
                params={"topic": f"ilike.{topic_name}"},
            )
        except Exception as e:
            return {"error": f"fetch failed: {e}"}

        if not rows:
            C.log(f"  ⚠ Topic '{topic_name}' not found in topic_kb")
            return {"skipped": f"topic '{topic_name}' not found"}

        row = rows[0]
        topic_id = row["topic_id"]
        old_rejection = int(row.get("rejection_count") or 0)
        old_priority  = float(row.get("priority_score") or 1.0)

        new_rejection = old_rejection + 1
        new_priority  = old_priority

        # Decrease priority only at multiples of PRIORITY_REJECT_THRESH
        if new_rejection >= PRIORITY_REJECT_THRESH and new_rejection % PRIORITY_REJECT_THRESH == 0:
            new_priority = max(old_priority - PRIORITY_DECREMENT, 0.0)

        patch = {
            "rejection_count": new_rejection,
            "last_rejection_date": datetime.datetime.utcnow().isoformat() + "Z",
        }
        if new_priority != old_priority:
            patch["priority_score"] = new_priority
            C.log(
                f"  ✓ Topic '{topic_name}' priority_score "
                f"{old_priority:.1f} → {new_priority:.1f} (rejected {new_rejection}x)"
            )

        try:
            C.sb_update("topic_kb", patch=patch, match={"topic_id": topic_id})
        except Exception as e:
            return {"error": f"update failed: {e}"}

        return {
            "topic": topic_name,
            "old_rejection_count": old_rejection,
            "new_rejection_count": new_rejection,
            "old_priority_score": old_priority,
            "new_priority_score": new_priority,
        }

    # ── analytics ─────────────────────────────────────────────────────────────

    def get_category_stats(self, category: str, days: int = 30) -> dict:
        """Rejection rate for a category over last N days."""
        since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
        try:
            rows = C.sb_select(
                "curator_feedback",
                select="action",
                params={"category": f"eq.{category}", "date": f"gte.{since}"},
            )
        except Exception as e:
            return {"error": str(e)}

        total = len(rows)
        rejected = sum(1 for r in rows if r["action"] in ("rejected", "replaced"))
        return {
            "category": category,
            "period_days": days,
            "total": total,
            "rejected": rejected,
            "rate_pct": round(rejected / total * 100, 1) if total else 0,
        }

    def get_top_rejected_categories(self, days: int = 30, limit: int = 5) -> list:
        """Return categories sorted by rejection rate (descending)."""
        since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
        try:
            rows = C.sb_select(
                "curator_feedback",
                select="category,action",
                params={"date": f"gte.{since}"},
            )
        except Exception as e:
            return []

        counts: dict = {}
        for r in rows:
            cat = r.get("category", "")
            if cat not in counts:
                counts[cat] = {"total": 0, "rejected": 0}
            counts[cat]["total"] += 1
            if r["action"] in ("rejected", "replaced"):
                counts[cat]["rejected"] += 1

        result = [
            {
                "category": cat,
                "total": v["total"],
                "rejected": v["rejected"],
                "rate_pct": round(v["rejected"] / v["total"] * 100, 1) if v["total"] else 0,
            }
            for cat, v in counts.items()
        ]
        result.sort(key=lambda x: x["rate_pct"], reverse=True)
        return result[:limit]
