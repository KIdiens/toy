# 可换公仔 AI 语音动作玩具 · 主机管理界面 DESIGN.md

> 生成日期：2026-09-21 | 设计师：颜好看 | 基于：开发书 v1.0（第 5.2 / 7 / 9.3 / 13 / 15 章）
> 三轴刻度：Variance=3 / Motion=3 / Density=7 | 平台轴：web（localhost 优先，响应式下探平板）
> 设计寄存器：**Product（设计服务产品）** —— 不是营销页，克制着色、功能性动效、数据可视化替代照片

---

## 0. 设计目标与三重受众（一切取舍的判据）

| 受众 | 场景 | 对界面的要求 | 设计手段 |
|---|---|---|---|
| **家长** | 管记忆 / 管提醒 / 设儿童边界 | 看得懂、敢点、知道"删了会怎样" | 浅色主题、16px 阅读正文、完整标签 + helper text、白话文案、删除二次确认 |
| **孩子** | （不直接操作网页）只会误触 | 不可达危险操作 | 危险区隔离 + 输入型确认 + 局域网登录门槛；设备端管理入口隐藏 |
| **开发者/创作者** | 四轴动作编排、精调关键帧 | 精确、密集、可键盘操作 | 深色工作台、µs 数值等宽、限位区间可视化、曲线与峰值速度可见、快捷键 |

> **核心工程约束（决定整套界面的骨架）**：本阶段硬件未采购、不接云端 → **无真实设备可连**。因此界面必须满足"**设备离线时依然可用且状态明确**"。

---

## 1. Visual Theme & Atmosphere（视觉主题与氛围）

- **关键词**：可校准、克制、诚实、工作台、可读性优先
- **氛围**：像一件**精密仪器的工作台**——不是炫技的仪表盘，而是"你能看懂它在做什么、为什么不能做"。深色用于编排器（长时精调，波形/关键帧对比清晰），浅色用于管理页（家长阅读）。
- **对标品牌**：
  1. **Teenage Engineering（OP-1 / EP-133）** —— 物理设备的设计语言：清晰标签、无废话、精密中带温度。对应"家长看得懂 + 开发时能精调"。
  2. **Bottango / CameraBotics Lensmaster** —— 机器人关节关键帧编辑器的功能范式（关节轨道 + 时间轴 + 擦洗器 + 缓入缓出 + Graph）。
  3. **Helios GCS 舵机诊断面板 / MRUI** —— 实时数值 + 交规式限位色（正常绿 / 接近琥珀 / 越限红）+ 1500µs 中位刻度 + "目标值 vs 实际值"分离标注。

## 2. Color Palette & Roles（色彩与角色）

- 配色来源：`references/design-systems/color-palettes.md` 第 14 套「生产力青绿」改造为 **石油青（petrol teal）** 品牌色，避开 AI 默认靛蓝/靛紫。
- **A1-identity（浅色 / 管理页）**：`--bg #F6F8F7` · `--surface #FFFFFF` · `--fg #14201E` · `--muted #556663` · `--accent #0F766E` · `--border #DDE4E2`
- **A1-identity（深色 / 编排器）**：`--bg #0C1211` · `--surface #121A18` · `--fg #EAF1EF` · `--muted #9DB0AB` · `--accent #14B8A6` · `--border rgba(255,255,255,0.08)`
- **A2-semantic**：`--success #15803D` · `--warn #B45309` · `--danger #B91C1C` · `--info #0E7490`
- **B-slot**：`--fg-2 #33403D` · `--surface-warm #EDF1F0` · `--meta #8A9793` · `--accent-hover #0D6259` · `--accent-soft #E4F1EF`
- **C-extension（领域色，本项目专属）**：
  - 限位：`--limit-normal #15803D` / `--limit-near #D97706` / `--limit-at #DC2626` / `--limit-neutral-mark #64748B`
  - 播放指针：`--playhead #B45309`（铜橙，行业惯例，与品牌色区分）
  - 连接：`--conn-offline #8A9793` / `--conn-online #15803D` / `--conn-active #0E7490` / `--conn-fault #B91C1C`
  - 四轴轨道身份：轴1左臂 `#2563EB` / 轴2右臂 `#0E7490` / 轴3头左右 `#B45309` / 轴4腰左右 `#7C3AED`
