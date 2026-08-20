"""Outbound proxy for the GOWA WhatsApp connection.

The WhatsApp WebSocket is dialed by ``whatsmeow`` directly, so the usual
``HTTP_PROXY``/``HTTPS_PROXY`` variables do NOT apply to it. GOWA exposes a
dedicated knob for that dialer (``WHATSAPP_PROXY`` env / ``--whatsapp-proxy``
flag), available from GOWA **8.11.0** onwards.

We deliberately pass it through the **environment** instead of the CLI flag:

* an older binary (< 8.11.0) ignores an unknown env var, while an unknown flag
  makes cobra abort and GOWA never starts;
* the proxy URL usually embeds a password, and env vars do not show up in
  ``ps aux`` the way argv does.

This module owns the whole proxy story: config shape, validation, URL
composition, masking for display and a real connectivity test (SOCKS5 handshake
or HTTP CONNECT) done with the stdlib only.
"""

from __future__ import annotations

import base64
import logging
import os
import socket
import ssl
import time
from urllib.parse import quote, urlsplit, urlunsplit

from gowa import binary as gowa_binary

logger = logging.getLogger(__name__)

# GOWA release that introduced the outbound proxy (see release notes of 8.11.0,
# "Outbound Proxy Support (#664)"). Anything older silently ignores it.
MIN_GOWA_VERSION = "8.11.0"

# Variable read by GOWA (viper AutomaticEnv over the `whatsapp_proxy` key).
ENV_VAR = "WHATSAPP_PROXY"
# Escape hatch for Docker/Coolify: when set, it wins over the panel config.
ENV_OVERRIDE = "WHATSBOT_GOWA_PROXY"

SCHEMES = ("socks5", "http", "https")
DEFAULT_PORTS = {"socks5": 1080, "http": 8080, "https": 443}

# Placeholder sent to the frontend in place of a stored password. Coming back
# unchanged in a PUT it means "keep what is saved".
PASSWORD_MASK = "***"

# Target used by the connectivity test: the very host GOWA has to reach.
TEST_HOST = "web.whatsapp.com"
TEST_PORT = 443
TEST_TIMEOUT_SEC = 10.0

CONFIG_DEFAULTS: dict[str, object] = {
    "gowa_proxy_enabled": False,
    "gowa_proxy_mode": "fields",      # "fields" (ip/porta/usuário/senha) | "url"
    "gowa_proxy_scheme": "socks5",
    "gowa_proxy_host": "",
    "gowa_proxy_port": 0,
    "gowa_proxy_username": "",
    "gowa_proxy_password": "",
    "gowa_proxy_url": "",
}

_SOCKS5_ERRORS = {
    0x01: "falha geral no servidor proxy",
    0x02: "conexão não permitida pelas regras do proxy",
    0x03: "rede inacessível a partir do proxy",
    0x04: "host de destino inacessível a partir do proxy",
    0x05: "conexão recusada pelo destino",
    0x06: "TTL expirado",
    0x07: "comando não suportado pelo proxy",
    0x08: "tipo de endereço não suportado pelo proxy",
}


class ProxyConfigError(ValueError):
    """Invalid proxy configuration (message is user-facing, pt-BR)."""


class ProxyTestError(RuntimeError):
    """Connectivity test failed (message is user-facing, pt-BR)."""


# ── Version gate ───────────────────────────────────────────────────────


def supports_proxy(version: str | None = None) -> bool:
    """True when the given (or installed) GOWA is new enough for the proxy."""
    if version is None:
        version = gowa_binary.installed_version()
    return gowa_binary.version_cmp(version, MIN_GOWA_VERSION) >= 0


# ── Config normalization ───────────────────────────────────────────────


def _split_host_port(host: str) -> tuple[str, int | None]:
    """Accept ``1.2.3.4:1080`` pasted into the host field.

    IPv6 must come bracketed (``[::1]:1080``); an unbracketed one is ambiguous
    and is left untouched.
    """
    if host.startswith("["):
        closing = host.find("]")
        if closing > 0:
            rest = host[closing + 1:]
            bare = host[1:closing]
            if rest.startswith(":") and rest[1:].isdigit():
                return bare, int(rest[1:])
            return bare, None
        return host, None
    if host.count(":") == 1:
        left, right = host.split(":", 1)
        if right.isdigit():
            return left, int(right)
    return host, None


def _coerce_port(value: object) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise ProxyConfigError("Porta do proxy inválida: informe apenas números.")


