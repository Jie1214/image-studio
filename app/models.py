# -*- coding: utf-8 -*-
"""本地模型 / LoRA 库：读 safetensors 头部元数据、找预览图、按触发词检索。

safetensors 头部格式（公开规范）：前 8 字节 = 小端 uint64 头部长度，随后是 JSON 头部，
`__metadata__` 键里放训练器写入的元数据（kohya / civitai 都写在这）。**只读文件头，不解权重、不整file读**，
所以几 GB 的模型也是毫秒级。

支持的目录约定（在设置页里配，代码里不写死任何路径）：
- 按所在文件夹名/文件名猜类型：loras / lora / checkpoints / vae / controlnet / embeddings / upscale_models …
- 预览图：同目录下 `同名.png` / `同名.preview.png` / `同名.jpg` 等
- 触发词：`ss_tag_frequency`（kohya 写的每个 tag 出现次数）→ 取出现最多的前若干个；
  另外认 `modelspec.description` / `ss_trained_words` / civitai 的 `activation text`
"""
from __future__ import annotations

import json
import re
import struct
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

MODEL_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".sft")
PREVIEW_EXTS = (".preview.png", ".preview.jpg", ".preview.jpeg", ".preview.webp", ".png", ".jpg", ".jpeg", ".webp")

# 文件夹名 → 类型（ComfyUI 默认目录名都在这）
DIR_KINDS = (
    ("loras", "LoRA"), ("lora", "LoRA"), ("lycoris", "LyCORIS"),
    ("checkpoints", "Checkpoint"), ("stable-diffusion", "Checkpoint"), ("unet", "UNet"),
    ("diffusion_models", "UNet"), ("diffusers", "Diffusers"),
    ("vae", "VAE"), ("controlnet", "ControlNet"), ("clip_vision", "CLIP Vision"), ("clip", "CLIP"),
    ("text_encoders", "Text Encoder"), ("embeddings", "Embedding"), ("upscale_models", "放大模型"),
    ("esrgan", "放大模型"), ("hypernetworks", "Hypernetwork"), ("style_models", "Style"),
    ("gligen", "GLIGEN"), ("photomaker", "PhotoMaker"),
)
# kohya 元数据键 → 好读的名字
META_LABELS = {
    "ss_base_model_version": "底模版本",
    "ss_sd_model_name": "训练底模",
    "ss_network_module": "网络类型",
    "ss_network_dim": "dim",
    "ss_network_alpha": "alpha",
    "ss_num_train_images": "训练图片数",
    "ss_epoch": "训练轮数",
    "ss_steps": "训练步数",
    "ss_resolution": "训练分辨率",
    "ss_learning_rate": "学习率",
    "ss_optimizer": "优化器",
    "ss_output_name": "输出名",
    "ss_clip_skip": "CLIP skip",
    "ss_v2": "SD2",
    "modelspec.title": "标题",
    "modelspec.description": "说明",
    "modelspec.architecture": "架构",
    "modelspec.author": "作者",
    "modelspec.license": "许可",
    "ss_pretrained_model_name_or_path": "预训练模型",
    "ss_dataset_dirs": "数据集",
}

TRIGGER_KEYS = ("ss_trained_words", "trained_words", "activation text", "activation_text", "trigger_words")


