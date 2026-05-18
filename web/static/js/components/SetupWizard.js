import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import htm from 'htm';
import { fetchQrBlob, refreshQr, setupRequestKey, setupKeyStatus } from '../services/api.js';
import { formatPhone } from './QRCode.js';

const html = htm.bind(h);

const STEPS = ['Conectar', 'Chave de API', 'Testar'];
const MAX_POLL_ATTEMPTS = 75; // ~2.5 min at 2s interval — beyond the Techify TTL

function StepDots({ step }) {
  return html`
    <div class="flex items-center justify-center gap-2 mb-6">
      ${STEPS.map((label, i) => {
        const n = i + 1;
        const done = n < step;
        const active = n === step;
        return html`
          <div key=${n} class="flex items-center gap-2">
            <div class="flex items-center gap-1.5">
              <div class="w-6 h-6 rounded-full flex items-center justify-center text-xs font-semibold ${
                done ? 'bg-wa-teal text-white'
                : active ? 'bg-wa-teal text-white'
                : 'bg-wa-panel text-wa-secondary border border-wa-border'}">
                ${done ? '✓' : n}
              </div>
              <span class="text-xs ${active ? 'text-wa-text font-medium' : 'text-wa-secondary'}">${label}</span>
            </div>
            ${n < STEPS.length ? html`<div class="w-5 h-px bg-wa-border"></div>` : null}
          </div>
        `;
      })}
    </div>
  `;
}

