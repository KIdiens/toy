# ADR-007: 前端使用原生 ES 模块 + Web Components，无构建步骤

## Status
Accepted (2026-09-21)

## Background
管理界面包含状态页与动作编排页（开发书 §13.1：四轴轨道、时间轴、播放指针、关键帧编辑、音频轨道、角色选择、限位显示、离线预演、停止、导入导出）。部署端是树莓派 4B，希望不引入 Node 构建工具链；开发书允许「普通前端组件或 React」，并要求同源。

## Decision
**原生 ES 模块 + Web Components（Custom Elements/Shadow DOM），无 Node 构建步骤**。资源以静态文件由 FastAPI `StaticFiles` 与 API 同源托管（`host/web/static/`）。交互组件（时间轴、关键帧编辑器）用原生 DOM + Canvas/SVG 实现，状态同步走 `fetch` + WebSocket。

依据：部署端零 Node 工具链；同源免跨域；编辑器为单页 + 时间轴，组件量可控；无构建即无产物同步问题。

排除：React 18 + Vite、Vue 3 + Vite——均需 Node 与构建产物，MVP 收益（组件化/生态）不足以抵消部署复杂度。

**升级触发条件**：组件数 > 25、或需要复杂表单/状态管理时，整体替换为 React 18 + Vite，**不动 API 契约**（ADR-006 的 `openapi.yaml` 为前后端唯一依据，前端可独立重写）。

与设计的边界：设计交付**视觉与交互规格**，不绑定实现框架；令牌与图标库见 ADR-009/ADR-012。

## Consequences
- 正面：零构建、零 Node 依赖、同源、离线可用；部署简单（拷静态文件即可）。
- 负面：缺少组件生态，复杂交互需自研；需自行处理可访问性与状态管理。
- 约束：静态资源不得写死颜色（走令牌）；图标统一用 Lucide（ADR-009）。

## Related ADRs
ADR-006（同源托管）、ADR-009（图标库）、ADR-012（设计令牌）。
