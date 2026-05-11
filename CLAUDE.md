# WhatsBot

Bot de WhatsApp com IA para usuários finais, distribuído como EXE Windows.

## Stack

- **Python 3.11+** — linguagem principal
- **SQLAlchemy 2.0 Core + Alembic** — camada de dados portável (Core, sem ORM declarativo)
- **SQLite** — banco default (WAL mode, driver `sqlite3` da stdlib)
- **PostgreSQL** — backend opcional via `psycopg[binary]`, configurável pela tela Settings → Banco
- **GOWA** (go-whatsapp-web-multidevice v8.5.0) — bridge WhatsApp via REST, roda como subprocess
- **OpenRouter** — LLM provider (API compatível com OpenAI)
- **FastAPI + uvicorn** — backend web (REST API + WebSocket)
- **Preact + HTM + Tailwind CSS** — frontend web (sem build step, vendorizado local)
- **PyInstaller** — empacotamento como EXE

## Arquitetura

```
main.py              → entry point, inicia uvicorn + abre browser
server/app.py        → FastAPI app (endpoints REST, WebSocket, webhook, background tasks)
gowa/manager.py      → lifecycle do subprocess GOWA (start/stop/watchdog)
gowa/client.py       → HTTP client para REST API do GOWA (localhost:3000)
agent/handler.py     → processa mensagens com LLM via OpenRouter (tool calling)
agent/memory.py      → ContactMemory e TagRegistry (leitura/escrita no SQLite via repos)
agent/tools/         → tools core do LLM (uma tool por arquivo, agregadas em CORE_TOOLS)
config/settings.py   → load/save config na tabela `config` do SQLite
db/                  → módulo de banco de dados (SQLAlchemy 2.0 Core)
  engine.py          → factory do Engine, URL resolution (env > arquivo > sqlite default), PRAGMAs SQLite
  tables.py          → MetaData + 11 Table objects (Core, sem mapper/Session)
  upsert.py          → helper dialect-agnóstico (INSERT ... ON CONFLICT)
  connection.py      → init_db(): cria engine + roda Alembic upgrade
  migration_postgres.py → migra dados SQLite → Postgres (usado pelo endpoint admin)
  migrate_json.py    → migração one-time de JSON legado → banco
  alembic/           → migrations Alembic (env.py + versions/)
  repositories/      → data access layer (um arquivo por domínio)
    config_repo.py   → get_all(), get(), set(), set_many(), delete_prefix()
    contact_repo.py  → get_or_create(), update(), list_contacts(), get_full_contact()
    message_repo.py  → add(), get_all(), get_context(), get_last(), delete_all()
    usage_repo.py    → add(), global_summary(), by_contact(), detail()
    tag_repo.py      → get_all(), create(), update(), delete(), set_contact_tags()
    plugin_repo.py   → list_all(), upsert(), set_enabled(), applied_migrations()
plugins/             → sistema de plugins (core, não confundir com storages/plugins)
  loader.py          → PluginRegistry, descoberta + importlib + bootstrap
  manifest.py        → parser plugin.yaml + validação semver
  migrator.py        → runner SQL com prefixo plugin_<id>_ obrigatório
  context.py         → ToolContext, PromptContext (passados aos plugins)
  restart.py         → schedule_restart() — touch sentinela + os._exit
assets/              → recursos não-código (templates copiados em runtime)
  plugin_examples/   → plugins de referência (copiados pra storages/plugins/ no 1º boot)
storages/plugins/    → user-writable, ignorado por .gitignore (preservado em updates)
web/index.html       → entry point do frontend (HTML + import map)
web/static/js/       → componentes Preact + HTM (sem build step)
web/static/vendor/   → libs JS vendorizadas (preact, htm, tailwind)
bin/gowa.exe         → binário GOWA pré-compilado (não editar)
```

## Comandos

```bash
# Dev (Windows)
run_dev.bat

# Build EXE
build.bat

# Instalar deps manualmente
pip install -r requirements.txt
python main.py
```

## Banco de dados

A camada de dados usa **SQLAlchemy 2.0 Core** (sem ORM declarativo). Cada tabela é um `Table` em [db/tables.py](db/tables.py) e os repositórios constroem statements via `select()/insert()/update()/delete()`. Repos rodam síncronos e são chamados das rotas via `asyncio.to_thread`.

### Escolha do backend

A URL é resolvida na ordem:

1. Variável de ambiente `DATABASE_URL` (cobre Docker/Coolify — `.env`).
2. Arquivo local `storages/database.json` (Windows / EXE — gerenciado pela UI).
3. Fallback: `sqlite:///storages/whatsbot.db`.

Para trocar para Postgres no Windows: Settings → Banco → cola a URL `postgresql+psycopg://user:senha@host:5432/whatsbot` → "Migrar agora". O endpoint `POST /api/admin/migrate-to-postgres` recebe a URL, valida que o destino está vazio, aplica Alembic, copia tabela a tabela (incluindo `plugin_*`), grava em `database.json` e dispara restart. SQLite original fica preservado para rollback (basta apagar/editar `database.json` e reiniciar).

Para Docker: setar `DATABASE_URL` no `.env` antes de subir o container — o arquivo `database.json` é ignorado quando a env está presente.

