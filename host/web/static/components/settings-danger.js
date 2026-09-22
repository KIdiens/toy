/* ============================================================================
 * components/settings-danger.js — 高级 · 危险区：danger-zone / factory-reset-dialog /
 * confirm-word-field / countdown-button
 * 不可逆操作三闸（Spec §7）：① 折叠展开才露出入口；② 逐字输入 RESET（大小写敏感）；
 * ③ 输入正确后 5 秒冷却，冷却结束才可点执行；关窗即清零。
 * 危险区永远置底、独立分组、danger 描边，不与主操作同排。
 * ==========================================================================*/
import { Base, define, ic, esc, emit, num, str } from './base.js';

class DangerZone extends Base {
  static slot = true;
  static observedAttributes = ['title'];
  template() {
    return `<section class="card card--pad0" style="border-color:var(--danger)" role="group" aria-labelledby="dz-t">
      <div class="section__head" style="padding:var(--space-4) var(--space-4) 0">
        <span class="section__ic" style="color:var(--danger)">${ic('triangle-alert', 20)}</span>
        <div><h2 class="section__title" id="dz-t" style="color:var(--danger)">${esc(str(this, 'title', '危险区'))}</h2>
          <p class="section__hint">以下操作不可逆，请谨慎确认。展开后才会出现入口。</p></div></div>
      <div style="padding:var(--space-4)"><span data-slot=""></span></div></section>`;
  }
}
define('danger-zone', DangerZone);

class ConfirmWordField extends Base {
  static observedAttributes = ['expect'];
  template() {
    const expect = str(this, 'expect', 'RESET');
    return `<div class="field"><label class="field__label" for="dz-word">输入 <span class="mono">${esc(expect)}</span> 以继续</label>
      <input class="input input--num" id="dz-word" data-word autocomplete="off" spellcheck="false"
        autocapitalize="off" placeholder="${esc(expect)}" aria-describedby="dz-word-h">
      <span class="field__helper" id="dz-word-h">大小写敏感，必须逐字相同。</span></div>`;
  }
  mounted() {
    this.on('input', '[data-word]', (e, t) => emit(this, 'confirm-word-change',
      { value: t.value, ok: t.value === str(this, 'expect', 'RESET') }));
  }
  focus() { this.$('[data-word]')?.focus(); }
  clear() { const i = this.$('[data-word]'); if (i) i.value = ''; }
}
define('confirm-word-field', ConfirmWordField);

class CountdownButton extends Base {
  static observedAttributes = ['cooldown-seconds'];
  template() {
    return `<button class="btn btn--danger" type="button" data-fire disabled aria-disabled="true"
      aria-live="polite" data-label>执行恢复出厂设置</button>`;
  }
  mounted() {
    this.duration = num(this, 'cooldown-seconds', 5);
    this.on('click', '[data-fire]', () => { if (!this._running && !this._blocked) emit(this, 'fire', {}); });
  }
  /** armed=true => 启动 5 秒冷却；armed=false => 立即禁用并清零。 */
  set armed(v) {
    this._blocked = !v;
    if (v) this._start(); else this.reset();
  }
  _start() {
    this.reset();
    const btn = this.$('[data-fire]');
    if (!btn) return;
    let left = this.duration;
    this._running = true;
    btn.disabled = true;
    btn.textContent = `冷却中… ${left}s`;
    this._timer = setInterval(() => {
      left -= 1;
      if (left > 0) { btn.textContent = `冷却中… ${left}s`; return; }
      clearInterval(this._timer); this._timer = null; this._running = false;
      btn.disabled = false;
      btn.textContent = '执行恢复出厂设置';
    }, 1000);
  }
  reset() {
    if (this._timer) { clearInterval(this._timer); this._timer = null; }
    this._running = false;
    const btn = this.$('[data-fire]');
    if (btn) { btn.disabled = true; btn.textContent = '执行恢复出厂设置'; }
  }
  disconnectedCallback() { this.reset(); }
}
define('countdown-button', CountdownButton);

class FactoryResetDialog extends Base {
  static observedAttributes = ['open'];
  template() {
    return `<dialog class="dlg" data-dlg aria-labelledby="fr-t">
      <h2 class="dlg__title" id="fr-t">${ic('rotate-ccw', 22)}恢复出厂设置</h2>
      <div class="dlg__body stack">
        <p class="small">将清空记忆、提醒与本地设置，并回到初始角色配置。此操作<strong>不可撤销</strong>。</p>
        <confirm-word-field expect="RESET"></confirm-word-field>
        <p class="xs meta">输入正确后会进入 <span data-num>5</span> 秒冷却，冷却结束才能执行；关闭窗口将清零。</p>
      </div>
      <div class="dlg__actions">
        <button class="btn btn--ghost" type="button" data-act="cancel">取消</button>
        <countdown-button cooldown-seconds="5"></countdown-button>
      </div></dialog>`;
  }
  mounted() {
    this.on('confirm-word-change', (e) => { e.stopPropagation(); this.$('countdown-button').armed = e.detail.ok; });
    this.on('click', '[data-act="cancel"]', () => { this.close(); emit(this, 'reset-cancel', {}); });
    this.on('fire', (e) => { e.stopPropagation(); emit(this, 'reset-confirm', {}); this.close(); });
    this.on('cancel', '[data-dlg]', (e) => { e.preventDefault(); this.close(); emit(this, 'reset-cancel', {}); });
  }
  updated() { if (str(this, 'open') === 'true') this.open(); }
  open() {
    const d = this.$('[data-dlg]');
    if (d && !d.open) d.showModal();
    this.$('confirm-word-field')?.clear();
    this.$('countdown-button')?.reset();
    this.$('confirm-word-field')?.focus();
  }
  close() {
    const d = this.$('[data-dlg]');
    if (d && d.open) d.close();
    this.$('countdown-button')?.reset(); // 关窗清零：冷却与已输入词都不保留
    this.$('confirm-word-field')?.clear();
    this.setAttribute('open', 'false');
  }
}
define('factory-reset-dialog', FactoryResetDialog);