- **着色纪律（防"3+ 高饱和色"反模式）**：
  - 轨道身份色**仅**用于 6px 色点与 20% 透明轨道底色；关键帧菱形一律用 `--fg` / `--accent`，不平涂轨道色。
  - 限位色**仅**出现在限位 mini-bar 与越界反馈中，不作装饰。
  - **每屏可见 `--accent` ≤ 2 处**（主操作按钮 + 当前选中态）。
- **深色模式**：用亮度递进表达层级（`bg→surface→surface-2→surface-3`），不用阴影；避免纯黑 `#000` / 纯白 `#fff` 直出。

## 3. Typography（排版）

- **字体栈**：`--font-display: Lexend` + `--font-body: Figtree` + `--font-mono: JetBrains Mono` + 中文回退 `Noto Sans SC`。
- **字体加载（离线硬约束处理）**：**自托管 woff2 子集，不使用外链 `@import`**。设备无外网时外链会导致三款字体全部回退、µs 数值列失去对齐。`design-tokens.css` 已改为 `@font-face` 指向 `/assets/fonts/*.woff2`，并配"等宽优先"回退链（`SFMono-Regular / Consolas / Liberation Mono / Menlo / ui-monospace`），即使字体文件缺失，数值列也不失对齐。四款字体均 SIL OFL 1.1，允许自托管。
- **降级行为（明确接受并标注）**：字体文件缺失时，显示字体回退至 `Noto Sans SC` / 系统中文字体栈，Sans 字重层级会略弱化；但 `font-variant-numeric: tabular-nums` 仍保证等宽数字列对齐。此为可接受降级，已在 `design-tokens.css` 注释与本文件同步标注。
- **选型理由**：Lexend/Figtree 均以"可读性优先"为目标且**不在反射拒绝列表**（避开 Inter / Space Grotesk / DM Sans / Plus Jakarta 等训练默认值）；JetBrains Mono 承担 µs / 时间码 / 关键帧坐标，用 `tabular-nums` 防数字跳动。
- **字号阶梯（8 级）**：12 / 13 / 15 / 16 / 18 / 22 / 28 / 34 px。工具 UI 默认 15px，家长阅读正文 16px。
- **字重**：400 正文 / 500 次标题与按钮 / 600 主标题。
- **行高**：正文 1.6 / 标题 1.25 / 展示 1.15。
- **字距**：标题 `-0.02em`；ALL CAPS 标签 `0.07em`；正文与等宽数值 `0`。

## 4. Components（组件规范）

- **按钮**：`primary`(accent 底) / `secondary`(描边 accent) / `ghost`(中性) / `danger`(danger 描边，**不与主按钮相邻**)；覆盖 default / hover / active / focus-visible / disabled / loading。
- **输入框**：default / focus(`--focus-ring`) / error(danger 描边 + 字段内错误文案) / disabled；label 常显，禁止 placeholder 当 label。
- **卡片**：`1px border + radius 10px + 无默认阴影`；hover 仅变边框色 → 规避"幽灵卡片"（1px 边框 + blur≥16 阴影）反模式。
- **连接状态 chip**：`offline`(灰) / `online`(绿) / `active`(青，脉冲) / `fault`(红)——常驻顶栏。
- **无数据占位**：离线时数值区显示 `—` + 斜纹底（`--no-data`），明确"无数据 ≠ 值为 0"。
- **限位 mini-bar**：横向轨道 + 中位刻度 + 绿/琥珀/红分区 + 当前值游标。
- **图标**：**锁定 Lucide（ISC 许可，可商用）**，统一 24px 网格 / 2px 描边 / 圆头圆角；尺寸 16px(行内) / 20px(按钮内) / 24px(独立)。16px 时描边降至 1.75 保持视觉重量。**全项目唯一图标源，禁止混用其他库，禁止 emoji 作功能图标。**
  - 关键语义映射：`Play / Square / Repeat / ChevronLeft / Mic / Volume2 / Bell / Brain / Save / Upload / Download / Settings / ShieldCheck / Lock / TriangleAlert / WifiOff / Usb / Gauge / Waves / Ruler / Link2Off / RotateCcw`
