/* ============================================================================
 * host/web/static/app.js — 组件注册 + 页面引导（唯一入口）
 * 无 Node 构建链：本文件是原生 ES 模块，浏览器直接加载。
 * 页面 HTML 只需： import { boot } from '/static/app.js'; boot('memories');
 * ==========================================================================*/
// 1) 注册全部自定义元素（副作用导入；kebab-case 命名，光 DOM）
import './components/chrome.js';
import './components/status.js';
import './components/axis.js';
import './components/timeline-ui.js';
import './components/editor-chrome.js';
import './components/editor-inspector.js';
import './components/memories.js';
import './components/reminders.js';
import './components/settings-sections.js';
import './components/settings-content.js';
import './components/settings-danger.js';

import { initTheme, toast } from './components/chrome.js';

export { toast, initTheme };

/** 页面控制器：按路由懒加载（每页一个模块，便于控制单文件体量）。 */
const PAGES = {
  status: () => import('./pages/status.js'),
  editor: () => import('./pages/editor.js'),
  memories: () => import('./pages/memories.js'),
  reminders: () => import('./pages/reminders.js'),
  settings: () => import('./pages/settings.js'),
};

export const THEME_BY_PAGE = {
  status: 'light', editor: 'dark', memories: 'light', reminders: 'light', settings: 'light',
};

export async function boot(page) {
  initTheme(THEME_BY_PAGE[page] || 'light');
  // 未捕获的 Promise 拒绝 => 中性 toast，绝不静默失败
  window.addEventListener('unhandledrejection', (e) => {
    const msg = e.reason && e.reason.message ? e.reason.message : '操作未完成';
    toast('error', msg);
  });
  const load = PAGES[page];
  if (!load) { toast('error', `未知页面：${page}`); return; }
  try {
    const mod = await load();
    if (typeof mod.default === 'function') await mod.default();
  } catch (err) {
    // 页面控制器自身加载失败不吞掉：给出可操作的提示
    toast('error', `页面初始化失败：${err && err.message ? err.message : err}`);
  }
}
