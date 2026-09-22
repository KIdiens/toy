/* ============================================================================
 * pages/settings.js — 设置（路由 /settings）控制器
 * 契约纪律：GET/PATCH /settings + POST /content/import。**只 PATCH 契约字段**
 *   （volume_level / language / memory_enabled / content_rating_max）。
 *   不做乐观写入：PATCH 成功前不改显示值，失败即回滚并说明原因，
 *   离线时明确「无法保存到本机服务」，绝不进入假成功。
 * ==========================================================================*/
import * as api from '../api.js';
import { toast } from '../components/chrome.js';
import { connStateFromStatus } from '../components/status.js';

export default async function boot() {
  const q = (s) => document.querySelector(s);
  const conn = q('#conn'), summary = q('#conn-summary'), wifi = q('#wifi'),
    review = q('#review'), impCard = q('#import-card'), impResults = q('#import-results'),
    resetEntry = q('#reset-entry'), resetDlg = q('#reset-dlg');

  let current = {};

  function applySettings(s) {
    current = s || {};
    q('#volume')?.setAttribute('value', current.volume_level || 'mid');
    q('#language')?.setAttribute('value', current.language || 'Auto');
    q('#rating')?.setAttribute('value', current.content_rating_max || 'kid');
    q('#child')?.setAttribute('memory-enabled', String(current.memory_enabled !== false));
    wifi?.setAttribute('ssid', current.wifi_ssid || '');
    wifi?.setAttribute('configured', String(!!current.wifi_ssid));
  }

  async function load() {
    let settings = null, status = null, features = null;
    try { settings = await api.getSettings(); } catch { settings = null; }
    try { status = await api.getStatus(); } catch { status = null; }
    try { features = await api.listFeatures(); } catch { features = null; }
    const online = !!status;
    conn.setAttribute('state', online ? connStateFromStatus(status) : 'offline');
    summary.setAttribute('conn-state', online ? 'online' : 'offline');
    summary.setAttribute('network-online', String(online && status.network_online !== false));
    wifi.setAttribute('conn-state', online ? 'online' : 'offline');
    if (settings) applySettings(settings);
    else applySettings({}); // 读取失败：各控件显示默认并在提示中说明
    review.setAttribute('enabled', String(!features || features.content_review !== false));
    q('#load-hint')?.classList.toggle('hidden', !!settings);
    q('#offline-hint')?.classList.toggle('hidden', online);
  }

  /** 通用单项保存：契约字段、成功前不改显示、失败回滚（不做乐观写入）。 */
  async function patch(field, value, control, host, attr) {
    const prev = current[field];
    if (control) control.disabled = true;
    try {
      await api.patchSettings({ [field]: value });
      current[field] = value;
      if (host && attr) host.setAttribute(attr, String(value)); // 成功后才同步显示值
      toast('success', '已保存');
    } catch (err) {
      if (control) control.value = prev === undefined || prev === null ? '' : String(prev);
      if (err instanceof api.ClarifyError) toast('error', `未保存：${err.message}`);
      else if (api.isOffline(err)) toast('error', '离线无法保存到本机服务，已回滚为原值');
      else toast('error', err.message || '未保存，请重试');
    } finally {
      if (control) control.disabled = false;
    }
  }

  const bindSelect = (id, field, attr) => {
    const el = q(id);
    el?.addEventListener('settings-patch', (e) => patch(field, e.detail[field], el.querySelector('select'), el, attr));
  };
  bindSelect('#volume', 'volume_level', 'value');
  bindSelect('#language', 'language', 'value');
  bindSelect('#rating', 'content_rating_max', 'value');

  const child = q('#child');
  child?.addEventListener('settings-patch', (e) => {
    if (e.detail.memory_enabled === undefined) return;
    patch('memory_enabled', e.detail.memory_enabled, child.querySelector('input[type="checkbox"]'), child, 'memory-enabled');
  });

  // 资源包导入：400 => 逐条拒绝原因 + 明确「旧版本已保留」
  impCard?.addEventListener('import-submit', async (e) => {
    if (e.detail.error) { impResults.results = []; toast('error', e.detail.error); return; }
    impCard.setAttribute('busy', 'true');
    impResults.setAttribute('busy', 'true');
    try {
      await api.importContent(e.detail.file);
      impResults.results = [{ path: e.detail.name, tone: 'ok', message: '导入成功，已原子切换生效目录' }];
      toast('success', '资源包已导入');
    } catch (err) {
      if (err instanceof api.ApiError && err.status === 400) {
        impResults.results = [{ path: e.detail.name, tone: 'error', message: err.message || '校验未通过（路径/符号链接/压缩比/签名）' }];
        toast('error', '导入失败，旧版本已保留');
      } else if (api.isOffline(err)) {
        impResults.results = [{ path: e.detail.name, tone: 'error', message: '未连接本机服务，导入未生效（旧版本保留）' }];
        toast('error', '导入未生效：本机服务不可达');
      } else {
        impResults.results = [{ path: e.detail.name, tone: 'warn', message: err.message || '导入未完成' }];
      }
    } finally {
      impCard.setAttribute('busy', 'false');
      impResults.setAttribute('busy', 'false');
    }
  });

  // 危险区：三闸 UI 完整；但契约未提供恢复出厂端点 => 如实说明未执行，不假装成功
  resetEntry?.addEventListener('click', () => { resetDlg.setAttribute('open', 'true'); resetDlg.open(); });
  resetDlg?.addEventListener('reset-confirm', () => {
    toast('error', 'API 契约未定义恢复出厂端点，操作未执行（已记录为待补契约项，避免假成功）');
  });
  resetDlg?.addEventListener('reset-cancel', () => { /* 关窗即清零，无需额外处理 */ });

  q('#retry')?.addEventListener('click', () => load());

  await load();
}
