/* ============================================================================
 * components/persona-dirty.js — 人设的规范化快照与脏判定（纯函数 + 一个极小的状态类）
 * 为什么单独一层：
 *  1) 判定必须**规范化**再比，否则「点进输入框再点走」也会被当成改动：
 *     字符串去首尾空白、口头禅/忌讳的空行忽略、max_chars 转成数字再比。
 *  2) 这是页面里唯一「丢了就找不回来」的判断（切换角色会重填表单），因此把它做成
 *     可被 node 直接单测的纯逻辑，而不是埋在 DOM 事件回调里。
 * ==========================================================================*/
import { LIMITS } from './persona-preview.js';

const TEXT_KEYS = ['archetype', 'tone', 'speech_style'];
const LIST_KEYS = ['catchphrases', 'avoid'];
const SEP = '\u0000'; // 条目分隔符：避免 ['ab','c'] 与 ['a','bc'] 拼成同一个串

const text = (v) => String(v ?? '').trim();

/** 只保留「用户能感知的内容」：空行、首尾空白、字符串形式的数字都不算差异。 */
export function normalizePersona(persona) {
  const p = persona || {};
  const out = {};
  TEXT_KEYS.forEach((k) => { out[k] = text(p[k]); });
  LIST_KEYS.forEach((k) => {
    const list = Array.isArray(p[k]) ? p[k] : [];
    out[k] = list.map(text).filter(Boolean);
  });
  const mc = Number(p.max_chars);
  out.max_chars = Number.isFinite(mc) && mc > 0 ? Math.trunc(mc) : LIMITS.maxCharsDefault;
  return out;
}

export function samePersona(a, b) {
  const x = normalizePersona(a);
  const y = normalizePersona(b);
  return TEXT_KEYS.every((k) => x[k] === y[k])
    && LIST_KEYS.every((k) => x[k].join(SEP) === y[k].join(SEP))
    && x.max_chars === y.max_chars;
}

/** 快照 = 最后一次「与磁盘一致」的人设（加载时回填的值，或保存成功后落盘的值）。 */
export class DirtyTracker {
  constructor() { this.baseline = null; }
  mark(persona) { this.baseline = normalizePersona(persona); }
  isDirty(current) { return this.baseline !== null && !samePersona(this.baseline, current); }
}

/** 只有「换到另一个角色」且表单已脏时才需要二次确认；点自己那张卡不算切换。 */
export function needsSwitchConfirm(currentId, nextId, dirty) {
  if (currentId === null || currentId === undefined) return false;
  return String(currentId) !== String(nextId) && !!dirty;
}