**Docker Swarm com múltiplas réplicas (ou rolling update entre tasks): `DATABASE_URL` apontando para Postgres compartilhado é obrigatório.** Volumes nomeados em Swarm são locais por nó, não compartilhados entre réplicas — SQLite local resulta em DBs divergentes (escritas vão pra uma réplica, leituras vêm de outra). Coolify e single-container não sofrem disso.

### Tabelas

| Tabela | Descrição |
|--------|-----------|
| `config` | Configurações do app (key-value, valores JSON-encoded). Configs de plugin usam prefixo `plugin.<id>.` |
| `contacts` | Contatos/grupos (phone, name, email, profissão, empresa, flags) |
| `observations` | Notas/observações por contato (texto livre) |
| `messages` | Histórico completo de mensagens (role, content, ts, media) |
| `usage` | Registros de uso da API (tokens, custo, modelo) |
| `tags` | Tags globais (name, color) |
| `contact_tags` | Relação N:N contato ↔ tag |
| `unread_msg_ids` | IDs de mensagens não lidas por contato |
| `executions` | Tracking de execuções (webhook → resposta) |
| `execution_steps` | Passos de cada execução (tool calls, llm_request, etc.) |
| `plugins` | Plugins descobertos no filesystem (id, version, enabled, load_error) |
| `plugin_migrations` | Versões de SQL migrations já aplicadas, por plugin |
| `plugin_<id>_*` | Tabelas criadas por plugins via suas migrations (prefixo obrigatório) |
| `tool_overrides` | Override por-tool (enabled, description, display_label). Row criada automaticamente para cada tool registrada (core + plugin) |

### Configuração SQLite

Quando o engine é SQLite (default), as PRAGMAs são aplicadas via `event.listens_for("connect")` em [db/engine.py](db/engine.py):

- `PRAGMA journal_mode=WAL` — permite leituras concorrentes
- `PRAGMA foreign_keys=ON` — integridade referencial
- `PRAGMA busy_timeout=5000` — espera até 5s em lock contention
- `connect_args={"check_same_thread": False}` — reuso entre threads compatível com `asyncio.to_thread`

Em Postgres essas pragmas não se aplicam (são SQLite-only); o engine usa `pool_pre_ping=True` para sobreviver a quedas idle de conexão.

### Padrão de acesso

Repos usam o padrão dialect-agnóstico baseado em `Table` objects:

```python
from sqlalchemy import select
from db.engine import get_engine
from db.tables import contacts

def get_by_phone(phone: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(contacts).where(contacts.c.phone == phone)
        ).mappings().first()
    return dict(row) if row else None
```

Regras:

- Leitura: `with get_engine().connect() as conn:` (sem transação implícita).
- Escrita: `with get_engine().begin() as conn:` (auto-commit no exit, rollback em exceção).
- UPSERT: usar `db.upsert.upsert()` / `db.upsert.upsert_ignore()` — escolhe `sqlite.insert()` ou `postgresql.insert()` automaticamente.
- Nunca usar `?` ou `%s` direto — bind params nomeados (`:phone`) via `sqlalchemy.text()` ou expressões Core.
- Migrations: Alembic ([db/alembic/versions](db/alembic/versions)). Para um schema change, rode `alembic revision --autogenerate -m "msg"` e revise. `init_db()` aplica `alembic upgrade head` no boot; DBs legados sem `alembic_version` são automaticamente stampados em `0001_baseline` antes do upgrade.

`db.connection.get_db()` ainda existe como shim deprecated retornando `engine.raw_connection()`, mas é apenas para plugins de terceiros não migrados. Código novo (core ou plugin oficial) usa `get_engine()`.

## Fluxo de mensagens (webhook)

Mensagens recebidas no WhatsApp são entregues em tempo real via webhook do GOWA:

1. GOWA inicia com `--webhook http://127.0.0.1:{web_port}/api/webhook`
2. Mensagem chega → GOWA faz POST em `/api/webhook` com payload contendo `body`, `from`, `id`, `is_from_me`
3. Webhook acumula mensagens do mesmo contato por `message_batch_delay` segundos (padrão: 3s) — se o contato enviar várias mensagens em sequência, são juntadas em uma só
4. Após o delay, `_process_batch()` junta os textos com `\n` e chama `agent_handler.process_message()`
5. O AgentHandler faz a chamada ao LLM com tool calling — se o LLM detectar dados pessoais (nome, email, profissão, empresa), chama `save_contact_info` automaticamente
6. Resposta é enviada via `gowa_client.send_message()`

**NÃO usa polling** — o auto-reply por polling foi removido. Toda recepção de mensagens é via webhook.

## Memória por contato

Cada contato é armazenado na tabela `contacts` com campos normalizados:

- **Info** (name, email, profession, company, address) — colunas diretas na tabela `contacts`
- **Observações** — tabela `observations` (uma linha por observação)
- **Mensagens** — tabela `messages` com colunas `role`, `content`, `ts`, `media_type`, `media_path`, `status`, `msg_id`
- **Usage** — tabela `usage` com tokens, custo e modelo por chamada
- **Tags** — relação N:N via `contact_tags`