def read_header(path: str | Path, max_header: int = 64 * 1024 * 1024) -> Dict[str, Any]:
    """只读 safetensors 头部，返回 {metadata, tensor_keys, header_bytes}。.ckpt/.pt 等老格式返回空。"""
    p = Path(path)
    out: Dict[str, Any] = {"metadata": {}, "tensor_keys": [], "error": ""}
    if p.suffix.lower() != ".safetensors":
        return out
    try:
        with p.open("rb") as f:
            head = f.read(8)
            if len(head) < 8:
                out["error"] = "文件太小"
                return out
            n = struct.unpack("<Q", head)[0]
            if n <= 0 or n > max_header:
                out["error"] = "头部长度异常：%d" % n
                return out
            blob = f.read(n)
        j = json.loads(blob.decode("utf-8", "replace"))
    except Exception as exc:                                     # noqa: BLE001
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
        return out
    if not isinstance(j, dict):
        return out
    meta = j.pop("__metadata__", None) or {}
    if isinstance(meta, dict):
        out["metadata"] = {str(k): (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
                           for k, v in meta.items()}
    out["tensor_keys"] = sorted(j.keys())[:60]
    out["tensor_count"] = len(j)
    return out


def _kind_of(path: Path) -> str:
    parts = [x.lower() for x in path.parts[-4:]] + [path.name.lower()]
    for key, kind in DIR_KINDS:
        for part in parts[:-1]:
            if key in part:
                return kind
    return "模型"


def trigger_words(meta: Dict[str, str], top: int = 12) -> List[str]:
    """触发词：优先显式字段，其次从 ss_tag_frequency 的词频里取高频标签。"""
    for k in TRIGGER_KEYS:
        v = next((meta[mk] for mk in meta if mk.lower() == k.lower()), "")
        if v:
            items = re.split(r"[,\n]+", v) if not v.strip().startswith("{") else []
            if not items:
                try:
                    j = json.loads(v)
                    items = list(j.keys()) if isinstance(j, dict) else [str(j)]
                except Exception:                                # noqa: BLE001
                    items = re.split(r"[,\n]+", v)
            words = [s.strip() for s in items if s and s.strip()]
            if words:
                return words[:top]
    freq = next((meta[mk] for mk in meta if mk.lower() == "ss_tag_frequency"), "")
    if not freq:
        return []
    try:
        data = json.loads(freq)
    except Exception:                                            # noqa: BLE001
        return []
    counter: Dict[str, float] = {}
    if isinstance(data, dict):
        for _ds, tags in data.items():
            if isinstance(tags, dict):
                for tag, cnt in tags.items():
                    try:
                        counter[str(tag)] = counter.get(str(tag), 0) + float(cnt)
                    except (TypeError, ValueError):
                        counter[str(tag)] = counter.get(str(tag), 0) + 1
    return [t for t, _ in sorted(counter.items(), key=lambda kv: -kv[1])[:top]]


def find_preview(path: Path) -> str:
    stem = path.with_suffix("")
    for ext in PREVIEW_EXTS:
        cand = Path(str(stem) + ext)
        if cand.is_file():
            return str(cand)
    return ""


def model_info(path: str | Path) -> Dict[str, Any]:
    """单个模型文件的信息（模型库列表用）。"""
    p = Path(path)
    info: Dict[str, Any] = {"path": str(p), "name": p.name, "dir": str(p.parent),
                            "kind": _kind_of(p), "ext": p.suffix.lower().lstrip(".")}
    try:
        st = p.stat()
        info["bytes"] = st.st_size
        info["mtime"] = st.st_mtime
    except OSError:
        info["bytes"] = 0
        info["mtime"] = 0
    head = read_header(p)
    meta = head.get("metadata") or {}
    info["metadata"] = meta
    info["meta_error"] = head.get("error", "")
    info["tensors"] = head.get("tensor_count", 0)
    info["triggers"] = trigger_words(meta)
    info["base_model"] = (meta.get("ss_base_model_version") or meta.get("modelspec.architecture")
                          or meta.get("ss_sd_model_name") or "")
    info["preview"] = find_preview(p)
    info["labels"] = {META_LABELS[k]: v for k, v in meta.items() if k in META_LABELS}
    if not meta and info["kind"] == "Embedding" and p.suffix.lower() in (".pt", ".bin"):
        info["note"] = "老式 embedding 文件（.pt），看不到触发词"
    return info


def scan_models(dirs: Iterable[str], recursive: bool = True, limit: int = 20000) -> Dict[str, Any]:
    """扫描若干目录下的模型文件（只读文件头，快）。"""
    found: List[Dict[str, Any]] = []
    roots: List[str] = []
    errors: List[str] = []
    for d in dirs:
        d = str(d or "").strip()
        if not d:
            continue
        p = Path(d)
        if not p.is_dir():
            errors.append("目录不存在：%s" % d)
            continue
        roots.append(str(p))
        it = p.rglob("*") if recursive else p.glob("*")
        for f in it:
            if len(found) >= limit:
                break
            try:
                if not f.is_file() or f.suffix.lower() not in MODEL_EXTS:
                    continue
                if any(part.endswith("_cache") or part == "__pycache__" for part in f.parts):
                    continue
            except OSError:
                continue
            found.append(model_info(f))
    found.sort(key=lambda m: (m["kind"], m["name"].lower()))
    return {"ok": True, "roots": roots, "errors": errors, "count": len(found), "models": found}


def summarize(models: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_kind: Dict[str, int] = {}
    by_base: Dict[str, int] = {}
    no_trigger = 0
    for m in models:
        by_kind[m["kind"]] = by_kind.get(m["kind"], 0) + 1
        base = (m.get("base_model") or "未知").split(".")[0][:28]
        by_base[base] = by_base.get(base, 0) + 1
        if not m.get("triggers"):
            no_trigger += 1
    top = sorted(by_kind.items(), key=lambda kv: -kv[1])
    return {"total": len(models), "kinds": [{"kind": k, "n": v} for k, v in top],
            "bases": [{"base": k, "n": v} for k, v in sorted(by_base.items(), key=lambda kv: -kv[1])[:10]],
            "no_trigger": no_trigger,
            "bytes": sum(int(m.get("bytes") or 0) for m in models)}


def search(models: List[Dict[str, Any]], q: str = "", kind: str = "", base: str = "",
           only_triggered: bool = False) -> List[Dict[str, Any]]:
    """在已扫描结果里过滤（模型数量级不大，内存过滤足够快）。"""
    q = (q or "").strip().lower()
    out = []
    for m in models:
        if kind and mode_kind(m, kind) is False:
            continue
        if base and base.lower() not in str(m.get("base_model") or "").lower():
            continue
        if only_triggered and not m.get("triggers"):
            continue
        if q:
            blob = " ".join([m.get("name", ""), m.get("dir", ""), str(m.get("base_model") or ""),
                             " ".join(m.get("triggers") or []),
                             " ".join(str(v) for v in (m.get("metadata") or {}).values())[:4000]]).lower()
            if q not in blob:
                continue
        out.append(m)
    return out


def mode_kind(m: Dict[str, Any], kind: str) -> bool:
    return kind.lower() in str(m.get("kind") or "").lower()
