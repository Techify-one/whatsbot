# /new-plugin — Criar um novo plugin do WhatsBot

Você (Claude) vai criar um novo plugin do WhatsBot **sem mexer em nenhum arquivo do core**. Tudo fica em `storages/plugins/<id>/`.

Argumento opcional do usuário (descrição do plugin): `$ARGUMENTS`

## Passo 1 — Coletar requisitos

Use `AskUserQuestion` para coletar (ou inferir do `$ARGUMENTS`):

1. **id do plugin** (snake_case, ex: `orders`, `cardapio`, `agenda`). Validar regex `^[a-z][a-z0-9_]{0,31}$`.
2. **Nome humano** e descrição curta.
3. **Telas**: lista de objetos `{title, path, icon}`. Ex: `[{title: 'Pedidos', path: '/orders', icon: 'shopping-cart'}]`. Ao menos 1.
4. **Tools que o LLM vai expor**: lista `[{name, description, params: {field: type}}]`. Pode ser vazia se o plugin é só UI.
5. **Precisa injetar conteúdo no system prompt?** (ex: cardápio). Se sim, descreva o que injetar.
6. **Tabelas no banco**: lista de `{name, columns}` (sem o prefixo, vou adicionar). Pode ser vazia.
7. **Settings declaráveis** (Pydantic Valves) — campos configuráveis pelo usuário na tela de settings. Pode ser vazio.
8. **Events que o plugin observa** (fire-and-forget, paralelo): lista de nomes a assinar — ex: `message.received`, `message.sent`, `llm.after`, `tool.after`, `*` (catch-all). Pode ser vazia. Veja a tabela completa em `CLAUDE.md` → Events.
9. **Filters que o plugin intercepta** (síncronos, podem modificar ou abortar): lista de nomes a interceptar — ex: `filter.message.before_save`, `filter.reply.part`, `filter.system_prompt`, `filter.tool.args`. Retornar `None` aborta a ação. Pode ser vazia. Veja a tabela completa em `CLAUDE.md` → Filters.

Se o usuário escreveu tudo no `$ARGUMENTS`, deduza e confirme com **uma** pergunta de validação.

## Passo 2 — Ler referências do core (NÃO modificar)

Antes de gerar qualquer arquivo, **leia** estes arquivos para seguir os padrões existentes:

- [agent/tools/save_contact_info.py](agent/tools/save_contact_info.py) — padrão de tool (schema dict + `execute(ctx, args)`)
- [agent/handler.py](agent/handler.py) linhas 227-300 — como prompt fragments são chamados
- [db/tables.py](db/tables.py) — `Table` objects do core (referência de tipos e nomes)
- [server/routes/tags.py](server/routes/tags.py) — padrão de APIRouter + helpers `_ok`/`_err`
- [web/static/js/components/Dashboard.js](web/static/js/components/Dashboard.js) — padrão de componente Preact + HTM
- [storages/plugins/lembretes/](storages/plugins/lembretes/) — plugin completo de referência (copie e adapte)
- [assets/plugin_examples/event_logger/events.py](assets/plugin_examples/event_logger/events.py) — exemplo de `EVENT_HANDLERS` com catch-all `*` + handler específico
- [assets/plugin_examples/blacklist/filters.py](assets/plugin_examples/blacklist/filters.py) — exemplo de filter que retorna `None` pra abortar (`filter.message.before_save`)
- [assets/plugin_examples/auto_signature/filters.py](assets/plugin_examples/auto_signature/filters.py) — exemplo de filter que modifica valor (`filter.reply.part`)
- [plugins/events.py](plugins/events.py) — implementação do bus (assinaturas reais, prioridade, sync/async)

## Passo 3 — Gerar a estrutura

Crie os arquivos em `storages/plugins/<id>/`. **Sempre** prefixe nomes de tabela com `plugin_<id>_` — o migrator valida e rejeita o contrário.

```
storages/plugins/<id>/
├── plugin.yaml              ← manifest (campos abaixo)
├── __init__.py              ← arquivo vazio
├── tools.py                 ← se houver tools
├── prompts.py               ← se houver fragments
├── routes.py                ← se houver REST endpoints
├── settings.py              ← se houver settings
├── events.py                ← se houver event handlers
├── filters.py               ← se houver filters
├── migrations/
│   └── 001_initial.sql
└── static/
    └── <id>.js
```

### plugin.yaml

