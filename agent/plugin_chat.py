"""Agno-powered assistant used by the built-in Chat / plugin creator."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Iterable

from agno.agent import Agent
from agno.models.message import Message
from agno.models.openai import OpenAILike
from agno.tools.function import Function
from agno.tools.workspace import Workspace

from config.settings import LLM_API_BASE_URL

logger = logging.getLogger(__name__)

SYSTEM_HELP_KNOWLEDGE_PATH = Path(__file__).with_name("SYSTEM_HELP.md")
PLUGIN_CREATOR_GUIDE_PATH = Path(__file__).with_name("PLUGIN_CREATOR.md")
_REFERENCE_EXTENSIONS = {".md", ".py", ".js", ".css", ".html", ".yaml", ".yml", ".txt", ".toml"}
_REFERENCE_ROOTS = (
    "README.md", "CLAUDE.md", "AGENTS.md", "agent", "config", "db", "docs", "plugins",
    "server", "web", "gowa", "tests", "assets/plugin_examples", "storages/plugins",
)
_BLOCKED_REFERENCE_PARTS = {".git", "venv", "logs", "__pycache__", "plugin_data"}
_SAFE_HELP_CONFIG_KEYS = {
    "model", "improvement_model", "audio_model", "image_model", "document_model",
    "auto_reply", "default_ai_enabled", "max_context_messages", "inactivity_timeout_min",
    "message_batch_delay", "response_delay_min", "response_delay_max", "split_messages",
    "split_message_delay", "audio_transcription_mode", "audio_transcription_target",
    "audio_transcription_chat_prefix", "image_transcription_enabled",
    "document_transcription_enabled", "transfer_alert_enabled", "transfer_alert_duration",
    "group_reply_mode", "low_balance_enabled", "low_balance_threshold", "max_executions",
    "ai_engine_enabled", "setup_completed", "gowa_auto_check_enabled", "gowa_latest_version",
    "gowa_last_check_at", "gowa_proxy_enabled", "gowa_proxy_mode", "gowa_proxy_scheme",
    "whatsbot_update_notifications_enabled", "whatsbot_skipped_version",
}

SYSTEM_HELP_PROMPT = """Você é a ajuda integrada do WhatsBot-Lite.
Responda em português brasileiro, com frases curtas e linguagem para uma pessoa sem conhecimento técnico.
Seu único escopo é explicar como usar e configurar o WhatsBot-Lite. Você pode ler o sistema, mas nunca o modifica.

A BASE OFICIAL DE AJUDA abaixo já está carregada nesta mensagem. Consulte-a primeiro e responda sem usar ferramentas quando ela trouxer a orientação necessária.
Use as ferramentas de consulta somente se a informação estiver ausente, incompleta ou parecer incompatível com a versão atual. Nesse caso, faça buscas objetivas nas referências do sistema, no código dos plugins instalados e na estrutura do banco. Nunca consulte nem exponha mensagens, contatos, chaves, senhas ou outros dados pessoais.
Se precisar investigar, conclua a resposta em linguagem simples e apresente apenas o caminho que a pessoa deve seguir. Não cite nomes de arquivos ou detalhes internos, salvo se ela pedir uma explicação técnica.

Você não cria, planeja, pesquisa nem altera plugins neste projeto. Se o usuário quiser criar ou modificar um plugin, explique em uma frase que ele deve clicar no botão + no topo da barra lateral esquerda do Chat, criar um projeto e descrever ali o que deseja. Não faça perguntas sobre o plugin e não use ferramentas para investigar esse pedido.

