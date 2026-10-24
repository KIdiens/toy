/* 编排器的资源包导入与动作导出；保持页面内编辑和后端导入的原有行为。 */
import * as api from '../api.js';
import { toast } from '../components/chrome.js';

export function bindEditorTransfers({ packFile, impDialog, action, reloadRoles }) {
  const q = (s) => document.querySelector(s);
  let pendingPack = null;
  q('#import-btn')?.addEventListener('click', () => packFile?.click());
  packFile?.addEventListener('change', () => {
    const f = packFile.files && packFile.files[0];
    if (!f) return;
    pendingPack = f;
    impDialog.manifest = { name: f.name, role_id: '—', config_version: '—', files: '—', pending: true };
    impDialog.setAttribute('open', 'true');
  });
  impDialog.addEventListener('import-confirm', async () => {
    if (!pendingPack) return;
    try {
      await api.importContent(pendingPack);
      impDialog.manifest = { name: pendingPack.name, role_id: '—', config_version: '—', files: '—', rejected: [] };
      toast('success', '资源包已导入并原子切换');
      await reloadRoles();
    } catch (e) {
      if (e instanceof api.ApiError && e.status === 400) {
        impDialog.manifest = { name: pendingPack.name, rejected: [{ path: '(服务端拒绝)', reason: e.message || '校验未通过' }] };
        toast('error', '导入失败，旧版本已保留');
      } else toast('error', api.isOffline(e) ? '未连接设备服务，导入未生效' : (e.message || '导入失败'));
    }
  });
  q('#export-btn')?.addEventListener('click', () => {
    const blob = new Blob([JSON.stringify(action(), null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = `${action().name || 'blank'}.json`; a.click();
    URL.revokeObjectURL(a.href);
    toast('success', '已导出本地动作文件');
  });

}
