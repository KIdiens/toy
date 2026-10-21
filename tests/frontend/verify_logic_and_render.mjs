/* Dev-time verification harness (not shipped to the browser).
 * 运行： node _verify_frontend.mjs
 * 目的：① 校验 timeline.js 纯逻辑与 Spec AC-09/AC-10/AC-11 断言一致；
 *      ② 用最小 DOM 桩实例化每个自定义元素并渲染，捕获模板期运行时错误。
 */
import assert from 'node:assert/strict';

/* ---------------------------------------------------- 最小 DOM 桩 */
class El {
  constructor() { this.childNodes = []; this._attrs = {}; this.innerHTML = ''; this._kids = []; }
  getAttribute(n) { return Object.prototype.hasOwnProperty.call(this._attrs, n) ? this._attrs[n] : null; }
  hasAttribute(n) { return Object.prototype.hasOwnProperty.call(this._attrs, n); }
  setAttribute(n, v) { this._attrs[n] = String(v); }
  removeAttribute(n) { delete this._attrs[n]; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  addEventListener() {}
  removeEventListener() {}
  dispatchEvent() {}
  appendChild() {}
  contains() { return false; }
  focus() {}
  get classList() { return { toggle() {}, add() {}, remove() {} }; }
  set textContent(v) { this._text = v; }
  get textContent() { return this._text || ''; }
}
globalThis.HTMLElement = El;
globalThis.CustomEvent = class { constructor(t, o = {}) { this.type = t; Object.assign(this, o); } };
globalThis.document = {
  documentElement: { getAttribute: () => null, setAttribute() {}, removeAttribute() {} },
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: () => new El(),
};
globalThis.window = globalThis;
globalThis.localStorage = { getItem: () => null, setItem() {} };
const REG = new Map();
globalThis.customElements = { define: (n, c) => REG.set(n, c), get: (n) => REG.get(n) };

/* ---------------------------------------------------- ① timeline 纯逻辑 */
const T = await import('../../host/web/static/timeline.js');
let pass = 0;
const ck = (name, fn) => { fn(); pass += 1; console.log('  PASS', name); };

console.log('--- timeline.js 逻辑断言 ---');
ck('smoothstep 端点与中点', () => {
  assert.equal(T.smoothstep(0), 0);
  assert.equal(T.smoothstep(1), 1);
  assert.equal(T.smoothstep(0.5), 0.5);
});
ck('sampleAxis：t<=0 取首帧、越界取末帧', () => {
  const fs = [{ t_ms: 0, us: [1500, 1500, 1500, 1500] }, { t_ms: 1000, us: [1600, 1400, 1500, 1500] }, { t_ms: 2000, us: [1500, 1500, 1500, 1500] }];
  assert.equal(T.sampleAxis(fs, 0, -5), 1500);
  assert.equal(T.sampleAxis(fs, 0, 99999), 1500);
  assert.equal(T.sampleAxis(fs, 0, 500), 1550); // smoothstep(0.5)=0.5 -> 1500+50
});
ck('makeEmptyAction 合法', () => {
  assert.equal(T.validateAction(T.makeEmptyAction(1)).valid, true);
});
ck('AC-09 脉宽 1399/1601/NaN/Inf/布尔/字符串 全部拒绝', () => {
  const bad = [1399, 1601, NaN, Infinity, -Infinity, true, false, '1500', null];
  bad.forEach((v) => {
    const a = T.makeEmptyAction(1);
    a.frames[1].us[0] = v;
    const r = T.validateAction(a);
    assert.equal(r.valid, false, `值 ${String(v)} 应被拒绝`);
  });
});
ck('AC-10 时间非严格递增被拒', () => {
  const a = T.makeEmptyAction(1);
  a.frames[1].t_ms = 0;
  assert.equal(T.validateAction(a).valid, false);
});
ck('AC-10 峰值速度 >300µs/s 被拒', () => {
  const a = { schema: 1, role_id: 1, frames: [
    { t_ms: 0, us: [1500, 1500, 1500, 1500] },
    { t_ms: 10, us: [1600, 1500, 1500, 1500] }, // 1.5*100/0.01 = 15000 µs/s
    { t_ms: 20, us: [1500, 1500, 1500, 1500] },
  ] };
  const r = T.validateAction(a);
  assert.equal(r.valid, false);
  assert.ok(r.errors.some((e) => /峰值速度/.test(e.message)));
});
ck('AC-10 峰值速度上限边界（=300 pass / >300 reject）', () => {
  // 限位内（≤1600）才能单独考察速度：1.5·Δus/Δt(s)
  const mk = (dt) => ({ schema: 1, role_id: 1, frames: [
    { t_ms: 0, us: [1500, 1500, 1500, 1500] }, { t_ms: dt, us: [1600, 1500, 1500, 1500] },
    { t_ms: 1000, us: [1500, 1500, 1500, 1500] }] });
  assert.equal(T.validateAction(mk(500)).valid, true);   // 1.5*100/0.5 = 300 <= 300
  assert.equal(T.validateAction(mk(490)).valid, false);  // 1.5*100/0.49 = 306.1 > 300
  assert.ok(Math.abs(T.peakSpeed(mk(500).frames, 0) - 300) < 1e-9);
});
ck('AC-11 离线预演：四轴全程 1400–1600 且首末帧 1500', () => {
  const a = T.makeEmptyAction(1);
  assert.equal(a.frames[0].us.every((v) => v === 1500), true);
  assert.equal(a.frames[a.frames.length - 1].us.every((v) => v === 1500), true);
  for (let t = 0; t <= 2400; t += 5) {
    const v = T.sampleAll(a.frames, t);
    v.forEach((x, i) => assert.ok(x >= 1400 && x <= 1600, `t=${t} 轴${i + 1} = ${x} 越界`));
  }
});
ck('axisState 三态判定', () => {
  assert.equal(T.axisState(null), 'nodata');
  assert.equal(T.axisState(1500), 'normal');
  assert.equal(T.axisState(1405), 'near');
  assert.equal(T.axisState(1620), 'at');
});
ck('waveform 确定性 + formatTime', () => {
  assert.deepEqual(T.waveform(7, 8), T.waveform(7, 8));
  assert.equal(T.formatTime(62040), '01:02.040');
});

/* ---------------------------------------------------- ①b 人设预览渲染 */
const P = await import('../../host/web/static/components/persona-preview.js');

console.log('--- persona 预览渲染断言 ---');
ck('compiled 里的用户内容一律 esc（不注入 DOM）', () => {
  const html = P.previewMarkup({ status: 'ok', compiled: '你是小熊，<img src=x onerror=alert(1)>' });
  assert.ok(!html.includes('<img'), '预览不得让 <img> 变成真实节点');
  assert.ok(html.includes('&lt;img'), '应保留可见的转义文本');
});
ck('invalid 时逐字段列出错误且转义', () => {
  const html = P.previewMarkup({
    status: 'invalid',
    errors: [{ path: 'catchphrases.0', message: '不允许包含尖括号 <' }],
  });
  assert.ok(html.includes('口头禅'), '错误要标明是哪个字段（人话标签）');
  assert.ok(html.includes('data-preview-errors'));
  assert.ok(!html.includes('尖括号 <'), '错误文案同样要转义');
});
ck('idle / loading / error 三态都有可读文案', () => {
  assert.ok(P.previewMarkup({ status: 'idle' }).includes('改上面的字段'));
  assert.ok(P.previewMarkup({ status: 'loading' }).includes('正在编译预览'));
  assert.ok(P.previewMarkup({ status: 'error', message: '本机服务未连通' }).includes('本机服务未连通'));
});
ck('字段限位与后端一致（含 max_chars 默认值）', () => {
  assert.deepEqual(
    [P.LIMITS.archetype, P.LIMITS.tone, P.LIMITS.speech_style, P.LIMITS.phrase,
      P.LIMITS.phrases, P.LIMITS.maxCharsMin, P.LIMITS.maxCharsMax, P.LIMITS.maxCharsDefault],
    [40, 60, 80, 20, 3, 20, 300, 100],
  );
});
ck('计数按码位、且忽略首尾空白', () => {
  assert.equal(P.count('  abc  '), 3);
  assert.equal(P.count(''), 0);
  assert.equal(P.fieldLabel('max_chars'), '单次回复字数');
});

/* ---------------------------------------------------- ② 组件渲染冒烟 */
console.log('--- 组件渲染冒烟（最小 DOM 桩） ---');
const mods = [
  'chrome.js', 'status.js', 'axis.js', 'timeline-ui.js', 'editor-chrome.js',
  'editor-inspector.js', 'memories.js', 'reminders.js', 'settings-sections.js',
  'settings-content.js', 'settings-danger.js', 'role-list.js', 'persona-editor.js',
];
const ATTRS = {
  state: 'fault', mode: 'offline', provider: 'stub', tone: 'danger', title: 't', subtitle: 's',
  label: 'l', hint: 'h', axis: '1', 'min-us': '1400', 'max-us': '1600', 'neutral-us': '1500',
  kind: 'preference', confirmed: 'true', 'created-at': '2026-09-21T10:00:00Z', status: 'missed',
  'role-id': '1', enabled: 'true', rows: '2', icon: 'gauge', field: 'memory_enabled',
  checked: 'true', disabled: 'false', 'cooldown-seconds': '1', 'duration-ms': '6000',
  'playhead-ms': '0', 'ms': '0', 'total-ms': '6000', 'offset-ms': '0', health: 'healthy',
  'docked': 'false', 'estop-healthy': 'false', 'role-match': 'false', 'code': 'TIMEOUT',
  detail: 'd', 'network-online': 'false', 'conn-state': 'offline', 'data-available': 'false',
  source: 'target', unit: 'µs', expect: 'RESET', selected: 'daily', value: 'mid',
  'config-version': '1.0.0', 'display-name': '小熊', open: 'false', busy: 'false',
  playing: 'false', looping: 'false', 'preview-only': 'true', 'disabled-reason': '未连接设备',
  'memory-enabled': 'true', 'night-quiet': 'true', 'record-indicator': 'true', src: '',
  muted: 'false', express: 's(u)=3u^2-2u^3', 'readonly-curve': 'true', 'max-speed': '300',
};
const emptyOk = new Set(['stub-badge', 'fault-panel']); // 条件渲染为空属正常
let rendered = 0;
for (const m of mods) {
  REG.clear();
  await import(`../../host/web/static/components/${m}`);
  for (const [name, cls] of REG) {
    const inst = new cls();
    Object.entries(ATTRS).forEach(([k, v]) => inst.setAttribute(k, v));
    try {
      if (name === 'track-lane') inst.model = { frames: T.makeEmptyAction(1).frames, durationMs: 2400, selected: 1 };
      if (name === 'audio-track-lane') inst.waveform = T.waveform(3, 24);
      inst.connectedCallback();
      const out = inst.innerHTML;
      assert.equal(typeof out, 'string');
      if (!emptyOk.has(name) && out.length === 0) throw new Error('渲染结果为空');
      rendered += 1;
      console.log(`  PASS <${name}>  ${out.length} 字符`);
    } catch (e) {
      console.log(`  FAIL <${name}>  ${e.message}`);
      process.exitCode = 1;
    }
  }
}

console.log(`\n组件渲染通过：${rendered}；逻辑断言通过：${pass}`);
if (process.exitCode) console.log('存在失败项');
