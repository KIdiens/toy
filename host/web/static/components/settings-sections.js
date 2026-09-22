/* ============================================================================
 * components/settings-sections.js — 设置页日常区：settings-section / volume-select /
 * language-select / rating-select / setting-toggle / password-field /
 * host-endpoint-field / wifi-config / child-mode-panel / content-review-panel
 * 契约纪律：**只提交 openapi.yaml Settings/SettingsPatch 已定义的字段**
 *   （volume_level / language / memory_enabled / content_rating_max）。
 *   契约未覆盖的开关一律 disabled + 显式说明，绝不 PATCH 未定义字段（防"假成功"）。
 * ==========================================================================*/
import { Base, define, ic, esc, emit, str, bool } from './base.js';

const NO_API = '该设置项尚未在 API 契约（openapi.yaml Settings / SettingsPatch）中定义，本阶段只读展示。';

class SettingsSection extends Base {
  static slot = true;
  static observedAttributes = ['title', 'hint', 'danger', 'icon'];
  template() {
    const danger = bool(this, 'danger');
    const hint = str(this, 'hint');
    return `<section class="section ${danger ? 'section--danger' : ''}" role="group"
      aria-label="${esc(str(this, 'title'))}${danger ? ' · 不可逆' : ''}">
      <div class="section__head">
        <span class="section__ic">${ic(str(this, 'icon', 'settings'), 20)}</span>
        <div class="grow"><h2 class="section__title">${esc(str(this, 'title'))}${danger ? ' · 不可逆' : ''}</h2>
        ${hint ? `<p class="section__hint">${esc(hint)}</p>` : ''}</div></div>
      <div class="card"><span data-slot=""></span></div></section>`;
  }
}
define('settings-section', SettingsSection);

class VolumeSelect extends Base {
  static observedAttributes = ['value', 'disabled'];
  template() {
    const v = str(this, 'value', 'mid');
    const off = bool(this, 'disabled');
    const opts = [['low', '低'], ['mid', '中'], ['high', '高']].map(([k, l]) =>
      `<option value="${k}" ${k === v ? 'selected' : ''}>${l}</option>`).join('');
    return `<div class="field" style="margin-bottom:0"><label class="field__label" for="set-vol">音量（三档）</label>
      <select class="select" id="set-vol" data-vol ${off ? 'disabled' : ''}>${opts}</select>
      <span class="field__helper">音量键在设备上同样按低、中、高循环。</span></div>`;
  }
  updated() { const s = this.$('[data-vol]'); if (s) s.value = str(this, 'value', 'mid'); }
  mounted() { this.on('change', '[data-vol]', (e, t) => emit(this, 'settings-patch', { volume_level: t.value })); }
}
define('volume-select', VolumeSelect);

class LanguageSelect extends Base {
  static observedAttributes = ['value', 'disabled'];
  template() {
    const v = str(this, 'value', 'Auto');
    const off = bool(this, 'disabled');
    const opts = [['Auto', '自动（跟随对话）'], ['zh', '中文'], ['en', 'English']].map(([k, l]) =>
      `<option value="${k}" ${k === v ? 'selected' : ''}>${l}</option>`).join('');
    return `<div class="field" style="margin-bottom:0"><label class="field__label" for="set-lang">语言</label>
      <select class="select" id="set-lang" data-lang ${off ? 'disabled' : ''}>${opts}</select></div>`;
  }
  updated() { const s = this.$('[data-lang]'); if (s) s.value = str(this, 'value', 'Auto'); }
  mounted() { this.on('change', '[data-lang]', (e, t) => emit(this, 'settings-patch', { language: t.value })); }
}
define('language-select', LanguageSelect);

class RatingSelect extends Base {
  static observedAttributes = ['value', 'disabled'];
  template() {
    const v = str(this, 'value', 'kid');
    const opts = [['kid', '儿童（最严）'], ['teen', '少年'], ['all', '不限（不推荐）']].map(([k, l]) =>
      `<option value="${k}" ${k === v ? 'selected' : ''}>${l}</option>`).join('');
    return `<div class="field" style="margin-bottom:0"><label class="field__label" for="set-rating">出声审核级别</label>
      <select class="select" id="set-rating" data-rating>${opts}</select>
      <span class="field__helper">审核在合成与播放<strong>之前</strong>完成，不设「先说出来再撤回」。</span></div>`;
  }
  updated() { const s = this.$('[data-rating]'); if (s) s.value = str(this, 'value', 'kid'); }
  mounted() { this.on('change', '[data-rating]', (e, t) => emit(this, 'settings-patch', { content_rating_max: t.value })); }
}
define('rating-select', RatingSelect);

/** 通用开关行：field 为契约字段名时写回；disabled 时只读并给原因。 */
class SettingToggle extends Base {
  static observedAttributes = ['label', 'hint', 'checked', 'disabled', 'field'];
  template() {
    const checked = str(this, 'checked', 'true') !== 'false';
    const off = bool(this, 'disabled');
    const hint = str(this, 'hint');
    return `<div class="rowflex" style="padding:var(--space-3) 0;border-bottom:1px solid var(--border-soft)">
      <span class="grow"><span class="field__label" style="display:block">${esc(str(this, 'label'))}</span>
        ${hint || off ? `<span class="field__helper">${esc(off ? (hint || NO_API) : hint)}</span>` : ''}</span>
      <label class="switch"><input type="checkbox" data-tog ${checked ? 'checked' : ''}
        ${off ? 'disabled aria-disabled="true"' : ''} role="switch" aria-checked="${checked}"
        aria-label="${esc(str(this, 'label'))}"><span class="small">${checked ? '开' : '关'}</span></label></div>`;
  }
  mounted() {
    this.on('change', '[data-tog]', (e, t) => {
      const field = str(this, 'field');
      const on = t.checked;
      if (!field) { emit(this, 'settings-patch', { _unbound: true }); return; }
      emit(this, 'settings-patch', { [field]: on });
    });
  }
}
define('setting-toggle', SettingToggle);

