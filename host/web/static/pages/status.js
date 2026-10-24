/* ============================================================================
 * pages/status.js — 设备状态面板（路由 /）控制器
 * 职责：拉 /status + /features，订阅 /events；**离线是预期常态**——界面照常渲染，
 *      四轴显示 — + 斜纹底（无数据 ≠ 0），横幅给「重试 / 连接指引」两条出路。
 * 硬约束：实时值只标「目标位置」；FAULT_LOCKED 不暗示自动恢复。
 * ==========================================================================*/
import * as api from '../api.js';
import { connStateFromStatus } from '../components/status.js';
import { toast } from '../components/chrome.js';
import { createTargetDisplay } from './status-targets.js';

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
  let disposed = false, revision = 0, loadId = 0, firstEvent = true;
  const targets = createTargetDisplay([...document.querySelectorAll('axis-limit-bar')]);

  function renderConn() {
    const state = status ? connStateFromStatus(status) : 'offline';
    conn.setAttribute('state', state);
    // hidden 的开关必须自成对：只在「已连上且无故障」时隐藏。
    // 缺 remove 分支时，页面一旦成功连过一次，后续任何故障都只会 setAttribute(mode)
    // 而横幅仍带 hidden —— 故障提示永久不可见（实测「在线转故障」必现）。
    if (!status) banner.setAttribute('mode', 'offline');
    else if (status.fault || status.state === 'FAULT_LOCKED') banner.setAttribute('mode', 'fault');
    else { banner.classList.add('hidden'); return; }
    banner.classList.remove('hidden');
  }

  function renderRole() {
    if (!status || !status.role_id) { role.removeAttribute('role-id'); return; }
    role.setAttribute('role-id', String(status.role_id));
    const match = roles.find((r) => r.role_id === status.role_id);
    if (match) {
      role.setAttribute('display-name', match.name || `角色 ${match.role_id}`);
      role.setAttribute('config-version', status.axis_limits?.config_version || match.config_version || '');
    } else {
      role.removeAttribute('display-name');
      role.removeAttribute('config-version');
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
    const version = revision, id = ++loadId, started = performance.now();
    const [snapshot, roleList, features] = await Promise.allSettled([
      api.getStatus(), api.listRoles(), api.listFeatures(),
    ]);
    if (disposed || id !== loadId) return;
    roles = roleList.status === 'fulfilled' ? roleList.value || [] : [];
    renderStub(features.status === 'fulfilled' ? features.value : null);
    // 较新的事件或断线已发生时，迟到的 HTTP 回包不得复活旧目标。
    if (version === revision) {
      status = snapshot.status === 'fulfilled' ? snapshot.value : null;
      targets.update(status, started);
    }
    render();
  }

  function render() {
    renderConn(); renderRole(); renderInterlock(); renderFault(); renderMeta();
  }

  // 事件流（WebSocket）：真实驱动；断线自动重连，失败则退避后回退到 /status 轮询
  let poll = null;
  const events = api.openEvents({
    onStatus(connected) {
      if (disposed) return;
      revision++;
      firstEvent = true; // 重连后的服务可能已重启，代数允许从新基线开始。
      if (connected) { if (poll) { clearInterval(poll); poll = null; } return; }
      status = null;
      targets.clear();
      render();
      if (!poll) poll = setInterval(load, 5000);
    },
    onEvent(evt) {
      if (disposed || evt?.kind !== 'STATUS') return;
      if (!firstEvent && evt.session_generation < status?.session_generation) return;
      firstEvent = false;
      revision++;
      status = { ...status, ...evt, action: evt.action ?? null, target: evt.target ?? null,
        axis_limits: evt.axis_limits ?? null };
      targets.update(status);
      render();
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
  window.addEventListener('pagehide', () => {
    disposed = true;
    targets.clear();
    clearInterval(poll);
    events.close();
  });
  window.addEventListener('pageshow', (event) => {
    if (event.persisted) location.reload(); // 历史缓存恢复时重新建立已关闭的事件流。
  });

  await load();
}
