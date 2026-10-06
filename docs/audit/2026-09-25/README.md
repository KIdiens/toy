# 2026-09-25 审计留档

>归档时间：2026-10-07
> 来源：`F:/AI智能底座玩具/_audit_*/` 与 `_verify_p1/`（仓库外的临时目录，已清理）
> 基线commit：`7ef6a6e`

这些是第三批整改之前的一次全面复查的原始产物。**留着是因为它们能证伪**——
第四批整改时若要复查某条结论，直接复跑探针比重新写快得多。

## 报告（5 份）

| 文件 | 来源 | 内容 |
|---|---|---|
| `AUDIT-BACKEND.md` | `_audit_backend/AUDIT_REPORT.md` | 协议一致性（固件↔ 模拟器）/ 角色包安全 / 存储契约 |
| `REPORT-QA3.md` | `_audit_qa3/REPORT.md` | 实测层：并发 / 资源 / 崩溃恢复 / 视觉门禁。**verdict = fail** |
| `REPORT-VERIFY-P1.md` | `_verify_p1/REPORT.md` | P0-1 按键对话闭环独立复验 |
| `REPORT-VERIFY-P1-2.md` | `_verify_p1/REPORT2.md` | P1 第二批复验 |
| `REPORT-VERIFY-P1-3.md` | `_verify_p1/REPORT3.md` | P1 第三批复验 |

## 探针（`probes/`，67 个文件）

按来源分目录，`.py` 与 `.out` / `.txt` 一一对应（`.out` 是原始输出，`.py` 可复跑）。

| 目录 | 数量 | 用途 |
|---|---|---|
| `probes/backend/` | 11 | 用真实 C 运行时 `sscanf` 对照 `sim.wire` 解析；穷举畸形 FRAME 行判 sim 是否比固件更宽松；角色包成员名归一化冲突/ADS |
| `probes/qa3/` | 41 | **真实进程 + 真实压力 + 真实异常注入**。按键风暴、播放竞态、长时运行、重启 20 次、存储锁、优雅退出 |
| `probes/frontend/` | 7 | 浏览器 DOM 快照 + 列表接口探测 + 自检输出 |
| `probes/lead/` | 7 | 架构/后端/d1c 复现脚本 |
| `probes/root/` | 1 | `_p0_probe.py` —— 不复用 `tests/` 任何断言，自带起停与判定的独立 P0 探针 |

## 复跑方式

探针路径依赖原目录（`F:/AI智能底座玩具/_audit_*`），已随目录清理失效。
要复跑时把对应子目录的内容拷回工程根的 `_audit_<name>/`，或直接改脚本里的路径常量。

关键依赖：`tests/support.py:14` 指向的冻结基线
`../AI角色玩具开发资料包/software/motion.py` 在**仓库外**。
不复制它，503 个 `test_motion_parity` 会 **skip 而不是 fail**——
拿 skip 数当通过的结论都得先看 skip 原因。

## 注意

`REPORT-QA3.md` 的 verdict 是 **fail**，其中 `[D1]`（表演键使设备永久卡
`PERFORMING`）属于已在后续批次修复的项。**报告反映的是基线 `7ef6a6e` 的状态，
不代表当前 HEAD。**
