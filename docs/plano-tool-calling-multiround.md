# Análise do plano "Tool-calling multi-round" aplicado à versão BASE (AGNO)

> Documento de **análise + planejamento** para você revisar antes de qualquer alteração.
> Ele avalia o plano que você gerou na versão **clínica** e mapeia o que realmente
> se aplica a **esta base** (`/home/thiago/whatsbot`).
>
> **Fontes:** leitura direta do código da base, do AGNO 2.6.21 instalado na venv, e
> da clínica extraída de `/tmp/whatsbot-clinica.zip`. Todas as afirmações têm
> referência `arquivo:linha` verificada.

---

## 0. Veredito (TL;DR)

**O plano foi escrito contra uma arquitetura que esta base já substituiu.** Ele
diagnostica o loop de tool-calling *feito à mão* no `agent/handler.py`
(`chat.completions.create` com tools → um follow-up **sem** tools). Esse loop
**não existe mais na base**: a base delega todo o raciocínio + tool-calling ao
**AGNO** (`agent/agno_engine.py` → `Agent.arun/run`), e o AGNO **já faz
encadeamento sequencial de tools nativamente, sem teto por padrão**.

Ou seja: o objetivo central do plano — `clinica_catalogo → clinica_horarios_disponiveis
→ clinica_agendar → texto final` — **já funciona nesta base, hoje, sem escrever
loop nenhum.** Implementar o plano "ao pé da letra" seria **re-introduzir código que
a base deletou de propósito** (o commit `58586e1 refactor(agent): remove AGNO Team...`
e a migração pro motor AGNO).

O que sobra de genuinamente útil do plano, adaptado à base, é **pequeno e opcional**:
1. um **teto de segurança configurável** (hoje o encadeamento é *ilimitado* — risco de runaway);
2. `max_tokens` **configurável** (hoje hardcoded em 1024);
3. decidir sobre o **guard anti-DSML** (existe na clínica, **não** existe na base);
4. um **harness de teste** com o *seam* de mock correto (que **não** é o `handler._get_client`);
5. (opcional) observabilidade por-round e unificação sync/async (bem menor agora).

E há um **caminho estratégico para a própria clínica** (§6): em vez de implementar o
loop manual do plano, a clínica deveria **adotar o motor AGNO da base**.

---

## 1. Prova técnica: o AGNO já encadeia (e sem teto)

Verificado lendo o AGNO 2.6.21 em `venv/lib/python3.12/site-packages/agno/`:

