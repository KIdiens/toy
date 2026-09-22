"""录制 / 回放（JSONL）与黄金比对。

行格式：``{"t_ms": int, "dir": "host"|"mcu", "line": str}``；``t_ms`` 为相对会话
起点的毫秒。``python -m sim --replay <file> --diff`` 即用本格式做逐行黄金比对。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

HOST = "host"
MCU = "mcu"


@dataclass(frozen=True, slots=True)
class ReplayRow:
    t_ms: int
    direction: str
    line: str

    def to_dict(self) -> dict:
        return {"t_ms": self.t_ms, "dir": self.direction, "line": self.line}

    @classmethod
    def from_dict(cls, data: dict) -> ReplayRow:
        return cls(int(data["t_ms"]), str(data["dir"]), str(data["line"]))


def load_jsonl(path: str | Path) -> list[ReplayRow]:
    rows: list[ReplayRow] = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        if raw.strip():
            rows.append(ReplayRow.from_dict(json.loads(raw)))
    return rows


def write_jsonl(path: str | Path, rows: list[ReplayRow]) -> None:
    text = "\n".join(json.dumps(row.to_dict(), ensure_ascii=False) for row in rows)
    Path(path).write_text(text + ("\n" if text else ""), encoding="utf-8")


def compare(expected: list[str], produced: list[str]) -> list[tuple[int, str, str]]:
    """逐行比对；返回 ``(下标, 期望, 实际)`` 列表（空列表 = 完全一致）。"""
    diffs: list[tuple[int, str, str]] = []
    for index in range(max(len(expected), len(produced))):
        exp = expected[index] if index < len(expected) else "<缺失>"
        got = produced[index] if index < len(produced) else "<缺失>"
        if exp != got:
            diffs.append((index, exp, got))
    return diffs


class ReplayRecorder:
    """把主机↔MCU 行按时间戳落成 JSONL。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._rows: list[ReplayRow] = []

    def record(self, t_ms: int, direction: str, line: str) -> None:
        if direction not in (HOST, MCU):
            raise ValueError(f"方向只能是 {HOST!r} 或 {MCU!r}")
        self._rows.append(ReplayRow(int(t_ms), direction, line))

    def flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_jsonl(self.path, self._rows)

    @property
    def rows(self) -> list[ReplayRow]:
        return list(self._rows)


class ReplayLink:
    """按虚拟时间回放录制中的 MCU 行；同时记录本次主机行以便黄金比对。"""

    def __init__(self, rows: list[ReplayRow], *, clock, start_ms: int | None = None) -> None:
        self.clock = clock
        self._mcu = [r for r in rows if r.direction == MCU]
        self._index = 0
        self._start_ms = int(start_ms if start_ms is not None else clock.now_ms())
        self.host_rows: list[ReplayRow] = []

    def open(self) -> None:  # noqa: D401 - 与 MotionLink 契约同名
        pass

    def close(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        # 丢弃到当前时刻为止、尚未被消费的 MCU 行
        now = self._relative_now()
        while self._index < len(self._mcu) and self._mcu[self._index].t_ms <= now:
            self._index += 1

    def write_line(self, line: str) -> None:
        self.host_rows.append(ReplayRow(self._relative_now(), HOST, line))

    def read_lines(self) -> list[str]:
        now = self._relative_now()
        lines: list[str] = []
        while self._index < len(self._mcu) and self._mcu[self._index].t_ms <= now:
            lines.append(self._mcu[self._index].line)
            self._index += 1
        return lines

    def _relative_now(self) -> int:
        return max(0, self.clock.now_ms() - self._start_ms)

    @property
    def produced_mcu_lines(self) -> list[str]:
        return [row.line for row in self._mcu[: self._index]]
