# 01 · 设备状态面板（Device Status）

> 路由 `/` ｜ 主题：浅色默认（管理页）｜ 组件基座：原生 Web Components（ADR-007，无 Node 构建）
> Token 引用一律 `var(--…)`；图标一律 Lucide 1.x 标识；本文件不得出现字面色值。

---

## 1. 信息层级

用户进入后**先确认「现在能不能动」，再决定「为什么不能动」**。自上而下：

1. **连接横幅**（非模态，不遮挡内容）——一句话给出当前连接结论 + 两条出路（重试 / 连接指引）。这是本页第一视觉锚点。
2. **顶栏状态条**——`conn-state-chip`（连接态）+ `role-chip`（当前角色与版本）+ `stub-badge`（是否桩后端）+ `theme-toggle`。
3. **四轴实时区**（主内容，最高信息密度）——四条 `axis-limit-bar` 横排，每轴配 `value-readout`。**实时值严格标注「目标位置」，禁止出现「实际角度」字样**（PWM 舵机无位置反馈；固件 `SETTLED` 仅证明指令到达）。
4. **联锁前置条件**（只读清单）——`interlock-checklist`：公仔到位 / 急停健康 / 电阻编号与文件一致。回答「为什么不能动」。
5. **故障区**（仅在有故障时出现，置末但高对比）——`fault-panel`，附故障码、人话解释与解除方式。
6. **页脚**：审计元信息（`session_generation`、固件协议版本、最后状态刷新时间），弱化处理。

主次关系：连接结论（1）> 四轴现状（3）> 归属与前置（2、4）> 故障细节（5）> 元信息（6）。

## 2. 组件清单

> 全部为自定义元素（custom element 名必含连字符）。状态经 attribute/property 传入，变更经 `CustomEvent` 回抛，不引入任何状态库。图标列给出 Lucide 1.x 实际标识。

| 组件 | attribute / property | Lucide 图标 |
|---|---|---|
| `conn-state-chip` | attr `state`=`offline｜online｜active｜fault`；prop `status`（`Status` schema）；emit `conn-retry` | `plug-zap`（online）/ `plug`（offline）/ `activity`（active）/ `triangle-alert`（fault） |
| `device-banner` | attr `mode`=`offline｜fault｜stub`；slot `actions`；emit `banner-retry`、`banner-guide` | `wifi-off`、`refresh-cw`、`book-open` |
| `role-chip` | attr `role-id`、`config-version`、`display-name`；prop `role` | `user-round` |
| `stub-badge` | attr `provider`=`stub｜qwen`（`stub` 时可见） | `flask-conical` |
| `axis-limit-bar` | attr `axis`=`1..4`、`min-us`、`max-us`、`neutral-us`、`state`=`normal｜near｜at｜nodata`；prop `frame`；emit `axis-select` | `gauge` |
| `value-readout` | attr `label`、`unit`、`source`=`target｜actual`、`data-available`；prop `value`；无数据渲染 `—` | `ruler` |
| `interlock-checklist` | attr `docked`、`estop-healthy`、`role-match`；prop `checks` | `list-checks` |
| `fault-panel` | attr `code`、`detail`；prop `fault`（`Fault` schema）；emit `fault-ack` | `triangle-alert` |
| `estop-badge` | attr `health`=`healthy｜unhealthy` | `shield-check` / `shield-alert` |
| `theme-toggle` | attr `theme`=`light｜dark`；emit `theme-change`（写 `<html data-theme>`） | `sun` / `moon` |
| `toast-stack` | prop `toasts[]`；emit `toast-dismiss` | `circle-check`、`circle-x`、`info` |
| `loader-skeleton` | attr `lines`、`variant` | `loader-circle` |

## 3. 交互状态

- **空态**：从未连接过设备时，四轴区显示四条 `axis-limit-bar` 的 `nodata` 底纹 + `value-readout` 全为 `—`；横幅文案：「未检测到设备。界面可离线查看，动作只会预演、不会发送。」
- **加载态**：初次拉取 `/api/v1/status` 与建立 `/api/v1/events` 期间，`conn-state-chip` 显示 `activity` + 「正在检测设备…」，四轴区上 `loader-skeleton`。
- **错误态**：`/status` 请求失败（网络/500）→ 横幅升级为中性警示：「读取设备状态失败」+ `refresh-cw` 重试；**不隐藏已缓存的上一帧**，但标注「数据可能过期」。
- **离线态（本阶段预期常态，重点）**：
  - 文案中性，**不得**出现「错误/崩溃/异常」；横幅用 `wifi-off` + 「未检测到设备 · 界面可离线查看」。
  - 两条出路常驻：`banner-retry`（重试检测）、`banner-guide`（连接指引，打开只读抽屉列出前置条件）。
  - 数值区一律 `—` + 斜纹底，明确「无数据 ≠ 0」。
  - 试运行/桩后端同时在线时，`stub-badge` 显示 `flask-conical` + 「桩后端（无云端）」，避免误判为已接真云。
