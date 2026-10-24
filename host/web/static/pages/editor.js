/* ============================================================================
 * pages/editor.js — 动作编排器（路由 /editor）控制器
 * 职责：角色切换、四轴时间轴编辑（拖播放指针 / 拖菱形关键帧 / 方向键微调）、
 *      离线本地插值预演（s(u)=3u²−2u³）、防抖校验、下发与停止、导入导出。
 * 关键纪律：
 *   - 预演用 performance.now() 采样经过时间，**不用 setInterval 累计**（会越播越慢）；
 *   - 第二个播放请求走 409 分支，绝不静默叠加两条时间轴（AC-24）；
 *   - 下发按钮携带预期 role_id，角色已变时被拒（AC-16）；离线时禁用并给原因。
 * ==========================================================================*/
import * as api from '../api.js';
import { createEditorDocuments } from './editor-document.js';
import { bindEditorRails } from './editor-rails.js';
import { bindEditorTransfers } from './editor-transfers.js';
import { toast } from '../components/chrome.js';
import { connStateFromStatus } from '../components/status.js';
import {
  AXES, sampleAll, validateAction, makeEmptyAction, clamp, waveform, US_MIN, US_MAX, US_NEUTRAL,
} from '../timeline.js';

const LANE_IDS = ['#lane-0', '#lane-1', '#lane-2', '#lane-3'];
const num = (el, a, d) => (el && el.hasAttribute(a) ? Number(el.getAttribute(a)) : d);

