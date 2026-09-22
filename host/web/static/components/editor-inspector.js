/* ============================================================================
 * components/editor-inspector.js — 右栏精调：keyframe-inspector /
 * interp-curve-preview / validate-result-list / import-rolepack-dialog
 * 校验清单直接消费 ActionValidateResult（{valid, errors:[{path,message}]}），
 * 失败项可点击定位到时间轴（result-jump-to）。
 * ==========================================================================*/
import { Base, define, ic, esc, emit, num, str } from './base.js';
import { AXES, peakSpeed, smoothstep, US_MIN, US_MAX, formatTime } from '../timeline.js';

class InterpCurvePreview extends Base {
  static observedAttributes = ['expr', 'highlight'];
  template() {
    const pts = [];
    for (let i = 0; i <= 20; i++) {
      const u = i / 20;
      pts.push(`${(u * 100).toFixed(1)},${(100 - smoothstep(u) * 100).toFixed(1)}`);
    }
    return `<figure style="margin:0">
      <svg viewBox="0 0 100 100" width="100%" height="72" role="img"
        aria-label="插值曲线缩略：三次平滑 s(u)=3u²−2u³，两端缓入缓出">
        <polyline points="${pts.join(' ')}" fill="none" stroke="currentColor" stroke-width="3"
          stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"></polyline>
      </svg>
      <figcaption class="xs meta" data-num>${esc(str(this, 'expr', 's(u) = 3u² − 2u³'))} · 只读</figcaption></figure>`;
  }
}
define('interp-curve-preview', InterpCurvePreview);

class KeyframeInspector extends Base {
  static observedAttributes = ['readonly-curve', 'max-speed'];
  /** @param {{index:number,frame:object,minUs:number,maxUs:number}} v */
  set keyframe(v) { this._kf = v; if (this.isConnected) this.render(); }
  get keyframe() { return this._kf; }
  template() {
    const k = this._kf;
    if (!k || !k.frame) {
      return `<div class="stack"><span class="rail-title">关键帧属性</span>
        <p class="xs meta">在时间轴上点选一个菱形关键帧，这里会显示它的时间、四轴目标值与安全校验。</p></div>`;
    }
    const min = k.minUs ?? US_MIN, max = k.maxUs ?? US_MAX;
    const f = k.frame;
    const usFields = AXES.map((a) => {
      const v = f.us[a.i];
      const bad = !Number.isFinite(v) || v < min || v > max;
      return `<div class="field" style="margin-bottom:var(--space-2)">
        <label class="field__label" for="us-${a.i}">轴${a.i + 1} ${esc(a.name)} 目标（µs）</label>
        <input class="input input--num" id="us-${a.i}" type="number" data-us="${a.i}" value="${Number.isFinite(v) ? v : ''}"
          min="${min}" max="${max}" step="1" aria-invalid="${bad}" ${bad ? 'aria-describedby="us-err-' + a.i + '"' : ''}>
        ${bad ? `<span class="field__error" id="us-err-${a.i}">${ic('triangle-alert', 14)}
          超出安全限位 ${min}–${max}µs</span>` : ''}</div>`;
    }).join('');
    const maxSpeed = num(this, 'max-speed', 300);
    const speeds = AXES.map((a) => {
      const p = peakSpeed(k.frames || [], a.i);
      const over = p > maxSpeed;
      return `<div class="inspector__row"><span class="inspector__k">${esc(a.name)} 峰值速度</span>
        <span class="inspector__v" style="color: ${over ? 'var(--danger)' : 'inherit'}">
          ${p.toFixed(1)} / ${maxSpeed} µs/s</span></div>`;
    }).join('');
    return `<div class="stack"><span class="rail-title">关键帧属性 · 第 ${k.index + 1} 帧</span>
      <div class="field" style="margin-bottom:var(--space-2)">
        <label class="field__label" for="kf-t">时间（ms）</label>
        <input class="input input--num" id="kf-t" type="number" data-t value="${f.t_ms}" min="0" max="60000" step="1">
        <span class="field__helper" data-num>${formatTime(f.t_ms)}</span></div>
      ${usFields}
      <interp-curve-preview highlight="${k.index}"></interp-curve-preview>
      <div class="stack">${speeds}</div>
      <div class="rowflex">
        <button class="btn btn--danger" type="button" data-act="del">${ic('trash-2', 16)}删除该帧</button>
        <button class="btn btn--ghost" type="button" data-act="focus">${ic('mouse-pointer-2', 16)}定位</button>
      </div></div>`;
  }
  mounted() {
    this.on('change', '[data-us]', (e, t) => emit(this, 'keyframe-field-change',
      { field: 'us', axis: Number(t.getAttribute('data-us')), value: Number(t.value) }));
    this.on('change', '[data-t]', (e, t) => emit(this, 'keyframe-field-change', { field: 't_ms', value: Number(t.value) }));
    this.on('click', '[data-act="del"]', () => emit(this, 'keyframe-delete', { index: this._kf?.index }));
    this.on('click', '[data-act="focus"]', () => emit(this, 'result-jump-to', { path: `frames[${this._kf?.index}]` }));
  }
}
define('keyframe-inspector', KeyframeInspector);

