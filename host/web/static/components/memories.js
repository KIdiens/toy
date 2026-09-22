/* ============================================================================
 * components/memories.js — 记忆管理：memory-list / memory-row / kind-badge /
 * kind-filter / search-field / memory-toggle / clear-all-dialog
 * 纪律：用列表而非卡片网格；删除二次确认（行内）；删除失败不乐观移除
 *      （避免「看着删了其实没删」）；界面不展示任何原始录音。
 * ==========================================================================*/
import { Base, define, ic, esc, emit, str } from './base.js';

const KIND = {
  preference: { label: '偏好', icon: 'tag' },
  fact: { label: '事实', icon: 'info' },
  event: { label: '事件', icon: 'calendar-clock' },
};
export const KIND_LABELS = KIND;

export function fmtWhen(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return esc(iso);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

class KindBadge extends Base {
  static observedAttributes = ['kind', 'confirmed'];
  template() {
    const k = KIND[str(this, 'kind')] || { label: str(this, 'kind', '未知'), icon: 'tag' };
    const pending = str(this, 'confirmed', 'true') === 'false';
    return `<span class="rowflex" style="gap:var(--space-1)">
      <span class="badge">${ic(k.icon, 14)}${esc(k.label)}</span>
      ${pending ? `<span class="xs meta">待确认</span>` : ''}</span>`;
  }
}
define('kind-badge', KindBadge);

class MemoryRow extends Base {
  static observedAttributes = ['memory-id', 'kind', 'confirmed', 'created-at'];
  /** @param {object} v Memory schema */
  set memory(v) { this._mem = v; if (this.isConnected) this.render(); }
  get memory() { return this._mem; }
  _confirming = false;
  _expanded = false;
  template() {
    const m = this._mem || {};
    const value = m.value ?? '';
    const id = m.id ?? str(this, 'memory-id');
    const created = m.created_at || str(this, 'created-at');
    const confirmed = m.confirmed !== undefined ? m.confirmed : str(this, 'confirmed', 'true') !== 'false';
    const long = value.length > 40;
    return `<div class="row" role="listitem" style="grid-template-columns: minmax(0,1fr) 96px 132px 92px auto"
      data-row="${esc(id)}">
      <div class="row__main">
        <div class="row__value" title="${esc(value)}" style="${this._expanded ? 'white-space:normal' : ''}">
          ${esc(this._expanded || !long ? value : value.slice(0, 40) + '…')}</div>
        <div class="row__meta">来源会话 · ${esc(fmtWhen(created))}</div>
      </div>
      <kind-badge kind="${esc(m.kind || str(this, 'kind'))}" confirmed="${confirmed}"></kind-badge>
      <span class="small muted">${esc(confirmed ? '已确认' : '待确认')}</span>
      <span class="small muted">${esc(fmtWhen(created))}</span>
      <span class="row__actions">
        ${long ? `<button class="btn btn--ghost" type="button" data-act="expand"
          aria-expanded="${this._expanded}">${ic('panel-right', 16)}${this._expanded ? '收起' : '展开'}</button>` : ''}
        ${this._confirming ? `<inline-confirm label="删除这条记忆"></inline-confirm>`
          : `<button class="btn btn--danger" type="button" data-act="del">${ic('trash-2', 16)}删除</button>`}
      </span></div>`;
  }
  mounted() {
    this.on('click', '[data-act="del"]', () => { this._confirming = true; this.render(); this.$('[data-act="yes"]')?.focus(); });
    this.on('click', '[data-act="expand"]', () => { this._expanded = !this._expanded; this.render(); });
    this.addEventListener('confirm', () => emit(this, 'row-delete', { id: this._mem?.id ?? str(this, 'memory-id') }));
    this.addEventListener('cancel', () => { this._confirming = false; this.render(); this.$('[data-act="del"]')?.focus(); });
  }
}
define('memory-row', MemoryRow);

class MemoryList extends Base {
  static observedAttributes = ['busy'];
  /** @param {object[]} v Memory[] */
  set items(v) { this._items = Array.isArray(v) ? v : []; if (this.isConnected) this.render(); }
  get items() { return this._items || []; }
  template() {
    if (str(this, 'busy') === 'true') return `<loader-skeleton rows="4"></loader-skeleton>`;
    const items = this._items || [];
    const head = `<div class="row xs meta" role="presentation" aria-hidden="true"
      style="grid-template-columns: minmax(0,1fr) 96px 132px 92px auto">
      <span>内容摘要</span><span>类型</span><span>状态</span><span>来源时间</span><span class="right">操作</span></div>`;
    const rows = items.map((m) => `<memory-row memory-id="${esc(m.id)}" kind="${esc(m.kind)}"
      confirmed="${m.confirmed}" created-at="${esc(m.created_at)}"></memory-row>`).join('');
    return `<div role="list" aria-label="记忆列表">${head}<div class="rows">${rows}</div></div>`;
  }
  mounted() {
    const rows = this.$$('memory-row');
    (this._items || []).forEach((m, i) => { if (rows[i]) rows[i].memory = m; });
    this.addEventListener('row-delete', (e) => { e.stopPropagation(); emit(this, 'row-delete', e.detail); });
  }
}
define('memory-list', MemoryList);

class SearchField extends Base {
  static observedAttributes = ['placeholder'];
  template() {
    return `<label class="rowflex grow"><span class="sr-only">搜索记忆</span>
      <span class="ic-btn">${ic('search', 18)}</span>
      <input class="input" type="search" data-q placeholder="${esc(str(this, 'placeholder', '搜索记忆内容'))}"
        autocomplete="off" style="min-width:16ch"></label>`;
  }
  mounted() {
    let timer = null;
    this.on('input', '[data-q]', (e, t) => {
      clearTimeout(timer);
      timer = setTimeout(() => emit(this, 'search-input', { q: t.value.trim() }), 250);
    });
  }
}
define('search-field', SearchField);

class KindFilter extends Base {
  static observedAttributes = ['selected'];
  /** @param {string[]} v 可选项（不含 value=''） */
  set kinds(v) { this._kinds = Array.isArray(v) ? v : Object.keys(KIND); if (this.isConnected) this.render(); }
  template() {
    const sel = str(this, 'selected');
    const opts = ['', ...(this._kinds || Object.keys(KIND))].map((k) => {
      const label = k === '' ? '全部类型' : (KIND[k]?.label || k);
      return `<option value="${esc(k)}" ${k === sel ? 'selected' : ''}>${esc(label)}</option>`;
    }).join('');
    return `<label class="rowflex"><span class="sr-only">按类型筛选</span>
      <span class="ic-btn">${ic('filter', 18)}</span>
      <select class="select" data-kind style="min-height:36px;width:auto">${opts}</select></label>`;
  }
  mounted() {
    this.on('change', '[data-kind]', (e, t) => emit(this, 'filter-change', { kind: t.value }));
  }
}
define('kind-filter', KindFilter);

class MemoryToggle extends Base {
  static observedAttributes = ['enabled'];
  template() {
    const on = str(this, 'enabled', 'true') !== 'false';
    return `<span class="chip" title="记忆开关在「设置 · 儿童模式」中修改">
      ${ic('toggle-right', 18)}<span>记忆写入：${on ? '开' : '关'}</span></span>`;
  }
}
define('memory-toggle', MemoryToggle);

class ClearAllDialog extends Base {
  static observedAttributes = ['open'];
  template() {
    return `<confirm-dialog tone="danger" title="清空全部记忆" require-checkbox
      confirm-text="清空全部"
      message="清空后玩具不会在任何新会话中再提起这些内容（含短期历史与摘要缓存）。此操作不可恢复。"></confirm-dialog>`;
  }
  mounted() {
    this.addEventListener('confirm', () => emit(this, 'cleared', {}));
    this.addEventListener('cancel', () => emit(this, 'cancel', {}));
  }
  open() { this.$('confirm-dialog')?.open(); }
  close() { this.$('confirm-dialog')?.close(); }
}
define('clear-all-dialog', ClearAllDialog);
