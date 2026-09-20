"""Focused, offline tests for Chat persistence and plugin validation."""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

tmp = Path(tempfile.mkdtemp(prefix="whatsbot_chat_test_"))

from db import init_db  # noqa: E402

init_db(tmp / "whatsbot.db")

from db.repositories import chat_repo  # noqa: E402
from plugins.context import get_plugin_setting, send_whatsapp_message, set_runtime  # noqa: E402
from server.routes.chat import (  # noqa: E402
    _STREAM_HEARTBEAT,
    _format_discovery_response,
    _ensure_system_help_link,
    _is_plugin_intent,
    _safe_workspace_file,
    _scaffold_plugin_workspace,
    _run_simple_tests,
    _validate_frontend_quality,
    _with_timeout,
    validate_workspace,
)
from agent.plugin_chat import (  # noqa: E402
    _inspect_system_state,
    _safe_reference_path,
    load_plugin_creator_guide,
    load_system_help_knowledge,
    plugin_creator_prompt,
    reference_functions,
    system_help_prompt,
)


def _plugin_dir(plugin_id="demo_chat", migration_sql=None):
    workspace = tmp / "projects" / plugin_id
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "plugin.yaml").write_text(
        f"id: {plugin_id}\n"
        f"name: Demo Chat\n"
        f"version: 1.0.0\n"
        f'whatsbot_api_version: ">=1.0,<2.0"\n'
        + ("migrations: migrations\n" if migration_sql else ""),
        encoding="utf-8",
    )
    (workspace / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    if migration_sql:
        (workspace / "migrations").mkdir(exist_ok=True)
        (workspace / "migrations" / "001_initial.sql").write_text(migration_sql, encoding="utf-8")
    return workspace


def test_system_help_prompt_uses_current_panel_origin():
    prompt = system_help_prompt("http://localhost:8080/")
    assert "http://localhost:8080/painel?aba=agente#prompt" in prompt
    assert "[Abrir instruções do agente](http://localhost:8080/painel?aba=agente#prompt)" in prompt
    assert "BASE OFICIAL DE AJUDA" in prompt
    assert "responda sem usar ferramentas" in prompt
    assert "{{base_url}}" not in prompt
    assert "https://" not in prompt


def test_system_help_knowledge_is_reloaded_and_has_safe_fallback():
    knowledge = tmp / "SYSTEM_HELP.md"
    knowledge.write_text("primeira versão", encoding="utf-8")
    assert load_system_help_knowledge(knowledge) == "primeira versão"
    knowledge.write_text("versão atualizada", encoding="utf-8")
    assert load_system_help_knowledge(knowledge) == "versão atualizada"
    assert "Consulte as referências" in load_system_help_knowledge(tmp / "missing.md")


def test_plugin_creator_guide_is_loaded_into_the_prompt():
    guide = load_plugin_creator_guide()
    workspace = tmp / "prompt_identity"
    workspace.mkdir(exist_ok=True)
    prompt = plugin_creator_prompt(workspace)
    assert "send_whatsapp_message" in guide
    assert "validate_plugin_project" in guide
    assert guide in prompt
    assert "Use-a primeiro" in prompt
    assert "ID obrigatório do plugin: prompt_identity" in prompt
    assert "Contrato de qualidade visual" in guide
    assert "1366×768" in guide and "360×800" in guide
    assert "max-w-5xl mx-auto text-wa-text" not in guide


def test_frontend_quality_rejects_old_narrow_screen_and_accepts_responsive_screen():
    bad = tmp / "bad-screen.js"
    bad.write_text(
        "export default function X(){return html`<div class=\"max-w-5xl mx-auto\">"
        "<input /><div>${items.map(x => x.name)}</div></div>`}",
        encoding="utf-8",
    )
    try:
        _validate_frontend_quality(bad)
    except ValueError as exc:
        message = str(exc)
        assert "largura" in message
        assert "breakpoint" in message
        assert "wa-field" in message
    else:
        raise AssertionError("a tela estreita e sem contraste deveria ser rejeitada")

    good = tmp / "good-screen.js"
    good.write_text(
        """export default function X(){
        const [loading,setLoading]=useState(true); const [error,setError]=useState('');
        fetch('/items');
        return html`<section class=\"w-full text-wa-text\"><style>
        .good:focus-visible{outline:3px solid currentColor}@media(max-width:560px){.good{width:100%}}
        </style><h2>Itens</h2><label>Busca<input class=\"wa-field good\" /></label>
        ${error ? html`<p>${error}<button>Tentar novamente</button></p>` : null}
        ${loading ? html`<p>Carregando</p>` : items.length === 0 ? html`<p>Nenhum item</p>` : null}
        </section>`}
        """,
        encoding="utf-8",
    )
    result = _validate_frontend_quality(good)
    assert result["responsive"] is True
    assert result["form_contrast"] is True

    crowded = tmp / "crowded-screen.js"
    crowded.write_text(
        """export default function X(){
        const [loading,setLoading]=useState(false); const [error,setError]=useState(''); fetch('/items');
        return html`<section class="crowded-page"><style>
        .crowded-page{--c-ink:#111827;--c-muted:#4b5563;--c-surface:#ffffff;--c-line:#d1d5db;width:100%}
        html.dark .crowded-page{--c-ink:#f9fafb;--c-muted:#9ca3af;--c-surface:#1f2937;--c-line:#374151}
        .crowded-page :focus-visible{outline:3px solid blue}@media(max-width:560px){.crowded-page{padding:12px}}
        </style><h1>Itens</h1><label>Busca<input class="wa-field" /></label>
        ${error ? html`<p>Erro <button>Tentar novamente</button></p>` : null}
        ${loading ? html`<p>Carregando</p>` : items.length === 0 ? html`<p>Nenhum item</p>`
          : items.map(item => html`<textarea class="wa-field" aria-label="Nota"></textarea>`)}</section>`}
        """,
        encoding="utf-8",
    )
    try:
        _validate_frontend_quality(crowded)
    except ValueError as exc:
        message = str(exc)
        assert "contraste" in message
        assert "textarea aberta" in message
    else:
        raise AssertionError("campos sem contraste e repetidos deveriam ser rejeitados")


def test_new_plugin_scaffold_forces_real_implementation():
    workspace = tmp / "projects" / "starter_chat"
    _scaffold_plugin_workspace(workspace, "starter_chat", "Agenda de visitas")
    assert "id: starter_chat" in (workspace / "plugin.yaml").read_text(encoding="utf-8")
    assert (workspace / "__init__.py").is_file()
    assert (workspace / ".whatsbot-scaffold").is_file()
    starter_prompt = plugin_creator_prompt(workspace)
    assert "primeira ação deve ser `write_file` em `plugin.yaml`" in starter_prompt
    assert "Não leia nem liste" in starter_prompt
    project = {"kind": "plugin", "plugin_id": "starter_chat", "workspace_path": str(workspace)}
    try:
        validate_workspace(project, save_version=False)
    except ValueError as exc:
        assert ".whatsbot-scaffold" in str(exc)
    else:
        raise AssertionError("a estrutura inicial vazia não pode ficar pronta para instalação")


def test_workspace_tools_expose_real_schemas_to_inexpensive_models():
    from agent.plugin_chat import build_agent

    workspace = tmp / "projects" / "tool_schema_chat"
    _scaffold_plugin_workspace(workspace, "tool_schema_chat", "Ferramentas")
    agent = build_agent(
        api_key="fake", model_id="provider/model", reasoning="",
        project_kind="plugin", workspace=workspace, project_root=ROOT,
    )
    tools = {getattr(tool, "name", ""): tool for tool in agent.tools}
    assert list(tools)[0] == "write_file"
    write_schema = tools["write_file"].to_dict()["parameters"]
    assert set(write_schema["required"]) == {"path", "content"}
    assert write_schema["properties"]["content"]["type"] == "string"
    command_schema = tools["run_command"].to_dict()["parameters"]
    assert command_schema["properties"]["args"]["items"]["type"] == "string"
    blocked = tools["run_command"].entrypoint(["grep", "-rn", "teste", "/home/francisco"])
    assert blocked.startswith("Error:") and "host" in blocked


def test_public_plugin_runtime_helpers_send_messages_and_read_settings():
    from db.repositories import config_repo

    class FakeGowa:
        def __init__(self):
            self.calls = []

        def send_message(self, phone, text, mentions=None, reply_message_id=None):
            self.calls.append((phone, text, mentions, reply_message_id))
            return {"ok": True, "id": "sent-1"}

    class FakeHandler:
        def __init__(self):
            self.saved = []

        def save_assistant_message(self, phone, text, msg_id=None, status="sent"):
            message = {
                "role": "assistant", "content": text, "phone": phone,
                "msg_id": msg_id, "status": status,
            }
            self.saved.append(message)
            return message

    gowa = FakeGowa()
    handler = FakeHandler()
    set_runtime(None, None, gowa, handler)
    result = send_whatsapp_message(
        "5511999999999", "Mensagem pronta", mentions=["5511888888888"],
        reply_message_id="original-1",
    )
    assert result["ok"] is True and result["msg_id"] == "sent-1"
    assert result["sandbox"] is False
    assert gowa.calls == [(
        "5511999999999", "Mensagem pronta", ["5511888888888"], "original-1",
    )]
    assert handler.saved[0]["content"] == "Mensagem pronta"

    config_repo.set("plugin.helper_demo.notice", "Aviso configurado")
    assert get_plugin_setting("helper_demo", "notice", "padrão") == "Aviso configurado"
    assert get_plugin_setting("helper_demo", "missing", "padrão") == "padrão"

    for args in (("", "texto"), ("5511999999999", "")):
        try:
            send_whatsapp_message(*args)
        except ValueError:
            pass
        else:
            raise AssertionError("campos vazios devem ser rejeitados")
    set_runtime(None, None, None, None)


def test_reference_paths_include_installed_plugin_sources_but_not_runtime_data():
    plugin_manifest = tmp / "storages" / "plugins" / "help_demo" / "plugin.yaml"
    plugin_manifest.parent.mkdir(parents=True, exist_ok=True)
    plugin_manifest.write_text("id: help_demo\n", encoding="utf-8")
    assert _safe_reference_path(tmp, "storages/plugins/help_demo/plugin.yaml") == plugin_manifest.resolve()
    try:
        _safe_reference_path(tmp, "storages/whatsbot.db")
    except ValueError:
        pass
    else:
        raise AssertionError("runtime database must not be exposed to the help agent")


def test_help_state_inspection_excludes_secrets_and_personal_rows():
    from db.repositories import config_repo, plugin_repo

    config_repo.set("openrouter_api_key", "secret-api-key")
    config_repo.set("web_password_hash", "secret-password-hash")
    config_repo.set("auto_reply", True)
    settings_text = _inspect_system_state("settings")
    settings = json.loads(settings_text)
    assert settings["auto_reply"] is True
    assert settings["api_key_configured"] is True
    assert settings["panel_password_configured"] is True
    assert "secret-api-key" not in settings_text
    assert "secret-password-hash" not in settings_text
    assert "openrouter_api_key" not in settings
    assert "web_password_hash" not in settings

    plugin_repo.upsert("help_state_demo", "1.0.0", enabled=True)
    plugins = json.loads(_inspect_system_state("plugins"))
    assert any(row["id"] == "help_state_demo" and row["enabled"] for row in plugins)

    schema = json.loads(_inspect_system_state("schema"))
    assert schema["database"] == "sqlite"
    contacts = next(row for row in schema["tables"] if row["table"] == "contacts")
    assert "phone" in contacts["columns"]
    assert all("rows" not in table for table in schema["tables"])


def test_runtime_state_tool_is_only_added_to_system_help():
    help_tool_names = {tool.name for tool in reference_functions(ROOT, include_state=True)}
    plugin_tool_names = {tool.name for tool in reference_functions(ROOT)}
    assert "inspect_whatsbot_state" in help_tool_names
    assert "inspect_whatsbot_state" not in plugin_tool_names


def test_plugin_reference_search_supports_files_and_blocks_exact_repeats():
    import asyncio

    functions = {tool.name: tool for tool in reference_functions(ROOT, plugin_creator=True)}
    first = asyncio.run(functions["search_whatsbot"].entrypoint(
        "Criador de Plugins", "AGENTS.md", 5,
    ))
    assert "AGENTS.md:" in first
    repeated = asyncio.run(functions["search_whatsbot"].entrypoint(
        "Criador de Plugins", "AGENTS.md", 5,
    ))
    assert repeated.startswith("Error: esta consulta já foi executada")


def test_chat_stream_heartbeat_does_not_cancel_slow_agent_event():
    import asyncio

    completed = False

    async def slow_events():
        nonlocal completed
        await asyncio.sleep(0.03)
        completed = True
        yield "done"

    async def exercise():
        items = []
        async for item in _with_timeout(
            slow_events(), seconds=0.2, heartbeat_seconds=0.005,
        ):
            items.append(item)
        assert completed is True
        assert items[-1] == "done"
        assert items.count(_STREAM_HEARTBEAT) >= 2

    asyncio.run(exercise())


def test_system_help_adds_specific_link_when_model_omits_it():
    result = _ensure_system_help_link(
        "Como altero as instruções do agente?", "Abra as configurações do painel.",
        "http://localhost:8080",
    )
    assert result.endswith("[Abrir instruções do agente](http://localhost:8080/painel?aba=agente#prompt)")

    proxy_result = _ensure_system_help_link(
        "Onde configuro o proxy do WhatsApp?", "Abra o painel.",
        "http://localhost:8080",
    )
    assert proxy_result.endswith("[Abrir proxy do WhatsApp](http://localhost:8080/painel?aba=sistema#gowa-proxy)")


def test_system_help_panel_links_point_to_real_tabs_and_targets():
    knowledge = (ROOT / "agent" / "SYSTEM_HELP.md").read_text(encoding="utf-8")
    frontend = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "web/static/js/components/ConfigPanel.js",
            "web/static/js/components/DatabaseSettings.js",
            "web/static/js/components/GowaSettings.js",
            "web/static/js/components/GowaProxySettings.js",
        )
    )
    links = re.findall(r"\{\{base_url\}\}(/painel\?aba=([a-z-]+)(?:#([a-z-]+))?)", knowledge)
    assert links
    assert {slug for _, slug, _ in links} == {"agente", "modelos-midia", "sistema"}
    tab_ids = {"agente": "agent", "modelos-midia": "models", "sistema": "system"}
    for _, slug, anchor in links:
        assert f"slug: '{slug}'" in frontend
        if anchor:
            assert f'id="{anchor}"' in frontend
            key = rf"(?:'{re.escape(anchor)}'|{re.escape(anchor)}): '{tab_ids[slug]}'"
            assert re.search(key, frontend), f"target #{anchor} is not mapped to tab {slug}"