- **图标禁用项**（反 AI 模板）：不出现火箭、火焰、灯泡、闪光、闪电、靶心、图表类符号（emoji）作图标；不出现紫色→粉色渐变。

## 5. Layout & Spacing（布局与间距）

- 间距基准：4px 网格（4/8/12/16/20/24/32/40/48/64）。圆角阶梯 6/10/14/9999。
- **编排器工作台**：固定三栏 — 左栏 260px（角色 + 四轴轨道列表）/ 中栏自适应（时间轴）/ 右栏 300px（关键帧属性 + 限位 + 校验）；顶栏 56px；容器上限 1440px。
- **管理页**：单列阅读，内容列上限 720px；节区间距 40 / 32 / 24px（桌面/平板/手机）。
- 响应式断点：sm 640 / md 768 / lg 1024 / xl 1280。
- **移动端回退**：Variance=3 无需回退，但 <768px 时编排器由三栏折叠为"轨道列表 → 时间轴 → 属性抽屉"的单列栈；触摸目标 ≥44×44px。

## 6. Depth & Elevation（深度与阴影）

- 三级：`--elev-flat none` / `--elev-ring 0 0 0 1px var(--border)`（卡片默认）/ `--elev-raised`（仅下拉菜单、抽屉、弹窗）。
- z-index：base 0 / dropdown 1000 / sticky 1100 / drawer 1200 / modal 1300 / toast 1400。
- 深色主题**不用阴影表达层级**，用 `surface → surface-2 → surface-3` 亮度递进。

## 7. Do's & Don'ts（设计守则）

**应当（Do）**
1. 离线时界面照常可编辑，仅"下发给设备"类动作禁用并给出**原因**。
2. 每个实时数值标注"**目标值**"，不标"实际角度"（PWM 舵机无位置反馈，见 9.2）。
3. 越界关键帧即时回弹 + 红闪 + 说明文字（含 `max_us` 数值）。
4. 危险操作独立成区（danger 描边）+ 输入型/勾选型二次确认。
5. 数值一律 `tabular-nums` 等宽，右对齐。
6. 键盘可达：时间轴擦洗/关键帧移动支持方向键；焦点环不可移除。
7. 支持 `prefers-reduced-motion`（已全局降级）。

**不应（Don't）**
1. 不用 emoji 作功能图标；不混用多套图标库。
2. 不用紫色→粉色渐变 + 发光边框 + 毛玻璃三位一体。
3. 不硬编码颜色（一律 Token 引用）。
4. 不用"Welcome to / 开始使用吧 / 一站式…"等空洞占位文案。
5. 不把"离线"渲染成错误崩溃态——它是**正常且预期**的状态。
6. 不用 1px 边框 + blur≥16 阴影同体（幽灵卡片）。
7. 卡片圆角 ≤14px；不用彩色左边框当卡片强调。

## 8. Responsive & Accessibility（响应式与无障碍）

- 对比度：正文 ≥ 4.5:1（`--fg`/`--muted` 已校验）；指示色（限位条/状态点）≥ 3:1。
- 键盘：全部操作可达；`Esc` 关闭抽屉/弹窗；表格行支持 `Enter` 打开详情。
- 触摸目标 ≥ 44×44px；按钮间距 ≥ 8px。
- 5 态覆盖：Loading / Empty / Error / Populated / Edge（见 §10 各页定义）。
- 状态语义**不单靠颜色**：限位/连接状态同时用"色点 + 文字标签 + 图标"三重编码（色觉障碍可用）。

## 9. Agent Implementation Guide（实现指南）