def _format_host(host: str) -> str:
    """Bracket a bare IPv6 literal so it can go into a URL."""
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def normalize(raw: dict, stored: dict | None = None) -> dict:
    """Merge ``raw`` (partial, from the API) over ``stored`` and validate.

    Returns the canonical config dict. Raises :class:`ProxyConfigError` with a
    user-facing message when the result is enabled but unusable.
    """
    stored = stored or {}

    def pick(key: str, default: object = "") -> object:
        if key in raw and raw[key] is not None:
            return raw[key]
        return stored.get(key, default)

    enabled = bool(pick("enabled", False))

    mode = str(pick("mode", "fields") or "fields").strip().lower()
    if mode not in ("fields", "url"):
        mode = "fields"

    scheme = str(pick("scheme", "socks5") or "socks5").strip().lower()
    host = str(pick("host", "") or "").strip()
    port = _coerce_port(pick("port", 0))
    username = str(pick("username", "") or "").strip()

    password = raw.get("password")
    if password is None or password == PASSWORD_MASK:
        password = str(stored.get("password", "") or "")
    password = str(password)

    url = str(pick("url", "") or "").strip()
    if url and PASSWORD_MASK in url:
        url = _restore_masked_password(url, str(stored.get("url", "") or ""))

    if host:
        bare_host, embedded_port = _split_host_port(host)
        host = bare_host
        if embedded_port and not port:
            port = embedded_port

    cfg = {
        "enabled": enabled,
        "mode": mode,
        "scheme": scheme,
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "url": url,
    }

    if not enabled:
        # Nothing is applied while disabled, so half-filled forms may be saved.
        return cfg

    if mode == "url":
        parsed = parse_url(url)
        cfg["scheme"] = parsed["scheme"]
        cfg["url"] = parsed["url"]
    else:
        if scheme not in SCHEMES:
            raise ProxyConfigError(
                f"Tipo de proxy inválido: use {', '.join(SCHEMES)}."
            )
        if not host:
            raise ProxyConfigError("Informe o IP ou o host do proxy.")
        if any(ch in host for ch in " \t/@") or "://" in host:
            raise ProxyConfigError(
                "Host do proxy inválido: informe só o IP ou o domínio "
                "(sem http://, sem barra e sem usuário)."
            )
        if not port:
            port = DEFAULT_PORTS[scheme]
            cfg["port"] = port
        if not 1 <= port <= 65535:
            raise ProxyConfigError("Porta do proxy fora da faixa (1 a 65535).")
        if password and not username:
            raise ProxyConfigError("Senha informada sem usuário do proxy.")

    return cfg


def parse_url(url: str) -> dict:
    """Validate a full proxy URL and split it into fields."""
    url = (url or "").strip()
    if not url:
        raise ProxyConfigError("Informe a URL do proxy.")
    if "://" not in url:
        raise ProxyConfigError(
            "URL do proxy sem o tipo. Exemplo: socks5://usuario:senha@1.2.3.4:1080"
        )
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise ProxyConfigError(f"URL do proxy inválida: {exc}")

    scheme = (parts.scheme or "").lower()
    if scheme not in SCHEMES:
        raise ProxyConfigError(
            f"Tipo de proxy não suportado: {scheme or '(vazio)'}. "
            f"Use {', '.join(SCHEMES)}."
        )
    host = parts.hostname or ""
    if not host:
        raise ProxyConfigError("URL do proxy sem host.")
    try:
        port = parts.port or DEFAULT_PORTS[scheme]
    except ValueError:
        raise ProxyConfigError("Porta do proxy inválida na URL.")
    if not 1 <= port <= 65535:
        raise ProxyConfigError("Porta do proxy fora da faixa (1 a 65535).")

    username = parts.username or ""
    password = parts.password or ""

    userinfo = ""
    if username:
        userinfo = quote(username, safe="")
        if password:
            userinfo += ":" + quote(password, safe="")
        userinfo += "@"
    canonical = f"{scheme}://{userinfo}{_format_host(host)}:{port}"

    return {
        "scheme": scheme,
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "url": canonical,
    }


def build_url(cfg: dict) -> str:
    """Compose the final proxy URL from a normalized config. '' when off."""
    if not cfg.get("enabled"):
        return ""
    if cfg.get("mode") == "url":
        return str(cfg.get("url") or "")

    scheme = str(cfg.get("scheme") or "socks5")
    host = _format_host(str(cfg.get("host") or ""))
    port = _coerce_port(cfg.get("port")) or DEFAULT_PORTS.get(scheme, 1080)
    userinfo = ""
    username = str(cfg.get("username") or "")
    password = str(cfg.get("password") or "")
    if username:
        userinfo = quote(username, safe="")
        if password:
            userinfo += ":" + quote(password, safe="")
        userinfo += "@"
    return f"{scheme}://{userinfo}{host}:{port}"


