"""独立复现后端报告的关键项：协议保真（P-1/P-2）与幂等重放取错记录（S-3）。

用真实 C 运行时（msvcrt.sscanf）对照固件 sscanf 语义，而不是靠记忆判断。
运行：python _audit_lead/repro_backend.py
"""
from __future__ import annotations

import ctypes
import sys
import tempfile
from ctypes import byref, c_char, c_char_p, c_int, c_ulong
from pathlib import Path

TOY_ROOT = Path(r"F:/AI智能底座玩具/toy")
sys.path.insert(0, str(TOY_ROOT))

from host.adapters.runtime import build_context  # noqa: E402
from host.config.settings import load_settings  # noqa: E402
from sim.wire import parse_arm, parse_frame  # noqa: E402

SEP = "=" * 76
SCRATCH = Path(tempfile.mkdtemp(prefix="repro-be-", dir=r"F:/AI智能底座玩具/_audit_lead"))

_msvcrt = ctypes.CDLL("msvcrt.dll")
_msvcrt.sscanf.restype = c_int


def firm_arm(line: str) -> int:
    """固件第 55 行：sscanf(line, "ARM %d %c", &role, &extra) 的返回值。"""
    role, extra = c_int(0), c_char(b"\x00")
    return _msvcrt.sscanf(c_char_p(line.encode()), c_char_p(b"ARM %d %c"),
                          byref(role), byref(extra))


def firm_frame(line: str) -> int:
    """固件第 70 行：sscanf(line, "FRAME %lu %d %d %d %d %c", ...) 的返回值。"""
    seq = c_ulong(0)
    a, b, c, d = (c_int(0) for _ in range(4))
    extra = c_char(b"\x00")
    return _msvcrt.sscanf(c_char_p(line.encode()),
                          c_char_p(b"FRAME %lu %d %d %d %d %c"),
                          byref(seq), byref(a), byref(b), byref(c), byref(d), byref(extra))


def p1_arm() -> int:
    print(SEP)
    print("P-1  ARM 行：固件接受 vs 模拟器拒绝（固件判据 == 1）")
    print(SEP)
    mismatches = 0
    for line in ("ARM 1", "ARM1", "ARM 1 ", "ARM 1  ", "ARM 1\t", "ARM   1", "ARM 1 2"):
        rc = firm_arm(line)
        firm_ok = rc == 1
        sim_role = parse_arm(line)
        sim_ok = sim_role is not None
        flag = "" if firm_ok == sim_ok else "   <<< 不一致"
        if firm_ok != sim_ok:
            mismatches += 1
        print(f"  {line!r:12s} 固件 rc={rc} 接受={firm_ok!s:5s} | "
              f"sim 角色={sim_role} 接受={sim_ok!s:5s}{flag}")
    print(f"  → 不一致条数 = {mismatches}")
    return mismatches


def p2_frame() -> int:
    print()
    print(SEP)
    print("P-2  FRAME 行：字段数不足时模拟器是否仍解析成功（固件判据 == 5）")
    print(SEP)
    mismatches = 0
    for line in (
        "FRAME 0 1500 1550 1500 1500",     # 合法
        "FRAME 0 1500 1550 1500",          # 只有 4 个数
        "FRAME 0 1500 1550",               # 只有 3 个数
        "FRAME 0 1500 1550 1500 1500 1500",  # 6 个数
    ):
        rc = firm_frame(line)
        firm_ok = rc == 5
        parsed = parse_frame(line)
        sim_ok = parsed is not None
        flag = "" if firm_ok == sim_ok else "   <<< 不一致"
        if firm_ok != sim_ok:
            mismatches += 1
        print(f"  {line!r:38s} 固件 rc={rc} 接受={firm_ok!s:5s} | "
              f"sim={parsed} 接受={sim_ok!s:5s}{flag}")
    print(f"  → 不一致条数 = {mismatches}")
    return mismatches


def s3_idempotency() -> bool:
    print()
    print(SEP)
    print("S-3  幂等重放是否取回同一条提醒")
    print(SEP)
    settings = load_settings({"DB_PATH": str(SCRATCH / "s3.sqlite3")})
    ctx = build_context(settings, simulate=True, root=TOY_ROOT)
    try:
        base = {"text": "A-该睡觉了", "due_at_utc": "2030-01-01T00:00:00Z", "profile_id": "default"}
        a = ctx.create_reminder({**base, "idempotency_key": "KEY-A"})
        b = ctx.create_reminder({**{**base, "text": "B-该喝水了"}, "idempotency_key": "KEY-B"})
        replay = ctx.create_reminder({**base, "idempotency_key": "KEY-A"})
        print(f"  首次创建 A       : id={a['id']} text={a['text']!r}")
        print(f"  首次创建 B       : id={b['id']} text={b['text']!r}")
        print(f"  重放 KEY-A（期望 A）: id={replay['id']} text={replay['text']!r}")
        wrong = replay["id"] != a["id"]
        print(f"  → 取回的不是同一条 : {wrong}"
              f"   {'← 幂等重放返回了另一条记录（S-3 复现）' if wrong else 'OK'}")
        return wrong
    finally:
        ctx.close()


def main() -> int:
    m1 = p1_arm()
    m2 = p2_frame()
    s3 = s3_idempotency()
    print()
    print(SEP)
    print("结论")
    print(SEP)
    print(f"  P-1 ARM  不一致 {m1} 条  → {'复现（确定缺陷）' if m1 else '未复现'}")
    print(f"  P-2 FRAME 不一致 {m2} 条  → {'复现（确定缺陷）' if m2 else '未复现'}")
    print(f"  S-3 幂等重放取错记录 : {'复现（确定缺陷）' if s3 else '未复现'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
