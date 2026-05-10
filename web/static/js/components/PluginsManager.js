// Plugins management screen — list cards, toggle, settings drawer,
// import (zip upload), export and delete. The Toggle/Delete actions
// trigger a server-side restart; we show a brief "reiniciando…" overlay.

import { h } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import htm from 'htm';
import { PluginSettingsForm } from './PluginSettingsForm.js';
import { authHeaders, handleUnauthorized } from '../services/api.js';

const html = htm.bind(h);


function StatusBadge({ plugin }) {
  if (plugin.error) {
    return html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-red-100 text-red-700">Manifest inválido</span>`;
  }
  if (plugin.load_error) {
    return html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-red-100 text-red-700" title=${plugin.load_error}>Erro ao carregar</span>`;
  }
  if (plugin.enabled && plugin.loaded) {
    return html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-green-100 text-green-700">Ativo</span>`;
  }
  if (plugin.enabled && !plugin.loaded) {
    return html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-yellow-100 text-yellow-800">Ativado (aguardando restart)</span>`;
  }
  return html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-gray-100 text-gray-600">Desativado</span>`;
}


function RestartBanner() {
  return html`
    <div class="fixed inset-0 bg-black/40 z-[60] flex items-center justify-center">
      <div class="bg-white rounded-lg shadow-xl p-6 max-w-sm">
        <div class="flex items-center gap-3">
          <div class="w-6 h-6 border-2 border-wa-teal border-t-transparent rounded-full animate-spin"></div>
          <div>
            <div class="font-medium">Reiniciando o servidor…</div>
            <div class="text-[12px] text-wa-secondary mt-1">A página será recarregada em alguns segundos.</div>
          </div>
        </div>
      </div>
    </div>
  `;
}


