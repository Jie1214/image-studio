# -*- coding: utf-8 -*-
"""其他 AI 画图工具的元数据解析。

只按各工具**公开的元数据格式**实现（键名/字段名属于事实层面），不复制任何第三方项目代码。
输出结构与 `app.metadata.parse_a1111` 保持一致，方便直接把结果并进统一结果里。

覆盖：
- SwarmUI      → PNG tEXt `sui_image_params`（对象，或塞在 `parameters` 的 JSON 里）
- InvokeAI     → `invokeai_metadata`（新）/ `sd-metadata`（旧）/ `dream`（更旧）
- NovelAI      → `Description`（纯文本提示词）+ `Comment`（JSON：采样参数 + 负向）
- Fooocus / Forge / SD.Next / Easy Diffusion / Draw Things → `parameters` 文本（A1111 风格，靠 Version 区分）
- 同名侧车 JSON（A1111「保存参数为 JSON」、ComfyUI 存图节点等）
- ComfyUI 只有 UI workflow 时 → 把 nodes+widgets_values 转成近似 API 图，复用现有解析
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

MODEL_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".onnx", ".gguf")
LORA_TAG = re.compile(r"<lora:([^:>]+)(?::([-\d\.]+))?(?::([-\d\.]+))?>", re.I)
NAN_RE = re.compile(r":\s*NaN\b")

# 从 parameters 文本认出具体是哪家的标识（Version 值 / 专有字段）
TOOL_HINTS = (
    ("fooocus", "Fooocus"),
    ("forge", "Forge"),
    ("sd.next", "SD.Next"),
    ("vlad", "SD.Next"),
    ("easy diffusion", "Easy Diffusion"),
    ("draw things", "Draw Things"),
    ("invoke", "InvokeAI"),
)


def _ci_get(d: Any, *keys: str, default: Any = None) -> Any:
    """大小写不敏感取值（PNG/iTXt 的键名大小写经常不一致）。"""
    if not isinstance(d, dict):
        return default
    low = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        v = low.get(str(k).lower())
        if v not in (None, "", b""):
            return v
    return default


def loads_json(v: Any) -> Any:
    """尽量把「对象或 JSON 字符串」变成对象；顺手把 JSON 里的 NaN 修掉。"""
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, (bytes, bytearray)):
        try:
            v = v.decode("utf-8", "replace")
        except Exception:                                        # noqa: BLE001
            return None
    if isinstance(v, str):
        s = v.strip()
        if s[:1] in "{[":
            try:
                return json.loads(NAN_RE.sub(": null", s))
            except Exception:                                    # noqa: BLE001
                return None
    return None


def _num(v: Any) -> Any:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        s = v.strip()
        try:
            return float(s) if "." in s or "e" in s.lower() else int(s)
        except ValueError:
            return s or None
    return None


def loras_from_text(text: str) -> List[Dict[str, Any]]:
    out = []
    for name, sw, sc in LORA_TAG.findall(text or ""):
        out.append({"name": name.strip(), "strength_model": float(sw) if sw else None,
                    "strength_clip": float(sc) if sc else None, "node": "prompt"})
    return out


def _sampler(d: Dict[str, Any], mapping) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for src, dst in mapping:
        v = _ci_get(d, src)
        if v is not None:
            n = _num(v)
            if n is not None:
                out[dst] = n
    return out


def _base(tool: str, pos: str, neg: str, models: List[Dict[str, str]], loras: List[Dict[str, Any]],
          sampler: Dict[str, Any], fields: Optional[Dict[str, Any]] = None, notes: Optional[List[str]] = None) -> Dict[str, Any]:
    emb = sorted({m.group(1) for m in re.finditer(r"embedding:([\w\.\-]+)", (pos or "") + " " + (neg or ""), re.I)})
    return {"tool": tool, "models": models, "loras": loras, "kinds": {}, "positive": pos or "", "negative": neg or "",
            "embeddings": emb, "sampler": sampler, "node_census": {}, "total_nodes": 0,
            "fields": fields or {}, "notes": notes or []}


# --------------------------------------------------------------------------- SwarmUI
def parse_swarmui(obj: Any) -> Optional[Dict[str, Any]]:
    """sui_image_params：SwarmUI 的标准元数据对象。"""
    data = obj
    if isinstance(obj, dict) and not _ci_get(obj, "sui_image_params", "prompt"):
        data = _ci_get(obj, "sui_image_params") or obj
    data = loads_json(data if not isinstance(data, dict) else _ci_get(data, "sui_image_params") or data) or data
    if not isinstance(data, dict) or not _ci_get(data, "prompt", "negativeprompt", "model", "steps"):
        return None
    pos = str(_ci_get(data, "prompt", default="") or "")
    neg = str(_ci_get(data, "negativeprompt", "negative_prompt", default="") or "")
    model = str(_ci_get(data, "model", default="") or "")
    models = [{"kind": "Checkpoint", "name": model}] if model else []
    loras: List[Dict[str, Any]] = []
    raw_loras = _ci_get(data, "loras", default=[]) or []
    if isinstance(raw_loras, str):
        raw_loras = [s.strip() for s in raw_loras.split(",") if s.strip()]
    for item in raw_loras:
        if isinstance(item, dict):
            nm = str(item.get("name") or item.get("lora") or "").strip()
            w = item.get("weight") if item.get("weight") is not None else item.get("strength")
        else:
            s = str(item).strip()
            nm, w = (s.rsplit(":", 1) + [None])[:2] if ":" in s else (s, None)
        if nm:
            loras.append({"name": nm, "strength_model": _num(w), "strength_clip": None, "node": "swarmui"})
    loras += [l for l in loras_from_text(pos + " " + neg) if not any(x["name"] == l["name"] for x in loras)]
    sampler = _sampler(data, (("seed", "seed"), ("steps", "steps"), ("cfgscale", "cfg"), ("cfg_scale", "cfg"),
                              ("sampler", "sampler_name"), ("scheduler", "scheduler"), ("width", "width"),
                              ("height", "height"), ("batchsize", "batch_size"), ("model", None)))
    sampler.pop(None, None)
    for k, v in (("width", _ci_get(data, "width")), ("height", _ci_get(data, "height"))):
        n = _num(v)
        if n:
            sampler[k] = n
    return _base("SwarmUI", pos, neg, models, loras, sampler, fields={"sui": True},
                 notes=["sui_image_params（SwarmUI）"])


# --------------------------------------------------------------------------- InvokeAI
def parse_invokeai(obj: Any) -> Optional[Dict[str, Any]]:
    data = loads_json(obj)
    if not isinstance(data, dict):
        return None
    # 新版 invokeai_metadata 直接是扁平对象；旧版 sd-metadata / dream 结构更深
    inner = _ci_get(data, "image", default=None)
    if isinstance(inner, dict):
        inner2 = loads_json(inner.get("metadata") if isinstance(inner.get("metadata"), str) else None) or inner
        data = inner2
    pos = str(_ci_get(data, "positive_prompt", "prompt", "positive", default="") or "")
    if not pos:
        p = _ci_get(data, "prompt")
        if isinstance(p, list):                                  # dream 结构：[{prompt: ..., weight: ...}]
            pos = ", ".join(str(x.get("prompt", "")).strip() for x in p if isinstance(x, dict))
        elif isinstance(p, str):
            pos = p
    neg = str(_ci_get(data, "negative_prompt", "negative", default="") or "")
    if not neg:
        up = _ci_get(data, "unconditioned_prompt")
        if isinstance(up, dict):
            neg = str(up.get("prompt") or "")
    model = str(_ci_get(data, "model", "model_name", "base_model", default="") or "")
    models = [{"kind": "Checkpoint", "name": model}] if model else []
    loras: List[Dict[str, Any]] = []
    for item in (_ci_get(data, "loras", default=[]) or []):
        nm, w = "", None
        if isinstance(item, dict):
            for k in ("name", "lora", "model_name"):
                v = item.get(k)
                if isinstance(v, str) and v.strip():
                    nm = v.strip()
                    break
            if not nm:
                sub = item.get("model")
                if isinstance(sub, dict):
                    nm = str(sub.get("name") or "").strip()
            w = item.get("weight")
            if w is None:
                w = item.get("strength")
        elif isinstance(item, str) and item.strip():
            nm = item.strip()
        if nm:
            loras.append({"name": nm, "strength_model": _num(w), "strength_clip": None, "node": "invokeai"})
    sampler = _sampler(data, (("seed", "seed"), ("steps", "steps"), ("cfg_scale", "cfg"), ("cfg", "cfg"),
                              ("scheduler", "scheduler"), ("width", "width"), ("height", "height"),
                              ("denoise", "denoise"), ("denoising_strength", "denoise"), ("strength", "denoise")))
    return _base("InvokeAI", pos, neg, models, loras, sampler, fields={"invokeai": True},
                 notes=["invokeai_metadata / sd-metadata（InvokeAI）"])


# --------------------------------------------------------------------------- NovelAI
def parse_novelai(comment: Any, description: str = "") -> Optional[Dict[str, Any]]:
    data = loads_json(comment)
    if not isinstance(data, dict):
        return None
    if not (_ci_get(data, "prompt", "uc", "steps", "sampler") is not None):
        return None
    pos = str(_ci_get(data, "prompt", default="") or "") or (description or "")
    neg = str(_ci_get(data, "uc", "negative_prompt", default="") or "")
    sampler = _sampler(data, (("seed", "seed"), ("steps", "steps"), ("scale", "cfg"), ("sampler", "sampler_name"),
                              ("width", "width"), ("height", "height")))
    if _ci_get(data, "qualityToggle") is not None:
        sampler["quality_toggle"] = bool(_ci_get(data, "qualityToggle"))
    fields = {k: v for k, v in (("ucPreset", _ci_get(data, "ucPreset")), ("sm", _ci_get(data, "sm")),
                                ("sm_dyn", _ci_get(data, "sm_dyn"))) if v is not None}
    return _base("NovelAI", pos, neg, [{"kind": "Model", "name": "NovelAI Diffusion"}], [], sampler,
                 fields=fields, notes=["Comment / Description（NovelAI）"])


# --------------------------------------------------------------------------- parameters 文本（A1111 系）
def tool_from_parameters(text: str) -> str:
    """从 parameters 文本里认工具（不认出来就是 A1111 系）。

    Forge 的版本号形如 `f2.0.1v1.10.1`（以 f+数字开头），A1111 本体是 `v1.10.1` —— 这是公开约定。
    """
    m = re.search(r"Version:\s*([^,\n]+)", text or "", re.I)
    ver = (m.group(1) if m else "").strip().lower()
    blob = (text or "").lower()
    for needle, name in TOOL_HINTS:
        if needle in ver or needle in blob[:400]:
            return name
    if re.match(r"^f\d", ver):
        return "Forge"
    if ver:
        return "A1111 / Forge / SD.Next"
    return "A1111 / Forge / SD.Next"


def civitai_resources(text: str) -> Dict[str, Any]:
    """A1111「Civitai resources」JSON：直接给模型 + LoRA 列表（比只看文件名准）。"""
    m = re.search(r"Civitai resources:\s*(\[[\s\S]*?\])\s*(?:,|$)", text or "")
    if not m:
        return {"models": [], "loras": []}
    try:
        arr = json.loads(m.group(1))
    except Exception:                                            # noqa: BLE001
        return {"models": [], "loras": []}
    models, loras = [], []
    for it in arr if isinstance(arr, list) else []:
        if not isinstance(it, dict):
            continue
        kind = str(it.get("type") or "").lower()
        name = str(it.get("modelName") or it.get("modelVersionName") or it.get("name") or "").strip()
        ver = str(it.get("modelVersionName") or "").strip()
        if not name:
            continue
        if kind == "lora":
            loras.append({"name": name, "version": ver, "strength_model": _num(it.get("weight")),
                          "strength_clip": None, "node": "Civitai resources"})
        else:
            models.append({"kind": "Checkpoint", "name": name, "version": ver})
    return {"models": models, "loras": loras}


def a1111_extras(text: str) -> Dict[str, Any]:
    """A1111 系文本里的额外信息：Hires、ControlNet、Clip skip、Model hash 等（有就带上）。"""
    out: Dict[str, Any] = {}
    hires = {}
    for label, rx in (("upscaler", r"Hires upscaler:\s*([^,\n]+)"), ("upscale", r"Hires upscale:\s*([\d.]+)"),
                      ("steps", r"Hires steps:\s*(\d+)"), ("denoise", r"Hires denoising strength:\s*([\d.]+)")):
        m = re.search(rx, text or "", re.I)
        if m:
            hires[label] = m.group(1).strip()
    if hires:
        out["hires"] = hires
    cn = re.findall(r"ControlNet \d+: \"([^\"]*)\"", text or "")
    if cn:
        out["controlnet"] = cn
    ms = re.search(r"Model hash:\s*([a-f0-9]+)", text or "", re.I)
    if ms:
        out["model_hash"] = ms.group(1)
    return out


# --------------------------------------------------------------------------- UI workflow → 近似 API 图
# 有些导出只带 workflow（UI 图），widgets_values 是数组，顺序跟节点的 widget 输入一致。
WORKFLOW_NODE_WIDGETS: Dict[str, List[str]] = {
    "KSampler": ["seed", "control_after_generate", "steps", "cfg", "sampler_name", "scheduler", "denoise"],
    "KSamplerAdvanced": ["add_noise", "noise_seed", "control_after_generate", "steps", "cfg", "sampler_name",
                         "scheduler", "start_at_step", "end_at_step", "return_with_leftover_noise"],
    "KSampler (Efficient)": ["seed", "control_after_generate", "steps", "cfg", "sampler_name", "scheduler", "denoise"],
    "CLIPTextEncode": ["text"],
    "BNK_CLIPTextEncodeAdvanced": ["text", "token_normalization", "weight_interpretation"],
    "ImpactWildcardEncode": ["wildcard_text", "populated_text", "mode", "seed"],
    "CheckpointLoaderSimple": ["ckpt_name"],
    "UNETLoader": ["unet_name", "weight_dtype"],
    "VAELoader": ["vae_name"],
    "LoraLoader": ["lora_name", "strength_model", "strength_clip"],
    "EmptyLatentImage": ["width", "height", "batch_size"],
    "EmptyLatentImagePresets": ["width", "height", "batch_size"],
    "SaveImage": ["filename_prefix"],
    "LoraTagLoader": ["text"],
    "CR Text": ["text"],
    "String Literal": ["string"],
}


def workflow_to_prompt_graph(workflow: Any) -> Optional[Dict[str, Any]]:
    """把 UI workflow 转成近似 API prompt 图（{id: {class_type, inputs}}），以复用现有解析逻辑。

    - 有名字的 widget 输入：按 inputs 里带 widget 标记的顺序配 widgets_values
    - 认不出的节点：用 WORKFLOW_NODE_WIDGETS 表兜底
    - 连线：用 workflow.links 还原成 API 形状的 [上游节点 id, 槽位]
    - 跳过被静音/绕过的节点（mode 2 / 4）
    """
    if not isinstance(workflow, dict):
        return None
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return None
    link_src: Dict[Any, Any] = {}
    for lk in (workflow.get("links") or []):
        if isinstance(lk, list) and len(lk) >= 5:
            link_src[lk[0]] = (str(lk[1]), lk[2])
        elif isinstance(lk, dict) and lk.get("id") is not None:
            link_src[lk["id"]] = (str(lk.get("origin_id")), lk.get("origin_slot"))
    out: Dict[str, Any] = {}
    for n in nodes:
        if not isinstance(n, dict) or n.get("mode") in (2, 4):
            continue
        nid = str(n.get("id"))
        ct = str(n.get("type") or n.get("class_type") or "")
        if not ct:
            continue
        ins: Dict[str, Any] = {}
        wv = n.get("widgets_values")
        if isinstance(wv, dict):
            ins.update({k: v for k, v in wv.items() if not isinstance(v, (list, dict)) or True})
        elif isinstance(wv, list):
            names: List[str] = []
            for i in (n.get("inputs") or []):
                # 真正被 widget 驱动的输入：{"name": "text", "widget": {"name": "text"}}（连线的输入没有 widget 键）
                if isinstance(i, dict) and isinstance(i.get("widget"), dict):
                    wname = str((i.get("widget") or {}).get("name") or i.get("name") or "")
                    if wname:
                        names.append(wname)
            fallback = WORKFLOW_NODE_WIDGETS.get(ct) or []
            j = 0
            for idx, val in enumerate(wv):
                name = names[idx] if idx < len(names) else (fallback[idx] if idx < len(fallback) else None)
                if name is None:
                    continue
                if isinstance(val, (dict,)):                     # 有些 widget 值是对象，跳过
                    continue
                ins[name] = val
                j += 1
        for i in (n.get("inputs") or []):
            if not (isinstance(i, dict) and i.get("link") is not None and i.get("name")):
                continue
            src = link_src.get(i["link"])
            if src:
                ins[str(i["name"])] = [src[0], src[1]]
        out[nid] = {"class_type": ct, "inputs": ins}
    return out or None


# --------------------------------------------------------------------------- 侧车 JSON
def parse_sidecar(obj: Any) -> Optional[Dict[str, Any]]:
    """图片旁的 .json（有些工作流/前端会把参数单独存一份）。"""
    data = loads_json(obj)
    if not isinstance(data, dict):
        return None
    if _ci_get(data, "sui_image_params") is not None:
        return parse_swarmui(data)
    if _ci_get(data, "invokeai_metadata") is not None:
        return parse_invokeai(_ci_get(data, "invokeai_metadata"))
    # ComfyUI 的 prompt 图（class_type）
    if any(isinstance(v, dict) and "class_type" in v for v in data.values()):
        return {"tool": "ComfyUI", "graph": data}
    # 通用：{prompt/negative_prompt/steps/...}
    pos = _ci_get(data, "positive", "positive_prompt", "prompt", "text")
    neg = _ci_get(data, "negative", "negative_prompt")
    if not isinstance(pos, str) or not pos.strip():
        return None
    model = _ci_get(data, "model", "model_name", "ckpt_name", "checkpoint")
    sampler = _sampler(data, (("seed", "seed"), ("steps", "steps"), ("cfg", "cfg"), ("cfg_scale", "cfg"),
                              ("sampler", "sampler_name"), ("sampler_name", "sampler_name"),
                              ("scheduler", "scheduler"), ("width", "width"), ("height", "height"),
                              ("denoise", "denoise"), ("denoising_strength", "denoise")))
    return _base("侧车 JSON", str(pos), str(neg or ""),
                 [{"kind": "Checkpoint", "name": str(model)}] if model else [], loras_from_text(str(pos)),
                 sampler, fields={"sidecar": True}, notes=["图片旁的同名 .json"])