```yaml
id: <id>
name: <Nome Humano>
version: 1.0.0
whatsbot_api_version: ">=1.0,<2.0"
description: <descrição curta>
author: <autor>
entry:
  tools: tools          # omitir se não houver
  prompts: prompts      # omitir se não houver
  routes: routes        # omitir se não houver
  settings: settings    # omitir se não houver
  events: events        # omitir se não houver
  filters: filters      # omitir se não houver
migrations: migrations  # omitir se não houver
screens:
  - id: <screen-id>
    title: <Título>
    path: /<path>       # SPA path, escolher algo único
    icon: <icon-name>   # opcional, informativo
    component: /plugins/<id>/static/<id>.js
permissions: []
dependencies: []
```

### tools.py (se houver tools)

Cada tool é um par `(schema, executor)`. O executor recebe `ToolContext` (ver [plugins/context.py](plugins/context.py)) e retorna `str | None` (string vira `tool` reply no follow-up; `None` usa o default).

```python
import logging
import time

from sqlalchemy import text
from plugins.context import broadcast, make_plugin_db

logger = logging.getLogger(__name__)

MY_TOOL = {
    "type": "function",
    "display_label": "<Rótulo legível>",  # opcional — default mostrado em /tools
    "function": {
        "name": "<tool_name>",   # único globalmente
        "description": "<descrição clara, instrui quando chamar>",
        "parameters": {
            "type": "object",
            "properties": {
                "<param>": {"type": "string", "description": "..."},
            },
            "required": [],
        },
    },
}

def execute_my_tool(ctx, args: dict) -> str | None:
    # ctx.contact é ContactMemory; ctx.handler é AgentHandler
    # ctx.tag_registry; ctx.plugin_id == '<id>'
    with make_plugin_db() as conn:
        conn.execute(
            text("INSERT INTO plugin_<id>_items (text, ts) VALUES (:text, :ts)"),
            {"text": args.get("text", ""), "ts": int(time.time())},
        )
    return None  # ou string de feedback

CORE_TOOLS = [(MY_TOOL, execute_my_tool)]
```

**Banco de dados (importante)**: o WhatsBot agora roda em cima de SQLAlchemy
Core (SQLite default, Postgres opcional via tela Settings → Banco). Plugin
acessa o banco SEMPRE via:

```python
from sqlalchemy import text
from plugins.context import make_plugin_db

with make_plugin_db() as conn:
    rows = conn.execute(
        text("SELECT * FROM plugin_<id>_items WHERE phone = :phone"),
        {"phone": phone},
    ).mappings().all()
```

Proibido em código de plugin (quebra em Postgres):

- `?` placeholders (use `:nome` bind params)
- `strftime('%s','now')` → use `int(time.time())` em Python
- `INSERT OR REPLACE` / `INSERT OR IGNORE` → ver `db.upsert.upsert()` ou refatore com select+update
- `cur.lastrowid` direto → use `result.inserted_primary_key[0]`
- Qualquer função/sintaxe SQLite-only (`||` concat com regras peculiares, `AUTOINCREMENT`, `PRAGMA`).

### prompts.py (se houver fragments)

```python
from sqlalchemy import text
from plugins.context import make_plugin_db

def my_fragment(contact, ctx) -> str:
    # contact: ContactMemory; ctx: PromptContext
    return "\n\n--- <Título> ---\n<conteúdo>\n--- Fim ---"

PROMPT_FRAGMENTS = [my_fragment]
```

### routes.py (se houver REST)

Mounted em `/api/plugins/<id>` automaticamente. Auth do core já cobre.

```python
from fastapi import APIRouter
from sqlalchemy import text
from plugins.context import make_plugin_db

router = APIRouter()

@router.get("/items")
async def list_items():
    with make_plugin_db() as conn:
        rows = conn.execute(
            text("SELECT * FROM plugin_<id>_items ORDER BY ts DESC")
        ).mappings().all()
    return {"ok": True, "data": [dict(r) for r in rows]}
```

### settings.py (se houver settings)

```python
from pydantic import BaseModel, Field

class Settings(BaseModel):
    field_a: str = Field(default="...", description="...")
    field_b: int = Field(default=10, description="...", ge=1)
```

### events.py (se houver event handlers)

Plugin assina eventos do bus declarando `EVENT_HANDLERS` (dict `nome -> callable`). Handler pode ser sync ou async — async é `await`-ado direto, sync vai pra `asyncio.to_thread`. Exceção em um handler é isolada (loga, não derruba outros).

```python
import logging

logger = logging.getLogger(__name__)

def on_message_received(ctx, payload: dict) -> None:
    # ctx: EventContext — ctx.handler, ctx.plugin_id, ctx.plugin_db,
    #                     ctx.event_name (importante p/ catch-all "*"), ctx.emitted_at
    # payload: dict tipado conforme o evento (ver tabela em CLAUDE.md)
    if payload.get("is_group"):
        return  # filtra cedo
    logger.info("[<id>] %s disse: %s", payload["phone"], payload["text"])

async def on_llm_after(ctx, payload: dict) -> None:
    # latency_ms, reply, tool_calls, usage
    logger.info("[<id>] LLM levou %sms", payload.get("latency_ms"))

EVENT_HANDLERS = {
    "message.received": on_message_received,
    "llm.after": on_llm_after,
    # "*": catch_all,   # opcional — recebe TODO evento (após handlers específicos)
}
```

