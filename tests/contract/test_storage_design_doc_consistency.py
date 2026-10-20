"""存储设计文档与实现的数值一致性护栏（第 8 组）。

缺陷本体：第 7 组（``e462c1b``）把 ``host/storage/database.py`` 的
``BUSY_TIMEOUT_MS`` 从 **5000 降到 500**（撞锁快速失败，避免每个撞锁写请求
独占一个 DB 连接 5 秒），但 ``docs/architecture/40-storage-design.md:86/103``
仍写 5000 —— **代码与「Accepted」级设计文档直接矛盾**。

为什么必须钉住：那份文档不是随手笔记，它自己声明「**签名一律定死，实现照抄；
签名变更需回写本文档**」。而测试基线里的 ``tests/contract/`` 正是为「契约护栏」
存在的。既然「文档写错→ 实现照抄」这条约定已经被违反过一次，就需要一个
**机器可检**的绑定，否则下次改常量又会漂移，且漂移不会被任何人发现。

**判别力**：把 ``database.py`` 的常量改成 5000（或文档改成 5000）→ 本文件转红。
另有 ``test_the_connection_really_applies_the_documented_busy_timeout`` 兜住
「常量/文档都对、但连接上生效的 PRAGMA 是别的数」这一空洞（实测原 3 条全绿）。

**只锁``BUSY_TIMEOUT_MS``**：它是本轮唯一被用户授权同步的数值。
``MAX_*``/超时类数字的核对结论只写进报告，不在本文件断言—— 未经确认不得
顺手改文档（见交付报告「任务 2」一节）。
"""
from __future__ import annotations

import re

from host.storage.database import BUSY_TIMEOUT_MS, Database

from tests.support import TOY_ROOT

DESIGN_DOC = TOY_ROOT / "docs" / "architecture" / "40-storage-design.md"


def _doc_text() -> str:
    assert DESIGN_DOC.is_file(), f"设计文档不存在: {DESIGN_DOC}"
    return DESIGN_DOC.read_text(encoding="utf-8")


def test_busy_timeout_constant_matches_design_doc() -> None:
    """``database.py`` 的 ``BUSY_TIMEOUT_MS`` 必须等于文档 §3.1 里写的值。

    读的是**代码里的真实常量**（不是重新硬编码一个期望值），所以它真的是
    「文档 vs 实现」的比较；把任一侧改成别的数字都会转红。
    """
    text = _doc_text()
    match = re.search(r"^BUSY_TIMEOUT_MS:\s*Final\[int\]\s*=\s*(\d+)\s*$",
                      text, re.MULTILINE)
    assert match is not None, (
        "设计文档 §3.1 里找不到 'BUSY_TIMEOUT_MS: Final[int] = <数字>' 形式的声明，"
        "该行可能被改写或删除—— 数值一致性就此失去锚点"
    )
    documented = int(match.group(1))
    assert documented == BUSY_TIMEOUT_MS, (
        f"文档写BUSY_TIMEOUT_MS={documented}，实现是 {BUSY_TIMEOUT_MS}。"
        "该文档自称『签名一律定死，签名变更需回写本文档』，漂移必须修文档或修实现，"
        "不能放着"
    )


def test_busy_timeout_pragma_line_matches_design_doc() -> None:
    """``PRAGMA busy_timeout = N`` 那一步也必须与常量一致（文档里写死了两处）。

    文档 §3.1 的执行顺序清单里第3 步写的是字面量，若只改了上面那处声明、
    忘了这里，两行就会自相矛盾 —— 而这种矛盾光看文档很难发现。
    """
    text = _doc_text()
    found = re.findall(r"^\s*\d+\.\s*`PRAGMA busy_timeout = (\d+)`\s*$",
                       text, re.MULTILINE)
    assert found, "设计文档 §3.1 的 PRAGMA 执行顺序清单里找不到 busy_timeout 那一步"
    assert set(found) == {str(BUSY_TIMEOUT_MS)}, (
        f"文档执行顺序清单里的 busy_timeout 有多个不一致的值: {found}，"
        f"实现是 {BUSY_TIMEOUT_MS}"
    )


def _effective_busy_timeout_ms(tmp_path) -> int:
    """真开一条 ``Database`` 连接，读回**生效的** ``PRAGMA busy_timeout``。"""
    database = Database(tmp_path / "pragma-probe.sqlite3")
    try:
        row = database.connect().execute("PRAGMA busy_timeout").fetchone()
    finally:
        database.close_all()
    assert row is not None, "PRAGMA busy_timeout 没有返回值"
    return int(row[0])


def test_the_connection_really_applies_the_documented_busy_timeout(tmp_path) -> None:
    """断言连接上**真正生效**的 busy_timeout，而不是只看常量与文档是否对齐。

    改前的三条用例全部在「文档文本 ↔ ``BUSY_TIMEOUT_MS`` 常量」之间比对。
    这留下一个空洞：常量、文档、测试三方可以完全自洽，而连接上的 PRAGMA 另有
    来源。实测（反向验证）：把 ``database.py`` 的
    ``f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}"`` 硬编码成
    ``"PRAGMA busy_timeout = 5000"``（常量仍是 500），本文件 3 条**全绿** ——
    整个「快速失败」的承诺其实已经不成立了，却无人报警。

    这里读的是 sqlite 自己记下的值（``PRAGMA busy_timeout`` 的读回值），
    所以常量、文档、生效值三者被真正串成一条链。
    """
    effective = _effective_busy_timeout_ms(tmp_path)
    assert effective > 0, f"busy_timeout 被关成了 {effective}：并发写的正常重叠会立刻失败"
    assert effective == BUSY_TIMEOUT_MS, (
        f"连接上生效的 busy_timeout 是 {effective}ms，实现常量声明的是 "
        f"{BUSY_TIMEOUT_MS}ms —— PRAGMA 那一行与常量脱钩了（文档写的是同一个数，"
        "所以「文档 ↔ 常量」比得再勤也发现不了）"
    )


def test_busy_timeout_rationale_documents_the_fail_fast_semantics() -> None:
    """文档必须写明「快速失败」的新语义：503 + Retry-After + code=50301。

    只同步数字是不够的：5000 -> 500 改变的是**对外行为**（撞锁从「等5 秒」变成
    「立刻503」）。若文档只留一个孤零零的 500，读者无从判断这是有意为之还是
    手滑，也无从知道调用方该期待什么状态码。
    """
    text = _doc_text()
    for needle, what in (("503", "状态码"), ("Retry-After", "退避头"),
                         ("50301", "信封 code")):
        assert needle in text, f"设计文档未提及{what}（{needle}），第 7 组的新语义没被同步"