`ContactMemory` em `agent/memory.py` é o wrapper que encapsula o acesso via repos. Mensagens são lazy-loaded do DB (não mantidas em memória). Apenas as últimas N (configurável) são enviadas ao LLM.

Info é salva automaticamente via tool calling do LLM e injetada no system prompt. Histórico persiste entre reinícios do app.

## API REST do WhatsBot (backend FastAPI)

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| GET | `/` | Serve o frontend (web/index.html) |
| GET | `/api/config` | Retorna config (API key mascarada) |
| PUT | `/api/config` | Salva config + atualiza AgentHandler |
| POST | `/api/config/test-key` | Testa API key OpenRouter |
| GET | `/api/status` | Status de conexão + contagem de msgs |
| GET | `/api/qr` | QR code como PNG (204 se indisponível) |
| POST | `/api/whatsapp/reconnect` | Reconectar GOWA |
| POST | `/api/whatsapp/logout` | Logout GOWA |
| POST | `/api/webhook` | Recebe mensagens do GOWA (webhook) |
| GET | `/api/contacts?archived=true` | Lista apenas contatos/grupos arquivados |
| GET | `/api/webhook-payloads?limit=N` | Últimos N payloads raw do webhook (debug, max 50) |
| GET | `/api/gowa-logs?limit=N` | Tail do `logs/gowa.log` (stdout/stderr do subprocess GOWA, só populado com `WHATSBOT_GOWA_DEBUG=1`) |
| GET | `/api/tools` | Lista todas as tools registradas (core + plugin) com estado de override |
| PUT | `/api/tools/{name}` | Atualiza override `{enabled?, description?, display_label?}`; `description=null` reseta |
| GET | `/api/plugins` | Lista todos os plugins descobertos com status (ativo/inativo/erro) |
| GET | `/api/plugins/manifest` | Manifest público dos plugins ativos (pro frontend dinâmico) |
| POST | `/api/plugins/{id}/enable` | Ativa o plugin e dispara restart |
| POST | `/api/plugins/{id}/disable` | Desativa o plugin e dispara restart |
| GET/PUT | `/api/plugins/{id}/settings` | Schema Pydantic + values do plugin (settings declarativas) |
| GET | `/api/plugins/{id}/export` | Baixa o plugin como `.zip` |
| POST | `/api/plugins/import` | Importa um plugin via upload de `.zip` |
| DELETE | `/api/plugins/{id}` | Remove a pasta + tabelas `plugin_<id>_*` + settings namespaceadas |
| POST | `/api/plugins/restart` | Restart manual do servidor |
| `*` | `/api/plugins/{id}/*` | Endpoints REST mountados pelo plugin (router próprio) |
| GET | `/api/admin/database` | Info do backend atual (dialect, URL redacted, caminho do config) |
| POST | `/api/admin/migrate-to-postgres` | Inicia migração SQLite → Postgres. Body: `{postgres_url}`. Status via WS `db_migration_progress` |
| GET | `/api/admin/migrate-to-postgres/status` | Snapshot polling do estado da migração |
| WS | `/ws` | WebSocket para eventos real-time |

Formato de resposta REST: `{"ok": bool, "data": ..., "error": ...}`

Eventos WebSocket: `{"event": "status|qr_update|gowa_status|config_saved", "data": {...}}`

## GOWA REST API (endpoints reais — v8.5.0 multi-device)

IMPORTANTE: O GOWA v8.5.0 é multi-device. Antes de usar qualquer endpoint, é necessário criar um device via `POST /devices`. Após criação, todas as requests (exceto `/devices`) exigem header `X-Device-Id`.

| Operação | Método | Endpoint | Notas |
|---|---|---|---|
| Listar devices | GET | `/devices` | Sem header obrigatório |
| Criar device | POST | `/devices` body: `{device_id?}` | Sem header, retorna device_id |
| Login/QR | GET | `/app/login` | Retorna JSON com `results.qr_link` (URL do PNG) |
| Status | GET | `/app/status` | Retorna `results.is_connected`, `results.is_logged_in` |
| Logout | GET | `/app/logout` | |
| Reconectar | GET | `/app/reconnect` | |
| Enviar msg | POST | `/send/message` body: `{phone, message}` | |
| Listar chats | GET | `/chats?limit=N` | Resposta aninhada: `results.data[]` |
| Msgs do chat | GET | `/chat/{jid}/messages?limit=N` | Resposta aninhada: `results.data[]` |

Binário iniciado com: `gowa.exe rest --port 3000 --webhook http://127.0.0.1:{web_port}/api/webhook`

Campos do payload do webhook GOWA: `body`, `from`, `sender_jid`, `chat_id`, `id`, `is_from_me`, `timestamp`, `from_name`

## Convenções de código

