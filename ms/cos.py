"""COS 存储：本地文件 → 公网 URL（视频参考素材中转、文档/图片/网页分享等通用场景）。
桶已配置 Agent/ 前缀 30 天生命周期（当前密钥也只有该前缀权限）；对象名带随机串防枚举。"""
from __future__ import annotations

import json
import mimetypes
import re
import secrets
import time
from datetime import datetime
from pathlib import Path

from qcloud_cos import CosConfig, CosS3Client

from .config import CFG, WORKDIR, cfg, job_dir

COS_PREFIX = cfg("COS_PATH", "/Agent/").strip("/") + "/"
REUSE_DAYS = 25  # 生命周期 30 天，留余量
_client: CosS3Client | None = None


def client() -> CosS3Client:
    global _client
    if _client is None:
        _client = CosS3Client(CosConfig(Region=cfg("COS_REGION"), SecretId=cfg("COS_SECRET_ID"),
                                        SecretKey=cfg("COS_SECRET_KEY"), Scheme="https"))
    return _client


def public_url(key: str) -> str:
    domain = CFG.get("COS_DOMAIN") or f"https://{cfg('COS_BUCKET')}.cos.{cfg('COS_REGION')}.myqcloud.com"
    return domain.rstrip("/") + "/" + key


def _slug(job: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]", "", job.encode("ascii", "ignore").decode()) or "job"


def _cache_file(job: str | None) -> Path:
    if job:
        return job_dir(job) / ".cos_cache.json"
    WORKDIR.mkdir(parents=True, exist_ok=True)
    return WORKDIR / ".cos_cache.json"


def upload(local: str | Path, job: str | None = None, folder: str | None = None) -> dict:
    """job：视频流水线的项目（缓存记在项目里）；folder：通用上传时的分组名（对象路径 Agent/<folder>/…）。"""
    p = Path(local).expanduser()
    if not p.is_file():
        raise FileNotFoundError(str(p))
    st = p.stat()
    cache_file = _cache_file(job)
    cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}
    sig = f"{p.resolve()}|{st.st_size}|{int(st.st_mtime)}"
    hit = cache.get(sig)
    if hit and time.time() - hit.get("at", 0) < REUSE_DAYS * 86400:
        return {"url": hit["url"], "key": hit["key"], "reused": True}
    key = f"{COS_PREFIX}{_slug(folder or job or 'misc')}/{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(6)}{p.suffix.lower()}"
    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    client().upload_file(Bucket=cfg("COS_BUCKET"), Key=key, LocalFilePath=str(p), ContentType=ctype)
    cache[sig] = {"url": public_url(key), "key": key, "at": time.time()}
    cache_file.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
    return {"url": public_url(key), "key": key, "reused": False, "size": st.st_size, "expires_in_days": 30}


def list_objects(folder: str = "", limit: int = 50) -> dict:
    """列出 Agent/ 前缀下的对象（新的在前）。"""
    prefix = COS_PREFIX + (_slug(folder) + "/" if folder else "")
    items, marker = [], ""
    while True:
        r = client().list_objects(Bucket=cfg("COS_BUCKET"), Prefix=prefix, Marker=marker, MaxKeys=1000)
        items += r.get("Contents", [])
        if r.get("IsTruncated") != "true":
            break
        marker = r.get("NextMarker") or items[-1]["Key"]
    items.sort(key=lambda o: o["LastModified"], reverse=True)
    return {"count": len(items), "objects": [{"key": o["Key"], "url": public_url(o["Key"]), "size": int(o["Size"]),
                                              "modified": o["LastModified"]} for o in items[:limit]]}


def delete(key: str) -> dict:
    """删除对象（只允许 Agent/ 前缀，防误删桶内其他数据）。"""
    if not key.startswith(COS_PREFIX) or ".." in key:
        raise ValueError(f"只能删除 {COS_PREFIX} 前缀下的对象：{key}")
    client().delete_object(Bucket=cfg("COS_BUCKET"), Key=key)
    return {"deleted": key}


def remember_source(local: str | Path, url: str, job: str) -> None:
    f = job_dir(job) / ".sources.json"
    d = json.loads(f.read_text()) if f.exists() else {}
    d[str(Path(local).expanduser().resolve())] = url
    f.write_text(json.dumps(d, ensure_ascii=False, indent=1))


def as_url(item: str, job: str) -> str:
    if re.match(r"^https?://", item):
        return item
    f = job_dir(job) / ".sources.json"
    if f.exists():  # reAPI 产物优先用其 CDN 原链接（海外探测更稳定）
        src = json.loads(f.read_text()).get(str(Path(item).expanduser().resolve()))
        if src:
            return src
    return upload(item, job)["url"]
