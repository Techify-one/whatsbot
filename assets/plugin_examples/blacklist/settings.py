"""Settings declarativas — formulário renderizado automaticamente em /plugins."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Settings(BaseModel):
    """Configurações persistidas em ``config`` com prefixo ``plugin.blacklist.``."""

    phones: str = Field(
        default="",
        description=(
            "Telefones bloqueados, separados por vírgula. Ex: "
            "`5511999990000,5521988880000`. O filter compara o número exato "
            "(sem `@s.whatsapp.net`)."
        ),
    )
    block_groups: bool = Field(
        default=False,
        description="Se true, também bloqueia mensagens vindas de grupos onde "
                    "qualquer membro estiver na lista (compara `individual_phone`).",
    )
