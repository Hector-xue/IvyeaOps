"""能力市场接口：浏览门道的 Skill，看清能力清单后再安装。

**这里的每个出站请求都由用户的动作触发**，没有任何后台轮询、没有心跳、不带机器
标识。市场默认关闭；关着的时候这些端点直接回 403 并说明原因，而不是偷偷去连。

安装的顺序是刻意的：``preview`` 先把包拉下来、校验、静态分析，把能力清单交给
用户；``install`` 必须带上用户确认过的那份指纹。**没看过清单就装不了** ——
"我不知道它要干什么就点了安装"这件事不该发生。
"""
from __future__ import annotations

import hashlib
import io
import logging
import tarfile
from typing import Any, Dict

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core.security import require_admin
from app.services import skill_market as sm

logger = logging.getLogger("ivyea.routers.skill_market")
router = APIRouter()

_TIMEOUT = 15.0
_MAX_DOWNLOAD = 4 * 1024 * 1024


def _require_enabled() -> None:
    if not sm.market_enabled():
        raise HTTPException(
            403,
            "能力市场默认关闭（它会向门道社区发起请求）。要用的话去「系统配置」打开 —— "
            "开启后也只在你主动浏览/安装时联网，不做任何后台上报。")


def _get(path: str, **params) -> Any:
    url = f"{sm.market_url()}{path}"
    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=False) as c:
            # 不带 cookie、不带自定义 UA 指纹 —— 对面拿不到任何可用于识别本机的东西。
            r = c.get(url, params={k: v for k, v in params.items() if v not in ("", None)})
    except httpx.HTTPError as exc:
        # 断网时优雅降级：市场不可达不该让整个板块白屏。
        raise HTTPException(503, f"连不上能力市场（{sm.market_url()}）：{exc}") from exc
    if r.status_code >= 400:
        raise HTTPException(r.status_code, f"市场返回 {r.status_code}")
    return r


@router.get("/status")
def market_status(_admin: str = Depends(require_admin)) -> dict:
    """给前端判断该显示什么：关着、开着、还是开着但连不上。"""
    return {
        "enabled": sm.market_enabled(),
        "url": sm.market_url(),
        "installed": sm.installed(),
    }


@router.get("/skills")
def browse(q: str = Query("", max_length=80), category: str = Query("", max_length=40),
           sort: str = Query("hot", pattern="^(hot|new|rating)$"),
           page: int = Query(1, ge=1, le=200),
           _admin: str = Depends(require_admin)) -> dict:
    _require_enabled()
    # 只要 A 类：客户端当前只装纯提示词的 Skill，把 B 类列出来只会让用户
    # 点进去才发现装不了。
    return _get("/skills", q=q, category=category, sort=sort, page=page, **{"class": "A"}).json()


def _fetch_package(slug: str, version: str) -> tuple:
    meta = _get(f"/skills/{slug}/{version}/manifest").json()
    resp = _get(f"/skills/{slug}/{version}/download")
    blob = resp.content
    if len(blob) > _MAX_DOWNLOAD:
        raise HTTPException(400, "安装包超过 4MB 上限")
    return meta, blob, resp.headers


def _unpack(blob: bytes) -> Dict[str, bytes]:
    """解包到内存。**不落盘** —— 在用户确认之前，陌生内容不该出现在文件系统上。"""
    files: Dict[str, bytes] = {}
    try:
        tf = tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz")
    except tarfile.TarError as exc:
        raise HTTPException(400, f"安装包不是合法的 tar.gz：{exc}") from exc
    with tf:
        for member in tf.getmembers():
            name = member.name
            if name.startswith("/") or ".." in name.split("/") or "\\" in name:
                raise HTTPException(400, f"安装包里有越界路径：{name}")
            if member.issym() or member.islnk():
                raise HTTPException(400, f"安装包里有软/硬链接：{name}")
            if not member.isfile():
                continue
            fh = tf.extractfile(member)
            if fh is not None:
                files[name.split("/", 1)[-1] if "/" in name else name] = fh.read()
    return files


class PreviewBody(BaseModel):
    slug: str
    version: str


