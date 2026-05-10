import { h } from 'preact';
import { useState, useEffect, useRef, useCallback } from 'preact/hooks';
import htm from 'htm';
import { sandboxSend, sandboxClear, getLogs, clearLogs } from '../services/api.js';

const html = htm.bind(h);

const LEVEL_COLORS = {
  DEBUG: 'text-gray-400',
  INFO: 'text-blue-600',
  WARNING: 'text-yellow-600',
  ERROR: 'text-red-500',
  CRITICAL: 'text-red-600 font-bold',
};

function LogPanel() {
  const [logs, setLogs] = useState([]);
  const [autoScroll, setAutoScroll] = useState(true);
  const [filter, setFilter] = useState('');
  const logRef = useRef(null);
  const intervalRef = useRef(null);

  const fetchLogs = useCallback(async () => {
    const res = await getLogs(300);
    if (res.ok) setLogs(res.data);
  }, []);

  useEffect(() => {
    fetchLogs();
    intervalRef.current = setInterval(fetchLogs, 2000);
    return () => clearInterval(intervalRef.current);
  }, [fetchLogs]);

  useEffect(() => {
    if (autoScroll && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs, autoScroll]);

  const filtered = filter
    ? logs.filter(l => l.message.toLowerCase().includes(filter.toLowerCase()) || l.level.includes(filter.toUpperCase()))
    : logs;

  async function handleClear() {
    await clearLogs();
    setLogs([]);
  }

  return html`
    <div class="flex flex-col h-full">
      <div class="flex items-center gap-2 mb-2">
        <h3 class="text-sm font-semibold text-wa-text uppercase tracking-wide">Logs</h3>
        <input
          type="text"
          placeholder="Filtrar logs..."
          value=${filter}
          onInput=${(e) => setFilter(e.target.value)}
          class="flex-1 bg-wa-panel border border-wa-border rounded px-2 py-1 text-xs text-wa-text focus:border-wa-teal focus:outline-none"
        />
        <label class="flex items-center gap-1 text-xs text-wa-secondary cursor-pointer select-none">
          <input
            type="checkbox"
            checked=${autoScroll}
            onChange=${(e) => setAutoScroll(e.target.checked)}
            class="rounded border-wa-border accent-wa-teal"
          />
          Auto-scroll
        </label>
        <button
          onClick=${handleClear}
          class="text-xs text-wa-secondary hover:text-red-500 transition-colors px-2 py-1"
        >Limpar</button>
      </div>
      <div
        ref=${logRef}
        class="flex-1 bg-wa-panel rounded border border-wa-border overflow-y-auto font-mono text-xs p-2 min-h-0"
        style="max-height: 300px;"
      >
        ${filtered.length === 0
          ? html`<div class="text-wa-secondary text-center py-8">Nenhum log ainda...</div>`
          : filtered.map((log, i) => html`
            <div key=${i} class="flex gap-2 py-0.5 hover:bg-wa-hover leading-tight">
              <span class="text-wa-secondary shrink-0">${log.ts}</span>
              <span class="shrink-0 w-16 ${LEVEL_COLORS[log.level] || 'text-wa-secondary'}">${log.level}</span>
              <span class="text-wa-secondary shrink-0">${log.name}</span>
              <span class="text-wa-text break-all">${log.message}</span>
            </div>
          `)
        }
      </div>
    </div>
  `;
}

function ChatPanel() {
  const [phone, setPhone] = useState('5511999999999');
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState([]);
  const [sending, setSending] = useState(false);
  const chatRef = useRef(null);

  useEffect(() => {
    if (chatRef.current) {
      chatRef.current.scrollTop = chatRef.current.scrollHeight;
    }
  }, [messages]);

  async function handleSend(e) {
    e.preventDefault();
    const text = input.trim();
    if (!text || sending) return;

    setMessages(prev => [...prev, { role: 'user', content: text, ts: new Date() }]);
    setInput('');
    setSending(true);

    try {
      const res = await sandboxSend(phone, text);
      if (res.ok) {
        const replies = Array.isArray(res.data.replies) && res.data.replies.length
          ? res.data.replies
          : [res.data.reply];
        const newBubbles = replies
          .filter(r => r && r.trim())
          .map(r => ({ role: 'assistant', content: r, ts: new Date() }));
        setMessages(prev => [...prev, ...newBubbles]);
      } else {
        setMessages(prev => [...prev, { role: 'error', content: res.error || 'Erro desconhecido', ts: new Date() }]);
      }
    } catch (err) {
      setMessages(prev => [...prev, { role: 'error', content: `Erro de rede: ${err.message}`, ts: new Date() }]);
    } finally {
      setSending(false);
    }
  }

  async function handleClear() {
    await sandboxClear(phone);
    setMessages([]);
  }

  return html`
    <div class="flex flex-col h-full">
      <!-- Header -->
      <div class="flex items-center gap-2 mb-2">
        <h3 class="text-sm font-semibold text-wa-text uppercase tracking-wide shrink-0">Chat Sandbox</h3>
        <div class="flex items-center gap-1 flex-1">
          <label class="text-xs text-wa-secondary shrink-0">Telefone:</label>
          <input
            type="text"
            value=${phone}
            onInput=${(e) => setPhone(e.target.value)}
            placeholder="5511999999999"
            class="bg-wa-panel border border-wa-border rounded px-2 py-1 text-xs text-wa-text w-36 focus:border-wa-teal focus:outline-none"
          />
        </div>
        <button
          onClick=${handleClear}
          class="text-xs text-wa-secondary hover:text-red-500 transition-colors px-2 py-1"
        >Limpar conversa</button>
      </div>

      <!-- Messages -->
      <div
        ref=${chatRef}
        class="flex-1 bg-wa-panel rounded border border-wa-border overflow-y-auto p-3 space-y-2 min-h-0"
        style="max-height: 400px;"
      >
        ${messages.length === 0
          ? html`<div class="text-wa-secondary text-center py-12 text-sm">
              Envie uma mensagem para testar o bot.<br/>
              <span class="text-xs text-wa-secondary opacity-70">Usa o mesmo pipeline do WhatsApp (AgentHandler.process_message)</span>
            </div>`
          : messages.map((msg, i) => html`
            <div key=${i} class="flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}">
              <div class="max-w-[80%] rounded-lg px-3 py-2 text-sm ${
                msg.role === 'user'
                  ? 'bg-wa-outgoing text-wa-text rounded-br-none'
                  : msg.role === 'error'
                    ? 'bg-red-50 text-red-600 border border-red-200 rounded-bl-none'
                    : 'bg-white text-wa-text rounded-bl-none shadow-sm'
              }">
                <div class="whitespace-pre-wrap break-words">${msg.content}</div>
                <div class="text-[10px] mt-1 text-wa-secondary text-right">
                  ${msg.ts.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                </div>
              </div>
            </div>
          `)
        }
        ${sending ? html`
          <div class="flex justify-start">
            <div class="bg-white rounded-lg px-3 py-2 text-sm text-wa-secondary rounded-bl-none animate-pulse-slow shadow-sm">
              Processando...
            </div>
          </div>
        ` : null}
      </div>

      <!-- Input -->
      <form onSubmit=${handleSend} class="flex gap-2 mt-2">
        <input
          type="text"
          value=${input}
          onInput=${(e) => setInput(e.target.value)}
          placeholder="Digite uma mensagem..."
          autoFocus
          class="flex-1 bg-white border border-wa-border rounded-lg px-3 py-2 text-sm text-wa-text placeholder-wa-secondary focus:border-wa-teal focus:outline-none"
        />
        <button
          type="submit"
          disabled=${sending || !input.trim()}
          class="bg-wa-teal hover:bg-wa-tealDark disabled:bg-wa-panel disabled:text-wa-secondary text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
        >Enviar</button>
      </form>
    </div>
  `;
}

export function Sandbox() {
  return html`
    <div class="space-y-4">
      <!-- Chat -->
      <div class="bg-white rounded-lg p-4 border border-wa-border shadow-sm">
        <${ChatPanel} />
      </div>

      <!-- Logs -->
      <div class="bg-white rounded-lg p-4 border border-wa-border shadow-sm">
        <${LogPanel} />
      </div>
    </div>
  `;
}
