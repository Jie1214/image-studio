# -*- coding: utf-8 -*-
"""读图参数：从图片元数据里还原「模型 / LoRA / 提示词 / 采样参数」。

支持三类来源：
  1. ComfyUI —— PNG 的 tEXt 块 prompt / workflow（API 格式 JSON），或 JPEG/WebP 的 EXIF UserComment
  2. A1111 / Forge / SD.Next —— `parameters` 文本（含 <lora:xxx:0.8> 写法）
  3. 兜底 —— 直接在文件字节里搜 ComfyUI 的 JSON 特征（class_type / nodes），不依赖容器类型
"""
from __future__ import annotations

import json
import re
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image

# 有些图的 EXIF 段是截断的，Pillow 会往控制台刷 UserWarning；我们本来就是「尽力而为」地读元数据，
# 读不到就算了，不需要污染运行窗口。
warnings.filterwarnings("ignore", message=r".*Corrupt EXIF data.*", category=UserWarning)

# 节点类名 → (归类, 取文件名的输入键)
LOADER_MAP: Dict[str, tuple] = {
    "CheckpointLoaderSimple": ("Checkpoint", "ckpt_name"),
    "CheckpointLoader": ("Checkpoint", "ckpt_name"),
    "ImageOnlyCheckpointLoader": ("Checkpoint", "ckpt_name"),
    "unCLIPCheckpointLoader": ("Checkpoint", "ckpt_name"),
    "UNETLoader": ("UNet / diffusion model", "unet_name"),
    "DiffusionModelLoader": ("UNet / diffusion model", "model_name"),
    "LoraLoader": ("LoRA", "lora_name"),
    "LoraLoaderModelOnly": ("LoRA", "lora_name"),
    "VAELoader": ("VAE", "vae_name"),
    "CLIPLoader": ("CLIP / text encoder", "clip_name"),
    "DualCLIPLoader": ("CLIP / text encoder", "clip_name1"),
    "TripleCLIPLoader": ("CLIP / text encoder", "clip_name1"),
    "CLIPVisionLoader": ("CLIP Vision", "clip_name"),
    "ControlNetLoader": ("ControlNet", "control_net_name"),
    "DiffControlNetLoader": ("ControlNet", "control_net_name"),
    "UpscaleModelLoader": ("放大模型", "model_name"),
    "IPAdapterModelLoader": ("IPAdapter", "ipadapter_file"),
    "IPAdapterUnifiedLoader": ("IPAdapter 预设", "preset"),
    "StyleModelLoader": ("风格模型", "style_model_name"),
    "GLIGENLoader": ("GLIGEN", "gligen_name"),
    "PhotoMakerLoader": ("PhotoMaker", "photomaker_model_name"),
    "InstantIDModelLoader": ("InstantID", "instantid_file"),
    "AnimateDiffLoaderWithContext": ("AnimateDiff", "model_name"),
}

# 需要跟着往下走的「透传」条件节点（把 conditioning 串起来）
COND_PASS = {
    "ConditioningCombine", "ConditioningConcat", "ConditioningAverage", "ConditioningSetArea",
    "ConditioningSetAreaPercentage", "ConditioningSetTimestepRange", "ConditioningSetMask",
    "ConditioningZeroOut", "ControlNetApply", "ControlNetApplyAdvanced", "ConditioningSetAreaStrength",
    "ConditioningSetTimestepRange", "ConditioningPostProcess", "ConditioningSetProperties",
}
TEXT_KEYS = ("text", "text_g", "text_l", "string", "prompt", "positive_prompt", "value", "prompt_text")
SAMPLERS = {"KSampler", "KSamplerAdvanced", "SamplerCustom", "SamplerCustomAdvanced", "KSamplerSelect"}
# 优先取的文本键（IMPACT 的 populated_text 是"已展开"的提示词，比原始 wildcard 更准）
PREFERRED_TEXT_KEYS = ("populated_text", "text", "text_g", "text_l", "positive_prompt", "wildcard_text",
                       "prompt", "string", "caption", "prompt_text", "value")