**Eventos disponíveis** (lista completa em `CLAUDE.md`):

- Mensagem: `message.received`, `message.sent`, `message.any`, `message.reaction`, `message.edited`, `message.revoked`, `message.deleted`
- Conexão/grupo: `presence.changed`, `receipt.changed`, `group.participants_changed`, `group.joined`, `call.received`, `connection.changed`, `chat.archived`
- LLM/tool: `llm.before`, `llm.after`, `tool.before`, `tool.after`
- Core: `contact.updated`, `contact.ai_toggled`, `contact.tagged`, `tag.created/updated/deleted`, `config.changed`, `tool_override.changed`, `plugin.loaded/enabled/disabled/settings.changed`, `app.startup/shutdown`

**Não chame `gowa_client.send_message` dentro de handler de `message.sent`** — gera loop infinito (a send produz outro `message.sent`).

### filters.py (se houver filters)

Plugin intercepta o pipeline declarando `FILTERS` (dict `nome -> callable` ou `(callable, priority)`). Filter recebe `(ctx, value)` e retorna `value` modificado ou `None` para **abortar** a ação envolvida. Pode ser sync ou async. Exceção é isolada (loga, valor passa intacto adiante).

```python
import logging

logger = logging.getLogger(__name__)

def block_keyword(ctx, msg: dict) -> dict | None:
    # ctx: FilterContext — ctx.handler, ctx.plugin_id, ctx.plugin_db,
    #                       ctx.filter_name, ctx.emitted_at
    text = (msg.get("text") or "").lower()
    if "spam" in text:
        logger.info("[<id>] bloqueado: %s", msg.get("phone"))
        return None  # ABORTA: mensagem não é salva nem responde
    return msg

def add_signature(ctx, part: str) -> str:
    if not part.strip():
        return part
    return f"{part}\n\n— Atendimento <Plugin>"

FILTERS = {
    "filter.message.before_save": block_keyword,
    "filter.reply.part": (add_signature, 50),  # priority 50 — roda antes do default (100)
}
```

**Filters disponíveis** (tabela completa com tipo do `value` em `CLAUDE.md`):

| Filter | `value` | `None` faz |
|---|---|---|
| `filter.webhook.payload` | `dict` (raw GOWA) | webhook responde 200 sem processar |
| `filter.message.before_save` | `dict` (mensagem tipada) | mensagem ignorada |
| `filter.system_prompt` | `str` | system prompt vazio |
| `filter.llm.messages` | `list[dict]` | LLM não é chamado |
| `filter.llm.tools` | `list[dict]` | LLM chamado sem tools |
| `filter.tool.args` | `{tool_name, args}` | tool pulada |
| `filter.tool.result` | `str` | LLM recebe string vazia |
| `filter.reply.raw` | `str` | nada é enviado |
| `filter.reply.parts` | `list[str]` | nada é enviado |
| `filter.reply.part` | `str` (cada parte) | parte é pulada |

**Filter síncrono trava o pipeline** — mantenha rápido. Persistência pesada / network → joga num event handler em `events.py`.

### migrations/001_initial.sql

**Toda** tabela / índice tem que começar com `plugin_<id>_`. O migrator faz validação por regex.

**Sintaxe das migrations.** O migrator usa `engine.begin()` e roda contra o
backend ativo — SQLite por default, Postgres se o usuário trocou pelo Settings.
Para máxima portabilidade, evite `strftime` (gere timestamps no Python com
`int(time.time())`) e defaults baseados em funções específicas. `INTEGER PRIMARY
KEY AUTOINCREMENT` funciona em SQLite (default) mas falha em fresh Postgres
install — se o plugin precisar rodar em Postgres direto do zero, prefira
gerar o `id` no código (UUID, snowflake) e declarar `id TEXT PRIMARY KEY`.
Para uma migração SQLite → Postgres existente, o endpoint admin reflete as
tabelas do source e recria no destino com os tipos corretos.

```sql
CREATE TABLE IF NOT EXISTS plugin_<id>_items (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    created_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS plugin_<id>_items_created_at
    ON plugin_<id>_items(created_at);
```

### static/<id>.js

Componente Preact + HTM com `default export`. Usar imports do importmap (`preact`, `preact/hooks`, `htm`). Receber `apiBase` como prop.

