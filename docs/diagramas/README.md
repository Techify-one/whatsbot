# WhatsBot-Lite — Documentação em Diagramas

Documentação visual da arquitetura do WhatsBot-Lite em arquivos **`.excalidraw` nativos**
(gerados com a skill [Agents365-ai/excalidraw-skill](https://github.com/Agents365-ai/excalidraw-skill)).
Cada diagrama tem um `.excalidraw` editável e um `.png` de pré-visualização em [excalidraw/](excalidraw/).

## Como abrir / editar

- **Editar:** arraste o arquivo `.excalidraw` para [excalidraw.com](https://excalidraw.com)
  (ou *File → Open*). As formas, cores e setas vêm prontas e totalmente editáveis.
- **Só visualizar:** abra o `.png` correspondente.
- **Re-exportar PNG/SVG:** `excalidraw-brute-export-cli -i arquivo.excalidraw -o saida.png -f png -s 2 -b true`.

## Arquivo único (todos os diagramas)

Se quiser abrir **tudo num só desenho**, use
[excalidraw/00-whatsbot-completo.excalidraw](excalidraw/00-whatsbot-completo.excalidraw) —
os 6 diagramas empilhados verticalmente num único canvas. É só arrastar esse arquivo
para o excalidraw.com. Os arquivos individuais abaixo continuam disponíveis.

## Diagramas

### 1. Arquitetura de componentes
Como GOWA (subprocess), FastAPI, AgentHandler/AGNO, proxy LLM Techify, plugins e o
banco se conectam em runtime.

![Arquitetura](excalidraw/01-arquitetura.png)

### 2. Fluxo de mensagem (webhook → resposta)
Sequência real: webhook do GOWA → batching sensível a "digitando" → LLM via AGNO com
tool calling → split e envio da resposta. Filters e events anotados na ordem em que disparam.

![Fluxo de mensagem](excalidraw/02-fluxo-mensagem.png)

### 3. Pipeline de Filters e Events
Ordem exata dos **filters** (azul, interceptivos) e **events** (amarelo, fire-and-forget)
ao longo do processamento, incluindo os pontos de abort (`None`).

![Filters e events](excalidraw/03-filters-events.png)

### 4. Camada de dados (SQLAlchemy Core)
As tabelas reais de [db/tables.py](../../db/tables.py) e suas relações (contacts como
entidade central, executions/plugins e tabelas standalone).

![Camada de dados](excalidraw/04-camada-dados.png)

### 5. Sistema de plugins — lifecycle
Do boot ao wiring: bootstrap → discovery → deps → migrations → import → registro, com o
branch de erro (`load_error`) e o loop de restart no toggle enable/disable.

![Plugin lifecycle](excalidraw/05-plugin-lifecycle.png)

### 6. Pontos de extensão de um plugin
O que um plugin pode agregar sem tocar no core (tools, prompts, events, filters, routes,
settings, migrations, screens) e onde cada peça se conecta.

![Extensão de plugin](excalidraw/06-extensao-plugin.png)

---

> Fontes verificadas no código: `server/routes/webhook.py`, `agent/handler.py`,
> `agent/agno_engine.py`, `plugins/loader.py`, `db/tables.py`.
