"""Contrato dos seams ``filter.provisioning.number`` e ``filter.provisioning.message``.

O wizard de 1ª execução manda uma frase de provisionamento para um número. Os dois
lados desse par são resolvidos CAMPO A CAMPO pelo core — ``GET /service_number``
(fonte da verdade, devolve ``{phone, message}``) → env → literal do código — e cada
um é então oferecido ao seu filtro, que tem a ÚLTIMA palavra.

Os testes exercitam o CORE direto (sem plugin): registram filtros em processo e
chamam ``fetch_provision_target`` com a ida ao ``/service_number`` mockada. O que
está travado aqui:

* ``None``/``""`` em qualquer um dos dois ABORTA — o core recusa o envio em vez de
  mandar a frase para um número que ninguém escolheu, ou uma mensagem vazia para ele;
* os campos são INDEPENDENTES — endpoint que só devolve ``phone`` não derruba a
  frase, e vice-versa;
* existe fallback embutido nos DOIS (a rede para o endpoint fora do ar);
* o core não opina sobre o formato do que volta.

Run standalone: ``python tests/test_provisioning_target.py``
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from plugins import events as bus  # noqa: E402
from server.routes import setup as svc  # noqa: E402

FILTER = "filter.provisioning.number"
MSG_FILTER = "filter.provisioning.message"
REMOTE = "5511111111111"
REMOTE_MSG = "Frase publicada no endpoint"
NUM_FALLBACK = svc.TECHIFY_PROVISION_NUMBER
MSG_FALLBACK = svc.TECHIFY_PROVISION_MESSAGE

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  OK {name}")
    else:
        failed += 1
        msg = f"  FAIL {name}"
        if detail:
            msg += f" -- {detail}"
        print(msg)


def section(title: str) -> None:
    print(f"\n--- {title} ---")


# ─── Test doubles for /service_number ────────────────────────────────


class _FakeServiceNumber:
    """Patches ``httpx.AsyncClient`` so ``GET /service_number`` returns ``body``.

    ``body=None`` simulates the endpoint being down (the core falls back).
    """

    def __init__(self, body: dict | None):
        self._body = body
        self._original = None

    def __enter__(self):
        body = self._body

        class _Resp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return body

        class _Client:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *a):
                return False

            async def get(self_inner, *a, **k):
                if body is None:
                    raise RuntimeError("sem rede")
                return _Resp()

        self._original = svc.httpx.AsyncClient
        svc.httpx.AsyncClient = lambda *a, **k: _Client()
        return self

    def __exit__(self, *exc):
        svc.httpx.AsyncClient = self._original
        return False


def _register(fn, name: str = FILTER, priority: int = 100) -> None:
    bus.register_filter("test_provisioning", name, fn, priority)


def _clear_filters() -> None:
    bus._filters.pop(FILTER, None)
    bus._filters.pop(MSG_FILTER, None)


def _target(body: dict | None):
    """Resolve the target with ``/service_number`` answering ``body``."""
    with _FakeServiceNumber(body):
        return asyncio.run(svc.fetch_provision_target())


# ─── 1. Sem filtro: a precedência do core ────────────────────────────
section("Precedência: endpoint > env/literal")

t = _target({"phone": REMOTE, "message": REMOTE_MSG})
check("endpoint dita o par inteiro", t.number == REMOTE and t.message == REMOTE_MSG,
      detail=str(t))

t = _target(None)
check("endpoint fora do ar cai no fallback dos dois",
      t.number == NUM_FALLBACK and t.message == MSG_FALLBACK, detail=str(t))

t = _target({"phone": REMOTE})
check("campos são independentes: só phone remoto, frase no fallback",
      t.number == REMOTE and t.message == MSG_FALLBACK, detail=str(t))

t = _target({"message": REMOTE_MSG})
check("campos são independentes: só message remota, número no fallback",
      t.number == NUM_FALLBACK and t.message == REMOTE_MSG, detail=str(t))

t = _target({"phone": "  ", "message": ""})
check("campo remoto vazio conta como ausente",
      t.number == NUM_FALLBACK and t.message == MSG_FALLBACK, detail=str(t))

t = _target(["not", "a", "dict"])  # endpoint respondeu, mas com corpo estranho
check("corpo não-objeto não derruba o provisionamento",
      t.number == NUM_FALLBACK and t.message == MSG_FALLBACK, detail=str(t))

# O fallback embutido é DELIBERADO: sem ele, uma queda do /service_number pararia
# o provisionamento de todo cliente novo — quem acabou de conectar o QR não tem
# env, não tem plugin e não teria como pedir a própria chave.
check("o core tem rede embutida para o número", bool(NUM_FALLBACK))
check("o core tem rede embutida para a mensagem", bool(MSG_FALLBACK))

with _FakeServiceNumber({"phone": REMOTE, "message": REMOTE_MSG}):
    check("fetch_provision_number devolve só o destino",
          asyncio.run(svc.fetch_provision_number()) == REMOTE)


# ─── 2. Os seams têm a última palavra ────────────────────────────────
section("Override por plugin")

_register(lambda ctx, number: "5599888887777")
t = _target({"phone": REMOTE, "message": REMOTE_MSG})
check("filtro troca o destino", t.number == "5599888887777", detail=str(t))
_clear_filters()

_register(lambda ctx, number: "5599888887777")
t = _target(None)
check("override vale também com o endpoint fora do ar",
      t.number == "5599888887777", detail=str(t))
_clear_filters()

_register(lambda ctx, msg: "Outra frase", name=MSG_FILTER)
t = _target({"phone": REMOTE, "message": REMOTE_MSG})
check("filtro troca a frase sem mexer no número",
      t.number == REMOTE and t.message == "Outra frase", detail=str(t))
_clear_filters()

# Formato é de quem responde, não do core: o valor sempre foi usado cru.
_register(lambda ctx, number: "+55 (99) 88888-7777")
t = _target({"phone": REMOTE, "message": REMOTE_MSG})
check("o core não normaliza o que o filtro devolve",
      t.number == "+55 (99) 88888-7777", detail=str(t))
_clear_filters()

_register(lambda ctx, number: "5500000000000", priority=10)
_register(lambda ctx, number: number + "9", priority=20)
t = _target({"phone": REMOTE, "message": REMOTE_MSG})
check("cadeia respeita prioridade", t.number == "55000000000009", detail=str(t))
_clear_filters()


# ─── 3. ctx.extras: de onde veio, e o outro lado do par ──────────────
section("ctx.extras")

seen: dict = {}


def _spy_number(ctx, number):
    seen["number"] = number
    seen["number_extras"] = dict(getattr(ctx, "extras", None) or {})
    return number


def _spy_message(ctx, message):
    seen["message"] = message
    seen["message_extras"] = dict(getattr(ctx, "extras", None) or {})
    return message


_register(_spy_number)
_register(_spy_message, name=MSG_FILTER)
_target({"phone": REMOTE, "message": REMOTE_MSG})
check("extras do número marcam a origem",
      seen["number_extras"].get("source") == "service_number", detail=str(seen))
check("o filtro de número JÁ vê a frase final",
      seen["number_extras"].get("message") == REMOTE_MSG, detail=str(seen))
check("o filtro de mensagem recebe o número já decidido",
      seen["message_extras"].get("number") == REMOTE, detail=str(seen))
_clear_filters()

seen.clear()
_register(_spy_number)
_target(None)
check("extras marcam o fallback",
      seen["number_extras"].get("source") == "fallback"
      and seen["number"] == NUM_FALLBACK, detail=str(seen))
_clear_filters()


# ─── 4. O aborto: sem destino/frase, ninguém envia ───────────────────
section("Aborto")

for devolvido in (None, "", "   "):
    _register(lambda ctx, number, _d=devolvido: _d)
    t = _target({"phone": REMOTE, "message": REMOTE_MSG})
    check(f"número {devolvido!r} do filtro significa sem destino",
          t.number == "" and t.message == "", detail=str(t))
    _clear_filters()

for devolvido in (None, "", "   "):
    _register(lambda ctx, msg, _d=devolvido: _d, name=MSG_FILTER)
    t = _target({"phone": REMOTE, "message": REMOTE_MSG})
    check(f"mensagem {devolvido!r} do filtro significa sem frase",
          t.number == REMOTE and t.message == "", detail=str(t))
    _clear_filters()

# A ordem importa: sem isto, "em branco" viraria "manda para o número do core".
_register(lambda ctx, number: None)
t = _target({"phone": REMOTE, "message": REMOTE_MSG})
check("aborto vence o número que o core resolveu",
      bool(REMOTE) and t.number == "", detail=str(t))
_clear_filters()

# Com o destino abortado, nem faz sentido perguntar a frase.
called: list = []
_register(lambda ctx, number: None)
_register(lambda ctx, msg: called.append(1) or msg, name=MSG_FILTER)
_target({"phone": REMOTE, "message": REMOTE_MSG})
check("sem destino, o seam da mensagem nem roda", not called, detail=str(called))
_clear_filters()


# Contrato do bus, não deste seam: apply_filter isola a exceção e o valor segue
# intacto — quem quer fail-closed captura a própria exceção e devolve None.
def _boom(ctx, number):
    raise RuntimeError("plugin quebrado")


_register(_boom)
t = _target({"phone": REMOTE, "message": REMOTE_MSG})
check("filtro que levanta deixa o valor do core passar", t.number == REMOTE,
      detail=str(t))
_clear_filters()


# ─── Wrap up ─────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"  RESULTS: {passed} passed, {failed} failed")
print("=" * 60)

if failed:
    sys.exit(1)