- Python com type hints nas assinaturas de função
- Logging via `logging` stdlib (nunca print)
- Operações bloqueantes (GOWA, OpenRouter, SQLite) usam `asyncio.to_thread()` no backend FastAPI
- Nomes de variáveis e comentários em inglês; textos exibidos ao usuário em português BR
- Tratar respostas da API GOWA com fallback para nomes de campo alternativos (a API não é 100% consistente nos nomes)
- Frontend: ES modules, componentes Preact em PascalCase, services/hooks em camelCase
- **Tools do LLM (core)**: criar em `agent/tools/<name>.py` com (a) o schema dict (`<NAME>_TOOL = {"type": "function", ...}`) e (b) função `execute(ctx, args) -> str | None`. Adicionar a tupla `(SCHEMA, execute)` em `CORE_TOOLS` em `agent/tools/__init__.py`. O dispatch é genérico via registry em `AgentHandler` — nunca adicionar `if/elif` por nome de tool
- **Tools de plugin**: viver em `storages/plugins/<id>/tools.py` no formato `CORE_TOOLS = [(schema, executor), ...]` e ser declaradas no manifest. NÃO mexer em `agent/tools/` ou no handler
- **Contrato de tool (core OU plugin)**: toda tool registrada vira row em `tool_overrides` automaticamente (via `tool_override_repo.ensure` no `_register_tool`). O usuário pode customizar `description` e `display_label` na tela `/tools`. O `name` da tool é IDENTIDADE e NÃO deve ser renomeado depois de release — quebra histórico de `usage` (`call_type=<name>`) e overrides do usuário. Description em código é o **default**: escreva como instrução clara pro LLM, deve funcionar sem customização. O schema também aceita `"display_label": "..."` no dict raiz (fora de `function`) — o handler retira antes de mandar pro LLM, e o valor vira o default mostrado na UI
- **Acesso a dados**: sempre via SQLAlchemy Core. Repos em `db/repositories/` usam `with get_engine().begin() as conn:` + statements de `db/tables`. Nunca usar `sqlite3` diretamente. Plugins acessam o banco via `from plugins.context import make_plugin_db` + `from sqlalchemy import text`

## Dados do projeto

Tudo salvo na pasta raiz do projeto (dev) ou junto ao EXE (PyInstaller):
- `storages/whatsbot.db` — banco SQLite (default; configs, contatos, mensagens, usage, tags)
- `storages/database.json` — override do backend (`{"url": "postgresql+psycopg://..."}`); ausente = SQLite
- `storages/` — dados do GOWA (sessão WhatsApp) + banco de dados da aplicação
- `logs/` — logs com rotação
- `statics/senditems/` — mídia enviada pelo operador
- **Webhook payloads (debug)**: últimos 50 payloads raw do GOWA em memória, acessíveis via `GET /api/webhook-payloads`
- **Contatos arquivados**: ao receber mensagem de um contato, o webhook consulta `gowa_client.is_chat_archived(jid)` e persiste `is_archived` na tabela `contacts`. A sidebar filtra por `?archived=true/false`. O status de archive é atualizado on-demand (não por polling)

## Sistema de plugins

Plugins são extensões opcionais isoladas em `storages/plugins/<id>/` (volume Docker / pasta separada no Windows, ignorada por updates). Um plugin pode agregar:

- **Tools** para o agente LLM (registradas no mesmo registry das tools core)
- **Prompt fragments** injetados dinamicamente no system prompt
- **Endpoints REST** sob `/api/plugins/<id>/...`
- **Tela Preact** carregada via `import()` ES dinâmico
- **Migrations SQL** com prefixo `plugin_<id>_` obrigatório
- **Settings declarativas** via Pydantic (form auto-gerado pela UI)
- **Broadcast WebSocket** via `from plugins.context import broadcast; broadcast("evento", {...})` — thread-safe, fire-and-forget, ws_manager + loop são injetados no startup do server. Use pra empurrar atualizações em tempo real à tela do plugin (a tela escuta `/ws` e filtra pelo nome do evento).

### Layout de um plugin

```
storages/plugins/<id>/
├── plugin.yaml              # manifest (id, name, version, whatsbot_api_version, entry, screens)
├── __init__.py
├── tools.py                 # CORE_TOOLS = [(schema, executor), ...]   (opcional)
├── prompts.py               # PROMPT_FRAGMENTS = [callable, ...]        (opcional)
├── routes.py                # router = APIRouter()                       (opcional)
├── settings.py              # class Settings(BaseModel) — Pydantic       (opcional)
├── migrations/
│   └── 001_initial.sql      # tabelas com prefixo plugin_<id>_
└── static/
    └── <id>.js              # default-export componente Preact
```

### Lifecycle

1. **Bootstrap**: na 1ª execução, `plugins.loader.bootstrap_initial_plugins()` copia `assets/plugin_examples/*` para `storages/plugins/` se a pasta estiver vazia (Windows e Docker).
2. **Discovery**: `discover_and_load(plugins_dir)` escaneia o filesystem, parseia cada manifest, faz `upsert` na tabela `plugins`.
3. **Migrations**: para plugins com `enabled=1`, `run_pending_migrations` aplica SQL files em ordem numérica. Naming `NNN_descricao.sql`. O migrator valida regex que toda `CREATE TABLE`/`ALTER TABLE`/`CREATE INDEX` use prefixo `plugin_<id>_`.
4. **Import**: `importlib.spec_from_file_location` registra o pacote como `whatsbot_plugins.<id>`. Submódulos declarados no `entry:` são importados sob demanda.
5. **Wiring**: `agent_handler.register_plugin_tools/prompts` adicionam ao registry. `app.include_router` monta o router em `/api/plugins/<id>`. `app.mount` serve `static/` em `/plugins/<id>/static`. `screens[].path` é registrado como rota SPA dinâmica.
6. **Toggle**: enable/disable atualiza a tabela `plugins` e dispara `schedule_restart` (`os._exit(0)` após delay; supervisor relança — Coolify/Docker `restart: unless-stopped` ou launcher do EXE).

