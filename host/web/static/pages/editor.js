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

  const S = {
    action: makeEmptyAction(1, 'blank'), selected: 1, playhead: 0,
    playing: false, looping: false, muted: [false, false, false, false], offset: 0,
    roles: [], status: null, online: false, pendingPack: null, wave: waveform(7, 110),
    fileList: [],   // 真实动作文件来源；契约未提供该端点前恒为空（绝不构造假文件名）
  };

  const duration = () => Math.max(1, S.action.frames[S.action.frames.length - 1]?.t_ms || 6000);
  const limits = () => ({ min_us: US_MIN, max_us: US_MAX, neutral_us: US_NEUTRAL, max_speed_us_s: 300 });

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

  /* 契约（Spec §5）无「列出动作文件」端点 => 绝不构造假文件名（如 wave.json）凑数，
     那会让人误以为真实存在。有真实数据用真实数据；无数据源显示空态并说明原因；
     M11 补端点后只需在此填充 S.fileList。 */
  function renderFiles() {
    files.files = S.fileList || [];
    files.setAttribute('empty-hint', S.online
      ? '本机服务动作文件列表接口尚未提供；可用下方「新建」「导入」在本地编辑。'
      : '离线预演模式：未连接后端服务，动作文件列表不可用；可用下方「新建」「导入」在本地编辑。');
  }

  function renderRight(result) {
    const frames = S.action.frames;
    const i = clamp(S.selected, 0, frames.length - 1);
    inspector.setAttribute('max-speed', '300');
    inspector.keyframe = { index: i, frame: frames[i], frames, minUs: limits().min_us, maxUs: limits().max_us };
    if (S.roles.length) {
      limitsPanel.removeAttribute('nodata');
      limitsPanel.axes = AXES.map((a) => ({
        name: a.name, min_us: US_MIN, max_us: US_MAX, neutral_us: US_NEUTRAL, max_speed_us_s: 300,
      }));
    } else {
      limitsPanel.setAttribute('nodata', 'true');
    }
    if (result) validateList.result = result;
  }

  function renderAll(result) { renderTimeline(); renderLeft(); renderRight(result); }

  /* ------------------------------------------------------------ 校验（防抖 + 服务端优先） */
  let vTimer = null;
  function revalidate() {
    validateList.setAttribute('busy', 'true');
    clearTimeout(vTimer);
    vTimer = setTimeout(async () => {
      const local = validateAction(S.action, limits());
      validateList.setAttribute('busy', 'false');
      validateList.result = local;
      guard.setAttribute('disabled-reason', S.online ? (local.valid ? '' : '校验未通过') : '未连接设备');
      try {
        const server = await api.validateAction(S.action); // AC-23：只校验、不触发硬件
        if (server && typeof server.valid === 'boolean') validateList.result = server;
      } catch { /* 离线/失败：保留本地校验结果，不静默覆盖为“通过” */ }
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
    // mode === 'send'：附预期 role_id 与动作版本；409 不叠加，只提示
    try {
      const r = await api.playAction({
        role_id: S.action.role_id, action_id: S.action.name || 'blank',
        action_version: String(S.roles.find((x) => x.role_id === S.action.role_id)?.config_version || ''),
        replace_current: false,
      });
      toast('success', `已下发给设备（首个 FRAME seq=${r && r.seq != null ? r.seq : '—'}）`);
    } catch (e) {
      if (e instanceof api.ConflictError) toast('error', `${e.message}（未叠加时间轴，可选择显式替换）`);
      else if (e instanceof api.ClarifyError) toast('error', `请求需要澄清：${e.message}`);
      else if (api.isOffline(e)) toast('error', '未连接设备，无法下发；当前仅支持预演');
      else toast('error', e.message || '下发失败');
    }
  }

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
  files.addEventListener('file-new', () => { S.action = makeEmptyAction(S.action.role_id, 'blank'); S.selected = 1; S.playhead = 0; renderAll(); revalidate(); toast('info', '已新建空白动作：本地编辑中，未写入磁盘。'); });
  files.addEventListener('file-open', (e) => {
    if (e.detail.name && !e.detail.name.startsWith(S.action.name)) { S.action = { ...S.action, name: e.detail.name.replace('.json', '') }; renderAll(); }
  });
  files.addEventListener('file-import', () => packFile?.click());
  q('#import-btn')?.addEventListener('click', () => packFile?.click());
  packFile?.addEventListener('change', () => {
    const f = packFile.files && packFile.files[0];
    if (!f) return;
    S.pendingPack = f;
    impDialog.manifest = { name: f.name, role_id: '—', config_version: '—', files: '—', pending: true };
    impDialog.setAttribute('open', 'true');
  });
  impDialog.addEventListener('import-confirm', async () => {
    if (!S.pendingPack) return;
    try {
      await api.importContent(S.pendingPack);
      impDialog.manifest = { name: S.pendingPack.name, role_id: '—', config_version: '—', files: '—', rejected: [] };
      toast('success', '资源包已导入并原子切换');
      await loadRoles();
    } catch (e) {
      if (e instanceof api.ApiError && e.status === 400) {
        impDialog.manifest = { name: S.pendingPack.name, rejected: [{ path: '(服务端拒绝)', reason: e.message || '校验未通过' }] };
        toast('error', '导入失败，旧版本已保留');
      } else toast('error', api.isOffline(e) ? '未连接设备服务，导入未生效' : (e.message || '导入失败'));
    }
  });
  q('#export-btn')?.addEventListener('click', () => {
    const blob = new Blob([JSON.stringify(S.action, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = `${S.action.name || 'blank'}.json`; a.click();
    URL.revokeObjectURL(a.href);
    toast('success', '已导出本地动作文件');
  });

  /* ------------------------------------------------------------ 角色与连接 */
  async function loadRoles() {
    try { S.roles = await api.listRoles() || []; }
    catch { S.roles = []; }
    roleSel.roles = S.roles;
    if (S.roles.length) { roleSel.setAttribute('role-id', String(S.roles[0].role_id)); roleSel.setAttribute('config-version', S.roles[0].config_version || ''); }
    renderRight();
  }
  roleSel.addEventListener('role-change', async (e) => {
    const prev = S.action.role_id;
    try {
      await api.setActiveRole(e.detail.role_id);
      S.action.role_id = e.detail.role_id;
      roleSel.setAttribute('config-version', e.detail.config_version || '');
      toast('success', `已切换到角色 ${e.detail.role_id}`);
    } catch (err) {
      roleSel.setAttribute('role-id', String(prev));
      if (err instanceof api.ConflictError) toast('error', `${err.message}（例如正在播放，已还原选择）`);
      else if (api.isOffline(err)) toast('info', '离线：角色切换未提交到设备，界面保持本地选择');
      else toast('error', err.message || '切换角色失败');
    }
  });

  async function loadStatus() {
    try { S.status = await api.getStatus(); S.online = true; }
    catch { S.status = null; S.online = false; }
    conn.setAttribute('state', S.online ? connStateFromStatus(S.status) : 'offline');
    guard.setAttribute('disabled-reason', S.online ? '' : '未连接设备');
    offlineBanner.classList.toggle('hidden', S.online);
    renderFiles();
    renderAll();
  }
  const events = api.openEvents({ onEvent(evt) {
    if (evt && evt.action && evt.action.progress_ms != null && S.online) { S.playhead = evt.action.progress_ms; renderTimeline(); }
  } });
  offlineBanner.addEventListener('banner-retry', () => loadStatus());
  offlineBanner.addEventListener('banner-guide', () => q('#guide')?.showModal());
  q('#guide-close')?.addEventListener('click', () => q('#guide').close());
  window.addEventListener('beforeunload', () => { events.close(); stopPreview(); });

  await loadRoles();
  await loadStatus();
  audio.waveform = S.wave;
  renderAll();
  revalidate();
}