# 键名像文本的（wildcard_text / populated_text / caption…）：用「包含」判，不再只认固定几个名字
TEXT_KEY_RE = re.compile(r"(text|prompt|wildcard|caption|string|subtitle|note)", re.I)
# 明显不是文本的键，避免把模型名 / 尺寸 / 权重当提示词
NON_TEXT_KEY_RE = re.compile(r"(model|clip|vae|lora|sampler|scheduler|seed|steps|cfg|denoise|width|height|batch"
                             r"|image|mask|latent|control|bbox|device|precision|dtype|mode|strength|threshold"
                             r"|resize|aspect|ratio|megapixel|name|path|file|dir)", re.I)
# 字符串中转类节点（easy showAnything / Any Switch (rgthree) / String Literal…）：提示词可能靠它们转过来
STRING_SOURCE_RE = re.compile(r"(string|text|show|switch|primitive|wildcard|prompt|concat|join)", re.I)
# 采样器识别用的输入名（不认类名，认接线：自定义/改名的采样节点也能认出来）
SAMPLER_PARAM_KEYS = ("seed", "noise_seed", "steps", "cfg", "sampler_name", "scheduler", "denoise", "eta")
MODEL_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".png", ".jpg", ".jpeg", ".webp", ".onnx", ".gguf")
# 容器 info 里**不是**生成参数的技术键，取 tEXt 时跳过；其余键一律收（SwarmUI/InvokeAI 的键名各不相同）
NON_TEXT_INFO = {
    "exif", "xmp", "icc_profile", "transparency", "gamma", "dpi", "duration", "loop", "jfif", "jfif_version",
    "jfif_unit", "jfif_density", "adobe", "adobe_transform", "photoshop", "progression", "mp", "aspect",
    "background", "interlace", "srgb", "chromaticity", "bits", "compression", "dpi_info", "timestamp",
    "date:create", "date:modify", "png:text", "ihdr", "_compression",
}


def _sampler_like(ct: str, ins: Dict[str, Any]) -> bool:
    """是不是采样节点：按「有没有 positive/negative 接线 + 采样参数」判断，而不是认类名。

    KSampler_A1111 / SamplerCustom 改名版 / 带前缀的自定义采样器都能命中；
    ControlNetApply 这类只有 positive/negative 没有 seed/steps 的透传节点不会被误认。
    """
    if ct in SAMPLERS:
        return True
    if _is_link(ins.get("positive")) and _is_link(ins.get("negative")):
        return any(k in ins for k in ("seed", "noise_seed", "steps", "cfg", "sampler_name", "scheduler"))
    return False


def _is_link(v: Any) -> bool:
    return isinstance(v, (list, tuple)) and len(v) == 2 and isinstance(v[0], (str, int))


def _decode_exif_text(b: bytes) -> str:
    """EXIF UserComment：先剥编码前缀，再按可能的编码解出来（优先能当 JSON 解析的那种）。"""
    if not b:
        return ""
    for pre in (b"ASCII\x00\x00\x00", b"UNICODE\x00", b"UTF8\x00", b"JIS\x00\x00\x00\x00\x00"):
        if b.startswith(pre):
            b = b[len(pre):]
            break
    b = b.strip(b"\x00 \r\n\t")
    cands = []
    for enc in ("utf-8", "utf-16-le", "utf-16-be", "latin-1"):
        try:
            s = b.decode(enc, "ignore").strip("\x00 \r\n\t")
        except Exception:
            continue
        if not any(c.isalnum() for c in s):
            continue
        cands.append(s)
    for s in cands:            # 能当 JSON 看的优先
        t = s.lstrip()
        if t.startswith("{") or t.startswith("["):
            return s
    return cands[0] if cands else ""


def _pretty_bytes_text(b: bytes) -> str:
    if not b:
        return ""
    if b.startswith((b"ASCII", b"UNICODE", b"UTF8", b"JIS")) or b"\x00" in b[:8]:
        return _decode_exif_text(b)
    s = b.decode("utf-8", "ignore").strip("\x00 \r\n\t")
    if not any(c.isalnum() for c in s):
        s = b.decode("latin-1", "ignore").strip("\x00 \r\n\t")
    return s


def _loads_tolerant(s: str) -> Any:
    """容忍尾部垃圾/补齐空字节的 JSON 解析。"""
    s = s.strip().strip("\x00")
    try:
        return json.loads(s)
    except Exception:
        pass
    start = s.find("{")
    while start != -1:
        try:
            obj, _end = json.JSONDecoder().raw_decode(s[start:])
            return obj
        except Exception:
            start = s.find("{", start + 1)
    raise ValueError("找不到可解析的 JSON")


