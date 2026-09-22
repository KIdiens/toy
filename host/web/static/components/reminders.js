/* ============================================================================
 * components/reminders.js — 提醒管理：reminder-list / reminder-row / status-pill /
 * next-fire-readout / reminder-form / time-field / repeat-picker / voice-select /
 * clock-warning
 * 纪律：写操作带 request_id，同一表单**稳定 idempotency_key** 防重复创建；
 *      POST 422 = 需要澄清（不关表单、错误靠近字段）；时间不可靠时暂停绝对提醒。
 * ==========================================================================*/
import { Base, define, ic, esc, emit, str, bool } from './base.js';

const TZ = 'Asia/Shanghai';
const STATUS = {
  pending: { label: '待触发', icon: 'clock', cls: 'pill--pending' },
  fired: { label: '已触发', icon: 'circle-check', cls: 'pill--fired' },
  missed: { label: '已错过', icon: 'triangle-alert', cls: 'pill--missed' },
  cancelled: { label: '已取消', icon: 'circle-slash', cls: 'pill--cancelled' },
};
const REPEAT = { once: '仅一次', daily: '每天', weekdays: '工作日' };

function fmtInZone(iso, tz = TZ) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return esc(iso);
  try {
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: tz, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(d);
  } catch { return d.toISOString().slice(0, 16).replace('T', ' '); }
}

/** 下次触发：一次性取 due_at；重复规则按本地时刻推进到下一未来时刻。 */
export function nextFire(r, now = new Date()) {
  if (!r || !r.due_at_utc) return null;
  if (r.status === 'cancelled') return null;
  const due = new Date(r.due_at_utc);
  if (Number.isNaN(due.getTime())) return null;
  const rule = r.repeat_rule;
  if (!rule || rule === 'once') return due;
  const next = new Date(now);
  next.setHours(due.getHours(), due.getMinutes(), 0, 0);
  const step = () => next.setDate(next.getDate() + 1);
  if (rule === 'weekdays') { while (next <= now || next.getDay() === 0 || next.getDay() === 6) step(); }
  else { while (next <= now) step(); }
  return next;
}

export function isoToLocalTime(iso, tz = TZ) {
  if (!iso) return '20:00';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '20:00';
  try {
    const parts = new Intl.DateTimeFormat('en-GB', { timeZone: tz, hour: '2-digit', minute: '2-digit', hour12: false }).format(d);
    return parts;
  } catch { return '20:00'; }
}

class StatusPill extends Base {
  static observedAttributes = ['status'];
  template() {
    const s = STATUS[str(this, 'status', 'pending')] || STATUS.pending;
    return `<span class="pill ${s.cls} rowflex" style="gap:var(--space-1);display:inline-flex">
      ${ic(s.icon, 14)}${esc(s.label)}</span>`;
  }
}
define('status-pill', StatusPill);

class NextFireReadout extends Base {
  static observedAttributes = ['due-at', 'enabled', 'paused'];
  template() {
    const enabled = str(this, 'enabled', 'true') !== 'false';
    const paused = bool(this, 'paused');
    const due = str(this, 'due-at');
    if (paused) return `<span class="rowflex small" style="color:var(--warn)">${ic('clock-alert', 16)}
      <span>已暂停，等待校时</span></span>`;
    if (!enabled) return `<span class="small muted">已暂停</span>`;
    return `<span class="rowflex small">${ic('timer', 16)}<span data-num>${esc(fmtInZone(due))}</span></span>`;
  }
}
define('next-fire-readout', NextFireReadout);

class ClockWarning extends Base {
  static observedAttributes = ['state'];
  template() {
    return `<div class="banner banner--fault" role="status" aria-live="polite">
      <span class="banner__ic">${ic('clock-alert', 20)}</span>
      <span class="banner__text">设备尚未确认当前时间，按绝对时间设置的提醒已<strong>暂停</strong>，不会错发。</span>
      <span class="banner__actions"><button class="btn btn--secondary" type="button" data-act="sync">
        ${ic('refresh-cw', 16)}校时指引</button></span></div>`;
  }
  mounted() { this.on('click', '[data-act="sync"]', () => emit(this, 'clock-sync-open', {})); }
}
define('clock-warning', ClockWarning);