def test_chat_persistence():
    assert _is_plugin_intent("quero criar um plugin de pedidos") is True
    assert _is_plugin_intent("o que é um plugin?") is False
    formatted = _format_discovery_response(
        "Entendi! O que deve fazer? Como interage no WhatsApp? Precisa de tela? &#x20;"
    )
    assert "&#x20;" not in formatted
    assert formatted.count("\n\n") == 2
    system = chat_repo.ensure_system_project()
    assert chat_repo.ensure_system_project()["id"] == system["id"]
    conversation = chat_repo.create_conversation(system["id"], "provider/model", "low")
    first = chat_repo.add_message(conversation["id"], "user", "Como desativo documentos?")
    chat_repo.add_message(conversation["id"], "assistant", "Abra o Painel.")
    rows = chat_repo.list_messages(conversation["id"])
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[0]["id"] == first["id"]
    chat_repo.update_conversation(conversation["id"], summary="decisão preservada", compacted_through_id=first["id"])
    assert chat_repo.get_conversation(conversation["id"])["summary"] == "decisão preservada"


def test_project_order_and_soft_delete():
    first_workspace = _plugin_dir("ordered_first")
    second_workspace = _plugin_dir("ordered_second")
    first = chat_repo.create_project("Primeiro", "plugin", "ordered_first", str(first_workspace))
    second = chat_repo.create_project("Segundo", "plugin", "ordered_second", str(second_workspace))
    active_ids = [project["id"] for project in chat_repo.list_projects()]
    reordered = [second["id"], first["id"], *[item for item in active_ids if item not in {first["id"], second["id"]}]]
    chat_repo.reorder_projects(reordered)
    assert [project["id"] for project in chat_repo.list_projects()][:2] == [second["id"], first["id"]]
    chat_repo.update_project(first["id"], name="Primeiro renomeado")
    assert chat_repo.get_project(first["id"])["name"] == "Primeiro renomeado"
    chat_repo.soft_delete_project(first["id"])
    assert first["id"] not in {project["id"] for project in chat_repo.list_projects()}
    assert chat_repo.get_project(first["id"])["deleted_at"] is not None
    assert first_workspace.is_dir()