def mask_url(url: str) -> str:
    """Replace the password inside a proxy URL with ``***`` for display."""
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.password:
        return url
    username = parts.username or ""
    userinfo = (quote(username, safe="") if username else "") + f":{PASSWORD_MASK}@"
    host = _format_host(parts.hostname or "")
    try:
        port = parts.port
    except ValueError:
        port = None
    hostport = f"{host}:{port}" if port else host
    return urlunsplit((parts.scheme, userinfo + hostport, parts.path, parts.query, parts.fragment))


def _restore_masked_password(new_url: str, stored_url: str) -> str:
    """Put the stored password back into a URL that came in masked."""
    if not stored_url:
        return new_url.replace(f":{PASSWORD_MASK}@", "@")
    try:
        stored_password = urlsplit(stored_url).password or ""
    except ValueError:
        stored_password = ""
    if not stored_password:
        return new_url.replace(f":{PASSWORD_MASK}@", "@")
    return new_url.replace(f":{PASSWORD_MASK}@", ":" + quote(stored_password, safe="") + "@", 1)


# ── Stored config ──────────────────────────────────────────────────────


def current_config() -> dict:
    """Read the proxy config straight from the config table.

    Deliberately independent from a ``Settings`` instance so ``GOWAManager``
    (which has none) can build the subprocess environment on its own.
    """
    from db.repositories import config_repo

    raw = {}
    for key, default in CONFIG_DEFAULTS.items():
        raw[key] = config_repo.get(key, default)
    return {
        "enabled": bool(raw["gowa_proxy_enabled"]),
        "mode": str(raw["gowa_proxy_mode"] or "fields"),
        "scheme": str(raw["gowa_proxy_scheme"] or "socks5"),
        "host": str(raw["gowa_proxy_host"] or ""),
        "port": _coerce_port(raw["gowa_proxy_port"]),
        "username": str(raw["gowa_proxy_username"] or ""),
        "password": str(raw["gowa_proxy_password"] or ""),
        "url": str(raw["gowa_proxy_url"] or ""),
    }


def save_config(cfg: dict) -> None:
    """Persist a normalized config dict."""
    from db.repositories import config_repo

    config_repo.set_many({
        "gowa_proxy_enabled": bool(cfg.get("enabled")),
        "gowa_proxy_mode": cfg.get("mode", "fields"),
        "gowa_proxy_scheme": cfg.get("scheme", "socks5"),
        "gowa_proxy_host": cfg.get("host", ""),
        "gowa_proxy_port": _coerce_port(cfg.get("port")),
        "gowa_proxy_username": cfg.get("username", ""),
        "gowa_proxy_password": cfg.get("password", ""),
        "gowa_proxy_url": cfg.get("url", ""),
    })


def current_url() -> str:
    """Effective proxy URL for the GOWA subprocess. '' when there is none.

    Never raises: a broken saved config must not stop GOWA from starting.
    """
    override = os.environ.get(ENV_OVERRIDE, "").strip()
    if override:
        return override
    try:
        cfg = current_config()
        if not cfg["enabled"]:
            return ""
        return build_url(normalize(cfg, cfg))
    except Exception as exc:
        logger.warning("Proxy do GOWA ignorado (config inválida): %s", exc)
        return ""


# ── Connectivity test ──────────────────────────────────────────────────


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    buf = b""
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            raise ProxyTestError("O proxy encerrou a conexão antes de responder.")
        buf += chunk
    return buf