- 引用 `design/design-tokens.css`（`:root` 全量变量）与 `design/design-tokens.json`（`import tokens from './design-tokens.json'`）。
- 主题切换：`<html data-theme="dark">`（编排器默认）/ 移除该属性 = 浅色（管理页默认）。
- Tailwind 映射建议：`colors.accent → var(--accent)`、`colors.bg → var(--bg)`…（全走 CSS 变量，勿写死 hex）。
- **已知坑**：
  - 时间轴不要用 `setInterval` 累计计时（会越播越慢，见 9.4）；用 `performance.now()` 采样经过时间。
  - 插值曲线固定 `s(u)=3u²−2u³`；校验必须检查**峰值速度**（= 位移/时长 ×1.5），不能只查平均速度（见 9.3）。
  - 播放请求必须携带 `role_id` + 动作版本；第二个播放请求默认拒绝或显式替换（见 13.2）。

---

## 10. 关键界面形态定义

### P1. 编排器主界面（Orchestrator）

- **路由**：`/`（工作台，深色默认）
- **信息层级**：角色/动作上下文（顶栏）→ 四轴轨道与时间轴（中栏，主焦点）→ 关键帧精调（右栏，按需）
- **布局**：三栏工作台（见 §5）。顶栏左=角色选择器（角色名 + 配置版本号）｜中=时间码 + 缩放｜右=**连接状态 chip** + 导入/导出。
- **左栏（260px）**：四轴轨道列表，每行 = 6px 色点 + 轴名（左臂/右臂/头部左右/腰部左右）+ 中位 μs + 当前采样值 + 限位 mini-bar；下方为动作文件列表（`wave.json` / `idle.json` …）+ 导入按钮。
- **中栏（时间轴）**：
  - 时间刻度尺 + 可拖拽**播放指针**（铜橙竖线）。
  - 4 条轴轨道，每条背景叠加**安全区间带**（绿）+ 首尾 10% 琥珀"接近限位"区；关键帧 = 菱形（选中实心，应用缓动曲线改色）。
  - 拖动关键帧越界 → 回弹 + 红闪 + tooltip「超出安全限位 max_us=…」。
  - 下方**音频轨道**：波形 + 可拖拽音频片段 + 同步偏移量显示。
  - 底部播放控制条：播放/停止/循环/时长 + **离线预演**标牌。
- **右栏（300px）**：关键帧属性（时间 ms / 四轴目标 μs / 只读插值曲线缩略 / 峰值速度估算 vs `max_speed_us_s`）；限位面板（min/max/neutral/current + 速度上限）；校验结果（`POST /api/v1/actions/validate` 的 OK/警告/阻断清单）。
- **关键交互**：
  - 拖拽播放指针 / 拖拽关键帧 → 各轴采样值实时刷新（**本地插值计算，离线即可预演**）。
  - **离线预演**：设备未连接时按"播放"进入预演——时间轴照常走、波形照常放、曲线照常采样，**设备不动作**；顶栏 chip 显示"预演模式 · 未连接设备"。"下发给设备"按钮 disabled + 原因 tooltip。
- **状态（5 态）**：Loading=载入动作文件（轨道骨架屏）；Empty=未选择动作 → 引导"新建空白动作 / 导入已有 rolepack"；Error=文件校验失败（列出具体字段，如"时间未递增"）；Populated=正常编辑；Edge=时长 >60s 或关键帧数超限 → 顶部提示 + 阻断导出。
- **接口**：`GET /api/v1/status` · `GET /api/v1/roles` · `PATCH /api/v1/roles/active`（顶栏角色切换）· `GET /api/v1/events`（WebSocket，驱动状态 chip 与播放进度实时刷新）· `POST /api/v1/actions/validate` · `POST /api/v1/actions/play` · `POST /api/v1/actions/stop` · `POST /api/v1/content/import`
- **接口契约**：路径与 `docs/api/openapi.yaml` 一致（base `http://127.0.0.1:8000/api/v1`；全部返回统一信封 `{code,data,message}`；请求至少含 `request_id`）。`POST /api/v1/actions/play` 需附 `role_id` + `action_id` + `action_version`，并携带 `replace_current`（默认 `false` 时若正在播放返回 409）。

