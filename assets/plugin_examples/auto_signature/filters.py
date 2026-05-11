"""Plugin de exemplo: adiciona assinatura ao final de cada parte da resposta.

Filter interceptive — recebe o texto, devolve modificado. ``filter.reply.part``
é disparado pela IA, pelo operador manual e pelo fluxo da IA acionada por
nota privada; aqui filtramos pelo ``ctx.extras["source"]`` pra escolher o
que assinar.

Texto da assinatura e o flag de operador vêm de ``Settings`` (settings.py) e
podem ser editados na tela /plugins → Auto Signature.
"""

from __future__ import annotations

from db.repositories import config_repo

_AI_SOURCES = {None, "ai", "private_ai", "retry"}


def _load_settings() -> tuple[str, bool]:
    signature = config_repo.get("plugin.auto_signature.signature", "*Mensagem enviada por IA*")
    apply_to_operator = config_repo.get("plugin.auto_signature.apply_to_operator", False)
    return str(signature or ""), bool(apply_to_operator)


def add_signature(ctx, value: str) -> str:
    if not isinstance(value, str):
        return value
    signature, apply_to_operator = _load_settings()
    if not signature.strip():
        return value
    source = (getattr(ctx, "extras", None) or {}).get("source")
    if source not in _AI_SOURCES and not (source == "operator" and apply_to_operator):
        return value
    suffix = "\n\n" + signature
    if value.endswith(suffix):
        return value
    return value + suffix


FILTERS = {
    "filter.reply.part": add_signature,
}
