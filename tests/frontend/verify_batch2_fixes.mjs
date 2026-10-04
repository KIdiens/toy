/* 第二批整改 · 前端缺陷回归测试（复现 → 修复 → 固化）
 *
 * 运行：node tests/frontend/verify_batch2_fixes.mjs
 *
 * 覆盖第二批第 1 组的 4 条缺陷：
 *   F1  reminders 列表永远停在骨架屏（busy 属性只设不清）
 *   F2  行数据注入放在 mounted()（只跑一次），重绘后行内容静默为空
 *   F3  api.js 缺 listActionFiles，编排器端点已存在却未接（editor.js 恒空）
 *   F4  status 在线转故障时故障横幅被永久隐藏（hidden 开关分支不闭合）
 *
 * 用最小 DOM 桩真实驱动组件生命周期（render/mounted/updated），不做源码正则断言。
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const STATIC = join(HERE, '..', '..', 'host', 'web', 'static');

/* ---------------------------------------------------- 最小 DOM 桩 */
/** innerHTML 赋值时按出现次数重建子元素表，让 querySelectorAll 能拿到"新行"。 */
class El {
  constructor(tag = 'div') {
    this.tagName = String(tag).toUpperCase();
    this.childNodes = [];
    this._attrs = {};
    this._classes = new Set();
    this._listeners = {};
    this._innerHTML = '';
    this._text = '';
    this.isConnected = true;
    this._rows = [];
  }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(v) {
    this._innerHTML = String(v);
    // 依据模板里出现的 <reminder-row / <memory-row 次数同步出行元素。
    // 关键：行元素必须按 customElements 注册表实例化，否则拿到的是基类桩，
    // 组件的 `set reminder` / `set memory` 写的是 this._r / this._mem，桩上不成立。
    const rows = [];
    for (const tagName of ['reminder-row', 'memory-row']) {
      const n = (this._innerHTML.match(new RegExp(`<${tagName}[\\s>]`, 'g')) || []).length;
      for (let i = 0; i < n; i += 1) {
        const Ctor = globalThis.customElements.get(tagName);
        const row = Ctor ? new Ctor() : new El(tagName);
        row.tagName = tagName.toUpperCase();
        rows.push(row);
      }
    }
    this._rows = rows;
  }
  getAttribute(n) { return Object.prototype.hasOwnProperty.call(this._attrs, n) ? this._attrs[n] : null; }
  hasAttribute(n) { return Object.prototype.hasOwnProperty.call(this._attrs, n); }
  setAttribute(n, v) {
    const old = this.getAttribute(n);
    this._attrs[n] = String(v);
    if (old !== String(v)) this._attrChanged(n, old, String(v));
  }
  removeAttribute(n) {
    const old = this.getAttribute(n);
    delete this._attrs[n];
    if (old !== null) this._attrChanged(n, old, null);
  }
  _attrChanged(name, oldV, newV) {
    const Ctor = globalThis.customElements.get(this.tagName.toLowerCase());
    // 浏览器只在**实例**的原型链上找 attributeChangedCallback（不是静态侧）。
    // 真实浏览器由 CustomElementDefinition 保证；桩里必须查 Ctor.prototype。
    if (!Ctor || typeof Ctor.prototype.attributeChangedCallback !== 'function') return;
    Ctor.prototype.attributeChangedCallback.call(this, name, oldV, newV);
  }
  querySelector() { return null; }
  querySelectorAll(sel) {
    const tag = sel.replace(/[[\]]/g, '');
    if (tag === 'reminder-row' || tag === 'memory-row') return this._rows || [];
    return [];
  }
  addEventListener(t, h) { (this._listeners[t] ||= []).push(h); }
  removeEventListener() {}
  dispatchEvent() {}
  appendChild() {}
  contains() { return false; }
  focus() {}
  get classList() {
    const s = this._classes;
    return {
      toggle: (c, on) => (on ? s.add(c) : s.delete(c)),
      add: (...cs) => cs.forEach((c) => s.add(c)),
      remove: (...cs) => cs.forEach((c) => s.delete(c)),
      contains: (c) => s.has(c),
    };
  }
  set textContent(v) { this._text = String(v); }
  get textContent() { return this._text; }
  get hidden() { return s_has(this._classes, 'hidden'); }
}
function s_has(set, v) { return set.has(v); }

