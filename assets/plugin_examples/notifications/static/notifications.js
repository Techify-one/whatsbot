import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';
// Reuse the core notification helpers (served at the app's static root). The
// firing logic stays in the core; this screen only flips the per-device prefs.
import {
  getNotifPref, setNotifPref, requestBrowserPermission,
  browserNotifSupported, browserNotifPermission, playNotificationSound,
} from '/static/js/utils/notifications.js';

const html = htm.bind(h);

export default function NotificationsScreen() {
  // Notification preferences (client-side / per-device, stored in localStorage).
  const [notifTab, setNotifTab] = useState(() => getNotifPref('tab'));
  const [notifBrowser, setNotifBrowser] = useState(() => getNotifPref('browser'));
  const [notifSound, setNotifSound] = useState(() => getNotifPref('sound'));
  const [message, setMessage] = useState('');

  // ── Apply instantly (no Save button) ──────────────────────────────────────
  function toggleNotifTab(value) {
    setNotifPref('tab', value);
    setNotifTab(value);
  }
  function toggleNotifSound(value) {
    setNotifPref('sound', value);
    setNotifSound(value);
    if (value) playNotificationSound();  // give immediate feedback
  }
  async function toggleNotifBrowser(value) {
    if (value) {
      if (!browserNotifSupported()) {
        setMessage('Seu navegador não suporta notificações.');
        return;
      }
      const perm = await requestBrowserPermission();
      if (perm !== 'granted') {
        setMessage('Permissão de notificação negada pelo navegador.');
        setNotifPref('browser', false);
        setNotifBrowser(false);
        return;
      }
    }
    setMessage('');
    setNotifPref('browser', value);
    setNotifBrowser(value);
  }

  return html`
    <div class="flex flex-col gap-4">
        <p class="text-xs text-wa-secondary">
          Preferências deste navegador/dispositivo — aplicadas imediatamente (não dependem de salvar).
        </p>

        ${message ? html`
          <div class="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">${message}</div>
        ` : ''}

        <label class="flex items-center gap-3 text-sm font-semibold text-wa-text cursor-pointer p-3 rounded-lg border ${notifTab ? 'bg-green-50 border-green-200' : 'bg-wa-bg border-wa-border'}">
          <input
            type="checkbox"
            checked=${notifTab}
            onChange=${(e) => toggleNotifTab(e.target.checked)}
            class="w-4 h-4 rounded border-wa-border accent-wa-teal"
          />
          <span class="flex-1">
            Contador na aba do navegador
            <span class="block text-xs font-normal text-wa-secondary">Mostra "(N) WhatsBot" no título da aba com o número de conversas com mensagens novas.</span>
          </span>
        </label>

        <label class="flex items-center gap-3 text-sm font-semibold text-wa-text cursor-pointer p-3 rounded-lg border ${notifBrowser ? 'bg-green-50 border-green-200' : 'bg-wa-bg border-wa-border'}">
          <input
            type="checkbox"
            checked=${notifBrowser}
            onChange=${(e) => toggleNotifBrowser(e.target.checked)}
            class="w-4 h-4 rounded border-wa-border accent-wa-teal"
          />
          <span class="flex-1">
            Notificações do navegador
            <span class="block text-xs font-normal text-wa-secondary">Exibe um aviso na área de trabalho quando chega uma mensagem e a aba não está visível. Requer permissão do navegador.</span>
            ${!browserNotifSupported() ? html`<span class="block text-xs font-normal text-amber-600">Indisponível neste endereço: notificações do navegador exigem HTTPS ou localhost (ex.: abrir por http://localhost:8090). O contador da aba e o som continuam funcionando normalmente.</span>` : ''}
            ${browserNotifSupported() && browserNotifPermission() === 'denied' ? html`<span class="block text-xs font-normal text-red-500">Permissão bloqueada no navegador — libere nas configurações do site para ativar.</span>` : ''}
          </span>
        </label>

        <label class="flex items-center gap-3 text-sm font-semibold text-wa-text cursor-pointer p-3 rounded-lg border ${notifSound ? 'bg-green-50 border-green-200' : 'bg-wa-bg border-wa-border'}">
          <input
            type="checkbox"
            checked=${notifSound}
            onChange=${(e) => toggleNotifSound(e.target.checked)}
            class="w-4 h-4 rounded border-wa-border accent-wa-teal"
          />
          <span class="flex-1">
            Som ao receber mensagem
            <span class="block text-xs font-normal text-wa-secondary">Toca um som quando uma nova mensagem chega.</span>
          </span>
        </label>
        <button
          type="button"
          onClick=${() => playNotificationSound()}
          class="text-xs text-wa-teal hover:underline self-start"
        >Testar som</button>
    </div>
  `;
}
