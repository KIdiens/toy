# 2026-10-07 各批次整改留档

> 与 `../2026-09-25/` 分开：那批是**已归档的历史复查**（探针原样保留，
> 与当初跑出结论时字节一致，所以不参与 lint）；这批是**当批整改的复现探针**
>（受 `ruff check .` 全仓检查）。

## 第 4 组· 契约缺陷（commit `38eb329`）

`probes/g4/` 下 3 个可重跑探针：

| 探针 | 用途 |
|---|---|
| `repro_g4.py` | 复现两个缺陷：真实 HTTP 响应里的 `due_at_utc`、`sample_rate=0` 链路 |
| `probe_recorder_fix.py` | 对比 3 种修法（现状 / `or 1` / 不推导），含合法输入对照 |
| `probe_fix_choice.py` | 补测 `or 1` 修法为什么是假修 |

### 缺陷 1：`due_at_utc` 的 `or ""` 违反 `format: date-time`

`reminders_repo.Reminder.due_at_utc` 是 `str | None`（DDL 该列可空），
`openapi.yaml` 声明 `format: date-time`，而 `dto.py` 曾用 `or ""` 把None
洗成空串 —— **空串不是合法 RFC 3339 date-time**。

修法三处同改（少任何一处都会 500）：DTO 保留 `None`、
`ReminderOut.due_at_utc` 改 `str | None = None`、openapi 响应侧标
`nullable: true`（本项目是 OpenAPI 3.0.3）。**请求侧仍保持
`minLength: 1` 必填** —— 新建提醒必须给时间，但历史数据的读取要能表达「没有」。

### 缺陷 2：`recorder.py` 的采样率零除

`Recording` 是 dataclass 无校验，`sample_rate=0` 构造得出来 → 除零。

**而且比除零更隐蔽的问题**：`keep = int(limit * 0) * 2 * channels == 0`，
时长已知时超限会把整段 PCM 砍成 **0 字节**。

修法不是 `or 1`（实测证伪：`or 1` 会算出 800s伪时长，把 1600B 截到 30B，
`has_speech` 还翻成 `True` —— 那是**更坏**的静默数据损坏），
而是「采样率非正时不推导时长、不做截断」。

**可触发性**：`Recording` 构造得出来、异常可复现；但两个在库后端都产不出 0
（默认 16000，且它们自己就在 `duration_s = frames / self.sample_rate` 先崩）。
触发前提是自定义后端，而 `AudioBackend` 是官方扩展点，故按真实缺陷处理。

## 复跑

```bash
cd F:/AI智能底座玩具/toy
python docs/audit/2026-10-07-batches/probes/g4/repro_g4.py
```

探针会自建 tmp 目录，不碰仓库。