export default async function boot() {
  const q = (s) => document.querySelector(s);
  const conn = q('#conn'), roleSel = q('#role'), tc = q('#tc'), ruler = q('#ruler'),
    playhead = q('#ph'), audio = q('#audio'), transport = q('#transport'), guard = q('#guard'),
    tracks = q('#tracks'), files = q('#files'), inspector = q('#inspector'), limitsPanel = q('#limits'),
    validateList = q('#validate'), impDialog = q('#impdialog'), offlineBanner = q('#offline-banner'),
    packFile = q('#pack-file');
  const lanes = LANE_IDS.map((id) => q(id));
  bindEditorRails(q);

  const S = {
    action: makeEmptyAction(1, 'blank'), selected: 1, playhead: 0,
    playing: false, looping: false, muted: [false, false, false, false], offset: 0,
    roles: [], status: null, online: false, wave: waveform(7, 110),
    sourceId: null, documentVersion: null, documentLimits: null, loading: false, sending: false,
    fileList: [],   // 真实动作文件来源；由 GET /actions/files 填充（绝不构造假文件名）
  };

  const duration = () => Math.max(1, S.action.frames[S.action.frames.length - 1]?.t_ms || 6000);
  const limits = () => S.documentLimits || ({ min_us: US_MIN, max_us: US_MAX, neutral_us: US_NEUTRAL, max_speed_us_s: 300 });

  /* ------------------------------------------------------------ 渲染 */
  function renderTimeline() {
    ruler.setAttribute('duration-ms', String(duration()));
    ruler.setAttribute('playhead-ms', String(Math.round(S.playhead)));
    playhead.setAttribute('duration-ms', String(duration()));
    playhead.setAttribute('ms', String(Math.round(S.playhead)));
    audio.setAttribute('duration-ms', String(duration()));
    audio.setAttribute('offset-ms', String(S.offset));
    lanes.forEach((lane, i) => {
      lane.model = { frames: S.action.frames, durationMs: duration(), selected: S.selected };
      lane.setAttribute('muted', String(S.muted[i]));
      lane.setAttribute('axis', String(i));
      lane.setAttribute('min-us', String(limits().min_us));
      lane.setAttribute('max-us', String(limits().max_us));
    });
    transport.setAttribute('playing', String(S.playing));
    transport.setAttribute('looping', String(S.looping));
    transport.setAttribute('preview-only', String(!S.online));
    transport.setAttribute('duration-ms', String(duration()));
    tc.setAttribute('ms', String(Math.round(S.playhead)));
    tc.setAttribute('total-ms', String(duration()));
  }

  function renderLeft() {
    const values = S.online ? sampleAll(S.action.frames, S.playhead) : null;
    tracks.tracks = AXES.map((a) => ({
      axis: a.i + 1, label: a.name, muted: S.muted[a.i],
      selected: a.i === S.selected, values,
    }));
  }

  function renderRight(result) {
    const frames = S.action.frames;
    const i = clamp(S.selected, 0, frames.length - 1);
    inspector.setAttribute('max-speed', '300');
    inspector.keyframe = { index: i, frame: frames[i], frames, minUs: limits().min_us, maxUs: limits().max_us };
    if (S.roles.length) {
      limitsPanel.removeAttribute('nodata');
      limitsPanel.axes = AXES.map((a) => ({
        name: a.name, ...limits(),
      }));
    } else {
      limitsPanel.setAttribute('nodata', 'true');
    }
    if (result) validateList.result = result;
  }

  function renderAll(result) { renderTimeline(); renderLeft(); renderRight(result); documents?.render(); }

  /* ------------------------------------------------------------ 校验（防抖 + 服务端优先） */
  let vTimer = null, vRevision = 0;
  function updateGuard(valid) {
    guard.setAttribute('disabled-reason', !S.online ? '未连接设备' :
      S.loading ? '正在读取动作' : S.sending ? '正在提交动作' :
        !S.documentVersion ? '未读取角色版本' : valid ? '' : '校验未通过');
  }
  function revalidate() {
    const revision = ++vRevision, snapshot = JSON.stringify(S.action);
    validateList.setAttribute('busy', 'true'); updateGuard(false);
    clearTimeout(vTimer);
    vTimer = setTimeout(async () => {
      let result = validateAction(JSON.parse(snapshot), limits());
      if (S.online) {
        try { result = await api.validateAction(JSON.parse(snapshot)); }
        catch { result = { valid: false, errors: [{ path: 'action', message: '服务端校验未确认，请重试' }] }; }
      }
      if (revision !== vRevision || JSON.stringify(S.action) !== snapshot) return;
      validateList.setAttribute('busy', 'false'); validateList.result = result;
      updateGuard(result.valid);
    }, 220);
  }

  /* ------------------------------------------------------------ 离线预演（本地插值） */
  let raf = null, t0 = 0, base = 0;
  function tick() {
    const elapsed = performance.now() - t0;      // 采样经过时间，避免越播越慢
    let ms = base + elapsed;
    const total = duration();
    if (ms >= total) {
      if (S.looping) { t0 = performance.now(); base = 0; ms = 0; } else { ms = total; stopPreview(); }
    }
    S.playhead = ms;
    renderTimeline(); renderLeft();
    if (S.playing) raf = requestAnimationFrame(tick);
  }
  function startPreview() { S.playing = true; base = S.playhead; t0 = performance.now(); renderAll(); raf = requestAnimationFrame(tick); }
  function stopPreview() { S.playing = false; if (raf) cancelAnimationFrame(raf); raf = null; renderAll(); }

  /* ------------------------------------------------------------ 编辑操作 */
  function applyMove({ axis, index, t_ms, us, commit }) {
    const fs = S.action.frames;
    const f = fs[index];
    if (!f) return;
    const lo = index > 0 ? fs[index - 1].t_ms + 1 : 0;
    const hi = index < fs.length - 1 ? fs[index + 1].t_ms - 1 : 60000;
    f.t_ms = Math.round(clamp(t_ms, lo, hi));
    // 越界即回弹 + 标记 + 说明（不写入非法值）
    if (us < limits().min_us || us > limits().max_us) {
      const lane = lanes[axis];
      const kf = lane && lane.querySelector(`[data-kf="${index}"]`);
      if (kf) { kf.setAttribute('data-out', 'true'); setTimeout(() => kf.removeAttribute('data-out'), 900); }
      toast('error', `超出安全限位 ${limits().min_us}–${limits().max_us}µs（轴${axis + 1} ${AXES[axis].name}）`);
    } else {
      f.us[axis] = Math.round(us);
    }
    S.selected = index;
    renderAll();
    if (commit) revalidate();
  }

  function addKeyframe() {
    const fs = S.action.frames;
    const at = Math.round(S.playhead);
    if (at <= 0 || at >= fs[fs.length - 1].t_ms) { toast('error', '请在首末帧之间插入关键帧'); return; }
    const us = sampleAll(fs, at).map((v) => Math.round(v));
    let i = fs.findIndex((f) => f.t_ms > at);
    if (i <= 0) i = 1;
    fs.splice(i, 0, { t_ms: at, us });
    S.selected = i;
    renderAll();
    revalidate();
  }

  /* ------------------------------------------------------------ 播放请求 */
  async function requestPlay(mode) {
    if (mode === 'guide') { q('#guide')?.showModal(); return; }
    if (mode === 'preview') { if (S.playing) stopPreview(); else startPreview(); return; }
    if (S.loading || S.sending || !S.online || !S.documentVersion) return;
    const snapshot = structuredClone(S.action), version = S.documentVersion;
    const actionId = S.sourceId || 'draft';
    S.sending = true; updateGuard(false);
    try {
      const verdict = await api.validateAction(snapshot);
      if (!verdict.valid) throw new api.ClarifyError('动作校验未通过');
      if (S.loading || actionId !== (S.sourceId || 'draft') ||
          JSON.stringify(S.action) !== JSON.stringify(snapshot) || version !== S.documentVersion) {
        toast('info', '校验期间动作已修改，请重新下发当前内容'); return;
      }
      await api.playAction({ role_id: snapshot.role_id, action_id: actionId,
        action_version: version, action: snapshot, replace_current: false });
      toast('success', '当前编辑动作已提交，等待设备执行；未写入磁盘。');
    } catch (e) {
      if (e instanceof api.ConflictError) toast('error', `${e.message}（未叠加时间轴，可选择显式替换）`);
      else if (e instanceof api.ClarifyError) toast('error', `请求需要澄清：${e.message}`);
      else if (api.isOffline(e)) toast('error', '未连接设备，无法下发；当前仅支持预演');
      else toast('error', e.message || '下发失败');
    } finally { S.sending = false; revalidate(); }
  }

  const documents = createEditorDocuments({ S, files, roleSel,
    listFiles: () => api.listActionFiles(), stopPreview, render: renderAll, validate: revalidate });

  /* ------------------------------------------------------------ 事件绑定 */
  ruler.addEventListener('ruler-seek', (e) => { S.playhead = e.detail.ms; renderTimeline(); renderLeft(); });
  playhead.addEventListener('playhead-seek', (e) => { S.playhead = e.detail.ms; renderTimeline(); renderLeft(); });
  lanes.forEach((lane, i) => {
    lane.addEventListener('keyframe-move', (e) => applyMove({ ...e.detail, axis: i }));
    lane.addEventListener('keyframe-select', (e) => { S.selected = e.detail.index; renderAll(); });
    lane.addEventListener('lane-seek', (e) => { S.playhead = e.detail.ms; renderTimeline(); renderLeft(); });
    lane.addEventListener('keyframe-delete', (e) => {
      const fs = S.action.frames;
      if (fs.length <= 2) { toast('error', '至少保留首末两帧'); return; }
      if (e.detail.index === 0 || e.detail.index === fs.length - 1) { toast('error', '首末帧为中位锚点，不可删除'); return; }
      fs.splice(e.detail.index, 1);
      renderAll(); revalidate();
    });
  });
  audio.addEventListener('audio-offset-change', (e) => { S.offset = e.detail.offset_ms; renderTimeline(); });
  transport.addEventListener('play', () => requestPlay('preview'));
  transport.addEventListener('stop', () => { stopPreview(); api.stopAction().catch(() => { /* 离线忽略 */ }); });
  transport.addEventListener('loop-toggle', () => { S.looping = !S.looping; renderTimeline(); });
  guard.addEventListener('play-request', (e) => requestPlay(e.detail.mode));
  tracks.addEventListener('track-select', (e) => { S.activeAxis = e.detail.axis - 1; renderLeft(); lanes.forEach((l) => l.focus?.()); });
  tracks.addEventListener('lane-toggle-mute', (e) => { const i = e.detail.axis - 1; S.muted[i] = !S.muted[i]; renderAll(); });
  inspector.addEventListener('keyframe-field-change', (e) => {
    const d = e.detail; const f = S.action.frames[S.selected]; if (!f) return;
    if (d.field === 't_ms') applyMove({ axis: 0, index: S.selected, t_ms: d.value, us: f.us[0], commit: true });
    else applyMove({ axis: d.axis, index: S.selected, t_ms: f.t_ms, us: d.value, commit: true });
  });
  q('#kf-insert')?.addEventListener('click', addKeyframe);
  validateList.addEventListener('result-jump-to', (e) => {
    const m = /frames\[(\d+)\]/.exec(e.detail.path || '');
    if (m) { S.selected = Number(m[1]); renderAll(); }
  });
  files.addEventListener('file-import', () => packFile?.click());
  bindEditorTransfers({ packFile, impDialog, action: () => S.action, reloadRoles: async () => { await loadRoles(); await documents.refresh(true); } });

  /* ------------------------------------------------------------ 角色与连接 */
  async function loadRoles() {
    try { S.roles = await api.listRoles() || []; }
    catch { S.roles = []; }
    roleSel.roles = S.roles;
    if (S.roles.length) { roleSel.setAttribute('role-id', String(S.roles[0].role_id)); roleSel.setAttribute('config-version', S.roles[0].config_version || ''); }
    renderRight();
  }
  async function loadStatus() {
    try { S.status = await api.getStatus(); S.online = true; }
    catch { S.status = null; S.online = false; }
    conn.setAttribute('state', S.online ? connStateFromStatus(S.status) : 'offline');
    guard.setAttribute('disabled-reason', S.online ? '' : '未连接设备');
    offlineBanner.classList.toggle('hidden', S.online);
    if (S.online) await documents.connect(S.status.role_id); else documents.render();
    renderAll();
  }
  const events = api.openEvents({ onEvent(evt) {
    if (evt && evt.action && evt.action.progress_ms != null && S.online) { S.playhead = evt.action.progress_ms; renderTimeline(); }
  } });
  offlineBanner.addEventListener('banner-retry', () => loadStatus());
  offlineBanner.addEventListener('banner-guide', () => q('#guide')?.showModal());
  q('#guide-close')?.addEventListener('click', () => q('#guide').close());
  window.addEventListener('pagehide', () => { events.close(); stopPreview(); });

  await loadRoles();
  await loadStatus();
  audio.waveform = S.wave;
  renderAll();
  revalidate();
}
