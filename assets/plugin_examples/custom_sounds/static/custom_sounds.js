// Tela do plugin "Sons de Notificação" — Preact + HTM, sem build.
// Envia/gerencia sons e define qual será tocado nas notificações do painel,
// gravando o data URL escolhido em localStorage['whatsbot_notif_sound_custom']
// (chave lida pelo core em web/static/js/utils/notifications.js).
import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

const CUSTOM_SOUND_KEY = 'whatsbot_notif_sound_custom';
const CUSTOM_SOUND_ID_KEY = 'whatsbot_notif_sound_custom_id';
const VOLUME_KEY = 'whatsbot_notif_volume';  // 0..1, lido pelo core ao tocar a notificação

function getVolume01() {
  const raw = localStorage.getItem(VOLUME_KEY);
  const v = raw === null ? 1 : parseFloat(raw);
  if (!isFinite(v)) return 1;
  return Math.max(0, Math.min(1, v));
}

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

export default function CustomSoundsScreen({ apiBase = '/api/plugins/custom_sounds' } = {}) {
  const [sounds, setSounds] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [selectedId, setSelectedId] = useState(() => {
    const v = localStorage.getItem(CUSTOM_SOUND_ID_KEY);
    return v ? parseInt(v, 10) : null;
  });
  const [volume, setVolume] = useState(() => Math.round(getVolume01() * 100));

  function changeVolume(pct) {
    const clamped = Math.max(0, Math.min(100, pct));
    setVolume(clamped);
    localStorage.setItem(VOLUME_KEY, String(clamped / 100));
  }

  function playUrl(url) {
    const audio = new Audio(url);
    audio.volume = getVolume01();
    audio.play().catch(() => {});
  }

  async function load() {
    try {
      const r = await apiFetch(`${apiBase}/sounds`);
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'erro');
      setSounds(data.data || []);
      setError(null);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function handleUpload(e) {
    const file = e.target.files && e.target.files[0];
    e.target.value = '';  // allow re-uploading the same file
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const r = await apiFetch(`${apiBase}/sounds`, { method: 'POST', body: fd });
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'Falha no envio.');
      await load();
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setUploading(false);
    }
  }

  async function fetchDataUrl(id) {
    const r = await apiFetch(`${apiBase}/sounds/${id}`);
    const data = await r.json();
    if (!data.ok) throw new Error(data.error || 'erro');
    return data.data.data_url;
  }

  async function preview(id) {
    try {
      playUrl(await fetchDataUrl(id));
    } catch (err) {
      setError(String(err.message || err));
    }
  }

  async function useSound(id) {
    try {
      const url = await fetchDataUrl(id);
      localStorage.setItem(CUSTOM_SOUND_KEY, url);
      localStorage.setItem(CUSTOM_SOUND_ID_KEY, String(id));
      setSelectedId(id);
      playUrl(url);  // confirmação audível
    } catch (err) {
      setError(String(err.message || err));
    }
  }

  function restoreDefault() {
    localStorage.removeItem(CUSTOM_SOUND_KEY);
    localStorage.removeItem(CUSTOM_SOUND_ID_KEY);
    setSelectedId(null);
  }

  async function rename(id, currentName) {
    const name = (window.prompt('Novo nome para o som:', currentName) || '').trim();
    if (!name || name === currentName) return;
    try {
      const r = await apiFetch(`${apiBase}/sounds/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'Falha ao renomear.');
      setSounds((prev) => prev.map((s) => (s.id === id ? { ...s, name } : s)));
    } catch (err) {
      setError(String(err.message || err));
    }
  }

  async function remove(id) {
    try {
      await apiFetch(`${apiBase}/sounds/${id}`, { method: 'DELETE' });
      if (selectedId === id) restoreDefault();
      setSounds((prev) => prev.filter((s) => s.id !== id));
    } catch (err) {
      setError(String(err.message || err));
    }
  }

  return html`
    <div class="space-y-4">
        <p class="text-sm text-wa-secondary">
          Envie seus próprios sons e escolha qual toca nas notificações. O som só
          é reproduzido quando a notificação sonora está ligada no plugin
          <span class="font-medium">Notificações</span> (Gerenciar Plugins → Configurar).
        </p>

        ${error ? html`<div class="text-sm text-red-500 bg-red-50 border border-red-200 rounded-lg px-3 py-2">${error}</div>` : ''}

        <div class="bg-white rounded-xl p-4 border border-wa-border shadow-sm flex items-center gap-3 flex-wrap">
          <label class="px-4 py-2 bg-wa-teal hover:bg-wa-tealDark text-white text-sm font-medium rounded-lg cursor-pointer transition-colors ${uploading ? 'opacity-50 pointer-events-none' : ''}">
            ${uploading ? 'Enviando...' : 'Enviar som'}
            <input type="file" accept="audio/*" onChange=${handleUpload} class="hidden" />
          </label>
          <span class="text-xs text-wa-secondary">Formatos de áudio (.mp3, .ogg, .wav...) até 1 MB.</span>
          <button
            type="button"
            onClick=${restoreDefault}
            class="ml-auto text-sm text-wa-teal hover:underline ${selectedId == null ? 'opacity-40 pointer-events-none' : ''}"
          >Restaurar som padrão</button>
        </div>

        <div class="bg-white rounded-xl p-4 border border-wa-border shadow-sm">
          <div class="flex items-center justify-between mb-2">
            <label class="text-sm font-semibold text-wa-text">Volume do som</label>
            <span class="text-xs text-wa-secondary">${volume}%</span>
          </div>
          <div class="flex items-center gap-3">
            <input
              type="range" min="0" max="100" step="5"
              value=${volume}
              onInput=${(e) => changeVolume(parseInt(e.target.value, 10))}
              class="flex-1 accent-wa-teal"
            />
            <button
              type="button"
              onClick=${() => { const u = localStorage.getItem(CUSTOM_SOUND_KEY); if (u) playUrl(u); }}
              class="text-sm text-wa-teal hover:underline shrink-0 ${selectedId == null ? 'opacity-40 pointer-events-none' : ''}"
            >Testar</button>
          </div>
          <span class="block text-xs text-wa-secondary mt-1">Aplica-se ao som das notificações deste navegador (inclusive ao som padrão).</span>
        </div>

        <div class="bg-white rounded-xl border border-wa-border shadow-sm divide-y divide-wa-border">
          ${loading ? html`
            <div class="p-6 text-center text-wa-secondary text-sm animate-pulse-slow">Carregando...</div>
          ` : sounds.length === 0 ? html`
            <div class="p-6 text-center text-wa-secondary text-sm">
              Nenhum som enviado ainda. Use "Enviar som" para adicionar o primeiro.
              <div class="mt-1 text-xs">Enquanto isso, o som padrão é usado.</div>
            </div>
          ` : sounds.map((s) => html`
            <div key=${s.id} class="flex items-center gap-3 px-4 py-3">
              <div class="flex-1 min-w-0">
                <div class="text-sm font-medium text-wa-text truncate flex items-center gap-2">
                  ${s.name}
                  ${selectedId === s.id ? html`<span class="text-[10px] font-semibold text-white bg-wa-teal rounded-full px-2 py-0.5">Em uso</span>` : ''}
                </div>
                <div class="text-xs text-wa-secondary">${s.mimetype}</div>
              </div>
              <button type="button" onClick=${() => preview(s.id)}
                class="text-sm text-wa-teal hover:underline shrink-0">Tocar</button>
              <button type="button" onClick=${() => rename(s.id, s.name)}
                class="text-sm text-wa-teal hover:underline shrink-0">Renomear</button>
              <button type="button" onClick=${() => useSound(s.id)}
                class="text-sm px-3 py-1.5 rounded-lg shrink-0 ${selectedId === s.id ? 'bg-wa-bg text-wa-secondary border border-wa-border' : 'bg-wa-teal text-white hover:bg-wa-tealDark'}"
                disabled=${selectedId === s.id}>
                ${selectedId === s.id ? 'Selecionado' : 'Usar'}
              </button>
              <button type="button" onClick=${() => remove(s.id)}
                class="text-sm text-red-500 hover:underline shrink-0">Excluir</button>
            </div>
          `)}
        </div>

        <p class="text-xs text-wa-secondary">
          A escolha do som vale para este navegador/dispositivo (a biblioteca de
          sons fica salva no servidor e pode ser reusada em qualquer dispositivo).
        </p>
    </div>
  `;
}