### P2. 设备状态面板（Device / Connection）

- **路由**：`/device`（或编排器右栏内的可展开面板）
- **信息层级**：连接状态（横幅）→ 硬件事项 → 四轴实时值 → 电源/温度/故障
- **离线表达（本阶段的重点）**：
  - 顶部**非模态横幅**（不遮挡编辑）：Lucide `WifiOff`/`Usb` + 「未检测到设备 · 界面可离线编辑，动作仅预演不发送」+ 两个动作：`查看连接指引` / `重试检测`。
  - 所有实时数值显示 `—` + 斜纹底（**明确"无数据"≠"0"**）。
  - 只读抽屉列出连接**前置条件**（借用书 8.1/5.2：公仔到位、急停健康、电阻编号与文件一致）→ 让家长/开发者知道"**为什么不能动**"。
  - 离线时危险按钮 disabled + tooltip 说明原因 + 一键跳连接指引。
- **数值呈现（借 Helios/MRUI 范式）**：每轴一条横向 bar gauge，绿(正常 1100–1900μs 等价区间)/琥珀(接近限位)/红(到达限位)，中心 1500μs 中位刻度；**"目标位置"与"实际值"分列标注**（当前阶段实际值恒为 `—`，因无反馈）。
- **状态（5 态）**：Loading=正在检测串口；Empty=未插 USB；Error=串口占用/驱动缺失；Populated=在线含实时值；Edge=故障停机锁定（红，附故障码 + 解除方式）。
- **接口**：`GET /api/v1/status`（含 `mcu_armed / docked / estop_healthy / state / fault / session_generation`）· `GET /api/v1/events`（WebSocket，`CoreEvent` 推送状态迁移、故障、按键、动作进度）

### P3. 记忆管理页（Memories）

- **路由**：`/memories`（管理页，浅色默认，`--container-read` 720px）
- **信息层级**：用途说明（白话）→ 记忆列表 → 单条/批量操作
- **布局**：**列表（非卡片网格）**。列=内容摘要 / 类型（偏好·事实·事件）/ 来源会话时间 / 状态（已确认）/ 操作。顶部一句白话："这些是玩具记住的关于你们的对话。删除后它不会再提起。"
- **关键交互**：单条删除 = 行内二次确认；批量清空 = **弹窗二次确认（勾选"我已了解"）**。不展示原始录音，仅文本（呼应第 15 章隐私）。
- **状态（5 态）**：Empty=「还什么都没记住。当孩子说"记住…"时，会显示在这里。」；Error=读取失败 + 重试；Populated=列表；Edge=记忆 >100 条 → 虚拟滚动 + 搜索/筛选。
- **接口**：`GET /api/v1/memories`（可按 `profile_id` / `kind` 过滤）· `DELETE /api/v1/memories/{id}`（删除单条，id 为路径参数）

### P4. 提醒管理页（Reminders）

- **路由**：`/reminders`（浅色默认）
- **信息层级**：列表（时间 → 内容 → 角色音色 → 状态 → 下次触发）→ 新建
- **布局**：列表 + 右侧/抽屉内新建表单。
- **新建表单（≤4 可见字段，控认知负荷）**：时间（time picker）+ 重复（每天 / 工作日 / 自定义，三选项） + 内容文本 + 角色音色。夜间静音规则自动遵守并以 helper text 说明。
- **关键交互**：启用/暂停用 switch；删除二次确认；重叠提醒给出提示；跨日/断电恢复在列表标注"已恢复"。
- **状态（5 态）**：Empty=引导"设第一个提醒，比如睡前故事"；Error=创建失败（时间冲突/非法）；Populated=列表 + 下次触发倒计时；Edge=提醒 >100 条虚拟滚动。
- **接口**：`GET /api/v1/reminders`（可按 `profile_id` 过滤）· `POST /api/v1/reminders`（创建；时间模糊返回 422 需澄清）· `DELETE /api/v1/reminders/{id}`（取消，id 为路径参数）

### P5. 设置页 / 儿童模式（Settings）