Nunca mencione endpoints, REST, JSON, IDs, banco de dados, classes ou detalhes de programação, salvo se o próprio usuário pedir uma explicação técnica.
"""


def load_system_help_knowledge(path: Path | None = None) -> str:
    """Read the canonical help base on every help-agent construction.

    Reading it at construction time keeps a long-running development server in
    sync with documentation edits and makes this file the single maintained
    source of end-user navigation guidance.
    """
    knowledge_path = path or SYSTEM_HELP_KNOWLEDGE_PATH
    try:
        content = knowledge_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning("System help knowledge could not be loaded from %s: %s", knowledge_path, exc)
        return "Base oficial indisponível. Consulte as referências do sistema antes de responder."
    return content or "Base oficial vazia. Consulte as referências do sistema antes de responder."


def system_help_prompt(panel_base_url: str = "") -> str:
    """Return the help prompt with the canonical base and current address.

    The base URL comes from the current request, so a user accessing a local
    installation receives ``localhost:port`` links while a hosted installation
    receives its public domain. The Markdown base is loaded on each call so it
    remains the source of truth for end-user features and navigation.
    """
    base = (panel_base_url or "").rstrip("/")
    knowledge = load_system_help_knowledge().replace("{{base_url}}", base)
    return f"{SYSTEM_HELP_PROMPT.rstrip()}\n\n--- BASE OFICIAL DE AJUDA ---\n{knowledge}"

PLUGIN_PROMPT = """Você é o Criador de Plugins do WhatsBot-Lite. Converse em português brasileiro com uma pessoa que não sabe programar.

Antes de usar qualquer ferramenta ou escrever código, confirme que entendeu:
- o que o plugin deve resolver;
- como ele deve interagir com as conversas do WhatsApp;
- se precisa de uma tela no painel e o que a pessoa fará nela;
- quais informações precisam ser cadastradas ou guardadas;
- um exemplo simples do resultado esperado.

Se essas informações estiverem incompletas, faça no máximo três perguntas curtas por vez. Use palavras comuns e exemplos. Coloque cada pergunta em uma linha própria, numerada, com uma linha em branco entre elas; nunca junte duas perguntas no mesmo parágrafo. Não fale de endpoints, REST, JSON, IDs, tabelas, schemas, classes ou detalhes internos. Não use ferramentas enquanto estiver esclarecendo o pedido. Quando o pedido já estiver claro, diga em uma frase o que entendeu e comece o trabalho sem pedir uma confirmação adicional. Essa frase e a primeira chamada de escrita devem ocorrer na mesma resposta do modelo; nunca encerre a rodada apenas dizendo que vai criar.

O contrato completo de plugins está carregado abaixo. Ele é a primeira fonte de verdade desta execução.
Normalmente ele basta. Consulte exemplos oficiais, outros plugins ou o código do WhatsBot-Lite somente quando
faltar uma informação concreta, com busca direcionada. Não faça um inventário geral do repositório.
Em projeto novo, o WhatsBot-Lite já fornece um esqueleto descartável e marca o projeto com
`.whatsbot-scaffold`. O conteúdo relevante já está aqui: o id obrigatório aparece no contexto, a versão é
`1.0.0`, a compatibilidade é `">=1.2,<2.0"` e o restante deve refletir o pedido. Não leia nem liste o
esqueleto. A primeira ferramenta deve ser `write_file` para sobrescrever `plugin.yaml`; continue criando os
demais arquivos na mesma execução. Apague `.whatsbot-scaffold` apenas quando o pedido estiver completo; a
validação reprova enquanto ele existir. Não faça uma rodada somente de raciocínio ou anúncio. Se uma chamada
falhar por argumentos ou formatação, corrija e repita; não pesquise o repositório para resolver um erro de ferramenta.

