# -*- coding: utf-8 -*-
"""配置管理：config.json 由 config.example.json 复制而来；**代码里不写死任何本机绝对路径**。

设置页改的一切都落在这个文件里，打包/分享时排除 config.json 即可。
"""
from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any, Dict

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / "config.json"
EXAMPLE_PATH = PROJECT_DIR / "config.example.json"

# 默认配置：路径类字段一律留空，由 config.example.json 或设置页填写
DEFAULT_CONFIG: Dict[str, Any] = {
    "port": 8720,
    "host": "127.0.0.1",
    "workers": 4,
    # 可批量扫描的目录（设置页可增删）
    "input_roots": [],
    # 压缩结果输出目录；留空则用 <项目>/output
    "output_dir": "",
    "exts": ["jpg", "jpeg", "png", "webp", "avif", "bmp", "tif", "tiff", "gif"],
    "defaults": {
        "format": "keep",          # keep | JPEG | PNG | WEBP | AVIF
        "quality": 78,
        "mode": "quality",         # quality | target
        "target_kb": 200,
        "lossless": False,
        "resize": "none",          # none | long | percent | box
        "long_edge": 1920,
        "percent": 100,
        "box_w": 1920,
        "box_h": 1080,
        "align": 0,
        "keep_metadata": False,
        "png_colors": 0,
        "skip_if_larger": True,
    },
}

_lock = threading.RLock()
_cache: Dict[str, Any] | None = None


def _merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(force: bool = False) -> Dict[str, Any]:
    global _cache
    with _lock:
        if _cache is not None and not force:
            return copy.deepcopy(_cache)
        data: Dict[str, Any] = {}
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        _cache = _merge(DEFAULT_CONFIG, data)
        return copy.deepcopy(_cache)


def save_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    global _cache
    with _lock:
        cur = load_config()
        new = _merge(cur, patch or {})
        CONFIG_PATH.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
        _cache = new
        return copy.deepcopy(new)


def output_dir() -> Path:
    cfg = load_config()
    d = (cfg.get("output_dir") or "").strip()
    p = Path(d) if d else (PROJECT_DIR / "output")
    p.mkdir(parents=True, exist_ok=True)
    return p


def upload_dir() -> Path:
    p = PROJECT_DIR / "work" / "uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


def thumb_dir() -> Path:
    p = PROJECT_DIR / "work" / "thumbs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_config_file() -> None:
    """首次运行时按示例生成 config.json（不覆盖已存在的）。"""
    if CONFIG_PATH.exists() or not EXAMPLE_PATH.exists():
        return
    try:
        CONFIG_PATH.write_text(EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        pass