def test_validate_and_export_ready_workspace():
    workspace = _plugin_dir(
        "demo_chat",
        "CREATE TABLE plugin_demo_chat_items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);",
    )
    (workspace / "test_plugin.py").write_text(
        "def test_value():\n    from __init__ import VALUE\n    assert VALUE == 1\n",
        encoding="utf-8",
    )
    project = chat_repo.create_project("Demo", "plugin", "demo_chat", str(workspace))
    result = validate_workspace(project, save_version=True)
    assert result["valid"] is True
    assert result["plugin_id"] == "demo_chat"
    assert result["migrations"] == ["001_initial.sql"]
    assert result["tests"] == 1
    assert Path(result["zip_path"]).is_file()


def test_simple_test_runner_uses_a_fresh_database_each_time():
    workspace = _plugin_dir(
        "isolated_tests",
        "CREATE TABLE plugin_isolated_tests_items (id INTEGER PRIMARY KEY AUTOINCREMENT);",
    )
    test_file = workspace / "test_isolation.py"
    test_file.write_text(
        "from sqlalchemy import text\n"
        "from plugins.context import make_plugin_db\n\n"
        "def test_clean_database():\n"
        "    with make_plugin_db() as conn:\n"
        "        conn.execute(text('INSERT INTO plugin_isolated_tests_items DEFAULT VALUES'))\n"
        "        total = conn.execute(text('SELECT COUNT(*) FROM plugin_isolated_tests_items')).scalar_one()\n"
        "    assert total == 1\n",
        encoding="utf-8",
    )
    first_failures, first_output = _run_simple_tests(workspace, [test_file])
    second_failures, second_output = _run_simple_tests(workspace, [test_file])
    assert first_failures == second_failures == 0
    assert "PASS test_isolation.py:test_clean_database" in first_output
    assert "PASS test_isolation.py:test_clean_database" in second_output


