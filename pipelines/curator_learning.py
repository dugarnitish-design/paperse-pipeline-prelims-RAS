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


class CuratorLearning:
    """
    Main class for curator feedback learning.
    All Supabase calls use _common.py helpers (sb_select, sb_update, sb_insert).
    """

    # ── feedback logging ──────────────────────────────────────────────────────

    def log_rejection(
        self,
        item_title: str,
        category: str,
        rejected_text: str = "",
        topic: str = "",
        auto_published: bool = False,
        rejection_reason: str = "",
    ) -> dict:
        """
        Log a curator rejection. Updates category + topic intelligence.

        Returns: dict with success status and learning results.
        """
        date_str = datetime.date.today().isoformat()

        # 1. Insert into curator_feedback
        try:
            C.sb_insert("curator_feedback", {
                "date": date_str,
                "item_title": item_title,
                "category": category,
                "action": "rejected",
                "auto_published": auto_published,
            }, returning=False)
        except Exception as e:
            C.log(f"  ⚠ curator_feedback insert failed: {e}")

        # 2. Update category intelligence
        cat_result = self.update_category_intelligence(category, rejected_text, rejection_reason)

        # 3. Update topic intelligence (if topic provided)
        topic_result = {}
        if topic:
            topic_result = self.update_topic_intelligence(topic)

        return {
            "success": True,
            "action": "rejected",
            "item_title": item_title,
            "category_update": cat_result,
            "topic_update": topic_result,
        }

    def log_replacement(
        self,
        old_item_title: str,
        new_item_title: str,
        category: str,
        old_item_text: str = "",
        topic: str = "",
        auto_published: bool = False,
    ) -> dict:
        """
        Log when an item is replaced by a candidate item.
        Treated as a rejection of the original item.
        """
        date_str = datetime.date.today().isoformat()

        try:
            C.sb_insert("curator_feedback", {
                "date": date_str,
                "item_title": old_item_title,
                "category": category,
                "action": "replaced",
                "auto_published": auto_published,
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
    ) -> None:
        """Log a curator approval (for auditing)."""
        try:
            C.sb_insert("curator_feedback", {
                "date": datetime.date.today().isoformat(),
                "item_title": item_title,
                "category": category,
                "action": "approved",
                "auto_published": auto_published,
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
