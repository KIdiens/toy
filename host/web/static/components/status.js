/* ============================================================================
 * components/status.js — 连接状态与故障：conn-state-chip / role-chip / stub-badge
 * / estop-badge / fault-panel / interlock-checklist / device-banner / conn-summary
 * 硬约束：状态语义「色点 + 文字 + 图标」三重编码，不单靠颜色；
 *        FAULT_LOCKED 永不暗示自动恢复，唯一收敛路径是 fault-ack。
 * ==========================================================================*/
import { Base, define, ic, esc, emit, str, bool } from './base.js';

const CONN = {
  offline: { icon: 'plug', label: '未检测到设备', cls: 'conn--offline' },
  online: { icon: 'plug-zap', label: '已连接设备', cls: 'conn--online' },
  active: { icon: 'activity', label: '已连接 · 运动中', cls: 'conn--active' },
  fault: { icon: 'triangle-alert', label: '故障 · 停机锁定', cls: 'conn--fault' },
};

/** 由 Status.state 去重推导连接态（offline 由调用方在无后端时显式给出）。 */
export function connStateFromStatus(status) {
  if (!status) return 'offline';
  if (status.state === 'FAULT_LOCKED' || status.fault) return 'fault';
  if (status.state === 'PERFORMING' || status.state === 'SPEAKING') return 'active';
  return 'online';
}

class ConnStateChip extends Base {
  static observedAttributes = ['state'];
  template() {
    const s = CONN[str(this, 'state', 'offline')] || CONN.offline;
    return `<span class="conn ${s.cls}" role="status" aria-live="polite">
      <span class="conn__dot" aria-hidden="true"></span>${ic(s.icon, 18)}
      <span>${esc(s.label)}</span></span>`;
  }
}
define('conn-state-chip', ConnStateChip);

class RoleChip extends Base {
  static observedAttributes = ['role-id', 'config-version', 'display-name'];
  template() {
    const id = str(this, 'role-id');
    if (!id) return `<span class="chip">${ic('user-round', 18)}<span>未安装公仔</span></span>`;
    const name = str(this, 'display-name', `角色 ${id}`);
    const ver = str(this, 'config-version');
    return `<span class="chip">${ic('user-round', 18)}<span>${esc(name)}</span>
      ${ver ? `<span class="meta" data-num>v${esc(ver)}</span>` : ''}</span>`;
  }
}
define('role-chip', RoleChip);

class StubBadge extends Base {
  static observedAttributes = ['provider'];
  template() {
    if (str(this, 'provider', 'qwen') !== 'stub') return '';
    return `<span class="chip" title="未设置 DASHSCOPE_API_KEY，对话走本地桩后端，零网络出口">
      ${ic('flask-conical', 18)}<span>桩后端（无云端）</span></span>`;
  }
}
define('stub-badge', StubBadge);

class EstopBadge extends Base {
  static observedAttributes = ['health'];
  template() {
    const healthy = str(this, 'health', 'healthy') === 'healthy';
    return `<span class="chip">${ic(healthy ? 'shield-check' : 'shield-alert', 18)}
      <span>${healthy ? '急停健康' : '急停异常'}</span></span>`;
  }
}
define('estop-badge', EstopBadge);

const FAULT_INFO = {
  INTERLOCK: { text: '安全联锁未通过，电机未解锁。', clear: '检查公仔是否到位、急停是否健康，然后确认解除。' },
  TIMEOUT: { text: '运动期间超过 300ms 未收到有效帧，看门狗已停机。', clear: '确认主机与串口连接正常，再确认解除。' },
  PWM_INIT: { text: 'PWM 初始化失败，未进入可运动状态。', clear: '检查供电与引脚接线后重启主机，再确认解除。' },
  SAFETY_TRIP: { text: '触发抢占式停机（拔出 / 急停 / 串口超时 / 换角色）。', clear: '排除触发原因后确认解除。' },
  PROVIDER: { text: '对话后端不可用，本轮对话已中止。', clear: '检查网络与桩后端配置，重试即可。' },
  STORAGE: { text: '本地数据库读写失败。', clear: '检查磁盘空间与数据目录权限，重试即可。' },
};

