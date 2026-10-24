/* 页面控制器级回归：删掉切换前的 if 分支时，即使脏判定纯函数仍正确也必须失败。 */
import assert from 'node:assert/strict';

class Element {
  constructor() { this.attrs = {}; this.listeners = new Map(); this.children = []; this.hidden = true; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(k, fn) { this.listeners.set(k, fn); }
  fire(k, detail = {}) { this.listeners.get(k)?.({ detail }); }
  replaceChildren(...nodes) { this.children = nodes; }
  querySelector() { return { focus() {} }; }
  get isConnected() { return true; }
}
globalThis.HTMLElement = Element;
globalThis.customElements = { define() {}, get() {} };
globalThis.window = globalThis;
globalThis.localStorage = { getItem: () => null, setItem() {} };
const nodes = new Map(['#role-list', '#editor', '#switch-guard', '#conn', '#retry']
  .map((id) => [id, new Element()]));
globalThis.document = {
  documentElement: new Element(), body: { appendChild() {} },
  querySelector: (id) => id === 'toast-stack' ? { push() {} } : nodes.get(id) || null,
  createElement: () => new Element(),
};
globalThis.fetch = async (url) => {
  const data = String(url).endsWith('/roles')
    ? [1, 2].map((role_id) => ({ role_id, name: `角色${role_id}`, config_version: '1.0',
      persona: { archetype: `身份${role_id}`, tone: '', speech_style: '',
        catchphrases: [], avoid: [], max_chars: 100 } }))
    : String(url).endsWith('/status') ? { state: 'IDLE', role_id: 1 }
      : { valid: true, compiled: '预览', errors: [] };
  return { ok: true, status: 200, text: async () => JSON.stringify({ code: 0, data }) };
};

const { DirtyTracker } = await import('../../host/web/static/components/persona-dirty.js');
const editor = nodes.get('#editor'), picker = nodes.get('#role-list');
const tracker = new DirtyTracker();
editor.load = (role, persona) => {
  editor.roleId = role.role_id;
  editor.value = structuredClone(persona);
  tracker.mark(persona);
};
editor.read = () => editor.value;
editor.isDirty = () => tracker.isDirty(editor.value);
editor.setPreview = (value) => { editor.preview = value; };
editor.setSaving = (value) => { editor.saving = value; };
picker.setRoles = () => {};
const { default: boot } = await import('../../host/web/static/pages/roles.js');
const { bindEditorTransfers } = await import('../../host/web/static/pages/editor-transfers.js');
assert.equal(typeof bindEditorTransfers, 'function');
await boot();
assert.equal(editor.roleId, 1);
editor.value.archetype = '未保存的修改';
picker.fire('role-select', { role_id: 2 });
const guard = nodes.get('#switch-guard');
assert.equal(editor.roleId, 1, '未保存时直接切换，页面控制器漏掉了拦截');
assert.equal(guard.hidden, false, '切换前没有显示确认条');
assert.equal(guard.children.length, 1);
guard.children[0].fire('cancel');
assert.equal(editor.roleId, 1);
assert.equal(editor.value.archetype, '未保存的修改');
assert.equal(guard.hidden, true);
picker.fire('role-select', { role_id: 2 });
guard.children[0].fire('confirm');
assert.equal(editor.roleId, 2, '确认后没有切换角色');
assert.equal(editor.isDirty(), false, '切换后没有使用新角色的基线');
picker.fire('role-select', { role_id: 1 });
assert.equal(editor.roleId, 1, '无修改时不应强制确认');
editor.value.archetype = '当前卡的修改';
picker.fire('role-select', { role_id: 1 });
assert.equal(editor.value.archetype, '当前卡的修改');
assert.equal(guard.hidden, true, '点击当前角色不应出现切换确认');
console.log('PASS 角色页面切换前确认、取消保留、确认切换与无修改对照');
