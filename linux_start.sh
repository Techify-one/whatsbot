#!/bin/bash
# WhatsBot — Linux native dev launcher (hot-reload).
#
# Equivalente do ``windows_start.bat --server`` no Windows: roda Python local
# (sem Docker) com ``uvicorn --reload`` watchando core + plugins. Edita
# qualquer ``.py`` em ``server/``, ``agent/``, ``config/``, ``gowa/``, ``db/``,
# ``plugins/`` ou ``storages/plugins/`` e o worker reinicia sozinho.
#
# O loop externo cobre quando o uvicorn parent morre (raro) ou um plugin
# chama ``os._exit`` (enable/disable via UI dispara ``schedule_restart``).
#
# Pra rodar em modo Docker (prod-like) use ``./docker_start.sh``.
set -u

# Porta web (frontend + REST + WS). Pode ser sobrescrita externamente.
export WHATSBOT_WEB_PORT="${WHATSBOT_WEB_PORT:-8090}"
# Porta interna do subprocess GOWA (não exposta).
export WHATSBOT_GOWA_PORT="${WHATSBOT_GOWA_PORT:-64998}"

cd "$(dirname "$0")"

# Garante venv. Quem rodar pela 1ª vez precisa de ``python -m venv venv`` +
# ``venv/bin/pip install -r requirements.txt`` — não automatizamos aqui pra
# manter o launcher idempotente e previsível.
if [ ! -x ./venv/bin/python ]; then
    echo "[linux_start] venv não encontrado em ./venv — crie com:"
    echo "    python3 -m venv venv && ./venv/bin/pip install -r requirements.txt"
    exit 1
fi

while true; do
    echo "[linux_start] $(date '+%H:%M:%S') starting uvicorn --reload (port $WHATSBOT_WEB_PORT)..."
    ./venv/bin/python -m uvicorn server.dev:app \
        --host 0.0.0.0 --port "$WHATSBOT_WEB_PORT" \
        --reload \
        --reload-dir server \
        --reload-dir agent \
        --reload-dir config \
        --reload-dir gowa \
        --reload-dir db \
        --reload-dir plugins \
        --reload-dir storages/plugins \
        --log-level warning
    rc=$?
    echo "[linux_start] $(date '+%H:%M:%S') uvicorn exited rc=$rc"
    # Mata GOWA orphan antes de relançar (senão a porta fica presa)
    pkill -f "$(pwd)/bin/gowa" 2>/dev/null || true
    sleep 2
done