export function SetupWizard({ status, qrAvailable, qrVersion, config, onComplete, canClose, onClose }) {
  const [step, setStep] = useState(1);

  // ── Step 1: QR / connection ──────────────────────────────────────
  const [qrImgSrc, setQrImgSrc] = useState(null);
  const [qrImgError, setQrImgError] = useState(false);
  const advancedRef = useRef(false);

  useEffect(() => {
    if (step !== 1 || status.connected) return;
    if (qrAvailable && qrVersion) {
      let cancelled = false;
      fetchQrBlob().then(url => {
        if (!cancelled && url) {
          setQrImgSrc(prev => { if (prev) URL.revokeObjectURL(prev); return url; });
          setQrImgError(false);
        }
      });
      return () => { cancelled = true; };
    }
  }, [step, status.connected, qrAvailable, qrVersion]);

  useEffect(() => () => { if (qrImgSrc) URL.revokeObjectURL(qrImgSrc); }, []);

  // Auto-advance to step 2 once WhatsApp is connected.
  useEffect(() => {
    if (step === 1 && status.connected && !advancedRef.current) {
      advancedRef.current = true;
      const t = setTimeout(() => setStep(2), 1200);
      return () => clearTimeout(t);
    }
  }, [step, status.connected]);

  // ── Step 2: API key provisioning ─────────────────────────────────
  // keyState: 'idle' | 'requesting' | 'polling' | 'ready' | 'error'
  const [keyState, setKeyState] = useState('idle');
  const [keyError, setKeyError] = useState('');
  const pollAttemptsRef = useRef(0);
  const hasKey = !!(config && config.openrouter_api_key && config.openrouter_api_key.length > 0);

  async function startProvisioning() {
    setKeyError('');
    setKeyState('requesting');
    let res;
    try { res = await setupRequestKey(); } catch (e) { res = null; }
    if (!res || !res.ok) {
      setKeyError((res && res.error) || 'Não foi possível solicitar a chave. Tente novamente.');
      setKeyState('error');
      return;
    }
    pollAttemptsRef.current = 0;
    setKeyState('polling');
  }

  // Poll the backend every 2s while in 'polling' state.
  useEffect(() => {
    if (keyState !== 'polling') return;
    let stopped = false;
    const tick = async () => {
      pollAttemptsRef.current += 1;
      let res;
      try { res = await setupKeyStatus(); } catch (e) { res = null; }
      if (stopped) return;
      const st = res && res.ok && res.data ? res.data.status : 'error';
      if (st === 'ready') {
        setKeyState('ready');
      } else if (st === 'expired') {
        setKeyError('A solicitação expirou. Toque para tentar de novo.');
        setKeyState('error');
      } else if (st === 'error') {
        setKeyError('Não conseguimos receber a chave. Toque para tentar de novo.');
        setKeyState('error');
      } else if (pollAttemptsRef.current >= MAX_POLL_ATTEMPTS) {
        setKeyError('A chave não chegou a tempo. Toque para tentar de novo.');
        setKeyState('error');
      }
    };
    const timer = setInterval(tick, 2000);
    return () => { stopped = true; clearInterval(timer); };
  }, [keyState]);

  // Once the key is ready, show the success state briefly then advance.
  useEffect(() => {
    if (keyState !== 'ready') return;
    const t = setTimeout(() => setStep(3), 1800);
    return () => clearTimeout(t);
  }, [keyState]);

  // ── Step 3: test link ────────────────────────────────────────────
  const [copied, setCopied] = useState(false);
  const phone = status.bot_phone || '';
  const waLink = phone ? `https://wa.me/${phone}?text=Oi` : '';

  function handleCopy() {
    if (!waLink) return;
    navigator.clipboard.writeText(waLink).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  // ── Render helpers ───────────────────────────────────────────────
  const btnPrimary = 'px-4 py-2.5 rounded-lg text-sm font-medium bg-wa-teal hover:bg-wa-tealDark text-white transition-colors disabled:opacity-60 disabled:cursor-not-allowed';
  const btnGhost = 'px-4 py-2.5 rounded-lg text-sm font-medium border border-wa-border bg-white hover:bg-wa-panel text-wa-text transition-colors';

  function renderStep1() {
    return html`
      <div class="flex flex-col items-center text-center">
        <h2 class="text-lg font-semibold text-wa-text mb-1">Conecte seu WhatsApp</h2>
        <p class="text-sm text-wa-secondary mb-4">
          Escaneie o código abaixo para o WhatsBot atender no seu número.
        </p>
        <div class="w-[240px] h-[240px] flex items-center justify-center bg-wa-panel rounded-xl overflow-hidden mb-3">
          ${status.connected ? html`
            <div class="text-center">
              <div class="text-5xl mb-2 text-wa-teal">✓</div>
              <div class="text-wa-teal font-semibold">Conectado!</div>
              ${status.bot_name ? html`<div class="text-sm text-wa-text font-medium mt-1">${status.bot_name}</div>` : null}
              ${status.bot_phone ? html`<div class="text-xs text-wa-secondary">${formatPhone(status.bot_phone)}</div>` : null}
            </div>
          ` : qrAvailable && qrImgSrc && !qrImgError ? html`
            <img
              src=${qrImgSrc}
              alt="QR Code"
              class="qr-image w-full h-full object-contain"
              onError=${() => setQrImgError(true)}
            />
          ` : html`
            <div class="text-center text-wa-secondary">
              <div class="animate-pulse-slow text-lg mb-1">...</div>
              <span class="text-sm">Aguardando QR Code...</span>
            </div>
          `}
        </div>
        ${status.connected ? html`
          <p class="text-sm text-wa-secondary">Avançando...</p>
        ` : html`
          <div class="text-xs text-wa-secondary leading-relaxed">
            No celular: <span class="text-wa-text">Configurações → Aparelhos conectados → Conectar um aparelho</span>
          </div>
          <button onClick=${() => refreshQr()} class="text-wa-teal hover:text-wa-tealDark text-xs underline mt-2 transition-colors">
            Atualizar QR Code
          </button>
        `}
      </div>
    `;
  }

  function renderStep2() {
    if (keyState === 'ready') {
      return html`
        <div class="flex flex-col items-center text-center">
          <div class="text-5xl mb-2 text-wa-teal">✓</div>
          <h2 class="text-lg font-semibold text-wa-text mb-1">Chave de API criada!</h2>
          <p class="text-sm text-wa-secondary">
            Sua conta foi criada com <span class="text-wa-teal font-medium">crédito grátis</span> para começar.
          </p>
        </div>
      `;
    }
    if (keyState === 'requesting' || keyState === 'polling') {
      return html`
        <div class="flex flex-col items-center text-center py-2">
          <div class="animate-pulse-slow text-3xl mb-3">⏳</div>
          <h2 class="text-lg font-semibold text-wa-text mb-1">
            ${keyState === 'requesting' ? 'Enviando solicitação...' : 'Criando sua conta...'}
          </h2>
          <p class="text-sm text-wa-secondary">
            ${keyState === 'requesting'
              ? 'Pedindo sua chave de API pelo WhatsApp.'
              : 'Gerando sua chave de API e seu crédito grátis. Isso leva alguns segundos.'}
          </p>
        </div>
      `;
    }
    // 'idle' or 'error'
    return html`
      <div class="flex flex-col items-center text-center">
        <h2 class="text-lg font-semibold text-wa-text mb-1">Criar conta e Ganhar Chave de API</h2>
        <p class="text-sm text-wa-teal font-medium mb-3">+ Crédito Grátis</p>
        <p class="text-sm text-wa-secondary mb-4 leading-relaxed">
          A chave de API conecta o WhatsBot à inteligência artificial. Ao tocar no botão,
          o WhatsBot envia uma mensagem pelo seu WhatsApp e cria sua conta automaticamente —
          você não precisa fazer mais nada.
        </p>
        ${keyState === 'error' && keyError ? html`
          <div class="w-full mb-3 px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-red-600 text-sm">
            ${keyError}
          </div>
        ` : null}
        ${hasKey && keyState === 'idle' ? html`
          <div class="w-full mb-3 px-3 py-2 rounded-lg bg-green-50 border border-green-200 text-green-700 text-sm">
            Você já tem uma chave de API configurada.
          </div>
        ` : null}
      </div>
    `;
  }

  function renderStep3() {
    return html`
      <div class="flex flex-col items-center text-center">
        <div class="text-5xl mb-2">🎉</div>
        <h2 class="text-lg font-semibold text-wa-text mb-1">Tudo pronto!</h2>
        <p class="text-sm text-wa-secondary mb-4">
          Agora é só me chamar no WhatsApp para testar:
        </p>
        <div class="w-full px-4 py-3 rounded-xl bg-wa-panel border border-wa-border mb-3">
          <div class="text-2xl font-bold text-wa-text tracking-wide">
            ${phone ? formatPhone(phone) : 'Carregando número...'}
          </div>
        </div>
        <p class="text-sm text-wa-secondary mb-3">
          Copie o link abaixo e abra no seu WhatsApp para me mandar um “oi”.
        </p>
        <button
          onClick=${handleCopy}
          disabled=${!waLink}
          class="${btnGhost} w-full mb-1 ${copied ? 'border-green-300 bg-green-50 text-green-700' : ''}"
        >
          ${copied ? 'Link copiado!' : 'Copiar link de contato'}
        </button>
        ${waLink ? html`
          <a href=${waLink} target="_blank" rel="noopener noreferrer" class="text-wa-teal hover:text-wa-tealDark text-xs underline">
            ou abrir no WhatsApp agora
          </a>
        ` : null}
      </div>
    `;
  }

  function renderFooter() {
    if (step === 1) {
      return html`<div class="text-xs text-wa-secondary">A próxima etapa abre sozinha após conectar.</div>`;
    }
    if (step === 2) {
      if (keyState === 'idle') {
        return html`
          <div class="flex items-center gap-2">
            ${hasKey ? html`
              <button onClick=${() => setStep(3)} class=${btnGhost}>Pular</button>
              <button onClick=${startProvisioning} class=${btnPrimary}>Gerar nova chave</button>
            ` : html`
              <button onClick=${startProvisioning} class=${btnPrimary}>Criar minha conta e receber a chave</button>
            `}
          </div>
        `;
      }
      if (keyState === 'error') {
        return html`
          <div class="flex items-center gap-2">
            <button onClick=${() => setStep(3)} class=${btnGhost}>Pular por agora</button>
            <button onClick=${startProvisioning} class=${btnPrimary}>Tentar novamente</button>
          </div>
        `;
      }
      // requesting / polling / ready — no action
      return html`<div class="text-xs text-wa-secondary">Aguarde um instante...</div>`;
    }
    // step 3
    return html`<button onClick=${onComplete} class=${btnPrimary}>Concluir</button>`;
  }

  return html`
    <div class="min-h-dvh w-full bg-wa-panel flex items-center justify-center p-4 overflow-auto">
      <div class="bg-white rounded-2xl shadow-2xl max-w-md w-full p-6 sm:p-8 relative">
        ${canClose ? html`
          <button
            onClick=${onClose}
            class="absolute top-3 right-3 text-wa-secondary hover:text-wa-text transition-colors p-1 rounded"
            title="Fechar"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        ` : null}

        <div class="text-center mb-1">
          <h1 class="text-xl font-semibold text-wa-text">Bem-vindo ao WhatsBot</h1>
          <p class="text-sm text-wa-secondary">Vamos configurar em 3 passos rápidos</p>
        </div>
        <div class="mt-5">
          <${StepDots} step=${step} />
        </div>

        <div class="min-h-[300px] flex flex-col justify-center">
          ${step === 1 ? renderStep1() : step === 2 ? renderStep2() : renderStep3()}
        </div>

        <div class="mt-6 flex items-center justify-center">
          ${renderFooter()}
        </div>
      </div>
    </div>
  `;
}
