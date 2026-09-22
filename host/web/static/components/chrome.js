/* ============================================================================
 * components/chrome.js — 5 页共用的界面骨架：顶栏 / 页头 / toast / 空态 /
 * 加载骨架 / 行内二次确认 / 弹窗二次确认 / 隐私说明 / 主题切换
 * 全部为光 DOM 自定义元素；图标取自 Lucide 1.47.0（vendor/lucide-icons.js）。
 * ==========================================================================*/
import { Base, define, ic, esc, emit, str, bool, NAV } from './base.js';

const THEME_KEY = 'toy-theme';

/* ---------------------------------------------------------------- 主题切换 */
class ThemeToggle extends Base {
  static slot = true;
  template() {
    const dark = document.documentElement.getAttribute('data-theme') === 'dark';
    return `<button class="btn btn--ghost" type="button" data-act="toggle"
      aria-pressed="${dark}" title="${dark ? '切换到浅色' : '切换到深色'}">
      ${ic(dark ? 'sun' : 'moon', 20)}<span class="small">${dark ? '浅色' : '深色'}</span></button>`;
  }
  mounted() {
    this.on('click', '[data-act="toggle"]', () => {
      const dark = document.documentElement.getAttribute('data-theme') === 'dark';
      const next = dark ? 'light' : 'dark';
      if (next === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
      else document.documentElement.removeAttribute('data-theme');
      try { localStorage.setItem(THEME_KEY, next); } catch { /* 隐私模式忽略 */ }
      emit(this, 'theme-change', { theme: next });
      this.render();
    });
  }
}
define('theme-toggle', ThemeToggle);

/** 页面初始化时恢复主题偏好（编排器默认深色由页面属性决定）。 */
export function initTheme(fallback = 'light') {
  let saved = null;
  try { saved = localStorage.getItem(THEME_KEY); } catch { saved = null; }
  const theme = saved || fallback;
  if (theme === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
  else document.documentElement.removeAttribute('data-theme');
}

/* ---------------------------------------------------------------- 顶栏 */
class AppTopbar extends Base {
  static slot = true;
  static observedAttributes = ['active'];
  template() {
    const active = str(this, 'active');
    const links = NAV.map((n) => `<a class="nav__link" href="${n.href}"
      ${n.route === active ? 'aria-current="page"' : ''}>${ic(n.icon, 18)}<span>${esc(n.label)}</span></a>`).join('');
    return `<a class="topbar__brand" href="/">${ic('gauge', 22)}<span>玩具主机</span></a>
      <nav class="nav" aria-label="主导航">${links}</nav>
      <span class="topbar__spacer"></span>
      <div class="topbar__group"><span data-slot></span><theme-toggle></theme-toggle></div>`;
  }
}
define('app-topbar', AppTopbar);

/* ---------------------------------------------------------------- 页头 */
class PageHeader extends Base {
  static slot = true;
  static observedAttributes = ['title', 'subtitle', 'icon'];
  template() {
    const sub = str(this, 'subtitle');
    return `<div class="page-head">
      <div class="page-head__ic">${ic(str(this, 'icon', 'circle-help'), 24)}</div>
      <div class="grow">
        <h1 class="page-head__title">${esc(str(this, 'title', '页面'))}</h1>
        ${sub ? `<p class="page-head__sub">${esc(sub)}</p>` : ''}
      </div>
      <div class="page-head__actions" data-slot></div>
    </div>`;
  }
}
define('page-header', PageHeader);

/* ---------------------------------------------------------------- 隐私/说明 */
class PrivacyNote extends Base {
  static slot = true;
  static observedAttributes = ['tone'];
  template() {
    const tone = str(this, 'tone', 'info');
    const cls = tone === 'warn' ? 'banner banner--fault' : 'banner';
    return `<div class="${cls}" role="note">${ic(tone === 'warn' ? 'triangle-alert' : 'info', 18)}
      <span class="banner__text" data-slot></span></div>`;
  }
}
define('privacy-note', PrivacyNote);

/* ---------------------------------------------------------------- toast */
class ToastStack extends Base {
  static slot = true;
  constructor() { super(); this.items = []; }
  template() {
    const rows = this.items.map((t) => `<div class="toast toast--${t.tone}" role="status">
      ${ic(t.tone === 'success' ? 'circle-check' : t.tone === 'error' ? 'circle-x' : 'info', 18)}
      <span class="grow">${esc(t.message)}</span>
      <button class="btn btn--ghost toast__close" type="button" data-dismiss="${t.id}"
        aria-label="关闭提示">${ic('x', 16)}</button></div>`).join('');
    return `<div class="toasts" aria-live="polite" aria-atomic="false">${rows}</div>`;
  }
  mounted() {
    this.on('click', '[data-dismiss]', (e, t) => {
      const id = Number(t.getAttribute('data-dismiss'));
      this.items = this.items.filter((x) => x.id !== id);
      this.render();
    });
  }
  push({ tone = 'info', message, timeout = 5000 }) {
    const id = (this._seq = (this._seq || 0) + 1);
    this.items = [...this.items, { id, tone, message }];
    this.render();
    if (timeout) setTimeout(() => { this.items = this.items.filter((x) => x.id !== id); this.render(); }, timeout);
    return id;
  }
}
define('toast-stack', ToastStack);

/** 全局 toast 出口：页面里放一个 <toast-stack id="toasts">，这里统一寻址。 */
export function toast(tone, message, timeout = 5000) {
  let stack = document.querySelector('toast-stack');
  if (!stack) { stack = document.createElement('toast-stack'); document.body.appendChild(stack); }
  return stack.push({ tone, message, timeout });
}

/* ---------------------------------------------------------------- 加载 / 空态 */
class LoaderSkeleton extends Base {
  static observedAttributes = ['rows', 'variant'];
  template() {
    const rows = Number(str(this, 'rows', '3')) || 3;
    const cls = str(this, 'variant', 'line');
    const lines = Array.from({ length: rows }, (_, i) =>
      `<div class="sk-line ${i % 2 ? 'sk-line--short' : ''}" aria-hidden="true"></div>`).join('');
    return `<div class="skeleton" role="status" aria-busy="true"
      aria-label="${esc(cls === 'track' ? '正在载入动作轨道' : '正在载入')}">${lines}</div>`;
  }
}
define('loader-skeleton', LoaderSkeleton);

class EmptyState extends Base {
  static slot = true;
  static observedAttributes = ['title', 'hint', 'icon'];
  template() {
    const hint = str(this, 'hint');
    return `<div class="empty">
      <div class="empty__ic">${ic(str(this, 'icon', 'inbox'), 34)}</div>
      <p class="empty__title">${esc(str(this, 'title', '暂无内容'))}</p>
      ${hint ? `<p class="small">${esc(hint)}</p>` : ''}
      <div class="empty__actions" data-slot></div>
    </div>`;
  }
}
define('empty-state', EmptyState);

/* ---------------------------------------------------------------- 行内二次确认 */
class InlineConfirm extends Base {
  static slot = true;
  static observedAttributes = ['label', 'confirm-text'];
  template() {
    const label = str(this, 'label', '删除');
    return `<span class="rowflex">
      <span class="small muted">${esc(label)}？</span>
      <button class="btn btn--danger" type="button" data-act="yes">${ic('check', 16)}确认</button>
      <button class="btn btn--ghost" type="button" data-act="no">${ic('x', 16)}取消</button></span>`;
  }
  mounted() {
    this.on('click', '[data-act="yes"]', () => emit(this, 'confirm', {}));
    this.on('click', '[data-act="no"]', () => emit(this, 'cancel', {}));
  }
}
define('inline-confirm', InlineConfirm);

/* ---------------------------------------------------------------- 弹窗二次确认 */
class ConfirmDialog extends Base {
  static slot = true;
  static observedAttributes = ['tone', 'title', 'message', 'require-typing', 'require-checkbox', 'confirm-text'];
  template() {
    const tone = str(this, 'tone', 'danger');
    const typing = str(this, 'require-typing');
    const needBox = bool(this, 'require-checkbox');
    return `<dialog class="dlg" data-dlg aria-labelledby="dlg-t">
      <h2 class="dlg__title" id="dlg-t">${ic(tone === 'danger' ? 'triangle-alert' : 'circle-help', 22)}
        ${esc(str(this, 'title', '确认操作'))}</h2>
      <p class="dlg__body">${esc(str(this, 'message'))}</p>
      ${needBox ? `<label class="switch small"><input type="checkbox" data-ack>
        我已了解此操作不可恢复</label>` : ''}
      ${typing ? `<div class="field"><label class="field__label" for="dlg-type">输入 ${esc(typing)} 以确认</label>
        <input class="input" id="dlg-type" data-type autocomplete="off" spellcheck="false"
         placeholder="${esc(typing)}"></div>` : ''}
      <div class="dlg__actions">
        <button class="btn btn--ghost" type="button" data-act="cancel">取消</button>
        <button class="btn ${tone === 'danger' ? 'btn--danger' : 'btn--primary'}" type="button" data-act="ok"
          disabled>${esc(str(this, 'confirm-text', '确认'))}</button>
      </div></dialog>`;
  }
  mounted() {
    this.on('input', '[data-type]', () => this.refresh());
    this.on('change', '[data-ack]', () => this.refresh());
    this.on('click', '[data-act="ok"]', () => { emit(this, 'confirm', {}); this.close(); });
    this.on('click', '[data-act="cancel"]', () => { emit(this, 'cancel', {}); this.close(); });
    // <dialog> 的 Esc 会派发可冒泡的 cancel；拦截后走同一收敛路径
    this.on('cancel', '[data-dlg]', (e) => { e.preventDefault(); emit(this, 'cancel', {}); this.close(); });
  }
  updated() { this.refresh(); }
  refresh() {
    const ok = this.$('[data-act="ok"]');
    if (!ok) return;
    const needBox = bool(this, 'require-checkbox');
    const typing = str(this, 'require-typing');
    const boxOk = !needBox || !!this.$('[data-ack]')?.checked;
    const typeOk = !typing || this.$('[data-type]')?.value === typing;
    ok.disabled = !(boxOk && typeOk);
  }
  open() { const d = this.$('[data-dlg]'); if (d && !d.open) d.showModal(); }
  close() { const d = this.$('[data-dlg]'); if (d && d.open) d.close(); }
}
define('confirm-dialog', ConfirmDialog);
