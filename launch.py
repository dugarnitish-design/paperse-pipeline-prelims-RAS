#!/usr/bin/env python3
"""
Railway launcher — avoids bash -c PATH issue with nixpacks.
Routes to curator Flask server or pipeline based on SERVICE_MODE env var.
"""
import os, sys, subprocess

SERVICE_MODE = os.environ.get("SERVICE_MODE", "")
PORT = int(os.environ.get("PORT", "8080"))

print(f"=== PaperSe Railway Launcher ===", flush=True)
print(f"SERVICE_MODE={SERVICE_MODE or 'not set'}", flush=True)
print(f"PORT={PORT}", flush=True)
print(f"Python={sys.version[:20]}", flush=True)


def _require_env(names):
    """Fail fast if a required secret is missing or still a template placeholder.

    Root cause of two prior production crashes: this Railway service was created
    by pasting the RAILWAY_DEPLOYMENT.md template block verbatim, leaving
    `<your-...>` placeholders in SUPABASE_SERVICE_KEY / ANTHROPIC_API_KEY. They
    didn't 'revert' — they were never real, and surfaced as cryptic 401s one
    crash at a time. This guard turns that into an obvious startup error naming
    the exact bad var, so a misconfigured deploy can never silently run.
    """
    bad = []
    for k in names:
        v = (os.environ.get(k) or "").strip()
        if (not v) or v.startswith("<") or v.lower().startswith("your-"):
            bad.append(f"{k}={v[:18]!r}")
    if bad:
        print("FATAL: required env var(s) missing or still a PLACEHOLDER — set the "
              "real values in Railway → Variables:", flush=True)
        for b in bad:
            print(f"   • {b}", flush=True)
        sys.exit(1)


if SERVICE_MODE == "curator":
    print("Starting Curator Flask server...", flush=True)
    # Fail fast on missing/placeholder secrets (publish regenerates the PDF →
    # needs Anthropic for PYQ linking + Telegram to post + Supabase).
    _require_env(["SUPABASE_SERVICE_KEY", "TELEGRAM_BOT_TOKEN", "ANTHROPIC_API_KEY", "CURATOR_CHAT_ID"])
    # Add project root to path so 'pipelines' package is importable
    sys.path.insert(0, os.getcwd())
    # Import the real curator server app
    from pipelines.curator_server import app, _start_telegram_polling, _start_autopublish_scheduler
    _start_telegram_polling()
    _start_autopublish_scheduler()   # daily 08:30 IST auto-publish of pending drafts
    print(f"Curator dashboard live on port {PORT}", flush=True)
    app.run(host="0.0.0.0", port=PORT, debug=False)
else:
    import datetime
    # ── Cron-window guard (deploy-proof) ──────────────────────────────────────
    # Railway starts this container on EVERY deploy/restart AS WELL AS on the
    # cron schedule. A Railway *service* env var (e.g. RAILWAY_CRON_RUN) is set
    # on EVERY start, so it can't distinguish a cron run from a deploy. Instead
    # we gate on the clock: the cron fires at CRON_HOUR_UTC:00 (default 01:00 UTC
    # = 06:30 IST), so only a start inside that window is the cron. Any other
    # start (a git-push deploy/restart) exits 0 without running — saving API $.
    #   • FORCE_RUN=true / --force → run regardless (manual test)
    #   • an explicit date arg     → run regardless (e.g. `python3 launch.py 2026-06-05`)
    date_args = [a for a in sys.argv[1:] if not a.startswith("-")]
    force     = (os.environ.get("FORCE_RUN", "").lower() in ("1", "true", "yes")
                 or "--force" in sys.argv
                 or bool(date_args))

    cron_hour = int(os.environ.get("CRON_HOUR_UTC", "1"))
    cron_min  = int(os.environ.get("CRON_MIN_UTC", "0"))   # supports e.g. 18:30 UTC cron
    window    = int(os.environ.get("CRON_WINDOW_MIN", "20"))
    now       = datetime.datetime.now(datetime.timezone.utc)
    mins_into = (now.hour - cron_hour) * 60 + (now.minute - cron_min)
    in_window = 0 <= mins_into < window

    if not (force or in_window):
        print(f"Deploy/restart at UTC {now:%H:%M} — not the cron window "
              f"({cron_hour:02d}:{cron_min:02d} +{window}m). Exiting 0 WITHOUT running the pipeline. "
              f"(set FORCE_RUN=true or pass a date arg to run manually)", flush=True)
        sys.exit(0)

    # Inside the cron window (or forced) and about to actually run — validate
    # secrets now so a placeholder fails loudly here instead of as a mid-run 401.
    _require_env(["SUPABASE_SERVICE_KEY", "ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "CURATOR_CHAT_ID"])
    print(f"Cron window (UTC {now:%H:%M}, force={force}) — Starting Daily CA...", flush=True)
    result = subprocess.run(["bash", "./pipelines/run_daily.sh"] + date_args)
    sys.exit(result.returncode)