def raw_metadata(path: str | Path, deep: bool = True) -> Dict[str, Any]:
    """取出容器里的原始元数据：PNG tEXt、EXIF、XMP，外加字节兜底（deep=False 时不扫字节，快）。"""
    path = Path(path)
    out: Dict[str, Any] = {"keys": [], "text": {}, "exif": {}, "xmp": ""}
    try:
        with Image.open(path) as im:
            out["format"] = im.format or ""
            out["size"] = list(im.size)
            out["mode"] = im.mode
            for k, raw_v in (im.info or {}).items():
                if str(k).lower() in NON_TEXT_INFO:
                    continue
                v = raw_v
                if isinstance(v, bytes):
                    v = _pretty_bytes_text(v)
                if isinstance(v, str) and v.strip():
                    out["text"][str(k)] = v[:2_000_000]
                elif isinstance(v, (int, float)) and not isinstance(v, bool):
                    out["text"][str(k)] = str(v)
            exp = im.info.get("exif")
            if exp and isinstance(exp, bytes):
                try:
                    ex = Image.Exif()
                    ex.load(exp)
                    for tid, name in ((0x010E, "ImageDescription"), (0x013B, "Artist"),
                                      (0x0131, "Software"), (0x9286, "UserComment"), (0x010F, "Make")):
                        v = ex.get(tid)
                        if isinstance(v, bytes):
                            v = _pretty_bytes_text(v)
                        if v:
                            out["exif"][name] = v
                    if not out["exif"]:
                        out["exif"] = {"_tags": len(ex)}
                except Exception:
                    pass
            try:
                ex = im.getexif()
                for tid, name in ((0x010E, "ImageDescription"), (0x9286, "UserComment"), (0x0131, "Software")):
                    v = ex.get(tid)
                    if isinstance(v, bytes):
                        v = _pretty_bytes_text(v)
                    if v:
                        out["exif"].setdefault(name, v)
            except Exception:
                pass
            xmp = im.info.get("xmp")
            if isinstance(xmp, bytes):
                xmp = xmp.decode("utf-8", "ignore")
            if xmp:
                out["xmp"] = xmp[:20000]
    except Exception as exc:      # noqa: BLE001
        out["error"] = "%s: %s" % (type(exc).__name__, exc)

    # 字节兜底：有些容器（或二次处理过的图）Pillow 读不到 tEXt，但 JSON 还在文件里
    # deep=False（批量分类）时跳过这步：要读整文件，几千张会很慢；PNG/JPEG 的常规路径已够判定
    if deep:
        try:
            blob = path.read_bytes()
            idx = blob.find(b'"class_type"')
            if idx != -1:
                best, best_n = "", 0
                # 从最近的若干个 '{' 往前试，取「含 class_type 最多」的那个对象（避免只抓到一个子节点）
                starts = []
                pos = idx
                for _ in range(12):
                    pos = blob.rfind(b"{", 0, pos if pos > 0 else 0)
                    if pos == -1:
                        break
                    starts.append(pos)
                    pos = pos - 1 if pos > 0 else 0
                for st in starts:
                    depth, end = 0, -1
                    limit = min(len(blob), st + 40_000_000)
                    for i in range(st, limit):
                        c = blob[i]
                        if c == 0x7B:
                            depth += 1
                        elif c == 0x7D:
                            depth -= 1
                            if depth == 0:
                                end = i + 1
                                break
                    if end <= st:
                        continue
                    chunk = blob[st:end].decode("utf-8", "ignore")
                    n = chunk.count('"class_type"')
                    if n > best_n:
                        best, best_n = chunk, n
                if best and best_n > 0:
                    out["byte_json"] = best
        except Exception:
            pass

    out["keys"] = list(out["text"].keys()) + (["xmp"] if out["xmp"] else [])
    return out


# 透传节点的「输出槽位 → 输入键」映射：不映射就会把正向链的文本带到负向去
OUT_SLOT_INPUT: Dict[str, Dict[int, Any]] = {
    "ControlNetApplyAdvanced": {0: "positive", 1: "negative"},
    "ControlNetApply": {0: "conditioning"},
    "ConditioningCombine": {0: ("conditioning_1", "conditioning_2")},
    "ConditioningConcat": {0: ("conditioning_to", "conditioning_from")},
    "ConditioningAverage": {0: ("conditioning_to", "conditioning_from")},
    "ConditioningSetArea": {0: "conditioning"},
    "ConditioningSetAreaPercentage": {0: "conditioning"},
    "ConditioningSetAreaStrength": {0: "conditioning"},
    "ConditioningSetMask": {0: "conditioning"},
    "ConditioningSetTimestepRange": {0: "conditioning"},
    "ConditioningSetProperties": {0: "conditioning"},
    "ConditioningZeroOut": {0: "conditioning"},
    "ConditioningPostProcess": {0: "conditioning"},
    "ConditioningSetTimestepRange": {0: "conditioning"},
}


