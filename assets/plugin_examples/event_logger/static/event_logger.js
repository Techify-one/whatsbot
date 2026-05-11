// Tela do plugin Event Logger — log ao vivo do bus de eventos.
// Carrega o snapshot inicial via /recent e atualiza por WS
// (evento "plugin_event_logger_tick").
import { h } from 'preact';
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
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

function formatTime(ts) {
  try {
    return new Date(ts * 1000).toLocaleTimeString();
  } catch {
    return '';
  }
}

function eventColor(name) {
  if (name.startsWith('message.')) return 'bg-emerald-100 text-emerald-800';
  if (name.startsWith('llm.')) return 'bg-violet-100 text-violet-800';
  if (name.startsWith('tool.')) return 'bg-amber-100 text-amber-800';
  if (name.startsWith('plugin.')) return 'bg-blue-100 text-blue-800';
  if (name.startsWith('contact.') || name.startsWith('tag.')) return 'bg-pink-100 text-pink-800';
  if (name.startsWith('connection.') || name.startsWith('app.')) return 'bg-gray-200 text-gray-700';
  return 'bg-slate-100 text-slate-700';
}

export default function EventLoggerScreen({ apiBase = '/api/plugins/event_logger' } = {}) {
  const [items, setItems] = useState([]);
  const [counts, setCounts] = useState({});
  const [total, setTotal] = useState(0);
  const [bufferSize, setBufferSize] = useState(200);
  const [wsOk, setWsOk] = useState(false);
  const [paused, setPaused] = useState(false);
  const [filter, setFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const pausedRef = useRef(paused);
  pausedRef.current = paused;

  async function load() {
    try {
      const r = await apiFetch(`${apiBase}/recent`);
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'erro');
      setItems(data.data.items || []);
      setCounts(data.data.by_event || {});
      setTotal(data.data.total || 0);
      setBufferSize(data.data.buffer_size || 200);
      setError(null);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
    }
  }

  async function clearAll() {
    try {
      const r = await apiFetch(`${apiBase}/clear`, { method: 'POST' });
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'erro');
      setItems([]);
      setCounts({});
      setTotal(0);
    } catch (e) {
      setError(String(e.message || e));
    }
  }

  useEffect(() => {
    load();
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${proto}//${location.host}/ws`);
    ws.onopen = () => setWsOk(true);
    ws.onclose = () => setWsOk(false);
    ws.onmessage = (msg) => {
      try {
        const ev = JSON.parse(msg.data);
        if (ev.event !== 'plugin_event_logger_tick' || !ev.data) return;
        const { id, ts, event, summary, total: t, by_event } = ev.data;
        setTotal(t || 0);
        setCounts(by_event || {});
        if (pausedRef.current) return;
        setItems((prev) => {
          const next = [{ id, ts, event, summary }, ...prev];
          return next.slice(0, 500);
        });
      } catch {}
    };
    return () => ws.close();
  }, []);

  const filtered = useMemo(() => {
    if (!filter.trim()) return items;
    const f = filter.toLowerCase();
    return items.filter((it) => it.event.toLowerCase().includes(f));
  }, [items, filter]);

  const eventTypes = useMemo(() => {
    return Object.entries(counts).sort((a, b) => b[1] - a[1]);
  }, [counts]);

  return html`
    <div class="p-6 max-w-5xl mx-auto">
      <div class="flex items-center gap-2 mb-1">
        <h1 class="text-2xl font-bold">Event Logger</h1>
        <span class=${`text-xs px-2 py-0.5 rounded ${wsOk ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
          ${wsOk ? 'ao vivo' : 'offline'}
        </span>
      </div>
      <p class="text-sm text-gray-500 mb-4">
        Log em tempo real de todos os eventos do bus (mensagens, LLM, tools, contatos, plugins…).
        Buffer em memória limitado a ${bufferSize} entradas — não persiste no banco.
      </p>

      <div class="flex items-center gap-2 mb-3 flex-wrap">
        <div class="text-sm">
          <span class="font-semibold">${total}</span>
          <span class="text-gray-500"> evento${total === 1 ? '' : 's'} desde o boot</span>
        </div>
        <div class="flex-1"></div>
        <input
          type="text"
          placeholder="filtrar por nome (ex: message.)"
          value=${filter}
          onInput=${(e) => setFilter(e.target.value)}
          class="px-2 py-1 border border-wa-border rounded text-sm w-56"
        />
        <button
          onClick=${() => setPaused(!paused)}
          class=${`px-3 py-1 rounded text-sm border ${paused ? 'bg-yellow-100 border-yellow-300 text-yellow-800' : 'bg-white border-wa-border hover:bg-wa-hover'}`}
        >${paused ? 'Retomar' : 'Pausar'}</button>
        <button
          onClick=${clearAll}
          class="px-3 py-1 rounded text-sm border border-wa-border bg-white hover:bg-wa-hover"
        >Limpar</button>
      </div>

      ${eventTypes.length > 0 ? html`
        <div class="mb-4 flex flex-wrap gap-1">
          ${eventTypes.map(([name, count]) => html`
            <button
              key=${name}
              onClick=${() => setFilter(name)}
              class=${`text-xs px-2 py-0.5 rounded ${eventColor(name)} hover:ring-1 ring-offset-1 ring-current`}
              title="filtrar por ${name}"
            >${name} · ${count}</button>
          `)}
        </div>
      ` : null}

      ${error && html`<div class="text-red-600 mb-3 text-sm">Erro: ${error}</div>`}

      ${loading
        ? html`<div class="text-gray-500">Carregando…</div>`
        : filtered.length === 0
          ? html`<div class="text-gray-500 py-8 text-center border border-dashed border-wa-border rounded">
              ${items.length === 0
                ? 'Nenhum evento ainda. Mande uma mensagem no WhatsApp pra ver o log se mexer.'
                : 'Nenhum evento bate com o filtro.'}
            </div>`
          : html`<ul class="divide-y border border-wa-border rounded">
              ${filtered.map((it) => html`
                <li key=${it.id} class="py-2 px-3 flex items-start gap-3 hover:bg-wa-hover">
                  <span class="text-xs text-gray-500 font-mono whitespace-nowrap mt-0.5">
                    ${formatTime(it.ts)}
                  </span>
                  <span class=${`text-xs px-2 py-0.5 rounded font-medium whitespace-nowrap ${eventColor(it.event)}`}>
                    ${it.event}
                  </span>
                  <pre class="flex-1 text-xs text-gray-700 font-mono overflow-x-auto whitespace-pre-wrap break-all m-0">${
                    JSON.stringify(it.summary, null, 0)
                  }</pre>
                </li>
              `)}
            </ul>`
      }
    </div>
  `;
}