| Fato | Evidência | Consequência |
|---|---|---|
| **Encadeia multi-round nativamente.** `while True:` chama o modelo; se vier `tool_calls`, executa, **acrescenta os resultados às mensagens** e faz `continue` (chama o modelo de novo **com as tools ainda disponíveis**); só dá `break` quando o modelo responde texto sem tool_calls. | `agno/models/base.py:925` (async `aresponse`) e `:703` (sync); execução em `:962`/`:973`, resultados em `:1038`, `continue` em `:1089`, `break` em `:1091-1092`. | Tool A → resultado → tool B → resultado → texto final funciona numa única chamada `runner.arun(...)`. **Não precisa de loop manual em `agno_engine`.** |
| **Teto de rounds = ILIMITADO por padrão.** `tool_call_limit` default `None` no `Agent`; a base **nunca** o define. Com `None`, o guard `if function_call_limit is not None:` é pulado inteiro. | `agno/agent/agent.py:177`; `agno/agent/agno_engine.py` (`_build_single_agent`, `_AGENT_CONTEXT_OFF`) não passa o parâmetro; guard em `agno/models/base.py:2324`. | Não há teto nenhum hoje. Um modelo que "adora" tools pode iterar indefinidamente (limitado só pelo modelo eventualmente emitir texto). **É o único ponto de risco real** e o único item do plano (§4.4/§9) que continua fazendo sentido. |
| **Ao atingir o teto (se definido), degrada com elegância** — injeta um resultado sintético "Tool call limit reached... Don't try to execute it again" e continua o loop pro modelo produzir texto. **Não** dá erro nem trava. | `agno/models/base.py:2327-2329` (`create_tool_call_limit_error_result`) + texto em `:2123-2131`. | Se adicionarmos um teto, o comportamento no limite é seguro pro webhook. (A ideia do plano de "forçar texto no último round" é desnecessária: o AGNO já resolve.) |
| **`tool_choice` = "auto" a cada round** (via omissão). A base nunca força tool. | `agno/agent/agent.py:184`; `agno/models/openai/chat.py:259-260` (só envia a chave se `!= None`). | O modelo escolhe tool-vs-texto livremente em cada round — é o que permite o encadeamento adaptativo e o término limpo. |
| **Usage é SOMADO entre todos os rounds.** `accumulate_model_metrics` roda dentro do loop a cada chamada e faz `+=`. | `agno/models/base.py:950-953`; `agno/metrics.py:703-705`. | `agno_engine._extract_usage` lendo `run_output.metrics` e o handler gravando **uma vez** já registra o custo/tokens **total** correto. Nada a fazer. |
| **`run_output.messages` tem o histórico completo dos rounds.** | `agno/agent/_response.py:1038-1040`. | `agno_engine._extract_reply` (varredura reversa pela última mensagem `assistant` sem tool_calls) devolve o texto final correto **após** o encadeamento — inclusive com `split_messages` ligado. Já resolvido. |
| **Cancelamento async propaga.** `arun` captura e re-levanta `asyncio.CancelledError`; há checkpoints `araise_if_cancelled` em volta da chamada do modelo. | `agno/agent/_run.py:1836-1859` + checkpoints `:1569/1659/1667/1691/1760`. | O webhook cancelando turnos obsoletos aborta o run AGNO no meio do loop corretamente. Já resolvido. |
| **`retries=0`** controla retries de falha da chamada do modelo, **não** rounds de tool. | `agno/agent/_run.py:399-400` (`num_attempts = agent.retries + 1`). | Não confundir com teto de tools (são coisas distintas). |

**Conclusão da §1:** dos 5 objetivos do plano (encadeamento, teto, usage por round,
cards em ordem, tratar `content`+`tool_calls` juntos), **4 já são atendidos de graça
pelo AGNO na base.** Só o "teto" merece ação — e mesmo assim como *guarda opcional*,
não como *feature ausente*.

---

## 2. Mapa: cada elemento do plano × realidade da base

