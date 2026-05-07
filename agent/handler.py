import base64
import dataclasses
import json
import logging
import mimetypes
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from openai import OpenAI

from agent.memory import ContactMemory, TagRegistry, _build_image_content
from agent.tools import ALL_TOOLS
from db.repositories import message_repo, contact_repo
from agent.execution import track_step

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class ProcessResult:
    """Result of process_message with optional tool call metadata."""
    reply: str
    tool_calls: list[dict] = dataclasses.field(default_factory=list)
    contact_info: dict | None = None


class AgentHandler:
    """Processes incoming WhatsApp messages using OpenRouter LLM."""

    def __init__(
        self,
        api_key: str,
        system_prompt: str,
        max_context_messages: int = 10,
        inactivity_timeout_min: int = 30,
        model: str = "openai/gpt-4o-mini",
        audio_model: str = "google/gemini-2.0-flash-001",
        image_model: str = "google/gemini-2.0-flash-001",
        pricing_fn=None,
        default_ai_enabled: bool = True,
    ):
        self.api_key = api_key
        self.system_prompt = system_prompt
        self.max_context_messages = max_context_messages
        self.inactivity_timeout = inactivity_timeout_min * 60
        self.model = model
        self.audio_model = audio_model
        self.image_model = image_model
        self.default_ai_enabled = default_ai_enabled
        self._contacts: dict[str, ContactMemory] = {}
        self._client: OpenAI | None = None
        self.pricing_fn = pricing_fn
        self.split_messages: bool = True
        self.tag_registry = TagRegistry()

    def _record_usage(self, phone: str, call_type: str, model: str, response) -> None:
        """Extract usage from an OpenAI-compatible response and record it."""
        try:
            usage = getattr(response, "usage", None)
            if not usage:
                return
            prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(usage, "completion_tokens", 0) or 0
            total_tokens = getattr(usage, "total_tokens", 0) or 0
            cost_usd = 0.0
            if self.pricing_fn:
                prompt_price, completion_price = self.pricing_fn(model)
                cost_usd = (prompt_tokens * prompt_price) + (completion_tokens * completion_price)
            contact = self._get_contact(phone)
            contact.add_usage(call_type, model, prompt_tokens, completion_tokens, total_tokens, cost_usd)
            logger.debug("Usage recorded for %s: %s %s tokens=%d cost=%.6f",
                         phone, call_type, model, total_tokens, cost_usd)
        except Exception as e:
            logger.warning("Failed to record usage: %s", e)

    def _get_client(self) -> OpenAI:
        if self._client is None or self._client.api_key != self.api_key:
            self._client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=self.api_key,
            )
        return self._client

    def update_config(
        self,
        api_key: str | None = None,
        system_prompt: str | None = None,
        max_context_messages: int | None = None,
        inactivity_timeout_min: int | None = None,
        model: str | None = None,
        audio_model: str | None = None,
        image_model: str | None = None,
        split_messages: bool | None = None,
        default_ai_enabled: bool | None = None,
    ):
        if api_key is not None:
            self.api_key = api_key
            self._client = None
        if system_prompt is not None:
            self.system_prompt = system_prompt
        if max_context_messages is not None:
            self.max_context_messages = max_context_messages
        if inactivity_timeout_min is not None:
            self.inactivity_timeout = inactivity_timeout_min * 60
        if model is not None:
            self.model = model
        if audio_model is not None:
            self.audio_model = audio_model
        if image_model is not None:
            self.image_model = image_model
        if split_messages is not None:
            self.split_messages = split_messages
        if default_ai_enabled is not None:
            self.default_ai_enabled = default_ai_enabled

    def transcribe_audio(self, audio_path: str, phone: str = "") -> str:
        """Transcribe an audio file using the configured audio model."""
        if not self.api_key:
            return ""
        try:
            p = Path(audio_path)
            if not p.is_absolute():
                p = Path(__file__).resolve().parent.parent / p
            if not p.exists():
                logger.warning("Audio file not found for transcription: %s", audio_path)
                return ""
            data = p.read_bytes()
            b64 = base64.b64encode(data).decode()
            # Determine format from extension
            ext = p.suffix.lower().lstrip(".")
            if ext in ("oga", "ogg", "opus"):
                fmt = "ogg"
            elif ext == "mp3":
                fmt = "mp3"
            elif ext == "wav":
                fmt = "wav"
            else:
                fmt = "ogg"

            client = self._get_client()
            response = client.chat.completions.create(
                model=self.audio_model,
                timeout=60,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": b64, "format": fmt},
                        },
                        {
                            "type": "text",
                            "text": "Transcreva este áudio fielmente em português. Retorne apenas a transcrição, sem comentários adicionais.",
                        },
                    ],
                }],
                max_tokens=2048,
            )
            self._record_usage(phone, "audio", self.audio_model, response)
            result = response.choices[0].message.content.strip()
            track_step("media_processed", {
                "type": "audio",
                "model": self.audio_model,
                "transcription_length": len(result),
            })
            logger.info("Audio transcribed (%d chars): %s", len(result), result[:80])
            return result
        except Exception as e:
            logger.error("Audio transcription failed: %s", e)
            track_step("error", {"error": str(e), "phase": "audio_transcription"}, status="error")
            return ""

    def describe_image(self, image_path: str, phone: str = "") -> str:
        """Describe an image using the configured image model."""
        if not self.api_key:
            return ""
        try:
            p = Path(image_path)
            if not p.is_absolute():
                p = Path(__file__).resolve().parent.parent / p
            if not p.exists():
                logger.warning("Image file not found for description: %s", image_path)
                return ""
            data = p.read_bytes()
            mime = mimetypes.guess_type(str(p))[0] or "image/png"
            b64 = base64.b64encode(data).decode()

            client = self._get_client()
            response = client.chat.completions.create(
                model=self.image_model,
                timeout=60,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        },
                        {
                            "type": "text",
                            "text": "Descreva detalhadamente o conteúdo desta imagem em português.",
                        },
                    ],
                }],
                max_tokens=1024,
            )
            self._record_usage(phone, "image", self.image_model, response)
            result = response.choices[0].message.content.strip()
            track_step("media_processed", {
                "type": "image",
                "model": self.image_model,
                "description_length": len(result),
            })
            logger.info("Image described (%d chars): %s", len(result), result[:80])
            return result
        except Exception as e:
            logger.error("Image description failed: %s", e)
            track_step("error", {"error": str(e), "phase": "image_description"}, status="error")
            return ""

    def _get_contact(self, phone: str) -> ContactMemory:
        if phone not in self._contacts:
            self._contacts[phone] = ContactMemory(phone, default_ai_enabled=self.default_ai_enabled)
        return self._contacts[phone]

    def _build_system_prompt(self, contact: ContactMemory) -> str:
        """Build system prompt with contact info and current date/time injected."""
        prompt = self.system_prompt
        if contact.is_group:
            gname = f" chamado '{contact.group_name}'" if contact.group_name else ""
            prompt += (
                f"\n\n--- Contexto de grupo ---\n"
                f"Esta é uma conversa de grupo do WhatsApp{gname}.\n"
                "As mensagens de usuários estão no formato '[Nome]: mensagem'.\n"
                "Quando responder, leve em conta quem fez a pergunta e responda "
                "de forma natural ao grupo.\n"
                "--- Fim do contexto de grupo ---"
            )
        info_summary = contact.get_info_summary()
        if info_summary:
            prompt += (
                f"\n\n--- Informações já conhecidas sobre este contato ({contact.phone}) ---\n"
                f"{info_summary}\n"
                "IMPORTANTE: Use estas informações na conversa. "
                "NÃO pergunte dados que já estão listados acima (ex: nome, email, etc). "
                "NÃO chame save_contact_info para dados que já aparecem nesta seção — "
                "eles já estão salvos. Só use save_contact_info quando o usuário revelar "
                "informação NOVA na mensagem mais recente.\n"
                "--- Fim das informações ---"
            )
        tags_summary = contact.get_tags_summary()
        if tags_summary:
            prompt += (
                f"\n\n--- Tags do contato ---\n"
                f"{tags_summary}\n"
                "Estas tags foram aplicadas por operadores humanos para classificar este contato. "
                "Use-as como sinal de contexto sobre o histórico/perfil do contato, mas não as "
                "mencione diretamente na conversa nem peça confirmação sobre elas.\n"
                "--- Fim das tags ---"
            )
        prompt += (
            "\n\nMensagens marcadas com '[Mensagem do operador humano]' no histórico "
            "foram enviadas por um atendente real, não por você. Considere o contexto "
            "mas não imite o estilo do operador."
        )
        _BRT = timezone(timedelta(hours=-3))
        now = datetime.now(_BRT)
        dias = ["segunda-feira", "terça-feira", "quarta-feira",
                "quinta-feira", "sexta-feira", "sábado", "domingo"]
        prompt += (
            f"\n\n--- Data e hora atual ---\n"
            f"Data: {now.strftime('%d/%m/%Y')} ({dias[now.weekday()]})\n"
            f"Hora: {now.strftime('%H:%M')}\n"
            "--- Fim ---"
        )
        if self.split_messages:
            prompt += (
                "\n\n--- Formato de resposta ---\n"
                "IMPORTANTE: Você DEVE responder SEMPRE em formato JSON array de strings.\n"
                "Cada string é uma mensagem separada que será enviada no WhatsApp.\n"
                "Regras:\n"
                "- Seja DIRETO e CONCISO. Não enrole. Responda apenas o necessário.\n"
                "- Respostas curtas e simples: USE APENAS 1 MENSAGEM (array com 1 elemento)\n"
                "- Só divida em múltiplas mensagens quando a resposta total for LONGA (mais de 4-5 linhas)\n"
                "- Quando dividir: máximo 2 a 3 partes, cada uma com 1-3 linhas\n"
                "- NÃO separe saudação do conteúdo se a resposta for curta\n"
                "- NÃO use markdown nem formatação especial\n"
                "Exemplos:\n"
                'Resposta curta: [\"Ok, só um minuto!\"]\n'
                'Resposta longa: [\"Então, sobre o plano mensal...\", '
                '\"O valor é R$99 e inclui X, Y e Z\", \"Quer que eu te mande o link?\"]\n'
                "Retorne APENAS o JSON array, sem texto antes ou depois.\n"
                "--- Fim do formato ---"
            )
        return prompt

    def process_message(self, sender: str, text: str, *,
                        save_user_message: bool = True,
                        save_response: bool = True,
                        image_path: str | None = None,
                        audio_path: str | None = None) -> ProcessResult:
        """Process an incoming message and return the AI response."""
        if not self.api_key:
            return ProcessResult(reply="[WhatsBot] API key não configurada.")

        contact = self._get_contact(sender)

        # Determine media metadata for storage
        media_type: str | None = None
        media_path: str | None = None
        if image_path:
            media_type = "image"
            media_path = image_path
        elif audio_path:
            media_type = "audio"
            media_path = audio_path

        if save_user_message:
            contact.add_message("user", text or "", media_type=media_type, media_path=media_path)

        context_messages = contact.get_context_messages(self.max_context_messages)

        messages = [
            {"role": "system", "content": self._build_system_prompt(contact)},
            *context_messages,
        ]

        try:
            client = self._get_client()
            track_step("llm_request", {
                "model": self.model,
                "context_messages": len(messages) - 1,
                "tools": [t["function"]["name"] for t in ALL_TOOLS],
            })
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=ALL_TOOLS,
                tool_choice="auto",
                max_tokens=1024,
            )

            self._record_usage(sender, "text", self.model, response)
            usage = response.usage
            track_step("llm_response", {
                "model": self.model,
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "has_tool_calls": bool(response.choices[0].message.tool_calls),
            })
            msg = response.choices[0].message

            # Handle tool calls generically
            executed_tools: list[dict] = []
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError as e:
                        logger.warning("Failed to parse tool args for %s: %s", sender, e)
                        args = {}

                    # Dispatch tool execution
                    if tool_name == "save_contact_info":
                        try:
                            contact.update_info(**args)
                        except Exception as e:
                            logger.warning("Failed to execute %s for %s: %s", tool_name, sender, e)
                    elif tool_name == "transfer_to_human":
                        try:
                            contact.set_ai_enabled(False)
                            self.tag_registry.create("transferido_atendente", "#ef4444")
                            contact.add_tag("transferido_atendente")
                            contact.save()
                            logger.info("Transfer to human for %s: %s", sender, args.get("reason", ""))
                        except Exception as e:
                            logger.warning("Failed to execute %s for %s: %s", tool_name, sender, e)

                    executed_tools.append({"tool": tool_name, "args": args})
                    track_step("tool_executed", {"tool": tool_name, "args": args})
                    logger.info("Tool call for %s: %s(%s)", sender, tool_name, args)

                # If model only called tools without text, do a follow-up call
                if not msg.content:
                    messages.append(msg.model_dump())
                    tool_results = {
                        "transfer_to_human": "Transferência realizada. Responda ao cliente de forma curta e natural, apenas confirmando que já vai ser atendido pela pessoa solicitada. NÃO mencione 'humano', 'atendente' nem 'transferência'.",
                    }
                    for tc in msg.tool_calls:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": tool_results.get(tc.function.name, "Informações salvas com sucesso."),
                        })
                    track_step("llm_request", {"model": self.model, "type": "followup"})
                    follow_up = client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        max_tokens=1024,
                    )
                    self._record_usage(sender, "text", self.model, follow_up)
                    fu_usage = follow_up.usage
                    track_step("llm_response", {
                        "model": self.model,
                        "type": "followup",
                        "prompt_tokens": fu_usage.prompt_tokens if fu_usage else 0,
                        "completion_tokens": fu_usage.completion_tokens if fu_usage else 0,
                    })
                    reply = follow_up.choices[0].message.content.strip()
                else:
                    reply = msg.content.strip()
            else:
                reply = msg.content.strip()

            if save_response:
                contact.add_message("assistant", reply)
            logger.info("Processed message from %s", sender)

            # Snapshot contact info if any tool modified it
            updated_info = None
            if any(tc.get("tool") == "save_contact_info" for tc in executed_tools):
                updated_info = dict(contact.info)
                # Deep copy observations list
                updated_info["observations"] = list(updated_info.get("observations", []))

            return ProcessResult(reply=reply, tool_calls=executed_tools, contact_info=updated_info)

        except Exception as e:
            logger.error("LLM error for %s: %s", sender, e)
            track_step("error", {"error": str(e), "phase": "llm_call"}, status="error")
            error_msg = str(e)
            if "401" in error_msg or "unauthorized" in error_msg.lower():
                return ProcessResult(reply="[WhatsBot] API key inválida. Verifique sua chave OpenRouter.")
            if "429" in error_msg or "rate" in error_msg.lower():
                return ProcessResult(reply="[WhatsBot] Limite de requisições atingido. Tente novamente em instantes.")
            return ProcessResult(reply="[WhatsBot] Erro ao processar mensagem. Tente novamente.")

    def test_api_key(self, api_key: str) -> tuple[bool, str]:
        """Test if an API key is valid."""
        try:
            client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=api_key,
            )
            client.chat.completions.create(
                model="openai/gpt-4o-mini",
                messages=[{"role": "user", "content": "test"}],
                max_tokens=5,
            )
            return True, "API key válida!"
        except Exception as e:
            return False, f"Erro: {e}"

    def save_assistant_message(self, phone: str, text: str, *,
                               msg_id: str | None = None,
                               status: str = "sent") -> dict:
        """Save an assistant (bot) message to contact memory after successful send."""
        contact = self._get_contact(phone)
        contact.add_message("assistant", text, msg_id=msg_id, status=status)
        return message_repo.get_last(contact.id) or {"role": "assistant", "content": text, "ts": time.time()}

    def save_operator_message(self, phone: str, text: str, *,
                              status: str | None = None,
                              msg_id: str | None = None) -> dict:
        """Save a manually sent message (from the operator) without LLM processing."""
        contact = self._get_contact(phone)
        contact.add_message("assistant", text, status=status, msg_id=msg_id)
        return message_repo.get_last(contact.id) or {"role": "assistant", "content": text, "ts": time.time()}

    def mark_message_sent(self, phone: str, content: str,
                          msg_id: str | None = None) -> dict | None:
        """Find the most recent failed message with matching content and mark as sent."""
        contact = self._get_contact(phone)
        message_repo.update_status(contact.id, content, "sent", msg_id=msg_id)
        return {"content": content}

    def update_last_user_message_content(self, phone: str, new_content: str) -> None:
        """Update the content of the last user message (e.g., with transcription)."""
        contact = self._get_contact(phone)
        msg = message_repo.get_last_user_message(contact.id)
        if msg and msg.get("_id"):
            message_repo.update_content(msg["_id"], new_content)

    def clear_conversation(self, sender: str):
        contact = self._get_contact(sender)
        message_repo.delete_all(contact.id)

    def clear_all_conversations(self):
        for contact in self._contacts.values():
            message_repo.delete_all(contact.id)
        self._contacts.clear()
