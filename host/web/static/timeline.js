/* ============================================================================
 * host/web/static/timeline.js — 时间轴纯模型（无 DOM，可单测）
 * 与后端 host/motion/{interpolation,validation}.py 同一公式，保证离线预演与
 * 下发校验结果一致：
 *   插值      s(u) = 3u² − 2u³           （u∈[0,1]；t≤0 取首帧、越界取末帧）
 *   峰值速度  peak = 1.5 · Δus / Δt(s)   （smoothstep 导数上界 1.5 × 平均斜率）
 * 边界（Spec §10）：脉宽 1400–1600µs；时长 ≤60s；帧数 2–1000；时间严格递增。
 * 主机侧限位真源 = 角色配置 min_us/max_us；此处默认取固件保守值 1400/1600。
 * ==========================================================================*/

export const US_MIN = 1400;
export const US_MAX = 1600;
export const US_NEUTRAL = 1500;
export const MAX_SPEED_US_S = 300;
export const MAX_DURATION_MS = 60000;
export const MAX_FRAMES = 1000;
export const MIN_FRAMES = 2;

export const AXES = [
  { i: 0, name: '左臂', token: '--track-1' },
  { i: 1, name: '右臂', token: '--track-2' },
  { i: 2, name: '头部左右', token: '--track-3' },
  { i: 3, name: '腰部左右', token: '--track-4' },
];
export const AXIS_NAMES = AXES.map((a) => a.name);

/** smoothstep：与后端 interpolation.py 逐字一致。 */
export function smoothstep(u) { return 3 * u * u - 2 * u * u * u; }

export function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

function frameAt(frames, tMs) {
  if (frames.length === 0) return null;
  if (tMs <= frames[0].t_ms) return frames[0];
  const last = frames[frames.length - 1];
  if (tMs >= last.t_ms) return last;
  let lo = 0, hi = frames.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (frames[mid].t_ms <= tMs) lo = mid; else hi = mid;
  }
  return frames[lo]; // 返回段落起点，交由 sampleAxis 计算 u
}

/** 采样单轴：段内用 s(u) 插值；段外取端点（与后端 sample 一致）。 */
export function sampleAxis(frames, axisIndex, tMs) {
  if (!frames || frames.length === 0) return null;
  const last = frames[frames.length - 1];
  if (tMs <= frames[0].t_ms) return frames[0].us[axisIndex];
  if (tMs >= last.t_ms) return last.us[axisIndex];
  const a = frameAt(frames, tMs);
  const bi = frames.indexOf(a) + 1;
  const b = frames[bi];
  const span = b.t_ms - a.t_ms;
  const u = span <= 0 ? 1 : (tMs - a.t_ms) / span;
  const s = smoothstep(clamp(u, 0, 1));
  return a.us[axisIndex] + (b.us[axisIndex] - a.us[axisIndex]) * s;
}

/** 采样四轴：返回 number[]（离线预演的实时读数来源）。 */
export function sampleAll(frames, tMs, count = AXES.length) {
  const out = [];
  for (let i = 0; i < count; i++) out.push(sampleAxis(frames, i, tMs));
  return out;
}

/** 峰值速度（µs/s）：逐段取 1.5·Δ/Δt 的最大值。用于 AC-10 阻断。 */
export function peakSpeed(frames, axisIndex) {
  let peak = 0;
  for (let k = 1; k < frames.length; k++) {
    const dt = (frames[k].t_ms - frames[k - 1].t_ms) / 1000;
    if (dt <= 0) continue;
    const d = Math.abs(frames[k].us[axisIndex] - frames[k - 1].us[axisIndex]);
    peak = Math.max(peak, (1.5 * d) / dt);
  }
  return peak;
}

/** 限位状态：normal / near / at / nodata。near = 距端点 ≤10% 行程。 */
export function axisState(us, minUs = US_MIN, maxUs = US_MAX, near = 0.1) {
  if (us === null || us === undefined || !Number.isFinite(us)) return 'nodata';
  const span = maxUs - minUs;
  const band = span * near;
  if (us <= minUs || us >= maxUs) return 'at';
  if (us <= minUs + band || us >= maxUs - band) return 'near';
  return 'normal';
}

export function makeEmptyAction(roleId = 1, name = 'blank') {
  const mid = [US_NEUTRAL, US_NEUTRAL, US_NEUTRAL, US_NEUTRAL];
  return {
    schema: 1, role_id: roleId, name,
    axes: AXIS_NAMES.slice(),
    frames: [
      { t_ms: 0, us: mid.slice() },
      { t_ms: 1200, us: [1560, 1440, US_NEUTRAL, US_NEUTRAL] },
      { t_ms: 2400, us: mid.slice() },
    ],
  };
}

