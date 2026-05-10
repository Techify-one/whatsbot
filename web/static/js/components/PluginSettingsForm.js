// Renders a Pydantic-derived JSON Schema as an editable form.
// Supports string / integer / number / boolean and string-enum fields
// (Pydantic Valves cover all common cases). Anything more complex falls
// through to a JSON textarea so the user is never blocked.

import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';
import { authHeaders, handleUnauthorized } from '../services/api.js';

const html = htm.bind(h);


function fieldType(prop) {
  if (Array.isArray(prop.enum)) return 'enum';
  if (prop.type === 'boolean') return 'boolean';
  if (prop.type === 'integer') return 'integer';
  if (prop.type === 'number') return 'number';
  if (prop.type === 'string') return 'string';
  return 'json';
}


function FieldInput({ name, prop, value, onChange }) {
  const t = fieldType(prop);
  const baseCls = "w-full border border-wa-border rounded px-3 py-2 text-[14px] focus:outline-none focus:border-wa-teal";

  if (t === 'enum') {
    return html`
      <select class=${baseCls} value=${value ?? ''} onChange=${e => onChange(e.target.value)}>
        ${prop.enum.map(opt => html`<option value=${opt}>${opt}</option>`)}
      </select>
    `;
  }
  if (t === 'boolean') {
    return html`
      <label class="inline-flex items-center gap-2">
        <input type="checkbox" checked=${!!value} onChange=${e => onChange(e.target.checked)} />
        <span class="text-[14px] text-wa-secondary">${value ? 'Ativado' : 'Desativado'}</span>
      </label>
    `;
  }
  if (t === 'integer' || t === 'number') {
    return html`
      <input type="number" step=${t === 'integer' ? '1' : 'any'}
        min=${prop.minimum ?? prop.exclusiveMinimum ?? undefined}
        max=${prop.maximum ?? prop.exclusiveMaximum ?? undefined}
        class=${baseCls}
        value=${value ?? ''}
        onInput=${e => {
          const v = e.target.value;
          if (v === '') return onChange(null);
          onChange(t === 'integer' ? parseInt(v, 10) : parseFloat(v));
        }}
      />
    `;
  }
  if (t === 'string') {
    const isLong = (prop.description || '').length > 80 || (value && String(value).length > 60);
    if (isLong) {
      return html`
        <textarea class="${baseCls} font-mono" rows="4"
          value=${value ?? ''} onInput=${e => onChange(e.target.value)} />
      `;
    }
    return html`
      <input type="text" class=${baseCls} value=${value ?? ''}
        onInput=${e => onChange(e.target.value)} />
    `;
  }
  // fallback: JSON
  return html`
    <textarea class="${baseCls} font-mono" rows="3"
      value=${JSON.stringify(value)}
      onInput=${e => {
        try { onChange(JSON.parse(e.target.value)); } catch (_) { /* ignore until valid */ }
      }}
    />
  `;
}


export function PluginSettingsForm({ pluginId, onSaved }) {
  const [schema, setSchema] = useState(null);
  const [values, setValues] = useState({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [feedback, setFeedback] = useState(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(`/api/plugins/${pluginId}/settings`, { headers: authHeaders() });
      if (r.status === 401) { handleUnauthorized(); throw new Error('Não autenticado.'); }
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'failed to load');
      setSchema(data.data.schema);
      setValues(data.data.values || {});
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, [pluginId]);

  async function save() {
    setSaving(true);
    setError(null);
    setFeedback(null);
    try {
      const r = await fetch(`/api/plugins/${pluginId}/settings`, {
        method: 'PUT',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(values),
      });
      if (r.status === 401) { handleUnauthorized(); throw new Error('Não autenticado.'); }
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'failed to save');
      setValues(data.data.values || values);
      setFeedback('Configurações salvas.');
      if (onSaved) onSaved(data.data.values);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setSaving(false);
    }
  }

  if (loading) return html`<div class="text-wa-secondary">Carregando…</div>`;
  if (!schema) return html`<div class="text-red-600">${error || 'Sem schema disponível.'}</div>`;

  const properties = schema.properties || {};
  const fields = Object.entries(properties);

  return html`
    <div class="space-y-4">
      ${fields.length === 0
        ? html`<div class="text-wa-secondary text-sm">Plugin sem campos de configuração.</div>`
        : fields.map(([name, prop]) => html`
            <div key=${name}>
              <label class="block text-[14px] font-medium text-wa-text mb-1">
                ${prop.title || name}
              </label>
              ${prop.description ? html`
                <div class="text-[12px] text-wa-secondary mb-1.5">${prop.description}</div>
              ` : null}
              <${FieldInput}
                name=${name}
                prop=${prop}
                value=${values[name]}
                onChange=${(v) => setValues({ ...values, [name]: v })}
              />
            </div>
          `)
      }

      ${error ? html`<div class="text-red-600 text-sm">${error}</div>` : null}
      ${feedback ? html`<div class="text-green-700 text-sm">${feedback}</div>` : null}

      <div class="flex gap-2">
        <button
          onClick=${save}
          disabled=${saving}
          class="px-4 py-2 bg-wa-teal text-white rounded text-[14px] disabled:opacity-50"
        >${saving ? 'Salvando…' : 'Salvar'}</button>
      </div>
    </div>
  `;
}

export default PluginSettingsForm;
