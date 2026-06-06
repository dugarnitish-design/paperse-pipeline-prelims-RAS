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
    # ── Cron-only guard ───────────────────────────────────────────────────────
    # Railway starts this container on EVERY deploy/restart AND on the cron. We
    # only run the daily pipeline when it's a cron run, identified by the env var
    # RAILWAY_CRON_RUN=true (set in the pipeline service's Variables). Any other
    # start (a git-push deploy/restart) exits 0 without running — saving API $.
    #   • --force flag         → run regardless (manual test)
    #   • an explicit date arg → run regardless (e.g. `python3 launch.py 2026-06-05`)
    is_cron   = os.environ.get("RAILWAY_CRON_RUN", "false").lower() == "true"
    date_args = [a for a in sys.argv[1:] if not a.startswith("-")]
    force     = "--force" in sys.argv or bool(date_args)

    if not (is_cron or force):
        print("Not a cron run (RAILWAY_CRON_RUN != true) — this is a deploy/restart. "
              "Exiting 0 WITHOUT running the pipeline. "
              "(pass --force or a date arg to run manually)", flush=True)
        sys.exit(0)

    print(f"Pipeline run (RAILWAY_CRON_RUN={is_cron}, force={force}) — "
          f"Starting Daily CA...", flush=True)
    result = subprocess.run(["bash", "./pipelines/run_daily.sh"] + date_args)
    sys.exit(result.returncode)
