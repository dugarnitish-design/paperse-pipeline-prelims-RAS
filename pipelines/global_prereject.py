#!/usr/bin/env python3
"""
LAYER 2 — global pre-reject. Hard rules, NO AI.

Drops obvious non-RPSC content before any expensive work (dedup, keyword scoring,
the Claude relevance call in Layer 3). These are deliberately *conservative* —
only clear-cut junk — because anything that survives here is still judged for
relevance by Layer 3 (rpsc_relevance.py). The goal is to cut cost/noise, not to
make fine relevance calls.

Replaces the scattered per-category ignore phrases / ad pre-rejects that used to
live inside run_filters, so a story can't bounce between categories to dodge a
single category's ignore list (the whack-a-mole we kept hitting).

API:
    from pipelines import global_prereject as PRE
    keep, reason = PRE.check(item)          # (bool, reason-or-None)
    kept, dropped = PRE.apply(items)        # dropped = [{**item, "_reason": ...}]

Each rule is (label, predicate(low, toks)). The FIRST matching rule wins and its
label is the drop reason (useful for the rpsc_filter_log).
"""
import re

try:
    from pipelines.daily_ca_pipeline import tokenize  # reuse the same tokenizer
except Exception:  # pragma: no cover - fallback if imported very early
    def tokenize(t):
        return set(w for w in re.findall(r"[a-z][a-z\-]{3,}", (t or "").lower()))


def _has(low, *frags):
    return any(f in low for f in frags)


# ── signal vocabularies ──────────────────────────────────────────────────────
INDIA_TOKENS = ("india", "indian", "bharat", "rajasthan", "new delhi", " modi",
                "centre", "union government", "lok sabha", "rajya sabha")
MERIT_TOKENS = ("medal", "gold", "silver", "bronze", "champion", "world record",
                "clinch", "arjuna", "khel ratna", "wins title", "wins gold",
                "wins the", "podium", "trophy lift")
SPORT_WORDS = ("cricket", "test match", " odi ", " t20", "tennis", "french open",
               "wimbledon", "us open", "australian open", "football", "fifa",
               "la liga", "premier league", "badminton", "grandmaster",
               "midfielder", "striker", "quarterfinal", "semifinal", "world cup",
               "ashes", "ronaldo", "messi", "sabalenka", "carlsen")
# Incident markers — dropped UNLESS a policy/legal angle is also present.
INCIDENT_WORDS = ("killed in", "airport attack", "terror attack", "terrorist attack",
                  "stabbed", "shooting", "gangrape", "gang-rape", "murder",
                  "fire incident", "road accident", "stampede", "killed abroad",
                  "indian national abroad")
POLICY_MARKERS = ("scheme", "policy", "law ", "bill", " act ", "guidelines",
                  "supreme court", "high court", "nhrc", "commission orders",
                  "compensation", "regulation", "amendment", "verdict", "treaty",
                  "mou", "agreement", "cabinet")


# ── rules (first match wins) ─────────────────────────────────────────────────
def _rule_ad(low, toks):
    # NOTE: deliberately exclude 'eligibility'/'admissions'/'semester'/'tuition' —
    # they collide with govt SCHEME notices ("scheme eligibility"), which are
    # high RPSC value. Recruitment/tender ads use the tokens below or the phrases.
    AD = {"emoluments", "vacancy", "applicant", "shortlisted", "hiring", "walkin",
          "sarkari", "vacancies", "librarian", "tenderer", "bidder", "corrigendum"}
    if toks & AD:
        return True
    if _has(low, "admission process", "admissions open", "last date to apply",
            "apply online", "walk-in interview", "invites applications for"):
        return True
    return any(s in f" {low} " for s in (" rfp ", " rfi ", " eoi ", " nit "))


def _rule_ie_header(low, toks):
    return bool(re.match(r"^\d{1,2}\s+(mon|tues|wed|thurs|fri|sat|sun)", low.strip()))


def _rule_pincode(low, toks):
    return bool(re.search(r"\b[1-9]\d{5}\b", low))


def _rule_foreign_sport(low, toks):
    # A sports story with NO India angle and NO achievement/merit signal.
    if not _has(low, *SPORT_WORDS):
        return False
    if _has(low, *INDIA_TOKENS) or _has(low, *MERIT_TOKENS):
        return False
    return True


def _rule_cricket_tour(low, toks):
    return _has(low, "cricket tour to", "set for tour", "matches in 40 days",
                "tour schedule announced")


def _rule_incident(low, toks):
    if _has(low, *INCIDENT_WORDS) and not _has(low, *POLICY_MARKERS):
        return True
    return False


def _rule_corporate(low, toks):
    # NOTE: bare 'acquisition' removed — it collides with LAND acquisition for
    # highways/infra (policy, high RPSC value). Corporate M&A uses these instead.
    return _has(low, "restructuring", "merger", " m&a ", "cci approves",
                "proposed combination", "stake sale", " ipo ", "quarterly results",
                "company earnings", "share price", "q4 results")


def _rule_party_internal(low, toks):
    return _has(low, "party revolt", "internal party", "intra-party", "vs mamata",
                "rebellion against", "mutiny", "expelled from", "factionalism",
                "party president resign", "internal rumblings", "puts exit plan",
                "exit plan on hold")


def _rule_lowvalue_sports_study(low, toks):
    # Low-RPSC-value "study/profile of a cricketer" filler (e.g. IIM study on a
    # teen cricket phenom) — not a testable CA event.
    return _has(low, "iim study on", "cricket study", "study on cricketer",
                "study on vaibhav", "sooryavanshi", "vaibhav suryavanshi",
                "vaibhav sooryavanshi")


def _rule_ceremonial(low, toks):
    return _has(low, "interacts with", "student delegation", "cycling ride",
                "bicycle day", "walkathon", "pays tribute", "pays homage",
                "extends greetings", "greets the nation", "condoles", "felicitat",
                "photo exhibition", "cultural programme", "flag-off", "flagged off")


RULES = [
    ("advertisement",        _rule_ad),
    ("ie-page-header",       _rule_ie_header),
    ("address/pincode",      _rule_pincode),
    ("foreign-sport-no-india", _rule_foreign_sport),
    ("cricket-tour-announcement", _rule_cricket_tour),
    ("incident-no-policy",   _rule_incident),
    ("corporate-m&a",        _rule_corporate),
    ("party-internal",       _rule_party_internal),
    ("lowvalue-sports-study", _rule_lowvalue_sports_study),
    ("ceremonial-no-data",   _rule_ceremonial),
]


def check(item):
    """Return (keep: bool, reason: str|None). reason is the rule label on drop."""
    text = (item.get("text") or "") + " " + (item.get("title") or "")
    low = text.lower()
    toks = tokenize(text)
    for label, pred in RULES:
        try:
            if pred(low, toks):
                return False, label
        except Exception:
            continue
    return True, None


def apply(items):
    """Partition items into (kept, dropped). dropped items carry '_reason'."""
    kept, dropped = [], []
    for it in items:
        keep, reason = check(it)
        if keep:
            kept.append(it)
        else:
            d = dict(it)
            d["_reason"] = reason
            dropped.append(d)
    return kept, dropped
