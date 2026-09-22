# 02 · 动作编排器（Action Editor）

> 路由 `/editor` ｜ 主题：**深色默认**（工作台，`<html data-theme="dark">`）｜ 组件基座：原生 Web Components（ADR-007，无 Node 构建）
> Token 引用一律 `var(--…)`；图标一律 Lucide 1.x 标识；本文件不得出现字面色值。

---

## 1. 信息层级

用户在这里的核心任务是「**在时间轴上把四个自由度编排成一段可复现的动作，并在下发前知道它安不安全**」。视觉重心在中栏时间轴。

1. **顶栏（`--topbar-h` 56px）**——左：`role-select`（当前角色 + 配置版本）；中：`timecode-display`（当前/总时长）+ 缩放；右：`conn-state-chip` + `offline-banner`（压缩为 chip）+ 导入/导出入口。**回答「我在给哪个角色编、现在什么状态」**。
2. **左栏（`--rail-left` 260px）**——四轴轨道列表（轴名 + 色点 + 中位 μs + 当前采样值 + 限位迷你条），下方是 `action-file-list`（`wave.json`/`idle.json`…）+ 导入。**回答「改哪条轴、编哪个动作」**。
3. **中栏（自适应，主焦点）**——时间轴：`timeline-ruler` → 四条 `track-lane`（含 `keyframe-marker` 菱形 + 安全限位带）→ `audio-track-lane`（波形） → `transport-bar`。**精调发生在这里**。
4. **右栏（`--rail-right` 300px）**——三段：`keyframe-inspector`（时间 / 四轴目标 μs / 只读插值曲线 / 峰值速度 vs 上限）→ `axis-limit-panel` → `validate-result-list`。**回答「这一帧安不安全」**。

主次：时间轴（3）> 顶栏上下文（1）> 右侧校验（4）> 轨道/文件（2）。

## 2. 组件清单

> 自定义元素；状态经 attribute/property 传入，用户操作经 `CustomEvent`（如 `keyframe-move`、`playhead-seek`）回抛。图标列给出 Lucide 1.x 实际标识。

| 组件 | attribute / property | Lucide 图标 |
|---|---|---|
| `editor-shell` | attr `theme="dark"`；slot `topbar`/`left`/`center`/`right` | — |
| `role-select` | attr `role-id`、`config-version`；prop `roles[]`；emit `role-change` | `user-round`、`chevron-down` |
| `timecode-display` | attr `ms`、`total-ms`；mono | `timer` |
| `timeline-ruler` | attr `duration-ms`、`zoom`、`playhead-ms`；emit `ruler-seek` | `ruler` |
| `track-lane` | attr `axis`、`label`、`muted`、`selected`；emit `lane-toggle-mute` | `move-horizontal`、`volume-x`（静音） |
| `limit-band` | attr `min-us`、`max-us`、`neutral-us`；背景层，无交互 | `gauge` |
| `keyframe-marker` | attr `t-ms`、`us`、`easing`、`selected`、`locked`、`out-of-limit`；prop `frame`；emit `keyframe-move`、`keyframe-select`、`keyframe-delete` | `diamond` |
| `playhead` | attr `ms`；emit `playhead-seek`（拖拽） | — |
| `audio-track-lane` | attr `src`、`offset-ms`、`duration-ms`；prop `waveform`；emit `audio-offset-change` | `audio-lines` |
| `track-list` | 容器；emit `track-select` | `list` |
| `action-file-list` | prop `files[]`；emit `file-open`、`file-new`、`file-import` | `file-json`、`plus`、`upload` |
| `keyframe-inspector` | prop `keyframe`；attr `readonly-curve`；emit `keyframe-field-change` | `sliders-horizontal` |
| `interp-curve-preview` | attr `expr="s(u)=3u^2-2u^3"`（只读缩略） | `spline` |
| `axis-limit-panel` | prop `axes[]`（`min_us/max_us/neutral_us/direction/max_speed_us_s`） | `ruler` |
| `validate-result-list` | prop `result`（`ActionValidateResult`）；emit `result-jump-to` | `circle-check`、`triangle-alert`、`circle-x` |
| `transport-bar` | attr `playing`、`looping`、`preview-only`；emit `play`、`stop`、`loop-toggle` | `play`、`square`、`repeat` |
| `play-guard-button` | attr `disabled-reason`；emit `play-request` | `upload`（下发）/ `play`（预演） |
| `import-rolepack-dialog` | prop `manifest`；emit `import-confirm` | `file-archive`、`shield-check` |
| `confirm-dialog` | attr `tone="danger"`、`require-typing`；emit `confirm`、`cancel` | `triangle-alert` |
| `offline-banner` | attr `mode="preview"`；emit `banner-retry`、`banner-guide` | `wifi-off`、`refresh-cw`、`book-open` |
| `conn-state-chip` | attr `state`；prop `status` | `plug-zap` / `plug` / `activity` / `triangle-alert` |
| `toast-stack` | prop `toasts[]` | `circle-check`、`circle-x`、`info` |
| `theme-toggle` | attr `theme`；emit `theme-change` | `sun` / `moon` |

