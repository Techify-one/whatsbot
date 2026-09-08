import { h } from 'preact';
import htm from 'htm';

const html = htm.bind(h);

// Shows only the changelog entry for the version currently installed — never
// the full history, even if this browser skipped several releases in between.
export function WhatsNewModal({ version, description, onClose }) {
  return html`
    <div
      class="fixed inset-0 bg-black/50 z-[60] flex items-center justify-center p-4"
      onClick=${(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div class="bg-wa-bg rounded-2xl shadow-2xl max-w-md w-full p-6 relative max-h-[80vh] flex flex-col">
        <button
          onClick=${onClose}
          class="absolute top-3 right-3 text-wa-secondary hover:text-wa-text transition-colors p-1 rounded"
          title="Fechar"
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>

        <div class="flex items-center gap-3 mb-3">
          <div class="w-10 h-10 rounded-full bg-wa-teal/10 flex items-center justify-center shrink-0">
            <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" class="text-wa-teal" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2l2.4 7.2H22l-6 4.6 2.4 7.2L12 16.4 5.6 21l2.4-7.2-6-4.6h7.6z"/></svg>
          </div>
          <div>
            <h2 class="text-base font-semibold text-wa-text">Novidades do WhatsBot</h2>
            <p class="text-xs text-wa-secondary">
              Você atualizou para a <span class="font-mono font-semibold text-wa-teal">v${version}</span>
            </p>
          </div>
        </div>

        <div class="text-sm text-wa-text whitespace-pre-wrap overflow-y-auto pr-1 flex-1">
          ${description || 'Sem detalhes para esta versão.'}
        </div>

        <button
          onClick=${onClose}
          class="mt-4 w-full text-center py-2.5 px-4 bg-wa-teal hover:bg-wa-tealDark text-white font-medium rounded-lg transition-colors"
        >
          Entendi
        </button>
      </div>
    </div>
  `;
}