class PasswordField extends Base {
  static observedAttributes = ['name', 'configured'];
  template() {
    const configured = str(this, 'configured', 'false') === 'true';
    return `<div class="field" style="margin-bottom:0">
      <label class="field__label" for="set-pw">配网密码</label>
      <input class="input" id="set-pw" type="password" data-pw autocomplete="new-password"
        placeholder="${configured ? '已设置 · 未修改' : '尚未设置'}" aria-describedby="set-pw-h">
      <span class="field__helper" id="set-pw-h">密码永不回显；仅在输入新值时才提交。留空表示不修改。</span></div>`;
  }
}
define('password-field', PasswordField);

class HostEndpointField extends Base {
  static observedAttributes = ['value'];
  template() {
    return `<div class="field" style="margin-bottom:0"><label class="field__label" for="set-host">主机服务地址</label>
      <input class="input input--num" id="set-host" type="url" data-host value="${esc(str(this, 'value', 'http://127.0.0.1:8000'))}"
        readonly aria-describedby="set-host-h">
      <span class="field__helper" id="set-host-h">本阶段仅绑定回环地址；开放局域网需登录并加 CSRF 防护。</span></div>`;
  }
}
define('host-endpoint-field', HostEndpointField);

class WifiConfig extends Base {
  static observedAttributes = ['ssid', 'configured', 'conn-state'];
  template() {
    const ssid = str(this, 'ssid');
    const configured = str(this, 'configured', 'false') === 'true';
    return `<div class="stack">
      <div class="rowflex"><conn-summary conn-state="${esc(str(this, 'conn-state', 'offline'))}"></conn-summary>
        <span class="chip chip--mono">${ic('wifi', 16)}SSID：${ssid ? esc(ssid) : '—'}</span></div>
      <password-field configured="${configured}"></password-field>
      <host-endpoint-field></host-endpoint-field>
      <div class="rowflex"><span class="grow"></span>
        <button class="btn btn--secondary" type="button" data-act="rescan">${ic('refresh-cw', 16)}扫描网络</button>
        <button class="btn btn--primary" type="button" data-act="apply" disabled aria-disabled="true"
          title="配网写入端点尚未在 API 契约中定义，需设备在线后开放">${ic('server', 16)}应用配网</button></div>
      <p class="xs meta">修改 Wi-Fi 前会弹窗显示设备名，避免误配邻居设备。当前离线时请先启动本机服务
        （<span data-num>python -m host.main --simulate</span>）。</p></div>`;
  }
  mounted() { this.on('click', '[data-act="rescan"]', () => emit(this, 'wifi-scan', {})); }
}
define('wifi-config', WifiConfig);

class ChildModePanel extends Base {
  static observedAttributes = ['memory-enabled', 'night-quiet', 'record-indicator'];
  template() {
    const mem = str(this, 'memory-enabled', 'true') !== 'false';
    return `<div class="stack">
      <setting-toggle label="长期记忆（记住对话）" field="memory_enabled" checked="${mem}"
        hint="关闭后孩子说「记住……」不会写入，已有记忆仍可查看与删除。"></setting-toggle>
      <setting-toggle label="夜间不主动发声" checked="${str(this, 'night-quiet', 'true')}"
        disabled hint="夜间静音时段设备不主动开口；该时段尚未在 API 契约中定义，只读展示。"></setting-toggle>
      <setting-toggle label="录音时可见指示" checked="${str(this, 'record-indicator', 'true')}"
        disabled hint="录音指示为强制项，不可关闭；静音＝真正停止采集，而非仅停止发送。"></setting-toggle>
      <setting-toggle label="访客模式不持久" checked="true" disabled
        hint="访客对话不写入长期记忆；由设备端策略强制，界面只读。"></setting-toggle>
    </div>`;
  }
}
define('child-mode-panel', ChildModePanel);

class ContentReviewPanel extends Base {
  static observedAttributes = ['enabled'];
  template() {
    const on = str(this, 'enabled', 'true') !== 'false';
    return `<div class="stack">
      <div class="rowflex" aria-readonly="true">
        <span class="grow"><span class="field__label">输出审核（前置）</span>
          <span class="field__helper">模型输出在<strong>合成 / 播放之前</strong>完成审核，不合格即拦截，
          绝不「先说出来再撤回」。</span></span>
        <span class="chip">${ic('scan-eye', 16)}${on ? '已启用 · 不可关闭' : '状态未知'}</span></div>
      <div class="rowflex" style="padding-top:var(--space-3);border-top:1px solid var(--border-soft)">
        <span class="section__ic">${ic('shield-check', 18)}</span>
        <span class="grow small">固定拒绝模板（不可被改写）：涉危险现实操作 => 引导找成年人；
          索要住址 / 学校 / 密码 => 固定拒绝。</span></div>
      <setting-toggle label="允许关闭内容审核" checked="false" disabled
        hint="审核不可关闭，此项仅用于表明边界。"></setting-toggle>
    </div>`;
  }
}
define('content-review-panel', ContentReviewPanel);