def _follow_text(nodes: Dict[str, Any], node_id: str, slot: Optional[int] = None,
                 depth: int = 0, seen: Optional[set] = None) -> List[str]:
    """顺着 conditioning 链找到提示词文本（按输出槽位走，避免正负串味）。"""
    if depth > 14:
        return []
    seen = seen or set()
    key = (str(node_id), slot)
    if key in seen:
        return []
    seen.add(key)
    node = nodes.get(str(node_id))
    if not isinstance(node, dict):
        return []
    ct = node.get("class_type", "")
    ins = node.get("inputs", {}) or {}

    # ConditioningZeroOut / 同类"清空"节点：这一侧压根没有提示词，别再顺着链把上游文本捞过来
    if re.search(r"zero\s*out|zeroconditioning|conditioningzero", ct, re.I):
        return []

    # 已知槽位映射的透传节点：只跟着对应的那条链走
    if slot is not None and ct in OUT_SLOT_INPUT:
        mapped = OUT_SLOT_INPUT[ct].get(slot)
        if mapped is not None:
            keys = mapped if isinstance(mapped, tuple) else (mapped,)
            texts: List[str] = []
            for k in keys:
                v = ins.get(k)
                if _is_link(v):
                    texts += _follow_text(nodes, str(v[0]), int(v[1]), depth + 1, seen)
                elif isinstance(v, str) and v.strip():
                    texts.append(v.strip())
            return [t for t in texts if t]

    texts = []
    # 认不出的透传节点（InpaintModelConditioning / 自定义 Conditioning 包装）：按惯例 输出槽 0=positive、1=negative
    if slot is not None and ct not in OUT_SLOT_INPUT and _is_link(ins.get("positive")) and _is_link(ins.get("negative")):
        _k = "positive" if slot == 0 else ("negative" if slot == 1 else None)
        if _k and _is_link(ins.get(_k)):
            return _follow_text(nodes, str(ins[_k][0]), int(ins[_k][1]), depth + 1, seen)
    # ---- 找文本 ----
    is_lora_tag = "lora" in ct.lower()          # LoRA 标签加载器的 text 是 <lora:…>，不算提示词
    is_text_node = (ct.startswith("CLIPTextEncode") or "CLIPTextEncode" in ct or "textencode" in ct.lower()
                    or "wildcard" in ct.lower())
    if not is_lora_tag:
        # 1) 节点自带字符串文本：text / text_g / wildcard_text / populated_text / caption…（键名用「包含」判）
        for k in PREFERRED_TEXT_KEYS + tuple(ins.keys()):
            if k not in ins:
                continue
            v = ins[k]
            if not isinstance(v, str) or not v.strip():
                continue
            if k in PREFERRED_TEXT_KEYS or (TEXT_KEY_RE.search(k) and not NON_TEXT_KEY_RE.search(k)):
                t = v.strip()
                if t.lower().endswith(MODEL_EXTS) or t == "ECHO_EMPTY":
                    continue
                texts.append(t)
                break
    if texts:
        return [t for t in texts if t]
    if not is_lora_tag:
        # 2) 文本是链接给的：easy showAnything / Any Switch (rgthree) / String Literal 这类中转节点
        class_is_string = bool(STRING_SOURCE_RE.search(ct))
        for k, v in ins.items():
            if not _is_link(v):
                continue
            target = nodes.get(str(v[0])) or {}
            tct = target.get("class_type", "")
            key_ok = bool(TEXT_KEY_RE.search(k) and not NON_TEXT_KEY_RE.search(k))
            if key_ok or STRING_SOURCE_RE.search(tct) or (class_is_string and not NON_TEXT_KEY_RE.search(k)):
                got = _follow_text(nodes, str(v[0]), int(v[1]), depth + 1, seen)
                if got:
                    return got
    if is_text_node and not is_lora_tag:
        return []           # 明确的文本节点但确实没文本，别再乱跟
    # 3) 兜底：不认识又没文本的节点，按顺序跟所有 link（并收下像提示词的长字符串）
    for k, v in ins.items():
        if _is_link(v):
            texts += _follow_text(nodes, str(v[0]), int(v[1]), depth + 1, seen)
        elif isinstance(v, str) and (k in TEXT_KEYS or TEXT_KEY_RE.search(k)) and len(v.strip()) > 1 \
                and not v.lower().endswith(MODEL_EXTS):
            if v.strip() not in texts:
                texts.append(v.strip())
    return [t for t in texts if t]


