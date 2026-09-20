# -*- coding: utf-8 -*-
"""一键分类：按「有没有 ComfyUI 生成信息」把图分到不同文件夹。

规则三种：
  comfy —— A: 含 ComfyUI 信息；            B: 其余
  any   —— A: 含任意生成信息(ComfyUI/A1111); B: 纯图
  three —— A: ComfyUI；                    B: 其他生成工具；      C: 无信息

安全约定：
  * 先扫描出「判定清单」（预览），确认后才动文件；
  * 复制是默认方式，移动必须显式传 confirm=True；
  * 扫描时自动跳过目标目录自身，避免把产物又搬进产物；
  * 同名冲突可选 改名 / 跳过 / 覆盖。
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import load_config
from .metadata import read_params

RULE_COMFY = "comfy"
RULE_ANY = "any"
RULE_THREE = "three"
DEFAULT_NAMES = {"A": "有ComfyUI信息", "B": "其他", "C": "无生成信息"}
IMAGE_EXTS = {"jpg", "jpeg", "jpe", "png", "webp", "avif", "bmp", "tif", "tiff", "gif", "jfif"}


def _exts() -> set:
    cfg = load_config()
    return set(str(e).lower().lstrip(".") for e in (cfg.get("exts") or [])) | IMAGE_EXTS


def bucket_of(meta: Dict[str, Any], rule: str) -> str:
    tool = (meta.get("tool") or "")
    is_comfy = tool.startswith("ComfyUI")
    has_any = bool(meta.get("ok"))
    if rule == RULE_ANY:
        return "A" if has_any else "B"
    if rule == RULE_THREE:
        if is_comfy:
            return "A"
        return "B" if has_any else "C"
    return "A" if is_comfy else "B"


def scan_classify(root: str | Path, recursive: bool = True, rule: str = RULE_COMFY,
                  limit: int = 50000, deep: bool = False, skip_dirs: Optional[List[str]] = None) -> Dict[str, Any]:
    """扫描目录并逐张判定归类（只读，不动文件）。"""
    root = Path(root)
    if not root.exists() or not root.is_dir():
        return {"ok": False, "error": "目录不存在：%s" % root, "items": []}
    rule = rule if rule in (RULE_COMFY, RULE_ANY, RULE_THREE) else RULE_COMFY
    exts = _exts()
    skip = [Path(p).resolve() for p in (skip_dirs or []) if p]
    found: List[Path] = []
    it = root.rglob("*") if recursive else root.glob("*")
    for p in it:
        try:
            if not p.is_file() or p.suffix.lower().lstrip(".") not in exts:
                continue
            rp = p.resolve()
            if any(str(rp).startswith(str(s)) for s in skip):        # 跳过目标目录等
                continue
            found.append(p)
            if len(found) >= limit:
                break
        except Exception:
            continue
    found.sort(key=lambda x: str(x).lower())

    items: List[Dict[str, Any]] = []
    counts = {"A": 0, "B": 0, "C": 0}
    bytes_by = {"A": 0, "B": 0, "C": 0}
    for p in found:
        try:
            meta = read_params(p, deep=deep)
            b = bucket_of(meta, rule)
            rel = str(p.relative_to(root))
            items.append({
                "path": str(p), "rel": rel, "name": p.name, "dir": str(p.parent),
                "bytes": p.stat().st_size, "w": meta["file"]["w"], "h": meta["file"]["h"],
                "format": meta["file"]["format"], "bucket": b, "tool": meta.get("tool") or "",
                "source": meta.get("meta_source") or "", "ok": bool(meta.get("ok")),
                "note": (meta.get("notes") or [""])[0],
            })
            counts[b] += 1
            bytes_by[b] += items[-1]["bytes"]
        except Exception as exc:      # noqa: BLE001
            items.append({"path": str(p), "rel": str(p), "name": p.name, "dir": str(p.parent),
                          "bytes": 0, "w": 0, "h": 0, "format": "", "bucket": "C" if rule == RULE_THREE else "B",
                          "tool": "读取失败", "ok": False, "note": "%s: %s" % (type(exc).__name__, exc)})
            counts[items[-1]["bucket"]] += 1
    return {"ok": True, "root": str(root), "rule": rule, "recursive": recursive,
            "items": items, "counts": counts, "bytes": bytes_by, "total": len(items),
            "scanned": len(items), "truncated": len(found) >= limit}


def _dest_for(src: Path, root: Path, out_dir: Path, bucket: str, names: Dict[str, str],
              preserve_tree: bool) -> Path:
    folder = names.get(bucket) or DEFAULT_NAMES.get(bucket, bucket)
    if preserve_tree:
        try:
            rel = src.relative_to(root)
            # 保留来源子目录结构（不含文件名）
            sub = rel.parent
            return out_dir / folder / sub / src.name
        except Exception:
            pass
    return out_dir / folder / src.name


def run_classify(items: List[Dict[str, Any]], root: str | Path, out_dir: str | Path,
                 names: Optional[Dict[str, str]] = None, mode: str = "copy",
                 conflict: str = "rename", preserve_tree: bool = True,
                 confirm: bool = False) -> Dict[str, Any]:
    """执行分类：把 items 按 bucket 复制/移动到 out_dir/<文件夹名>/... 。"""
    root = Path(root)
    out_dir = Path(out_dir)
    names = {**DEFAULT_NAMES, **(names or {})}
    mode = "move" if mode == "move" else "copy"
    conflict = conflict if conflict in ("rename", "skip", "overwrite") else "rename"
    if mode == "move" and not confirm:
        return {"ok": False, "error": "移动模式需要传 confirm=true（界面上会先弹确认）"}

    # 安全阀：目标目录不能是来源目录本身
    try:
        if out_dir.resolve() == root.resolve():
            return {"ok": False, "error": "目标目录不能与来源目录相同"}
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:      # noqa: BLE001
        return {"ok": False, "error": "无法创建目标目录：%s" % exc}

    results: List[Dict[str, Any]] = []
    stat = {"ok": 0, "copied": 0, "moved": 0, "skipped": 0, "failed": 0, "bytes": 0}
    t0 = time.time()
    for it in items:
        src = Path(it.get("path") or "")
        bucket = it.get("bucket") or "B"
        rec: Dict[str, Any] = {"src": str(src), "name": src.name, "bucket": bucket}
        try:
            if not src.is_file():
                rec.update({"ok": False, "error": "源文件不存在"})
                stat["failed"] += 1
                results.append(rec)
                continue
            dst = _dest_for(src, root, out_dir, bucket, names, preserve_tree)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists() and dst.resolve() != src.resolve():
                if conflict == "skip":
                    rec.update({"ok": True, "skipped": True, "out": str(dst), "error": "同名已存在，已跳过"})
                    stat["skipped"] += 1
                    results.append(rec)
                    continue
                if conflict == "rename":
                    i = 1
                    while dst.exists():
                        dst = dst.with_name("%s_%d%s" % (dst.stem, i, dst.suffix))
                        i += 1
            if mode == "move":
                shutil.move(str(src), str(dst))
                stat["moved"] += 1
            else:
                shutil.copy2(src, dst)
                stat["copied"] += 1
            size = dst.stat().st_size if dst.exists() else 0
            stat["bytes"] += size
            stat["ok"] += 1
            rec.update({"ok": True, "out": str(dst), "bytes": size})
        except Exception as exc:      # noqa: BLE001
            rec.update({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)})
            stat["failed"] += 1
        results.append(rec)
    return {"ok": True, "out_dir": str(out_dir), "mode": mode, "conflict": conflict,
            "names": names, "stat": stat, "results": results, "ms": int((time.time() - t0) * 1000)}
