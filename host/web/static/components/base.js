/* ============================================================================
 * host/web/static/components/base.js — 自定义元素基座（光 DOM）
 * 选型说明：组件用**光 DOM**（不 attachShadow）。理由：全局 styles.css 直接生效、
 * 无需在影子树重复注入样式，<dialog>/焦点管理保持原生语义，失效面更小；
 * 图标经 vendor/lucide-icons.js 内联为真实 Lucide 1.47.0 SVG（全项目唯一图标源）。
 * ==========================================================================*/
import { iconMarkup, hasIcon } from '../vendor/lucide-icons.js';

export { iconMarkup, hasIcon };

/** 语义图标（无 emoji、无字符画）。名称不存在时返回空串而非乱码符号。 */
export const ic = (name, size = 20) => iconMarkup(name, size);

export function esc(v) {
  if (v === null || v === undefined) return '';
  return String(v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/** 回抛用户操作；状态经 attribute/property 传入，事件经 CustomEvent 回抛。 */
export function emit(el, type, detail) {
  el.dispatchEvent(new CustomEvent(type, { detail, bubbles: true, composed: true }));
}

export function num(el, name, fallback = null) {
  const raw = el.getAttribute(name);
  if (raw === null || raw === '') return fallback;
  const v = Number(raw);
  return Number.isFinite(v) ? v : fallback;
}
export const bool = (el, name) => el.hasAttribute(name) && el.getAttribute(name) !== 'false';
export const str = (el, name, fallback = '') => (el.getAttribute(name) ?? fallback);

export class Base extends HTMLElement {
  /** 子类声明需要监听、且变更即整体重绘的属性 */
  static observedAttributes = [];
  static get observed() { return this.observedAttributes; }

  connectedCallback() {
    // 光 DOM 需要「投射」子节点：首次挂载前缓存，重绘后再放入 [data-slot]。
    // 注意：自定义元素**升级时** attributeChangedCallback 先于 connectedCallback 触发，
    // 首次 render 就会清空 innerHTML；因此缓存必须在 render() 内、清空之前完成。
    this.render();
  }
  disconnectedCallback() { this.unmounted?.(); }
  attributeChangedCallback(name, oldV, newV) {
    if (oldV === newV || !this.isConnected) return;
    if (this.onAttr) this.onAttr(name, oldV, newV);
    if (this.rerenderOnAttr !== false) this.render();
  }
  /** 局部刷新（不重建 DOM，保输入焦点）；默认等价于整体重绘 */
  update() { this.render(); }
  /** 首次渲染前缓存原始子节点（仅 slot 组件）；之后不再重取，避免抓到模板自身节点。 */
  _ensureKids() {
    if (this.constructor.slot && this._kids === undefined) this._kids = Array.from(this.childNodes);
  }
  render() {
    this._ensureKids();
    const first = !this._mounted;
    this.innerHTML = this.template();
    if (this.constructor.slot && this._kids) {
      const plain = this.querySelector('[data-slot=""]');
      this._kids.forEach((n) => {
        const name = n.nodeType === 1 && n.getAttribute ? n.getAttribute('slot') : null;
        const box = (name && this.querySelector(`[data-slot="${name}"]`)) || plain;
        if (box) box.appendChild(n);
      });
    }
    // mounted 只跑一次（事件一律用 this.on 委托，天然抵消重绘）；
    // updated 在每次重绘后跑（聚焦、回填 <select> 选项、同步 dialog 开合等副作用）。
    if (first) { this._mounted = true; this.mounted?.(); }
    this.updated?.();
  }
  template() { return ''; }
  /** 事件委托：一次绑定，避免重绘后监听泄漏 */
  on(type, selector, handler) { this.addEventListener(type, (e) => {
    const t = e.target.closest(selector);
    if (t && this.contains(t)) handler(e, t);
  }); }
  $(sel) { return this.querySelector(sel); }
  $$(sel) { return Array.from(this.querySelectorAll(sel)); }
}

export function define(name, cls) {
  if (customElements.get(name)) return customElements.get(name);
  customElements.define(name, cls);
  return cls;
}

/** 顶栏品牌名与导航结构（5 页共用，避免每页重复声明）。 */
export const NAV = [
  { href: '/', label: '设备状态', icon: 'gauge', route: 'status' },
  { href: '/editor', label: '动作编排', icon: 'move-horizontal', route: 'editor' },
  { href: '/memories', label: '记忆', icon: 'brain', route: 'memories' },
  { href: '/reminders', label: '提醒', icon: 'bell', route: 'reminders' },
  { href: '/settings', label: '设置', icon: 'settings', route: 'settings' },
];
