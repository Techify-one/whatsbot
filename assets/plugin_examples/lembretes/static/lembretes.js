// Tela do plugin Lembretes — Preact + HTM, sem build.
// A lista se atualiza em tempo real via WebSocket /ws (eventos
// "plugin_lembretes_added" e "plugin_lembretes_deleted").
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

export default function LembretesScreen({ apiBase = '/api/plugins/lembretes' } = {}) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [wsOk, setWsOk] = useState(false);

  async function load() {
    try {
      const r = await apiFetch(`${apiBase}/items`);
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'erro');
      setItems(data.data || []);
      setError(null);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
    }
  }

  async function remove(id) {
    try {
      await apiFetch(`${apiBase}/items/${id}`, { method: 'DELETE' });
      setItems((prev) => prev.filter((i) => i.id !== id));
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
        if (ev.event === 'plugin_lembretes_added' && ev.data) {
          setItems((prev) =>
            prev.find((i) => i.id === ev.data.id) ? prev : [ev.data, ...prev]
          );
        } else if (ev.event === 'plugin_lembretes_deleted' && ev.data) {
          setItems((prev) => prev.filter((i) => i.id !== ev.data.id));
        }
      } catch {}
    };
    return () => ws.close();
  }, []);

  return html`
    <div class="p-6 max-w-3xl mx-auto">
      <div class="flex items-center gap-2 mb-1">
        <h1 class="text-2xl font-bold">Lembretes</h1>
        <span class=${`text-xs px-2 py-0.5 rounded ${wsOk ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
          ${wsOk ? 'ao vivo' : 'offline'}
        </span>
      </div>
      <p class="text-sm text-gray-500 mb-4">
        Quando um contato disser "me lembre de X" no WhatsApp, o lembrete aparece aqui automaticamente.
      </p>
      ${error && html`<div class="text-red-600 mb-3">Erro: ${error}</div>`}
      ${loading
        ? html`<div>Carregando…</div>`
        : items.length === 0
          ? html`<div class="text-gray-500">Nenhum lembrete ainda.</div>`
          : html`<ul class="divide-y">
              ${items.map((it) => html`
                <li key=${it.id} class="py-3 flex items-start gap-3">
                  <div class="flex-1">
                    <div class="text-sm">${it.text}</div>
                    <div class="text-xs text-gray-500 mt-1">
                      ${it.name || it.phone} · ${new Date(it.ts * 1000).toLocaleString()}
                    </div>
                  </div>
                  <button
                    class="text-xs text-red-600 hover:underline"
                    onClick=${() => remove(it.id)}
                  >remover</button>
                </li>
              `)}
            </ul>`
      }
    </div>
  `;
}