def test_rejects_destructive_migration():
    workspace = _plugin_dir("unsafe_chat", "DROP TABLE plugin_unsafe_chat_items;")
    project = chat_repo.create_project("Unsafe", "plugin", "unsafe_chat", str(workspace))
    try:
        validate_workspace(project, save_version=False)
    except ValueError as exc:
        assert "destrutiva" in str(exc)
        return
    raise AssertionError("destructive migration should have been rejected")


def test_workspace_path_cannot_escape():
    workspace = _plugin_dir("safe_chat")
    project = chat_repo.create_project("Safe", "plugin", "safe_chat", str(workspace))
    try:
        _safe_workspace_file(project, "../outside.txt")
    except ValueError:
        return
    raise AssertionError("path traversal should have been rejected")


def test_discovery_agent_has_no_tools():
    from agent.plugin_chat import build_agent
    agent = build_agent(
        api_key="fake", model_id="provider/model", reasoning="",
        project_kind="discovery", workspace=None, project_root=ROOT,
    )
    assert not agent.tools
    assert "sem conhecimento técnico" in agent.system_message

    plugin_agent = build_agent(
        api_key="fake", model_id="provider/model", reasoning="",
        project_kind="plugin", workspace=tmp / "unlimited_tools", project_root=ROOT,
    )
    assert plugin_agent.tool_call_limit is None
    assert plugin_agent.model.extra_body == {"reasoning": {"effort": "low"}}
    assert "Não encerre dizendo que vai começar" in plugin_agent.system_message
    assert "REFERÊNCIA OFICIAL DE PLUGINS" in plugin_agent.system_message
    assert "ID obrigatório do plugin: unlimited_tools" in plugin_agent.system_message
    tool_names = {getattr(tool, "name", "") for tool in plugin_agent.tools}
    assert not {"list_whatsbot_files", "read_whatsbot_file", "search_whatsbot"} & tool_names
    assert {"run_command", "write_file", "read_file", "list_files"} <= tool_names

    (tmp / "unlimited_tools" / "plugin.yaml").write_text(
        "id: unlimited_tools\nname: Existing\nversion: 1.0.0\n", encoding="utf-8",
    )
    update_agent = build_agent(
        api_key="fake", model_id="provider/model", reasoning="",
        project_kind="plugin", workspace=tmp / "unlimited_tools", project_root=ROOT,
    )
    update_names = {getattr(tool, "name", "") for tool in update_agent.tools}
    assert {"list_whatsbot_files", "read_whatsbot_file", "search_whatsbot"} <= update_names

    high_reasoning_agent = build_agent(
        api_key="fake", model_id="provider/model", reasoning="high",
        project_kind="plugin", workspace=tmp / "unlimited_tools", project_root=ROOT,
    )
    assert high_reasoning_agent.model.extra_body == {"reasoning": {"effort": "high"}}

    deepseek_agent = build_agent(
        api_key="fake", model_id="deepseek/deepseek-v4.1-flash", reasoning="",
        project_kind="plugin", workspace=tmp / "unlimited_tools", project_root=ROOT,
    )
    assert deepseek_agent.model.extra_body == {"reasoning": {"effort": "none"}}