**Importante (auth):** quando o usuário configura uma senha no app, a API exige `Authorization: Bearer <token>` em **todas** as chamadas `/api/*`. O token fica em `localStorage` sob a chave `whatsbot_token`. Plugin precisa anexar esse header — senão a tela mostra `Não autenticado.` quando o app está protegido por senha. O helper abaixo cobre isso e também captura 401 pra disparar o evento de logout do core (`whatsbot:unauthorized`):

```js
import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

function authHeaders(extra = {}) {
  const token = localStorage.getItem('whatsbot_token') || '';
  return token ? { ...extra, Authorization: `Bearer ${token}` } : { ...extra };
}

async function apiFetch(url, init = {}) {
  const headers = authHeaders(init.headers || {});
  const res = await fetch(url, { ...init, headers });
  if (res.status === 401) {
    localStorage.removeItem('whatsbot_token');
    window.dispatchEvent(new Event('whatsbot:unauthorized'));
    throw new Error('Não autenticado.');
  }
  return res;
}

export default function MyScreen({ apiBase }) {
  const [items, setItems] = useState([]);
  useEffect(() => {
    apiFetch(`${apiBase}/items`)
      .then(r => r.json())
      .then(d => { if (d.ok) setItems(d.data || []); })
      .catch(() => { /* unauthorized já tratado */ });
  }, []);
  return html`
    <div class="p-6 max-w-3xl mx-auto">
      <h1 class="text-2xl font-bold mb-4">Minha Tela</h1>
      ${items.map(it => html`<div key=${it.id}>${it.name}</div>`)}
    </div>
  `;
}
```

Para `POST`/`PUT` com JSON, passe `headers: { 'Content-Type': 'application/json' }` e `body: JSON.stringify(...)` — o `apiFetch` adiciona o `Authorization` em cima desses headers. Para uploads (`FormData`), **não** defina `Content-Type` — o navegador define com boundary correto.

## Passo 4 — Instruções finais ao usuário

Ao terminar, mostre:

1. Caminho da pasta criada.
2. Lista de arquivos gerados.
3. Próximo passo: "Acesse `/plugins` no app. Clique em **Ativar** no card do plugin. O servidor reinicia em ~3s e a tela aparece no menu."
4. Para customizar settings: na tela `/plugins`, clique em **Configurar**.
5. Para compartilhar: na tela `/plugins`, **Exportar** baixa um `.zip`.

## Regras importantes

- **Nunca modifique arquivos do core** (`agent/`, `db/`, `server/`, `web/`). Plugin é totalmente isolado.
- **Sempre prefixe tabelas com `plugin_<id>_`**. O migrator rejeita o contrário.
- **Não invente nomes de imports** — use os do importmap (`preact`, `preact/hooks`, `htm`) e os módulos que o core já expõe (`db.engine`, `db.tables`, `db.repositories.*`, `agent.memory`, `plugins.context`). Para acesso ao banco em plugin: `from plugins.context import make_plugin_db` e `from sqlalchemy import text`.
- **Tool name é global**: se conflitar com um nome existente o loader rejeita o plugin. Prefira nomes específicos como `<id>_<verbo>` (ex: `orders_create`, `cardapio_listar`).
- **Settings UI é gerada automaticamente** a partir do schema Pydantic — strings, ints, floats, bools, enums. Não escreva form manual.
- **Migrations rodam uma única vez** por versão. Para evoluir o schema, crie `002_*.sql`, `003_*.sql` — não edite `001`.

## Contrato de tools (importante)

Toda tool registrada num plugin é automaticamente inserida na tabela `tool_overrides` com defaults (enabled=1, description=NULL). O usuário pode customizar via UI em `/tools` — ligar/desligar, editar a description que vai pro LLM, e renomear o display label.

Por isso:

- **`name`** vira identidade pública e estável. **NÃO renomeie** depois de release — quebra histórico de `usage` (que grava `call_type=<name>`) e qualquer override que o usuário tenha criado. Para evoluir, crie uma tool nova e deprecie a antiga.
- **`description`** em código é o **default** mostrado na UI. Escreva como instrução clara pro LLM (quando usar / quando NÃO usar) — seu default precisa funcionar sem customização. O usuário pode sobrescrever, mas o reset volta pro seu texto.
- **`display_label`** (opcional, no nível do dict raiz, fora de `function`) é o rótulo legível mostrado em `/tools`. O handler retira esse campo antes de mandar pro LLM (não vai pra OpenAI). Use português, curto. Ex: `"display_label": "Salva Dados do Contato"`.
- Quando o plugin é deletado pela UI, todas as overrides daquele plugin somem junto (`delete_for_plugin` no DELETE do plugin).
- Convenção de naming: `<plugin_id>_<verbo>` (ex: `lembretes_create`, `orders_search`) — evita colisão e ajuda o usuário a saber de que plugin a tool veio.
