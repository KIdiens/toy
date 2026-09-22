/* ============================================================================
 * pages/memories.js — 记忆管理（路由 /memories）控制器
 * 契约纪律：只有 GET /memories 与 DELETE /memories/{id} 两个端点。
 *   - 类型筛选以 kind 参数下推到后端；搜索在已加载集合上过滤。
 *   - 「清空全部」契约无批量端点 => 逐个 DELETE 并**如实汇报成功/失败**，
 *     绝不假装整体成功（避免「看着删了其实没删」）。
 *   - 删除失败不乐观移除该行，行保持原状。
 * ==========================================================================*/
import * as api from '../api.js';
import { toast } from '../components/chrome.js';

export default async function boot() {
  const q = (s) => document.querySelector(s);
  const list = q('#list'), empty = q('#empty-slot'), source = q('#source'),
    search = q('#search'), filter = q('#filter'), toggle = q('#mem-toggle'),
    clearBtn = q('#clear-all'), clearDlg = q('#clear-dlg'), footer = q('#count');

  let items = [];
  let kind = '';
  let keyword = '';
  let lastErr = null;

  function filtered() {
    if (!keyword) return items;
    const k = keyword.toLowerCase();
    return items.filter((m) => String(m.value || '').toLowerCase().includes(k));
  }

  function render() {
    const shown = filtered();
    if (lastErr) source.setAttribute('mode', 'offlineData');
    source.classList.toggle('hidden', !lastErr);
    list.items = shown;
    list.setAttribute('busy', 'false');
    const noItems = items.length === 0;
    empty.innerHTML = '';
    if (noItems && !lastErr) {
      const es = document.createElement('empty-state');
      es.setAttribute('icon', 'inbox');
      es.setAttribute('title', '还什么都没记住');
      es.setAttribute('hint', '当孩子说「记住我喜欢恐龙」时，会显示在这里。');
      empty.appendChild(es);
    } else if (shown.length === 0 && keyword) {
      const es = document.createElement('empty-state');
      es.setAttribute('icon', 'search');
      es.setAttribute('title', '没有匹配的记忆');
      es.setAttribute('hint', '试试换个关键词，或清除筛选条件。');
      empty.appendChild(es);
    }
    footer.textContent = `共 ${items.length} 条记忆${keyword ? `（筛选中：${shown.length} 条）` : ''}`;
    clearBtn.disabled = items.length === 0;
  }

  async function load() {
    list.setAttribute('busy', 'true');
    lastErr = null;
    try {
      items = await api.listMemories(kind ? { kind } : undefined) || [];
    } catch (e) {
      lastErr = e; items = [];
      toast('error', api.isOffline(e) ? '读取记忆失败：本机服务未连接' : (e.message || '读取记忆失败'));
    }
    try {
      const settings = await api.getSettings();
      toggle.setAttribute('enabled', String(settings && settings.memory_enabled !== false));
    } catch { toggle.setAttribute('enabled', 'true'); }
    render();
  }

  async function removeOne(id) {
    try {
      await api.deleteMemory(id);
      items = items.filter((m) => String(m.id) !== String(id));
      render();
      toast('success', '已删除。新会话不会再提起这条记忆（含历史与摘要缓存）。');
    } catch (e) {
      render(); // 行保持原状，不做乐观删除
      toast('error', api.isOffline(e) ? '删除未生效（未连接本机服务），请重试' : (e.message || '删除失败，请重试'));
    }
  }

  async function clearAll() {
    const ids = items.map((m) => m.id);
    let ok = 0, failed = 0;
    for (const id of ids) {
      try { await api.deleteMemory(id); ok += 1; }
      catch (e) { failed = ids.length - ok; if (api.isOffline(e)) break; }
    }
    await load();
    if (failed === 0) toast('success', `已清空全部记忆（${ok} 条）`);
    else toast('error', `已删除 ${ok} 条，${failed} 条未生效；请重试（本机服务可能不可达）`);
  }

  list.addEventListener('row-delete', (e) => removeOne(e.detail.id));
  search.addEventListener('search-input', (e) => { keyword = e.detail.q; render(); });
  filter.addEventListener('filter-change', (e) => { kind = e.detail.kind; load(); });
  clearBtn.addEventListener('click', () => { clearDlg.setAttribute('open', 'true'); clearDlg.open(); });
  clearDlg.addEventListener('cleared', () => clearAll());
  q('#retry')?.addEventListener('click', () => load());

  await load();
}