## 3. 交互状态

- **空态**：未打开任何动作 → 中栏显示引导卡（非空白）：「新建空白动作」或「导入角色包」；文案示例动作 `wave`（挥手）。左栏 `action-file-list` 仍列出磁盘已有文件。
- **加载态**：打开动作文件期间，轨道区 `loader-skeleton`；`validate-result-list` 显示「正在校验…」。
- **错误态**：
  - 文件解析失败（如「时间未递增」）→ 中栏顶部警示条 + 右侧 `validate-result-list` 逐字段列出，失败项可点 `result-jump-to` 定位到时间轴。
  - `/actions/play` 返回 `409` → **不触碰当前编辑内容**，`toast-stack` 显示 `message`（如「当前正在播放，已拒绝叠加」），`play-guard-button` 恢复可用。
- **离线态（预期常态，重点）**：
  - 顶栏 `conn-state-chip` = `offline`（`plug`）；`transport-bar` 的 `preview-only` 置位，**播放仍可用**（走本地插值预演：时间轴走、波形放、曲线采样），但显示 `offline-banner`「预演模式 · 未连接设备」。
  - `play-guard-button` 的「下发给设备」按钮 `disabled`，`disabled-reason="未连接设备"`，`title`/`aria-describedby` 给出原因与跳转连接指引的入口。**绝不静默失败**。
  - 全部编辑（拖关键帧、改数值、撤销）离线可用。
- **边界态**：
  - 关键帧拖出 `min_us`/`max_us` → 即时回弹 + `out-of-limit` 红闪 + tooltip「超出安全限位 max_us=…（轴N）」。
  - 峰值速度 > `max_speed_us_s`（本阶段 300μs/s）→ `keyframe-inspector` 标红 + 校验清单阻断。
  - 时长 > 60s 或帧数 > 1000 → 顶栏提示 + 阻断导出。
  - 首/末帧非中位（1500μs）→ 校验阻断并高亮首末 `keyframe-marker`。
  - 无数据轴（未选角色）→ `axis-limit-panel` 显示 `—` + 斜纹底。

## 4. 对应 API

| 端点 | 触发 | 渲染要点 |
|---|---|---|
| `GET /api/v1/roles` | 顶栏 `role-select` | 列出 `RoleSummary[]`（`role_id`/`name`/`config_version`），选择即写 `role-id`、`config-version` |
| `PATCH /api/v1/roles/active` | 切换角色 | 带 `request_id` + `role_id`；**409**（如正在播放）→ 还原选择 + `toast-stack` 显 `message` |
| `POST /api/v1/actions/validate` | 每次编辑后防抖 | 带 `request_id` + `action`（`ActionDoc`）；返回 `ActionValidateResult`（`valid` + `errors[{path,message}]`）→ `validate-result-list`，字段级高亮 |
| `POST /api/v1/actions/play` | 「下发给设备」 | 带 `request_id` + **预期 `role_id`** + `action_id` + `action_version` + `replace_current`（默认 `false`）；离线禁用。**409** → 不叠加，提示可选「显式替换」 |
| `POST /api/v1/actions/stop` | `transport-bar` 的 STOP | 带 `request_id`；任意状态可点，最高优先，立即取消队列 |
| `POST /api/v1/content/import` | 导入角色包 | multipart，带 `request_id` + `file`；**400**（`../`/绝对路径/符号链接/超量/高压缩比/签名不符）→ `import-rolepack-dialog` 展示失败项，**保留旧版本**；**201** → 原子切换后刷新 `roles` |
| `GET /api/v1/status` · `GET /api/v1/events` | 顶栏 chip 与进度 | 动作进度 `action.progress_ms` 驱动 `playhead`（真机在线时）；离线时由本地插值驱动 |

