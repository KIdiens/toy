/* ============================================================================
 * components/timeline-ui.js — 时间轴视觉层：timeline-ruler / playhead /
 * keyframe-marker（菱形）/ track-lane（含安全限位带）/ audio-track-lane（波形轨）
 * 交互：可拖播放指针、可拖关键帧、方向键微调、Delete 删除；拖出限位即时回弹 + 红闪。
 * 数值坐标：x = t/duration，y = 1 − (us−min)/(max−min)（lane 内竖向即安全区间带）。
 * ==========================================================================*/
import { Base, define, ic, esc, emit, num, str } from './base.js';
import { US_MIN, US_MAX, formatTime } from '../timeline.js';

const pct = (v, span) => `${Math.min(100, Math.max(0, (v / span) * 100))}%`;
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

class TimelineRuler extends Base {
  static observedAttributes = ['duration-ms', 'playhead-ms'];
  template() {
    const d = Math.max(1, num(this, 'duration-ms', 6000));
    const step = d <= 3000 ? 250 : d <= 12000 ? 500 : d <= 30000 ? 1000 : 5000;
    let ticks = '';
    for (let t = 0; t <= d; t += step) {
      ticks += `<span class="ruler__tick" style="left: ${pct(t, d)}" aria-hidden="true"></span>
        <span class="ruler__label" style="left: calc(${pct(t, d)} + 3px)">${t % 1000 === 0 ? `${t / 1000}s` : `${t}ms`}</span>`;
    }
    return `<div class="ruler" role="slider" tabindex="0" aria-label="时间刻度，方向键移动播放指针"
      aria-valuemin="0" aria-valuemax="${d}" aria-valuenow="${num(this, 'playhead-ms', 0)}"
      aria-valuetext="${formatTime(num(this, 'playhead-ms', 0))}" data-ruler>${ticks}</div>`;
  }
  mounted() {
    const d = () => Math.max(1, num(this, 'duration-ms', 6000));
    const seek = (clientX) => {
      const r = this.$('[data-ruler]').getBoundingClientRect();
      emit(this, 'ruler-seek', { ms: clamp(((clientX - r.left) / r.width) * d(), 0, d()) });
    };
    let dragging = false;
    this.addEventListener('pointerdown', (e) => { dragging = true; this.setPointerCapture?.(e.pointerId); seek(e.clientX); });
    this.addEventListener('pointermove', (e) => { if (dragging) seek(e.clientX); });
    this.addEventListener('pointerup', () => { dragging = false; });
    this.addEventListener('keydown', (e) => {
      const cur = num(this, 'playhead-ms', 0);
      const dlt = e.shiftKey ? 100 : 10;
      if (e.key === 'ArrowLeft') { emit(this, 'ruler-seek', { ms: clamp(cur - dlt, 0, d()) }); e.preventDefault(); }
      if (e.key === 'ArrowRight') { emit(this, 'ruler-seek', { ms: clamp(cur + dlt, 0, d()) }); e.preventDefault(); }
    });
  }
}
define('timeline-ruler', TimelineRuler);

class Playhead extends Base {
  static observedAttributes = ['ms', 'duration-ms'];
  template() {
    const d = Math.max(1, num(this, 'duration-ms', 6000));
    const ms = num(this, 'ms', 0);
    const left = pct(ms, d);
    return `<div class="playhead" style="left: ${left}" data-ph>
      <span class="playhead__grip" role="slider" tabindex="0" aria-label="播放指针"
        aria-valuemin="0" aria-valuemax="${d}" aria-valuenow="${ms}"
        aria-valuetext="${formatTime(ms)}"></span></div>`;
  }
  mounted() {
    const d = () => Math.max(1, num(this, 'duration-ms', 6000));
    const move = (clientX) => {
      const host = this.parentElement?.getBoundingClientRect();
      if (!host) return;
      emit(this, 'playhead-seek', { ms: clamp(((clientX - host.left) / host.width) * d(), 0, d()) });
    };
    let drag = false;
    this.addEventListener('pointerdown', (e) => { drag = true; e.preventDefault(); this.setPointerCapture?.(e.pointerId); move(e.clientX); });
    this.addEventListener('pointermove', (e) => { if (drag) move(e.clientX); });
    this.addEventListener('pointerup', () => { drag = false; });
  }
}
define('timeline-playhead', Playhead);

