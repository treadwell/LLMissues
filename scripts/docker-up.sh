#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

start_port="${HOST_PORT:-8012}"
port="$start_port"

while lsof -iTCP:"$port" -sTCP:LISTEN -nP >/dev/null 2>&1; do
  port=$((port + 1))
done

export HOST_PORT="$port"
echo "Starting iimcs on host port $HOST_PORT"
docker compose up --build -d
echo "App URL: http://127.0.0.1:$HOST_PORT"