Regras de implementação:
- O plugin deve seguir o formato nativo do WhatsBot-Lite, usar SQLAlchemy e tabelas prefixadas com plugin_<id>_.
- Preserve o id do plugin e migrations já publicadas. Atualizações adicionam migrations numeradas; nunca reescrevem migrations aplicadas.
- Guarde uploads, imagens geradas e outros arquivos do usuário em plugins.context.plugin_data_dir('<id>'), fora da pasta substituível do código.
- Configuração do plugin vive no próprio plugin. Não altere o painel de configurações do core.
- Execute validações e testes aplicáveis. Ao encontrar erro, investigue e corrija autonomamente.
- Não diga que instalou ou atualizou durante a criação. Depois da validação, informe apenas que o plugin está
  pronto e que o WhatsBot-Lite exibirá a opção **Sim, instalar**. O usuário também pode autorizar escrevendo
  “instale por favor” no próprio chat; nesse caso o WhatsBot-Lite executa a instalação diretamente. Nunca mande
  o usuário procurar esse plugin na página Plugins antes de instalá-lo.
- Evite dependências externas quando a biblioteca padrão ou dependências do host forem suficientes.
- Quando uma ação depender de um efeito externo, como enviar uma mensagem, só persista o novo status e a
  marca de notificação depois que o efeito externo terminar com sucesso. Se o envio falhar, preserve o
  estado anterior para permitir nova tentativa. Inclua um teste comportamental dessa falha quando esse
  fluxo fizer parte do plugin.
- Não encerre dizendo que vai começar, que continuará depois ou que fará o trabalho na próxima rodada. Quando o pedido estiver claro, use as ferramentas para criar os arquivos nesta mesma execução. Só responda ao usuário depois de validar o resultado ou se faltar uma decisão que realmente impeça a implementação.
- Se o usuário disser para você decidir detalhes de tela, texto ou fluxo, adote padrões simples e prossiga.
- Use o terminal para testes e diagnósticos portáteis dentro do workspace. Não instale pacotes nem acesse a rede.
- Quando o plugin tiver tela, trate o design como parte da implementação. Antes de escrever o componente,
  defina mentalmente o trabalho principal da página, a ordem de leitura dos dados e como a mesma informação
  se reorganiza no celular. Siga integralmente o contrato de qualidade visual da referência oficial.
- Não copie uma tela de pedidos nem force cards em todo plugin. Escolha a estrutura adequada ao conteúdo,
  mas entregue a mesma qualidade: largura bem aproveitada, hierarquia clara, densidade útil, contraste real,
  estados completos, foco visível e responsividade. `validate_plugin_project` também audita esses requisitos.

Use as ferramentas do workspace para trabalhar no projeto e `validate_plugin_project` para validar. As
ferramentas de referência são somente leitura e complementares. As ações são exibidas ao usuário,
portanto use nomes objetivos e comece a criar os arquivos assim que o pedido estiver claro.
"""


def load_plugin_creator_guide(path: Path | None = None) -> str:
    guide_path = path or PLUGIN_CREATOR_GUIDE_PATH
    try:
        content = guide_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"referência oficial de plugins indisponível: {guide_path}") from exc
    if not content:
        raise RuntimeError(f"referência oficial de plugins vazia: {guide_path}")
    return content


def plugin_creator_prompt(workspace: Path | None = None) -> str:
    """Build the stable SDK prompt plus the concrete project identity.

    The guide stays before the dynamic block to keep the expensive prefix as
    stable as possible for provider prompt caching.
    """
    prompt = f"{PLUGIN_PROMPT.rstrip()}\n\n--- REFERÊNCIA OFICIAL DE PLUGINS ---\n{load_plugin_creator_guide()}"
    if workspace is None:
        return prompt
    existing = []
    if workspace.is_dir():
        existing = [
            path.relative_to(workspace).as_posix()
            for path in sorted(workspace.rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        ][:120]
    inventory = "\n".join(f"- {path}" for path in existing) or "- workspace vazio"
    scaffold = ".whatsbot-scaffold" in existing
    starter = (
        "Este é um projeto novo com estrutura inicial descartável. Não leia nem liste os arquivos iniciais. "
        "Sua primeira ação deve ser `write_file` em `plugin.yaml`; depois implemente o pedido completo e "
        "apague `.whatsbot-scaffold` antes da validação. Não pesquise referências antes de começar.\n"
        if scaffold else ""
    )
    return (
        f"{prompt}\n\n--- CONTEXTO DESTE PROJETO ---\n"
        f"ID obrigatório do plugin: {workspace.name}\n"
        f"{starter}"
        "Todos os caminhos das ferramentas Workspace são relativos à raiz deste plugin.\n"
        "Arquivos existentes:\n"
        f"{inventory}"
    )

DISCOVERY_PROMPT = """Você ajuda uma pessoa sem conhecimento técnico a explicar o plugin que deseja criar para o WhatsBot-Lite.
Ainda não programe e não mencione detalhes técnicos. Entenda o pedido e faça no máximo três perguntas curtas e específicas. Pergunte apenas o que ainda falta entre: objetivo, interação com as conversas do WhatsApp, necessidade de uma tela no painel, informações que serão guardadas e um exemplo do resultado esperado.
Comece reconhecendo o pedido em uma frase simples. Depois escreva uma frase curta explicando que precisa entender alguns detalhes.