@router.post("/preview")
def preview(body: PreviewBody, _admin: str = Depends(require_admin)) -> dict:
    """拉包、校验、静态分析，把**能力清单**交给用户。安装前必经这一步。

    这里做的分析和门道那边上架时做的是两回事：那边判"能不能上架"，
    这里判"要不要让它落到我这台机器上"。市场源是可以换的，所以这一步不能省。
    """
    _require_enabled()
    from app.core import hub_settings

    meta, blob, headers = _fetch_package(body.slug, body.version)
    problems = sm.verify_payload(
        blob,
        sha256=headers.get("X-Skill-Sha256", "") or meta.get("sha256", ""),
        signature=headers.get("X-Skill-Signature", "") or meta.get("signature", ""),
        public_key=str(hub_settings.get("skill_market_pubkey") or ""),
    )
    files = _unpack(blob)
    manifest = sm.analyze(files)

    return {
        "slug": body.slug,
        "version": body.version,
        "integrity": {"ok": not problems, "problems": problems},
        "manifest": manifest.to_dict(),
        "market_manifest": meta.get("manifest", {}),
        # 分享类的署名要一路带到确认弹窗上 —— 用户在决定装不装的那一刻，
        # 就该知道这东西是谁写的、什么许可证。
        "attribution": meta.get("attribution", {}),
        "sha256": headers.get("X-Skill-Sha256", "") or meta.get("sha256", ""),
        # 用户确认的是这份指纹；install 会核对，防止"看的是 A、装的是 B"。
        "confirm_token": hashlib.sha256(blob).hexdigest(),
    }


class InstallBody(BaseModel):
    slug: str
    version: str
    confirm_token: str


@router.post("/install")
def install(body: InstallBody, _admin: str = Depends(require_admin)) -> dict:
    """安装。必须带 preview 给出的 confirm_token —— 没看过清单就装不了。"""
    _require_enabled()
    from pathlib import Path

    from app.core import hub_settings
    from app.core.skill_paths import SKILLS_ROOT
    from app.services.skill_repo import validate_skill_name

    meta, blob, headers = _fetch_package(body.slug, body.version)

    actual = hashlib.sha256(blob).hexdigest()
    if actual != body.confirm_token:
        # 两次下载内容不一致：可能是上游换了包，也可能是中间人。
        # 无论哪种，用户确认过的都不是现在这份。
        raise HTTPException(
            409, "安装包与你确认时的内容不一致，已中止。请重新查看能力清单。")

    problems = sm.verify_payload(
        blob, sha256=headers.get("X-Skill-Sha256", "") or meta.get("sha256", ""),
        signature=headers.get("X-Skill-Signature", "") or meta.get("signature", ""),
        public_key=str(hub_settings.get("skill_market_pubkey") or ""))
    if problems:
        raise HTTPException(400, "完整性校验失败：" + "；".join(problems))

    files = _unpack(blob)
    manifest = sm.analyze(files)
    if not manifest.installable:
        raise HTTPException(400, "这个 Skill 未通过本地安全检查：" + "；".join(manifest.blockers))

    validate_skill_name(body.slug)
    dest = (Path(SKILLS_ROOT) / "community" / body.slug).resolve()
    root = (Path(SKILLS_ROOT) / "community").resolve()
    # **别用 str(...).startswith(root + "/") 做包含检查**：Windows 的分隔符是
    # 反斜杠，那样写恒为假，结果是 Windows 用户装任何 Skill 都会被判"非法路径"。
    # Path.is_relative_to（3.9 起可用）按路径层级比，不碰分隔符字面量。
    if dest != root and not dest.is_relative_to(root):
        raise HTTPException(400, "非法的安装路径")

    dest.mkdir(parents=True, exist_ok=True)
    for name, blob_bytes in files.items():
        target = (dest / name).resolve()
        # 同样按路径层级比。原写法用字符串前缀，"…/community/foo-evil" 会被
        # 判成在 "…/community/foo" 之内 —— 在任何平台上都是个洞。
        if target != dest and not target.is_relative_to(dest):
            raise HTTPException(400, f"越界文件：{name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob_bytes)

    sm.record_install(body.slug, version=body.version, sha256=actual,
                      source=sm.market_url(), skill_class=manifest.skill_class)
    return {"ok": True, "slug": body.slug, "version": body.version,
            "path": str(dest), "files": len(files)}


class UninstallBody(BaseModel):
    slug: str


@router.post("/uninstall")
def uninstall(body: UninstallBody, _admin: str = Depends(require_admin)) -> dict:
    """卸载。走回收站而不是直接删 —— 误删一个改过的 Skill 是能挽回的。"""
    from app.core.skill_paths import SKILLS_ROOT
    from app.services.skill_repo import validate_skill_name

    validate_skill_name(body.slug)
    from pathlib import Path
    dest = (Path(SKILLS_ROOT) / "community" / body.slug).resolve()
    root = (Path(SKILLS_ROOT) / "community").resolve()
    if dest != root and not dest.is_relative_to(root):
        raise HTTPException(400, "非法路径")

    moved = False
    if dest.is_dir():
        try:
            # trash_skill 收的是相对 SKILLS_ROOT 的 skill 名，不是绝对路径。
            from app.services.trash import trash_skill
            trash_skill(f"community/{body.slug}")
            moved = True
        except Exception as exc:  # noqa: BLE001 — 回收站不可用时退化为直接删
            logger.warning("移入回收站失败，改为直接删除：%s", exc)
            import shutil
            shutil.rmtree(dest, ignore_errors=True)

    sm.forget(body.slug)
    return {"ok": True, "slug": body.slug, "to_trash": moved}
