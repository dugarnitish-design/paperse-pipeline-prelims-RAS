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
    print("Starting Daily CA Pipeline...", flush=True)
    result = subprocess.run(["bash", "./pipelines/run_daily.sh"] + sys.argv[1:])
    sys.exit(result.returncode)