### Settings declarativas (Pydantic Valves)

Plugin declara `class Settings(BaseModel)` em `settings.py`. O endpoint `GET /api/plugins/<id>/settings` retorna `model_json_schema()` + valores atuais; `PUT` valida via Pydantic e persiste em `config_repo` com prefixo `plugin.<id>.<field>`. Frontend (`PluginSettingsForm.js`) renderiza form genérico para string/int/float/bool/enum.

### Frontend dinâmico

`/api/plugins/manifest` retorna apenas plugins carregados com seus `screens[]`. `app.js` faz fetch no boot, popula `pluginScreens`, mostra entradas no `GearMenu` e renderiza via `PluginScreen` que faz `import(screen.component)` dinâmico. Plugin component recebe `apiBase = "/api/plugins/<id>"` como prop. Importmap em `web/index.html` cobre `preact`, `preact/hooks`, `htm` — plugin usa os mesmos sem bundle.

### Convenções obrigatórias

- **`id`**: snake_case, regex `^[a-z][a-z0-9_]{0,31}$`. Vira o prefixo de tabela e o nome do pacote Python.
- **Tabelas**: SEMPRE `plugin_<id>_<nome>`. O migrator rejeita o contrário com erro claro.
- **`whatsbot_api_version`**: range semver no manifest (ex: `">=1.0,<2.0"`). Versão atual em `plugins/manifest.WHATSBOT_API_VERSION`.
- **Permissions**: declaradas no manifest mas **não enforced no MVP** — informativo apenas.
- **Settings**: chaves persistem com prefixo `plugin.<id>.`. Plugin nunca grava direto na tabela `config` sem esse prefixo.

### Events e Filters (bus do plugin)

Plugins podem reagir a tudo que acontece no WhatsBot e modificar dados em trânsito sem editar o core. Dois mecanismos complementares (padrão WordPress: actions + filters; referências validadas em Baileys / WAHA / Home Assistant):

- **Events** — broadcast fire-and-forget, paralelo. Plugin exporta `EVENT_HANDLERS` em `<plugin>/events.py` e declara `entry.events: events` no manifest. Não bloqueia o pipeline principal; exceção em um handler nunca afeta outros.
- **Filters** — interceptive, síncrono no pipeline. Plugin exporta `FILTERS` em `<plugin>/filters.py` e declara `entry.filters: filters` no manifest. Recebe `(ctx, value)` e retorna valor modificado ou `None` pra abortar a ação envolvida. Exceção em um filter é isolada (loga + valor passa intacto ao próximo).

Toggle do plugin = tudo-ou-nada: enable liga handlers e filters; disable derruba ambos no próximo restart.

**Eventos GOWA / mensagem** (cobre tudo que o webhook GOWA emite):

| Evento | Quando dispara | Payload chave |
|--------|---------------|---------------|
| `message.received` | Inbound user msg (inclui group sem @mention) | `phone, name, text, raw_text, msg_id, media_type, media_path, is_group, group_jid, individual_phone, raw` |
| `message.sent` | Resposta IA, operator send, image/audio panel, retry, private @ia, echo do próprio celular | `phone, text, msg_id, media_type, media_path, source, status` — `source ∈ {ai, operator, private_ai, retry, echo}` |
| `message.any` *(alias)* | Re-dispatch de `received` + `sent` com `direction: "in"\|"out"` | igual ao original + `direction` |
| `message.reaction` | Reação emoji em mensagem | `id, phone, reaction, reacted_message_id, is_from_me` |
| `message.edited` | Mensagem editada | `id, phone, original_message_id, body` |
| `message.revoked` | Mensagem apagada pra todos | `id, phone, revoked_message_id, revoked_from_me, revoked_chat` |
| `message.deleted` | Mensagem deletada do histórico | `deleted_message_id, original_content, original_sender, was_from_me` |
| `presence.changed` | Digitando / gravando | `phone, state` (`composing`/`paused`), `media` (`text`/`audio`) |
| `receipt.changed` | Ack delivered/read | `phone, msg_ids, status` |
| `group.participants_changed` | Join/leave/promote/demote | `chat_id, phone, type, jids` |
| `group.joined` | Bot adicionado ao grupo | `chat_id, phone` |
| `call.received` | Chamada recebida (offer) | `call_id, phone, auto_rejected` |
| `newsletter.event` | Eventos de newsletter | `subtype, raw` |
| `chat.archived` | Arquivamento detectado no GOWA | `phone, archived` |
| `connection.changed` | GOWA connect/disconnect/QR | `is_connected, is_logged_in, qr_required` |

**Eventos internos**:

