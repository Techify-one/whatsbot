import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import htm from 'htm';
import { testApiKey, checkForUpdates, performUpdate } from '../services/api.js';
import { ModelSelect } from './ModelSelect.js';

const html = htm.bind(h);

function Section({ title, children }) {
  return html`
    <div class="bg-white rounded-xl p-5 border border-wa-border shadow-sm">
      ${title ? html`
        <h3 class="text-xs font-semibold text-wa-secondary uppercase tracking-wider mb-4">${title}</h3>
      ` : null}
      <div class="flex flex-col gap-4">
        ${children}
      </div>
    </div>
  `;
}

export function ConfigPanel({ config, saving, onSave, onNotify }) {
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState('');
  const [audioModel, setAudioModel] = useState('');
  const [imageModel, setImageModel] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [autoReply, setAutoReply] = useState(true);
  const [maxContext, setMaxContext] = useState(10);
  const [batchDelay, setBatchDelay] = useState(3);
  const [splitMessages, setSplitMessages] = useState(true);
  const [splitDelay, setSplitDelay] = useState(2);
  const [audioTranscriptionEnabled, setAudioTranscriptionEnabled] = useState(true);
  const [imageTranscriptionEnabled, setImageTranscriptionEnabled] = useState(true);
  const [transferAlertEnabled, setTransferAlertEnabled] = useState(true);
  const [transferAlertDuration, setTransferAlertDuration] = useState(5);
  const [maxExecutions, setMaxExecutions] = useState(200);
  const [defaultAiEnabled, setDefaultAiEnabled] = useState(true);
  const [testing, setTesting] = useState(false);
  const [webPassword, setWebPassword] = useState('');
  const [webPasswordConfirm, setWebPasswordConfirm] = useState('');
  const [removePassword, setRemovePassword] = useState(false);

  const [saveSuccess, setSaveSuccess] = useState(false);
  const [promptFullscreen, setPromptFullscreen] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [currentVersion, setCurrentVersion] = useState('');
  const [latestVersion, setLatestVersion] = useState('');
  const [updateAvailable, setUpdateAvailable] = useState(false);
  const [checkingUpdate, setCheckingUpdate] = useState(false);

  // Check for updates on mount
  useEffect(() => {
    fetchVersionInfo();
  }, []);

  async function fetchVersionInfo() {
    setCheckingUpdate(true);
    try {
      const res = await checkForUpdates();
      if (res.ok) {
        setCurrentVersion(res.data.current_version || '');
        setLatestVersion(res.data.latest_version || '');
        setUpdateAvailable(res.data.update_available || false);
      }
    } catch (e) {}
    setCheckingUpdate(false);
  }

  // Populate form when config loads
  useEffect(() => {
    if (config) {
      setApiKey(''); // Don't show masked key in input
      setModel(config.model || '');
      setAudioModel(config.audio_model || '');
      setImageModel(config.image_model || '');
      setSystemPrompt(config.system_prompt || '');
      setAutoReply(config.auto_reply ?? true);
      setMaxContext(config.max_context_messages ?? 10);
      setBatchDelay(config.message_batch_delay ?? 3);
      setSplitMessages(config.split_messages ?? true);
      setSplitDelay(config.split_message_delay ?? 2);
      setAudioTranscriptionEnabled(config.audio_transcription_enabled ?? true);
      setImageTranscriptionEnabled(config.image_transcription_enabled ?? true);
      setTransferAlertEnabled(config.transfer_alert_enabled ?? true);
      setTransferAlertDuration(config.transfer_alert_duration ?? 5);
      setMaxExecutions(config.max_executions ?? 200);
      setDefaultAiEnabled(config.default_ai_enabled ?? true);
    }
  }, [config]);

  const [testResult, setTestResult] = useState(null); // {ok, message}

  async function handleTestKey() {
    const key = apiKey.trim();
    if (!key) {
      onNotify('Insira uma API key primeiro.');
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const res = await testApiKey(key);
      if (res.ok) {
        setTestResult({ ok: res.data.valid, message: res.data.message });
        onNotify(res.data.message);
        // Auto-save when key is valid
        if (res.data.valid) {
          await onSave({ openrouter_api_key: key });
        }
      } else {
        setTestResult({ ok: false, message: res.error || 'Erro ao testar.' });
        onNotify(res.error || 'Erro ao testar.');
      }
    } catch {
      setTestResult({ ok: false, message: 'Erro de conexão.' });
      onNotify('Erro de conexão.');
    }
    setTesting(false);
  }

  const handleUpdate = async () => {
    if (!confirm('Deseja atualizar o WhatsBot para a versão mais recente?\nO servidor precisará ser reiniciado após a atualização.')) return;
    setUpdating(true);
    try {
      const res = await performUpdate();
      if (res.ok) {
        onNotify(res.data?.message || 'Atualização concluída! Reinicie o servidor.');
        await fetchVersionInfo();
      } else {
        onNotify(res.error || 'Erro ao atualizar.');
      }
    } catch (e) {
      onNotify('Erro de conexão ao atualizar.');
    } finally {
      setUpdating(false);
    }
  };

  async function handleSave() {
    const data = {
      model: model.trim() || 'deepseek/deepseek-v4-pro',
      audio_model: audioModel.trim() || 'google/gemini-3-flash-preview',
      image_model: imageModel.trim() || 'google/gemini-3-flash-preview',
      system_prompt: systemPrompt,
      auto_reply: autoReply,
      max_context_messages: parseInt(maxContext, 10) || 10,
      message_batch_delay: isNaN(parseFloat(batchDelay)) ? 0 : parseFloat(batchDelay),
      split_messages: splitMessages,
      split_message_delay: isNaN(parseFloat(splitDelay)) ? 0 : parseFloat(splitDelay),
      audio_transcription_enabled: audioTranscriptionEnabled,
      image_transcription_enabled: imageTranscriptionEnabled,
      transfer_alert_enabled: transferAlertEnabled,
      transfer_alert_duration: parseInt(transferAlertDuration, 10) || 5,
      max_executions: parseInt(maxExecutions, 10) || 200,
      default_ai_enabled: defaultAiEnabled,
    };
    // Only include api_key if user typed a new one
    if (apiKey.trim()) {
      data.openrouter_api_key = apiKey.trim();
    }
    // Handle password change/removal
    if (removePassword) {
      data.web_password = '';
    } else if (webPassword.trim()) {
      if (webPassword !== webPasswordConfirm) {
        onNotify('As senhas não coincidem.');
        return;
      }
      data.web_password = webPassword;
    }
    setSaveSuccess(false);
    const result = await onSave(data);
    if (result !== false) {
      setSaveSuccess(true);
      setWebPassword('');
      setWebPasswordConfirm('');
      setRemovePassword(false);
      setTimeout(() => setSaveSuccess(false), 3000);
    }
  }

  if (!config) {
    return html`<div class="bg-white rounded-xl p-5 animate-pulse-slow text-wa-secondary border border-wa-border">Carregando...</div>`;
  }

  return html`
    <div class="flex flex-col gap-4 flex-1">

      <!-- Section: Automacao -->
      <${Section} title="Automação">
        <label class="flex items-center gap-3 text-sm font-semibold text-wa-text cursor-pointer p-3 rounded-lg border ${autoReply ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'}">
          <input
            type="checkbox"
            checked=${autoReply}
            onChange=${(e) => setAutoReply(e.target.checked)}
            class="w-4 h-4 rounded border-wa-border accent-wa-teal"
          />
          Ativar agente de IA para responder mensagens
        </label>

        <label class="flex items-center gap-3 text-sm font-semibold text-wa-text cursor-pointer p-3 rounded-lg border ${defaultAiEnabled ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'}">
          <input
            type="checkbox"
            checked=${defaultAiEnabled}
            onChange=${(e) => setDefaultAiEnabled(e.target.checked)}
            class="w-4 h-4 rounded border-wa-border accent-wa-teal"
          />
          IA ativada por padrão para novos contatos
        </label>
      <//>

      <!-- Section: API e Modelos -->
      <${Section} title="API e Modelos">
        <!-- API Key -->
        <div>
          <label class="block text-sm font-semibold text-wa-text mb-1">API Key OpenRouter</label>
          <div class="flex gap-2">
            <input
              type="password"
              value=${apiKey}
              onInput=${(e) => setApiKey(e.target.value)}
              placeholder=${config.openrouter_api_key || 'sk-or-...'}
              class="flex-1 bg-wa-panel text-wa-text px-3 py-2 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none"
            />
            <button
              onClick=${handleTestKey}
              disabled=${testing}
              class="px-4 py-2 bg-wa-panel hover:bg-wa-hover disabled:opacity-50 text-wa-text text-sm rounded-lg transition-colors whitespace-nowrap border border-wa-border"
            >
              ${testing ? '...' : 'Testar'}
            </button>
          </div>
          ${testResult ? html`
            <p class="text-xs mt-1 ${testResult.ok ? 'text-green-600' : 'text-red-500'}">
              ${testResult.ok ? '\u2713' : '\u2717'} ${testResult.message}
            </p>
          ` : config.openrouter_api_key ? html`
            <p class="text-xs mt-1 text-wa-secondary">Chave salva: ${config.openrouter_api_key}</p>
          ` : null}
        </div>

        <!-- Model -->
        <div>
          <label class="block text-sm font-semibold text-wa-text mb-1">Modelo de IA (chat)</label>
          <${ModelSelect}
            value=${model}
            onChange=${setModel}
            placeholder="deepseek/deepseek-v4-pro"
          />
        </div>

        <!-- Audio & Image models -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label class="block text-sm font-semibold text-wa-text mb-1">Modelo transcrição áudio</label>
            <${ModelSelect}
              value=${audioModel}
              onChange=${setAudioModel}
              filterModality="audio"
              placeholder="google/gemini-3-flash-preview"
            />
            <span class="text-xs text-wa-secondary">Modelo com suporte a áudio</span>
            <label class="flex items-center gap-2 mt-2 cursor-pointer">
              <input
                type="checkbox"
                checked=${audioTranscriptionEnabled}
                onChange=${(e) => setAudioTranscriptionEnabled(e.target.checked)}
                class="accent-wa-teal w-4 h-4"
              />
              <span class="text-sm text-wa-text">Ativar transcrição de áudio</span>
            </label>
          </div>
          <div>
            <label class="block text-sm font-semibold text-wa-text mb-1">Modelo descrição imagem</label>
            <${ModelSelect}
              value=${imageModel}
              onChange=${setImageModel}
              filterModality="image"
              placeholder="google/gemini-3-flash-preview"
            />
            <span class="text-xs text-wa-secondary">Modelo com suporte a visão</span>
            <label class="flex items-center gap-2 mt-2 cursor-pointer">
              <input
                type="checkbox"
                checked=${imageTranscriptionEnabled}
                onChange=${(e) => setImageTranscriptionEnabled(e.target.checked)}
                class="accent-wa-teal w-4 h-4"
              />
              <span class="text-sm text-wa-text">Ativar transcrição de imagem</span>
            </label>
          </div>
        </div>
      <//>

      <!-- Section: System Prompt -->
      <${Section} title="System Prompt">
        <div class="flex-1 flex flex-col">
          <div class="flex items-center justify-between mb-1">
            <label class="block text-sm font-semibold text-wa-text">Prompt</label>
            <button
              type="button"
              onClick=${() => setPromptFullscreen(true)}
              class="text-wa-secondary hover:text-wa-teal transition-colors p-1 rounded"
              title="Abrir editor em tela cheia"
            >
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/></svg>
            </button>
          </div>
          <textarea
            value=${systemPrompt}
            onInput=${(e) => setSystemPrompt(e.target.value)}
            rows="4"
            class="w-full flex-1 bg-wa-panel text-wa-text px-3 py-2 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none resize-none"
          ></textarea>
        </div>
      <//>

      <!-- Fullscreen Prompt Editor -->
      ${promptFullscreen ? html`
        <div class="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick=${(e) => { if (e.target === e.currentTarget) setPromptFullscreen(false); }}>
          <div class="bg-white w-full h-full rounded-xl flex flex-col shadow-2xl overflow-hidden">
            <div class="flex items-center justify-between px-5 py-3 border-b border-wa-border">
              <h2 class="text-sm font-semibold text-wa-text">System Prompt</h2>
              <button
                type="button"
                onClick=${() => setPromptFullscreen(false)}
                class="text-wa-secondary hover:text-wa-text transition-colors p-1 rounded"
                title="Fechar"
              >
                <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              </button>
            </div>
            <textarea
              value=${systemPrompt}
              onInput=${(e) => setSystemPrompt(e.target.value)}
              class="flex-1 w-full bg-white text-wa-text px-5 py-4 text-sm leading-relaxed focus:outline-none resize-none"
              autofocus
            ></textarea>
          </div>
        </div>
      ` : null}

      <!-- Section: Comportamento -->
      <${Section} title="Comportamento">
        <!-- Context & Batch Settings -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label class="block text-sm font-semibold text-wa-text mb-1">Mensagens de contexto</label>
            <input
              type="number"
              min="2"
              max="100"
              value=${maxContext}
              onInput=${(e) => setMaxContext(e.target.value)}
              class="w-full bg-wa-panel text-wa-text px-3 py-2 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none"
            />
            <span class="text-xs text-wa-secondary">Qtd de msgs enviadas ao LLM</span>
          </div>
          <div>
            <label class="block text-sm font-semibold text-wa-text mb-1">Agrupar mensagens (s)</label>
            <input
              type="number"
              min="0"
              max="30"
              step="0.5"
              value=${batchDelay}
              onInput=${(e) => setBatchDelay(e.target.value)}
              class="w-full bg-wa-panel text-wa-text px-3 py-2 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none"
            />
            <span class="text-xs text-wa-secondary">Espera antes de responder</span>
          </div>
        </div>

        <!-- Split Messages -->
        <div class="flex flex-col gap-2 p-3 bg-wa-panel rounded-lg border border-wa-border">
          <label class="flex items-center gap-2 text-sm font-semibold text-wa-text cursor-pointer">
            <input
              type="checkbox"
              checked=${splitMessages}
              onChange=${(e) => setSplitMessages(e.target.checked)}
              class="w-4 h-4 rounded border-wa-border accent-wa-teal"
            />
            Mensagens picadas (dividir resposta)
          </label>
          <span class="text-xs text-wa-secondary">Divide a resposta da IA em várias mensagens curtas, como uma conversa natural</span>
          ${splitMessages ? html`
            <div class="mt-1">
              <label class="block text-xs font-medium text-wa-text mb-1">Intervalo entre mensagens (s)</label>
              <input
                type="number"
                min="0"
                max="10"
                step="0.5"
                value=${splitDelay}
                onInput=${(e) => setSplitDelay(e.target.value)}
                class="w-32 bg-white text-wa-text px-3 py-1.5 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none"
              />
            </div>
          ` : null}
        </div>

        <!-- Transfer Alert -->
        <div class="flex flex-col gap-2 p-3 bg-wa-panel rounded-lg border border-wa-border">
          <label class="flex items-center gap-2 text-sm font-semibold text-wa-text cursor-pointer">
            <input
              type="checkbox"
              checked=${transferAlertEnabled}
              onChange=${(e) => setTransferAlertEnabled(e.target.checked)}
              class="w-4 h-4 rounded border-wa-border accent-wa-teal"
            />
            Alerta sonoro ao transferir para humano
          </label>
          <span class="text-xs text-wa-secondary">Emite um alerta sonoro quando a IA transfere o atendimento para um humano</span>
          ${transferAlertEnabled ? html`
            <div class="mt-1">
              <label class="block text-xs font-medium text-wa-text mb-1">Duração do alerta (segundos)</label>
              <input
                type="number"
                min="1"
                max="30"
                step="1"
                value=${transferAlertDuration}
                onInput=${(e) => setTransferAlertDuration(e.target.value)}
                class="w-32 bg-white text-wa-text px-3 py-1.5 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none"
              />
            </div>
          ` : null}
        </div>
      <//>

      <!-- Section: Avancado -->
      <${Section} title="Avançado">
        <!-- Max Executions -->
        <div>
          <label class="block text-sm font-semibold text-wa-text mb-1">Execuções salvas</label>
          <input
            type="number"
            min="10"
            max="10000"
            step="10"
            value=${maxExecutions}
            onInput=${(e) => setMaxExecutions(e.target.value)}
            class="w-full bg-wa-panel text-wa-text px-3 py-2 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none"
          />
          <span class="text-xs text-wa-secondary">Quantidade máxima de execuções e payloads mantidos no banco</span>
        </div>

        <!-- Panel Password -->
        <div class="flex flex-col gap-2 p-3 bg-wa-panel rounded-lg border border-wa-border">
          <div class="flex items-center justify-between">
            <label class="text-sm font-semibold text-wa-text">Senha do Painel</label>
            ${config.has_password ? html`
              <span class="text-xs bg-wa-teal text-white px-2 py-0.5 rounded-full">Ativa</span>
            ` : html`
              <span class="text-xs bg-wa-secondary/20 text-wa-secondary px-2 py-0.5 rounded-full">Desativada</span>
            `}
          </div>
          <span class="text-xs text-wa-secondary">Protege o acesso ao painel web com senha</span>
          ${!removePassword ? html`
            <input
              type="password"
              value=${webPassword}
              onInput=${(e) => setWebPassword(e.target.value)}
              placeholder=${config.has_password ? 'Nova senha (deixe vazio para manter)' : 'Definir senha'}
              class="w-full bg-white text-wa-text px-3 py-2 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none"
            />
            ${webPassword ? html`
              <input
                type="password"
                value=${webPasswordConfirm}
                onInput=${(e) => setWebPasswordConfirm(e.target.value)}
                placeholder="Confirmar senha"
                class="w-full bg-white text-wa-text px-3 py-2 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none ${webPassword && webPasswordConfirm && webPassword !== webPasswordConfirm ? 'border-red-400' : ''}"
              />
              ${webPassword && webPasswordConfirm && webPassword !== webPasswordConfirm ? html`
                <span class="text-xs text-red-500">As senhas não coincidem</span>
              ` : null}
            ` : null}
          ` : null}
          ${config.has_password ? html`
            <label class="flex items-center gap-2 text-sm text-red-600 cursor-pointer mt-1">
              <input
                type="checkbox"
                checked=${removePassword}
                onChange=${(e) => { setRemovePassword(e.target.checked); if (e.target.checked) { setWebPassword(''); setWebPasswordConfirm(''); } }}
                class="w-4 h-4 rounded border-wa-border accent-red-600"
              />
              Remover senha
            </label>
          ` : null}
        </div>

        <!-- Update -->
        <div class="p-3 bg-wa-panel rounded-lg border border-wa-border">
          <div class="flex items-center justify-between">
            <div>
              <label class="text-sm font-semibold text-wa-text">Atualizar WhatsBot</label>
              <div class="flex items-center gap-3 mt-1.5">
                <span class="text-xs text-wa-secondary">
                  Atual: <span class="font-mono font-semibold text-wa-text">${currentVersion || '...'}</span>
                </span>
                ${latestVersion ? html`
                  <span class="text-xs text-wa-secondary">
                    Última: <span class="font-mono font-semibold ${updateAvailable ? 'text-blue-600' : 'text-green-600'}">${latestVersion}</span>
                  </span>
                ` : null}
                ${!checkingUpdate && !updateAvailable && latestVersion ? html`
                  <span class="text-xs text-green-600 font-medium">Atualizado</span>
                ` : null}
                ${updateAvailable ? html`
                  <span class="text-xs text-blue-600 font-medium">Nova versão disponível</span>
                ` : null}
              </div>
            </div>
            <div class="flex items-center gap-2 ml-4">
              <button
                onClick=${fetchVersionInfo}
                disabled=${checkingUpdate || updating}
                class="px-3 py-2 bg-gray-100 hover:bg-gray-200 disabled:opacity-50 text-wa-text text-sm rounded-lg transition-colors"
                title="Verificar atualizações"
              >
                ${checkingUpdate ? '...' : 'Verificar'}
              </button>
              ${updateAvailable ? html`
                <button
                  onClick=${handleUpdate}
                  disabled=${updating}
                  class="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-sm font-medium rounded-lg transition-colors whitespace-nowrap"
                >
                  ${updating ? 'Atualizando...' : 'Atualizar'}
                </button>
              ` : null}
            </div>
          </div>
        </div>
      <//>

      <!-- Save Button (sticky) -->
      <div class="sticky bottom-0 z-10 bg-wa-panel pt-2 pb-1">
        <button
          onClick=${handleSave}
          disabled=${saving}
          class="w-full py-2.5 ${saveSuccess ? 'bg-green-600' : 'bg-wa-teal hover:bg-wa-tealDark'} disabled:opacity-50 text-white font-medium rounded-lg transition-colors shadow-sm"
        >
          ${saving ? 'Salvando...' : saveSuccess ? '\u2713 Salvo!' : 'Salvar Configurações'}
        </button>
      </div>
    </div>
  `;
}
