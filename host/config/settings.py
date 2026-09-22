"""唯一配置加载点：环境变量覆盖 ``defaults.toml``。

纪律：
- 密钥（``DASHSCOPE_API_KEY``）只从环境变量读取，**永不落盘、永不进日志**。
- ``Settings.redacted()`` 供日志/诊断使用，保证 ``api_key`` 不回显。
- 未设置 ``DASHSCOPE_API_KEY`` 时 ``ai.provider`` 必须为桩后端（AC-17）。
"""
from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULTS_PATH = Path(__file__).resolve().parent / "defaults.toml"


@dataclass(frozen=True, slots=True)
class MotionSettings:
    port: str
    baudrate: int
    read_timeout_s: float
    write_timeout_s: float
    boot_wait_s: float


@dataclass(frozen=True, slots=True)
class AiSettings:
    provider: str
    api_key: str | None = None

    @property
    def is_stub(self) -> bool:
        return self.provider == "stub"


@dataclass(frozen=True, slots=True)
class WebSettings:
    host: str
    port: int


@dataclass(frozen=True, slots=True)
class StorageSettings:
    path: str


@dataclass(frozen=True, slots=True)
class CorsSettings:
    origins: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LoggingSettings:
    level: str


@dataclass(frozen=True, slots=True)
class Settings:
    motion: MotionSettings
    ai: AiSettings
    web: WebSettings
    storage: StorageSettings
    cors: CorsSettings
    logging: LoggingSettings
    raw: Mapping[str, Any] = field(default_factory=dict)

    def redacted(self) -> dict[str, Any]:
        """诊断友好视图：密钥替换为占位，绝不回显原值。"""
        return {
            "motion": {
                "port": self.motion.port,
                "baudrate": self.motion.baudrate,
                "boot_wait_s": self.motion.boot_wait_s,
            },
            "ai": {
                "provider": self.ai.provider,
                "api_key": "<set>" if self.ai.api_key else "<unset>",
            },
            "web": {"host": self.web.host, "port": self.web.port},
            "storage": {"path": self.storage.path},
            "cors": {"origins": list(self.cors.origins)},
            "logging": {"level": self.logging.level},
        }


def load_toml(path: str | Path = DEFAULTS_PATH) -> dict[str, Any]:
    return tomllib.loads(Path(path).read_text(encoding="utf-8"))


def load_settings(
    env: Mapping[str, str] | None = None,
    *,
    defaults_path: str | Path = DEFAULTS_PATH,
) -> Settings:
    """加载配置：环境变量优先级高于 ``defaults.toml``。"""
    env = os.environ if env is None else env
    data = load_toml(defaults_path)

    motion = dict(data.get("motion", {}))
    ai = dict(data.get("ai", {}))
    web = dict(data.get("web", {}))
    storage = dict(data.get("storage", {}))
    cors = dict(data.get("cors", {}))
    logging_cfg = dict(data.get("logging", {}))

    port = env.get("MOTION_PORT", motion.get("port", "socket://127.0.0.1:5555"))
    api_key = env.get("DASHSCOPE_API_KEY") or None
    provider = env.get("AI_PROVIDER", ai.get("provider", "stub"))
    if not api_key and "AI_PROVIDER" not in env:
        provider = "stub"  # 无 Key 自动落桩（AC-17）

    web_host = env.get("HOST_BIND", web.get("host", "127.0.0.1"))
    web_port = int(env.get("HOST_PORT", web.get("port", 8000)))
    db_path = env.get("DB_PATH", storage.get("path", "data/toy.sqlite3"))
    origins = cors.get("origins", ["http://127.0.0.1:8000"])
    if env.get("CORS_ORIGIN"):
        origins = [o.strip() for o in env["CORS_ORIGIN"].split(",") if o.strip()]

    return Settings(
        motion=MotionSettings(
            port=str(port),
            baudrate=int(env.get("MOTION_BAUD", motion.get("baudrate", 115200))),
            read_timeout_s=float(motion.get("read_timeout_s", 0.02)),
            write_timeout_s=float(motion.get("write_timeout_s", 0.2)),
            boot_wait_s=float(env.get("MOTION_BOOT_WAIT_S", motion.get("boot_wait_s", 2.0))),
        ),
        ai=AiSettings(provider=str(provider), api_key=api_key),
        web=WebSettings(host=str(web_host), port=web_port),
        storage=StorageSettings(path=str(db_path)),
        cors=CorsSettings(origins=tuple(origins)),
        logging=LoggingSettings(level=str(env.get("LOG_LEVEL", logging_cfg.get("level", "INFO")))),
        raw=data,
    )
