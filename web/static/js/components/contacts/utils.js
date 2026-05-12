// ── Time formatting ──────────────────────────────────────────────

export function formatTime(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const now = new Date();
  const diffDays = Math.floor((now - d) / 86400000);
  if (diffDays === 0) return d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
  if (diffDays === 1) return 'Ontem';
  if (diffDays < 7) return d.toLocaleDateString('pt-BR', { weekday: 'short' });
  return d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' });
}

export function formatBubbleTime(ts) {
  if (!ts) return '';
  return new Date(ts * 1000).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
}

export function isSameDay(tsA, tsB) {
  if (!tsA || !tsB) return false;
  const a = new Date(tsA * 1000);
  const b = new Date(tsB * 1000);
  return a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate();
}

export function formatDateSeparator(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const now = new Date();
  const startOfDay = (date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const diffDays = Math.round((startOfDay(now) - startOfDay(d)) / 86400000);
  if (diffDays === 0) return 'HOJE';
  if (diffDays === 1) return 'ONTEM';
  if (diffDays >= 2 && diffDays <= 6) {
    return d.toLocaleDateString('pt-BR', { weekday: 'long' });
  }
  const sameYear = d.getFullYear() === now.getFullYear();
  return d.toLocaleDateString('pt-BR', sameYear
    ? { day: 'numeric', month: 'long' }
    : { day: 'numeric', month: 'long', year: 'numeric' });
}
