/* 编排文档加载：源文件 ID 与文档显示名称分开，迟到响应不能覆盖新选择或新草稿。 */
import * as api from '../api.js';
import { makeEmptyAction } from '../timeline.js';
import { toast } from '../components/chrome.js';

export function createEditorDocuments({ S, files, roleSel, listFiles, stopPreview, render, validate }) {
  let epoch = 0, baseline = JSON.stringify(S.action), initialized = false, changingRole = false;
  const signature = () => JSON.stringify(S.action);
  const dirty = () => signature() !== baseline;
  const confirmReplace = () => !dirty() || window.confirm('当前动作有未导出的修改，是否放弃并切换？');
  const version = (role) => S.roles.find((item) => item.role_id === role)?.config_version || null;

  function show() {
    files.files = S.fileList.map((item) => ({ ...item, active: item.name === S.sourceId }));
    files.setAttribute('empty-hint', S.online ? '本角色暂无可读取动作，可新建本地草稿。' : '离线时可编辑、预演和导出本地草稿。');
    files.setAttribute('document-status', S.loading ? '正在读取动作…' :
      `${S.sourceId || '新建草稿'}${dirty() ? ' · 已修改' : ''} · 下发执行当前编辑内容，未写盘`);
    roleSel.setAttribute('role-id', String(S.action.role_id));
    roleSel.setAttribute('config-version', S.documentVersion || '');
  }

  function replace(action, sourceId, configVersion, mechanicalLimits = null) {
    stopPreview();
    S.action = structuredClone(action);
    S.sourceId = sourceId;
    S.documentVersion = configVersion;
    S.documentLimits = mechanicalLimits;
    S.selected = Math.min(1, S.action.frames.length - 1);
    S.playhead = 0; S.offset = 0;
    baseline = signature();
    render(); validate();
  }

  async function open(name, { confirmed = false } = {}) {
    if (!name || changingRole) return;
    if (!confirmed && !confirmReplace()) return;
    const ticket = ++epoch, before = signature(), role = S.action.role_id;
    S.loading = true; render(); validate();
    try {
      const loaded = await api.getActionDocument(role, name);
      if (ticket !== epoch) return;
      if (signature() !== before) { toast('info', '读取期间产生了新修改，已保留当前草稿，请重新选择文件。'); return; }
      if (loaded.action?.role_id !== role || loaded.action_id !== name) throw Error('返回的动作与所选文件不一致');
      replace(loaded.action, name, loaded.config_version, loaded.limits);
    } catch (error) {
      if (ticket === epoch) toast('error', error.message || '读取动作失败，当前草稿已保留');
    } finally {
      if (ticket === epoch) { S.loading = false; render(); validate(); }
    }
  }

  async function refresh(select = false) {
    const ticket = ++epoch, role = S.action.role_id;
    S.loading = true; render(); validate();
    try {
      const list = await listFiles();
      if (ticket !== epoch || role !== S.action.role_id) return;
      S.fileList = Array.isArray(list) ? list : [];
      show();
      if (select) {
        const selected = S.fileList.find((item) => item.active) || S.fileList[0];
        if (selected) { S.loading = false; await open(selected.name); }
      }
    } catch (error) {
      if (ticket === epoch) { S.fileList = []; show(); toast('error', error.message || '无法读取动作列表'); }
    } finally {
      if (ticket === epoch) { S.loading = false; render(); validate(); }
    }
  }

  async function connect(role) {
    if (!initialized) {
      initialized = true;
      if (dirty()) {
        S.documentVersion = version(S.action.role_id);
        toast('info', '已保留离线编辑内容，连接后下发仍会校验设备角色。');
        await refresh(); return;
      }
      if (role) replace(makeEmptyAction(role, 'blank'), null, version(role));
      await refresh(true);
    } else {
      if (role !== S.action.role_id) toast('info', '设备角色已变化，请重新选择角色后下发。');
      await refresh();
    }
  }

  function newDraft() {
    if (changingRole) return;
    if (!confirmReplace()) return;
    ++epoch; S.loading = false;
    replace(makeEmptyAction(S.action.role_id, 'blank'), null, S.documentVersion, S.documentLimits);
  }

  async function changeRole(detail) {
    const previous = S.action.role_id;
    if (changingRole || detail.role_id === previous || !confirmReplace()) { show(); return; }
    changingRole = true;
    const before = signature();
    ++epoch; S.loading = true; roleSel.setAttribute('disabled', 'true'); render(); validate();
    try {
      await api.setActiveRole(detail.role_id);
      if (signature() !== before) {
        toast('info', '设备角色已切换；切换期间产生的新草稿已保留，请导出后重新选择角色。');
        return;
      }
      replace(makeEmptyAction(detail.role_id, 'blank'), null, version(detail.role_id));
      S.fileList = [];
      changingRole = false;
      await refresh(true);
    } catch (error) {
      toast('error', api.isOffline(error) ? '未确认角色切换成功，请重新连接核对。' : error.message);
    } finally {
      changingRole = false;
      S.loading = false; roleSel.removeAttribute('disabled'); render(); validate();
    }
  }

  files.addEventListener('file-open', (event) => open(event.detail.name));
  files.addEventListener('file-new', newDraft);
  roleSel.addEventListener('role-change', (event) => changeRole(event.detail));
  window.addEventListener('beforeunload', (event) => {
    if (dirty()) { event.preventDefault(); event.returnValue = ''; }
  });
  return { render: show, refresh, connect, open, dirty, newDraft, changeRole };
}