- **边界态**：
  - 某轴目标值落在 `--limit-near` 区间 → `axis-limit-bar` 的 `state=near`（琥珀）+ 游标变色；到达/超出 `min-us`/`max-us` → `state=at`（红）。
  - `role-id` 缺失（未装公仔）→ `role-chip` 显示「未安装公仔」，四轴区整体置灰并保留 `—`。
  - 故障锁定：`state=FAULT_LOCKED` → `fault-panel` 常显 + `conn-state-chip` 转 `fault`；**无人工确认入口前不得暗示会自动恢复**。
  - 超长 `detail` → 单行截断 + `title` 全文 + 展开按钮。

## 4. 对应 API

| 端点 | 用途 | 渲染要点 |
|---|---|---|
| `GET /api/v1/status` | 首屏与轮询兜底 | 映射 `Status`：`state`（7 状态枚举）→ `conn-state-chip`；`mcu_armed/docked/estop_healthy` → `interlock-checklist`；`role_id` → `role-chip`；`session_generation` → 页脚；`network_online` → 横幅；`fault` → `fault-panel` |
| `GET /api/v1/events`（WebSocket） | 实时驱动，替代高频轮询 | 订阅 `CoreEvent`：状态迁移、故障、按键、动作进度。断线自动重连并回退到 `/status` 轮询（退避 2s→8s） |
| `GET /api/v1/features` | 功能开关 | 控制 `stub-badge`、是否显示「连接指引」入口等灰度 |

- 全部写操作**当前页无**（本页只读），故不涉及 `request_id`。
- `409` 处理范式（供全站复用）：统一信封 `{code,data,message}`；本页虽无写操作，但若 `PATCH /roles/active`（顶栏角色切换，若在本页暴露）返回 409，则**不覆盖当前展示**，用 `toast-stack` 显示 `message`，并把 `conn-state-chip` 保持真实状态。
- `/status` 响应**不含任何密钥**（契约强制）；前端不得缓存或回显敏感字段。

## 5. 响应式

- 断点：sm 640 / md 768 / lg 1024 / xl 1280。
- **主题**：默认浅色（`<html>` 无 `data-theme`）。`theme-toggle` 写入 `<html data-theme="dark">` 即切深色；选择持久化到本地偏好。
- ≥ lg：顶栏单行；四轴区四条 `axis-limit-bar` **竖排分组**（每轴一行，左侧轴名 + 色点，右侧限位条 + 数值），充分展开。
- md–lg：四轴仍竖排，页脚元信息折为两行。
- < md：`axis-limit-bar` 保持竖排但压缩数值列宽（等宽字体 `tabular-nums` 保列对齐）；横幅按钮换行堆叠；故障区 `detail` 折叠。
- 触摸目标：所有可点元素 ≥ `var(--touch-min)`（44px）。

## 6. 无障碍

- 焦点顺序：`conn-state-chip`（若可操作）→ 横幅「重试」→「连接指引」→ `theme-toggle` → 四轴区（按轴 1→4）→ 联锁清单 → 故障区「标记已知悉」→ 页脚。
- `aria-label` 要点：`axis-limit-bar` 用 `role="meter"` + `aria-valuemin/-max/-now`（值绑定目标脉宽），`aria-label="轴1 左臂 目标位置"`；**读屏文案同样说「目标位置」**。
- 连接状态**不单靠颜色**：`conn-state-chip` 同时呈现色点 + 文字 + 图标（色觉障碍可用）。
- `fault-panel` 出现时 `aria-live="assertive"`；`conn-state-chip` 变化用 `aria-live="polite"`。
- 对比度：正文 ≥ 4.5:1（`var(--fg)`/`var(--muted)`）；限位指示色 ≥ 3:1。
- 键盘：全部可达；`Esc` 关闭连接指引抽屉；焦点环用 `var(--focus-ring)`，禁止移除。

## 7. 验收对应

| AC | 本页体现 |
|---|---|
| AC-02 故障锁定 | `FAULT_LOCKED` 必须常显且不暗示自动恢复；`fault-ack` 为唯一收敛路径 |
| AC-03 抢占式停机 | `status.state` 转到停机锁定时，`conn-state-chip` 立即转 `fault`，横幅给出原因 |
| AC-06 看门狗 | `FAULT INTERLOCK_OR_TIMEOUT` 经 `fault-panel` 呈现，附 `detail` |
| AC-17 零网络出口 | `stub-badge` 依据 `/features` 显示桩后端标识，明确「无云端」 |
