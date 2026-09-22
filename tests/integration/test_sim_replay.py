"""黄金回放回归（Spec §12 步骤 5）：录制 → 回放 → 逐行比对必须可复现。

守护 QA B1 修复后的三条保证：初态可复现、时间推进到最后一步、RX 排空。
既跑 CLI 真子进程（``python -m sim --replay <file> --diff``），也做进程内往返比对。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from sim.scenario import BUILTIN_SCENARIOS
from sim.session import Session, replay

from tests.support import TOY_ROOT

SESSIONS = TOY_ROOT / "tests" / "fixtures" / "sessions"
SESSION_FILES = sorted(SESSIONS.glob("*.jsonl"))


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "sim", *args],
        cwd=str(TOY_ROOT), capture_output=True, text=True, timeout=60,
    )


def test_fixture_dir_covers_every_builtin_scenario() -> None:
    recorded = {path.stem for path in SESSION_FILES}
    missing = sorted(set(BUILTIN_SCENARIOS) - recorded)
    extra = sorted(recorded - set(BUILTIN_SCENARIOS))
    assert recorded == set(BUILTIN_SCENARIOS), f"缺失: {missing}；多余: {extra}"


@pytest.mark.parametrize("path", SESSION_FILES, ids=lambda item: item.stem)
def test_cli_replay_diff_passes(path: Path) -> None:
    result = _run_cli("--replay", str(path), "--diff")
    assert result.returncode == 0, f"{path.name} 回放不一致:\n{result.stdout}\n{result.stderr}"
    assert "RESULT: PASS" in result.stdout


def test_cli_accepts_the_whole_glob_at_once() -> None:
    """`python -m sim --replay tests/fixtures/sessions/*.jsonl --diff` 必须一次全绿。"""
    result = _run_cli("--replay", *[str(path) for path in SESSION_FILES], "--diff")
    assert result.returncode == 0, result.stdout
    assert result.stdout.count("RESULT: PASS") == len(SESSION_FILES)


@pytest.mark.parametrize("scenario_id", sorted(BUILTIN_SCENARIOS))
def test_in_process_round_trip_matches_golden(scenario_id: str) -> None:
    session = Session.from_scenario(BUILTIN_SCENARIOS[scenario_id])
    golden = [(int(row["t_ms"]), row["line"]) for row in session.expected]
    assert replay(session) == golden


@pytest.mark.parametrize("scenario_id", sorted(BUILTIN_SCENARIOS))
def test_committed_fixture_matches_current_scenario(scenario_id: str) -> None:
    """夹具不得漂移：磁盘上的录制必须等于用当前场景重新录制的结果。"""
    path = SESSIONS / f"{scenario_id}.jsonl"
    assert path.is_file(), path
    regenerated = Session.from_scenario(BUILTIN_SCENARIOS[scenario_id])
    assert path.read_text(encoding="utf-8") == "\n".join(regenerated.to_lines()) + "\n"


def test_time_driven_output_beyond_last_host_line_is_replayed() -> None:
    """看门狗 FAULT 出现在最后一条 host 行之后，回放必须仍能重建（B1 根因 2）。"""
    session = Session.load(SESSIONS / "watchdog_timeout.jsonl")
    last_host_ms = max(int(row["t_ms"]) for row in session.events if row["dir"] == "host")
    fault_times = [int(row["t_ms"]) for row in session.expected
                   if row["line"].startswith("FAULT")]
    assert fault_times and fault_times[0] > last_host_ms
    assert any(line.startswith("FAULT") for _t, line in replay(session))


def test_initial_state_is_reproduced_not_defaulted() -> None:
    """非默认物理态必须由录制重建：dock 打开的场景回放仍报 INTERLOCK。"""
    session = Session.from_scenario(BUILTIN_SCENARIOS["interlock_dock_open"])
    lines = [line for _t, line in replay(session)]
    assert "ERR INTERLOCK" in lines
    assert not any(line.startswith("OK ARM") for line in lines)


def test_diff_detects_corrupted_recording(tmp_path: Path) -> None:
    rows = []
    for text in (SESSIONS / "watchdog_timeout.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(text)
        if row.get("dir") == "mcu" and str(row.get("line", "")).startswith("FAULT"):
            row["line"] = "FAULT TAMPERED"
        rows.append(json.dumps(row, ensure_ascii=False))
    target = tmp_path / "tampered.jsonl"
    target.write_text("\n".join(rows) + "\n", encoding="utf-8")
    result = _run_cli("--replay", str(target), "--diff")
    assert result.returncode == 1, result.stdout
    assert "RESULT: FAIL" in result.stdout
