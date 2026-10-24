/* ============================================================================
 * host/web/static/api.js — 与 host/web/routers 对接的唯一出口
 * 契约来源：docs/api/openapi.yaml（base /api/v1），统一信封 {code,data,message}。
 * 设计要点
 *  1) 每个写操作自动附带 request_id（openapi 强制），调用方无需手动生成。
 *  2) 409 / 422 是一等公民：分别抛 ConflictError / ClarifyError，页面据此走
 *     「不叠加时间轴」与「澄清表单」两条专用分支，绝不当成 500。
 *  3) 无后端（本阶段预期常态）时抛 OfflineError：页面渲染中性的离线态，
 *     照常可编辑，仅 L3 动作（下发/实时值/急停）禁用并给原因——绝不静默失败。
 * ==========================================================================*/

export const API_BASE = '/api/v1';
export const EVENTS_PATH = '/api/v1/events';
const TIMEOUT_MS = 6000;

export class ApiError extends Error {
  constructor(message, info = {}) {
    super(message || '请求失败');
    this.name = 'ApiError';
    this.status = info.status ?? 0;
    this.code = info.code ?? null;
    this.envelope = info.envelope ?? null;
  }
}
export class OfflineError extends ApiError {
  constructor(message = '未连接设备服务') {
    super(message, { status: 0 });
    this.name = 'OfflineError';
  }
}
export class ConflictError extends ApiError { // HTTP 409
  constructor(message, info = {}) { super(message, { ...info, status: 409 }); this.name = 'ConflictError'; }
}
export class ClarifyError extends ApiError { // HTTP 422：需要澄清，非错误崩溃
  constructor(message, info = {}) { super(message, { ...info, status: 422 }); this.name = 'ClarifyError'; }
}
export const isOffline = (e) => e instanceof OfflineError;

/** 写操作审计与去重用 request_id（契约强制：每个写操作必带）。 */
export function newRequestId() {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === 'function') return c.randomUUID();
  return 'r-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
}

function buildQuery(query) {
  if (!query) return '';
  const usp = new URLSearchParams();
  Object.entries(query).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') usp.set(k, String(v));
  });
  const s = usp.toString();
  return s ? '?' + s : '';
}

/**
 * @param {'GET'|'POST'|'PATCH'|'DELETE'} method
 * @param {string} path 以 / 开头，相对 API_BASE
 * @param {{query?:object, body?:object, form?:FormData, withRequestId?:boolean, signal?:AbortSignal}} opts
 */
export async function request(method, path, opts = {}) {
  const { query, body, form, withRequestId = false, signal } = opts;
  const url = API_BASE + path + buildQuery(query);
  const headers = { Accept: 'application/json' };
  let payload;
  if (form) {
    payload = form; // multipart：不设 Content-Type，交由浏览器携带 boundary
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  if (withRequestId) {
    headers['X-Request-Id'] = newRequestId();
  }

  const ctrl = new AbortController();
  const onAbort = () => ctrl.abort();
  if (signal) signal.addEventListener('abort', onAbort, { once: true });
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);

  let res;
  try {
    res = await fetch(url, { method, headers, body: payload, signal: ctrl.signal, credentials: 'same-origin' });
  } catch (err) {
    clearTimeout(timer);
    throw new OfflineError(); // 网络不可达 / 超时 => 离线（预期常态）
  } finally {
    clearTimeout(timer);
    if (signal) signal.removeEventListener('abort', onAbort);
  }

  let envelope = null;
  const text = await res.text();
  if (text) { try { envelope = JSON.parse(text); } catch { envelope = null; } }
  const message = (envelope && envelope.message) || res.statusText || '请求失败';

  if (!res.ok) {
    const info = { status: res.status, code: envelope && envelope.code, envelope };
    if (res.status === 409) throw new ConflictError(message, info);
    if (res.status === 422) throw new ClarifyError(message, info);
    throw new ApiError(message, info);
  }
  if (envelope && typeof envelope.code === 'number' && envelope.code !== 0) {
    throw new ApiError(message, { status: res.status, code: envelope.code, envelope });
  }
  return envelope && 'data' in envelope ? envelope.data : envelope;
}

