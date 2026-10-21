/* ============================================================================
 * components/persona-preview.js — 人设字段限位表 + 预览区渲染（纯函数）
 * 为什么单独一个模块：① 限位表是**前端唯一**的真源（字段计数、保存前拦截都读它）；
 * ② 预览渲染是纯字符串函数，可以在 Node 回归脚本里直接断言「用户输入必须被转义」，
 *    不必实例化自定义元素。
 * `compiled` 是后端按结构化字段拼出来的纯文本，但文本里**可能**出现用户填的
 * 任意字符（预览端点对非法内容也会尽量给出可用串），所以插入 DOM 前一律 esc()。
 * ==========================================================================*/
import { ic, esc } from './base.js';

/** 与 host/roles/schema.py 的领域限位一致；max_chars 默认值同 DEFAULT_PERSONA_MAX_CHARS。 */
export const LIMITS = {
  archetype: 40,
  tone: 60,
  speech_style: 80,
  phrase: 20,
  phrases: 3,
  maxCharsMin: 20,
  maxCharsMax: 300,
  maxCharsDefault: 100,
};

export const FIELD_LABELS = {
  archetype: '身份 / 原型',
  tone: '语气',
  speech_style: '说话方式',
  catchphrases: '口头禅',
  avoid: '忌讳',
  max_chars: '单次回复字数',
};

/** 按**码位**计数（与 Python 的 len() 同口径），不是 UTF-16 码元。 */
export const count = (value) => Array.from(String(value ?? '').trim()).length;

/** pydantic 的 ValueError 文案带 "Value error, " 前缀，直接展示给家长很怪，去掉。 */
export const cleanMessage = (message) => String(message ?? '').replace(/^Value error,\s*/, '');

/** 后端 error.path 形如 `tone` / `catchphrases.0`，取字段段翻成人话。 */
export function fieldLabel(path) {
  const head = String(path || '').split('.')[0];
  return FIELD_LABELS[head] || head || '整体';
}

/**
 * 预览区内容（不含外层容器，便于局部刷新时不丢焦点）。
 * @param {{status?:'idle'|'loading'|'ok'|'invalid'|'error', compiled?:string|null,
 *          errors?:{path:string,message:string}[], message?:string}} state
 */
export function previewMarkup(state = {}) {
  const { status = 'idle', compiled = null, errors = [], message = '' } = state;
  if (status === 'loading') {
    return `<p class="small muted rowflex">${ic('loader-circle', 16)}正在编译预览…</p>`;
  }
  if (status === 'error') {
    return `<p class="field__error" role="alert">${ic('circle-x', 16)}
      <span>${esc(message || '预览请求失败')}</span></p>`;
  }
  if (status === 'invalid') {
    const rows = (errors || []).map((e) =>
      `<li><strong>${esc(fieldLabel(e.path))}</strong>：${esc(cleanMessage(e.message) || '不合法')}</li>`).join('');
    return `<p class="field__error">${ic('triangle-alert', 16)}
      <span>后端判定当前内容不合法，这段不会被保存。</span></p>
      <ul class="small stack" data-preview-errors>${rows || '<li>内容不合法</li>'}</ul>`;
  }
  if (status === 'ok') {
    return `<p class="preview__text" data-compiled>${esc(compiled || '')}</p>`;
  }
  return '<p class="small muted">改上面的字段，这里会显示实际发给模型的角色设定。</p>';
}
