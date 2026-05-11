/**
 * REST API client for WhatsBot backend.
 */

const BASE = '';

function _getToken() {
  return localStorage.getItem('whatsbot_token') || '';
}

function _authHeaders(headers = {}) {
  const token = _getToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return headers;
}

export function authHeaders(extra = {}) {
  return _authHeaders({ ...extra });
}

export function handleUnauthorized() {
  localStorage.removeItem('whatsbot_token');
  window.dispatchEvent(new Event('whatsbot:unauthorized'));
}

async function request(method, path, body) {
  const opts = {
    method,
    headers: _authHeaders({ 'Content-Type': 'application/json' }),
  };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(`${BASE}${path}`, opts);
  if (res.status === 401) {
    localStorage.removeItem('whatsbot_token');
    window.dispatchEvent(new Event('whatsbot:unauthorized'));
    return { ok: false, error: 'Não autenticado.' };
  }
  return res.json();
}

export async function getConfig() {
  return request('GET', '/api/config');
}

export async function saveConfig(config) {
  return request('PUT', '/api/config', config);
}

export async function testApiKey(apiKey) {
  return request('POST', '/api/config/test-key', { api_key: apiKey });
}

export async function getStatus() {
  return request('GET', '/api/status');
}

export async function reconnect() {
  return request('POST', '/api/whatsapp/reconnect');
}

export async function logout() {
  return request('POST', '/api/whatsapp/logout');
}

export async function fetchQrBlob() {
  const res = await fetch(`${BASE}/api/qr?t=${Date.now()}`, {
    headers: _authHeaders(),
  });
  if (!res.ok) return null;
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

export async function refreshQr() {
  return request('POST', '/api/qr/refresh');
}

// ── Sandbox ────────────────────────────────────────────────────────

export async function sandboxSend(phone, message) {
  return request('POST', '/api/sandbox/send', { phone, message });
}

export async function sandboxClear(phone) {
  return request('POST', '/api/sandbox/clear', { phone: phone || '' });
}

// ── Contacts ──────────────────────────────────────────────────────

export async function getContacts(q = '', archived = false) {
  const params = [];
  if (archived) params.push('archived=true');
  if (q) params.push(`q=${encodeURIComponent(q)}`);
  const query = params.length ? `?${params.join('&')}` : '';
  return request('GET', `/api/contacts${query}`);
}

export async function getContact(phone, markRead = true) {
  const qs = markRead ? '' : '?mark_read=false';
  return request('GET', `/api/contacts/${encodeURIComponent(phone)}${qs}`);
}

export async function deleteContact(phone) {
  return request('DELETE', `/api/contacts/${encodeURIComponent(phone)}`);
}

export async function archiveContact(phone, archived) {
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/archive`, { archived });
}

export async function sendMessage(phone, message) {
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/send`, { message });
}

export async function retrySend(phone, message) {
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/retry-send`, { message });
}

export async function sendPrivateMessage(phone, text, opts = {}) {
  const body = { text };
  if (opts.aiRead !== undefined) body.ai_read = !!opts.aiRead;
  if (opts.aiReply !== undefined) body.ai_reply = !!opts.aiReply;
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/private-message`, body);
}

export async function markAsRead(phone) {
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/read`);
}

export async function updateContactInfo(phone, info) {
  return request('PUT', `/api/contacts/${encodeURIComponent(phone)}/info`, info);
}

export async function toggleContactAI(phone, enabled) {
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/toggle-ai`, { enabled });
}

export async function sendImage(phone, file, caption = '') {
  const form = new FormData();
  form.append('image', file);
  form.append('caption', caption);
  const res = await fetch(`${BASE}/api/contacts/${encodeURIComponent(phone)}/send-image`, {
    method: 'POST',
    headers: _authHeaders(),
    body: form,
  });
  if (res.status === 401) {
    localStorage.removeItem('whatsbot_token');
    window.dispatchEvent(new Event('whatsbot:unauthorized'));
    return { ok: false, error: 'Não autenticado.' };
  }
  return res.json();
}

export async function sendAudio(phone, blob, filename = 'voice.ogg') {
  const form = new FormData();
  form.append('audio', blob, filename);
  const res = await fetch(`${BASE}/api/contacts/${encodeURIComponent(phone)}/send-audio`, {
    method: 'POST',
    headers: _authHeaders(),
    body: form,
  });
  if (res.status === 401) {
    localStorage.removeItem('whatsbot_token');
    window.dispatchEvent(new Event('whatsbot:unauthorized'));
    return { ok: false, error: 'Não autenticado.' };
  }
  return res.json();
}

export async function sendPresence(phone, action = 'start') {
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/presence`, { action });
}

export async function checkPhone(phone) {
  return request('POST', '/api/contacts/check-phone', { phone });
}

// ── Tags ─────────────────────────────────────────────────────────────

export async function getTags() {
  return request('GET', '/api/tags');
}

export async function createTag(name, color) {
  return request('POST', '/api/tags', { name, color });
}

export async function updateTag(name, data) {
  return request('PUT', `/api/tags/${encodeURIComponent(name)}`, data);
}

export async function deleteTag(name) {
  return request('DELETE', `/api/tags/${encodeURIComponent(name)}`);
}

export async function updateContactTags(phone, tags) {
  return request('PUT', `/api/contacts/${encodeURIComponent(phone)}/tags`, { tags });
}

// ── Models ──────────────────────────────────────────────────────────

export async function getModels() {
  return request('GET', '/api/models');
}

// ── Logs ───────────────────────────────────────────────────────────

export async function getLogs(limit = 200) {
  return request('GET', `/api/logs?limit=${limit}`);
}

export async function clearLogs() {
  return request('DELETE', '/api/logs');
}

// ── Executions ───────────────────────────────────────────────────

export async function getExecutions(params = {}) {
  const qs = new URLSearchParams(params).toString();
  return request('GET', `/api/executions${qs ? '?' + qs : ''}`);
}

export async function getExecution(id) {
  return request('GET', `/api/executions/${id}`);
}

// ── Usage / Costs ─────────────────────────────────────────────────

export async function getUsageSummary(params = {}) {
  const qs = new URLSearchParams(params).toString();
  return request('GET', `/api/usage/summary${qs ? '?' + qs : ''}`);
}

export async function getUsageByContact(params = {}) {
  const qs = new URLSearchParams(params).toString();
  return request('GET', `/api/usage/by-contact${qs ? '?' + qs : ''}`);
}

export async function getUsageContactDetail(phone, params = {}) {
  const qs = new URLSearchParams(params).toString();
  return request('GET', `/api/usage/contact/${encodeURIComponent(phone)}${qs ? '?' + qs : ''}`);
}

// ── Update ────────────────────────────────────────────────────────

export async function checkForUpdates() {
  return request('GET', '/api/update/check');
}

export async function performUpdate() {
  return request('POST', '/api/update');
}

// ── Auth ──────────────────────────────────────────────────────────

export async function login(password) {
  return request('POST', '/api/auth/login', { password });
}

export async function checkAuth() {
  // checkAuth needs to send token but not trigger unauthorized event on 401
  const opts = {
    method: 'GET',
    headers: _authHeaders({ 'Content-Type': 'application/json' }),
  };
  const res = await fetch(`${BASE}/api/auth/check`, opts);
  return res.json();
}
