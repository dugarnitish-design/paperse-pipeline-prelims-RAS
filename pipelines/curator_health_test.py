#!/usr/bin/env python3
"""
Minimal Flask health test — no pipeline imports.
Used to verify Flask + Railway port config works before adding full app.
Run via: python3 pipelines/curator_health_test.py
"""
import os
import sys
from flask import Flask, jsonify

app = Flask(__name__)

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "port": os.environ.get("PORT", "not set"),
        "service_mode": os.environ.get("SERVICE_MODE", "not set"),
        "python": sys.version,
    })

@app.route("/")
def index():
    return "<h1>Curator server is running!</h1>"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"Starting minimal health server on port {port}", flush=True)
    app.run(host="0.0.0.0", port=port)
