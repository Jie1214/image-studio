# -*- coding: utf-8 -*-
"""上传缓存（work/uploads）的信息统计与清理。

为什么会有这个缓存：浏览器里「选择图片 / 选择文件夹 / 拖入」拿不到本地绝对路径，
只能把文件内容 POST 上来，服务端先落到 work/uploads/<时间戳>/ 再解析 —— 所以用这类入口时
磁盘上会多出一份**副本**（原件始终在你自己的文件夹里，没动过）。
而「读取该目录 / 分类目录」走的是服务端直接读盘，**不复制、不占额外空间**。

这个模块负责：查大小、按时间/容量清理、启动时自动腾地方。
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any, Dict, List

from . import PROJECT_DIR, load_config, thumb_dir, upload_dir


def _dir_size(p: Path) -> tuple:
    """(字节数, 文件数)，读不到的跳过。"""
    total = 0
    n = 0
    try:
        for f in p.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
                    n += 1
            except OSError:
                continue
    except OSError:
        pass
    return total, n


def _sessions() -> List[Dict[str, Any]]:
    """上传批次列表，按时间从新到旧。"""
    root = upload_dir()
    out: List[Dict[str, Any]] = []
    try:
        children = [c for c in root.iterdir() if c.is_dir()]
    except OSError:
        return out
    for c in children:
        st = None
        try:
            st = c.stat()
        except OSError:
            continue
        size, files = _dir_size(c)
        out.append({"name": c.name, "path": str(c), "bytes": size, "files": files,
                    "mtime": st.st_mtime})
    out.sort(key=lambda d: d["mtime"], reverse=True)
    return out


def cache_info() -> Dict[str, Any]:
    up = _sessions()
    thumbs_size, thumbs_files = _dir_size(thumb_dir())
    total = sum(s["bytes"] for s in up)
    cfg = load_config()
    return {
        "ok": True,
        "uploads": {
            "dir": str(upload_dir()),
            "batches": len(up),
            "files": sum(s["files"] for s in up),
            "bytes": total,
            "oldest": (time.strftime("%Y-%m-%d %H:%M", time.localtime(up[-1]["mtime"])) if up else ""),
            "newest": (time.strftime("%Y-%m-%d %H:%M", time.localtime(up[0]["mtime"])) if up else ""),
        },
        "thumbs": {"dir": str(thumb_dir()), "files": thumbs_files, "bytes": thumbs_size},
        "total_bytes": total + thumbs_size,
        "keep_hours": int(cfg.get("upload_keep_hours") or 24),
        "max_gb": float(cfg.get("upload_max_gb") or 5),
    }


def _rm(p: Path) -> None:
    try:
        shutil.rmtree(p, ignore_errors=False)
    except Exception:                     # noqa: BLE001  删不掉就跳过，别让清理把请求搞挂
        try:
            for f in p.iterdir():
                try:
                    f.unlink() if f.is_file() else shutil.rmtree(f, ignore_errors=True)
                except OSError:
                    pass
        except OSError:
            pass


def prune(keep_hours: float | None = None, keep_newest: int = 1, max_bytes: int | None = None,
          thumbs: bool = True, all_: bool = False) -> Dict[str, Any]:
    """清理上传缓存。

    keep_hours: 比这个更旧的批次删掉（None = 用配置里的 upload_keep_hours）
    keep_newest: 无论如何保留最新的 N 个批次（正在用的那批不能被端掉）
    max_bytes: 清完时间线后再按总大小上限删，超了从最旧的开始删
    all_: True = 只留 keep_newest 个批次，其余全删
    """
    cfg = load_config()
    if keep_hours is None:
        keep_hours = float(cfg.get("upload_keep_hours") or 24)
    if max_bytes is None:
        max_bytes = int(float(cfg.get("upload_max_gb") or 5) * 1024 ** 3)

    sess = _sessions()
    now = time.time()
    freed = 0
    removed: List[str] = []
    kept: List[str] = []

    for i, s in enumerate(sess):
        if i < keep_newest:
            kept.append(s["name"])
            continue
        old = (now - s["mtime"]) > keep_hours * 3600
        if all_ or old:
            _rm(Path(s["path"]))
            freed += s["bytes"]
            removed.append(s["name"])
            continue
        kept.append(s["name"])

    # 容量上限：还在的批次按旧的先删，直到降到达标（最新那批永远留着）
    if max_bytes > 0:
        newest = sess[0]["name"] if sess else None
        rest = [s for s in sess if s["name"] in kept and s["name"] != newest]
        alive = sum(s["bytes"] for s in sess if s["name"] in kept)
        for s in sorted(rest, key=lambda d: d["mtime"]):
            if alive <= max_bytes:
                break
            _rm(Path(s["path"]))
            alive -= s["bytes"]
            freed += s["bytes"]
            removed.append(s["name"])
            if s["name"] in kept:
                kept.remove(s["name"])

    t_freed = 0
    if thumbs:
        cut = now - keep_hours * 3600
        try:
            for f in thumb_dir().glob("*"):
                try:
                    if f.is_file() and (all_ or f.stat().st_mtime < cut):
                        sz = f.stat().st_size
                        f.unlink()
                        t_freed += sz
                except OSError:
                    continue
        except OSError:
            pass
        freed += t_freed

    info = cache_info()
    info.update({"removed": removed, "kept": kept, "freed_bytes": freed, "thumbs_freed": t_freed})
    return info


def auto_prune() -> Dict[str, Any]:
    """启动时调用：按配置腾地方（默认 >24h 的批次删掉，总量控制在 5GB 以内）。"""
    try:
        return prune()
    except Exception as exc:              # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
