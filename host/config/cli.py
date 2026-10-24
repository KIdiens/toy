"""命令行参数转成配置层环境变量；不接触设备或数据库。"""
from __future__ import annotations

import argparse
import os
from tomllib import TOMLDecodeError


def describe_config_error(exc: BaseException) -> str:
    """把启动配置异常翻译成包含配置项与处理办法的中文提示。"""
    text = str(exc)
    if isinstance(exc, ValueError) and "invalid literal for int()" in text:
        return (
            f"配置项的取值不是合法数字（{text}）。"
            "请检查 HOST_PORT / MOTION_BAUD / AUDIO_SAMPLE_RATE 等环境变量，"
            "或 host/config/defaults.toml 里的对应值。"
        )
    if isinstance(exc, ValueError) and "Unknown level" in text:
        return (
            f"LOG_LEVEL 取值非法（{text}）。"
            "请改成 DEBUG / INFO / WARNING / ERROR / CRITICAL 之一。"
        )
    if isinstance(exc, TOMLDecodeError):
        return (
            f"配置文件 host/config/defaults.toml 语法错误（{text}）。"
            "请检查 TOML 方括号是否成对、是否有中文标点。"
        )
    if isinstance(exc, FileNotFoundError):
        return f"找不到文件（{text}）。请检查配置路径或当前工作目录。"
    if isinstance(exc, PermissionError):
        return f"没有权限（{text}）。请检查 DB_PATH 指向的目录是否可写。"
    return (f"配置无法加载（{type(exc).__name__}: {text}）。"
            "请检查环境变量与 host/config/defaults.toml。")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="host.main", description="AI 角色玩具主机服务")
    parser.add_argument("--simulate", action="store_true", help="无硬件档位（进程内 MCU 模拟器）")
    parser.add_argument("--host", default=None, help="监听地址（覆盖 HOST_BIND）")
    parser.add_argument("--port", type=int, default=None, help="监听端口（覆盖 HOST_PORT）")
    parser.add_argument("--db", default=None, help="SQLite 路径（覆盖 DB_PATH）")
    parser.add_argument("--log-level", default=None, help="日志级别（覆盖 LOG_LEVEL）")
    parser.add_argument("--audio", choices=("null", "sd"), help="音频后端（默认 null）")
    parser.add_argument("--audio-input-device", help="录音设备名称或编号（默认系统输入）")
    parser.add_argument("--audio-output-device", help="播放设备名称或编号（默认系统输出）")
    parser.add_argument("--audio-sample-rate", type=int, help="录音采样率 Hz（默认 16000）")
    return parser.parse_args(argv)


def build_env(args: argparse.Namespace, base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    if args.host:
        env["HOST_BIND"] = args.host
    if args.port:
        env["HOST_PORT"] = str(args.port)
    if args.db:
        env["DB_PATH"] = args.db
    if args.log_level:
        env["LOG_LEVEL"] = args.log_level
    for name in ("audio", "audio_input_device", "audio_output_device", "audio_sample_rate"):
        value = getattr(args, name, None)
        if value is not None:
            env["AUDIO_BACKEND" if name == "audio" else name.upper()] = str(value)
    return env