def parse_comfy(prompt_json: str) -> Dict[str, Any]:
    try:
        data = _loads_tolerant(prompt_json)
    except Exception as exc:      # noqa: BLE001
        return {"error": "prompt JSON 解析失败：%s" % exc}
    if isinstance(data, dict) and "nodes" in data:      # 传进来的是 UI workflow
        return {"error": "这是 UI workflow，不是可解析的 API prompt"}
    nodes = {str(k): v for k, v in data.items() if isinstance(v, dict)}
    models: List[Dict[str, str]] = []
    loras: List[Dict[str, Any]] = []
    kinds: Dict[str, List[str]] = {}
    census: Dict[str, int] = {}
    for nid, node in nodes.items():
        ct = node.get("class_type", "?")
        census[ct] = census.get(ct, 0) + 1
        ins = node.get("inputs", {}) or {}
        if ct in LOADER_MAP:
            kind, key = LOADER_MAP[ct]
            val = ins.get(key)
            names = []
            if isinstance(val, str):
                names = [val]
            else:      # DualCLIPLoader / TripleCLIPLoader 的 clip_name2/3
                for k2 in ("clip_name1", "clip_name2", "clip_name3"):
                    if isinstance(ins.get(k2), str):
                        names.append(ins[k2])
            for n in names:
                if not n:
                    continue
                if kind == "LoRA":
                    loras.append({
                        "name": n,
                        "strength_model": ins.get("strength_model"),
                        "strength_clip": ins.get("strength_clip"),
                        "node": nid,
                    })
                else:
                    kinds.setdefault(kind, [])
                    if n not in kinds[kind]:
                        kinds[kind].append(n)
    # 采样参数 + 从采样器回溯提示词
    sampler: Dict[str, Any] = {}
    positive: List[str] = []
    negative: List[str] = []
    for nid, node in nodes.items():
        ct = node.get("class_type", "")
        ins = node.get("inputs", {}) or {}
        if not _sampler_like(ct, ins):
            continue
        if not sampler:
            for k_in, k_out in (("seed", "seed"), ("noise_seed", "seed"), ("steps", "steps"), ("cfg", "cfg"),
                                ("sampler_name", "sampler_name"), ("scheduler", "scheduler"), ("denoise", "denoise")):
                if k_in in ins and not isinstance(ins[k_in], (list, tuple)):
                    sampler[k_out] = ins[k_in]
        if _is_link(ins.get("positive")):
            positive += _follow_text(nodes, str(ins["positive"][0]), int(ins["positive"][1]))
        if _is_link(ins.get("negative")):
            negative += _follow_text(nodes, str(ins["negative"][0]), int(ins["negative"][1]))
    # 兜底 1：没接出正向提示词时（例如正向被 ConditioningZeroOut 抹掉），直接找文本编码节点，别让图里的提示词白丢
    if not positive:
        for nid, node in nodes.items():
            ct = node.get("class_type", "")
            if "lora" in ct.lower():
                continue
            ins = node.get("inputs", {}) or {}
            looks_text = (ct.startswith("CLIPTextEncode") or "CLIPTextEncode" in ct or "textencode" in ct.lower()
                          or "wildcard" in ct.lower()
                          or any(TEXT_KEY_RE.search(k) and not NON_TEXT_KEY_RE.search(k)
                                 and isinstance(v, str) and v.strip() for k, v in ins.items()))
            if not looks_text:
                continue
            for t in _follow_text(nodes, str(nid), None):     # 顺着链接跟（文本可能来自中转节点）
                if t and t not in negative and t not in positive:
                    positive.append(t)
                    break
    # 兜底 2：种子可能不在采样节点上（rgthree 的 Seed / SetSubseeds 这类独立种子节点）
    if "seed" not in sampler:
        for nid, node in nodes.items():
            ins = node.get("inputs", {}) or {}
            v = ins.get("seed", ins.get("noise_seed"))
            if isinstance(v, int):
                sampler["seed"] = v
                break
    # 尺寸：只认真正的数字（宽度来自节点链接时不能把 ["82",0] 这种链接写进参数）
    def _sz(ins: Dict[str, Any]):
        w, h = ins.get("width"), ins.get("height")
        if isinstance(w, int) and isinstance(h, int) and w and h:
            return w, h, ins.get("batch_size")
        return None
    for nid, node in nodes.items():
        ct = node.get("class_type", "")
        if ct.startswith("EmptyLatent") or ct in ("EmptySD3LatentImage", "EmptyLatentImagePresets", "EmptyImage"):
            got = _sz(node.get("inputs", {}) or {})
            if got:
                sampler.setdefault("width", got[0]); sampler.setdefault("height", got[1])
                if got[2] and got[2] != 1:
                    sampler["batch_size"] = got[2]
                break
    if "width" not in sampler:                       # 分辨率来自 TTResolutionSelector / 自定义尺寸节点
        for nid, node in nodes.items():
            ct = node.get("class_type", "")
            if not any(t in ct for t in ("Resolution", "Size", "Wh", "WH", "Aspect")):
                continue
            got = _sz(node.get("inputs", {}) or {})
            if got:
                sampler.setdefault("width", got[0]); sampler.setdefault("height", got[1])
                break

    def _dedup(seq: List[str]) -> List[str]:
        out: List[str] = []
        for s in seq:
            if s and s not in out:
                out.append(s)
        return out

    pos = _dedup(positive)
    neg = _dedup(negative)
    emb = sorted({m.group(1) for t in pos + neg for m in re.finditer(r"embedding:([\w\.\-]+)", t, re.I)})
    return {
        "tool": "ComfyUI",
        "models": [{"kind": k, "name": n} for k, names in kinds.items() for n in names],
        "loras": loras,
        "kinds": kinds,
        "positive": "\n".join(pos),
        "negative": "\n".join(neg),
        "embeddings": emb,
        "sampler": sampler,
        "node_census": dict(sorted(census.items(), key=lambda kv: -kv[1])),
        "total_nodes": len(nodes),
    }