- 全部写操作带 `request_id`；第二个播放请求一律经 409 分支，**不得静默叠加两条时间轴**（AC-24）。

## 5. 响应式

- **主题**：默认深色，`<html data-theme="dark">` 由本页写入（`editor-shell` 挂载时设置）。切浅色走 `theme-toggle`。
- ≥ xl(1280)：三栏 `260 / auto / 300` 完整展开，`--container-max` 1440。
- lg–xl(1024–1280)：三栏不变，右栏缩至 260，左栏 232。
- md–lg(768–1024)：**折叠为两栏**——左栏收进抽屉（`drawer` 触发按钮），右栏保留（校验是安全关键）。
- < md：**单列栈**——顺序为「顶栏 → 轨道列表（折叠）→ 时间轴 → 属性抽屉」。时间轴改为横向滚动 + 缩小刻度密度；`rail-right` 变为底部抽屉（`--z-drawer`），`Esc` 关闭。
- 时间轴在窄屏保持 `tabular-nums`，数值列不换行；触摸目标 ≥ 44px（关键帧菱形视觉 8px 但命中区 ≥ 44px）。

## 6. 无障碍

- 焦点顺序：顶栏（`role-select` → `conn-state-chip` → 导入/导出）→ 左栏轨道 → 中栏（`timeline-ruler` 可聚焦，方向键移播放指针；`keyframe-marker` 逐个可聚焦）→ 右栏（`keyframe-inspector` 字段 → 校验项）→ `transport-bar`。
- 键盘：`Space` 播放/暂停、`←/→` 移动播放指针（`Shift` 加速）、关键帧选中后 `←/→` 微调时间、`↑/↓` 调目标 μs、`Delete` 删除、`Esc` 取消选择/关抽屉。
- `aria-label` 全中文语义：`role="slider"` + `aria-valuemin/-max/-now` 用于 `keyframe-marker` 的时间/脉宽；`playhead` 用 `role="slider"` + `aria-valuetext="00:04.200"`。
- `limit-band` 的绿/琥珀/红**同时**用图案（斜纹/实心）与文字标签表达，不单靠颜色。
- `toast-stack` 用 `aria-live="polite"`；校验阻断用 `aria-live="assertive"`。
- 焦点环 `var(--focus-ring)`，禁止移除；`prefers-reduced-motion` 下播放指针不做缓动跟随。
- 对比度：深色下 `var(--fg)`/`var(--muted)` 正文 ≥ 4.5:1；限位指示色 ≥ 3:1。

## 7. 验收对应

| AC | 本页体现 |
|---|---|
| AC-09 动作数值校验 | `keyframe-inspector` 输入 1399/1601/NaN/Inf 等非法值时即时阻断并提示 |
| AC-10 速度与时间校验 | 时间非递增 / 峰值速度 >300μs/s → `validate-result-list` 拒绝 |
| AC-11 离线预演 | 无硬件下 `transport-bar` 仅预演：四轴全程 1400–1600μs、首末帧 1500 |
| AC-16 旧 role_id 拒绝 | `play-guard-button` 携带预期 `role_id`；角色已变时 409 → 拒绝并提示 |
| AC-23 编排校验接口 | `POST /actions/validate` 只校验、不触发任何硬件动作（界面不出现 ARM/FRAME 痕迹） |
| AC-24 不叠加时间轴 | 第二个播放请求走 409 分支，`transport-bar` 不进入双轨状态 |
| AC-25 资源包安全 | `import-rolepack-dialog` 展示被拒原因，失败保留旧版本 |