| O plano assume / pede | Na base (`agent/handler.py` + `agno_engine.py`) | Situação |
|---|---|---|
| Loop `create` com tools + **follow-up sem tools** em `aprocess_message`/`process_message` | **Não existe.** Ambos delegam ao AGNO: `handler.py:1060` (`await agno_engine.run_async(...)`) e `handler.py:1190` (`agno_engine.run_sync(...)`). | **Obsoleto** — o "bug" que o plano corrige já foi removido. |
| Transformar em loop multi-round (§4) | AGNO **já é** esse loop (`base.py:925/703`). | **Já feito** (pelo framework). |
| `executed_tools` acumular entre rounds (§4.1, §6.1-6.2) | O coletor `executed` acumula **todas** as tools de todos os rounds dentro do run (`agno_engine.py` entrypoints em `:160/199`). | **Já feito.** |
| `MAX_TOOL_ROUNDS = 5`, configurável (§4.3) | AGNO usa `tool_call_limit` (default `None` = ilimitado). A base **não** o expõe. | **Parcial** — falta expor como teto de segurança (§4.1 abaixo). |
| Caso "content + tool_calls juntos" (§2.2B) | `_extract_reply` pega a última `assistant` **sem** tool_calls; o AGNO só encerra quando não há tool_calls. | **Já resolvido** (`agno_engine.py:281`). |
| Guard anti-DSML `_sanitize_reply`/`_looks_like_raw_tool_call` (§2, §6.5) | **Não existe na base** (grep vazio). Existe só na clínica (`handler.py:48/58/290`). | **Decisão** — ver §4.4. |
| Tensão `split_messages` (JSON array) × tool-calling (§2.3/§8.2) | A regra de JSON ainda é injetada (`handler.py:792-831`); a base **adicionou** mitigações que a clínica não tem: `_encode_history_for_split` (`:662-693`) + `_extract_reply`. | **Ainda existe**, mas atenuada. Ver §4.3. |
| Unificar duplicação sync/async de ~200 linhas (§5) | A duplicação encolheu muito: o miolo do loop foi pro AGNO. Sobra duplicação **menor** entre os dois métodos do handler (setup pré-LLM) e entre `run_async`/`run_sync` do `agno_engine`. | **Relevância reduzida.** Ver §4.7. |
| `max_tokens` hardcoded em 1024 (§8.3) | Confirmado: `agno_engine.py:55` (`_DEFAULT_MAX_TOKENS = 1024`) e `:90` (`build_model`). | **Válido** — ver §4.2. |
| Harness de LLM falso; mock em `handler._get_client` (§7) | Testes hoje mockam em `patch.object(agent_handler, "process_message")` (`tests/test_endpoints.py:827`) — **nunca** exercitam o loop. E o *seam* de mock do plano **está errado pra base**: o AGNO cria seu **próprio** cliente OpenAI. | **Válido, mas com seam diferente.** Ver §4.6. |
| Registrar usage/steps por round (§6.3, §8.4) | `agno_engine` registra `track_step('llm_request'/'llm_response')` **uma vez por run** (`:322/332` e `:350/360`) e `tool_executed` por tool (`:161/200`). Os rounds internos do AGNO **não** viram steps separados. | **Gap de observabilidade** (opcional). Ver §4.5. |
| Streaming fora de escopo (§8.6) | O `arun` do AGNO já roda por um caminho de streaming interno. | Nota de caveat (§8). |

---

## 3. O que do plano ainda vale — itens acionáveis na base

Ordenados por valor. Todos são **pequenos** e **independentes**.

### 4.1 Teto de segurança configurável (`tool_call_limit`) — **o único item "de feature"**

**Problema real:** hoje o encadeamento é ilimitado. Em produção, um modelo que
entra em loop de tools (pede a mesma tool repetidamente) só para quando "resolver"
emitir texto — sem garantia. É exatamente o runaway que o plano teme em §4.4/§9.

**Solução (mínima):** passar `tool_call_limit` ao `Agent` em
`agno_engine._build_single_agent` (`agent/agno_engine.py:252-258`), lido de um
atributo do handler (`self.max_tool_calls`), com um default seguro (ex.: **8**
— cobre folgado catálogo→horários→agendar e ainda dá margem).

**Semântica a documentar:** `tool_call_limit` no AGNO conta **execuções de tool**
(não "rounds"). Ao exceder, o AGNO injeta o erro sintético e deixa o modelo
finalizar em texto (`base.py:2327`) — não trava. Portanto é um **orçamento**, não um
corte abrupto.

**Plumbing (segue o padrão de `max_context_messages`):**
- `config/settings.py`: adicionar `"max_tool_calls": 8` ao `DEFAULTS` (perto de `:73`) e, se quiser env override, ao mapa (perto de `:49`).
- `agent/handler.py`: novo param no `__init__` + atributo `self.max_tool_calls`; tratar em `update_config` (`:344-384`).
- Construção: `main.py:60` e `server/dev.py:52` passam `max_tool_calls=settings.get("max_tool_calls", 8)`.
- PUT config: incluir `"max_tool_calls"` no allowlist (`server/routes/config.py:78-79`) e no `update_config(...)` (`:112`/`:144`); `setup.py:194` idem se aplicável.
- `agent/agno_engine.py`: `build_runner`/`_build_single_agent` recebem o valor e passam `tool_call_limit=...` ao `Agent(...)`.

**Esforço:** ~1-2 h. **Risco:** baixo (só adiciona um teto onde não havia).

### 4.2 `max_tokens` configurável

