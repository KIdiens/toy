/* ============================================================================
 * pages/reminders.js — 提醒管理（路由 /reminders）控制器
 * 契约纪律：GET /reminders、POST /reminders、DELETE /reminders/{id}。
 *   - 创建带 request_id + 稳定 idempotency_key（同一表单重复提交防重复创建）；
 *     422 = 需澄清 => **不关表单**、错误靠近字段、焦点移到该字段。
 *   - 取消为软取消（status=cancelled，行保留可回溯）。
 *   - 时间不可靠（离线 / network_online=false）=> 显示校时告警并把 pending 标注「已暂停」，
 *     绝不静默错发。
 * ==========================================================================*/
import * as api from '../api.js';
import { toast } from '../components/chrome.js';

const TZ = 'Asia/Shanghai';

function localTimeToUtcIso(time, now = new Date()) {
  const [h, m] = String(time || '20:00').split(':').map(Number);
  const d = new Date(now);
  d.setHours(Number.isFinite(h) ? h : 20, Number.isFinite(m) ? m : 0, 0, 0);
  if (d <= now) d.setDate(d.getDate() + 1); // 已过则顺延到明天
  return d.toISOString();
}

export default async function boot() {
  const q = (s) => document.querySelector(s);
  const list = q('#list'), empty = q('#empty-slot'), warn = q('#clock-warn'),
    form = q('#form'), newBtn = q('#new-reminder'), footer = q('#tz-note');

  let items = [];
  let roles = [];
  let netOnline = true;
  let knownTime = true;

  function paused() { return !netOnline || !knownTime; }

  function render() {
    list.items = items;
    if (paused()) list.querySelectorAll('reminder-row').forEach((r) => {
      const nf = r.querySelector('next-fire-readout');
      if (nf && r.reminder && r.reminder.status === 'pending') nf.setAttribute('paused', 'true');
    });
    warn.classList.toggle('hidden', !paused());
    empty.innerHTML = '';
    if (items.length === 0) {
      const es = document.createElement('empty-state');
      es.setAttribute('icon', 'bell-off');
      es.setAttribute('title', '还没有提醒');
      es.setAttribute('hint', '设一个试试——比如每天晚上 8 点提醒刷牙。夜间静音时段设备不会主动发声。');
      empty.appendChild(es);
    }
    const dupes = new Set();
    const seen = new Map();
    items.forEach((r) => {
      if (r.status !== 'pending') return;
      const key = String(r.due_at_utc || '');
      if (seen.has(key)) dupes.add(key); else seen.set(key, true);
    });
    footer.textContent = `共 ${items.length} 条提醒 · 存储 UTC、展示 ${TZ}`
      + (dupes.size ? ` · 有 ${dupes.size} 组时间重叠（同日同时刻）` : ' · 创建带幂等键，重复提交不会重复创建');
  }

  async function load() {
    list.setAttribute('busy', 'true');
    try { items = await api.listReminders() || []; }
    catch (e) {
      items = [];
      toast('error', api.isOffline(e) ? '读取提醒失败：本机服务未连接' : (e.message || '读取提醒失败'));
    }
    try {
      const st = await api.getStatus();
      netOnline = st ? st.network_online !== false : true;
      knownTime = true;
    } catch { netOnline = false; knownTime = false; }
    try { roles = await api.listRoles() || []; } catch { roles = []; }
    const vs = form.querySelector('voice-select');
    if (vs) { vs.roles = roles; if (roles[0]) vs.setAttribute('role-id', String(roles[0].role_id)); }
    // 必须先解除 busy 再塞数据：reminder-list 的模板在 busy==='true' 时只返回骨架，
    // 此时写 items 不会产生任何行。顺序反过来列表会永远停在骨架屏。
    list.setAttribute('busy', 'false');
    list.items = items;
    render();
  }

  form.addEventListener('form-submit', async (e) => {
    const d = e.detail;
    if (!netOnline) { toast('error', '离线不可创建，请先连接本机服务'); return; }
    form.busy = true;
    try {
      const created = await api.createReminder({
        profile_id: 'default', text: d.text,
        due_at_utc: localTimeToUtcIso(d.time), timezone: TZ,
        repeat_rule: d.repeat_rule === 'once' ? null : d.repeat_rule,
        idempotency_key: d.idempotency_key,
      });
      items = [...items, created || { id: d.idempotency_key, text: d.text, status: 'pending',
        due_at_utc: localTimeToUtcIso(d.time), timezone: TZ, repeat_rule: d.repeat_rule }];
      form.reset();
      render();
      toast('success', '提醒已创建');
    } catch (err) {
      if (err instanceof api.ClarifyError) { // 422：澄清分支，不关表单
        form.showError('time', err.message || '请指明是上午还是下午');
        form.querySelector('time-field')?.$('[data-time]')?.focus();
      } else if (api.isOffline(err)) toast('error', '创建失败，未保存（本机服务不可达）');
      else { form.showError('text', err.message || '创建失败，未保存'); toast('error', err.message || '创建失败，未保存'); }
    } finally { form.busy = false; }
  });

  list.addEventListener('row-cancel', async (e) => {
    try {
      await api.cancelReminder(e.detail.id);
      items = items.map((r) => (String(r.id) === String(e.detail.id) ? { ...r, status: 'cancelled' } : r));
      render();
      toast('success', '提醒已取消（保留记录可回溯）');
    } catch (err) {
      toast('error', api.isOffline(err) ? '取消失败：本机服务不可达，状态未改变' : (err.message || '取消失败'));
    }
  });
  list.addEventListener('row-toggle', () => {
    toast('info', '当前 API 契约未提供提醒修改端点，暂停/启用不可写入；如需该能力请在契约中补充。');
  });

  newBtn.addEventListener('click', () => { form.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); form.focusFirst(); });
  warn.addEventListener('clock-sync-open', () => toast('info', '请让设备联网一次以确认当前时间，或手动校时后重试。'));
  q('#retry')?.addEventListener('click', () => load());

  await load();
}