A1111_RE = re.compile(r"^([^:]+):\s*(.+)$", re.M)
LORA_TAG = re.compile(r"<lora:([^:>]+)(?::([-\d\.]+))?(?::([-\d\.]+))?>", re.I)


def parse_a1111(text: str) -> Dict[str, Any]:
    lines = text.replace("\r\n", "\n")
    neg = ""
    m = re.search(r"\nNegative prompt:\s*(.*?)(?=\n[A-Z][\w ]*:|\Z)", lines, re.S)
    if m:
        neg = m.group(1).strip()
        pos = lines[:m.start()].strip()
    else:
        cut = re.search(r"\n[A-Z][\w ]*:\s", lines)
        pos = (lines[:cut.start()] if cut else lines).strip()
    meta_line = lines[m.end():] if m else (lines[cut.start():] if cut else "")
    if not meta_line:
        meta_line = "\n".join(l for l in lines.split("\n") if re.match(r"^\s*(Steps|Sampler|CFG scale|Seed|Size|Model|Model hash|Denoising|Clip skip|ENSD|Hires upscale|VAE):", l))
    fields: Dict[str, str] = {}
    # A1111 的采样参数都挤在同一行：按「键: 值」逐段切（值里可能有引号包住的逗号）
    for fm in re.finditer(r'([A-Za-z][A-Za-z0-9 _\-]*):\s*("[^"]*"|[^,]*)(?:,\s*|$)', meta_line):
        k = fm.group(1).strip()
        v = fm.group(2).strip().strip('"')
        if k and v and k not in fields and not k.lower().startswith(("negative prompt",)):
            fields[k] = v
    loras = []
    for name, sw, sc in LORA_TAG.findall(pos + " " + neg):
        loras.append({"name": name.strip(), "strength_model": float(sw) if sw else None,
                      "strength_clip": float(sc) if sc else None, "node": "prompt"})
    if "Lora hashes" in fields:
        for pair in fields["Lora hashes"].split(","):
            nm = pair.split(":")[0].strip().strip('"')
            if nm and not any(l["name"] == nm for l in loras):
                loras.append({"name": nm, "strength_model": None, "strength_clip": None, "node": "Lora hashes"})
    sampler: Dict[str, Any] = {}
    for src, dst in (("Steps", "steps"), ("Sampler", "sampler_name"), ("CFG scale", "cfg"), ("Seed", "seed"),
                     ("Denoising strength", "denoise"), ("Schedule type", "scheduler"), ("Clip skip", "clip_skip")):
        if src in fields:
            val = fields[src]
            try:
                sampler[dst] = float(val) if "." in val else int(val)
            except ValueError:
                sampler[dst] = val
    if "Size" in fields:
        mm = re.match(r"(\d+)\s*x\s*(\d+)", fields["Size"])
        if mm:
            sampler["width"], sampler["height"] = int(mm.group(1)), int(mm.group(2))
    models = []
    for src, kind in (("Model", "Checkpoint"), ("Model hash", "Checkpoint hash"), ("VAE", "VAE"),
                      ("VAE hash", "VAE hash")):
        if src in fields:
            models.append({"kind": kind, "name": fields[src]})
    emb = sorted({m.group(1) for m in re.finditer(r"embedding:([\w\.\-]+)", pos + " " + neg, re.I)})
    return {"tool": "A1111 / Forge / SD.Next", "models": models, "loras": loras, "kinds": {},
            "positive": pos, "negative": neg, "embeddings": emb, "sampler": sampler,
            "node_census": {}, "total_nodes": 0, "fields": fields}