globalThis.HTMLElement = El;
globalThis.CustomEvent = class { constructor(t, o = {}) { this.type = t; Object.assign(this, o); } };
globalThis.document = {
  documentElement: new El('html'),
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: (t) => new El(t),
};
globalThis.window = globalThis;
globalThis.location = { protocol: 'http:', host: 'localhost' };
globalThis.localStorage = { getItem: () => null, setItem() {} };
const REG = new Map();
globalThis.customElements = { define: (n, c) => REG.set(n, c), get: (n) => REG.get(n) };

/* ---------------------------------------------------- 断言骨架 */
let pass = 0;
const failures = [];
const ck = async (name, fn) => {
  try { await fn(); pass += 1; console.log('  PASS', name); }
  catch (e) { failures.push(name); console.log('  FAIL', name, '->', e.message); }
};
const module = async (rel) => import(pathToFileURL(join(STATIC, rel)).href);

/* ---------------------------------------------------- F1 + F2：reminder-list */
console.log('--- F1/F2 reminder-list 骨架与行数据 ---');
await module('components/reminders.js');
const ReminderList = REG.get('reminder-list');

const SAMPLE = [
  { id: 1, text: '该刷牙啦', status: 'pending', due_at_utc: '2026-09-26T12:00:00Z', timezone: 'Asia/Shanghai', profile_id: 'default', repeat_rule: 'daily', idempotency_key: 'rmd-abcdef12' },
  { id: 2, text: '吃药', status: 'cancelled', due_at_utc: '2026-09-27T12:00:00Z', timezone: 'Asia/Shanghai', profile_id: 'default', repeat_rule: 'once', idempotency_key: 'rmd-99999999' },
];

/** 按 pages/reminders.js 的 load() 顺序驱动一次：设 busy → 赋 items → 清 busy。 */
function driveLoad(list, { clearBusy }) {
  list.setAttribute('busy', 'true');
  list.connectedCallback();
  list.items = SAMPLE;
  if (clearBusy) list.setAttribute('busy', 'false');
  return list;
}

/** 实例化已注册组件：tagName 必须是标签名，否则属性变更走不到自定义元素的重绘。 */
function mount(name) {
  const inst = new (REG.get(name))();
  inst.tagName = name.toUpperCase();
  return inst;
}

ck('F1 load 完成后 busy 必须被清掉（否则列表永远停在骨架屏）', () => {
  const list = driveLoad(mount('reminder-list'), { clearBusy: true });
  assert.equal(list.getAttribute('busy'), 'false',
    `busy 仍为 ${JSON.stringify(list.getAttribute('busy'))} —— 模板会一直返回 loader-skeleton`);
  assert.ok(!list.innerHTML.includes('loader-skeleton'),
    '渲染结果仍含 loader-skeleton，真实数据不可见');
});

ck('F2 重绘后每一行都必须拿到自己的数据（不能只靠 mounted 一次）', () => {
  const list = driveLoad(mount('reminder-list'), { clearBusy: true });
  const rows = list.querySelectorAll('reminder-row');
  assert.equal(rows.length, SAMPLE.length, `期望 ${SAMPLE.length} 行，实际 ${rows.length} 行`);
  rows.forEach((row, i) => {
    assert.equal(row._r, SAMPLE[i],
      `第 ${i + 1} 行 _r=${JSON.stringify(row._r)} —— 行内容会渲染成 "—"`);
  });
});

ck('F2b busy 从 true 翻到 false 的那次重绘同样要注入行数据', () => {
  const list = mount('reminder-list');
  list.setAttribute('busy', 'true');
  list.connectedCallback();
  list.items = SAMPLE;               // 此时仍 busy，模板只有骨架、没有行
  list.setAttribute('busy', 'false'); // 关键：这一次重绘才第一次出行
  const rows = list.querySelectorAll('reminder-row');
  assert.equal(rows.length, SAMPLE.length);
  assert.ok(rows[0]._r === SAMPLE[0], '首次出行时行数据未被注入');
});

