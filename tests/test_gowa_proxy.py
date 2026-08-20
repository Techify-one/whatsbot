"""GOWA outbound-proxy tests.

Runs entirely offline: validation/URL composition is pure, and the connectivity
test is exercised against tiny SOCKS5 / HTTP proxy servers implemented here with
plain sockets.

    python tests/test_gowa_proxy.py
"""

import socket
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gowa import proxy as gowa_proxy  # noqa: E402

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  OK {name}")
    else:
        failed += 1
        print(f"  FAIL {name}" + (f" -> {detail}" if detail else ""))


def section(title: str) -> None:
    print("\n" + "-" * 60)
    print(f"  {title}")
    print("-" * 60)


def expect_error(name: str, raw: dict, stored: dict | None = None) -> None:
    try:
        gowa_proxy.normalize(raw, stored)
    except gowa_proxy.ProxyConfigError:
        check(name, True)
    else:
        check(name, False, "não levantou ProxyConfigError")


# ── Version gate ───────────────────────────────────────────────────────

section("Faixa de versão")

check("8.8.0 não suporta proxy", gowa_proxy.supports_proxy("8.8.0") is False)
check("8.10.9 não suporta proxy", gowa_proxy.supports_proxy("8.10.9") is False)
check("8.11.0 suporta proxy", gowa_proxy.supports_proxy("8.11.0") is True)
check("9.1.0 suporta proxy", gowa_proxy.supports_proxy("9.1.0") is True)


# ── Normalização e composição da URL ───────────────────────────────────

section("Campos separados")

cfg = gowa_proxy.normalize({
    "enabled": True, "mode": "fields", "scheme": "socks5",
    "host": "1.2.3.4", "port": 1080,
})
check("URL sem credenciais", gowa_proxy.build_url(cfg) == "socks5://1.2.3.4:1080",
      gowa_proxy.build_url(cfg))

cfg = gowa_proxy.normalize({
    "enabled": True, "mode": "fields", "scheme": "http",
    "host": "proxy.exemplo.com", "port": 8080,
    "username": "user", "password": "p@ss word",
})
check("usuário e senha são percent-encoded",
      gowa_proxy.build_url(cfg) == "http://user:p%40ss%20word@proxy.exemplo.com:8080",
      gowa_proxy.build_url(cfg))

cfg = gowa_proxy.normalize({"enabled": True, "host": "1.2.3.4:9050"})
check("host colado como ip:porta é separado", cfg["host"] == "1.2.3.4" and cfg["port"] == 9050,
      f"{cfg['host']}:{cfg['port']}")

cfg = gowa_proxy.normalize({"enabled": True, "scheme": "http", "host": "1.2.3.4"})
check("porta ausente cai no default do tipo", cfg["port"] == 8080, str(cfg["port"]))

cfg = gowa_proxy.normalize({"enabled": True, "host": "[2001:db8::1]", "port": 1080})
check("IPv6 é envelopado em colchetes na URL",
      gowa_proxy.build_url(cfg) == "socks5://[2001:db8::1]:1080", gowa_proxy.build_url(cfg))

check("desativado não gera URL", gowa_proxy.build_url(gowa_proxy.normalize(
    {"enabled": False, "host": "1.2.3.4", "port": 1080})) == "")

expect_error("host vazio é recusado", {"enabled": True, "host": "", "port": 1080})
expect_error("host com esquema é recusado", {"enabled": True, "host": "socks5://1.2.3.4", "port": 1080})
expect_error("porta fora da faixa é recusada", {"enabled": True, "host": "1.2.3.4", "port": 70000})
expect_error("porta não numérica é recusada", {"enabled": True, "host": "1.2.3.4", "port": "abc"})
expect_error("tipo inválido é recusado",
             {"enabled": True, "scheme": "ftp", "host": "1.2.3.4", "port": 1080})
expect_error("senha sem usuário é recusada",
             {"enabled": True, "host": "1.2.3.4", "port": 1080, "password": "x"})
check("config desativada aceita campos incompletos",
      gowa_proxy.normalize({"enabled": False, "host": ""})["enabled"] is False)