class ReminderRow extends Base {
  static observedAttributes = ['reminder-id', 'status', 'timezone'];
  set reminder(v) { this._r = v; if (this.isConnected) this.render(); }
  get reminder() { return this._r; }
  _confirming = false;
  template() {
    const r = this._r || {};
    const status = r.status || str(this, 'status', 'pending');
    const tz = r.timezone || str(this, 'timezone', TZ);
    const enabled = status === 'pending';
    const next = nextFire(r);
    const time = isoToLocalTime(r.due_at_utc, tz);
    return `<div class="row" role="listitem" style="grid-template-columns: 128px minmax(0,1fr) 120px 110px 160px auto">
      <div><span class="timecode">${esc(r.repeat_rule && r.repeat_rule !== 'once' ? REPEAT[r.repeat_rule] || r.repeat_rule : '')}
        <span data-num>${esc(r.repeat_rule && r.repeat_rule !== 'once' ? time : fmtInZone(r.due_at_utc, tz))}</span></span>
        ${!r.repeat_rule || r.repeat_rule === 'once' ? '' : `<br><span class="xs meta" data-num>${esc(time)}</span>`}
      </div>
      <div class="row__main"><div class="row__value" title="${esc(r.text)}">${esc(r.text || '—')}</div>
        <div class="row__meta">${esc(r.profile_id || '本机档案')} · 幂等键 ${esc((r.idempotency_key || '—').slice(0, 8))}</div></div>
      <span class="small muted rowflex">${ic('user-round', 16)}${esc(r.tts_voice || '默认音色')}</span>
      <status-pill status="${esc(status)}"></status-pill>
      <next-fire-readout due-at="${esc(next ? next.toISOString() : '')}" enabled="${enabled}" paused="${r.paused}"></next-fire-readout>
      <span class="row__actions">
        <button class="btn btn--ghost" type="button" data-act="toggle" role="switch" aria-checked="${enabled}"
          disabled aria-disabled="true"
          title="API 契约（openapi.yaml）只提供创建与取消，无修改端点；暂停/启用暂不可写入后端">${ic('toggle-right', 16)}${enabled ? '暂停' : '启用'}</button>
        ${this._confirming ? `<inline-confirm label="取消这条提醒"></inline-confirm>`
          : `<button class="btn btn--danger" type="button" data-act="cancel">${ic('trash-2', 16)}取消</button>`}
      </span></div>`;
  }
  mounted() {
    this.on('click', '[data-act="toggle"]', () => emit(this, 'row-toggle', { id: this._r?.id, enable: !(this._r?.status === 'pending') }));
    this.on('click', '[data-act="cancel"]', () => { this._confirming = true; this.render(); this.$('[data-act="yes"]')?.focus(); });
    this.addEventListener('confirm', () => emit(this, 'row-cancel', { id: this._r?.id ?? str(this, 'reminder-id') }));
    this.addEventListener('cancel', () => { this._confirming = false; this.render(); });
  }
}
define('reminder-row', ReminderRow);

class ReminderList extends Base {
  static observedAttributes = ['busy'];
  set items(v) { this._items = Array.isArray(v) ? v : []; if (this.isConnected) this.render(); }
  get items() { return this._items || []; }
  template() {
    if (str(this, 'busy') === 'true') return `<loader-skeleton rows="3"></loader-skeleton>`;
    const items = this._items || [];
    const head = `<div class="row xs meta" role="presentation" aria-hidden="true"
      style="grid-template-columns: 128px minmax(0,1fr) 120px 110px 160px auto">
      <span>时间 / 重复</span><span>内容</span><span>角色音色</span><span>状态</span><span>下次触发</span><span class="right">操作</span></div>`;
    const rows = items.map((r) => `<reminder-row reminder-id="${esc(r.id)}" status="${esc(r.status)}"
      timezone="${esc(r.timezone || TZ)}"></reminder-row>`).join('');
    return `<div role="list" aria-label="提醒列表">${head}<div class="rows">${rows}</div></div>`;
  }
  mounted() {
    const rows = this.$$('reminder-row');
    (this._items || []).forEach((r, i) => { if (rows[i]) rows[i].reminder = r; });
    this.addEventListener('row-toggle', (e) => { e.stopPropagation(); emit(this, 'row-toggle', e.detail); });
    this.addEventListener('row-cancel', (e) => { e.stopPropagation(); emit(this, 'row-cancel', e.detail); });
  }
}
define('reminder-list', ReminderList);

class TimeField extends Base {
  static observedAttributes = ['value'];
  template() {
    return `<div class="field"><label class="field__label" for="rf-time">提醒时间</label>
      <input class="input" id="rf-time" type="time" data-time value="${esc(str(this, 'value', '20:00'))}"
        aria-describedby="rf-time-h"><span class="field__helper" id="rf-time-h">按设备本地时间（${TZ}）设置，存储为 UTC。</span>
      <span class="field__error hidden" data-err>${ic('triangle-alert', 14)}<span></span></span></div>`;
  }
  mounted() { this.on('change', '[data-time]', (e, t) => emit(this, 'time-change', { value: t.value })); }
  showError(msg) {
    const box = this.$('[data-err]');
    if (!box) return;
    box.classList.toggle('hidden', !msg);
    box.querySelector('span').textContent = msg || '';
    this.$('[data-time]')?.setAttribute('aria-invalid', msg ? 'true' : 'false');
  }
}
define('time-field', TimeField);