def read_params(path: str | Path, deep: bool = True) -> Dict[str, Any]:
    """主入口：返回结构化结果（含原始元数据摘要）。deep=False 时不做整文件字节兜底（批量分类用，快）。"""
    path = Path(path)
    raw = raw_metadata(path, deep=deep)
    res: Dict[str, Any] = {
        "ok": False, "file": {
            "name": path.name, "path": str(path), "bytes": path.stat().st_size if path.exists() else 0,
            "format": raw.get("format", ""), "w": (raw.get("size") or [0, 0])[0], "h": (raw.get("size") or [0, 0])[1],
            "mode": raw.get("mode", ""),
        },
        "tool": "未检测到生成参数", "models": [], "loras": [], "kinds": {}, "positive": "", "negative": "",
        "embeddings": [], "sampler": {}, "node_census": {}, "total_nodes": 0,
        "meta_keys": raw.get("keys", []), "notes": [], "raw": {},
    }
    text = raw.get("text", {}) or {}
    exif = raw.get("exif", {}) or {}
    from .parsers_more import (a1111_extras, civitai_resources, loads_json, parse_invokeai, parse_novelai,
                               parse_sidecar, parse_swarmui, tool_from_parameters, workflow_to_prompt_graph)

    def _ci(d, *keys, default=""):
        if not isinstance(d, dict):
            return default
        low = {str(k).lower(): v for k, v in d.items()}
        for k in keys:
            v = low.get(str(k).lower())
            if v not in (None, "", b""):
                return v
        return default

    parameters = _ci(text, "parameters")
    workflow_json = _ci(text, "workflow")
    prompt_txt = _ci(text, "prompt")
    uc = exif.get("UserComment") or exif.get("ImageDescription") or ""

    # 收集所有可能的 ComfyUI prompt 候选，挑「可解析出节点最多」的那个（EXIF / tEXt / xmp / 字节兜底）
    candidates: List[tuple] = []
    if prompt_txt:
        candidates.append(("tEXt prompt", prompt_txt))
    if uc and "class_type" in uc:
        candidates.append(("EXIF UserComment", uc[uc.find("{"):]))
    if raw.get("xmp") and "class_type" in raw["xmp"]:
        candidates.append(("XMP", raw["xmp"][raw["xmp"].find("{"):]))
    if raw.get("byte_json"):
        candidates.append(("字节兜底", raw["byte_json"]))

    prompt_json, parsed = "", None
    for src, cand in candidates:
        got = parse_comfy(cand)
        n = got.get("total_nodes") or 0
        if not got.get("error") and n and (parsed is None or n > (parsed.get("total_nodes") or 0)):
            prompt_json, parsed = cand, got
            parsed["source"] = src
    if parsed is None and candidates:
        parsed = parse_comfy(candidates[0][1])

    if not parameters and uc and "Steps:" in uc and "class_type" not in uc:
        parameters = uc

    if parsed and not parsed.get("error"):
        res.update({k: v for k, v in parsed.items() if k != "kinds"})
        res["kinds"] = parsed.get("kinds", {})
        res["meta_source"] = parsed.get("source", "")
        res["ok"] = True
    elif parsed:
        res["notes"].append(parsed["error"])

    # 只有 UI workflow 的图：按节点 widget 还原成近似 API 图，再走同一套解析（以前这种图直接判「读不到」）
    if not res["ok"] and workflow_json:
        try:
            graph = workflow_to_prompt_graph(loads_json(workflow_json))
        except Exception:                                        # noqa: BLE001
            graph = None
        if graph:
            got = parse_comfy(json.dumps(graph, ensure_ascii=False))
            if not got.get("error") and got.get("total_nodes"):
                got["source"] = "UI workflow（按节点 widget 还原）"
                res.update({k: v for k, v in got.items() if k != "kinds"})
                res["kinds"] = got.get("kinds", {})
                res["meta_source"] = got["source"]
                res["ok"] = True
                prompt_json = json.dumps(graph, ensure_ascii=False)
                res["notes"].append("只存了 UI workflow：参数按节点 widget 顺序还原（自定义节点的高级设置可能不全）")

    if not parameters and uc and "Steps:" in uc and "class_type" not in uc:
        parameters = uc

    # 其他工具（顺序：越具体的越先试）
    if not res["ok"]:
        other = None
        sui = _ci(text, "sui_image_params") or None
        if sui is None and "sui_image_params" in parameters:
            sui = parameters
        if sui is not None:
            other = parse_swarmui({"sui_image_params": sui})
        if other is None:
            inv = _ci(text, "invokeai_metadata") or _ci(text, "sd-metadata") or _ci(text, "dream")
            if inv:
                other = parse_invokeai(inv)
        if other is None:
            nai = parse_novelai(_ci(text, "Comment"), _ci(text, "Description"))
            other = nai
        if other is None and not parameters:
            side = Path(path).with_suffix(".json")
            if side.is_file():
                try:
                    other = parse_sidecar(side.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    other = None
        if other and other.get("graph"):                         # 侧车 JSON 里是 ComfyUI 图
            got = parse_comfy(json.dumps(other["graph"], ensure_ascii=False))
            if not got.get("error") and got.get("total_nodes"):
                got["source"] = "侧车 JSON"
                other = got
        if other and (other.get("positive") or other.get("total_nodes")):
            res.update({k: v for k, v in other.items() if k not in ("kinds", "graph")})
            res["kinds"] = other.get("kinds", {})
            res["meta_source"] = (other.get("notes") or [""])[0] or other.get("tool", "")
            res["ok"] = True

    if not res["ok"] and parameters:
        parsed2 = parse_a1111(parameters)
        civ = civitai_resources(parameters)
        if civ["models"]:
            parsed2["models"] = civ["models"] + parsed2.get("models", [])
        if civ["loras"]:
            parsed2["loras"] = civ["loras"] + parsed2.get("loras", [])
        extra = a1111_extras(parameters)
        parsed2["fields"].update(extra)
        parsed2["tool"] = tool_from_parameters(parameters)
        if extra.get("controlnet"):
            parsed2["notes"].append("含 ControlNet：%d 个（参数在下面「提示」里）" % len(extra["controlnet"]))
        res.update({k: v for k, v in parsed2.items() if k != "kinds"})
        res["kinds"] = {}
        res["ok"] = True
        res["meta_source"] = "parameters 文本"

    if res["ok"]:
        res["tool"] = "ComfyUI" if "class_type" in (prompt_json or "") else res["tool"]
    # 元数据里有哪些键、多大，方便排错
    res["raw"] = {
        "prompt": (prompt_json[:200000] if prompt_json else ""),
        "workflow": (workflow_json[:200000] if workflow_json else ""),
        "parameters": parameters[:20000],
        "exif_keys": list(exif.keys()),
    }
    if not res["ok"] and not prompt_json and not workflow_json and not parameters and not exif:
        res["notes"].append("这张图没有任何生成元数据（可能是截图、手绘图，或导出时被平台抹掉了 EXIF）")
    if not workflow_json and res["tool"] == "ComfyUI":
        res["notes"].append("只有 API prompt（可解析），没有 UI workflow")
    return res
