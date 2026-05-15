// Tela do plugin transcricao_grupos — Preact + HTM, segue o padrão visual
// do app (wa-* classes, toggle peer-checkbox).
import { h } from 'preact';
import { useEffect, useMemo, useState } from 'preact/hooks';
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

function audioLabel(mode) {
  if (mode === 'off') return 'Desligado';
  if (mode === 'received') return 'Recebidos';
  if (mode === 'both') return 'Recebidos e enviados';
  if (mode === 'sent') return 'Enviados';
  return mode || '-';
}

function Toggle({ checked, disabled, onChange }) {
  return html`
    <label class="inline-flex items-center cursor-pointer align-middle ${disabled ? 'opacity-60 cursor-not-allowed' : ''}">
      <input
        type="checkbox"
        class="sr-only peer"
        checked=${checked}
        disabled=${disabled}
        onChange=${(e) => onChange(e.currentTarget.checked)}
      />
      <div class="relative w-9 h-5 bg-gray-300 rounded-full peer peer-checked:bg-green-600 transition-colors after:content-[''] after:absolute after:top-0.5 after:left-0.5 after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-transform peer-checked:after:translate-x-4"></div>
    </label>
  `;
}

export default function TranscricaoGrupos({ apiBase = '/api/plugins/transcricao_grupos' } = {}) {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState({});
  const [defaults, setDefaults] = useState(null);
  const [groups, setGroups] = useState([]);
  const [query, setQuery] = useState('');
  const [showArchived, setShowArchived] = useState(false);
  const [error, setError] = useState(null);
  const [okMsg, setOkMsg] = useState('');

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const r = await apiFetch(`${apiBase}/groups`);
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'erro');
      setDefaults(data.data.defaults);
      setGroups(data.data.groups || []);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function updateGroup(jid, patch) {
    setSaving((p) => ({ ...p, [jid]: true }));
    setError(null);
    setOkMsg('');
    try {
      const r = await apiFetch(
        `${apiBase}/groups/${encodeURIComponent(jid)}`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(patch),
        }
      );
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'erro');
      setGroups((prev) => prev.map((g) => {
        if (g.chat_jid !== jid) return g;
        const next = { ...g };
        if ('audio_mode' in patch) {
          next.override_audio = data.data.audio_mode;
          next.effective_audio_mode = data.data.audio_mode === 'off'
            ? 'off'
            : (defaults?.audio_transcription_mode || 'received');
        }
        if ('image_enabled' in patch) {
          next.override_image = data.data.image_enabled;
          next.effective_image_enabled = data.data.image_enabled === 0
            ? false
            : !!(defaults?.image_transcription_enabled);
        }
        return next;
      }));
      setOkMsg('Salvo');
      setTimeout(() => setOkMsg(''), 1200);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setSaving((p) => {
        const n = { ...p };
        delete n[jid];
        return n;
      });
    }
  }

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return groups.filter((g) => {
      if (!showArchived && g.is_archived) return false;
      if (!q) return true;
      return (g.name || '').toLowerCase().includes(q)
        || (g.chat_jid || '').toLowerCase().includes(q);
    });
  }, [groups, query, showArchived]);

  const overriddenCount = useMemo(
    () => groups.filter(
      (g) => g.override_audio !== null || g.override_image !== null
    ).length,
    [groups]
  );

  const audioDefault = defaults?.audio_transcription_mode || 'received';
  const imageDefault = !!defaults?.image_transcription_enabled;
  const audioGlobalActive = audioDefault === 'received' || audioDefault === 'both';

  return html`
    <div class="p-6 max-w-5xl mx-auto">
      <div class="flex items-center gap-2 mb-1">
        <h1 class="text-2xl font-bold">Transcrição por Grupo</h1>
        ${overriddenCount > 0 ? html`
          <span class="text-xs px-2 py-0.5 rounded bg-blue-100 text-blue-700">
            ${overriddenCount} customizado${overriddenCount === 1 ? '' : 's'}
          </span>
        ` : null}
      </div>
      <p class="text-sm text-wa-secondary mb-4">
        Por padrão, a transcrição segue a configuração global do app.
        Aqui você pode forçar <strong>desligado</strong> em grupos
        específicos para economizar chamadas à API.
      </p>

      <div class="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
        <div class="bg-white border border-wa-border rounded-lg px-4 py-3">
          <div class="text-[11px] uppercase tracking-wide text-wa-secondary mb-1">
            Padrão global · Áudio
          </div>
          <div class="flex items-center gap-2">
            <span class="text-[13px] font-medium ${audioGlobalActive ? 'text-green-700' : 'text-wa-secondary'}">
              ${audioLabel(audioDefault)}
            </span>
            ${!audioGlobalActive ? html`
              <span class="text-[11px] text-amber-700">
                — global desligado
              </span>
            ` : null}
          </div>
        </div>
        <div class="bg-white border border-wa-border rounded-lg px-4 py-3">
          <div class="text-[11px] uppercase tracking-wide text-wa-secondary mb-1">
            Padrão global · Imagem
          </div>
          <div class="flex items-center gap-2">
            <span class="text-[13px] font-medium ${imageDefault ? 'text-green-700' : 'text-wa-secondary'}">
              ${imageDefault ? 'Ligado' : 'Desligado'}
            </span>
            ${!imageDefault ? html`
              <span class="text-[11px] text-amber-700">— global desligado</span>
            ` : null}
          </div>
        </div>
      </div>

      <div class="flex flex-wrap items-center gap-3 mb-3">
        <input
          type="search"
          value=${query}
          onInput=${(e) => setQuery(e.target.value)}
          placeholder="Buscar grupo…"
          class="flex-1 min-w-[220px] md:max-w-sm text-[13px] border border-wa-border rounded px-3 py-2 focus:outline-none focus:border-wa-teal"
        />
        <label class="inline-flex items-center gap-2 text-[12px] text-wa-secondary cursor-pointer">
          <input
            type="checkbox"
            checked=${showArchived}
            onChange=${(e) => setShowArchived(e.currentTarget.checked)}
            class="rounded border-wa-border"
          />
          mostrar arquivados
        </label>
        <button
          onClick=${load}
          disabled=${loading}
          class="text-[13px] px-3 py-2 border border-wa-border rounded hover:bg-gray-50 disabled:opacity-50"
        >
          ${loading ? 'Carregando…' : 'Atualizar'}
        </button>
        ${okMsg ? html`<span class="text-[12px] text-green-700">${okMsg}</span>` : null}
      </div>

      ${error ? html`<div class="text-red-600 mb-3 text-sm">${error}</div>` : null}

      ${loading && groups.length === 0
        ? html`<div class="text-wa-secondary">Carregando…</div>`
        : filtered.length === 0
          ? html`
            <div class="bg-white border border-wa-border rounded-lg px-4 py-8 text-center text-wa-secondary text-[13px]">
              ${groups.length === 0
                ? 'Nenhum grupo encontrado. Receba uma mensagem em algum grupo pra ele aparecer aqui.'
                : 'Nenhum grupo encontrado com esse filtro.'}
            </div>
          `
          : html`
            <div class="bg-white border border-wa-border rounded-lg overflow-hidden">
              <table class="w-full text-[13px]">
                <thead class="bg-wa-panel border-b border-wa-border text-wa-secondary text-[12px] uppercase tracking-wide">
                  <tr>
                    <th class="text-left px-3 py-2 font-medium">Grupo</th>
                    <th class="text-center px-3 py-2 font-medium w-32">Áudio</th>
                    <th class="text-center px-3 py-2 font-medium w-32">Imagem</th>
                  </tr>
                </thead>
                <tbody>
                  ${filtered.map((g) => {
                    const isSaving = !!saving[g.chat_jid];
                    const audioOn = g.effective_audio_mode !== 'off';
                    const imageOn = !!g.effective_image_enabled;
                    const audioOverridden = g.override_audio !== null && g.override_audio !== undefined;
                    const imageOverridden = g.override_image !== null && g.override_image !== undefined;
                    return html`
                      <tr key=${g.chat_jid} class="border-b border-wa-border last:border-b-0 hover:bg-gray-50">
                        <td class="px-3 py-2">
                          <div class="flex items-center gap-2">
                            <span class="font-medium truncate">${g.name}</span>
                            ${g.is_archived ? html`<span class="text-[10px] uppercase px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">arquivado</span>` : null}
                          </div>
                          <div class="text-[11px] text-wa-secondary font-mono truncate max-w-[460px]">${g.chat_jid}</div>
                        </td>
                        <td class="px-3 py-2 text-center">
                          <div class="flex flex-col items-center gap-0.5">
                            <${Toggle}
                              checked=${audioOn}
                              disabled=${isSaving || !audioGlobalActive}
                              onChange=${(v) => updateGroup(g.chat_jid, { audio_mode: v ? null : 'off' })}
                            />
                            <span class="text-[10px] ${audioOverridden ? 'text-blue-700' : 'text-wa-secondary'}">
                              ${audioOverridden ? 'customizado' : 'padrão'}
                            </span>
                          </div>
                        </td>
                        <td class="px-3 py-2 text-center">
                          <div class="flex flex-col items-center gap-0.5">
                            <${Toggle}
                              checked=${imageOn}
                              disabled=${isSaving || !imageDefault}
                              onChange=${(v) => updateGroup(g.chat_jid, { image_enabled: v ? null : 0 })}
                            />
                            <span class="text-[10px] ${imageOverridden ? 'text-blue-700' : 'text-wa-secondary'}">
                              ${imageOverridden ? 'customizado' : 'padrão'}
                            </span>
                          </div>
                        </td>
                      </tr>
                    `;
                  })}
                </tbody>
              </table>
            </div>
          `
      }

      <p class="text-[11px] text-wa-secondary mt-4">
        Quando desligado, o plugin retorna <code>False</code> em
        <code>filter.transcription.should_run</code> e o core nem chega
        a chamar a API. O áudio/imagem continua chegando ao histórico
        com seu player nativo. Para forçar a transcrição num grupo
        quando o padrão global está desligado, ative o global em
        <strong>Settings → Transcrição</strong> primeiro.
      </p>
    </div>
  `;
}