class RepeatPicker extends Base {
  static observedAttributes = ['selected'];
  template() {
    const sel = str(this, 'selected', 'daily');
    const opts = ['once', 'daily', 'weekdays'].map((k) => `<label class="switch small" style="min-height:auto">
      <input type="radio" name="rp-${this._uid || (this._uid = Math.random().toString(36).slice(2))}"
        value="${k}" data-rep ${k === sel ? 'checked' : ''}>${esc(REPEAT[k])}</label>`).join('');
    return `<fieldset class="field" style="border:0;padding:0;margin:0 0 var(--space-4)">
      <legend class="field__label">重复</legend><div class="rowflex" role="radiogroup" aria-label="重复规则">${opts}</div></fieldset>`;
  }
  mounted() { this.on('change', '[data-rep]', (e, t) => emit(this, 'repeat-change', { value: t.value })); }
}
define('repeat-picker', RepeatPicker);

class VoiceSelect extends Base {
  static observedAttributes = ['role-id'];
  set roles(v) { this._roles = Array.isArray(v) ? v : []; if (this.isConnected) this._fill(); }
  template() {
    return `<div class="field"><label class="field__label" for="rf-voice">角色音色</label>
      <select class="select" id="rf-voice" data-voice></select>
      <span class="field__helper">未注册角色时先用默认音色。</span></div>`;
  }
  mounted() { this.on('change', '[data-voice]', (e, t) => emit(this, 'voice-change', { role_id: Number(t.value) })); }
  updated() { this._fill(); }
  _fill() {
    const sel = this.$('[data-voice]');
    if (!sel) return;
    const cur = str(this, 'role-id');
    const list = this._roles || [];
    sel.innerHTML = list.length
      ? list.map((r) => `<option value="${r.role_id}" ${String(r.role_id) === cur ? 'selected' : ''}>
          ${esc(r.name || '角色 ' + r.role_id)}${r.tts_voice ? ' · ' + esc(r.tts_voice) : ''}</option>`).join('')
      : `<option value="">— 默认音色</option>`;
  }
}
define('voice-select', VoiceSelect);

class ReminderForm extends Base {
  template() {
    const mode = str(this, 'mode', 'create');
    return `<form class="card" data-form novalidate aria-label="${mode === 'edit' ? '编辑提醒' : '新建提醒'}">
      <div class="section__head" style="margin-bottom:var(--space-3)">
        <span class="section__ic">${ic('square-pen', 20)}</span>
        <h2 class="section__title">${mode === 'edit' ? '编辑提醒' : '新建提醒'}</h2></div>
      <time-field value="20:00"></time-field>
      <repeat-picker selected="daily"></repeat-picker>
      <div class="field"><label class="field__label" for="rf-text">提醒内容</label>
        <input class="input" id="rf-text" data-text maxlength="200" placeholder="例如：该刷牙啦"
          aria-describedby="rf-text-h rf-text-err">
        <span class="field__helper" id="rf-text-h">最多 200 字。夜间静音时段设备不会主动发声。</span>
        <span class="field__error hidden" id="rf-text-err">${ic('triangle-alert', 14)}<span></span></span></div>
      <voice-select></voice-select>
      <div class="rowflex" style="justify-content:flex-end">
        <button class="btn btn--ghost" type="button" data-act="cancel">取消</button>
        <button class="btn btn--primary" type="submit" data-act="submit">${ic('check', 18)}保存提醒</button>
      </div></form>`;
  }
  mounted() {
    this._key = this._newKey();
    this.on('click', '[data-act="cancel"]', () => emit(this, 'form-cancel', {}));
    this.on('submit', '[data-form]', (e) => { e.preventDefault(); this._submit(); });
  }
  _newKey() { return 'rmd-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8); }
  _submit() {
    const text = this.$('[data-text]')?.value.trim() || '';
    if (!text) { this.showError('text', '请填写提醒内容'); this.$('[data-text]')?.focus(); return; }
    const time = this.$('time-field')?.$('[data-time]')?.value || '20:00';
    const repeat = this.$('repeat-picker')?.$('[data-rep]:checked')?.value || 'daily';
    const role_id = Number(this.$('voice-select')?.$('[data-voice]')?.value || 1);
    emit(this, 'form-submit', { text, time, repeat_rule: repeat, role_id, idempotency_key: this._key });
  }
  set busy(v) {
    const btn = this.$('[data-act="submit"]');
    if (btn) { btn.disabled = !!v; btn.classList.toggle('hidden', false); }
  }
  /** 422/网络失败：不关表单，错误靠近字段（AC：澄清而非崩溃）。 */
  showError(field, msg) {
    if (field === 'text') {
      const box = this.$('#rf-text-err');
      box?.classList.toggle('hidden', !msg);
      if (box) box.querySelector('span').textContent = msg || '';
    }
    this.$('time-field')?.showError(field === 'time' ? msg : '');
  }
  reset() { this._key = this._newKey(); this.$('[data-text]') && (this.$('[data-text]').value = ''); this.showError('text', ''); this.showError('time', ''); }
  focusFirst() { this.$('[data-text]')?.focus(); }
}
define('reminder-form', ReminderForm);