def test_chat_cost_estimate_uses_cache_price_and_running_total():
    from datetime import datetime, timezone
    from server.routes.chat import _add_conversation_cost_totals, _add_cost_estimate, _current_model_pricing

    pricing = {
        "prompt": "0.00000015", "completion": "0.0000006",
        "input_cache_read": "0.000000003",
        "overrides": [{
            "utc_days": ["wednesday"], "utc_start": 100, "utc_end": 400,
            "prompt": "0.0000003", "completion": "0.0000012",
            "input_cache_read": "0.000000006",
        }],
    }
    active = _current_model_pricing(pricing, datetime(2026, 9, 16, 2, 0, tzinfo=timezone.utc))
    assert float(active["prompt"]) == 0.0000003
    metrics = _add_cost_estimate({
        "input_tokens": 681, "output_tokens": 84, "total_tokens": 765,
        "cache_read_tokens": 531, "cache_write_tokens": 0,
    }, "deepseek/deepseek-v4.1-flash", pricing)
    expected = 150 * 0.00000015 + 531 * 0.000000003 + 84 * 0.0000006
    assert abs(metrics["estimated_cost_usd"] - expected) < 1e-12
    rows = _add_conversation_cost_totals([
        {"kind": "metrics", "metadata": metrics},
        {"kind": "metrics", "metadata": {"input_tokens": 10, "output_tokens": 2}},
    ], "deepseek/deepseek-v4.1-flash", pricing)
    assert rows[-1]["metadata"]["conversation_estimated_cost_usd"] > expected


