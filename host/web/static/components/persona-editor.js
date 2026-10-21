/* ============================================================================
 * components/persona-editor.js — 角色人设编辑器（光 DOM 自定义元素）
 * 设计要点
 *  1) **首次渲染后不再整体重绘**：表单是原生输入，重绘会丢焦点/光标位置，
 *     所以字段值、计数、预览区全部走「局部写 DOM」（syncValidity / setPreview）。
 *  2) `load(role, persona)` 是唯一回填入口：三个角色互相切换时把每个字段都**重设**
 *     （persona 为 null 时一律清空 + max_chars 回到默认 100），绝不残留上一个角色的输入。
 *  3) 前端只在**保存前**拦长度/条数（本机校验），**不拦预览**：预览端点要负责把
 *     「尖括号/换行/零宽字符」这类内容错误以内联 errors 交回来，本地先挡就没得显示了。
 * ==========================================================================*/
import { Base, define, ic, esc, emit } from './base.js';
import { LIMITS, FIELD_LABELS, count, previewMarkup } from './persona-preview.js';
import { TEXT_FIELDS, textField, phraseField, phraseRow } from './persona-fields.js';
import { DirtyTracker } from './persona-dirty.js';

class PersonaEditor extends Base {
  // 注意：**不能**叫 this.role —— HTMLElement 自带 ARIA `role` 属性，
  // 赋值会被转成字符串（"object Object"），后续按对象读 role.persona 全是 undefined。
  constructor() { super(); this.currentRole = null; this._serverErrors = {}; this._dirty = new DirtyTracker(); }

  template() {
    return `<div class="stack">
      <p class="small muted">正在编辑：<strong data-role-name>—</strong></p>
      <form class="stack" data-form novalidate>
        ${TEXT_FIELDS.map(textField).join('')}
        ${phraseField('catchphrases', '口头禅', '它常挂嘴边的话，例如「让我想想」。留空的条不会发送。')}
        ${phraseField('avoid', '忌讳', '不希望它说的话题或口气，例如「别说教」。')}
        <div class="field" data-field="max_chars">
          <div class="field__top">
            <label class="field__label" for="pf-max_chars">单次回复不超过多少字</label>
            <span class="xs meta">${LIMITS.maxCharsMin}–${LIMITS.maxCharsMax}，默认 ${LIMITS.maxCharsDefault}</span>
          </div>
          <div class="rowflex">
            <input class="input input--num" id="pf-max_chars" data-in="max_chars" type="number"
              inputmode="numeric" min="${LIMITS.maxCharsMin}" max="${LIMITS.maxCharsMax}" step="10"
              style="max-width: 8rem" aria-describedby="pf-max_chars-hint">
            <span class="small muted">字</span>
          </div>
          <p class="field__helper" id="pf-max_chars-hint">它每次开口只说这么多字，超出由主机截断策略兜底。</p>
          <p class="field__error hidden" data-err="max_chars" role="alert"></p>
        </div>
        <div class="rowflex" data-actions>
          <button class="btn btn--primary" type="submit" data-act="save">
            ${ic('save', 18)}<span data-save-label>保存人设</span></button>
          <button class="btn btn--ghost" type="button" data-act="reset">
            ${ic('refresh-cw', 18)}恢复默认人设</button>
        </div>
        <p class="field__error hidden" data-form-error role="alert"></p>
        <p class="xs meta">「恢复默认人设」只清空本页字段，<strong>不会立刻写入</strong>；
          还要再点「保存人设」，该角色才会真正变回默认人设（自定义段落不再发给模型）。</p>
      </form>
      <section class="card" aria-labelledby="preview-title">
        <div class="preview__head">${ic('eye', 18)}
          <h2 class="section__title" id="preview-title">编译预览</h2>
          <span class="xs meta">不写盘，只展示当前填写会编译成什么</span>
        </div>
        <div class="preview">
          <p class="xs muted" data-preview-note>这段文字会作为角色设定发给模型，
            安全规则始终由主机追加、不可被这段覆盖。</p>
          <div data-preview-live aria-live="polite" aria-atomic="true">
            ${previewMarkup({ status: 'idle' })}
          </div>
        </div>
      </section>
    </div>`;
  }

