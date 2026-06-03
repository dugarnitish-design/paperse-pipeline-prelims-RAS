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
    print("Starting Curator Flask server...", flush=True)
    # Minimal inline Flask — verifies Python + Flask work before adding imports
    from flask import Flask, jsonify, redirect, url_for
    app = Flask(__name__)

    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "port": PORT, "service": "curator", "python": sys.version[:10]})

    @app.route("/")
    def index():
        return redirect("/health")

    print(f"Flask listening on 0.0.0.0:{PORT}", flush=True)
    app.run(host="0.0.0.0", port=int(PORT), debug=False)
else:
    print("Starting Daily CA Pipeline...", flush=True)
    result = subprocess.run(["bash", "./pipelines/run_daily.sh"] + sys.argv[1:])
    sys.exit(result.returncode)
