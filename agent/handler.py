import base64
import copy
import dataclasses
import json
import logging
import mimetypes
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from openai import OpenAI

from agent.memory import ContactMemory, TagRegistry, _build_image_content
from agent.tools import CORE_TOOLS
from db.repositories import message_repo, contact_repo, tool_override_repo
from agent.execution import track_step
from plugins.context import ToolContext, PromptContext

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
        model: str = "deepseek/deepseek-v4-pro",
        audio_model: str = "google/gemini-3-flash-preview",
        image_model: str = "google/gemini-3-flash-preview",
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

        # Tool registry — populated with core tools at construction; plugins
        # call ``register_plugin_tools`` after the loader runs.
        # ``_tool_originals`` keeps the canonical schema as defined in code
        # (already stripped of non-OpenAI fields like ``display_label``).
        # ``_tool_schemas`` is the *effective* list sent to the LLM, rebuilt
        # whenever overrides change.
        # ``_tool_default_labels`` holds the in-code ``display_label`` per tool
        # — UI default when the user hasn't customized it.
        self._tool_originals: dict[str, dict] = {}
        self._tool_default_labels: dict[str, str] = {}
        self._tool_schemas: list[dict] = []
        self._disabled_tools: set[str] = set()
        # name -> (executor_callable, plugin_id_or_None)
        self._tool_executors: dict[str, tuple[callable, str | None]] = {}
        for schema, executor in CORE_TOOLS:
            self._register_tool(schema, executor)

        # Prompt fragment registry — list of (fragment_fn, plugin_id_or_None).
        # Each fragment is ``Callable[[ContactMemory, PromptContext], str]``.
        self._prompt_fragments: list[tuple[callable, str | None]] = []

    def _register_tool(
        self,
        schema: dict,
        executor: callable,
        plugin_id: str | None = None,
    ) -> None:
        """Register a tool schema + executor. No-ops on name collision.

        Stores a clean deep-copy in ``_tool_originals`` (with WhatsBot-specific
        keys like ``display_label`` stripped, so it's safe to send to the LLM
        as-is) and eagerly inserts a default row into ``tool_overrides`` so the
        management UI sees every registered tool.
        """
        try:
            name = schema["function"]["name"]
        except (KeyError, TypeError):
            logger.warning("Invalid tool schema: %s", schema)
            return
        if name in self._tool_executors:
            existing_pid = self._tool_executors[name][1]
            logger.warning(
                "Tool name collision: '%s' already registered by %s; ignoring %s",
                name, existing_pid or "core", plugin_id or "core",
            )
            return
        # Pluck WhatsBot-only metadata so the schema we pass to OpenAI/OpenRouter
        # is a clean tool spec.
        clean = copy.deepcopy(schema)
        default_label = clean.pop("display_label", None)
        if default_label:
            self._tool_default_labels[name] = str(default_label)
        self._tool_originals[name] = clean
        self._tool_schemas.append(clean)
        self._tool_executors[name] = (executor, plugin_id)
        try:
            tool_override_repo.ensure(name, plugin_id)
        except Exception as e:
            logger.warning("tool_overrides.ensure failed for %s: %s", name, e)

    def register_plugin_tools(
        self,
        plugin_id: str,
        tools: list[tuple[dict, callable]],
    ) -> None:
        """Register tools from a plugin. Called by the plugin loader."""
        for schema, executor in tools:
            self._register_tool(schema, executor, plugin_id=plugin_id)

    def register_plugin_prompts(
        self,
        plugin_id: str,
        fragments: list[callable],
    ) -> None:
        """Register prompt fragments from a plugin. Called by the plugin loader."""
        for fn in fragments:
            self._prompt_fragments.append((fn, plugin_id))

    def known_tool_names(self) -> set[str]:
        """Names of every tool currently registered (core + plugin)."""
        return set(self._tool_originals.keys())

    def refresh_tool_overrides(self) -> None:
        """Re-read ``tool_overrides`` and rebuild ``_tool_schemas``.

        Called after every PUT on /api/tools/{name} and once after plugin
        loading at startup. Atomically replaces ``_tool_schemas`` so requests
        already in flight keep their captured reference.

        Plugin tools registered after startup are not supported today — plugin
        enable/disable forces a server restart, and this method assumes the
        registry is stable when called.
        """
        try:
            overrides = {row["name"]: row for row in tool_override_repo.list_all()}
        except Exception as e:
            logger.warning("Failed to read tool_overrides: %s", e)
            overrides = {}
        new_schemas: list[dict] = []
        new_disabled: set[str] = set()
        for name, original in self._tool_originals.items():
            ov = overrides.get(name)
            if ov and not ov["enabled"]:
                new_disabled.add(name)
                continue
            if ov and ov.get("description"):
                schema = copy.deepcopy(original)
                schema["function"]["description"] = ov["description"]
                new_schemas.append(schema)
            else:
                new_schemas.append(original)
        self._tool_schemas = new_schemas
        self._disabled_tools = new_disabled

    def list_tools(self) -> list[dict]:
        """Return metadata for every registered tool, with override state merged."""
        try:
            overrides = {row["name"]: row for row in tool_override_repo.list_all()}
        except Exception:
            overrides = {}
        items: list[dict] = []
        for name, original in self._tool_originals.items():
            fn = original.get("function", {})
            default_description = fn.get("description", "")
            default_label = self._tool_default_labels.get(name)
            _, plugin_id = self._tool_executors.get(name, (None, None))
            ov = overrides.get(name) or {}
            current_description = ov.get("description") or default_description
            current_label = ov.get("display_label") or default_label
            items.append({
                "name": name,
                "plugin_id": plugin_id,
                "default_description": default_description,
                "current_description": current_description,
                "default_label": default_label,
                "display_label": ov.get("display_label"),
                "current_label": current_label,
                "enabled": bool(ov.get("enabled", 1)),
                "has_override": bool(ov.get("description")),
                "has_label_override": bool(ov.get("display_label")),
                "parameters_schema": fn.get("parameters", {}),
            })
        return items

    def _make_tool_ctx(
        self,
        contact: ContactMemory,
        plugin_id: str | None = None,
    ) -> ToolContext:
        return ToolContext(
            contact=contact,
            handler=self,
            tag_registry=self.tag_registry,
            plugin_id=plugin_id,
        )

    def _dispatch_tool(
        self,
        contact: ContactMemory,
        name: str,
        args: dict,
    ) -> str | None:
        """Run a tool by name and return an optional follow-up feedback string."""
        entry = self._tool_executors.get(name)
        if not entry:
            logger.warning("Unknown tool: %s", name)
            return None
        if name in self._disabled_tools:
            logger.info("Tool '%s' is disabled by user override; skipping", name)
            return None
        executor, plugin_id = entry
        ctx = self._make_tool_ctx(contact, plugin_id=plugin_id)
        try:
            return executor(ctx, args)
        except Exception as e:
            logger.warning("Tool '%s' execution failed: %s", name, e)
            return None

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
        prompt += (
            "\n\nMensagens marcadas com '[Mensagem do operador humano]' no histórico "
            "foram enviadas por um atendente real, não por você. Considere o contexto "
            "mas não imite o estilo do operador."
        )

        # Plugin-contributed prompt fragments. Each fragment is a callable that
        # receives (contact, PromptContext) and returns a string (or empty).
        # Errors are isolated so a buggy plugin can't kill the request.
        for fragment_fn, plugin_id in self._prompt_fragments:
            try:
                ctx = PromptContext(handler=self, plugin_id=plugin_id)
                chunk = fragment_fn(contact, ctx)
                if chunk:
                    prompt += chunk
            except Exception as e:
                logger.warning(
                    "Plugin %s prompt fragment failed: %s",
                    plugin_id or "?", e,
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
            active_tools = self._tool_schemas
            track_step("llm_request", {
                "model": self.model,
                "context_messages": len(messages) - 1,
                "tools": [t["function"]["name"] for t in active_tools],
            })
            create_kwargs = {
                "model": self.model,
                "messages": messages,
                "max_tokens": 1024,
            }
            if active_tools:
                create_kwargs["tools"] = active_tools
                create_kwargs["tool_choice"] = "auto"
            response = client.chat.completions.create(**create_kwargs)

            self._record_usage(sender, "text", self.model, response)
            usage = response.usage
            track_step("llm_response", {
                "model": self.model,
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "has_tool_calls": bool(response.choices[0].message.tool_calls),
            })
            msg = response.choices[0].message

            # Handle tool calls via the registry
            executed_tools: list[dict] = []
            tool_feedbacks: dict[str, str] = {}
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError as e:
                        logger.warning("Failed to parse tool args for %s: %s", sender, e)
                        args = {}

                    feedback = self._dispatch_tool(contact, tool_name, args)
                    if feedback:
                        tool_feedbacks[tc.id] = feedback

                    executed_tools.append({"tool": tool_name, "args": args})
                    track_step("tool_executed", {"tool": tool_name, "args": args})
                    logger.info("Tool call for %s: %s(%s)", sender, tool_name, args)

                # If model only called tools without text, do a follow-up call
                if not msg.content:
                    messages.append(msg.model_dump())
                    for tc in msg.tool_calls:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": tool_feedbacks.get(tc.id, "Informações salvas com sucesso."),
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
