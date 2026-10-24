/* 实际页面控制器：文件数据、快照下发、草稿保护、读请求与校验响应竞态。 */
import assert from 'node:assert/strict';
import fs from 'node:fs';

class Element {
  constructor() { this.attrs = new Map(); this.events = new Map(); this.classList = { toggle() {} }; }
  setAttribute(k, v) { this.attrs.set(k, String(v)); }
  getAttribute(k) { return this.attrs.get(k) ?? null; }
  hasAttribute(k) { return this.attrs.has(k); }
  removeAttribute(k) { this.attrs.delete(k); }
  addEventListener(k, fn) { if (!this.events.has(k)) this.events.set(k, []); this.events.get(k).push(fn); }
  async fire(k, detail) { await Promise.all((this.events.get(k) || []).map((fn) => fn({ detail }))); }
  querySelector() { return null; }
  focus() {}
}
globalThis.HTMLElement = Element;
const components = new Map();
globalThis.customElements = { define(name, value) { components.set(name, value); }, get(name) { return components.get(name); } };
globalThis.location = { protocol: 'http:', host: 'localhost' };
const windowEvents = new Map(); let closedSockets = 0;
globalThis.window = { confirm: () => true, addEventListener(name, fn) {
  if (!windowEvents.has(name)) windowEvents.set(name, []);
  windowEvents.get(name).push(fn);
} };
globalThis.WebSocket = class { close() { closedSockets += 1; } };
const nodes = new Map(), notifications = [];
globalThis.document = {
  querySelector(id) {
    if (id === 'toast-stack') return { push: (item) => notifications.push(item) };
    if (!nodes.has(id)) nodes.set(id, new Element());
    return nodes.get(id);
  },
};
const clone = (value) => structuredClone(value);
const docs = new Map(['greet', 'wave'].map((id) => [id,
  JSON.parse(fs.readFileSync(new URL(`../../content/roles/1/actions/${id}.json`, import.meta.url)))]));
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const pending = new Map(), played = [];
globalThis.fetch = async (url, options) => {
  let data;
  if (url.endsWith('/roles')) data = [1, 2].map((role_id) => ({ role_id, name: `角色${role_id}`, config_version: '1.1.0' }));
  else if (url.endsWith('/status')) data = { role_id: 1, state: 'IDLE' };
  else if (url.endsWith('/actions/files')) data = [...docs.keys()].map((name) => ({ name, active: name === 'greet' }));
  else if (url.includes('/actions/files/')) {
    const name = url.split('/').at(-1).split('?')[0];
    if (pending.has(name)) await pending.get(name).promise;
    data = { action_id: name, config_version: '1.1.0', action: clone(docs.get(name)) };
  } else if (url.endsWith('/roles/active')) {
    if (pending.has('role')) await pending.get('role').promise;
    data = { role_id: JSON.parse(options.body).role_id };
  } else if (url.endsWith('/actions/play')) { played.push(JSON.parse(options.body)); data = { scheduled: true }; }
  else data = { valid: true, errors: [] };
  return { ok: true, status: 200, text: async () => JSON.stringify({ code: 0, data }) };
};
const node = (id) => nodes.get(id);
const frames = () => node('#lane-1').model.frames;
const { default: boot } = await import('../../host/web/static/pages/editor.js');
await boot();
assert.deepEqual(frames(), docs.get('greet').frames, '初始高亮对应的实际文件没有加载');
await node('#files').fire('file-open', { name: 'wave' });
assert.deepEqual(frames(), docs.get('wave').frames, '选文件只改名，时间轴未切换');
assert.equal(node('#tc').getAttribute('total-ms'), '4000');
assert.equal(node('#files').files.find((item) => item.active).name, 'wave');
await node('#inspector').fire('keyframe-field-change', { field: 'us', axis: 1, value: 1540 });
await node('#guard').fire('play-request', { mode: 'send' });
assert.equal(played.length, 1);
assert.equal(played[0].action_id, 'wave');
assert.equal(played[0].action.name, docs.get('wave').name, '文档显示名被当成文件 ID');
assert.equal(played[0].action.frames[1].us[1], 1540, '下发丢失编辑内容');
assert.equal(docs.get('wave').frames[1].us[1], 1600, '下发意外修改了源文件');
window.confirm = () => false;
await node('#files').fire('file-open', { name: 'greet' });
await node('#role').fire('role-change', { role_id: 2 });
assert.equal(frames()[1].us[1], 1540);
assert.equal(node('#role').getAttribute('role-id'), '1');
window.confirm = () => true;
const old = deferred(); pending.set('greet', old);
const oldRead = node('#files').fire('file-open', { name: 'greet' });
await node('#files').fire('file-open', { name: 'wave' });
old.resolve(); await oldRead; pending.clear();
assert.deepEqual(frames(), docs.get('wave').frames, '旧文件响应覆盖了最新选择');
const reading = deferred(); pending.set('greet', reading);
const incoming = node('#files').fire('file-open', { name: 'greet' });
await node('#inspector').fire('keyframe-field-change', { field: 'us', axis: 1, value: 1530 });
reading.resolve(); await incoming; pending.clear();
assert.equal(frames()[1].us[1], 1530, '读取期间新编辑被响应覆盖');
await node('#files').fire('file-new');
assert.equal(node('#files').files.some((item) => item.active), false, '新草稿仍高亮磁盘文件');
await node('#guard').fire('play-request', { mode: 'send' });
assert.equal(played.at(-1).action_id, 'draft');
assert.deepEqual(played.at(-1).action.frames, frames(), '新草稿没有下发当前数据');
await new Promise((resolve) => setTimeout(resolve, 260));
assert.equal(node('#validate').result.valid, true);
await node('#files-toggle').fire('click');
assert.equal(node('#files-toggle').getAttribute('aria-expanded'), 'true');
await node('#files-close').fire('click');
assert.equal(node('#files-toggle').getAttribute('aria-expanded'), 'false');
await node('#inspector-toggle').fire('click');
for (const listener of windowEvents.get('keydown') || []) listener({ key: 'Escape' });
assert.equal(node('#inspector-toggle').getAttribute('aria-expanded'), 'false');
const roleSwitch = deferred(); pending.set('role', roleSwitch);
const switching = node('#role').fire('role-change', { role_id: 2 });
await node('#inspector').fire('keyframe-field-change', { field: 'us', axis: 1, value: 1535 });
roleSwitch.resolve(); await switching; pending.clear();
assert.equal(frames()[1].us[1], 1535, '切换角色期间产生的新草稿被覆盖');
assert.equal(node('#role').getAttribute('role-id'), '1');
let unloadBlocked = false;
for (const listener of windowEvents.get('beforeunload') || []) listener({ preventDefault() { unloadBlocked = true; } });
assert.equal(unloadBlocked, true);
assert.equal(closedSockets, 0, '尚未确认离开页面就关闭了 WebSocket');
const { createEditorDocuments } = await import('../../host/web/static/pages/editor-document.js');
const offline = { action: clone(docs.get('wave')), roles: [{ role_id: 1, config_version: '1.1.0' }],
  fileList: [], online: false, loading: false };
const local = createEditorDocuments({ S: offline, files: new Element(), roleSel: new Element(),
  listFiles: async () => [], stopPreview() {}, render() {}, validate() {} });
offline.action.frames[1].us[1] = 1522;
await local.connect(1);
assert.equal(offline.action.frames[1].us[1], 1522, '恢复连接覆盖了离线草稿');
await import('../../host/web/static/components/editor-chrome.js');
const RoleSelect = components.get('role-select'), role = new RoleSelect();
const selector = { innerHTML: '', disabled: false }, badge = { textContent: '' };
const firstOption = { textContent: '角色一' };
role.$ = (query) => query === '[data-sel]' ? selector : query === 'span[data-ver]' ? badge : firstOption;
role.setAttribute('role-id', '1'); role.setAttribute('disabled', 'true');
role._roles = [{ role_id: 1, name: '角色一', config_version: '1.1.0' }];
role._fill();
assert.equal(badge.textContent, '1.1.0', '版本标记没有写入版本 chip');
assert.equal(firstOption.textContent, '角色一', '角色名称被版本号覆盖');
assert.equal(selector.disabled, true);
console.log('PASS 实际编排控制器：完整文档、来源高亮、编辑快照、取消丢弃、并发选择与新编辑保护');
