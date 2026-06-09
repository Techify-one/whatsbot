"""Settings declarativas — formulário renderizado automaticamente em /plugins.

Persistidas na tabela ``config`` com prefixo ``plugin.horario_funcionamento.``.
Cada dia da semana aceita uma faixa ``HH:MM-HH:MM`` (24h). Vazio = fechado o dia
todo. Faixas que cruzam a meia-noite são suportadas (ex.: ``18:00-02:00``).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Settings(BaseModel):
    enabled: bool = Field(
        default=True,
        description=(
            "Liga/desliga a regra de horário sem precisar desativar o plugin. "
            "Quando desligado, a IA responde normalmente a qualquer hora."
        ),
    )
    away_message: str = Field(
        default=(
            "Olá! No momento estamos fora do horário de atendimento. "
            "Sua mensagem foi recebida e retornaremos assim que possível. 🕒"
        ),
        description=(
            "Mensagem enviada automaticamente quando o cliente escreve fora do "
            "horário de funcionamento."
        ),
    )
    cooldown_min: int = Field(
        default=60,
        ge=0,
        description=(
            "Tempo mínimo (em minutos) entre duas mensagens de ausência para o "
            "MESMO contato — evita repetir o aviso a cada mensagem. Use 0 para "
            "responder com a ausência sempre que chegar mensagem fora do horário."
        ),
    )
    tz_offset_hours: float = Field(
        default=-3.0,
        ge=-12.0,
        le=14.0,
        description=(
            "Fuso horário do estabelecimento como offset em relação ao UTC. "
            "Brasil (Brasília) = -3. Usado para saber a hora local na hora de "
            "comparar com o horário de funcionamento."
        ),
    )
    apply_to_groups: bool = Field(
        default=False,
        description=(
            "Se ativado, a regra de horário também vale em grupos. Por padrão "
            "fica desligado (a ausência só responde em conversas individuais)."
        ),
    )
    monday: str = Field(default="08:00-18:00", description="Segunda — faixa HH:MM-HH:MM (vazio = fechado).")
    tuesday: str = Field(default="08:00-18:00", description="Terça — faixa HH:MM-HH:MM (vazio = fechado).")
    wednesday: str = Field(default="08:00-18:00", description="Quarta — faixa HH:MM-HH:MM (vazio = fechado).")
    thursday: str = Field(default="08:00-18:00", description="Quinta — faixa HH:MM-HH:MM (vazio = fechado).")
    friday: str = Field(default="08:00-18:00", description="Sexta — faixa HH:MM-HH:MM (vazio = fechado).")
    saturday: str = Field(default="08:00-12:00", description="Sábado — faixa HH:MM-HH:MM (vazio = fechado).")
    sunday: str = Field(default="", description="Domingo — faixa HH:MM-HH:MM (vazio = fechado).")
