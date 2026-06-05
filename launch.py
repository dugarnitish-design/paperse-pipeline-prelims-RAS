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

if SERVICE_MODE == "curator":
    print("Starting Curator Flask server...", flush=True)
    # Add project root to path so 'pipelines' package is importable
    sys.path.insert(0, os.getcwd())
    # Import the real curator server app
    from pipelines.curator_server import app, _start_telegram_polling
    _start_telegram_polling()
    print(f"Curator dashboard live on port {PORT}", flush=True)
    app.run(host="0.0.0.0", port=PORT, debug=False)
else:
    import datetime
    # ── Cron-only guard ───────────────────────────────────────────────────────
    # Railway starts this container on EVERY deploy/restart as well as on the
    # cron schedule. We only want the daily pipeline to fire on the cron — not on
    # each git push. The cron runs at CRON_HOUR_UTC:00 (default 01:00 UTC =
    # 06:30 IST). A start outside that window is a deploy/restart → exit 0.
    #   • FORCE_RUN=true        → run regardless (manual test)
    #   • an explicit date arg  → run regardless (e.g. `python3 launch.py 2026-06-05`)
    force = os.environ.get("FORCE_RUN", "").lower() in ("1", "true", "yes")
    has_arg = len(sys.argv) > 1
    cron_hour = int(os.environ.get("CRON_HOUR_UTC", "1"))
    window = int(os.environ.get("CRON_WINDOW_MIN", "20"))
    now = datetime.datetime.utcnow()
    mins_into = (now.hour - cron_hour) * 60 + now.minute
    in_window = 0 <= mins_into < window

    if not (force or has_arg or in_window):
        print(f"Deploy/restart at UTC {now:%H:%M} — not the cron window "
              f"({cron_hour:02d}:00 +{window}m). Exiting 0 WITHOUT running the pipeline. "
              f"(set FORCE_RUN=true to force a manual run)", flush=True)
        sys.exit(0)

    print(f"Cron window (UTC {now:%H:%M}, force={force}, arg={has_arg}) — "
          f"Starting Daily CA Pipeline...", flush=True)
    result = subprocess.run(["bash", "./pipelines/run_daily.sh"] + sys.argv[1:])
    sys.exit(result.returncode)
