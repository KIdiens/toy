"""内容导入路由：``POST /content/import``（multipart）。

安全契约（AC-25）：
1. 上传先**落临时文件**并限制体积；
2. ``rolepack.read_member`` 读 ``role.json`` 并做 ``RoleConfig`` schema 校验 →
   非法包在替换前即被拒绝（旧版本不受影响）；
3. ``rolepack.import_rolepack`` 先解临时目录、校验通过后**原子切换**，任一步失败保留旧版本；
4. 切换成功后 ``roles.load_all()`` 重新索引，使新配置立即生效。
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse

from host.roles import rolepack
from host.roles.schema import RoleConfig
from host.web import schemas
from host.web.deps import ContextDep

router = APIRouter(tags=["content"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
IMPORT_FAILED_CODE = 40001
IMPORT_FAILED_STATUS = 400


def _reject(message: str) -> JSONResponse:
    """导入失败：返回统一信封 400（失败保留旧版本，见 AC-25）。"""
    return JSONResponse(
        status_code=IMPORT_FAILED_STATUS,
        content=schemas.error(IMPORT_FAILED_CODE, message),
    )


@router.post("/content/import", status_code=201)
async def import_content(
    ctx: ContextDep,
    request_id: Annotated[str, Form(min_length=1)],
    file: Annotated[UploadFile, File()],
) -> Any:
    """限体积、路径与结构校验后导入角色包，原子切换生效目录。"""
    del request_id  # 已在 Form 层强制非空；审计交给网关日志
    workdir = Path(tempfile.mkdtemp(prefix="rolepack-"))
    zip_path = workdir / "rolepack.zip"
    try:
        await _save_upload(file, zip_path)
        config = _read_config(zip_path)
        target = Path(ctx.roles.root) / str(config.role_id)
        report = rolepack.import_rolepack(zip_path, target)
        ctx.roles.load_all()
        result = schemas.ContentImportResult(
            role_id=config.role_id, config_version=config.config_version, files=report.files
        )
        return schemas.ok(result.model_dump())
    except rolepack.RolePackError as exc:
        return _reject(str(exc))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def _save_upload(file: UploadFile, dest: Path) -> None:
    """流式落盘并限制总体积，超限直接拒绝（不落满磁盘）。"""
    total = 0
    with dest.open("wb") as out:
        while True:
            chunk = await file.read(CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise rolepack.RolePackError(f"上传体积超上限 {MAX_UPLOAD_BYTES} 字节")
            out.write(chunk)
    if total == 0:
        raise rolepack.RolePackError("上传内容为空")


def _read_config(zip_path: Path) -> RoleConfig:
    """读包内 ``role.json`` 并做 schema 校验；非法包在此被拒绝。"""
    try:
        raw = json.loads(rolepack.read_member(zip_path).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise rolepack.RolePackError(f"role.json 不是合法 JSON: {exc}") from exc
    try:
        return RoleConfig.model_validate(raw)
    except ValueError as exc:
        raise rolepack.RolePackError(f"角色配置非法: {exc}") from exc