| Evento | Source |
|--------|--------|
| `llm.before` / `llm.after` | `aprocess_message`/`process_message` antes/depois de `chat.completions.create`. `after`: `reply, tool_calls, usage, latency_ms` |
| `tool.before` / `tool.after` | `_dispatch_tool`. `after`: `result, error, latency_ms` |
| `contact.updated` | PUT `/api/contacts/{phone}/info` |
| `contact.ai_toggled` | POST `/api/contacts/{phone}/toggle-ai` |
| `contact.tagged` | PUT `/api/contacts/{phone}/tags` |
| `tag.created` / `tag.updated` / `tag.deleted` | tag endpoints |
| `config.changed` | PUT `/api/config` (com `keys_changed`) |
| `tool_override.changed` | PUT `/api/tools/{name}` |
| `plugin.loaded` / `plugin.enabled` / `plugin.disabled` / `plugin.settings.changed` | lifecycle do plugin |
| `app.startup` / `app.shutdown` | lifespan do server |

Chave especial `*` — subscrever via `EVENT_HANDLERS = {"*": fn}` recebe todo evento emitido (após os subscribers específicos). `ctx.event_name` traz o nome real.

**Filters disponíveis** (ponto de modificação/cancelamento):

| Filter | Local | Tipo do valor | `None` faz |
|--------|-------|---------------|------------|
| `filter.webhook.payload` | `/api/webhook` antes de qualquer parse | `dict` (body raw GOWA) | Webhook responde 200 sem processar |
| `filter.message.before_save` | inbound depois do parse | `dict` (mensagem tipada com `raw`) | Mensagem ignorada (nem salva nem responde) |
| `filter.system_prompt` | antes do LLM | `str` | System prompt vira vazio |
| `filter.llm.messages` | antes do LLM | `list[dict]` (formato OpenAI) | LLM não é chamado |
| `filter.llm.tools` | antes do LLM | `list[dict]` (schemas) | LLM chamado sem tools |
| `filter.tool.args` | `_dispatch_tool` antes do executor | `{tool_name, args}` | Tool pulada |
| `filter.tool.result` | `_dispatch_tool` depois do executor | `str` (feedback pro LLM) | LLM recebe string vazia |
| `filter.reply.raw` | `_send_reply` antes do split | `str` | Nada é enviado |
| `filter.reply.parts` | depois do split | `list[str]` | Nada é enviado |
| `filter.reply.part` | cada parte antes do GOWA (vale pra send manual também) | `str` | Aquela parte é pulada |

**Assinaturas**:

```python
# events.py
def on_event(ctx: EventContext, payload: dict) -> None: ...
async def on_event_async(ctx: EventContext, payload: dict) -> None: ...

EVENT_HANDLERS = {"message.received": on_event, "llm.after": on_event_async}

# filters.py
def fn(ctx: FilterContext, value: T) -> T | None: ...
async def fn_async(ctx: FilterContext, value: T) -> T | None: ...

FILTERS = {
    "filter.reply.part": fn,                    # priority default 100
    "filter.message.before_save": (fn, 50),     # priority 50 — roda antes
}
```

`ctx` expõe `handler` (AgentHandler), `plugin_id`, `plugin_db`, `event_name`/`filter_name`, `emitted_at`. Sync vai pra `asyncio.to_thread`; async é `await`-ado direto. Filter pode ser sync ou async — em paths sync (process_message) o WhatsBot usa `apply_filter_sync` que delega ao loop com `run_coroutine_threadsafe`.

**Padrões de uso comuns**:

- **Observador / auditor / analytics** — `EVENT_HANDLERS = {"*": log_handler}` ou eventos específicos.
- **Anonimizar / traduzir / sanitizar inbound** — `FILTERS = {"filter.message.before_save": fn}` modifica o dict.
- **Adicionar assinatura / formatar / mascarar PII na saída** — `FILTERS = {"filter.reply.part": fn}` modifica cada parte.
- **Bloquear contato / palavra-chave / horário** — qualquer filter retornando `None`. Veja `assets/plugin_examples/blacklist`.
- **Injetar contexto extra no LLM** — `FILTERS = {"filter.system_prompt": fn}` ou `filter.llm.messages` pra reescrever o histórico antes do call.
- **Reagir a tool call específica** — `EVENT_HANDLERS = {"tool.after": fn}` com `if payload["tool_name"] == "x"`.
- **Push em tempo real pra tela do plugin** — `plugins.context.broadcast("evento", {...})` do dentro do handler.

**Boas práticas**:

- Filter síncrono trava o pipeline — mantenha rápido. Persistência pesada/network vai num event handler.
- NÃO chamar `gowa_client.send_message` dentro de handler de `message.sent` → loop infinito (a send produz outro `message.sent`).
- Filtre por `media_type` / `source` / `is_group` no INÍCIO do handler. O bus entrega tudo.
- Persista estado entre eventos em tabelas `plugin_<id>_*` (via `ctx.plugin_db` + `from sqlalchemy import text`), nunca em globals — não sobrevivem ao restart.
- `payload["raw"]` carrega o payload bruto do GOWA (potencialmente grande, com base64 de áudio). Plugins que logam tudo devem cortar `raw` antes de serializar.
- Restart obrigatório no toggle do plugin: `plugin.enabled`/`plugin.disabled` emitem ANTES do `os._exit`; o novo processo emite `plugin.loaded` no boot.

Plugins de exemplo bundled em `assets/plugin_examples/`: `event_logger` (assina `*`), `auto_signature` (`filter.reply.part`), `blacklist` (`filter.message.before_save` → `None`).