Formatação obrigatória:
- Separe a introdução e as perguntas com uma linha em branco.
- Escreva cada pergunta em uma linha própria, numerada como 1., 2. e 3.
- Comece cada pergunta com um rótulo curto em negrito, por exemplo: **Funcionamento:**.
- Nunca coloque duas perguntas no mesmo parágrafo.
- Termine com uma frase curta dizendo que a pessoa pode responder do jeito dela.

Não use listas longas, entidades HTML nem explique como o sistema será implementado.
"""

SUMMARY_PROMPT = """Resuma uma conversa de desenvolvimento de plugin do WhatsBot-Lite para retomada técnica.
Preserve somente fatos confirmados: objetivo do usuário, decisões, arquivos criados ou alterados, estado da
validação, erros relevantes e próximos passos. Não crie plugin, não use ferramentas, não dê orientação ao
usuário e não invente. Responda em português, de forma compacta e estruturada.
"""


def _safe_reference_path(root: Path, relative: str) -> Path:
    relative = (relative or "").replace("\\", "/").lstrip("/")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("caminho fora das referências do WhatsBot-Lite") from exc
    parts = set(candidate.relative_to(root.resolve()).parts)
    if parts & _BLOCKED_REFERENCE_PARTS:
        raise ValueError("essa pasta não faz parte das referências disponíveis")
    allowed = any(relative == item or relative.startswith(item + "/") for item in _REFERENCE_ROOTS)
    if not allowed:
        raise ValueError("referência fora das áreas permitidas")
    return candidate


def _is_allowed_reference(relative: Path) -> bool:
    value = relative.as_posix()
    return any(value == item or value.startswith(item + "/") for item in _REFERENCE_ROOTS)


def _inspect_system_state(section: str) -> str:
    """Return support diagnostics without exposing user content or secrets."""
    section = (section or "settings").strip().lower()
    if section == "settings":
        from config.settings import DEFAULT_CONFIG
        from db.repositories import config_repo

        stored = config_repo.get_all()
        settings = {
            key: stored.get(key, DEFAULT_CONFIG.get(key))
            for key in sorted(_SAFE_HELP_CONFIG_KEYS)
            if key in stored or key in DEFAULT_CONFIG
        }
        settings["api_key_configured"] = bool(stored.get("openrouter_api_key"))
        settings["panel_password_configured"] = bool(stored.get("web_password_hash"))
        return json.dumps(settings, ensure_ascii=False, default=str)

    if section == "plugins":
        from db.repositories import plugin_repo

        plugins = [
            {
                "id": row.get("id"),
                "version": row.get("version"),
                "enabled": bool(row.get("enabled")),
                "load_status": "error" if row.get("load_error") else "ok",
            }
            for row in plugin_repo.list_all()
        ]
        return json.dumps(plugins, ensure_ascii=False)

    if section == "schema":
        from sqlalchemy import inspect as sqlalchemy_inspect
        from db.engine import get_engine

        engine = get_engine()
        inspector = sqlalchemy_inspect(engine)
        tables = []
        for table_name in sorted(inspector.get_table_names())[:100]:
            columns = [column["name"] for column in inspector.get_columns(table_name)[:80]]
            tables.append({"table": table_name, "columns": columns})
        return json.dumps({"database": engine.dialect.name, "tables": tables}, ensure_ascii=False)

    return "Error: seção inválida; use settings, plugins ou schema"


def reference_functions(
    project_root: Path, *, include_state: bool = False, plugin_creator: bool = False,
) -> list[Function]:
    repeated_calls: set[str] = set()

    def reject_repeated(name: str, values: dict) -> str | None:
        if not plugin_creator:
            return None
        signature = json.dumps([name, values], ensure_ascii=False, sort_keys=True, default=str)
        if signature in repeated_calls:
            return (
                "Error: esta consulta já foi executada com os mesmos argumentos. "
                "Use a informação anterior, faça uma pergunta diferente ou volte à implementação."
            )
        repeated_calls.add(signature)
        return None

    async def list_files(directory: str = ".", limit: int = 200) -> str:
        repeated = reject_repeated("list", {"directory": directory, "limit": limit})
        if repeated:
            return repeated
        base = project_root if directory in ("", ".") else _safe_reference_path(project_root, directory)
        if not base.exists():
            return "Error: pasta não encontrada"
        items: list[str] = []
        candidates = [base] if base.is_file() else base.rglob("*")
        for path in candidates:
            if len(items) >= max(1, min(limit, 500)):
                break
            rel = path.relative_to(project_root)
            if set(rel.parts) & _BLOCKED_REFERENCE_PARTS:
                continue
            if not _is_allowed_reference(rel):
                continue
            if path.is_file() and path.suffix.lower() in _REFERENCE_EXTENSIONS:
                items.append(rel.as_posix())
        return "\n".join(items) or "Nenhum arquivo encontrado."

    async def read_file(path: str, start_line: int = 1, end_line: int = 400) -> str:
        repeated = reject_repeated(
            "read", {"path": path, "start_line": start_line, "end_line": end_line},
        )
        if repeated:
            return repeated
        try:
            target = _safe_reference_path(project_root, path)
        except ValueError as exc:
            return f"Error: {exc}"
        if not target.is_file() or target.suffix.lower() not in _REFERENCE_EXTENSIONS:
            return "Error: arquivo de referência não encontrado ou formato não permitido"
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, start_line)
        end = min(len(lines), max(start, min(end_line, start + 799)))
        return "\n".join(f"{i}: {lines[i - 1]}" for i in range(start, end + 1))

    async def search(query: str, directory: str = ".", limit: int = 20) -> str:
        repeated = reject_repeated(
            "search", {"query": query, "directory": directory, "limit": limit},
        )
        if repeated:
            return repeated
        if not query or len(query) > 200:
            return "Error: busca vazia ou longa demais"
        try:
            base = project_root if directory in ("", ".") else _safe_reference_path(project_root, directory)
        except ValueError as exc:
            return f"Error: {exc}"
        if not base.exists():
            return "Error: referência não encontrada"
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        found: list[str] = []
        candidates = [base] if base.is_file() else base.rglob("*")
        for path in candidates:
            if len(found) >= max(1, min(limit, 100)):
                break
            rel = path.relative_to(project_root)
            if set(rel.parts) & _BLOCKED_REFERENCE_PARTS or not path.is_file():
                continue
            if not _is_allowed_reference(rel):
                continue
            if path.suffix.lower() not in _REFERENCE_EXTENSIONS:
                continue
            try:
                for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if pattern.search(line):
                        found.append(f"{rel.as_posix()}:{number}: {line[:300]}")
                        if len(found) >= limit:
                            break
            except OSError:
                continue
        return "\n".join(found) or "Nenhuma ocorrência encontrada."

    async def inspect_state(section: str = "settings") -> str:
        return await asyncio.to_thread(_inspect_system_state, section)

    functions = [
        Function(
            name="list_whatsbot_files", description=(
                "Lista referências do WhatsBot-Lite. No criador, use apenas para localizar um exemplo ou contrato "
                "específico ausente no guia; não liste o repositório inteiro."
                if plugin_creator else
                "Lista arquivos de documentação e código do WhatsBot-Lite disponíveis para consulta."
            ),
            parameters={"type": "object", "properties": {"directory": {"type": "string"}, "limit": {"type": "integer"}}},
            entrypoint=list_files, skip_entrypoint_processing=True,
        ),
        Function(
            name="read_whatsbot_file", description=(
                "Lê um trecho de exemplo, plugin instalado ou API pública do WhatsBot-Lite. Use somente quando "
                "o guia carregado não responder uma dúvida concreta."
                if plugin_creator else
                "Lê um trecho numerado de um arquivo do WhatsBot-Lite em modo somente leitura."
            ),
            parameters={"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, "required": ["path"]},
            entrypoint=read_file, skip_entrypoint_processing=True,
        ),
        Function(
            name="search_whatsbot", description=(
                "Pesquisa uma informação concreta nas referências do WhatsBot-Lite. Não repita buscas equivalentes "
                "e comece a implementar assim que a lacuna estiver resolvida."
                if plugin_creator else
                "Pesquisa texto na documentação e no código do WhatsBot-Lite em modo somente leitura."
            ),
            parameters={"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]},
            entrypoint=search, skip_entrypoint_processing=True,
        ),
    ]
    if include_state:
        functions.append(Function(
            name="inspect_whatsbot_state",
            description="Consulta o estado seguro do WhatsBot-Lite para diagnóstico: configurações operacionais sem segredos, plugins registrados ou estrutura das tabelas sem registros pessoais.",
            parameters={
                "type": "object",
                "properties": {"section": {"type": "string", "enum": ["settings", "plugins", "schema"]}},
            },
            entrypoint=inspect_state, skip_entrypoint_processing=True,
        ))
    return functions


def build_model(api_key: str, model_id: str, reasoning: str = "") -> OpenAILike:
    kwargs = {
        "id": model_id,
        "api_key": api_key,
        "base_url": LLM_API_BASE_URL,
        "max_tokens": 8192,
        # Stable per-conversation prefixes are kept by the caller. OpenRouter
        # and compatible proxies report actual cache reads in usage metrics.
    }
    if reasoning:
        # The configured connection is OpenRouter-compatible. Its normalized
        # Chat Completions contract uses the ``reasoning`` object across model
        # providers (OpenAI, Anthropic, DeepSeek, etc.).
        kwargs["extra_body"] = {"reasoning": {"effort": reasoning}}
    return OpenAILike(**kwargs)


def workspace_functions(workspace: Path, *, require_read_before_write: bool) -> list[Function]:
    """Expose Agno's safe workspace with explicit schemas.

    Agno 2.9 can register ``Workspace`` methods with empty parameter objects
    under OpenAILike. Larger models sometimes guess the arguments, while small
    models either stop or repeatedly list files. Wrapping the same protected
    implementation keeps its path checks and atomic writes while ensuring the
    provider receives usable JSON schemas.
    """
    toolkit = Workspace(
        workspace,
        allowed=["read", "list", "search", "write", "edit", "move", "delete", "shell"],
        confirm=[],
        require_read_before_write=require_read_before_write,
        exclude_patterns=[".git", "__pycache__", ".pytest_cache", "*.pyc", "versions"],
    )

    def run_workspace_command(args: list[str], tail: int = 100, timeout: int = 120) -> str:
        """Keep diagnostics inside the plugin and away from host internals."""
        values = [str(value) for value in (args or [])]
        if not values:
            return "Error: informe o comando em args"
        outside_path = re.compile(r"(^|[\s\"'])(?:/|~/?|\.\./)")
        if any(outside_path.search(value) for value in values):
            return (
                "Error: use apenas comandos e caminhos relativos ao workspace do plugin. "
                "Não procure executáveis, testes ou arquivos do host; use validate_plugin_project."
            )
        return toolkit.run_command(values, tail=tail, timeout=timeout)

    specs = [
        ("read_file", "Lê um arquivo do projeto com números de linha.", {
            "type": "object", "properties": {
                "path": {"type": "string", "description": "Caminho relativo à raiz do plugin"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
            }, "required": ["path"],
        }),
        ("list_files", "Lista arquivos do projeto. Evite repetir esta consulta.", {
            "type": "object", "properties": {
                "directory": {"type": "string", "default": "."},
                "pattern": {"type": "string"},
                "recursive": {"type": "boolean", "default": False},
                "max_depth": {"type": "integer", "minimum": 1, "maximum": 8, "default": 3},
            },
        }),
        ("search_content", "Pesquisa texto nos arquivos do projeto.", {
            "type": "object", "properties": {
                "query": {"type": "string"},
                "directory": {"type": "string", "default": "."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
            }, "required": ["query"],
        }),
        ("write_file", "Cria ou sobrescreve um arquivo de texto do plugin. Use para implementar arquivos completos.", {
            "type": "object", "properties": {
                "path": {"type": "string", "description": "Caminho relativo à raiz do plugin"},
                "content": {"type": "string", "description": "Conteúdo completo do arquivo"},
                "overwrite": {"type": "boolean", "default": True},
            }, "required": ["path", "content"],
        }),
        ("edit_file", "Substitui um trecho exato de um arquivo já lido.", {
            "type": "object", "properties": {
                "path": {"type": "string"},
                "old_str": {"type": "string"},
                "new_str": {"type": "string"},
                "replace_all": {"type": "boolean", "default": False},
            }, "required": ["path", "old_str", "new_str"],
        }),
        ("move_file", "Move ou renomeia um arquivo dentro do projeto.", {
            "type": "object", "properties": {
                "src": {"type": "string"}, "dst": {"type": "string"},
                "overwrite": {"type": "boolean", "default": False},
            }, "required": ["src", "dst"],
        }),
        ("delete_file", "Apaga um arquivo do projeto. Não apaga diretórios.", {
            "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"],
        }),
        ("run_command", "Executa um comando portátil na raiz do plugin, somente com caminhos relativos. Não procure Python, pytest ou arquivos do host; a validação integrada executa os testes.", {
            "type": "object", "properties": {
                "args": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "tail": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
                "timeout": {"type": "integer", "minimum": 1, "maximum": 180, "default": 120},
            }, "required": ["args"],
        }),
    ]
    if not require_read_before_write:
        # The first visible schema reinforces the required first action for a
        # new project. Existing projects keep read-first ordering.
        specs.sort(key=lambda item: 0 if item[0] == "write_file" else 1)
    functions = []
    for name, description, parameters in specs:
        entrypoint = run_workspace_command if name == "run_command" else getattr(toolkit, name)
        functions.append(Function(
            name=name,
            description=description,
            parameters=parameters,
            entrypoint=entrypoint,
            skip_entrypoint_processing=True,
        ))
    return functions


def build_agent(
    *, api_key: str, model_id: str, reasoning: str, project_kind: str,
    workspace: Path | None, project_root: Path, panel_base_url: str = "",
) -> Agent:
    if project_kind == "discovery":
        tools: list = []
        system_message = DISCOVERY_PROMPT
    elif project_kind == "summary":
        tools = []
        system_message = SUMMARY_PROMPT
    elif project_kind == "system":
        tools = reference_functions(project_root, include_state=True)
        system_message = system_help_prompt(panel_base_url)
    else:
        is_scaffold = bool(workspace and (workspace / ".whatsbot-scaffold").is_file())
        has_existing_files = bool(
            workspace and workspace.is_dir()
            and any(path.is_file() for path in workspace.rglob("*"))
        ) and not is_scaffold
        # A new plugin starts from the complete embedded contract, which keeps
        # it from spending its first turn inventorying the repository. Once a
        # project has code, directed references become available for updates
        # and concrete compatibility questions.
        tools = (
            reference_functions(project_root, plugin_creator=True)
            if has_existing_files else []
        )
        system_message = plugin_creator_prompt(workspace)
    if project_kind == "plugin" and workspace is not None:
        workspace.mkdir(parents=True, exist_ok=True)
        tools[0:0] = workspace_functions(
            workspace, require_read_before_write=not is_scaffold,
        )
        async def validate_plugin() -> str:
            """Run the same independent validation required before installation."""
            try:
                # Lazy import avoids coupling server startup to this optional
                # agent tool while keeping one validation source of truth.
                from server.routes.chat import validate_workspace
                result = await asyncio.to_thread(
                    validate_workspace,
                    {"kind": "plugin", "plugin_id": workspace.name, "workspace_path": str(workspace)},
                    save_version=False,
                )
                return json.dumps(result, ensure_ascii=False)
            except Exception as exc:
                return f"Error: {exc}"

        tools.append(Function(
            name="validate_plugin_project",
            description="Valida manifest, migrations, sintaxe, dependências e testes do plugin atual. Use antes de declarar o trabalho concluído e corrija qualquer erro retornado.",
            parameters={"type": "object", "properties": {}},
            entrypoint=validate_plugin,
            skip_entrypoint_processing=True,
        ))
    # Both current inexpensive defaults enable substantial reasoning when the
    # request omits a level (DeepSeek V4.1 Flash defaults to high, Gemini 3.8
    # Flash to medium). On the creator prompt DeepSeek consumed the complete
    # 8k output budget without reaching its first tool call even at low. Its
    # endpoint accepts ``none`` and then writes normally; mandatory-reasoning
    # models such as Gemini keep a low budget. An explicit UI selection wins.
    creator_default_reasoning = (
        "none" if model_id == "deepseek/deepseek-v4.1-flash" else "low"
    )
    effective_reasoning = reasoning or (
        creator_default_reasoning if project_kind == "plugin" else ""
    )
    return Agent(
        name="WhatsBot-Lite Chat",
        model=build_model(api_key, model_id, effective_reasoning),
        system_message=system_message,
        tools=tools,
        markdown=True,
        telemetry=False,
        # A complex plugin legitimately needs many reference reads, file writes,
        # tests and fixes. A global cap used to stop the run after 40 calls,
        # frequently just when the model was about to create the first file.
        # The route owns a wall-clock timeout and cancellation for stuck runs.
        tool_call_limit=None,
        build_context=False,
        add_history_to_context=False,
        add_datetime_to_context=False,
        resolve_in_context=False,
        retries=2,
        delay_between_retries=2,
        exponential_backoff=True,
    )


def history_messages(summary: str, rows: Iterable[dict]) -> list[Message]:
    messages: list[Message] = []
    if summary:
        messages.append(Message(role="system", content="Resumo persistente da conversa anterior:\n" + summary))
    for row in rows:
        if row.get("kind") != "message" or row.get("role") not in ("user", "assistant"):
            continue
        messages.append(Message(role=row["role"], content=row.get("content") or ""))
    return messages


def metrics_dict(metrics) -> dict:
    if not metrics:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    return {
        "input_tokens": getattr(metrics, "input_tokens", 0) or 0,
        "output_tokens": getattr(metrics, "output_tokens", 0) or 0,
        "total_tokens": getattr(metrics, "total_tokens", 0) or 0,
        "cache_read_tokens": getattr(metrics, "cache_read_tokens", 0) or 0,
        "cache_write_tokens": getattr(metrics, "cache_write_tokens", 0) or 0,
        "reasoning_tokens": getattr(metrics, "reasoning_tokens", 0) or 0,
    }
