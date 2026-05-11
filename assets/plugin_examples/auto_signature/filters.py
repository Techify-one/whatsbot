"""Plugin de exemplo: adiciona assinatura ao final de cada parte da resposta.

Filter interceptive — recebe o texto, devolve modificado. Vale para a IA, o
operador manual e o fluxo @ia privado (cada um aplica ``filter.reply.part``).

Para customizar a frase: ajustar ``SIGNATURE`` ou converter este plugin para
ler de settings declarativas (settings.py com BaseModel).
"""

from __future__ import annotations

SIGNATURE = "\n\n— enviado pelo bot"


def add_signature(ctx, value: str) -> str:
    # Filter recebe o texto exato que vai sair. Plugin só adiciona se ainda
    # não estiver lá (idempotente — útil em flows com retry).
    if not isinstance(value, str):
        return value
    if value.endswith(SIGNATURE):
        return value
    return value + SIGNATURE


FILTERS = {
    "filter.reply.part": add_signature,
}
