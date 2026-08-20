import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';
import { sendSelfLink } from '../../services/api.js';

const html = htm.bind(h);

// Warning shown before starting a conversation with a number that isn't a
// contact yet. Starting a chat from an unofficial API is the riskiest action
// for the account, so the operator gets a way out: send the wa.me link to their
// own WhatsApp and open the conversation from the phone instead.
export function StartChatWarningModal({ phone, phoneDisplay, onConfirm, onClose }) {
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(null);      // { link, own_phone }
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState(false);

  const link = sent ? sent.link : `https://wa.me/${phone}`;

  const handleDecline = async () => {
    if (sending) return;
    setSending(true);
    setError(null);
    const res = await sendSelfLink(phone);
    setSending(false);
    if (!res.ok) {
      setError(res.error || 'Falha ao enviar o link.');
      return;
    }
    setSent(res.data);
  };

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (e) {
      setError('Não foi possível copiar. Selecione o link manualmente.');
    }
  };

  return html`
    <div
      class="fixed inset-0 z-[130] bg-black/50 flex items-center justify-center p-4"
      onClick=${(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div class="bg-wa-panel rounded-2xl shadow-2xl max-w-md w-full p-6 border border-wa-border">
        <div class="flex items-center gap-3 mb-3">
          <div class="w-10 h-10 rounded-full bg-amber-500/15 flex items-center justify-center shrink-0">
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#d97706" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
          </div>
          <h2 class="text-base font-semibold text-wa-text">Atenção antes de iniciar</h2>
        </div>

        ${!sent ? html`
          <p class="text-[14px] text-wa-secondary mb-3 leading-[20px]">
            Iniciar uma conversa a partir de uma API não oficial pode gerar
            bloqueios no seu número.
          </p>
          <p class="text-[14px] text-wa-secondary mb-4 leading-[20px]">
            O mais seguro é iniciar essa conversa pelo celular.
            Se escolher <span class="text-wa-text font-medium">Não</span>, enviamos o link de <span class="text-wa-text font-medium">${phoneDisplay || phone}</span> para o seu próprio WhatsApp: é só tocar no link no celular e a conversa abre direto.
          </p>

          ${error ? html`<div class="text-red-400 text-[13px] mb-3">${error}</div>` : null}

          <div class="flex gap-2 justify-end">
            <button
              onClick=${handleDecline}
              disabled=${sending}
              class="px-4 py-[8px] rounded-lg text-[14px] bg-wa-teal text-white hover:opacity-90 transition-opacity disabled:opacity-50"
            >${sending ? 'Enviando link...' : 'Não, mandar link pro meu celular'}</button>
            <button
              onClick=${onConfirm}
              disabled=${sending}
              class="px-4 py-[8px] rounded-lg text-[14px] border border-wa-border text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-50"
            >Sim, continuar aqui</button>
          </div>
        ` : html`
          <p class="text-[14px] text-wa-secondary mb-3 leading-[20px]">
            Link enviado para o seu WhatsApp${sent.own_phone ? html` (<span class="text-wa-text">+${sent.own_phone}</span>)` : ''}.
            Abra o app no celular e toque no link para iniciar a conversa.
          </p>

          <div class="flex items-center gap-2 mb-4">
            <input
              type="text"
              readonly
              value=${link}
              onClick=${(e) => e.target.select()}
              class="wa-field flex-1 rounded-lg px-3 py-[8px] text-[13px] outline-none"
            />
            <button
              onClick=${handleCopy}
              class="px-3 py-[8px] rounded-lg text-[13px] border border-wa-border text-wa-text hover:bg-wa-hover transition-colors shrink-0"
            >${copied ? 'Copiado!' : 'Copiar'}</button>
          </div>

          ${error ? html`<div class="text-red-400 text-[13px] mb-3">${error}</div>` : null}

          <div class="flex justify-end">
            <button
              onClick=${onClose}
              class="px-4 py-[8px] rounded-lg text-[14px] bg-wa-teal text-white hover:opacity-90 transition-opacity"
            >Fechar</button>
          </div>
        `}
      </div>
    </div>
  `;
}
