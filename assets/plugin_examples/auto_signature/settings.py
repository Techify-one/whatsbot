"""Settings declarativas — formulário renderizado automaticamente em /plugins."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Settings(BaseModel):
    """Configurações persistidas em ``config`` com prefixo ``plugin.auto_signature.``."""

    signature: str = Field(
        default="*Mensagem enviada por IA*",
        description=(
            "Assinatura adicionada ao final das mensagens enviadas pela IA. "
            "Para deixar em negrito no WhatsApp, envolva com asteriscos "
            "(ex: `*Mensagem enviada por IA*`). Deixe em branco para "
            "desativar a assinatura da IA sem precisar desligar o plugin."
        ),
    )
    apply_to_operator: bool = Field(
        default=False,
        description=(
            "Se ativado, mensagens enviadas manualmente pelo painel (operador) "
            "também recebem assinatura — usando o texto do campo abaixo."
        ),
    )
    operator_signature: str = Field(
        default="",
        description=(
            "Assinatura para mensagens enviadas manualmente pelo painel (operador). "
            "Usada apenas quando 'Apply To Operator' está ativado. "
            "Se ficar em branco, usa a mesma assinatura da IA."
        ),
    )