def test_chat_api_and_streaming_without_external_services():
    import asyncio
    from fastapi import FastAPI
    import server.routes.chat as chat_routes

    class Settings(dict):
        data_dir = tmp

    class FakeMetrics:
        input_tokens = 12
        output_tokens = 3
        total_tokens = 15
        cache_read_tokens = 4
        cache_write_tokens = 0
        reasoning_tokens = 0

    class FakeAgent:
        def arun(self, **kwargs):
            async def events():
                yield SimpleNamespace(event="ToolCallStarted", tool=SimpleNamespace(
                    tool_call_id="tool-1", tool_name="read_file", tool_args={"path": "plugin.yaml"},
                    result=None, tool_call_error=False,
                ))
                yield SimpleNamespace(event="ToolCallCompleted", tool=SimpleNamespace(
                    tool_call_id="tool-1", tool_name="read_file", tool_args={"path": "plugin.yaml"},
                    result="conteúdo", tool_call_error=False,
                ))
                yield SimpleNamespace(event="RunContent", content="Resposta de teste.")
                yield SimpleNamespace(event="RunCompleted", metrics=FakeMetrics())
            return events()

        async def acancel_run(self, _run_id):
            return True

    class FakeRequest:
        async def is_disconnected(self):
            return False

    class FakeUpload:
        filename = "voice.ogg"

        async def read(self, _limit):
            return b"OggS-test-audio"

    class FakeHandler:
        transcribed_path = ""

        def transcribe_audio(self, path, phone):
            self.transcribed_path = path
            assert phone == ""
            assert Path(path).read_bytes() == b"OggS-test-audio"
            return "crie um plugin de pedidos"

    original_builder = chat_routes.build_agent
    original_to_thread = asyncio.to_thread
    original_restart = chat_routes.schedule_restart
    original_pricing = chat_routes._get_model_pricing_details
    async def inline_to_thread(function, *args, **kwargs):
        return function(*args, **kwargs)
    build_calls = []
    def fake_builder(**kwargs):
        build_calls.append(kwargs)
        return FakeAgent()
    chat_routes.build_agent = fake_builder
    chat_routes.schedule_restart = lambda **kwargs: None
    chat_routes._get_model_pricing_details = lambda *_args, **_kwargs: {
        "prompt": "0.000001", "completion": "0.000002", "input_cache_read": "0.0000001",
    }
    asyncio.to_thread = inline_to_thread
    try:
        app = FastAPI()
        plugins_dir = tmp / "installed_plugins"
        plugins_dir.mkdir(exist_ok=True)
        fake_handler = FakeHandler()
        deps = SimpleNamespace(
            settings=Settings(openrouter_api_key="fake", model="provider/model"),
            plugins_dir=plugins_dir,
            agent_handler=fake_handler,
        )
        chat_routes.register_routes(app, deps)

        def endpoint(path, method):
            return next(
                route.endpoint for route in app.routes
                if getattr(route, "path", None) == path and method in getattr(route, "methods", set())
            )

        async def exercise():
            boot = await endpoint("/api/chat", "GET")()
            assert boot["ok"] is True
            system = next(p for p in boot["data"]["projects"] if p["kind"] == "system")
            created_response = await endpoint(
                "/api/chat/projects/{project_id}/conversations", "POST"
            )(system["id"], {"model": "provider/model"})
            created = created_response["data"]
            transcribed = await endpoint(
                "/api/chat/conversations/{conversation_id}/transcribe-audio", "POST"
            )(created["id"], FakeUpload())
            assert transcribed["data"]["transcription"] == "crie um plugin de pedidos"
            assert fake_handler.transcribed_path
            assert not Path(fake_handler.transcribed_path).exists()
            response = await endpoint(
                "/api/chat/conversations/{conversation_id}/messages", "POST"
            )(created["id"], {"content": "Como configuro documentos?"}, FakeRequest())
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
            events = [json.loads(line) for line in "".join(chunks).splitlines() if line]
            assert any(e["event"] == "content" for e in events)
            completed = next(e for e in events if e["event"] == "run_completed")
            assert completed["data"]["metrics"]["cache_read_tokens"] == 4
            metric_events = [e for e in events if e["event"] == "metrics"]
            assert len(metric_events) == 1
            assert events.index(metric_events[0]) > next(
                index for index, event in enumerate(events) if event["event"] == "message_saved"
            )
            saved = chat_repo.list_messages(created["id"])
            normal = [m for m in saved if m["kind"] == "message"]
            assert [m["role"] for m in normal[-2:]] == ["user", "assistant"]
            assert saved[-1]["kind"] == "metrics"
            assert saved[-1]["metadata"]["estimated_cost_usd"] > 0
            assert saved[-1]["metadata"]["conversation_estimated_cost_usd"] > 0
            actions = [m for m in saved if m["kind"] == "action"]
            assert len(actions) == 1
            assert actions[0]["metadata"]["status"] == "completed"

            redirected_response = await endpoint(
                "/api/chat/projects/{project_id}/conversations", "POST"
            )(system["id"], {"model": "provider/model"})
            redirected_id = redirected_response["data"]["id"]
            calls_before_redirect = len(build_calls)
            redirected_stream = await endpoint(
                "/api/chat/conversations/{conversation_id}/messages", "POST"
            )(redirected_id, {"content": "Quero criar um plugin de pedidos"}, FakeRequest())
            redirected_chunks = []
            async for chunk in redirected_stream.body_iterator:
                redirected_chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
            redirected_events = [json.loads(line) for line in "".join(redirected_chunks).splitlines() if line]
            assert len(build_calls) == calls_before_redirect
            redirect_message = next(event for event in redirected_events if event["event"] == "message_saved")
            assert "botão **+**" in redirect_message["data"]["content"]

            renamed = await endpoint(
                "/api/chat/conversations/{conversation_id}", "PUT"
            )(created["id"], {"title": "Conversa renomeada"})
            assert renamed["data"]["title"] == "Conversa renomeada"
            removed = await endpoint(
                "/api/chat/conversations/{conversation_id}", "DELETE"
            )(created["id"])
            assert removed["data"]["deleted"] is True
            assert chat_repo.get_conversation(created["id"]) is None

            # Exercise the complete confirmed install + update path. The
            # existing DB row must survive the second version's migration.
            from db.engine import get_engine
            from sqlalchemy import text as sa_text

            workspace = _plugin_dir(
                "install_chat",
                "CREATE TABLE plugin_install_chat_items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);",
            )
            install_project = chat_repo.create_project(
                "Install Chat", "plugin", "install_chat", str(workspace)
            )
            install_endpoint = endpoint("/api/chat/projects/{project_id}/install", "POST")
            first_install = await install_endpoint(install_project["id"], {})
            assert first_install["ok"] is True and first_install["data"]["updated"] is False
            with get_engine().begin() as connection:
                connection.execute(sa_text(
                    "INSERT INTO plugin_install_chat_items (name) VALUES ('preservar')"
                ))
            manifest = workspace / "plugin.yaml"
            manifest.write_text(manifest.read_text().replace("1.0.0", "1.1.0"), encoding="utf-8")
            (workspace / "migrations" / "002_note.sql").write_text(
                "ALTER TABLE plugin_install_chat_items ADD COLUMN note TEXT;", encoding="utf-8"
            )
            second_install = await install_endpoint(install_project["id"], {})
            assert second_install["ok"] is True and second_install["data"]["updated"] is True
            with get_engine().connect() as connection:
                rows = connection.execute(sa_text(
                    "SELECT name, note FROM plugin_install_chat_items"
                )).all()
            assert rows == [("preservar", None)]

        asyncio.run(exercise())
    finally:
        chat_routes.build_agent = original_builder
        chat_routes.schedule_restart = original_restart
        chat_routes._get_model_pricing_details = original_pricing
        asyncio.to_thread = original_to_thread


