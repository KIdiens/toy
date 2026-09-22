"""MCU 模拟器 CLI。

用法::

    python -m sim --tcp 5555 --role 1 --docked --estop-ok
    python -m sim --scenario watchdog_timeout
    python -m sim --list-scenarios
    python -m sim --record-dir tests/fixtures/sessions     # 生成黄金回放夹具
    python -m sim --replay tests/fixtures/sessions/x.jsonl --diff

``--tcp`` 常驻服务；``--scenario`` 跑内置或文件场景并打印逐行时间轴；
``--record-dir`` 把全部内置场景录成会话 JSONL；``--replay`` 重放录制做黄金比对。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sim.scenario import BUILTIN_SCENARIOS, Scenario
from sim.session import Session, replay
from sim.tcp_bridge import TcpBridge, make_controller


def _print_transcript(transcript: list[dict]) -> None:
    for row in transcript:
        arrow = "host>" if row["dir"] == "host" else "mcu >"
        print(f"{row['t_ms']:>7} ms  {arrow} {row['line']}")


def _run_scenario(args) -> int:
    if args.scenario in BUILTIN_SCENARIOS:
        scenario = BUILTIN_SCENARIOS[args.scenario]
    else:
        path = Path(args.scenario)
        if not path.is_file():
            print(f"未找到场景: {args.scenario}", file=sys.stderr)
            return 2
        scenario = Scenario.load(path)
    controller, clock = scenario.build_controller()
    _print_transcript(scenario.run(controller, clock))
    return 0


def _run_replay(args) -> int:
    """回放一个或多个录制；``--diff`` 时要求全部逐行一致才返回 0。"""
    failures = 0
    for raw in args.replay:
        path = Path(raw)
        if not path.is_file():
            print(f"未找到录制: {raw}", file=sys.stderr)
            return 2
        session = Session.load(path)
        produced = replay(session)
        if not args.diff:
            for t_ms, line in produced:
                print(f"{t_ms:>7} ms  mcu > {line}")
            continue
        expected = [(int(row["t_ms"]), row["line"]) for row in session.expected]
        mismatches = _diff(expected, produced)
        if mismatches:
            for index, exp, got in mismatches:
                print(f"DIFF @{index} [{path.name}]: expected={exp!r} actual={got!r}")
            print(f"RESULT: FAIL（{path.name}：{len(mismatches)} 行不一致）")
            failures += 1
        else:
            print(f"RESULT: PASS（{path.name}：{len(expected)} 行逐行一致，含时间戳）")
    return 1 if failures else 0


def _diff(expected: list, produced: list) -> list[tuple[int, object, object]]:
    out: list[tuple[int, object, object]] = []
    for index in range(max(len(expected), len(produced))):
        exp = expected[index] if index < len(expected) else "<缺失>"
        got = produced[index] if index < len(produced) else "<缺失>"
        if exp != got:
            out.append((index, exp, got))
    return out


def _record_all(directory: str) -> int:
    """把全部内置场景录制成会话 JSONL（黄金回放夹具的可复现生成入口）。"""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    for name in sorted(BUILTIN_SCENARIOS):
        session = Session.from_scenario(BUILTIN_SCENARIOS[name])
        session.save(target / f"{name}.jsonl")
        print(f"已录制 {name}（{len(session.expected)} 行 MCU 输出）")
    return 0


def _serve_tcp(args) -> int:
    def factory(clock):
        return make_controller(
            clock,
            pwm_ready=not args.pwm_init_fail,
            role=args.role,
            docked=not args.undocked,
            estop_ok=not args.estop,
        )

    bridge = TcpBridge(host=args.host, port=args.tcp, controller_factory=factory)
    print(f"MCU 模拟器监听 {args.host}:{args.tcp}（role={args.role}）")
    try:
        bridge.serve_forever()
    except KeyboardInterrupt:
        print("已停止")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sim", description="MCU 模拟器（复刻 ESP32 固件）")
    parser.add_argument("--tcp", type=int, metavar="PORT", help="以 TCP 服务端运行")
    parser.add_argument("--host", default="127.0.0.1", help="TCP 绑定地址（默认回环）")
    parser.add_argument("--role", type=int, default=1, choices=(1, 2, 3), help="公仔角色")
    parser.add_argument("--undocked", action="store_true", help="启动即未到位")
    parser.add_argument("--estop", action="store_true", help="启动即急停按下")
    parser.add_argument("--pwm-init-fail", action="store_true", help="模拟 PWM 初始化失败")
    parser.add_argument("--scenario", metavar="ID|FILE", help="运行内置场景或场景文件")
    parser.add_argument("--list-scenarios", action="store_true", help="列出内置场景")
    parser.add_argument("--replay", metavar="FILE", nargs="+", help="回放一个或多个 JSONL 录制")
    parser.add_argument("--diff", action="store_true", help="回放时做黄金比对")
    parser.add_argument("--record-dir", metavar="DIR", help="把全部内置场景录制到目录")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        for name in sorted(BUILTIN_SCENARIOS):
            print(name)
        return 0
    if args.record_dir:
        return _record_all(args.record_dir)
    if args.scenario:
        return _run_scenario(args)
    if args.replay:
        return _run_replay(args)
    if args.tcp:
        return _serve_tcp(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
