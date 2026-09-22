/* ============================================================================
 * pages/status.js — 设备状态面板（路由 /）控制器
 * 职责：拉 /status + /features，订阅 /events；**离线是预期常态**——界面照常渲染，
 *      四轴显示 — + 斜纹底（无数据 ≠ 0），横幅给「重试 / 连接指引」两条出路。
 * 硬约束：实时值只标「目标位置」；FAULT_LOCKED 不暗示自动恢复。
 * ==========================================================================*/
import * as api from '../api.js';
import { connStateFromStatus } from '../components/status.js';
import { toast } from '../components/chrome.js';

function setText(root, sel, text) { const el = root.querySelector(sel); if (el) el.textContent = text; }
const now = () => new Date().toLocaleTimeString('zh-CN', { hour12: false });

export default async function boot() {
  const $ = (s) => document.querySelector(s);
  const conn = $('#conn');
  const role = $('#role');
  const stub = $('#stub');
  const banner = $('#banner');
  const interlock = $('#interlock');
  const faultSlot = $('#fault-slot');
  const guide = $('#guide');

  let status = null;
  let roles = [];

  function renderConn() {
    const state = status ? connStateFromStatus(status) : 'offline';
    conn.setAttribute('state', state);
    if (!status) banner.setAttribute('mode', state === 'fault' ? 'fault' : 'offline');
    else if (status.fault || status.state === 'FAULT_LOCKED') banner.setAttribute('mode', 'fault');
    else banner.classList.add('hidden');
  }

  function renderRole() {
    if (!status || !status.role_id) { role.removeAttribute('role-id'); return; }
    role.setAttribute('role-id', String(status.role_id));
    const match = roles.find((r) => r.role_id === status.role_id);
    if (match) {
      role.setAttribute('display-name', match.name || `角色 ${match.role_id}`);
      role.setAttribute('config-version', match.config_version || '');
    }
  }

  function renderInterlock() {
    const docked = !!(status && status.docked);
    const estop = !!(status && status.estop_healthy);
    const match = !!(status && status.role_id && roles.some((r) => r.role_id === status.role_id));
    interlock.setAttribute('docked', String(docked));
    interlock.setAttribute('estop-healthy', String(estop));
    interlock.setAttribute('role-match', String(match));
  }

  function renderFault() {
    const f = status && status.fault;
    faultSlot.innerHTML = '';
    if (!f) return;
    const panel = document.createElement('fault-panel');
    panel.setAttribute('code', f.code || 'INTERLOCK');
    panel.setAttribute('detail', f.detail || '');
    faultSlot.appendChild(panel);
  }

  function renderMeta() {
    setText(document, '#meta-gen', status ? String(status.session_generation ?? '—') : '—');
    setText(document, '#meta-proto', 'v1（SPEC §7.2 串口协议）');
    setText(document, '#meta-fetch', now());
  }

  function renderStub(features) {
    // Spec §2.3 硬要求：未设 DASHSCOPE_API_KEY 时必须落桩后端并零网络出口。
    // 契约未显式给出 provider 字段时，按本阶段「无云端」默认展示桩标识。
    const isQwen = features && features.provider === 'qwen';
    stub.setAttribute('provider', isQwen ? 'qwen' : 'stub');
  }

  async function load() {
    let anyOffline = false;
    try { status = await api.getStatus(); } catch (e) { if (api.isOffline(e) || e.name === 'ApiError') { status = null; anyOffline = true; } }
    try { roles = await api.listRoles() || []; } catch { roles = []; }
    try { renderStub(await api.listFeatures()); } catch { renderStub(null); }
    renderConn(); renderRole(); renderInterlock(); renderFault(); renderMeta();
    if (anyOffline && !status) banner.classList.remove('hidden');
  }

  // 事件流（WebSocket）：真实驱动；断线自动重连，失败则退避后回退到 /status 轮询
  let poll = null;
  const events = api.openEvents({
    onStatus(connected) {
      if (connected) { if (poll) { clearInterval(poll); poll = null; } return; }
      if (!poll) poll = setInterval(load, 5000);
    },
    onEvent(evt) {
      if (!evt || !evt.kind) return;
      status = status || {};
      if (evt.state) status.state = evt.state;
      if (evt.fault !== undefined) status.fault = evt.fault;
      if (evt.docked !== undefined) status.docked = evt.docked;
      if (evt.estop_healthy !== undefined) status.estop_healthy = evt.estop_healthy;
      if (evt.session_generation !== undefined) status.session_generation = evt.session_generation;
      if (evt.action) status.action = evt.action;
      renderConn(); renderInterlock(); renderFault(); renderMeta();
    },
  });

  banner.addEventListener('banner-retry', () => { load().then(() => toast('info', '已重新检测设备连接')); });
  banner.addEventListener('banner-guide', () => guide && guide.showModal());
  faultSlot.addEventListener('fault-ack', (e) => {
    // 解除故障必须经后端确认；本阶段后端未就绪 => 明确提示，不假装成功
    toast('info', '已提交解除请求，等待设备确认（未连接时会话保持锁定）');
    e.stopPropagation();
  });
  document.querySelector('#guide-close')?.addEventListener('click', () => guide.close());
  window.addEventListener('beforeunload', () => events.close());

  await load();
}