def main():
    tests = [
        test_system_help_prompt_uses_current_panel_origin,
        test_system_help_knowledge_is_reloaded_and_has_safe_fallback,
        test_plugin_creator_guide_is_loaded_into_the_prompt,
        test_frontend_quality_rejects_old_narrow_screen_and_accepts_responsive_screen,
        test_new_plugin_scaffold_forces_real_implementation,
        test_workspace_tools_expose_real_schemas_to_inexpensive_models,
        test_public_plugin_runtime_helpers_send_messages_and_read_settings,
        test_reference_paths_include_installed_plugin_sources_but_not_runtime_data,
        test_help_state_inspection_excludes_secrets_and_personal_rows,
        test_runtime_state_tool_is_only_added_to_system_help,
        test_plugin_reference_search_supports_files_and_blocks_exact_repeats,
        test_chat_stream_heartbeat_does_not_cancel_slow_agent_event,
        test_system_help_adds_specific_link_when_model_omits_it,
        test_chat_persistence,
        test_project_order_and_soft_delete,
        test_validate_and_export_ready_workspace,
        test_simple_test_runner_uses_a_fresh_database_each_time,
        test_rejects_destructive_migration,
        test_workspace_path_cannot_escape,
        test_discovery_agent_has_no_tools,
        test_chat_cost_estimate_uses_cache_price_and_running_total,
        test_chat_api_and_streaming_without_external_services,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  ok   {test.__name__}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passaram")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
