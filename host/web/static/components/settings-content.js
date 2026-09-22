/* ============================================================================
 * components/settings-content.js — 资源包导入：content-import-card / import-result-list
 * 契约：POST /api/v1/content/import（multipart，request_id + file）
 *   201 => 原子切换生效目录；400 => 逐条拒绝原因，且**旧版本已保留、未被替换**（AC-25）。
 * 拒收类别：../ 路径穿越、绝对路径、符号链接、超量文件、高压缩比、签名/哈希不符。
 * ==========================================================================*/
import { Base, define, ic, esc, emit, str } from './base.js';

const TONE_ICON = { ok: 'circle-check', warn: 'triangle-alert', error: 'circle-x' };

class ImportResultList extends Base {
  static observedAttributes = ['busy', 'kept-old'];
  /** @param {{path:string, tone:'ok'|'warn'|'error', message:string}[]} v */
  set results(v) { this._r = Array.isArray(v) ? v : []; if (this.isConnected) this.render(); }
  get results() { return this._r || []; }
  template() {
    if (str(this, 'busy') === 'true') {
      return `<div class="stack"><span class="rowflex small muted">${ic('loader-circle', 16)}正在校验并导入…</span></div>`;
    }
    const items = this._r || [];
    if (!items.length) {
      return `<p class="xs meta">导入结果会逐条列在这里：通过项与拒收原因（含具体路径）。</p>`;
    }
    const hasError = items.some((r) => r.tone === 'error');
    const rows = items.map((r) => `<li class="issue issue--${r.tone === 'error' ? 'error' : r.tone === 'warn' ? 'warn' : 'ok'}">
      ${ic(TONE_ICON[r.tone] || 'info', 16)}<span class="grow">${esc(r.message)}
      ${r.path ? `<br><span class="issue__path">${esc(r.path)}</span>` : ''}</span></li>`).join('');
    return `<div class="stack"><ul style="list-style:none;margin:0;padding:0">${rows}</ul>
      <p class="xs ${hasError ? '' : 'muted'}" style="color:${hasError ? 'var(--danger)' : 'inherit'}">
        ${hasError ? `${ic('shield-check', 14)} 导入失败：旧版本已保留，未被替换。`
    : `${ic('circle-check', 14)} 导入成功，已原子切换生效目录。`}</p></div>`;
  }
}
define('import-result-list', ImportResultList);

class ContentImportCard extends Base {
  static observedAttributes = ['busy'];
  template() {
    const busy = str(this, 'busy') === 'true';
    return `<div class="stack">
      <div class="field" style="margin-bottom:0">
        <label class="field__label" for="imp-file">角色包 / 内容包（zip）</label>
        <input class="input" id="imp-file" type="file" data-file accept=".zip,application/zip"
          ${busy ? 'disabled' : ''} aria-describedby="imp-h">
        <span class="field__helper" id="imp-h">限制大小与文件数，校验路径、符号链接、压缩比与签名。</span>
      </div>
      <div class="rowflex"><span class="grow small muted" data-name>尚未选择文件</span>
        <button class="btn btn--primary" type="button" data-act="submit" ${busy ? 'disabled' : ''}>
          ${ic('upload', 18)}${busy ? '导入中…' : '导入资源包'}</button></div>
      <p class="xs meta">失败会逐条给出原因，并且不会替换现有版本。</p>
    </div>`;
  }
  mounted() {
    this.on('change', '[data-file]', (e, t) => {
      const f = t.files && t.files[0];
      const name = this.$('[data-name]');
      if (name) name.textContent = f ? `已选择：${f.name}（${Math.round(f.size / 1024)} KB）` : '尚未选择文件';
      if (f) emit(this, 'import-select', { name: f.name, size: f.size });
    });
    this.on('click', '[data-act="submit"]', () => {
      const f = this.$('[data-file]')?.files?.[0];
      if (!f) { emit(this, 'import-submit', { error: '请先选择要导入的 zip 文件' }); return; }
      emit(this, 'import-submit', { file: f, name: f.name });
    });
  }
}
define('content-import-card', ContentImportCard);
