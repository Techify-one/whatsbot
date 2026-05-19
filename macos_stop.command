#!/bin/bash
# WhatsBot — macOS stop script. Equivalente do ``windows_stop.bat``.
# Encerra o servidor uvicorn e o subprocess GOWA.
set -u

WEB_PORT="${WHATSBOT_WEB_PORT:-8080}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Parando o WhatsBot..."

# GOWA deste diretorio.
pkill -f "$SCRIPT_DIR/bin/gowa" 2>/dev/null || true

# Tudo que estiver escutando na porta web (uvicorn + workers).
pkill -f "uvicorn server.dev" 2>/dev/null || true
pids=$(lsof -ti "tcp:${WEB_PORT}" 2>/dev/null || true)
if [ -n "$pids" ]; then
    kill -TERM $pids 2>/dev/null || true
    sleep 2
    pids=$(lsof -ti "tcp:${WEB_PORT}" 2>/dev/null || true)
    [ -n "$pids" ] && kill -KILL $pids 2>/dev/null || true
fi

echo "WhatsBot parado."
sleep 1