class ValidateResultList extends Base {
  static observedAttributes = ['busy'];
  /** @param {{valid:boolean, errors:{path:string,message:string}[], warnings?:{path,message}[]}} v */
  set result(v) { this._r = v; if (this.isConnected) this.render(); }
  get result() { return this._r; }
  template() {
    if (str(this, 'busy') === 'true') {
      return `<div class="stack"><span class="rail-title">校验结果</span>
        <span class="rowflex small muted">${ic('loader-circle', 16)}正在校验…</span></div>`;
    }
    const r = this._r;
    if (!r) return `<div class="stack"><span class="rail-title">校验结果</span><p class="xs meta">编辑后自动调用 /actions/validate，此处显示结果。</p></div>`;
    const errs = r.errors || [], warns = r.warnings || [];
    const rows = [
      ...errs.map((e) => ({ tone: 'error', icon: 'circle-x', e })),
      ...warns.map((w) => ({ tone: 'warn', icon: 'triangle-alert', e: w })),
    ].map(({ tone, icon, e }) => `<button class="issue issue--${tone}" type="button" data-jump="${esc(e.path)}"
        style="background:none;border:0;border-bottom:1px solid var(--border-soft);width:100%;text-align:left;cursor:pointer">
        ${ic(icon, 18)}<span class="grow"><span>${esc(e.message)}</span>
        <br><span class="issue__path">${esc(e.path)}</span></span></button>`).join('');
    const ok = r.valid && errs.length === 0;
    return `<div class="stack"><span class="rail-title">校验结果</span>
      ${ok ? `<span class="issue issue--ok">${ic('circle-check', 18)}<span class="grow">校验通过：可安全下发或预演。</span></span>`
        : `<span class="issue issue--error">${ic('circle-x', 18)}<span class="grow">校验未通过（${errs.length} 项阻断）</span></span>${rows}`}
      <p class="xs meta">只校验、不移动硬件（POST /actions/validate）。</p></div>`;
  }
  mounted() { this.on('click', '[data-jump]', (e, t) => emit(this, 'result-jump-to', { path: t.getAttribute('data-jump') })); }
}
define('validate-result-list', ValidateResultList);

class ImportRolepackDialog extends Base {
  static observedAttributes = ['open'];
  /** @param {{name:string, role_id:number, config_version:string, files:number, rejected?:{path,reason}[], ok:boolean}} v */
  set manifest(v) { this._m = v; if (this.isConnected) this.render(); this._syncOpen(); }
  get manifest() { return this._m; }
  template() {
    const m = this._m || {};
    const rejected = m.rejected || [];
    const pending = !!m.pending && !rejected.length;
    const rows = rejected.length
      ? rejected.map((r) => `<li class="issue issue--error">${ic('circle-x', 16)}
          <span class="grow">${esc(r.reason)}<br><span class="issue__path">${esc(r.path)}</span></span></li>`).join('')
      : pending
        ? `<li class="issue issue--warn">${ic('triangle-alert', 16)}<span class="grow">客户端不做预校验；
            确认后由服务端校验路径穿越 / 符号链接 / 压缩比 / 签名（400 => 保留旧版本）</span></li>`
        : `<li class="issue issue--ok">${ic('shield-check', 16)}<span class="grow">路径、符号链接、压缩比与签名校验均通过</span></li>`;
    const note = rejected.length
      ? '导入失败：旧版本已保留，未被替换（AC-25）。'
      : pending ? '确认后提交服务端校验；校验失败不会替换现有版本。' : '导入成功后将原子切换生效目录。';
    return `<dialog class="dlg" data-dlg aria-labelledby="imp-t">
      <h2 class="dlg__title" id="imp-t">${ic('file-archive', 22)}导入角色包</h2>
      <div class="dlg__body stack">
        <div class="inspector__row"><span class="inspector__k">文件</span><span class="inspector__v">${esc(m.name || '—')}</span></div>
        <div class="inspector__row"><span class="inspector__k">将覆盖角色</span><span class="inspector__v">${esc(m.role_id ?? '—')} · v${esc(m.config_version || '—')}</span></div>
        <div class="inspector__row"><span class="inspector__k">文件数</span><span class="inspector__v" data-num>${esc(m.files ?? '—')}</span></div>
        <ul style="list-style:none;margin:0;padding:0">${rows}</ul>
        <p class="xs" style="color:${rejected.length ? 'var(--danger)' : 'inherit'}">${esc(note)}</p>
      </div>
      <div class="dlg__actions">
        <button class="btn btn--ghost" type="button" data-act="cancel">取消</button>
        <button class="btn ${rejected.length ? 'btn--ghost' : 'btn--primary'}" type="button" data-act="ok"
          ${rejected.length ? 'disabled' : ''}>${ic('shield-check', 18)}确认导入</button>
      </div></dialog>`;
  }
  mounted() {
    this.on('click', '[data-act="ok"]', () => { emit(this, 'import-confirm', {}); this.close(); });
    this.on('click', '[data-act="cancel"]', () => { emit(this, 'import-cancel', {}); this.close(); });
    this.on('cancel', '[data-dlg]', (e) => { e.preventDefault(); emit(this, 'import-cancel', {}); this.close(); });
  }
  updated() { this._syncOpen(); }
  _syncOpen() { if (str(this, 'open') === 'true') this.open(); }
  open() { const d = this.$('[data-dlg]'); if (d && !d.open) d.showModal(); }
  close() { const d = this.$('[data-dlg]'); if (d && d.open) d.close(); this.setAttribute('open', 'false'); }
}
define('import-rolepack-dialog', ImportRolepackDialog);
