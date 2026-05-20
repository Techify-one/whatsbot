import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

function formatUsd(value) {
  const v = Number(value || 0);
  return v.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 4, maximumFractionDigits: 4 });
}

export function LowBalanceModal({ balance, threshold, accountUrl, onClose, onSnooze }) {
  const [snooze, setSnooze] = useState(false);

  function handleClose() {
    if (snooze) onSnooze(24 * 60 * 60 * 1000);
    onClose();
  }

  function handleRecharge(e) {
    // Let the link navigate (target=_blank). Apply snooze, then close.
    if (snooze) onSnooze(24 * 60 * 60 * 1000);
    onClose();
  }

  const remaining = balance != null ? formatUsd(balance) : '—';
  const limit = formatUsd(threshold);

  return html`
    <div
      class="fixed inset-0 bg-black/50 z-[60] flex items-center justify-center p-4"
      onClick=${(e) => { if (e.target === e.currentTarget) handleClose(); }}
    >
      <div class="bg-white rounded-2xl shadow-2xl max-w-sm w-full p-6 relative">
        <button
          onClick=${handleClose}
          class="absolute top-3 right-3 text-wa-secondary hover:text-wa-text transition-colors p-1 rounded"
          title="Fechar"
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>

        <div class="flex items-center gap-3 mb-3">
          <div class="w-10 h-10 rounded-full bg-amber-100 flex items-center justify-center shrink-0">
            <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#d97706" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
          </div>
          <h2 class="text-base font-semibold text-wa-text">Seu saldo está acabando</h2>
        </div>

        <p class="text-sm text-wa-secondary mb-3">
          Saldo atual: <span class="font-semibold text-wa-text">${remaining}</span>
          <br/>
          Limite configurado: <span class="text-wa-text">${limit}</span>
        </p>

        <p class="text-sm text-wa-secondary mb-4">
          Para evitar interrupções no atendimento da IA, recarregue agora.
        </p>

        <div class="flex flex-col gap-2">
          ${accountUrl ? html`
            <a
              href=${accountUrl}
              target="_blank"
              rel="noopener noreferrer"
              onClick=${handleRecharge}
              class="w-full text-center py-2.5 px-4 bg-wa-teal hover:bg-wa-tealDark text-white font-medium rounded-lg transition-colors no-underline"
            >
              Recarregar agora
            </a>
          ` : html`
            <div class="w-full text-center py-2.5 px-4 bg-wa-panel text-wa-secondary rounded-lg text-sm">
              URL de recarga não configurada
            </div>
          `}
          <button
            onClick=${handleClose}
            class="w-full text-center py-2.5 px-4 bg-wa-panel hover:bg-wa-hover text-wa-text rounded-lg transition-colors"
          >
            Agora não
          </button>
        </div>

        <label class="flex items-center gap-2 mt-4 text-sm text-wa-text cursor-pointer">
          <input
            type="checkbox"
            checked=${snooze}
            onChange=${(e) => setSnooze(e.target.checked)}
            class="w-4 h-4 rounded border-wa-border accent-wa-teal"
          />
          Não mostrar nas próximas 24h
        </label>
      </div>
    </div>
  `;
}
