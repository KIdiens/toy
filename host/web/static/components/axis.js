/* ============================================================================
 * components/axis.js — 四轴限位可视化：axis-limit-bar / value-readout /
 * axis-limit-panel / limit-band
 * 硬约束（Spec §7 界面）：
 *   1) 实时值只标「目标位置」，严禁出现「实际角度」字样（PWM 舵机无位置反馈）；
 *   2) 无数据显示 — 并加斜纹底，明确「无数据 ≠ 0」。
 * ==========================================================================*/
import { Base, define, ic, esc, emit, num, str } from './base.js';
import { AXES, US_MIN, US_MAX, US_NEUTRAL, axisState } from '../timeline.js';

const AXIS_TOKEN = (i) => AXES[i - 1]?.token || '--track-1';
const AXIS_NAME = (i) => AXES[i - 1]?.name || `轴${i}`;

class LimitBand extends Base {
  static observedAttributes = ['min-us', 'max-us', 'neutral-us'];
  template() {
    const min = num(this, 'min-us', US_MIN), max = num(this, 'max-us', US_MAX);
    const neutral = num(this, 'neutral-us', US_NEUTRAL);
    const npos = max > min ? ((neutral - min) / (max - min)) * 100 : 50;
    return `<div class="axis-band limit-band" aria-hidden="true"></div>
      <div class="axis-mark" style="left: ${npos}%" aria-hidden="true"></div>`;
  }
}
define('limit-band', LimitBand);

class AxisLimitBar extends Base {
  static observedAttributes = ['axis', 'min-us', 'max-us', 'neutral-us', 'state'];
  /** @param {number[]|null} frame 四轴目标脉宽（null = 无数据） */
  set frame(v) { this._frame = v; if (this.isConnected) this.render(); }
  get frame() { return this._frame; }

  _value() {
    const i = num(this, 'axis', 1) - 1;
    if (!Array.isArray(this._frame)) return null;
    const v = this._frame[i];
    return Number.isFinite(v) ? v : null;
  }
  template() {
    const axis = num(this, 'axis', 1);
    const min = num(this, 'min-us', US_MIN), max = num(this, 'max-us', US_MAX);
    const v = this._value();
    const state = str(this, 'state') || axisState(v, min, max);
    const pos = v === null ? 50 : Math.min(100, Math.max(0, ((v - min) / (max - min)) * 100));
    const nodata = state === 'nodata' || v === null;
    return `<div class="axis-row axis-row--${nodata ? 'nodata' : state}" style="--axis-color: var(${AXIS_TOKEN(axis)})"
      ${nodata ? '' : `aria-label="轴${axis} ${AXIS_NAME(axis)} 目标位置"`}>
      <div class="axis-row__id">
        <span class="axis-row__dot" style="background: var(${AXIS_TOKEN(axis)})" aria-hidden="true"></span>
        ${ic('gauge', 16)}
        <span><span class="axis-row__name">轴${axis} ${esc(AXIS_NAME(axis))}</span>
        <br><span class="xs meta" data-num>中位 ${num(this, 'neutral-us', US_NEUTRAL)}µs · ${min}–${max}µs</span></span>
      </div>
      <div class="axis-track" role="meter" aria-valuemin="${min}" aria-valuemax="${max}"
        aria-valuenow="${v === null ? '' : Math.round(v)}"
        aria-valuetext="${v === null ? '无数据' : `目标 ${Math.round(v)} 微秒`}"
        aria-label="轴${axis} ${esc(AXIS_NAME(axis))} 目标位置">
        <limit-band min-us="${min}" max-us="${max}" neutral-us="${num(this, 'neutral-us', US_NEUTRAL)}"></limit-band>
        ${nodata ? '' : `<span class="axis-cursor" style="--pos: ${pos}%" aria-hidden="true"></span>`}
      </div>
      <div class="axis-row__readout">
        <value-readout label="目标位置" unit="µs" source="target"
          data-available="${nodata ? 'false' : 'true'}" data-value="${v === null ? '' : Math.round(v)}"></value-readout>
      </div></div>`;
  }
  mounted() { this.addEventListener('click', () => emit(this, 'axis-select', { axis: num(this, 'axis', 1) })); }
}
define('axis-limit-bar', AxisLimitBar);

class ValueReadout extends Base {
  static observedAttributes = ['label', 'unit', 'source', 'data-available', 'data-value'];
  template() {
    const label = str(this, 'label', '数值');
    const unit = str(this, 'unit', '');
    const source = str(this, 'source', 'target');
    const avail = str(this, 'data-available', 'true') !== 'false';
    const raw = str(this, 'data-value');
    const shown = avail && raw !== '' ? raw : '—';
    const tag = source === 'target'
      ? `<span class="xs meta">目标位置 · 指令值</span>`
      : `<span class="xs meta">实际值 · 无反馈</span>`;
    return `<div class="readout">
      <div class="readout__label">${esc(label)}</div>
      <div class="readout__value ${avail ? '' : 'no-data'}" title="${avail ? '' : '无数据（不等于 0）'}">
        ${esc(shown)}${unit ? ` <span class="readout__unit">${esc(unit)}</span>` : ''}</div>
      ${tag}</div>`;
  }
}
define('value-readout', ValueReadout);

class AxisLimitPanel extends Base {
  static observedAttributes = ['nodata'];
  /** @param {Array} axes 角色配置的轴限位：[{min_us,max_us,neutral_us,direction,max_speed_us_s}] */
  set axes(v) { this._axes = Array.isArray(v) ? v : null; if (this.isConnected) this.render(); }
  get axes() { return this._axes; }
  template() {
    if (str(this, 'nodata') === 'true' || !this._axes) {
      return `<div class="stack">${AXES.map((a) => `<div class="rowflex">
        <span class="axis-row__dot" style="background: var(${a.token})"></span>
        <span class="grow">${esc(a.name)}</span>
        <span class="no-data xs" title="无数据（不等于 0）">—</span></div>`).join('')}
        <p class="xs meta">未选角色，限位与速度上限尚无来源。</p></div>`;
    }
    const rows = this._axes.map((a, i) => `<div class="inspector__row">
      <span class="inspector__k"><span class="axis-row__dot" style="display:inline-block;background: var(${AXIS_TOKEN(i + 1)})"></span>
        ${esc(a.name || AXIS_NAME(i + 1))}</span>
      <span class="inspector__v" data-num>${a.min_us}–${a.max_us}µs · 中位 ${a.neutral_us ?? US_NEUTRAL}</span></div>`).join('');
    const speed = this._axes[0]?.max_speed_us_s ?? 300;
    return `<div class="stack"><div class="stack">${rows}</div>
      <div class="inspector__row"><span class="inspector__k">峰值速度上限</span>
      <span class="inspector__v" data-num>${speed} µs/s</span></div></div>`;
  }
}
define('axis-limit-panel', AxisLimitPanel);

export { AXIS_NAME, AXIS_TOKEN };
