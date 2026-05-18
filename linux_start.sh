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

# Versão do GOWA que casa com o cliente em gowa/client.py e o Dockerfile.
GOWA_VERSION="${GOWA_VERSION:-8.5.0}"

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "[linux_start] ERRO: '$1' não encontrado. Instale com o gerenciador de pacotes da sua distro."
        return 1
    fi
}

# ===== Python + venv =====
if [ ! -x ./venv/bin/python ]; then
    require_cmd python3 || exit 1
    echo "[linux_start] criando venv em ./venv ..."
    python3 -m venv venv
fi
echo "[linux_start] sincronizando dependências (pip install -r requirements.txt)..."
./venv/bin/pip install -q -r requirements.txt

# ===== GOWA Linux binary =====
# Equivalente ao Dockerfile: baixa a release oficial do go-whatsapp-web-multidevice
# se o binário local não existir. .gitignore exclui bin/gowa — cada máquina
# baixa o seu na 1ª execução.
if [ ! -x ./bin/gowa ]; then
    require_cmd curl  || exit 1
    require_cmd unzip || exit 1
    case "$(uname -m)" in
        x86_64|amd64)  TARGETARCH="amd64" ;;
        aarch64|arm64) TARGETARCH="arm64" ;;
        *) echo "[linux_start] arquitetura $(uname -m) não suportada pelo GOWA"; exit 1 ;;
    esac
    GOWA_URL="https://github.com/aldinokemal/go-whatsapp-web-multidevice/releases/download/v${GOWA_VERSION}/whatsapp_${GOWA_VERSION}_linux_${TARGETARCH}.zip"
    echo "[linux_start] baixando GOWA v${GOWA_VERSION} (linux-${TARGETARCH})..."
    mkdir -p bin
    tmpdir=$(mktemp -d)
    trap 'rm -rf "$tmpdir"' EXIT
    if ! curl -fsSL "$GOWA_URL" -o "$tmpdir/gowa.zip"; then
        echo "[linux_start] ERRO: falha ao baixar $GOWA_URL"
        exit 1
    fi
    unzip -q "$tmpdir/gowa.zip" -d "$tmpdir/extract"
    if [ ! -f "$tmpdir/extract/linux-${TARGETARCH}" ]; then
        echo "[linux_start] ERRO: arquivo linux-${TARGETARCH} não encontrado dentro do zip do GOWA"
        ls -la "$tmpdir/extract" || true
        exit 1
    fi
    cp "$tmpdir/extract/linux-${TARGETARCH}" ./bin/gowa
    chmod +x ./bin/gowa
    rm -rf "$tmpdir"
    trap - EXIT
    echo "[linux_start] GOWA pronto em ./bin/gowa"
fi

# Libera as portas que vamos usar — equivalente ao taskkill do
# ``windows_start.bat``. Mata QUALQUER processo escutando nas portas alvo
# (uvicorn antigo nosso, instância de outro workspace, qualquer coisa) e
# também gowa órfão deste diretório.
free_port() {
    local port="$1"
    if command -v fuser >/dev/null 2>&1; then
        fuser -k -TERM "${port}/tcp" 2>/dev/null || true
    elif command -v lsof >/dev/null 2>&1; then
        local pids
        pids=$(lsof -ti "tcp:${port}" 2>/dev/null || true)
        if [ -n "$pids" ]; then
            kill -TERM $pids 2>/dev/null || true
        fi
    else
        echo "[linux_start] aviso: nem 'fuser' nem 'lsof' encontrados; não consigo liberar :$port"
    fi
}
echo "[linux_start] $(date '+%H:%M:%S') liberando portas $WHATSBOT_WEB_PORT (web) e $WHATSBOT_GOWA_PORT (gowa)..."
free_port "$WHATSBOT_WEB_PORT"
free_port "$WHATSBOT_GOWA_PORT"
pkill -f "$(pwd)/bin/gowa" 2>/dev/null || true
sleep 1

# uvicorn valida cada --reload-dir antes de subir; numa instalação nova
# storages/plugins ainda não existe (é criada em runtime por create_app).
mkdir -p storages/plugins

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