### Criar um plugin novo

Use o slash command `/new-plugin` no Claude Code. O comando lê os arquivos de referência, pergunta requisitos (id, telas, tools, tabelas, settings) e gera a estrutura completa em `storages/plugins/<id>/` sem tocar no core. Veja `.claude/commands/new-plugin.md`.

### Importar/exportar

- Export: `GET /api/plugins/<id>/export` retorna um `.zip` da pasta (excluindo `__pycache__/` e arquivos `.db`).
- Import: `POST /api/plugins/import` (multipart) valida o `plugin.yaml` na raiz, checa colisão de `id` e path traversal, extrai em `storages/plugins/<id>/`. Plugin importado fica `enabled=0` — usuário ativa pela UI.

## Migração de dados legados

Para instalações que usavam a versão anterior (armazenamento em JSON), o sistema detecta automaticamente na inicialização se o banco está vazio e existem arquivos JSON legados (`contacts/*.json`, `config.json`). Nesse caso, executa a migração via `db/migrate_json.py`. Os arquivos JSON originais não são deletados.

## Testes automatizados

Testes de endpoint em `tests/test_endpoints.py` — cobrem todos os endpoints da API usando FastAPI TestClient com banco SQLite temporário. GOWA e OpenRouter são mockados.

```bash
# Rodar testes (não precisa de servidor rodando)
source venv/Scripts/activate
python tests/test_endpoints.py
```

Os testes criam um banco temporário (SQLite por default; setar `WHATSBOT_TEST_DB_URL=postgresql+psycopg://...` para rodar contra Postgres), inserem dados de teste (contatos, mensagens, tags, usage), e validam 129 assertions cobrindo:
- Health, Auth (com e sem senha), Config (GET/PUT/test-key), Status
- Contacts (list, detail, search, archived, send, retry, image, audio, presence, read, toggle-ai, update info)
- Tags (CRUD + contact tags)
- Usage (summary, by-contact, detail)
- Logs, Webhook payloads, Webhook (presence, echo, ack)
- WhatsApp/QR (get, refresh, reconnect, logout)
- Sandbox (send, clear)
- Frontend SPA routes
- Auth middleware (proteção de endpoints, exemptions)

## Teste opcional com Evolution API

Se você tiver acesso a uma instância da Evolution API, pode testar o fluxo de mensagens de ponta a ponta. Isso é opcional, mas recomendado ao alterar webhook, agent, handler ou batching.

Variáveis de teste devem ser configuradas no arquivo `.env`:
- `EVOLUTION_API_URL` — URL base da Evolution API
- `EVOLUTION_API_KEY` — API key de autenticação
- `EVOLUTION_INSTANCE_ID` — ID da instância Evolution
- `EVOLUTION_TEST_NUMBER` — número WhatsApp para receber a mensagem de teste

### Como testar

1. Garanta que o servidor está rodando e conectado (`curl /api/status` → `connected: true`)
2. Envie mensagem de teste via Evolution API:
```bash
source .env
curl -X POST "${EVOLUTION_API_URL}/message/sendText/${EVOLUTION_INSTANCE_ID}" \
  -H "Content-Type: application/json" \
  -H "apikey: ${EVOLUTION_API_KEY}" \
  -d "{\"number\": \"${EVOLUTION_TEST_NUMBER}\", \"text\": \"mensagem de teste\"}"
```
3. Aguarde ~10 segundos e verifique os logs:
```bash
curl -s http://127.0.0.1:{web_port}/api/logs?limit=10
```
4. Confirme nos logs que aparece:
   - `[Webhook] Message from ...` — mensagem recebida
   - `[Batch] Processing N messages ...` — batch processado
   - `[Batch] Replied to ...` — resposta enviada

### Processo de teste para kill/restart

```bash
# Matar processos anteriores
taskkill //F //IM gowa.exe 2>&1; taskkill //F //IM python.exe 2>&1

# Iniciar servidor
source venv/Scripts/activate
python -c "import uvicorn; from server.dev import app; uvicorn.run(app, host='127.0.0.1', port=8080, log_level='info')"
```

## Gotchas