- **路由**：`/settings`（浅色默认，家长视角，正文 16px）
- **分区**：音量与语言 · 网络（配网） · 儿童模式 · 内容审核 · 高级/危险区
- **儿童模式可调项**：夜间时段（不自动动作、不主动开口）· 动作幅度上限（收紧限速/限位）· 允许角色集合 · 出声审核级别。
- **不可关闭项（强制）**：录音时可见指示；静音 = 真正停止采集（非仅停止发送）——以禁用态开关 + 说明呈现。
- **接口**：`GET /api/v1/settings` · `PATCH /api/v1/settings`（密码字段**不回显**，仅回显 SSID；显示"•••• 未修改"）· `GET /api/v1/features`（功能开关灰度，用于控制"高级/危险区"入口可见性）

---

## 11. 儿童模式与家长设置的界面边界

### 需二次确认（double-confirm）的操作
| 操作 | 确认形式 |
|---|---|
| 删除单条记忆 | 行内 `确认删除 / 取消` |
| 清空全部记忆 | 弹窗 + 勾选"我已了解不可恢复" |
| 删除提醒 | 行内确认 |
| 导入角色包（覆盖现有） | 弹窗 + 显示将覆盖的角色 + 校验摘要 |
| 修改配网 / Wi-Fi | 弹窗 + 显示设备名（防误配邻居设备，呼应 14.1） |
| 关闭内容审核 | 弹窗 + 输入确认 |
| **恢复出厂设置** | **独立危险区 + 输入型确认（输入 `RESET`）+ 5s 冷却**（呼应 5.2"放在有再次确认的管理界面") |

### 必须隐藏或加锁的入口
- **设备端的网页/管理入口默认隐藏**；孩子无法从设备侧进入任何管理页。
- **开放局域网时强制登录 + 权限控制**（呼应 7 章 `web/ 受保护的管理页面`、13.1"开放给局域网需要登录"）；写操作加 CSRF 防护。
- **危险区永远置底、独立分组、danger 描边**，不与"保存/应用"主按钮同排相邻。

### 界面层级的表达规则
- 家长页出现**零技术术语**（不说 `min_us`、`CRC`、`CRC 校验`）；技术术语只出现在编排器（开发者面）。
- 主操作（保存/应用）固定右下；危险操作独立区。
- 破坏性按钮统一 `--danger` 描边（非填充），降低误认成"确认"的概率。

---

## 12. 离线可用性总则（硬约束落地）

| 层级 | 能力 | 依赖 |
|---|---|---|
| **L1 完全离线可用** | 编辑/撤销动作、导入导出校验、查看/删除记忆、增删提醒、查看/修改本地设置 | 无 |
| **L2 离线降级可用** | 时间轴播放 / 擦洗 / 音频同步**预演**（本地插值） | 仅前端 |
| **L3 必须连接** | 下发动作、读取实时值、急停、配网状态、OTA | 设备/网络 |

- 统一 **Connection State Chip** 常驻顶栏；L3 动作在离线时 disabled + 原因 tooltip，**绝不静默失败**。
- 离线是**预期状态**而非错误：文案中性（"未检测到设备"），提供"重试检测"与"连接指引"两条出路。

---

## 13. 竞品调研结论（3+ 竞品）

| 竞品 | 借鉴点 | 采纳 |
|---|---|---|
| **Bottango（机器人关键帧编辑器）** | 关节列表 + 时间轴擦洗器 + 自动关键帧 + 音频轨道 + Graph 视图 + Live 模式（真机镜像） | 四轴轨道 + 音频轨道 + 离线预演作为 Live 的降级 |
| **CameraBotics Lensmaster（运动控制）** | 每关键帧 Ease In/Ease Out（秒）、Keyframe Lock、按轴编辑数值、Continue/Stop 行为 | 右栏关键帧属性面板结构 |
| **Lottie Creator / LottieFiles（浏览器动效编辑）** | 菱形关键帧 + 播放指针 + 工作区(Work Area) + Graph Editor 曲线 + 键盘快捷键 + 帧吸附 | 关键帧视觉语言与快捷键；插值曲线缩略 |
| **Helios GCS 舵机诊断面板** | 16 通道 PWM 横向 bar，绿/琥珀/红交规配色 + 1500µs 中位竖线 + 0 值灰显 | **限位显示与设备状态面板的直接范式** |
| **MRUI（模块化机器人 UI）** | "目标 vs 实际"分离、duty/limits/brownout、FSM 状态与阈值告警、模块化 widget | 设备状态面板的信息架构；目标/实际分列 |
| **Loona 配套 App** | Home 仪表盘（电量/状态）+ Settings（勿扰时段/人格）+ 空/异常态处理 | 管理页信息架构与家长向文案语气 |

