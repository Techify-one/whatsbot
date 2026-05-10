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

Se o usuário escreveu tudo no `$ARGUMENTS`, deduza e confirme com **uma** pergunta de validação.

## Passo 2 — Ler referências do core (NÃO modificar)

Antes de gerar qualquer arquivo, **leia** estes arquivos para seguir os padrões existentes:

- [agent/tools/save_contact_info.py](agent/tools/save_contact_info.py) — padrão de tool (schema dict + `execute(ctx, args)`)
- [agent/handler.py](agent/handler.py) linhas 227-300 — como prompt fragments são chamados
- [db/schema.sql](db/schema.sql) — estilo de SQL (CREATE TABLE IF NOT EXISTS, índices)
- [server/routes/tags.py](server/routes/tags.py) — padrão de APIRouter + helpers `_ok`/`_err`
- [web/static/js/components/Dashboard.js](web/static/js/components/Dashboard.js) — padrão de componente Preact + HTM
- [storages/plugins/lembretes/](storages/plugins/lembretes/) — plugin completo de referência (copie e adapte)

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
from db.connection import get_db

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
    conn = get_db()
    # use prefixo plugin_<id>_ em queries
    return None  # ou string de feedback

CORE_TOOLS = [(MY_TOOL, execute_my_tool)]
```

### prompts.py (se houver fragments)

```python
from db.connection import get_db

def my_fragment(contact, ctx) -> str:
    # contact: ContactMemory; ctx: PromptContext
    return "\n\n--- <Título> ---\n<conteúdo>\n--- Fim ---"

PROMPT_FRAGMENTS = [my_fragment]
```

### routes.py (se houver REST)

Mounted em `/api/plugins/<id>` automaticamente. Auth do core já cobre.

```python
from fastapi import APIRouter
from db.connection import get_db

router = APIRouter()

@router.get("/items")
async def list_items():
    conn = get_db()
    rows = conn.execute("SELECT * FROM plugin_<id>_items").fetchall()
    return {"ok": True, "data": [dict(r) for r in rows]}
```

### settings.py (se houver settings)

```python
from pydantic import BaseModel, Field

class Settings(BaseModel):
    field_a: str = Field(default="...", description="...")
    field_b: int = Field(default=10, description="...", ge=1)
```

### migrations/001_initial.sql

**Toda** tabela / índice tem que começar com `plugin_<id>_`. O migrator faz validação por regex.

```sql
CREATE TABLE IF NOT EXISTS plugin_<id>_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
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
- **Não invente nomes de imports** — use os do importmap (`preact`, `preact/hooks`, `htm`) e os módulos que o core já expõe (`db.connection`, `db.repositories.*`, `agent.memory`).
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