Hoje `_DEFAULT_MAX_TOKENS = 1024` (`agno_engine.py:55`, usado em `build_model:90`).
Respostas longas (ex.: listar 18 horários) podem truncar. Já existe um caminho
parcial: `build_model` lê `model_config["max_tokens"]` (path config-in-DB via
`agent_factory`), mas no caminho default (`ai_engine_enabled=False`, o comum)
`model_config` é `None` e cai no 1024.

**Solução:** mesmo plumbing do §4.1 — `self.max_tokens` no handler (default 1024),
passado a `build_model` como fallback quando `model_config` não traz o valor.
Chave `"max_tokens"` no `DEFAULTS` e no PUT de config.

**Esforço:** ~1 h. **Risco:** baixo. **Recomendação:** fazer junto com §4.1 (mesmo plumbing).

### 4.3 Tensão `split_messages` (JSON array) × tool-calling — **documentar, não reescrever**

A regra "responda SEMPRE em array JSON" (`handler.py:792-831`) ainda compete com o
tool-calling, mas a base **já** adicionou duas defesas que a clínica não tinha:
`_encode_history_for_split` (mostra o histórico do assistant no mesmo formato JSON,
`:662-693`) e `_extract_reply` (evita concatenar "chatter" pré-tool com o texto
final). Isso reduz muito o drift.

**Recomendação:** **não** implementar §8.2 agora (desacoplar formato de tool-calling
é mudança grande e arriscada). Apenas **medir**: se o `deepseek-v4-pro` via proxy
ainda vazar sintaxe crua *mesmo com o AGNO oferecendo tools todo round*, aí sim
avaliar a opção 8.2(b) (largar o "modelo formata JSON" e mover o split 100% pra
pós-processamento com `parse_split_reply`). Deixar como item de roadmap.

### 4.4 Guard anti-DSML — **RECOMENDADO** como rede de segurança (não existe na base)

A clínica tem `_looks_like_raw_tool_call` + `_sanitize_reply` (`clinica .../agent/handler.py:48/58/290`)
que detectam o vazamento `<｜DSML｜tool_calls>...` e trocam por uma mensagem de
desculpas. **A base não tem isso.**

**Causa raiz confirmada (via feedback do dono):** o vazamento acontecia porque *"a IA
não conseguia executar mais de uma tool na mesma mensagem do cliente"* — exatamente a
limitação do loop manual da clínica (1 round + follow-up **sem tools**). Quando o
modelo via o resultado do `clinica_catalogo` e queria chamar `clinica_horarios_disponiveis`,
o follow-up não oferecia tools → sem canal estruturado → improvisava a chamada como
texto DSML. **O AGNO oferece tools em todo round, então esse gatilho some na base** —
o modelo emite a 2ª tool pelo campo estruturado `tool_calls`. Portanto **o vazamento do
exemplo se resolve na base pela migração pro AGNO** (não pelos itens deste plano).

**Ressalva residual — a base está desprotegida nela:** verifiquei que o AGNO só lê o
campo estruturado `tool_calls` (`agno/models/openai/chat.py:749/827`); ele **não**
parseia DSML/hermes/pythonic a partir do texto. Logo, se o modelo/proxy ainda cuspir
DSML como texto **mesmo com tools oferecidas** (falha de parsing no proxy Techify, não
mais o gatilho "sem tools"), o AGNO trata como **resposta final** e — sem `_sanitize_reply`
na base — vaza **cru pro cliente** (pior que na clínica, que ao menos troca por desculpa).
Caso raro, mas não zero com `deepseek-v4-pro` via proxy. Fator agravante: a regra
`split_messages` "responda SEMPRE em array JSON" (§4.3) empurra o modelo pra texto e
compete com tool-calling.

**Opções (escolher):**
- **(a) Não portar** — confiar no AGNO. Reavaliar com dados reais. *Deixa a base exposta ao caso residual.*
- **(b) Portar como filtro de plugin** (`filter.reply.raw` → devolve fallback se detectar sintaxe crua). Mantém o core limpo, é opt-in, cobre a clínica sem tocar no motor. **Recomendado.**
- **(c) Portar pro core** (todos os usuários). Se o vazamento se mostrar geral (não só clínica).