---

## 14. 三轴刻度与覆盖清单

- `DESIGN_VARIANCE=3`（工具型，规整网格，禁止居中 Hero）· `MOTION_INTENSITY=3`（功能性动效，150ms 收敛）· `VISUAL_DENSITY=7`（编排器驾驶舱；管理页降至 5，见 `pages/*.md` 覆盖）
- 覆盖（均已完成）：Design Token · 9 节规范 · 5 态 · 图标锁定 Lucide · 无障碍 · 响应式 · 儿童边界 · 离线总则

### P0 自检
- [x] 零 emoji 功能图标（图标锁定 Lucide）
- [x] 无紫色→粉色渐变（仅轴4 单个纯色 `#7C3AED`，非渐变）
- [x] 无空洞占位文案（全部为具体产品功能文案）
- [x] 全部颜色走 Token，间距 4px 网格
- [x] 核心界面展示真实产品内容（时间轴/限位/记忆列表/提醒列表），非口号式 Hero

---

## 15. 接口契约对齐（docs/api/openapi.yaml · base `/api/v1`）

> 契约共 14 条路径（16 个方法）。下表逐条给出 UI 归属，保证**每个契约端点都有页面承载**，且设计稿引用的路径**全部存在于契约**。base：`http://127.0.0.1:8000/api/v1`；响应统一信封 `{code,data,message}`。

| 契约路径 | 方法 | UI 归属 |
|---|---|---|
| `/api/v1/status` | GET | 顶栏 Connection State Chip；P2 设备状态面板 |
| `/api/v1/roles` | GET | P1 顶栏角色选择器（角色名 + 配置版本） |
| `/api/v1/roles/active` | PATCH | P1 顶栏角色切换（触发换角色流程） |
| `/api/v1/actions/validate` | POST | P1 右栏校验结果清单 |
| `/api/v1/actions/play` | POST | P1"下发给设备"按钮（离线禁用 + 原因） |
| `/api/v1/actions/stop` | POST | P1 播放控制条 Stop（任意状态最高优先） |
| `/api/v1/content/import` | POST | P1/角色包导入（弹窗二次确认 + 校验摘要） |
| `/api/v1/memories` | GET | P3 记忆列表（按 `profile_id` / `kind` 过滤） |
| `/api/v1/memories/{id}` | DELETE | P3 单条删除 |
| `/api/v1/reminders` | GET | P4 提醒列表 |
| `/api/v1/reminders` | POST | P4 新建提醒（时间模糊 → 422 澄清） |
| `/api/v1/reminders/{id}` | DELETE | P4 取消提醒 |
| `/api/v1/settings` | GET | P5 设置读取 |
| `/api/v1/settings` | PATCH | P5 设置修改（密码不回显） |
| `/api/v1/features` | GET | P5"高级/危险区"入口可见性（灰度） |
| `/api/v1/events` | GET (WebSocket) | 顶栏 chip / P1 播放进度 / P2 事件与故障实时推送 |

- **无未归属端点**：契约 14 路径全部在页面有承载。
- **无越界引用**：设计稿只引用契约内路径，`_verify.py` 双向断言（设计稿 ⊆ 契约 ∧ 写操作端点 ⊇ 页面归属）。
- **离线相关**：`/api/v1/events` 与 `/api/v1/status` 驱动 L3 能力（下发动作/实时值/急停）的可用性；离线时 L3 入口禁用并给原因（见 §12）。