class TrackLane extends Base {
  /** @param {{frames:Array,durationMs:number,selected:number}} v */
  set model(v) { this._m = v; if (this.isConnected) this.render(); }
  get model() { return this._m; }
  template() {
    const m = this._m || { frames: [], durationMs: 6000, selected: -1 };
    const min = num(this, 'min-us', US_MIN), max = num(this, 'max-us', US_MAX);
    const dur = Math.max(1, m.durationMs);
    const muted = str(this, 'muted', 'false') === 'true';
    const band = `position:absolute;left:0;right:0;top:4px;bottom:4px;border-radius:var(--radius-sm);
      background:linear-gradient(180deg,var(--limit-near) 0,var(--limit-normal) 10%,var(--limit-normal) 90%,var(--limit-near) 100%)`;
    const marks = (m.frames || []).map((f, i) => {
      const us = Array.isArray(f.us) ? f.us[num(this, 'axis', 0)] : null;
      const out = Number.isFinite(us) && (us < min || us > max);
      const top = Number.isFinite(us) ? `${100 - ((clamp(us, min, max) - min) / (max - min)) * 100}%` : '50%';
      return `<button class="kf" type="button" data-kf="${i}" data-out="${out}"
        aria-selected="${i === m.selected}" style="left: ${pct(f.t_ms, dur)}; top: ${top}"
        aria-label="关键帧 ${i + 1}：${f.t_ms} 毫秒，目标 ${Number.isFinite(us) ? Math.round(us) : '无'} 微秒"
        data-us="${Number.isFinite(us) ? us : ''}"></button>`;
    }).join('');
    return `<div class="lane" data-lane data-muted="${muted}" aria-label="轴${num(this, 'axis', 0) + 1} 轨道">
      <div class="lane__band" style="${band}" aria-hidden="true"></div>${marks}</div>`;
  }
  mounted() {
    const geom = () => {
      const r = this.$('[data-lane]').getBoundingClientRect();
      const m = this._m || { durationMs: 6000, frames: [] };
      return { r, dur: Math.max(1, m.durationMs), min: num(this, 'min-us', US_MIN), max: num(this, 'max-us', US_MAX) };
    };
    const axis = () => num(this, 'axis', 0);
    const fromEvent = (e) => {
      const { r, dur, min, max } = geom();
      const x = clamp((e.clientX - r.left) / r.width, 0, 1);
      const y = clamp((e.clientY - r.top) / r.height, 0, 1);
      return { t_ms: Math.round(x * dur), us: Math.round(max - y * (max - min)) };
    };
    let dragIdx = -1;
    this.addEventListener('pointerdown', (e) => {
      const kf = e.target.closest('[data-kf]');
      if (!kf) { emit(this, 'lane-seek', { ms: fromEvent(e).t_ms }); return; }
      dragIdx = Number(kf.getAttribute('data-kf'));
      emit(this, 'keyframe-select', { axis: axis(), index: dragIdx });
      this.setPointerCapture?.(e.pointerId);
      e.preventDefault();
    });
    this.addEventListener('pointermove', (e) => {
      if (dragIdx < 0) return;
      const p = fromEvent(e);
      emit(this, 'keyframe-move', { axis: axis(), index: dragIdx, t_ms: p.t_ms, us: p.us, commit: false });
    });
    const end = (e) => {
      if (dragIdx < 0) return;
      const p = fromEvent(e);
      emit(this, 'keyframe-move', { axis: axis(), index: dragIdx, t_ms: p.t_ms, us: p.us, commit: true });
      dragIdx = -1;
    };
    this.addEventListener('pointerup', end);
    this.addEventListener('keydown', (e) => {
      const kf = e.target.closest('[data-kf]');
      if (!kf) return;
      const i = Number(kf.getAttribute('data-kf'));
      const frames = (this._m && this._m.frames) || [];
      const f = frames[i];
      if (!f) return;
      const a = axis();
      const cur = f.us[a];
      const step = e.shiftKey ? 100 : 10;
      const usStep = e.shiftKey ? 25 : 5;
      const send = (t_ms, us) => { emit(this, 'keyframe-move', { axis: a, index: i, t_ms, us, commit: true }); e.preventDefault(); };
      if (e.key === 'ArrowLeft') send(Math.max(0, f.t_ms - step), cur);
      if (e.key === 'ArrowRight') send(f.t_ms + step, cur);
      if (e.key === 'ArrowUp') send(f.t_ms, cur + usStep);
      if (e.key === 'ArrowDown') send(f.t_ms, cur - usStep);
      if (e.key === 'Delete' || e.key === 'Backspace') { emit(this, 'keyframe-delete', { axis: a, index: i }); e.preventDefault(); }
    });
  }
}
define('track-lane', TrackLane);

class AudioTrackLane extends Base {
  static observedAttributes = ['offset-ms', 'duration-ms', 'src'];
  /** @param {number[]} v 波形幅度数组 */
  set waveform(v) { this._w = Array.isArray(v) ? v : []; if (this.isConnected) this.render(); }
  get waveform() { return this._w; }
  template() {
    const d = Math.max(1, num(this, 'duration-ms', 6000));
    const off = num(this, 'offset-ms', 0);
    const bars = (this._w || []).map((a) => `<span class="wave__bar" style="height: ${Math.round(a * 40)}px"></span>`).join('');
    const src = str(this, 'src');
    return `<div class="audio-lane" aria-label="音频轨道，可拖动调整同步偏移">
      <div class="wave" style="left: ${pct(off, d)}; right: 0">${bars}</div>
      <div style="position:absolute;left:var(--space-2);top:var(--space-1)">
        <span class="chip chip--mono">${ic('audio-lines', 14)}
          ${src ? esc(src) : '演示波形'} · 偏移 <span data-num>${off}ms</span></span></div></div>`;
  }
  mounted() {
    let drag = false, startX = 0, startOff = 0;
    this.addEventListener('pointerdown', (e) => {
      drag = true; startX = e.clientX; startOff = num(this, 'offset-ms', 0);
      this.setPointerCapture?.(e.pointerId);
    });
    this.addEventListener('pointermove', (e) => {
      if (!drag) return;
      const w = this.getBoundingClientRect().width || 1;
      const d = Math.max(1, num(this, 'duration-ms', 6000));
      const next = clamp(Math.round(startOff + ((e.clientX - startX) / w) * d), 0, d);
      emit(this, 'audio-offset-change', { offset_ms: next });
    });
    this.addEventListener('pointerup', () => { drag = false; });
  }
}
define('audio-track-lane', AudioTrackLane);

export { pct };