**Conserto de causa raiz "de verdade"** (fora do whatsbot): garantir no proxy Techify /
servidor de inferência que o `tool_call_parser` / chat template do DeepSeek esteja ativo,
convertendo `<｜DSML｜tool_calls>` em `tool_calls` estruturado antes de chegar ao AGNO.

### 4.5 Observabilidade por-round (opcional)

Como o AGNO roda o loop internamente, `track_step('llm_request'/'llm_response')`
dispara **1x por run**, não por round. Se você quiser ver cada round na tela de
execuções, dá pra plugar um **hook do AGNO** (o `after_tool_results` que aparece em
`base.py:840` é um ponto de checkpoint por-turno) e emitir um `track_step` por round.
**Baixa prioridade** — o usage total já está correto (§1) e os `tool_executed`
individuais já são rastreados.

### 4.6 Harness de teste com o *seam* correto — **faça, mas mude o alvo do mock**

O plano (§7) manda mockar `handler._get_client`/`_get_async_client`. **Isso não
funciona na base:** o AGNO cria o **próprio** cliente OpenAI, separado do handler,
via `OpenAIChat.get_client()` (`agno/models/openai/chat.py:133`) e
`get_async_client()` (`:159`), que instanciam `OpenAIClient(...)`/`AsyncOpenAIClient(...)`;
a chamada acontece em `chat.py:418` (`self.get_client().chat.completions.create(...)`).

**Seam correto para um "LLM falso scriptável" que dirige rounds reais do AGNO:**
- Patchar `agno.models.openai.chat.OpenAIChat.get_async_client` (e `get_client`) para
  devolver um fake cujo `.chat.completions.create` é um `AsyncMock`/`MagicMock` com
  `side_effect = [resp_round1, resp_round2, ...]` (cada `resp` = um objeto estilo
  ChatCompletion, umas com `tool_calls`, a última com texto). Assim o **loop real do
  AGNO** roda e o encadeamento é de fato testado.
- Alternativa mais grosseira: `patch` em `agno_engine.run_async/run_sync` — mas isso
  **pula** o loop, então só testa o *wiring* do handler, não o encadeamento. Serve pra
  outros testes, não pra este.

Casos a cobrir (adaptados de §7.2): encadeamento feliz (A→B→texto), tools em paralelo
num round, teto atingido (com `tool_call_limit` do §4.1 → checa o
`create_tool_call_limit_error_result`), filtro vetando uma tool no meio
(`filter.tool.args`→None), e cancelamento async. Este harness vira infra permanente
(§8.5).

Arquivo novo sugerido: `tests/test_tool_loop.py`. **Escreva-o primeiro contra o
comportamento atual** — deve **passar** (encadeamento já funciona), provando o
veredito da §0, e depois blindar as mudanças §4.1/§4.2.

### 4.7 Unificação sync/async (§5) — relevância reduzida, opcional

O grosso da duplicação foi pro AGNO. Sobra duplicação **menor**: (a) o setup pré-LLM
em `aprocess_message` vs `process_message` (`handler.py:980+` vs `:1116+`), e (b)
`run_async` vs `run_sync` em `agno_engine.py` (`:313` vs `:341`). Ainda há risco de
divergência (ex.: mexer num e esquecer o outro), mas o custo/benefício de unificar
caiu. **Opcional**; se fizer, a abordagem (B) do plano (extrair helpers
compartilhados) é mais segura que a (A) aqui, porque o handler tem bastante `await`
espalhado. **Não** bloquear §4.1/§4.2 nisto.

---

## 5. Non-regression: o que NÃO pode quebrar (já verificado)