/* ------------------------------------------------------------------ 读操作 */
export const getStatus = () => request('GET', '/status');
export const listRoles = () => request('GET', '/roles');
export const listFeatures = () => request('GET', '/features');
/** @param {{profile_id?:string, kind?:string}} q */
export const listMemories = (q) => request('GET', '/memories', { query: q });
export const listReminders = (q) => request('GET', '/reminders', { query: q });
export const getSettings = () => request('GET', '/settings');
/** 当前角色可编辑的动作文件列表；`active` 标记当前编辑指针（只读端点，无 query 参数）。 */
export const listActionFiles = () => request('GET', '/actions/files');
export const getActionDocument = (role_id, action_id) =>
  request('GET', `/actions/files/${encodeURIComponent(action_id)}`, { query: { role_id } });

/* ------------------------------------------------------------------ 写操作 */
export const setActiveRole = (role_id) =>
  request('PATCH', '/roles/active', { body: { request_id: newRequestId(), role_id }, withRequestId: true });

/* 人设（一键改性格）。注意请求体就是 PersonaOut 本体：**没有** request_id 字段
 * （见 host/web/schemas.py 的 PersonaOut，openapi 的 requestBody 也只指向它），
 * 审计用的追踪号走 X-Request-Id 头，不往 body 里塞多余键。 */
export const savePersona = (role_id, persona) =>
  request('PATCH', `/roles/${role_id}/persona`, { body: persona, withRequestId: true });

/** 编译预览：后端**永远 200**，非法内容用 valid:false + errors 表达，不是异常。 */
export const previewPersona = (role_id, persona) =>
  request('POST', `/roles/${role_id}/persona/preview`, { body: persona, withRequestId: true });

export const validateAction = (action) =>
  request('POST', '/actions/validate', { body: { request_id: newRequestId(), action }, withRequestId: true });

/** 播放必须附预期 role_id 与动作版本；replace_current 默认 false => 正在播放时 409（AC-16/AC-24）。 */
export const playAction = ({ role_id, action_id, action_version, action, replace_current = false }) =>
  request('POST', '/actions/play', {
    body: { request_id: newRequestId(), role_id, action_id, action_version, action, replace_current },
    withRequestId: true,
  });

export const stopAction = () =>
  request('POST', '/actions/stop', { body: { request_id: newRequestId() }, withRequestId: true });

export function importContent(file) {
  const form = new FormData();
  form.append('request_id', newRequestId());
  form.append('file', file);
  return request('POST', '/content/import', { form, withRequestId: true });
}

export const deleteMemory = (id) => request('DELETE', `/memories/${encodeURIComponent(id)}`, { withRequestId: true });

export function createReminder({ profile_id, text, due_at_utc, timezone, repeat_rule, idempotency_key }) {
  return request('POST', '/reminders', {
    body: { request_id: newRequestId(), profile_id, text, due_at_utc, timezone, repeat_rule, idempotency_key },
    withRequestId: true,
  });
}
export const cancelReminder = (id) => request('DELETE', `/reminders/${encodeURIComponent(id)}`, { withRequestId: true });

/** 仅提交变化字段；密码类字段绝不回显、绝不回传旧值。 */
export const patchSettings = (patch) =>
  request('PATCH', '/settings', { body: { request_id: newRequestId(), ...patch }, withRequestId: true });

/* -------------------------------------------------------------- 事件流（WS） */
function wsUrl() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${location.host}${EVENTS_PATH}`;
}

/**
 * 订阅 CoreEvent（状态迁移/故障/按键/动作进度）。
 * 断线自动重连并退避 2s 至 8s；反复失败时由 onStatus(false) 让页面回退到 /status 轮询。
 * @returns {{close:()=>void}}
 */
export function openEvents({ onEvent, onStatus } = {}) {
  let sock = null, closed = false, delay = 2000, timer = null;
  const report = (s) => { if (onStatus) onStatus(s); };
  const schedule = () => {
    if (closed) return;
    timer = setTimeout(connect, delay);
    delay = Math.min(delay * 2, 8000);
  };
  function connect() {
    if (closed) return;
    try { sock = new WebSocket(wsUrl()); } catch { report(false); schedule(); return; }
    sock.onopen = () => { delay = 2000; report(true); };
    sock.onmessage = (ev) => { try { onEvent && onEvent(JSON.parse(ev.data)); } catch { /* 丢弃非 JSON 帧 */ } };
    sock.onerror = () => { report(false); };
    sock.onclose = () => { report(false); schedule(); };
  }
  connect();
  return { close() { closed = true; clearTimeout(timer); if (sock) sock.close(); } };
}
