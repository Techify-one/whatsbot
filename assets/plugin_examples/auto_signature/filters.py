"""Plugin de exemplo: adiciona assinatura ao final de cada parte da resposta.

Filter interceptive — recebe o texto, devolve modificado. ``filter.reply.part``
é disparado pela IA, pelo operador manual e pelo fluxo da IA acionada por
nota privada; aqui filtramos pelo ``ctx.extras["source"]`` pra escolher o
que assinar e qual assinatura usar (IA vs operador).

Os textos das assinaturas e o flag de operador vêm de ``Settings`` (settings.py)
e podem ser editados na tela /plugins → Auto Signature.
"""

from __future__ import annotations

from db.repositories import config_repo

_AI_SOURCES = {None, "ai", "private_ai", "retry"}


def _load_settings() -> tuple[str, bool, str]:
    signature = config_repo.get("plugin.auto_signature.signature", "*Mensagem enviada por IA*")
    apply_to_operator = config_repo.get("plugin.auto_signature.apply_to_operator", False)
    operator_signature = config_repo.get("plugin.auto_signature.operator_signature", "")
    return str(signature or ""), bool(apply_to_operator), str(operator_signature or "")


def _signature_for(source, ai_sig: str, apply_to_operator: bool, op_sig: str) -> str | None:
    """Pick which signature applies to this message, or None to leave it untouched."""
    if source in _AI_SOURCES:
        return ai_sig
    if source == "operator" and apply_to_operator:
        # Operator gets its own signature; falls back to the AI one when blank.
        return op_sig.strip() or ai_sig
    return None


def add_signature(ctx, value: str) -> str:
    if not isinstance(value, str):
        return value
    ai_sig, apply_to_operator, op_sig = _load_settings()
    source = (getattr(ctx, "extras", None) or {}).get("source")
    signature = _signature_for(source, ai_sig, apply_to_operator, op_sig)
    if not signature or not signature.strip():
        return value
    suffix = "\n\n" + signature
    if value.endswith(suffix):
        return value
    return value + suffix


FILTERS = {
    "filter.reply.part": add_signature,
}
