"""Tool: save_contact_info — saves personal data mentioned by the contact."""

import logging

logger = logging.getLogger(__name__)


SAVE_CONTACT_INFO_TOOL = {
    "type": "function",
    "display_label": "Salvar Dados do Contato",
    "function": {
        "name": "save_contact_info",
        "description": (
            "Salva informações pessoais do contato (nome, email, profissão, empresa, "
            "endereço ou observação relevante). "
            "Chame APENAS quando a ÚLTIMA mensagem do usuário contiver dados pessoais "
            "NOVOS que ainda NÃO estão listados na seção 'Informações já conhecidas' "
            "do system prompt. NÃO chame se os dados já foram salvos anteriormente."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Nome completo do contato",
                },
                "email": {
                    "type": "string",
                    "description": "Email do contato",
                },
                "profession": {
                    "type": "string",
                    "description": "Profissão ou cargo do contato",
                },
                "company": {
                    "type": "string",
                    "description": "Empresa onde trabalha",
                },
                "address": {
                    "type": "string",
                    "description": "Endereço completo do contato (rua, número, bairro, cidade)",
                },
                "observation": {
                    "type": "string",
                    "description": "Qualquer outra informação relevante sobre o contato",
                },
            },
            "required": [],
        },
    },
}


def execute(ctx, args: dict) -> str | None:
    """Persist contact info from an LLM tool call.

    Returns ``None`` so the handler uses its default follow-up message.
    """
    try:
        ctx.contact.update_info(**args)
    except Exception as e:
        logger.warning("save_contact_info failed for %s: %s", ctx.contact.phone, e)
    return None