- **Cards "Ferramenta IA" na UI**: `_broadcast_tool_calls` (`server/routes/webhook.py:586`)
  já itera `for tc in tool_calls` (lista) criando uma msg `role="tool_call"` por tool
  (`:590-603`); chamado em `:886` e `:1025` com `result.tool_calls`. Como o `executed`
  do AGNO já acumula na ordem de execução, os cards saem em sequência
  (catálogo→horários→agendar). Trata `transfer_to_human` especial em `:618`. **OK.**
- **`parse_split_reply`** (`server/helpers.py:30`): processa só o reply **final**;
  intermediários nunca chegam nele. **OK.**
- **Usage/custo**: somado pelo AGNO (§1). **OK.**
- **Cancelamento**: propaga (§1). **OK.**

---

## 6. Caminho estratégico para a CLÍNICA (importante)

O plano nasceu na clínica pra consertar o loop manual quebrado. Mas a clínica é um
**fork antigo, pré-AGNO**: `clinica .../agent/` tem só `handler.py, memory.py,
execution.py, tools/` — **sem** `agno_engine.py`, `agent_factory.py`,
`group_mentions.py`, feature de "melhoria", nem `ai_engine_enabled`. O loop manual da
clínica está em `handler.py:659/720/729` (async) e `:872/934/943` (sync).

**Recomendação:** para a clínica ter o encadeamento, o certo **não** é implementar o
loop manual do plano — é **adotar o motor AGNO da base**. As tools da clínica
(`clinica_catalogo` `:562`, `clinica_horarios_disponiveis` `:641`, `clinica_agendar`
`:763`, + `consultar_agendamentos`, `enviar_link_agendamento`, `clinica_cancelar_agendamento`,
`clinica_remarcar_agendamento`) vivem **no plugin** `storages/plugins/clinica/tools.py`
(lista `CORE_TOOLS` em `:1174`), **desacopladas do handler** — portam sem mudança.

**Gap de adoção (moderado):** o handler da base traz junto AGNO + `agent_factory`
(config-in-DB) + `group_mentions` + feature de melhoria + `_record_usage_tokens`. A
clínica não tem nada disso. Duas rotas:
- **(i)** Trazer o `handler.py` + `agno_engine.py` da base para a clínica *inteiros*
  (recomendado) e **re-adicionar só o guard anti-DSML** (como filtro de plugin, §4.4b).
- **(ii)** Rebasear a clínica sobre a base e re-aplicar só as customizações da clínica
  (o plugin `clinica/`, o plugin `agenda/`, ajustes de prompt).

> ⚠️ Este documento planeja mudanças na **base**. A adoção pela clínica é um esforço
> à parte (merge/rebase de fork) — vale um plano próprio se você for por esse caminho.

---

## 7. O que **NÃO** fazer

- ❌ **Não** reintroduzir um loop `chat.completions.create` manual no `handler.py` da
  base. Seria desfazer o refactor pro AGNO e recriar o bug de "content intermediário".
- ❌ **Não** mockar `handler._get_client` no teste do loop (não é o seam do AGNO; §4.6).
- ❌ **Não** setar `tool_choice` forçado nem "round final sem tools" — o AGNO já
  termina limpo e degrada bem no teto (§1).
- ❌ **Não** reescrever `split_messages`/`parse_split_reply` agora (§4.3) — medir antes.

---

## 8. Riscos / caveats

- **Usage por round no proxy streaming**: o `arun` do AGNO usa caminho de streaming;
  se algum round não retornar `usage` no chunk final, os tokens **daquele round** são
  ignorados por `accumulate_model_metrics` (`agno/metrics.py`) — o encadeamento
  continua, mas o custo pode subestimar. Vale confirmar que a Techify devolve usage por
  round. (Não é regressão do plano; é característica do provider.)
- **Custo/latência com encadeamento**: cada round é 1 chamada de LLM. Como hoje é
  ilimitado, o §4.1 (teto) é a mitigação. Sem ele, um fluxo patológico multiplica custo.
- **Teto conta execuções de tool, não rounds**: ao escolher o número do §4.1, lembre
  que 2 tools num mesmo round consomem 2 do orçamento.
