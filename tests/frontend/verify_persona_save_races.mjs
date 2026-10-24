/* 真实角色控制器 + 可控保存响应，覆盖等待期间的编辑、清空、切换和错误回流。 */
import assert from 'node:assert/strict';

class Element {
  constructor() { this.attrs = {}; this.listeners = new Map(); this.children = []; this.hidden = true; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(k, fn) { this.listeners.set(k, fn); }
  fire(k, detail = {}) { return this.listeners.get(k)?.({ detail }); }
  replaceChildren(...nodes) { this.children = nodes; }
  querySelector() { return { focus() {} }; }
  get isConnected() { return true; }
}
globalThis.HTMLElement = Element;
globalThis.customElements = { define() {}, get() {} };
globalThis.window = globalThis;
globalThis.localStorage = { getItem: () => null, setItem() {} };
let nodes, requests, messages;
globalThis.document = {
  documentElement: new Element(), body: { appendChild() {} },
  querySelector: (id) => id === 'toast-stack' ? { push: (msg) => messages.push(msg) } : nodes.get(id),
  createElement: () => new Element(),
};
const { PersonaEditor } = await import('../../host/web/static/components/persona-editor.js');
const { default: boot } = await import('../../host/web/static/pages/roles.js');
const persona = (archetype) => ({ archetype, tone: '', speech_style: '', catchphrases: [], avoid: [], max_chars: 100 });
const response = (data, status = 200, message = '') => ({
  ok: status < 400, status, text: async () => JSON.stringify({ code: status < 400 ? 0 : status, data, message }),
});

async function page() {
  requests = []; messages = [];
  nodes = new Map(['#role-list', '#switch-guard', '#conn', '#retry'].map((k) => [k, new Element()]));
  const editor = new PersonaEditor();
  nodes.set('#editor', editor);
  editor.load = (role, p) => {
    editor.currentRole = role;
    editor.value = structuredClone(p || persona(''));
    editor._dirty.mark(p);
    editor.formError = '';
    editor.fieldError = '';
  };
  editor.read = () => structuredClone(editor.value);
  editor.setPreview = (value) => { editor.preview = value; };
  editor.clearFormError = () => { editor.formError = ''; };
  editor.showFormError = (msg) => { editor.formError = msg; };
  editor.applyServerErrors = (err) => { editor.fieldError = err.message; };
  editor.setSaving = (value) => { editor.saving = value; };
  const picker = nodes.get('#role-list');
  picker.setRoles = (roles) => { picker.roles = roles; };
  globalThis.fetch = async (url, opts) => {
    if (opts.method === 'PATCH') {
      return new Promise((resolve, reject) => requests.push({
        url, body: JSON.parse(opts.body), resolve, reject,
      }));
    }
    const data = String(url).endsWith('/roles')
      ? [1, 2].map((role_id) => ({ role_id, name: `角色${role_id}`, persona: persona(`身份${role_id}`) }))
      : String(url).endsWith('/status') ? { state: 'IDLE', role_id: 1 }
        : { valid: true, compiled: '预览', errors: [] };
    return response(data);
  };
  await boot();
  return {
    editor, picker,
    edit(text) { editor.value.archetype = text; editor.fire('persona-change'); },
    save() { return editor.fire('persona-save', editor.read()); },
    select(id) {
      picker.fire('role-select', { role_id: id });
      const guard = nodes.get('#switch-guard');
      if (!guard.hidden) guard.children[0].fire('confirm');
    },
    finish(index, text, status = 200) {
      requests[index].resolve(response({ persona: text === null ? null : persona(text) }, status,
        status === 422 ? '请求参数校验失败：archetype: 内容非法' : ''));
    },
  };
}

{
  const p = await page();
  p.edit(' 提交内容 '); const saved = p.save();
  p.finish(0, '提交内容'); await saved;
  assert.equal(p.editor.value.archetype, '提交内容', '无新输入时仍应回填后端规范化值');
  assert.equal(p.editor.isDirty(), false);
  assert.equal(p.editor.saving, false);
}
{
  const p = await page();
  p.edit('提交内容'); const saved = p.save();
  p.edit('等待时的新输入');
  p.finish(0, '提交内容'); await saved;
  assert.equal(p.editor.value.archetype, '等待时的新输入', '保存响应覆盖了提交后的输入');
  assert.equal(p.editor.isDirty(), true, '新输入不应被算作已经保存');
  p.edit('提交内容');
  assert.equal(p.editor.isDirty(), false, '脏检查基线应更新为实际落盘值');
}
{
  const p = await page();
  p.edit('提交内容'); const saved = p.save();
  p.edit('中途输入'); p.edit('提交内容');
  p.finish(0, '提交内容'); await saved;
  assert.equal(p.editor.isDirty(), false);
  assert.equal(messages.at(-1).message.includes('尚未保存'), false, '改回落盘内容后不应谎报未保存');
}
{
  const p = await page();
  p.edit('提交内容'); const saved = p.save();
  await p.save();
  assert.equal(requests.length, 1, '保存中按回车不应重复发起同角色写入');
  p.editor.load(p.editor.currentRole, null);
  p.editor.fire('persona-reset');
  p.finish(0, '提交内容'); await saved;
  assert.equal(p.editor.value.archetype, '', '恢复默认后的清空草稿被旧响应恢复');
  assert.equal(p.editor.isDirty(), true, '清空操作还没有保存');
  const reset = p.save(); p.finish(1, null); await reset;
  assert.equal(p.editor.isDirty(), false, '清空成功后应以默认人设作为基线');
  assert.equal(p.picker.roles[0].persona, null);
}
{
  const p = await page();
  p.edit('角色1提交'); const first = p.save();
  p.select(2); p.edit('角色2提交'); const second = p.save();
  assert.equal(requests.length, 2, '不同角色可独立保存');
  p.finish(0, '角色1提交'); await first;
  assert.equal(p.editor.saving, true, '角色1结束不能解除角色2的保存中状态');
  assert.equal(p.editor.value.archetype, '角色2提交');
  p.finish(1, '角色2提交'); await second;
  assert.equal(p.editor.saving, false);
  assert.equal(p.editor.isDirty(), false);
}
{
  const p = await page();
  p.edit('旧提交'); const saved = p.save();
  p.select(2); p.select(1);
  p.finish(0, '旧提交'); await saved;
  assert.equal(p.editor.value.archetype, '身份1', '离开再回来属于新的编辑视图，旧响应不能重填');
  assert.equal(p.editor.isDirty(), true, '此时磁盘已保存旧提交，与当前表单不同');
}
{
  const p = await page();
  p.edit('错误提交'); const saved = p.save();
  p.select(2);
  p.finish(0, '', 422); await saved;
  assert.equal(p.editor.value.archetype, '身份2');
  assert.equal(p.editor.fieldError, '', '角色1的校验错误污染了角色2');
  assert.equal(p.editor.formError, '');
}
{
  const p = await page();
  p.edit('错误提交'); const saved = p.save();
  p.edit('已修正的草稿');
  p.finish(0, '', 422); await saved;
  assert.equal(p.editor.fieldError, '', '旧字段错误不应标到已修改的草稿上');
  assert.equal(p.editor.value.archetype, '已修正的草稿');
  assert.equal(p.editor.isDirty(), true);
}
{
  const p = await page();
  p.edit('错误提交'); const saved = p.save();
  p.finish(0, '', 422); await saved;
  assert.ok(p.editor.fieldError.includes('archetype'), '原草稿的真实字段错误仍必须显示');
  assert.equal(p.editor.isDirty(), true);
  assert.equal(p.editor.saving, false);
}
{
  const p = await page();
  p.edit('离线时的输入'); const saved = p.save();
  requests[0].reject(new TypeError('network unavailable')); await saved;
  assert.ok(p.editor.formError.includes('未确认保存成功'), '断网时不能断言服务端一定没有写盘');
  assert.equal(p.editor.value.archetype, '离线时的输入');
  assert.equal(p.editor.saving, false);
  const retry = p.save();
  assert.equal(requests.length, 2, '失败后必须释放保存状态并允许重试');
  p.finish(1, '离线时的输入'); await retry;
  assert.equal(p.editor.isDirty(), false);
}
console.log('PASS 保存规范化、后续编辑、清空、重复提交、跨角色并发、错误隔离与失败重试');
