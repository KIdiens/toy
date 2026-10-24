/* ============================================================================
 * pages/roles.js — 角色性格页（路由 /roles）控制器
 * 一页完成「选角色，改性格，看效果，保存」：
 *   选角色  role-picker 发 role-select，回填**该角色自己的** persona（null 即空表单）
 *   改性格  persona-editor 本地限长（超长真禁用保存按钮），输入事件回抛 persona-change
 *   看效果  POST /roles/{id}/persona/preview，300ms 防抖 + 序号丢弃过期响应
 *   保存    PATCH /roles/{id}/persona；422 逐字段落到对应字段下方，不弹一个笼统错误
 * 预览是「永远 200」的端点：valid:false + errors 才是内容问题，只有网络/404 才是故障。
 * ==========================================================================*/
import * as api from '../api.js';
import { toast } from '../components/chrome.js';
import { connStateFromStatus } from '../components/status.js';
import { needsSwitchConfirm } from '../components/persona-dirty.js';

/** 防抖窗口：用户正常打字节奏约 150-250ms/键，300ms 既能跟上又不会每键一发。 */
const PREVIEW_DEBOUNCE_MS = 300;

export default async function boot() {
  const picker = document.querySelector('#role-list');
  const editor = document.querySelector('#editor');
  if (!picker || !editor) return;

  let roles = [];
  let activeId = null;
  let currentId = null;
  let timer = null;
  let seq = 0;
  let viewId = 0;
  let editRevision = 0;
  const pendingSaves = new Set();

  const find = (id) => roles.find((r) => String(r.role_id) === String(id)) || null;

  /* ------------------------------------------------------------- 预览 */
  async function runPreview() {
    if (currentId === null) return;
    const roleId = currentId;
    const mySeq = ++seq;
    const persona = editor.read();
    editor.setPreview({ status: 'loading' });
    try {
      const res = await api.previewPersona(roleId, persona);
      if (mySeq !== seq) return; // 已有更新的请求，丢弃过期结果（否则预览会倒退）
      if (!res) { editor.setPreview({ status: 'error', message: '预览没有返回内容' }); return; }
      editor.setPreview(res.valid
        ? { status: 'ok', compiled: res.compiled }
        : { status: 'invalid', errors: res.errors || [] });
    } catch (err) {
      if (mySeq !== seq) return;
      const msg = err.status === 404
        ? '这个角色已不存在（可能已被换掉），请刷新页面后重试'
        : api.isOffline(err)
          ? '预览失败：本机服务未连通。改动只留在页面上，未写入设备。'
          : `预览失败：${err.message || '未知错误'}`;
      editor.setPreview({ status: 'error', message: msg });
      toast('error', msg);
    }
  }

  function schedulePreview(immediate = false) {
    clearTimeout(timer);
    if (immediate) { runPreview(); return; }
    timer = setTimeout(runPreview, PREVIEW_DEBOUNCE_MS);
  }

  /* ------------------------------------------------------------- 选择 */
  function select(id) {
    viewId += 1;
    currentId = Number(id);
    picker.setAttribute('selected', String(currentId));
    const role = find(currentId);
    editor.load(role, role ? role.persona || null : null);
    editor.setSaving(pendingSaves.has(currentId));
    schedulePreview(true);
  }

  /* ------------------------------------------------ 脏检查 + 切换前确认 */
  // 切换角色会把表单重填成另一个人的 persona，**已改的内容会没有提示地消失**。
  // 所以脏的时候不直接切：先用项目现成的 inline-confirm 问一句。
  let guardBox = null;
  let guardFor = null;

  /** 确认条挂在角色卡下方；页面没留锚点时按同一位置补一个，绝不「没容器就直接切」。 */
  function ensureGuardBox() {
    if (guardBox && guardBox.isConnected) return guardBox;
    guardBox = document.querySelector('#switch-guard');
    if (!guardBox) {
      guardBox = document.createElement('div');
      guardBox.id = 'switch-guard';
      picker.after(guardBox);
    }
    return guardBox;
  }

  function closeGuard() {
    if (!guardBox) return;
    guardBox.replaceChildren();
    guardBox.hidden = true;
    guardFor = null;
  }

  function askSwitch(nextId) {
    if (guardFor === nextId) return;
    closeGuard();
    guardFor = nextId;
    const el = document.createElement('inline-confirm');
    el.setAttribute('label', '切换会丢弃当前未保存的修改');
    el.addEventListener('confirm', () => { closeGuard(); select(nextId); });
    el.addEventListener('cancel', () => {
      closeGuard();
      toast('info', '已取消切换，当前修改仍在');
    });
    ensureGuardBox().replaceChildren(el);
    guardBox.hidden = false;
    el.querySelector('[data-act="yes"]')?.focus();
  }

  function requestSelect(id) {
    const next = Number(id);
    if (currentId !== null && String(currentId) === String(next)) {
      closeGuard(); // 点的是当前这张卡：不是切换，保留已改的内容
      return;
    }
    if (needsSwitchConfirm(currentId, next, editor.isDirty())) { askSwitch(next); return; }
    closeGuard();
    select(next);
  }

  /* ------------------------------------------------------------- 保存 */
  async function save(persona) {
    if (currentId === null || pendingSaves.has(currentId)) return;
    const roleId = currentId;
    const submittedView = viewId;
    const submittedRevision = editRevision;
    const name = find(roleId)?.name || `角色${roleId}`;
    const sameDraft = () => currentId === roleId && viewId === submittedView && editRevision === submittedRevision;
    pendingSaves.add(roleId);
    editor.clearFormError();
    editor.setSaving(true);
    try {
      const res = await api.savePersona(roleId, persona);
      const role = find(roleId);
      const savedPersona = res && res.persona ? res.persona : null;
      if (role) role.persona = savedPersona;
      let keptDraft = false;
      if (String(currentId) === String(roleId)) {
        if (sameDraft()) {
          editor.load(role, savedPersona);
          schedulePreview(true);
        } else {
          editor.markSaved(savedPersona);
          keptDraft = editor.isDirty();
        }
      }
      picker.setRoles(roles, activeId);
      toast('success', keptDraft
        ? `${name}已保存提交时的人设；当前表单的后续改动尚未保存`
        : savedPersona ? `${name}的人设已保存；正在进行的回复仍用旧人设，下一轮生效`
          : `${name}已恢复默认人设（不再发自定义段落）`);
    } catch (err) {
      if (!sameDraft()) {
        toast('error', `${name}提交的人设保存失败：${err.message || '未确认成功'}；当前填写已保留`);
      } else if (err instanceof api.ClarifyError) {
        editor.applyServerErrors(err);
        toast('error', '有字段不合规，已标在对应字段下方；改动未写入');
      } else if (api.isOffline(err)) {
        const msg = '未确认保存成功：本机服务不可达或响应超时。当前填写已保留，请确认服务后重试。';
        editor.showFormError(msg);
        toast('error', msg);
      } else {
        const msg = `保存失败：${err.message || '未知错误'}（当前填写已保留）`;
        editor.showFormError(msg);
        toast('error', msg);
      }
    } finally {
      pendingSaves.delete(roleId);
      if (currentId === roleId) editor.setSaving(false);
    }
  }

  /* ------------------------------------------------------------- 装载 */
  async function load() {
    picker.setAttribute('busy', 'true');
    try {
      roles = (await api.listRoles()) || [];
    } catch (err) {
      roles = [];
      toast('error', api.isOffline(err)
        ? '读取角色失败：本机服务未连通，页面无法编辑'
        : `读取角色失败：${err.message || '未知错误'}`);
    }
    try {
      const status = await api.getStatus();
      activeId = status && status.role_id !== undefined ? status.role_id : null;
      // 顶栏的连接态按真实 state 渲染（不是写死 offline），否则「未检测到设备」会骗人
      document.querySelector('#conn')?.setAttribute('state', connStateFromStatus(status));
    } catch {
      activeId = null; // 拿不到状态只影响「当前激活」标记，不影响编辑
      document.querySelector('#conn')?.setAttribute('state', 'offline');
    }
    picker.setAttribute('busy', 'false'); // 必须先解除 busy，骨架态下塞数据不会出行
    picker.setRoles(roles, activeId);
    const first = find(activeId) || roles[0] || null;
    if (first) {
      editor.removeAttribute('inert');
      select(first.role_id);
    } else {
      // 没有角色就没有「改谁」这件事：整块表单 inert，绝不留一个点了没反应的保存按钮
      editor.setAttribute('inert', '');
      editor.load(null, null);
      editor.showFormError('没有读到角色列表，暂时不能保存。请确认本机服务在运行，再点「重新读取角色」。');
    }
  }

  /* ------------------------------------------------------------- 事件 */
  picker.addEventListener('role-select', (e) => requestSelect(e.detail.role_id));
  editor.addEventListener('persona-change', () => { editRevision += 1; schedulePreview(); });
  editor.addEventListener('persona-reset', () => {
    editRevision += 1;
    schedulePreview(true);
    toast('info', '已清空本页字段；点「保存人设」才会真正恢复默认人设');
  });
  editor.addEventListener('persona-invalid', () => toast('error', '有字段超出上限，保存已被拦下'));
  editor.addEventListener('persona-save', (e) => save(e.detail));

  document.querySelector('#retry')?.addEventListener('click', () => { load(); });

  await load();
}
