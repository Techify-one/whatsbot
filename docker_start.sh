#!/bin/bash
# WhatsBot — Docker launcher (prod-like).
# Sobe o container via docker compose, replicando o ambiente de deploy
# (Coolify/Swarm). Lê DATABASE_URL do .env quando presente.
set -e

# Create persistent data directories
mkdir -p data/storages data/statics data/logs

# Build and start
docker compose up --build -d

echo ""
echo "WhatsBot iniciado (Docker)!"
echo "Web UI: http://localhost:${WHATSBOT_WEB_PORT:-8080}"
echo "Logs:   docker compose logs -f"