- O GOWA demora ~5s para iniciar e aceitar conexões — o polling de QR/status deve tolerar falhas silenciosamente
- **Device obrigatório**: `POST /devices` deve ser chamado antes de qualquer outro endpoint; sem device registrado, tudo retorna 404 `DEVICE_NOT_FOUND`
- **Login quando já conectado**: `GET /app/login` retorna erro `ALREADY_LOGGED_IN` se o device já está autenticado — verificar `is_connected()` antes de pedir QR
- **Respostas aninhadas**: listas de chats/mensagens vêm em `results.data[]`, não direto em `results`
- JIDs do WhatsApp seguem formato `5511999999999@s.whatsapp.net` — extrair phone com `.split("@")[0]`
- PyInstaller no Windows: paths de binários e web/ mudam (`sys._MEIPASS`), tratado em `gowa/manager.py` e `server/app.py`
- `subprocess.CREATE_NO_WINDOW` é necessário no Windows para não abrir janela de console do GOWA
- GOWA usa `stdout=subprocess.DEVNULL` — NUNCA usar `subprocess.PIPE` sem consumir, causa deadlock no Windows
- Config auto-salva no shutdown do server (lifespan) e na primeira execução (`Settings.load`)
- Frontend vendorizado: libs JS em `web/static/vendor/` — sem dependência de CDN em runtime
- **Sockets fantasma no Windows**: ao reiniciar frequentemente, portas podem ficar presas em LISTENING com PIDs inexistentes. Use porta alternativa ou reinicie o PC
- **run_dev.bat mata processos**: o bat já executa `taskkill` para gowa.exe e uvicorn.exe antes de iniciar
- **GOWA `/chats` limit máximo**: `GET /chats?limit=N` retorna HTTP 400 para valores acima de ~200. Usar `limit=100` como máximo seguro
- **Archive status é chat-level**: o webhook do GOWA **não** inclui campo de archive no payload. Para saber se um chat é arquivado, consultar `GET /chats` e verificar o campo `archived` no item com o `jid` correspondente
- **Debug do subprocess GOWA**: por padrão o stdout/stderr do GOWA vão para `DEVNULL` (sem custo). Para diagnosticar mensagens descartadas (payloads vazios, tipos não decodificados, templates HSM da Cloud API, etc.), setar a env `WHATSBOT_GOWA_DEBUG=1` (no Coolify ou outro ambiente) e reiniciar o container. Com a flag ativa, o GOWA é iniciado com `--debug=true` e os logs são gravados em `logs/gowa.log` (truncado quando passa de ~10 MB). Acessível via `GET /api/gowa-logs?limit=N` (default 500, max 5000). A resposta inclui `debug_enabled`, `log_path`, `size` e `lines[]`. Desligar setando `WHATSBOT_GOWA_DEBUG=0` ou removendo a variável + reiniciando
- **Mensagens HSM via Cloud API (linked device limitation)**: contas Business via WhatsApp Cloud API enviam mensagens template (`<hsm tag="..."/>`, ex: Mercado Livre, OTP, notificações). Por design do WhatsApp, esses templates **não são entregues com conteúdo para linked devices** — só para o device primário. O GOWA recebe um `placeholderMessage` com `type: MASK_LINKED_DEVICES` (sem body/media), e o webhook chega só com metadata (`chat_id`, `from`, `id`, `timestamp`). Não é bug — é limitação estrutural. Para confirmar, ativar `WHATSBOT_GOWA_DEBUG=1` e procurar `placeholderMessage` ou `<hsm tag=` em `/api/gowa-logs`
- **SQLite WAL files**: `whatsbot.db-wal` e `whatsbot.db-shm` são criados automaticamente pelo SQLite no modo WAL. Não deletar enquanto o servidor estiver rodando. São limpos automaticamente quando todas as conexões fecham
- **Auto-criação do banco**: na inicialização, `init_db()` resolve a URL (env > `storages/database.json` > sqlite default), cria o engine e roda `alembic upgrade head`. SQLite vazio é criado do zero; DBs SQLite legados (sem `alembic_version`) são automaticamente stampados no baseline antes do upgrade — não há recriação destrutiva
- **Bootstrap de plugins**: os plugins de referência vivem em `assets/plugin_examples/<id>/` (trackeados no git) e são copiados para `storages/plugins/<id>/` apenas na 1ª execução, quando `storages/plugins/` está vazio. Atualizar o core nunca sobrescreve plugins do usuário. Se o usuário deletar um plugin de referência pela UI, ele NÃO volta no próximo boot — a flag de "primeira execução" é "tem alguma subpasta?". Hoje o único bundled é `lembretes`.
- **Restart de plugin requer supervisor**: `enable`/`disable` chama `os._exit(0)` após um delay curto. Em Docker, `restart: unless-stopped` (compose) faz o container relançar; em dev, `restart.py` toca `server/_reload_trigger.py` (`.py` dentro de um `--reload-dir`, casa com o include default `*.py` do uvicorn) — o watchfiles reinicia o worker antes do `os._exit` rodar. O arquivo é regenerado em runtime e está no `.gitignore`. Em EXE Windows, o `update.py` relança. Sem supervisor, o servidor cai e não volta sozinho.
- **Prefixo de tabela enforced**: o migrator usa regex em `CREATE TABLE`/`ALTER TABLE`/`CREATE INDEX`/`DROP TABLE`/`DROP INDEX` e RECUSA migration que tente criar objeto fora do prefixo `plugin_<id>_`. Erro mostra qual nome violou. Usar comentários SQL `--` ou `/* */` é OK; o migrator os strip-a antes da validação.
- **Tool name é global**: se um plugin registra uma tool com nome já existente (core ou outro plugin), o registry loga warning e ignora a duplicata. Convenção: nomes específicos como `<id>_<verbo>` (ex: `orders_create`).
- **Import dinâmico de plugin JS**: o componente é carregado via `import(screen.component)` ES nativo. O path no manifest precisa começar com `/plugins/<id>/static/...` (servido pelo mount estático). CSP em `server/app.py` permite `'self'`, então funciona sem mudança.
- **Plugin com erro de carga**: se importação falha, o erro vai pra coluna `load_error` na tabela `plugins`, aparece no card da UI, e o plugin é pulado — o app sobe normalmente. Não há crash em cascata.