class FaultPanel extends Base {
  static observedAttributes = ['code', 'detail'];
  template() {
    const code = str(this, 'code');
    if (!code) return '';
    const info = FAULT_INFO[code] || { text: '发生未知故障。', clear: '查看主机日志后确认解除。' };
    const detail = str(this, 'detail');
    return `<section class="card" role="alert" aria-live="assertive" style="border-color: var(--danger)">
      <div class="section__head" style="margin-bottom: var(--space-3)">
        <span class="section__ic" style="color: var(--danger)">${ic('triangle-alert', 22)}</span>
        <h2 class="section__title" style="color: var(--danger)">故障 · 停机锁定</h2>
        <span class="chip chip--mono">${esc(code)}</span>
      </div>
      <p class="small">${esc(info.text)}</p>
      ${detail ? `<p class="xs meta" title="${esc(detail)}" style="margin-top: var(--space-2)">${esc(detail)}</p>` : ''}
      <p class="small muted" style="margin-top: var(--space-3)">解除方式：${esc(info.clear)}
        <strong>系统不会自动恢复</strong>，必须人工确认。</p>
      <div class="rowflex" style="margin-top: var(--space-4)">
        <button class="btn btn--secondary" type="button" data-act="ack">${ic('check', 18)}标记已知悉并请求解除</button>
      </div></section>`;
  }
  mounted() { this.on('click', '[data-act="ack"]', () => emit(this, 'fault-ack', { code: str(this, 'code') })); }
}
define('fault-panel', FaultPanel);

class InterlockChecklist extends Base {
  static observedAttributes = ['docked', 'estop-healthy', 'role-match'];
  template() {
    const rows = [
      ['公仔到位', bool(this, 'docked'), '公仔未插到位时电机不启用'],
      ['急停健康', bool(this, 'estop-healthy'), '常闭触点断开时必须停机'],
      ['电阻编号与角色文件一致', bool(this, 'role-match'), '编号不符时不 ARM'],
    ].map(([label, ok, why]) => `<li class="rowflex" style="padding: var(--space-2) 0; border-bottom: 1px solid var(--border-soft)">
      <span style="color: var(${ok ? '--success' : '--warn'})">${ic(ok ? 'circle-check' : 'circle-slash', 18)}</span>
      <span class="grow">${esc(label)}<br><span class="xs meta">${esc(why)}</span></span>
      <span class="chip">${ok ? '满足' : '不满足'}</span></li>`).join('');
    return `<section class="card card--pad0">
      <div class="section__head" style="padding: var(--space-4) var(--space-4) 0">
        <span class="section__ic">${ic('list-checks', 20)}</span>
        <div><h2 class="section__title">连接前置条件</h2>
        <p class="section__hint">这些条件任一不满足，动作都不会下发到设备。</p></div></div>
      <ul style="list-style: none; margin: var(--space-2) var(--space-4) var(--space-4); padding: 0">${rows}</ul></section>`;
  }
}
define('interlock-checklist', InterlockChecklist);

class DeviceBanner extends Base {
  static slot = true;
  static observedAttributes = ['mode'];
  template() {
    const mode = str(this, 'mode', 'offline');
    const conf = {
      offline: {
        cls: 'banner banner--offline', icon: 'wifi-off',
        text: '未检测到设备。界面可离线查看与编辑，动作只会预演、不会发送。',
      },
      fault: {
        cls: 'banner banner--fault', icon: 'triangle-alert',
        text: '设备处于故障停机锁定，需人工确认解除后才会恢复。',
      },
      stub: {
        cls: 'banner banner--stub', icon: 'flask-conical',
        text: '当前使用桩后端对话，零网络出口；接入云 Key 后行为一致。',
      },
      offlineData: {
        cls: 'banner banner--offline', icon: 'wifi-off',
        text: '离线模式 · 数据来自本机数据库，读取与删除仍可用。',
      },
    }[mode] || { cls: 'banner', icon: 'info', text: '' };
    return `<div class="${conf.cls}" role="status">
      <span class="banner__ic">${ic(conf.icon, 20)}</span>
      <span class="banner__text">${esc(conf.text)}</span>
      <span class="banner__actions" data-slot></span>
      <span class="banner__actions">
        <button class="btn btn--secondary" type="button" data-act="retry">${ic('refresh-cw', 16)}重试检测</button>
        <button class="btn btn--ghost" type="button" data-act="guide">${ic('book-open', 16)}连接指引</button>
      </span></div>`;
  }
  mounted() {
    this.on('click', '[data-act="retry"]', () => emit(this, 'banner-retry', {}));
    this.on('click', '[data-act="guide"]', () => emit(this, 'banner-guide', {}));
  }
}
define('device-banner', DeviceBanner);

class ConnSummary extends Base {
  static observedAttributes = ['network-online', 'conn-state'];
  template() {
    const state = str(this, 'conn-state', 'offline');
    const online = bool(this, 'network-online');
    const icon = state === 'offline' ? 'wifi-off' : online ? 'wifi' : 'plug-zap';
    const text = state === 'offline' ? '未连接设备服务' : online ? '外网可达' : '本机服务可达 · 外网不可达';
    return `<span class="chip">${ic(icon, 18)}<span>${esc(text)}</span></span>`;
  }
}
define('conn-summary', ConnSummary);
