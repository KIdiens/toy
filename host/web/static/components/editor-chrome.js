/* ============================================================================
 * components/editor-chrome.js — 编排器外框：editor-shell / role-select /
 * timecode-display / transport-bar / play-guard-button / action-file-list / track-list
 * 离线是预期常态：transport 仍可预演（本地插值），仅「下发给设备」禁用并给原因。
 * ==========================================================================*/
import { Base, define, ic, esc, emit, num, str, bool } from './base.js';
import { AXES } from '../timeline.js';

class EditorShell extends Base {
  static slot = true;
  template() {
    return `<div class="editor">
      <aside class="editor__rail editor__rail--left" data-left><span data-slot="left"></span></aside>
      <section class="editor__center"><span data-slot="center"></span></section>
      <aside class="editor__rail editor__rail--right" data-right><span data-slot="right"></span></aside>
    </div>`;
  }
  toggleRail(which) {
    const el = this.$(`[data-${which}]`);
    if (el) el.setAttribute('data-open', el.getAttribute('data-open') === 'true' ? 'false' : 'true');
  }
}
define('editor-shell', EditorShell);

class RoleSelect extends Base {
  static observedAttributes = ['role-id', 'config-version', 'disabled'];
  /** @param {Array} v RoleSummary[] */
  set roles(v) { this._roles = Array.isArray(v) ? v : []; this._fill(); }
  get roles() { return this._roles || []; }
  template() {
    return `<label class="rowflex small"><span class="muted">角色</span>
      <span class="ic-btn">${ic('user-round', 18)}</span>
      <select class="select" data-sel aria-label="选择角色" style="min-height:36px;width:auto"></select>
      <span class="chip chip--mono" data-ver></span></label>`;
  }
  mounted() {
    this.on('change', '[data-sel]', (e, t) => {
      const opt = t.options[t.selectedIndex];
      emit(this, 'role-change', { role_id: Number(t.value), config_version: opt?.dataset.ver || '' });
    });
  }
  updated() { this._fill(); }
  _fill() {
    const sel = this.$('[data-sel]');
    if (!sel) return;
    const cur = str(this, 'role-id');
    const list = this._roles && this._roles.length ? this._roles
      : (cur ? [{ role_id: Number(cur), name: `角色 ${cur}`, config_version: str(this, 'config-version') }] : []);
    sel.innerHTML = list.length
      ? list.map((r) => `<option value="${r.role_id}" data-ver="${esc(r.config_version || '')}"
          ${String(r.role_id) === cur ? 'selected' : ''}>${esc(r.name || '角色 ' + r.role_id)}</option>`).join('')
      : `<option value="">未安装公仔</option>`;
    const ver = this.$('[data-ver]');
    if (ver) ver.textContent = list.find((r) => String(r.role_id) === cur)?.config_version || str(this, 'config-version') || '—';
  }
}
define('role-select', RoleSelect);

