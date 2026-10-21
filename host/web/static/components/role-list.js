/* ============================================================================
 * components/role-list.js — 角色选择列表（光 DOM）
 * 三个角色一次性铺开：点谁就是改谁，不做多级跳转。
 * 「当前激活」来自 GET /api/v1/status 的 role_id；取不到时该标记只是不显示，
 * 不影响选择与编辑（绝不因为拿不到状态就把整页禁用）。
 * ==========================================================================*/
import { Base, define, ic, esc, emit, str } from './base.js';

class RolePicker extends Base {
  static observedAttributes = ['selected', 'busy'];
  constructor() { super(); this.roles = []; this.activeId = null; }

  template() {
    if (str(this, 'busy') === 'true') {
      return '<loader-skeleton rows="3" aria-label="正在载入角色列表"></loader-skeleton>';
    }
    if (!this.roles.length) {
      return `<p class="small muted">没有读到角色列表。列表为空时无法改性格，请确认本机服务可用后重试。</p>`;
    }
    const selected = str(this, 'selected');
    const cards = this.roles.map((role) => {
      const id = String(role.role_id);
      const active = String(this.activeId ?? '') === id;
      return `<button class="role-card" type="button" data-role="${esc(id)}"
        aria-pressed="${selected === id}">
        <span class="role-card__name">${ic('user-round', 18)}<span>${esc(role.name)}</span></span>
        <span class="role-card__meta">
          <span class="chip chip--mono">${ic('audio-lines', 14)}${esc(role.tts_voice || '未指定')}</span>
          <span class="chip chip--mono">${ic('tag', 14)}${esc(role.config_version || '—')}</span>
        </span>
        ${active ? `<span class="role-card__active">${ic('circle-check', 14)}当前激活</span>` : ''}
      </button>`;
    }).join('');
    return `<div class="role-grid" role="group" aria-label="选择一个角色来改性格">${cards}</div>`;
  }

  mounted() {
    this.on('click', '[data-role]', (_e, el) => {
      emit(this, 'role-select', { role_id: Number(el.getAttribute('data-role')) });
    });
  }

  /** 控制器统一入口：先解除 busy 再塞数据，否则模板仍是骨架，行永远出不来。 */
  setRoles(roles, activeId) {
    this.roles = Array.isArray(roles) ? roles : [];
    this.activeId = activeId;
    this.render();
  }
}

define('role-picker', RolePicker);
export { RolePicker };
