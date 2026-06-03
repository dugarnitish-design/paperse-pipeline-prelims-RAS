#!/usr/bin/env python3
"""
Railway launcher — uses Python directly (avoids bash -c PATH issue with nixpacks).
Routes to curator server or pipeline based on SERVICE_MODE env var.
"""
import os, sys, subprocess

SERVICE_MODE = os.environ.get("SERVICE_MODE", "")
PORT = os.environ.get("PORT", "8080")
print(f"=== PaperSe Railway Launcher ===")
print(f"SERVICE_MODE={SERVICE_MODE or 'not set'}")
print(f"PORT={PORT}")
print(f"Python={sys.version}")
print(f"CWD={os.getcwd()}")

if SERVICE_MODE == "curator":
    print("Starting Curator Flask server...")
    # Import and run Flask app directly (avoids subprocess PATH issues)
    sys.path.insert(0, os.getcwd())
    os.environ.setdefault("PORT", PORT)
    import pipelines.curator_server as cs
    cs.app.run(host="0.0.0.0", port=int(PORT), debug=False)
else:
    print("Starting Daily CA Pipeline...")
    result = subprocess.run(["bash", "./pipelines/run_daily.sh"] + sys.argv[1:])
    sys.exit(result.returncode)