class TimecodeDisplay extends Base {
  static observedAttributes = ['ms', 'total-ms'];
  template() {
    const ms = num(this, 'ms', 0), total = num(this, 'total-ms', 0);
    const fmt = (v) => {
      const m = Math.floor(v / 60000), s = Math.floor((v % 60000) / 1000), f = Math.round(v % 1000);
      return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${String(f).padStart(3, '0')}`;
    };
    return `<span class="ic-btn">${ic('timer', 18)}
      <span class="timecode">${fmt(ms)} <span class="meta">/ ${fmt(total)}</span></span></span>`;
  }
}
define('timecode-display', TimecodeDisplay);

class TransportBar extends Base {
  static observedAttributes = ['playing', 'looping', 'preview-only', 'duration-ms'];
  template() {
    const playing = bool(this, 'playing');
    const looping = bool(this, 'looping');
    const preview = bool(this, 'preview-only');
    return `<div class="seg">
      <button class="btn btn--primary" type="button" data-act="play">${ic(playing ? 'square' : 'play', 18)}
        ${playing ? '停止' : '播放'}</button>
      <button class="btn btn--ghost" type="button" data-act="stop">${ic('square', 18)}STOP</button>
      <button class="btn ${looping ? 'btn--secondary' : 'btn--ghost'}" type="button" data-act="loop"
        aria-pressed="${looping}">${ic('repeat', 18)}循环</button>
      <span class="grow"></span>
      ${preview ? `<span class="chip">${ic('wifi-off', 16)}离线预演 · 设备不动作</span>`
        : `<span class="chip">${ic('plug-zap', 16)}设备在线</span>`}
      <span class="chip chip--mono" data-num>${Math.round(num(this, 'duration-ms', 0))}ms</span></div>`;
  }
  mounted() {
    this.on('click', '[data-act="play"]', () => emit(this, 'play', {}));
    this.on('click', '[data-act="stop"]', () => emit(this, 'stop', {}));
    this.on('click', '[data-act="loop"]', () => emit(this, 'loop-toggle', {}));
  }
}
define('transport-bar', TransportBar);

class PlayGuardButton extends Base {
  static observedAttributes = ['disabled-reason', 'playing'];
  template() {
    const reason = str(this, 'disabled-reason');
    const blocked = !!reason;
    return `<span class="seg">
      <button class="btn btn--primary" type="button" data-act="preview">${ic('play', 18)}预演</button>
      <button class="btn btn--secondary" type="button" data-act="send" ${blocked ? 'disabled aria-disabled="true"' : ''}
        ${blocked ? `title="${esc(reason)}" aria-describedby="pg-reason"` : ''}>${ic('upload', 18)}下发给设备</button>
      ${blocked ? `<span class="xs meta" id="pg-reason">${esc(reason)} ·
        <button class="btn btn--ghost xs" type="button" data-act="guide">${ic('book-open', 14)}连接指引</button></span>` : ''}
    </span>`;
  }
  mounted() {
    this.on('click', '[data-act="preview"]', () => emit(this, 'play-request', { mode: 'preview' }));
    this.on('click', '[data-act="send"]', (e, t) => { if (!t.disabled) emit(this, 'play-request', { mode: 'send' }); });
    this.on('click', '[data-act="guide"]', () => emit(this, 'play-request', { mode: 'guide' }));
  }
}
define('play-guard-button', PlayGuardButton);

class ActionFileList extends Base {
  static observedAttributes = ['empty-hint'];
  /** @param {{name:string,active?:boolean}[]} v 真实动作文件列表（来自后端；无数据源时传空数组） */
  set files(v) { this._files = Array.isArray(v) ? v : []; if (this.isConnected) this.render(); }
  get files() { return this._files || []; }
  template() {
    const files = this._files || [];
    const rows = files.length
      ? files.map((f) => `<button class="file-item" type="button" data-open="${esc(f.name)}"
          aria-current="${f.active ? 'true' : 'false'}">${ic('file-json', 18)}<span class="grow">${esc(f.name)}</span>
          ${f.active ? ic('check', 16) : ''}</button>`).join('')
      : `<p class="xs meta">${esc(str(this, 'empty-hint', '磁盘暂无动作文件，可从下方新建或导入。'))}</p>`;
    return `<div class="stack">
      <div class="rowflex"><span class="rail-title grow" style="margin:0">动作文件</span>
        <button class="btn btn--ghost" type="button" data-act="new" title="新建空白动作">${ic('plus', 18)}</button>
        <button class="btn btn--ghost" type="button" data-act="import" title="导入角色包">${ic('upload', 18)}</button>
      </div>
      <div style="margin: 0 calc(var(--space-2) * -1)">${rows}</div></div>`;
  }
  mounted() {
    this.on('click', '[data-open]', (e, t) => emit(this, 'file-open', { name: t.getAttribute('data-open') }));
    this.on('click', '[data-act="new"]', () => emit(this, 'file-new', {}));
    this.on('click', '[data-act="import"]', () => emit(this, 'file-import', {}));
  }
}
define('action-file-list', ActionFileList);

class TrackList extends Base {
  /** @param {{axis:number,label:string,muted:boolean,selected:boolean,values:number[]}} v */
  set tracks(v) { this._tracks = Array.isArray(v) ? v : []; if (this.isConnected) this.render(); }
  get tracks() { return this._tracks || []; }
  template() {
    const tracks = (this._tracks && this._tracks.length) ? this._tracks : AXES.map((a) => ({ axis: a.i + 1, label: a.name }));
    const rows = tracks.map((t) => {
      const i = t.axis - 1;
      const v = Array.isArray(t.values) ? t.values[i] : null;
      const shown = Number.isFinite(v) ? Math.round(v) : '—';
      return `<div class="track-item" data-axis="${t.axis}" role="option" aria-selected="${t.selected ? 'true' : 'false'}">
        <span class="track-item__dot" style="background: var(${AXES[i]?.token || '--track-1'})"></span>
        <span data-axis-name="${t.axis}"><span class="track-item__name">轴${t.axis} ${esc(t.label || AXES[i]?.name || '')}</span>
          <br><span class="track-item__sub">${t.muted ? '已静音' : `采样 ${shown}µs`}</span></span>
        <button class="btn btn--ghost" type="button" data-mute="${t.axis}"
          aria-pressed="${t.muted}" title="${t.muted ? '取消静音' : '静音该轴'}">${ic('volume-x', 16)}</button></div>`;
    }).join('');
    return `<div class="stack"><span class="rail-title">四轴轨道</span>
      <div role="listbox" aria-label="四轴轨道列表">${rows}</div></div>`;
  }
  mounted() {
    this.on('click', '[data-mute]', (e, t) => {
      e.stopPropagation();
      emit(this, 'lane-toggle-mute', { axis: Number(t.getAttribute('data-mute')) });
    });
    this.on('click', '[data-axis]', (e, t) => {
      if (e.target.closest('[data-mute]')) return;
      emit(this, 'track-select', { axis: Number(t.getAttribute('data-axis')) });
    });
  }
}
define('track-list', TrackList);