- **Guard anti-DSML ausente** na base: se você trouxer o handler da base pra clínica
  sem re-adicionar o guard (§4.4/§6), perde a rede de segurança contra o vazamento.

---

## 9. Decisões em aberto (pra você decidir antes da implementação)

1. **Adicionar o teto `max_tool_calls`?** (§4.1) Recomendo **sim**, default 8. Qual valor?
2. **`max_tokens` configurável?** (§4.2) Recomendo **sim**, default 1024, e fazer junto do teto.
3. **Guard anti-DSML** (§4.4): (a) não portar / (b) filtro de plugin `filter.reply.raw` / (c) core. **Recomendo (b)** — o vazamento do exemplo se resolve na base via AGNO, mas o AGNO não recupera DSML de texto e a base está desprotegida no caso residual (proxy cuspindo DSML como texto mesmo com tools).
4. **Escopo agora**: este plano é sobre a **base**. Você quer também que eu planeje a
   **adoção do motor AGNO pela clínica** (§6)? É um trabalho separado (merge de fork).
5. **§8.2 (desacoplar JSON de tools)** e **§4.5 (observabilidade por round)** e
   **§4.7 (unificar sync/async)**: manter como roadmap (não fazer agora)?

---

## 10. Plano de implementação sugerido (para a BASE, se aprovado)

Assumindo "sim" para §4.1 + §4.2 + harness de teste (o núcleo de valor):

1. **`tests/test_tool_loop.py`** — harness de LLM falso via patch em
   `agno.models.openai.chat.OpenAIChat.get_async_client`/`get_client` (§4.6). Escrever
   os casos **contra o comportamento atual** e confirmar verde (encadeamento já
   funciona). *Teste primeiro.*
2. **Config plumbing** de `max_tool_calls` (default 8) e `max_tokens` (default 1024):
   `config/settings.py` (DEFAULTS + env), `agent/handler.py` (`__init__` + atributos +
   `update_config`), `main.py`/`server/dev.py` (construção),
   `server/routes/config.py` (allowlist + `update_config`), `server/routes/setup.py` se aplicável.
3. **`agent/agno_engine.py`**: `_build_single_agent` passa `tool_call_limit=self.max_tool_calls`;
   `build_model` usa `self.max_tokens` como fallback quando `model_config` não traz.
4. **Testar**: harness cobre teto atingido (`create_tool_call_limit_error_result`) e
   `max_tokens` aplicado. Rodar toda a suíte
   (`tests/test_endpoints.py`, `tests/test_events_filters.py`, `tests/test_plugin_events.py`).
5. **(Decisão)** guard anti-DSML (§4.4) — se optar por (b), criar plugin com
   `filter.reply.raw`.
6. **Doc**: registrar a nova semântica no `CLAUDE.md` (seção "Motor de agente (AGNO)"):
   encadeamento nativo + o novo teto configurável.

---

## 11. Arquivos que a implementação tocaria (base)

| Arquivo | O quê |
|---|---|
| `config/settings.py` | `DEFAULTS`: `max_tool_calls` (8), `max_tokens` (1024); env opcional |
| `agent/handler.py` | `__init__` + `self.max_tool_calls`/`self.max_tokens` + `update_config` |
| `agent/agno_engine.py` | `tool_call_limit` no `Agent`; `max_tokens` fallback em `build_model` |
| `main.py`, `server/dev.py` | Passar as novas chaves na construção do `AgentHandler` |
| `server/routes/config.py` (+ `setup.py`) | Allowlist + `update_config` das novas chaves |
| `tests/test_tool_loop.py` | **Novo** — harness de LLM falso no seam do AGNO |
| `CLAUDE.md` | Documentar encadeamento nativo + teto configurável |
| *(opcional)* plugin `filter.reply.raw` | Guard anti-DSML, se decidido (§4.4b) |

> **Nada** em `handler.py` precisa de um loop manual. A mudança é **aditiva e pequena**:
> expor um teto onde hoje é ilimitado e tornar `max_tokens` configurável.