  mounted() {
    const form = this.$('[data-form]');
    if (!form) return;
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      if (!this.syncValidity()) { this.focusFirstInvalid(); emit(this, 'persona-invalid', {}); return; }
      emit(this, 'persona-save', this.read());
    });
    form.addEventListener('input', () => {
      this._serverErrors = {}; // 用户又改了：上一次 422 的字段结论已过期
      this.syncValidity();
      this.clearFormError();
      emit(this, 'persona-change', this.read());
    });
    this.on('click', '[data-act="add-phrase"]', (_e, btn) => this.addPhrase(btn.getAttribute('data-group')));
    this.on('click', '[data-act="del-phrase"]', (_e, btn) =>
      this.delPhrase(btn.getAttribute('data-group'), Number(btn.getAttribute('data-idx'))));
    this.on('click', '[data-act="reset"]', () => {
      const role = this.currentRole;
      this.load(role, null);
      // 快照留在**已存值**上：清空字段是「未保存的修改」，切换角色前仍需确认。
      this._dirty.mark(role ? role.persona : null);
      emit(this, 'persona-reset', {});
    });
  }

  /* ---------------------------------------------------------------- 回填 */
  /** 唯一的回填入口：persona 为 null/缺字段都按「未填」处理。 */
  load(role, persona) {
    this.currentRole = role || null;
    const p = persona || {};
    this._dirty.mark(p); // 快照 = 磁盘上的值（含 persona 为 null 的空表单）
    this.clearErrors();
    this.clearFormError();
    this.setPreview({ status: 'idle' });
    this._setValue('archetype', p.archetype || '');
    this._setValue('tone', p.tone || '');
    this._setValue('speech_style', p.speech_style || '');
    const mc = Number(p.max_chars);
    this._setValue('max_chars', Number.isFinite(mc) && mc > 0 ? String(mc) : String(LIMITS.maxCharsDefault));
    this._renderPhrases('catchphrases', p.catchphrases || []);
    this._renderPhrases('avoid', p.avoid || []);
    const nameEl = this.$('[data-role-name]');
    if (nameEl) {
      nameEl.textContent = role
        ? `${role.name}（${role.config_version} · 音色 ${role.tts_voice}）`
        : '—';
    }
    this.syncValidity();
  }

  _setValue(name, value) {
    const el = this.$(`[data-in="${name}"]`);
    if (el) el.value = value;
  }
  _rawValues(group) { return this.$$(`[data-in="${group}"]`).map((el) => el.value); }
  _values(group) { return this._rawValues(group).map((v) => v.trim()).filter(Boolean); }

  _renderPhrases(group, values, focusIdx = -1) {
    const box = this.$(`[data-phrases="${group}"]`);
    if (!box) return;
    const items = values.slice(0, LIMITS.phrases);
    if (!items.length) items.push('');
    box.innerHTML = items.map((v, i) => phraseRow(group, i, v)).join('');
    const add = this.$(`[data-act="add-phrase"][data-group="${group}"]`);
    // 按**行数**禁用，不按「有内容的条数」：否则 3 行里有空行时按钮仍可点，
    // 但 slice 会悄悄丢掉第 4 行 —— 正是「按钮点了没反应」那种假可用。
    if (add) add.disabled = items.length >= LIMITS.phrases;
    if (focusIdx >= 0) this.$(`[data-in="${group}"][data-idx="${focusIdx}"]`)?.focus();
  }

  addPhrase(group) {
    const raw = this._rawValues(group);
    if (raw.length >= LIMITS.phrases) return; // 上限按行数，与按钮禁用条件同源
    this._renderPhrases(group, [...raw, ''], raw.length);
    this.syncValidity();
    emit(this, 'persona-change', this.read());
  }

  delPhrase(group, index) {
    const raw = this._rawValues(group);
    raw.splice(index, 1);
    this._renderPhrases(group, raw.length ? raw : [''], Math.max(0, index - 1));
    this.syncValidity();
    emit(this, 'persona-change', this.read());
  }

  /* ---------------------------------------------------------------- 读值 */
  _maxChars() {
    const el = this.$('[data-in="max_chars"]');
    const raw = el ? String(el.value).trim() : '';
    const n = Number(raw);
    return raw !== '' && Number.isFinite(n) ? Math.trunc(n) : LIMITS.maxCharsDefault;
  }

  read() {
    return {
      archetype: this._val('archetype'),
      tone: this._val('tone'),
      speech_style: this._val('speech_style'),
      catchphrases: this._values('catchphrases'),
      avoid: this._values('avoid'),
      max_chars: this._maxChars(),
    };
  }

  _val(name) { return String(this.$(`[data-in="${name}"]`)?.value ?? '').trim(); }

  /** 是否与「磁盘上的值」不同（规范化比较见 persona-dirty.js：空白/空行不算改动）。 */
  isDirty() { return this._dirty.isDirty(this.read()); }

  /* ---------------------------------------------------------------- 校验 */
  /** 本地限长：返回 false 时保存按钮已被真禁用（不是假按钮）。 */
  syncValidity() {
    let ok = true;
    for (const [name, , , limit] of TEXT_FIELDS) {
      const len = count(this._val(name));
      this._counter(name, len, limit);
      const over = len > limit;
      this._renderField(name, over ? `超出 ${limit} 字上限（当前 ${len} 字），请先缩短再保存` : '');
      ok = ok && !over;
    }
    for (const group of ['catchphrases', 'avoid']) {
      const over = [];
      this._rawValues(group).forEach((v, i) => {
        const len = count(v);
        this._counter(`${group}.${i}`, len, LIMITS.phrase);
        if (len > LIMITS.phrase) over.push(`第 ${i + 1} 条超出 ${LIMITS.phrase} 字（当前 ${len} 字）`);
      });
      this._renderField(group, over.join('；'));
      ok = ok && over.length === 0;
    }
    const mc = this._maxChars();
    const mcOk = mc >= LIMITS.maxCharsMin && mc <= LIMITS.maxCharsMax;
    this._renderField('max_chars',
      mcOk ? '' : `需在 ${LIMITS.maxCharsMin} 到 ${LIMITS.maxCharsMax} 之间（当前 ${mc}）`);
    ok = ok && mcOk;

    const btn = this.$('[data-act="save"]');
    if (btn && !btn.getAttribute('data-busy')) {
      btn.disabled = !ok;
      btn.title = ok ? '' : '有字段超出上限，已拦下保存；请先按提示缩短';
    }
    return ok;
  }

  /**
   * 合并显示：本地限长优先，其次保留上一次保存 422 的字段结论。
   * 为什么必须合并：`setSaving(false)`、任何输入都会跑一遍 syncValidity，
   * 若它无条件把错误位写成空串，刚显示出来的 422 红字会立刻被自己擦掉
   * （表现是「弹了一下就没了」，用户根本读不到哪个字段不合规）。
   */
  _renderField(field, localMessage) {
    this._setFieldError(field, localMessage || this._serverErrors[field] || '');
  }

  _counter(key, len, limit) {
    const cell = this.$(`[data-count="${key}"]`);
    if (!cell) return;
    cell.setAttribute('data-over', String(len > limit));
    const num = cell.querySelector('[data-len]');
    if (num) num.textContent = String(len);
  }

  _setFieldError(field, message) {
    const box = this.$(`[data-err="${field}"]`);
    if (box) {
      box.innerHTML = message ? `${ic('triangle-alert', 14)}<span>${esc(message)}</span>` : '';
      box.classList.toggle('hidden', !message);
    }
    const input = this.$(`[data-in="${field}"]`);
    if (input) input.setAttribute('aria-invalid', message ? 'true' : 'false');
  }

  clearErrors() {
    this._serverErrors = {};
    ['archetype', 'tone', 'speech_style', 'catchphrases', 'avoid', 'max_chars']
      .forEach((f) => this._setFieldError(f, ''));
  }

  /** 422 的 message 形如「请求参数校验失败：archetype: ...；tone: ...」，逐条落到字段行。 */
  applyServerErrors(err) {
    const raw = String((err && err.message) || '');
    const parts = raw.replace(/^请求参数校验失败[:：]?/, '').split('；');
    let matched = 0;
    parts.forEach((part) => {
      const m = part.match(/^\s*([a-zA-Z_]+)(?:\.[0-9]+)?\s*[:：]\s*(.+)$/);
      if (!m) return;
      const field = m[1];
      if (!(field in FIELD_LABELS)) return;
      this._serverErrors[field] = m[2].replace(/^Value error,\s*/, '');
      this._renderField(field, '');
      matched += 1;
    });
    if (!matched) this.showFormError(raw || '保存失败：内容不合规');
    else this.showFormError('有字段不合规，已标在对应字段下方；改动未写入。');
  }

  showFormError(message) {
    const el = this.$('[data-form-error]');
    if (!el) return;
    el.innerHTML = `${ic('circle-x', 14)}<span>${esc(message)}</span>`;
    el.classList.toggle('hidden', !message);
  }

  clearFormError() { this.showFormError(''); }

  focusFirstInvalid() {
    const el = this.$('[aria-invalid="true"]');
    if (el) el.focus();
  }

  setPreview(state) {
    const box = this.$('[data-preview-live]');
    if (box) box.innerHTML = previewMarkup(state);
  }

  setSaving(on) {
    const btn = this.$('[data-act="save"]');
    const label = this.$('[data-save-label]');
    if (label) label.textContent = on ? '保存中…' : '保存人设';
    if (!btn) return;
    if (on) { btn.setAttribute('data-busy', 'true'); btn.disabled = true; }
    else { btn.removeAttribute('data-busy'); this.syncValidity(); }
  }
}

define('persona-editor', PersonaEditor);
export { PersonaEditor };
