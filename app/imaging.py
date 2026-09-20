# -*- coding: utf-8 -*-
"""压缩核心：加载 → 缩放 → 编码（可二分搜索到目标体积）→ 元数据控制。

只用 Pillow（本机已有，含 libjpeg-turbo / zlib / libwebp / libavif）。
"""
from __future__ import annotations

import io
import math
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = None          # 允许超大图
try:
    ImageFile = __import__("PIL.ImageFile", fromlist=["ImageFile"])
except Exception:  # pragma: no cover
    ImageFile = None

FMT_ALIASES = {
    "jpg": "JPEG", "jpeg": "JPEG", "jpe": "JPEG", "png": "PNG", "webp": "WEBP",
    "avif": "AVIF", "bmp": "BMP", "tif": "TIFF", "tiff": "TIFF", "gif": "GIF",
}
EXT_FOR = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "AVIF": ".avif",
           "BMP": ".bmp", "TIFF": ".tif", "GIF": ".gif"}
LOSSY = {"JPEG", "WEBP", "AVIF"}
KEEP_SAFE = {"JPEG", "PNG", "WEBP", "AVIF"}     # 可安全重编码的格式


@dataclass
class Settings:
    format: str = "keep"
    quality: int = 78
    mode: str = "quality"
    target_kb: int = 200
    lossless: bool = False
    resize: str = "none"
    long_edge: int = 1920
    percent: int = 100
    box_w: int = 1920
    box_h: int = 1080
    align: int = 0
    keep_metadata: bool = False
    png_colors: int = 0
    skip_if_larger: bool = True
    allow_shrink: bool = True

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "Settings":
        d = d or {}
        st = cls()
        for f in cls.__dataclass_fields__:      # type: ignore[attr-defined]
            if f in d and d[f] is not None:
                setattr(st, f, d[f])
        st.quality = max(1, min(100, int(st.quality)))
        st.mode = "target" if str(st.mode) == "target" else "quality"
        st.resize = st.resize if st.resize in ("none", "long", "percent", "box") else "none"
        st.align = int(st.align) if int(st.align or 0) in (0, 2, 8, 16, 32, 64) else 0
        st.png_colors = int(st.png_colors or 0)
        st.percent = max(1, min(400, int(st.percent or 100)))
        st.long_edge = max(16, int(st.long_edge or 1920))
        st.box_w = max(16, int(st.box_w or 1920))
        st.box_h = max(16, int(st.box_h or 1080))
        st.target_kb = max(1, int(st.target_kb or 200))
        return st

    def dict(self) -> Dict[str, Any]:
        return asdict(self)


def open_image(path: str | Path, max_pixels: Optional[int] = None) -> Image.Image:
    """打开并纠正 EXIF 旋转；超过 max_pixels 时先降采样（用于缩略图）。"""
    img = Image.open(path)
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    if max_pixels:
        w, h = img.size
        if w * h > max_pixels:
            scale = math.sqrt(max_pixels / float(w * h))
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
    return img


