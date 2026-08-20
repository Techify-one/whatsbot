// ── Avatar URL (with cache-busting version) ──────────────────────
// `v` is the cached file's mtime (avatar_v from the API); appending it makes
// the browser re-fetch when the photo changes instead of using the stale image.
export function avatarUrl(phone, v) {
  if (!phone) return null;
  const base = `/statics/avatars/${phone}.jpg`;
  return v ? `${base}?v=${v}` : base;
}

// ── Phone formatting ─────────────────────────────────────────────

// 5585973605591 → +55 (85) 97360-5591
export function formatPhoneDisplay(phone) {
  if (!phone || phone.length < 12) return phone;
  return `+${phone.slice(0, 2)} (${phone.slice(2, 4)}) ${phone.slice(4, 9)}-${phone.slice(9)}`;
}

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

// ── Messages ─────────────────────────────────────────────────────

// True when two messages are the same logical message — used to dedupe a
// WebSocket-delivered message against an optimistic/already-loaded bubble.
export function isSameMessage(a, b) {
  if (!a || !b || a.role !== b.role) return false;
  if (a.ts === b.ts) return true;
  return a.content === b.content && Math.abs((a.ts || 0) - (b.ts || 0)) < 30;
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
