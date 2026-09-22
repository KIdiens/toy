# ADR-009: 锁定 Lucide 作为全项目唯一图标库

## Status
Accepted (2026-09-21)

## Background
团队级 P0 规则：**禁止 emoji 作为功能图标**；Spec 必须锁定**一套** SVG 图标库，前端依赖随之一并锁定，全项目统一不混用。管理界面需要状态/操作/故障类图标（播放、停止、保存、导入、导出、音量、麦克风、警告、故障、角色、时间轴等）。

## Decision
**锁定 Lucide（ISC 授权，版本 `1.x`）为全项目唯一图标库**，任何界面与文档示意不得混用第二套图标，不得用 emoji/字符画充当功能图标。

落地方式（离线优先）：
- 主用 `host/web/static/vendor/lucide.min.js`（vanilla，`createIcons()` 扫描 `data-lucide="<name>"`），随仓库 vendoring。
- 若需按需精简，改用 `host/web/static/icons/*.svg`（Lucide 裸 SVG）内联。
- 图标尺寸只用令牌 `--icon-size`；颜色用 `currentColor` 继承文本色令牌（ADR-012）。

选型对比：Tabler Icons（MIT，5900+，风格更细，量大易混用）；Material Symbols（Apache-2.0，但官方形态以可变字体为主，需另拉 SVG 包，且字体方案与「纯 SVG」要求不完全对齐）。选 Lucide 的理由：ISC 宽松（商业可用，仅需保留版权声明）、1600+ 图标、严格 24px/2px 描边栅格保证一致性、同时提供 vanilla 与 React 包（未来若切框架不换图标库）、不含品牌 Logo（规避商标风险）。

## Consequences
- 正面：单一视觉语言；授权干净；框架无关（vanilla 与 React 皆有）；离线可用。
- 负面：需在 vendoring 时记录 Lucide 版本与 LICENSE 文件；升级图标库需整体回归视觉。
- 门禁：前端代码与文档中 `grep` emoji 与第二图标库 CDN 链接，命中即不合格。

## Related ADRs
ADR-007（前端方案）、ADR-012（设计令牌与颜色）。