/**
 * 本地校验（镜像后端规则，用于离线即时反馈与 AC-09/AC-10 阻断）。
 * @returns {{valid:boolean, errors:{path:string,message:string}[]}}
 */
export function validateAction(action, limits = {}) {
  const minUs = limits.min_us ?? US_MIN;
  const maxUs = limits.max_us ?? US_MAX;
  const maxSpeed = limits.max_speed_us_s ?? MAX_SPEED_US_S;
  const neutral = limits.neutral_us ?? US_NEUTRAL;
  const errors = [];
  const push = (path, message) => errors.push({ path, message });
  if (!action || typeof action !== 'object') { push('action', '动作包为空或格式不正确'); return { valid: false, errors }; }
  if (action.schema !== 1) push('schema', 'schema 必须为 1');
  if (!Number.isInteger(action.role_id) || action.role_id < 1 || action.role_id > 3) push('role_id', 'role_id 必须是 1/2/3');
  const fs = action.frames;
  if (!Array.isArray(fs)) { push('frames', 'frames 必须是数组'); return { valid: false, errors }; }
  if (fs.length < MIN_FRAMES || fs.length > MAX_FRAMES) push('frames', `帧数须在 ${MIN_FRAMES}–${MAX_FRAMES} 之间（当前 ${fs.length}）`);
  let prev = -1;
  fs.forEach((f, k) => {
    const at = `frames[${k}]`;
    if (typeof f.t_ms !== 'number' || !Number.isInteger(f.t_ms)) push(`${at}.t_ms`, '时间必须是整数毫秒');
    else if (f.t_ms < 0 || f.t_ms > MAX_DURATION_MS) push(`${at}.t_ms`, `时间须在 0–${MAX_DURATION_MS}ms`);
    else if (f.t_ms <= prev) push(`${at}.t_ms`, `时间必须严格递增（${f.t_ms} ≤ ${prev}）`);
    else prev = f.t_ms;
    if (!Array.isArray(f.us) || f.us.length !== 4) { push(`${at}.us`, 'us 必须为 4 个数值'); return; }
    f.us.forEach((v, i) => {
      if (typeof v !== 'number' || !Number.isFinite(v)) push(`${at}.us[${i}]`, '脉宽必须是有限数值（不接受 NaN/Inf/布尔/字符串）');
      else if (v < minUs || v > maxUs) push(`${at}.us[${i}]`, `超出安全限位 ${minUs}–${maxUs}µs（当前 ${v}）`);
    });
  });
  if (fs.length >= 2) {
    if (fs[0].t_ms !== 0) push('frames[0].t_ms', '首帧时间必须为 0ms');
    const mid = (f) => Array.isArray(f.us) && f.us.every((v) => v === neutral);
    if (!mid(fs[0])) push('frames[0].us', `首帧四轴必须为中位 ${neutral}µs`);
    if (!mid(fs[fs.length - 1])) push(`frames[${fs.length - 1}].us`, `末帧四轴必须为中位 ${neutral}µs`);
    for (let i = 0; i < 4; i++) {
      const p = peakSpeed(fs, i);
      if (p > maxSpeed) push(`axes[${i}]`, `峰值速度 ${p.toFixed(1)}µs/s 超过上限 ${maxSpeed}µs/s（轴${i + 1} ${AXIS_NAMES[i]}）`);
    }
  }
  return { valid: errors.length === 0, errors };
}

/* -------------------------------------------------------------- 展示辅助 */
export function formatTime(ms) {
  const v = Math.max(0, Math.round(ms || 0));
  const m = Math.floor(v / 60000);
  const s = Math.floor((v % 60000) / 1000);
  const f = v % 1000;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${String(f).padStart(3, '0')}`;
}
export const formatUs = (v) => (Number.isFinite(v) ? String(Math.round(v)) : '—');

/** 确定性伪随机波形（离线也有稳定波形可看，不依赖音频设备）。 */
export function waveform(seed = 1, bars = 96) {
  let x = (seed * 9301 + 49297) % 233280;
  const out = [];
  for (let i = 0; i < bars; i++) {
    x = (x * 9301 + 49297) % 233280;
    const r = x / 233280;
    const env = Math.sin((Math.PI * i) / (bars - 1));
    out.push(0.15 + 0.85 * env * r);
  }
  return out;
}

export function timezoneOffsetLabel(d = new Date()) {
  const off = -d.getTimezoneOffset() / 60;
  const sign = off >= 0 ? '+' : '-';
  const abs = Math.abs(off);
  return `UTC${sign}${String(Math.floor(abs)).padStart(2, '0')}:${String(Math.round((abs % 1) * 60)).padStart(2, '0')}`;
}
