# -*- coding: utf-8 -*-
"""批量任务引擎：扫描目录 / 线程池并行压缩 / 进度快照 / 可取消 / 结果打包。"""
from __future__ import annotations

import os
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image

from . import load_config, output_dir
from .imaging import Settings, compress_file, fmt_of, open_image

Image.MAX_IMAGE_PIXELS = None

IMAGE_EXTS = {"jpg", "jpeg", "jpe", "png", "webp", "avif", "bmp", "tif", "tiff", "gif", "jfif"}


def _exts() -> set:
    cfg = load_config()
    ext = [str(e).lower().lstrip(".") for e in (cfg.get("exts") or [])]
    return set(ext) | IMAGE_EXTS


def probe(path: str | Path) -> Dict[str, Any]:
    """读取文件基本信息（尺寸只读文件头，很快）。"""
    p = Path(path)
    info: Dict[str, Any] = {"path": str(p), "name": p.name, "dir": str(p.parent)}
    try:
        st = p.stat()
        info["bytes"] = st.st_size
        info["mtime"] = int(st.st_mtime)
    except Exception as exc:
        info["bytes"] = 0
        info["error"] = str(exc)
    try:
        with Image.open(p) as im:
            info["w"], info["h"] = im.size
            info["format"] = im.format or fmt_of(p)
            info["mode"] = im.mode
    except Exception as exc:
        info["w"] = info["h"] = 0
        info["format"] = fmt_of(p)
        info["error"] = "%s: %s" % (type(exc).__name__, exc)
    return info


def scan_dir(root: str | Path, recursive: bool = True, limit: int = 20000) -> Dict[str, Any]:
    root = Path(root)
    if not root.exists() or not root.is_dir():
        return {"ok": False, "error": "目录不存在：%s" % root, "files": []}
    exts = _exts()
    found: List[Path] = []
    it = root.rglob("*") if recursive else root.glob("*")
    for p in it:
        try:
            if p.is_file() and p.suffix.lower().lstrip(".") in exts:
                found.append(p)
                if len(found) >= limit:
                    break
        except Exception:
            continue
    found.sort(key=lambda x: str(x).lower())
    files = [probe(p) for p in found]
    return {
        "ok": True,
        "root": str(root),
        "recursive": recursive,
        "files": files,
        "total": len(files),
        "total_bytes": sum(f.get("bytes", 0) for f in files),
        "truncated": len(found) >= limit,
    }


@dataclass
class Job:
    id: str
    settings: Dict[str, Any]
    files: List[str]
    out_dir: str
    workers: int = 4
    status: str = "running"            # running | done | cancelled | error
    done: int = 0
    total: int = 0
    results: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[Dict[str, Any]] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    finished: float = 0.0
    cancel: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            res = list(self.results)
            errs = list(self.errors)
        src = sum(r.get("src_bytes", 0) for r in res if r.get("ok"))
        out = sum(r.get("out_bytes", 0) for r in res if r.get("ok"))
        return {
            "id": self.id,
            "status": self.status,
            "done": self.done,
            "total": self.total,
            "out_dir": self.out_dir,
            "workers": self.workers,
            "elapsed": round((self.finished or time.time()) - self.started, 1),
            "results": res,
            "errors": errs,
            "total_src_bytes": src,
            "total_out_bytes": out,
            "saved_pct": round(100.0 * (1 - out / src), 1) if src else 0.0,
        }


JOBS: Dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()


def get_job(job_id: str) -> Optional[Job]:
    return JOBS.get(job_id)


def _run(job: Job) -> None:
    st = Settings.from_dict(job.settings)
    try:
        with ThreadPoolExecutor(max_workers=max(1, int(job.workers))) as pool:
            futures = [pool.submit(compress_file, f, st, job.out_dir) for f in job.files]
            for fut in futures:
                if job.cancel:
                    break
                try:
                    r = fut.result()
                except Exception as exc:      # noqa: BLE001
                    r = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc), "src": "", "name": ""}
                with job.lock:
                    if r.get("ok"):
                        job.results.append(r)
                    else:
                        job.errors.append(r)
                    job.done += 1
        job.status = "cancelled" if job.cancel else "done"
    except Exception as exc:                  # noqa: BLE001
        job.status = "error"
        job.errors.append({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)})
    finally:
        job.finished = time.time()


def start_job(files: List[str], settings: Dict[str, Any], out_dir: Optional[str] = None,
              workers: Optional[int] = None, subdir: Optional[str] = None) -> Job:
    cfg = load_config()
    base = Path(out_dir) if out_dir else output_dir()
    if subdir:
        base = base / subdir
    base.mkdir(parents=True, exist_ok=True)
    job = Job(
        id=uuid.uuid4().hex[:12],
        settings=settings or {},
        files=[str(f) for f in files],
        out_dir=str(base),
        workers=int(workers or cfg.get("workers") or 4),
    )
    job.total = len(job.files)
    with _JOBS_LOCK:
        JOBS[job.id] = job
    threading.Thread(target=_run, args=(job,), name="job-%s" % job.id, daemon=True).start()
    return job


def zip_results(job: Job) -> Optional[str]:
    """把任务结果打包成 zip，返回路径。"""
    snap = job.snapshot()
    outs = [r.get("out") for r in snap["results"] if r.get("out") and os.path.exists(r["out"])]
    if not outs:
        return None
    out_dir = Path(job.out_dir)
    target = out_dir / ("%s_打包.zip" % time.strftime("%Y%m%d-%H%M%S"))
    # 打包目录放在 output 下会被下次扫描看到，所以放 work 里
    target = Path(__file__).resolve().parent.parent / "work" / target.name
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        used = set()
        for o in outs:
            p = Path(o)
            name = p.name
            i = 1
            while name in used:
                name = "%s_%d%s" % (p.stem, i, p.suffix)
                i += 1
            used.add(name)
            z.write(p, arcname=name)
    return str(target)