export function PluginsManager({ onPluginsChanged }) {
  const [plugins, setPlugins] = useState([]);
  const [apiVersion, setApiVersion] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(null); // plugin id
  const [importing, setImporting] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const fileRef = useRef(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch('/api/plugins', { headers: authHeaders() });
      if (r.status === 401) { handleUnauthorized(); throw new Error('Não autenticado.'); }
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'failed');
      setPlugins(data.data.plugins || []);
      setApiVersion(data.data.whatsbot_api_version || '');
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  // After we trigger a restart, poll /health until it responds again, then reload.
  useEffect(() => {
    if (!restarting) return;
    let cancelled = false;
    const startedAt = Date.now();
    async function poll() {
      while (!cancelled && Date.now() - startedAt < 60_000) {
        try {
          await new Promise(r => setTimeout(r, 1500));
          const r = await fetch('/health', { cache: 'no-store' });
          if (r.ok) { window.location.reload(); return; }
        } catch (_) { /* still down */ }
      }
      if (!cancelled) setRestarting(false);
    }
    poll();
    return () => { cancelled = true; };
  }, [restarting]);

  async function toggle(pid, enable) {
    const action = enable ? 'enable' : 'disable';
    try {
      const r = await fetch(`/api/plugins/${pid}/${action}`, { method: 'POST', headers: authHeaders() });
      if (r.status === 401) { handleUnauthorized(); throw new Error('Não autenticado.'); }
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'falha');
      setRestarting(true);
    } catch (e) {
      alert(`Erro ao ${enable ? 'ativar' : 'desativar'}: ${e.message || e}`);
    }
  }

  async function deletePlugin(pid) {
    if (!confirm(`Remover plugin '${pid}'? A pasta e as tabelas dele serão apagadas.`)) return;
    try {
      const r = await fetch(`/api/plugins/${pid}`, { method: 'DELETE', headers: authHeaders() });
      if (r.status === 401) { handleUnauthorized(); throw new Error('Não autenticado.'); }
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'falha');
      setRestarting(true);
    } catch (e) {
      alert(`Erro ao deletar: ${e.message || e}`);
    }
  }

  async function exportPlugin(pid) {
    try {
      const r = await fetch(`/api/plugins/${pid}/export`, { headers: authHeaders() });
      if (r.status === 401) { handleUnauthorized(); throw new Error('Não autenticado.'); }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const blob = await r.blob();
      const cd = r.headers.get('content-disposition') || '';
      const m = cd.match(/filename="?([^";]+)"?/i);
      const filename = (m && m[1]) || `${pid}.zip`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      alert(`Erro ao exportar: ${e.message || e}`);
    }
  }

  async function importPlugin(file) {
    if (!file) return;
    setImporting(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const r = await fetch('/api/plugins/import', { method: 'POST', body: fd, headers: authHeaders() });
      if (r.status === 401) { handleUnauthorized(); throw new Error('Não autenticado.'); }
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'falha');
      await load();
      alert(`Plugin '${data.data.id}' importado. Ative-o quando estiver pronto.`);
    } catch (e) {
      alert(`Erro ao importar: ${e.message || e}`);
    } finally {
      setImporting(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  }

  if (loading) return html`<div class="text-wa-secondary">Carregando plugins…</div>`;
  if (error) return html`<div class="text-red-600">Erro: ${error}</div>`;

  return html`
    <div>
      ${restarting ? html`<${RestartBanner} />` : null}

      <div class="flex items-center justify-between mb-4">
        <div class="text-[12px] text-wa-secondary">
          API do core: ${apiVersion}
          · ${plugins.length} plugin${plugins.length === 1 ? '' : 's'}
        </div>
        <div>
          <input type="file" ref=${fileRef} accept=".zip" class="hidden"
            onChange=${e => importPlugin(e.target.files && e.target.files[0])} />
          <button
            disabled=${importing}
            onClick=${() => fileRef.current && fileRef.current.click()}
            class="px-3 py-1.5 bg-wa-teal text-white rounded text-[14px] disabled:opacity-50"
          >${importing ? 'Importando…' : 'Importar (.zip)'}</button>
        </div>
      </div>

      ${plugins.length === 0
        ? html`
          <div class="bg-wa-panel border border-wa-border rounded p-6 text-center">
            <div class="font-medium mb-1">Nenhum plugin instalado</div>
            <div class="text-sm text-wa-secondary">
              Coloque um plugin em <code>storages/plugins/&lt;id&gt;/</code> ou importe um arquivo .zip.
            </div>
          </div>`
        : html`
          <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
            ${plugins.map(p => html`
              <div key=${p.id} class="bg-white border border-wa-border rounded-lg p-4">
                <div class="flex items-start justify-between gap-2">
                  <div>
                    <div class="font-medium text-[15px]">${p.name || p.id}</div>
                    <div class="text-[12px] text-wa-secondary">
                      <code>${p.id}</code>${p.version ? html` · v${p.version}` : null}
                      ${p.author ? html` · ${p.author}` : null}
                    </div>
                  </div>
                  <${StatusBadge} plugin=${p} />
                </div>
                ${p.description ? html`
                  <div class="text-[13px] mt-2 text-wa-text">${p.description}</div>
                ` : null}
                ${p.load_error ? html`
                  <div class="mt-2 text-[12px] text-red-700 bg-red-50 border border-red-100 rounded px-2 py-1 break-all">
                    ${p.load_error}
                  </div>
                ` : null}
                <div class="flex flex-wrap gap-2 mt-3">
                  <button
                    onClick=${() => toggle(p.id, !p.enabled)}
                    class="px-3 py-1 text-[13px] rounded ${p.enabled ? 'bg-yellow-500 text-white' : 'bg-green-600 text-white'}"
                  >${p.enabled ? 'Desativar' : 'Ativar'}</button>
                  <button
                    onClick=${() => setSettingsOpen(p.id)}
                    disabled=${!p.loaded}
                    class="px-3 py-1 text-[13px] rounded bg-wa-panel border border-wa-border disabled:opacity-50"
                  >Configurar</button>
                  <button
                    onClick=${() => exportPlugin(p.id)}
                    class="px-3 py-1 text-[13px] rounded bg-wa-panel border border-wa-border"
                  >Exportar</button>
                  <button
                    onClick=${() => deletePlugin(p.id)}
                    class="px-3 py-1 text-[13px] rounded bg-red-50 text-red-700 border border-red-200"
                  >Deletar</button>
                </div>
              </div>
            `)}
          </div>`
      }

      ${settingsOpen ? html`
        <div class="fixed inset-0 bg-black/40 z-50 flex items-center justify-center"
             onClick=${() => setSettingsOpen(null)}>
          <div class="bg-white rounded-lg shadow-xl max-w-lg w-full mx-4 max-h-[80vh] overflow-y-auto"
               onClick=${e => e.stopPropagation()}>
            <div class="border-b border-wa-border px-4 py-3 flex items-center justify-between">
              <div class="font-medium">Configurações — ${settingsOpen}</div>
              <button class="text-wa-secondary hover:text-wa-text"
                      onClick=${() => setSettingsOpen(null)}>×</button>
            </div>
            <div class="p-4">
              <${PluginSettingsForm}
                pluginId=${settingsOpen}
                onSaved=${() => onPluginsChanged && onPluginsChanged()}
              />
            </div>
          </div>
        </div>
      ` : null}
    </div>
  `;
}

export default PluginsManager;
