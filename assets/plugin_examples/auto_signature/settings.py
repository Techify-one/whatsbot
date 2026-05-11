"""Settings declarativas — formulário renderizado automaticamente em /plugins."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Settings(BaseModel):
    """Configurações persistidas em ``config`` com prefixo ``plugin.auto_signature.``."""

    signature: str = Field(
        default="*Mensagem enviada por IA*",
        description=(
            "Texto que será adicionado ao final de cada mensagem da IA. "
            "Para deixar em negrito no WhatsApp, envolva com asteriscos "
            "(ex: `*Mensagem enviada por IA*`). Deixe em branco para "
            "desativar a assinatura sem precisar desligar o plugin."
        ),
    )
    apply_to_operator: bool = Field(
        default=False,
        description=(
            "Se ativado, a assinatura também é adicionada em mensagens "
            "enviadas manualmente pelo painel (operador). Por padrão, "
            "só mensagens da IA recebem a assinatura."
        ),
    )
