#!/usr/bin/env bash
# Railway startup script — checks SERVICE_MODE to run correct process.
# Used by Procfile: web: bash start.sh
set -e

echo "=== PaperSe Railway Start ==="
echo "SERVICE_MODE=${SERVICE_MODE:-not set}"
echo "PORT=${PORT:-not set}"
echo "Python: $(python3 --version 2>/dev/null || python --version 2>/dev/null || echo 'NOT FOUND')"
echo "PWD: $(pwd)"
echo "=============================="

if [ "${SERVICE_MODE}" = "curator" ]; then
    echo "Starting Curator Flask server..."
    exec python3 pipelines/curator_server.py
else
    echo "Starting Daily CA Pipeline..."
    exec bash ./pipelines/run_daily.sh
fi