section("URL única")

cfg = gowa_proxy.normalize({"enabled": True, "mode": "url",
                            "url": "socks5://u:p@1.2.3.4:1080"})
check("URL válida é preservada", gowa_proxy.build_url(cfg) == "socks5://u:p@1.2.3.4:1080",
      gowa_proxy.build_url(cfg))
check("esquema é extraído da URL", cfg["scheme"] == "socks5")

cfg = gowa_proxy.normalize({"enabled": True, "mode": "url", "url": "HTTP://Proxy.Exemplo.com"})
check("URL sem porta ganha o default", gowa_proxy.build_url(cfg) == "http://proxy.exemplo.com:8080",
      gowa_proxy.build_url(cfg))

expect_error("URL sem esquema é recusada", {"enabled": True, "mode": "url", "url": "1.2.3.4:1080"})
expect_error("esquema não suportado é recusado",
             {"enabled": True, "mode": "url", "url": "ftp://1.2.3.4:21"})
expect_error("URL vazia é recusada", {"enabled": True, "mode": "url", "url": ""})


section("Máscara de senha")

check("mask esconde a senha",
      gowa_proxy.mask_url("socks5://u:segredo@1.2.3.4:1080") == "socks5://u:***@1.2.3.4:1080",
      gowa_proxy.mask_url("socks5://u:segredo@1.2.3.4:1080"))
check("mask não altera URL sem senha",
      gowa_proxy.mask_url("socks5://1.2.3.4:1080") == "socks5://1.2.3.4:1080")
check("mask de string vazia", gowa_proxy.mask_url("") == "")

stored = {"enabled": True, "mode": "fields", "scheme": "socks5", "host": "1.2.3.4",
          "port": 1080, "username": "u", "password": "segredo", "url": ""}
cfg = gowa_proxy.normalize({"enabled": True, "password": gowa_proxy.PASSWORD_MASK}, stored)
check("senha mascarada de volta preserva a salva", cfg["password"] == "segredo")
cfg = gowa_proxy.normalize({"enabled": True, "password": "nova"}, stored)
check("senha nova substitui a salva", cfg["password"] == "nova")
cfg = gowa_proxy.normalize({"enabled": True, "password": ""}, stored)
check("senha vazia limpa a salva", cfg["password"] == "")

stored_url = {"enabled": True, "mode": "url", "scheme": "socks5",
              "host": "", "port": 0, "username": "", "password": "",
              "url": "socks5://u:segredo@1.2.3.4:1080"}
cfg = gowa_proxy.normalize(
    {"enabled": True, "mode": "url", "url": "socks5://u:***@1.2.3.4:1080"}, stored_url)
check("URL devolvida mascarada recupera a senha salva",
      gowa_proxy.build_url(cfg) == "socks5://u:segredo@1.2.3.4:1080", gowa_proxy.build_url(cfg))


# ── Proxies falsos para o teste de conectividade ───────────────────────

section("Teste de conectividade")


class FakeProxy:
    """Single-shot proxy server. ``handler(conn)`` speaks the protocol."""

    def __init__(self, handler):
        self._handler = handler
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(4)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            try:
                conn.settimeout(5)
                self._handler(conn)
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass


def _recv(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("fim inesperado")
        buf += chunk
    return buf


def socks5_handler(*, require_auth=False, user="u", password="p",
                   connect_reply=0x00, no_acceptable=False):
    def handler(conn):
        version, count = _recv(conn, 2)
        methods = _recv(conn, count)
        if no_acceptable:
            conn.sendall(b"\x05\xff")
            return
        if require_auth:
            if 0x02 not in methods:
                conn.sendall(b"\x05\xff")
                return
            conn.sendall(b"\x05\x02")
            _recv(conn, 1)  # auth version
            ulen = _recv(conn, 1)[0]
            got_user = _recv(conn, ulen).decode()
            plen = _recv(conn, 1)[0]
            got_pass = _recv(conn, plen).decode()
            ok = got_user == user and got_pass == password
            conn.sendall(b"\x01" + (b"\x00" if ok else b"\x01"))
            if not ok:
                return
        else:
            conn.sendall(b"\x05\x00")
        head = _recv(conn, 4)
        atyp = head[3]
        if atyp == 0x03:
            length = _recv(conn, 1)[0]
            _recv(conn, length + 2)
        elif atyp == 0x01:
            _recv(conn, 4 + 2)
        conn.sendall(b"\x05" + bytes([connect_reply]) + b"\x00\x01" + b"\x00\x00\x00\x00" + b"\x00\x00")
    return handler


def http_handler(*, require_auth=False, status=b"200 Connection established"):
    def handler(conn):
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                return
            buf += chunk
        if require_auth and b"Proxy-Authorization:" not in buf:
            conn.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")
            return
        conn.sendall(b"HTTP/1.1 " + status + b"\r\n\r\n")
    return handler


srv = FakeProxy(socks5_handler())
res = gowa_proxy.test_connection(f"socks5://127.0.0.1:{srv.port}", "web.whatsapp.com", 443, timeout=5)
check("SOCKS5 sem auth conecta", res["ok"], res["message"])
srv.close()

srv = FakeProxy(socks5_handler(require_auth=True))
res = gowa_proxy.test_connection(f"socks5://u:p@127.0.0.1:{srv.port}", timeout=5)
check("SOCKS5 com usuário e senha corretos conecta", res["ok"], res["message"])
res = gowa_proxy.test_connection(f"socks5://u:errada@127.0.0.1:{srv.port}", timeout=5)
check("SOCKS5 com senha errada falha", not res["ok"] and "recusad" in res["message"].lower(),
      res["message"])
res = gowa_proxy.test_connection(f"socks5://127.0.0.1:{srv.port}", timeout=5)
check("SOCKS5 que exige auth sem credenciais falha", not res["ok"], res["message"])
srv.close()

srv = FakeProxy(socks5_handler(connect_reply=0x02))
res = gowa_proxy.test_connection(f"socks5://127.0.0.1:{srv.port}", timeout=5)
check("SOCKS5 bloqueando o destino falha", not res["ok"] and "não permitida" in res["message"],
      res["message"])
srv.close()

srv = FakeProxy(http_handler())
res = gowa_proxy.test_connection(f"http://127.0.0.1:{srv.port}", timeout=5)
check("HTTP CONNECT conecta", res["ok"], res["message"])
srv.close()

srv = FakeProxy(http_handler(require_auth=True))
res = gowa_proxy.test_connection(f"http://127.0.0.1:{srv.port}", timeout=5)
check("HTTP sem credenciais devolve 407", not res["ok"] and "407" in res["message"], res["message"])
res = gowa_proxy.test_connection(f"http://u:p@127.0.0.1:{srv.port}", timeout=5)
check("HTTP com credenciais conecta", res["ok"], res["message"])
srv.close()

srv = FakeProxy(http_handler(status=b"502 Bad Gateway"))
res = gowa_proxy.test_connection(f"http://127.0.0.1:{srv.port}", timeout=5)
check("HTTP com erro do proxy falha", not res["ok"] and "502" in res["message"], res["message"])
srv.close()

# Proxy HTTP configurado como SOCKS5: erro tem que ser compreensível.
srv = FakeProxy(http_handler())
res = gowa_proxy.test_connection(f"socks5://127.0.0.1:{srv.port}", timeout=5)
check("tipo errado é reportado com clareza", not res["ok"], res["message"])
srv.close()

# Porta fechada.
closed = socket.socket()
closed.bind(("127.0.0.1", 0))
closed_port = closed.getsockname()[1]
closed.close()
res = gowa_proxy.test_connection(f"socks5://127.0.0.1:{closed_port}", timeout=3)
check("porta fechada falha limpo", not res["ok"], res["message"])

res = gowa_proxy.test_connection("socks5://")
check("URL inválida no teste vira mensagem, não exceção", not res["ok"], res["message"])


# ── Resultado ──────────────────────────────────────────────────────────

print("\n" + "=" * 60)
print(f"  {passed} passaram, {failed} falharam")
print("=" * 60)
sys.exit(1 if failed else 0)