def _socks5_handshake(sock: socket.socket, username: str, password: str,
                      target_host: str, target_port: int) -> None:
    methods = b"\x00\x02" if username else b"\x00"
    sock.sendall(bytes([0x05, len(methods)]) + methods)
    resp = _recv_exact(sock, 2)
    if resp[0] != 0x05:
        raise ProxyTestError("Resposta não é SOCKS5: confira o tipo do proxy (talvez seja HTTP).")
    method = resp[1]
    if method == 0xFF:
        raise ProxyTestError(
            "O proxy recusou os métodos de autenticação. "
            "Se ele exige usuário e senha, preencha os dois campos."
        )
    if method == 0x02:
        if not username:
            raise ProxyTestError("Este proxy exige usuário e senha.")
        user_bytes = username.encode("utf-8")
        pass_bytes = password.encode("utf-8")
        if len(user_bytes) > 255 or len(pass_bytes) > 255:
            raise ProxyTestError("Usuário ou senha do proxy longos demais (máx. 255 bytes).")
        sock.sendall(b"\x01" + bytes([len(user_bytes)]) + user_bytes
                     + bytes([len(pass_bytes)]) + pass_bytes)
        auth = _recv_exact(sock, 2)
        if auth[1] != 0x00:
            raise ProxyTestError("Usuário ou senha do proxy recusados.")
    elif method != 0x00:
        raise ProxyTestError(f"Método de autenticação não suportado (0x{method:02x}).")

    host_bytes = target_host.encode("idna")
    sock.sendall(b"\x05\x01\x00\x03" + bytes([len(host_bytes)]) + host_bytes
                 + target_port.to_bytes(2, "big"))
    reply = _recv_exact(sock, 4)
    if reply[1] != 0x00:
        raise ProxyTestError(
            _SOCKS5_ERRORS.get(reply[1], f"o proxy recusou a conexão (código {reply[1]})")
            .capitalize() + "."
        )
    atyp = reply[3]
    if atyp == 0x01:
        _recv_exact(sock, 4 + 2)
    elif atyp == 0x03:
        length = _recv_exact(sock, 1)[0]
        _recv_exact(sock, length + 2)
    elif atyp == 0x04:
        _recv_exact(sock, 16 + 2)


def _http_connect(sock: socket.socket, username: str, password: str,
                  target_host: str, target_port: int) -> None:
    request = (
        f"CONNECT {target_host}:{target_port} HTTP/1.1\r\n"
        f"Host: {target_host}:{target_port}\r\n"
    )
    if username:
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        request += f"Proxy-Authorization: Basic {token}\r\n"
    request += "Proxy-Connection: keep-alive\r\n\r\n"
    sock.sendall(request.encode("utf-8"))

    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
        if len(buf) > 32768:
            break
    if not buf:
        raise ProxyTestError("O proxy não respondeu ao CONNECT.")

    status_line = buf.split(b"\r\n", 1)[0].decode("latin-1", "replace")
    parts = status_line.split(" ", 2)
    if len(parts) < 2 or not parts[1].isdigit():
        raise ProxyTestError(f"Resposta inesperada do proxy: {status_line[:120]}")
    status = int(parts[1])
    if status == 407:
        raise ProxyTestError("Usuário ou senha do proxy recusados (407).")
    if status != 200:
        raise ProxyTestError(
            f"O proxy respondeu {status} ao tentar alcançar {target_host}:{target_port}."
        )


def test_connection(url: str, target_host: str = TEST_HOST, target_port: int = TEST_PORT,
                    timeout: float = TEST_TIMEOUT_SEC) -> dict:
    """Open a real tunnel through the proxy to ``target_host``.

    Returns ``{"ok": bool, "message": str, "latency_ms": int}``; never raises.
    """
    started = time.monotonic()
    try:
        parsed = parse_url(url)
    except ProxyConfigError as exc:
        return {"ok": False, "message": str(exc), "latency_ms": 0}

    scheme = parsed["scheme"]
    host, port = parsed["host"], parsed["port"]
    username, password = parsed["username"], parsed["password"]

    sock = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.settimeout(timeout)
        if scheme == "https":
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=host)
        if scheme == "socks5":
            _socks5_handshake(sock, username, password, target_host, target_port)
        else:
            _http_connect(sock, username, password, target_host, target_port)
    except ProxyTestError as exc:
        return {"ok": False, "message": str(exc), "latency_ms": _elapsed_ms(started)}
    except socket.timeout:
        return {"ok": False,
                "message": f"Tempo esgotado ({timeout:.0f}s) falando com {host}:{port}.",
                "latency_ms": _elapsed_ms(started)}
    except ConnectionRefusedError:
        return {"ok": False, "message": f"Conexão recusada por {host}:{port}.",
                "latency_ms": _elapsed_ms(started)}
    except socket.gaierror:
        return {"ok": False, "message": f"Host do proxy não encontrado: {host}.",
                "latency_ms": _elapsed_ms(started)}
    except ssl.SSLError as exc:
        return {"ok": False, "message": f"Falha de TLS com o proxy: {exc}.",
                "latency_ms": _elapsed_ms(started)}
    except OSError as exc:
        return {"ok": False, "message": f"Falha ao conectar em {host}:{port}: {exc}.",
                "latency_ms": _elapsed_ms(started)}
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    elapsed = _elapsed_ms(started)
    return {
        "ok": True,
        "message": f"Proxy respondeu e liberou {target_host}:{target_port} em {elapsed} ms.",
        "latency_ms": elapsed,
    }


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
