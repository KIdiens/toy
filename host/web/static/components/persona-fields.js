/* ============================================================================
 * components/persona-fields.js — 人设表单的字段模板（纯字符串函数）
 * 单独一层的理由：字段的「标签 / 说明 / 计数位 / 错误位」是固定骨架，
 * 与编辑器的交互逻辑（防抖预览、保存、回填）无关；拆开后两边都能单独读懂。
 * 计数位用 `data-count="<字段>[.<序号>]"`，错误位用 `data-err="<字段>"`，
 * 组件只按这两个约定写 DOM，不做选择器推导。
 * ==========================================================================*/
import { ic, esc } from './base.js';
import { LIMITS } from './persona-preview.js';

/** [字段名, 标签, 说明, 上限, 是否多行] —— 顺序即成串顺序 */
export const TEXT_FIELDS = [
  ['archetype', '身份 / 原型', '它「是谁」，例如「好奇的小孩」「慢性子的老爷爷」。', LIMITS.archetype, false],
  ['tone', '语气', '情绪基调，例如「温和、耐心」「兴奋、爱追问」。', LIMITS.tone, false],
  ['speech_style', '说话方式', '句子长短与用词习惯，例如「多用短句，偶尔打个比方」。', LIMITS.speech_style, true],
];

export function textField([name, label, hint, limit, multiline]) {
  const control = multiline
    ? `<textarea class="textarea" id="pf-${name}" data-in="${name}" rows="2"
        aria-describedby="pf-${name}-hint"></textarea>`
    : `<input class="input" id="pf-${name}" data-in="${name}" type="text" autocomplete="off"
        spellcheck="false" aria-describedby="pf-${name}-hint">`;
  return `<div class="field" data-field="${name}">
    <div class="field__top">
      <label class="field__label" for="pf-${name}">${esc(label)}</label>
      <span class="counter" data-count="${name}" data-over="false"><span data-len>0</span>/${limit}</span>
    </div>
    ${control}
    <p class="field__helper" id="pf-${name}-hint">${esc(hint)}</p>
    <p class="field__error hidden" data-err="${name}" role="alert"></p>
  </div>`;
}

export function phraseField(name, label, hint) {
  return `<div class="field" data-field="${name}">
    <div class="field__top">
      <span class="field__label" id="pf-${name}-label">${esc(label)}</span>
      <span class="xs meta">最多 ${LIMITS.phrases} 条 · 每条 ${LIMITS.phrase} 字以内</span>
    </div>
    <div class="stack" data-phrases="${name}" role="group" aria-labelledby="pf-${name}-label"></div>
    <div><button class="btn btn--secondary" type="button" data-act="add-phrase" data-group="${name}">
      ${ic('plus', 16)}添加一条</button></div>
    <p class="field__helper">${esc(hint)}</p>
    <p class="field__error hidden" data-err="${name}" role="alert"></p>
  </div>`;
}