def target_size(w: int, h: int, st: Settings) -> Tuple[int, int]:
    """按设置计算目标像素尺寸（等比，不变形）。"""
    if st.resize == "none" or w <= 0 or h <= 0:
        return w, h
    if st.resize == "long":
        m = max(w, h)
        if m <= st.long_edge:
            return w, h
        s = st.long_edge / float(m)
    elif st.resize == "percent":
        s = st.percent / 100.0
    elif st.resize == "box":
        s = min(st.box_w / float(w), st.box_h / float(h))
        if s >= 1.0:
            return w, h
    else:
        return w, h
    nw, nh = max(1, int(round(w * s))), max(1, int(round(h * s)))
    if st.align > 1:
        a = st.align
        nw, nh = max(a, nw // a * a), max(a, nh // a * a)
    return nw, nh


def _flatten(img: Image.Image, bg=(255, 255, 255)) -> Image.Image:
    """把透明通道垫成白底（JPG/BMP 不支持 alpha）。"""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        base = Image.new("RGB", rgba.size, bg)
        base.paste(rgba, mask=rgba.split()[-1])
        return base
    if img.mode in ("I;16", "I", "F", "I;16B", "I;16L"):
        return img.convert("RGB")
    return img.convert("RGB") if img.mode not in ("RGB", "L") else img


def _quantize_png(img: Image.Image, colors: int) -> Image.Image:
    """PNG 调色板量化（大色块图能省一大截，照片类慎用）。"""
    colors = max(2, min(256, int(colors)))
    if img.mode == "RGBA" or (img.mode == "P" and "transparency" in img.info):
        return img.convert("RGBA").quantize(colors=colors, method=Image.Quantize.FASTOCTREE)
    return img.convert("RGB").quantize(colors=colors, method=Image.Quantize.MEDIANCUT)


def encode(img: Image.Image, fmt: str, st: Settings, quality: Optional[int] = None) -> bytes:
    """按格式编码成字节。quality 可临时覆盖（二分搜索用）。"""
    q = int(st.quality if quality is None else quality)
    q = max(1, min(100, q))
    buf = io.BytesIO()
    meta: Dict[str, Any] = {}
    if st.keep_metadata:
        if img.info.get("exif"):
            meta["exif"] = img.info["exif"]
        if img.info.get("icc_profile"):
            meta["icc_profile"] = img.info["icc_profile"]

    if fmt == "JPEG":
        im = _flatten(img)
        kw = {"quality": q, "optimize": True, "progressive": True, **meta}
        kw["subsampling"] = 2 if q <= 88 else 0        # 4:2:0 更省体积；高质量用 4:4:4
        if q >= 95:
            kw["quality"] = 95
        im.save(buf, "JPEG", **kw)
    elif fmt == "WEBP":
        kw = {"quality": q, "method": 6, "alpha_quality": 100, **meta}
        if st.lossless:
            kw = {"lossless": True, "method": 6, **meta}
        img.save(buf, "WEBP", **kw)
    elif fmt == "AVIF":
        kw = {"quality": q, "speed": 6, **meta}
        if st.lossless:
            kw["lossless"] = True
        try:
            img.save(buf, "AVIF", **kw)
        except Exception:
            buf = io.BytesIO()
            im = img.convert("RGB") if img.mode not in ("RGB", "RGBA") else img
            im.save(buf, "AVIF", **kw)
    elif fmt == "PNG":
        im = img
        if st.png_colors and not st.lossless:
            im = _quantize_png(img, st.png_colors)
        if st.lossless and st.png_colors:
            im = _quantize_png(img, max(2, min(256, st.png_colors)))
        kw = {"optimize": True, "compress_level": 9, **meta}
        im.save(buf, "PNG", **kw)
    elif fmt in ("BMP", "TIFF", "GIF"):
        im = _flatten(img) if fmt in ("BMP", "GIF") else img
        if fmt == "GIF":
            im = im.convert("P", palette=Image.Palette.ADAPTIVE, colors=256)
        im.save(buf, fmt, **meta)
    else:
        raise ValueError("不支持的格式: %s" % fmt)
    return buf.getvalue()


def encode_to_target(img: Image.Image, fmt: str, st: Settings, target_bytes: int):
    """按目标体积编码：先试给定质量，超了就二分降质量，仍超则逐步缩分辨率。"""
    best = encode(img, fmt, st)
    best_q = st.quality
    shrink = 1.0
    if len(best) <= target_bytes:
        return best, best_q, shrink
    lo, hi = 1, max(1, st.quality - 1)
    while lo <= hi:
        mid = (lo + hi) // 2
        data = encode(img, fmt, st, quality=mid)
        if len(data) <= target_bytes:
            best, best_q, lo = data, mid, mid + 1
        else:
            hi = mid - 1
    if len(best) <= target_bytes or not st.allow_shrink or fmt not in LOSSY:
        return best, best_q, shrink
    cur = img
    for _ in range(6):
        nw = max(16, int(cur.width * 0.85))
        nh = max(16, int(cur.height * 0.85))
        cur = cur.resize((nw, nh), Image.Resampling.LANCZOS)
        shrink *= 0.85
        data = encode(cur, fmt, st, quality=max(1, best_q))
        if len(data) <= target_bytes:
            return data, max(1, best_q), shrink
        if len(data) < len(best):
            best, shrink_best = data, shrink
        else:
            break
    return best, best_q, shrink


def fmt_of(path: str | Path) -> str:
    ext = Path(path).suffix.lower().lstrip(".")
    return FMT_ALIASES.get(ext, "PNG")


def resolve_format(st: Settings, src_format: str) -> str:
    if st.format and st.format != "keep":
        return st.format
    if src_format in KEEP_SAFE:
        return src_format
    if src_format == "GIF":
        return "PNG"        # GIF 只取首帧、256 色，转 PNG 更实用
    return "JPEG"


def out_name(src: str | Path, fmt: str, suffix: str = "") -> str:
    p = Path(src)
    return (p.stem + suffix + EXT_FOR.get(fmt, ".png"))


def compress_file(src: str | Path, st: Settings, out_dir: str | Path,
                  suffix: str = "", overwrite: bool = True) -> Dict[str, Any]:
    """压缩一张图并落盘，返回一份结果字典（含体积/尺寸/耗时）。"""
    src = Path(src)
    t0 = time.perf_counter()
    res: Dict[str, Any] = {"src": str(src), "name": src.name, "ok": False, "error": ""}
    try:
        src_bytes = src.stat().st_size
        res["src_bytes"] = src_bytes
        src_fmt = fmt_of(src)
        img = open_image(src)
        res["src_w"], res["src_h"] = img.size
        res["src_format"] = src_fmt
        fmt = resolve_format(st, src_fmt)
        res["out_format"] = fmt

        nw, nh = target_size(img.width, img.height, st)
        if (nw, nh) != img.size:
            img = img.resize((nw, nh), Image.Resampling.LANCZOS)
        res["out_w"], res["out_h"] = img.size

        if st.mode == "target" and fmt in LOSSY:
            data, used_q, shrink = encode_to_target(img, fmt, st, st.target_kb * 1024)
            res["quality_used"] = used_q
            res["shrink"] = round(shrink, 3)
        else:
            data, used_q, shrink = encode(img, fmt, st), st.quality, 1.0
            res["quality_used"] = used_q
            res["shrink"] = 1.0

        # 不比原图更小就保留原文件（可选），避免"越压越大"
        passthrough = False
        if st.skip_if_larger and len(data) >= src_bytes and fmt == src_fmt and (nw, nh) == (res["src_w"], res["src_h"]):
            try:
                data = src.read_bytes()
                passthrough = True
            except Exception:
                pass

        out_path = Path(out_dir) / out_name(src, fmt, suffix)
        if out_path.exists() and not overwrite:
            i = 1
            while (Path(out_dir) / (out_path.stem + "_%d" % i + out_path.suffix)).exists():
                i += 1
            out_path = Path(out_dir) / (out_path.stem + "_%d" % i + out_path.suffix)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)

        res.update({
            "ok": True,
            "out": str(out_path),
            "out_name": out_path.name,
            "out_bytes": out_path.stat().st_size,
            "passthrough": passthrough,
            "ms": int((time.perf_counter() - t0) * 1000),
        })
        res["saved_pct"] = round(100.0 * (1 - res["out_bytes"] / src_bytes), 1) if src_bytes else 0.0
        res["ratio"] = round(res["out_bytes"] / src_bytes, 3) if src_bytes else 0.0
    except Exception as exc:      # noqa: BLE001
        res["error"] = "%s: %s" % (type(exc).__name__, exc)
        res["ms"] = int((time.perf_counter() - t0) * 1000)
    return res


def make_thumb(src: str | Path, dst: str | Path, box: int = 320, quality: int = 82) -> Optional[str]:
    """生成缩略图（用于界面预览），返回路径。"""
    try:
        img = open_image(src, max_pixels=40_000_000)
        img.thumbnail((box, box), Image.Resampling.LANCZOS)
        img = _flatten(img)
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        img.save(dst, "JPEG", quality=quality, optimize=True)
        return str(dst)
    except Exception:
        return None
