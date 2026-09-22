"""本地内容库：扫描/索引本地音频资产，并提供响度与峰值检查。

- 内容资产放 ``content/``（内容资产），与「记忆」分开治理（§7.4）；不把音频路径写进记忆。
- 峰值/响度检查用于导入与播放前的「太响/削顶」体检，避免伤耳；阈值保守，判定可复现。
- 只做读写与度量，不含业务规则；业务侧经 ``host/tools/content.py`` 调用。
"""
from __future__ import annotations

import array
import math
from dataclasses import dataclass
from pathlib import Path

CONTENT_KINDS: tuple[str, ...] = ("story", "sfx")
PEAK_LIMIT_DBFS = -1.0
FULL_SCALE = 32768.0
SILENT_DBFS = -99.0


@dataclass(frozen=True, slots=True)
class ContentItem:
    id: str
    title: str
    path: str
    kind: str
    duration_s: float


@dataclass(frozen=True, slots=True)
class PeakReport:
    peak_dbfs: float
    rms_dbfs: float
    clipping: bool
    ok: bool
    reason: str = ""


def _to_dbfs(amplitude: float) -> float:
    if amplitude <= 0:
        return SILENT_DBFS
    return 20.0 * math.log10(amplitude / FULL_SCALE)


def analyze_pcm16(pcm: bytes, *, channels: int = 1) -> PeakReport:
    """对 16-bit PCM 计算峰值/有效值 dBFS 与削顶标志。"""
    usable = len(pcm) - (len(pcm) % 2)
    samples = array.array("h")
    samples.frombytes(pcm[:usable])
    if not samples:
        return PeakReport(SILENT_DBFS, SILENT_DBFS, False, False, "空音频")
    peak = max(abs(s) for s in samples)
    rms = (sum(int(s) * int(s) for s in samples) / len(samples)) ** 0.5
    clipping = peak >= 32700
    peak_dbfs = _to_dbfs(float(peak))
    ok = (not clipping) and peak_dbfs <= PEAK_LIMIT_DBFS
    reason = "" if ok else f"峰值 {peak_dbfs:.1f} dBFS 过高或存在削顶"
    return PeakReport(peak_dbfs, _to_dbfs(float(rms)), clipping, ok, reason)


def read_wav_pcm(path: str | Path) -> tuple[bytes, int, int]:
    """读取 16-bit PCM WAV，返回 ``(pcm, sample_rate, channels)``；非 16-bit PCM 报错。"""
    import wave  # noqa: PLC0415

    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2:
            raise ValueError("仅支持 16-bit PCM WAV")
        return wav.readframes(wav.getnframes()), wav.getframerate(), wav.getnchannels()


def wav_duration_s(path: str | Path) -> float:
    import wave  # noqa: PLC0415

    with wave.open(str(path), "rb") as wav:
        rate = wav.getframerate() or 1
        return wav.getnframes() / rate


class ContentCatalog:
    """扫描 ``root`` 下的本地音频内容（按 ``kind`` 子目录归类）。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _scan_dir(self, directory: Path, kind: str) -> list[ContentItem]:
        if not directory.is_dir():
            return []
        items: list[ContentItem] = []
        for path in sorted(directory.glob("*.wav")):
            items.append(ContentItem(
                id=f"{kind}:{path.stem}",
                title=path.stem,
                path=str(path),
                kind=kind,
                duration_s=wav_duration_s(path),
            ))
        return items

    def list(self, kind: str | None = None) -> list[ContentItem]:
        kinds = (kind,) if kind else CONTENT_KINDS
        items: list[ContentItem] = []
        for k in kinds:
            if k not in CONTENT_KINDS:
                raise ValueError(f"未知内容类型: {k}")
            items.extend(self._scan_dir(self.root / k, k))
        return items

    def get(self, item_id: str) -> ContentItem | None:
        return next((item for item in self.list() if item.id == item_id), None)

    def analyze(self, item_id: str) -> PeakReport:
        item = self.get(item_id)
        if item is None:
            raise ValueError(f"内容不存在: {item_id}")
        pcm, _rate, channels = read_wav_pcm(item.path)
        return analyze_pcm16(pcm, channels=channels)