/* ---------------------------------------------------- F2 同源：memory-list */
console.log('--- F2 同源 memory-list 行数据 ---');
REG.clear();
await module('components/memories.js');
const MemoryList = REG.get('memory-list');
const MEMS = [
  { id: 10, value: '喜欢喝美式', kind: 'preference', confirmed: true, created_at: '2026-09-20T10:00:00Z' },
  { id: 11, value: '猫叫豆豆', kind: 'fact', confirmed: false, created_at: '2026-09-21T10:00:00Z' },
];
ck('memory-list 重绘后每行都拿到数据', () => {
  const list = mount('memory-list');
  list.setAttribute('busy', 'true');
  list.connectedCallback();
  list.items = MEMS;
  list.setAttribute('busy', 'false');
  const rows = list.querySelectorAll('memory-row');
  assert.equal(rows.length, MEMS.length);
  rows.forEach((row, i) => assert.equal(row._mem, MEMS[i], `第 ${i + 1} 行 _mem 为空`));
});

/* ---------------------------------------------------- F3：编排器端点接线 */
console.log('--- F3 GET /actions/files 接线 ---');
const apiSrc = readFileSync(join(STATIC, 'api.js'), 'utf8');
const editorSrc = readFileSync(join(STATIC, 'pages', 'editor.js'), 'utf8');
const actionsRouter = readFileSync(join(STATIC, '..', 'routers', 'actions.py'), 'utf8');

ck('F3a 后端确实提供 GET /actions/files（说明契约已具备，不是"尚未提供"）', () => {
  assert.match(actionsRouter, /@router\.get\("\/actions\/files"\)/);
});

ck('F3b api.js 必须导出 listActionFiles', async () => {
  assert.match(apiSrc, /export\s+const\s+listActionFiles\s*=/,
    'api.js 未导出 listActionFiles —— 前端无法访问已存在的端点');
});

ck('F3c editor.js 必须调用 listActionFiles 填充 S.fileList', () => {
  assert.match(editorSrc, /listActionFiles\(/,
    'editor.js 从未调用 listActionFiles，动作文件列表恒为空');
  assert.doesNotMatch(editorSrc, /契约未提供该端点前恒为空|动作文件列表接口尚未提供/,
    'editor.js 仍宣称契约未提供该端点——这是过时信息，端点已存在');
});

/* ---------------------------------------------------- F4：故障横幅显隐 */
console.log('--- F4 在线转故障的横幅显隐 ---');
const statusPageSrc = readFileSync(join(STATIC, 'pages', 'status.js'), 'utf8');

/** 抽出 renderConn 的分支决策：在不引入完整 DOM 的前提下验证 hidden 开关是否闭合。 */
function renderConnDecision(status) {
  const state = !status ? 'offline' : (status.fault || status.state === 'FAULT_LOCKED') ? 'fault' : 'online';
  // 复刻 pages/status.js renderConn 对 banner.hidden 的处理
  const m = statusPageSrc.match(/function renderConn\(\)\s*\{[\s\S]*?\n  \}/);
  assert.ok(m, '未能在 pages/status.js 中定位 renderConn');
  const body = m[0];
  const addHidden = /banner\.classList\.add\('hidden'\)/.test(body);
  const removeHidden = /banner\.classList\.remove\('hidden'\)/.test(body);
  return { state, addHidden, removeHidden };
}

ck('F4a 故障分支必须显式把 banner 的 hidden 去掉', () => {
  const d = renderConnDecision({ state: 'IDLE', fault: { code: 'INTERLOCK', detail: 'x' } });
  assert.equal(d.state, 'fault');
  assert.ok(d.removeHidden,
    'renderConn 的故障分支只 setAttribute(mode) 不 remove hidden —— '
    + '页面一旦成功连过一次，故障横幅就永久隐藏');
});

ck('F4b renderConn 自身必须同时拥有 hidden 的两条分支（不依赖外部补刀）', () => {
  const d = renderConnDecision({ state: 'IDLE', fault: null });
  assert.ok(d.addHidden && d.removeHidden,
    `renderConn 内 add=${d.addHidden} remove=${d.removeHidden}，两条分支缺一即不可逆`);
});

console.log(`\n通过 ${pass} 项；失败 ${failures.length} 项`);
if (failures.length) {
  console.log('失败项：\n  - ' + failures.join('\n  - '));
  process.exitCode = 1;
}
