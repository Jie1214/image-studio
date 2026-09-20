"""其他工具元数据解析的回归测试：

    .venv/Scripts/python.exe tests/test_parse_tools.py

覆盖：SwarmUI / InvokeAI / NovelAI / A1111 系（Civitai resources、Hires、工具识别）
      / 只有 UI workflow 的图（widget 还原）/ 侧车 JSON。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, PngImagePlugin                                     # noqa: E402

from app.metadata import parse_a1111, parse_comfy, read_params            # noqa: E402
from app.parsers_more import (a1111_extras, civitai_resources, parse_invokeai, parse_novelai,  # noqa: E402
                              parse_sidecar, parse_swarmui, tool_from_parameters, workflow_to_prompt_graph)


def test_swarmui():
    r = parse_swarmui({"sui_image_params": {
        "prompt": "a marble statue, cinematic", "negativeprompt": "blurry, low quality",
        "model": "flux1-dev.safetensors", "loras": ["detail.safetensors:0.7"], "seed": 12345,
        "steps": 20, "cfgscale": 3.5, "sampler": "euler", "scheduler": "simple", "width": 1024, "height": 1536}})
    assert r["tool"] == "SwarmUI" and r["positive"].startswith("a marble statue"), r
    assert r["negative"] == "blurry, low quality" and r["models"][0]["name"] == "flux1-dev.safetensors"
    assert r["loras"][0]["name"] == "detail.safetensors" and r["loras"][0]["strength_model"] == 0.7
    assert r["sampler"]["steps"] == 20 and r["sampler"]["cfg"] == 3.5 and r["sampler"]["width"] == 1024
    print("  ✓ SwarmUI：sui_image_params（提示词/负向/模型/LoRA/采样参数）")


def test_invokeai():
    r = parse_invokeai(json.dumps({
        "positive_prompt": "1girl, marble hall", "negative_prompt": "worst quality",
        "model": {"name": "animagine-xl.safetensors"} if False else "animagine-xl.safetensors",
        "seed": 999, "steps": 30, "cfg_scale": 6.5, "scheduler": "euler", "width": 832, "height": 1216,
        "loras": [{"name": "style.safetensors", "weight": 0.85}]}))
    assert r["tool"] == "InvokeAI" and "1girl" in r["positive"] and r["negative"] == "worst quality", r
    assert r["sampler"]["cfg"] == 6.5 and r["sampler"]["seed"] == 999
    assert r["loras"][0]["name"] == "style.safetensors" and r["loras"][0]["strength_model"] == 0.85
    print("  ✓ InvokeAI：invokeai_metadata（含 loras / cfg_scale）")


def test_novelai():
    r = parse_novelai(json.dumps({"prompt": "1girl, white dress", "uc": "lowres, bad anatomy",
                                  "steps": 28, "scale": 5.0, "seed": 42, "sampler": "k_euler_ancestral",
                                  "width": 832, "height": 1216, "qualityToggle": True}),
                      description="1girl, white dress")
    assert r["tool"] == "NovelAI" and r["negative"].startswith("lowres"), r
    assert r["sampler"]["cfg"] == 5.0 and r["sampler"]["sampler_name"] == "k_euler_ancestral"
    print("  ✓ NovelAI：Comment(JSON) + Description")


def test_a1111_civitai_resources_and_tool_hint():
    text = ("masterpiece, best quality, 1girl\n"
            "Negative prompt: worst quality, bad anatomy\n"
            "Steps: 28, Sampler: DPM++ 2M Karras, CFG scale: 7, Seed: 111, Size: 832x1216, "
            "Model hash: 1a2b3c4d, Model: animagineXL.safetensors, VAE: sdxl_vae.safetensors, "
            "Denoising strength: 0.4, Clip skip: 2, Version: f2.0.1v1.10.1, "
            'Civitai resources: [{"type":"checkpoint","modelName":"Animagine XL 3.1","modelVersionName":"v3.1"},'
            '{"type":"lora","modelName":"Detail Tweaker XL","weight":0.8}], '
            "Hires upscale: 1.5, Hires upscaler: 4x-UltraSharp")
    r = parse_a1111(text)
    civ = civitai_resources(text)
    assert civ["loras"][0]["name"] == "Detail Tweaker XL" and civ["loras"][0]["strength_model"] == 0.8, civ
    assert civ["models"][0]["version"] == "v3.1", civ
    ex = a1111_extras(text)
    assert ex["model_hash"] == "1a2b3c4d" and ex["hires"]["upscale"] == "1.5", ex
    assert tool_from_parameters(text) == "Forge", tool_from_parameters(text)      # Version: f2.0.1v1.10.1
    assert r["sampler"]["denoise"] == 0.4 and r["negative"].startswith("worst quality")
    print("  ✓ A1111 系：Civitai resources / Hires / Model hash / 版本识别（Forge）")


def test_ui_workflow_only_image():
    """只存 UI workflow（没有 API prompt）的 PNG：以前直接判「读不到」，现在按 widget 还原。"""
    workflow = {"nodes": [
        {"id": 1, "type": "CheckpointLoaderSimple", "widgets_values": ["krea2-turbo.safetensors"]},
        {"id": 2, "type": "CLIPTextEncode", "widgets_values": ["a marble statue, cinematic light"],
         "inputs": [{"name": "clip", "link": 1, "widget": None}]},
        {"id": 3, "type": "CLIPTextEncode", "widgets_values": ["blurry, low quality"]},
        {"id": 4, "type": "EmptyLatentImage", "widgets_values": [2560, 1080, 1]},
        {"id": 5, "type": "KSampler", "widgets_values": [777, "randomize", 8, 1.0, "euler", "simple", 1.0],
         "inputs": [{"name": "positive", "link": 2}, {"name": "negative", "link": 3},
                    {"name": "latent_image", "link": 4}, {"name": "model", "link": 5}]},
        {"id": 6, "type": "VAEDecode", "mode": 4, "widgets_values": []},          # 静音节点应被跳过
    ], "links": [[1, 1, 1, 2, 0, "CLIP"], [2, 2, 0, 5, 0, "CONDITIONING"],
                 [3, 3, 0, 5, 1, "CONDITIONING"], [4, 4, 0, 5, 3, "LATENT"], [5, 1, 0, 5, 4, "MODEL"]]}
    graph = workflow_to_prompt_graph(workflow)
    assert graph and "1" in graph and "6" not in graph, graph            # 静音节点被跳过
    assert graph["5"]["inputs"]["positive"] == ["2", 0], graph["5"]["inputs"]  # 连线还原成 API 形状
    r = parse_comfy(json.dumps(graph, ensure_ascii=False))
    assert "marble statue" in r["positive"], r["positive"]
    assert r["negative"] == "blurry, low quality" and r["sampler"]["steps"] == 8
    assert r["sampler"]["seed"] == 777 and r["sampler"]["width"] == 2560
    # 端到端：真的写一张只带 workflow 的 PNG，走 read_params
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "ui_only.png"
        info = PngImagePlugin.PngInfo()
        info.add_text("workflow", json.dumps(workflow, ensure_ascii=False))
        Image.new("RGB", (64, 64), (10, 20, 30)).save(p, pnginfo=info)
        m = read_params(p)
        assert m["ok"] and "marble statue" in m["positive"], (m["ok"], m["positive"], m["notes"])
        assert m["tool"] == "ComfyUI" and "widget" in m["meta_source"], (m["tool"], m["meta_source"])
    print("  ✓ 只有 UI workflow 的图：按节点 widget 还原出提示词 + 采样参数")


def test_sidecar_json():
    with tempfile.TemporaryDirectory() as td:
        img = Path(td) / "shot.png"
        Image.new("RGB", (32, 32), (1, 2, 3)).save(img)                       # 图本身没有元数据
        (Path(td) / "shot.json").write_text(json.dumps({
            "prompt": "a lighthouse at night", "negative_prompt": "people", "steps": 25,
            "cfg": 6.0, "seed": 5, "model": "sd15.safetensors"}, ensure_ascii=False), encoding="utf-8")
        m = read_params(img)
        assert m["ok"] and m["positive"] == "a lighthouse at night", (m["ok"], m["positive"], m["notes"])
        assert m["negative"] == "people" and m["sampler"]["steps"] == 25
    r = parse_sidecar(json.dumps({"sui_image_params": {"prompt": "x", "model": "m.safetensors", "steps": 1}}))
    assert r["tool"] == "SwarmUI"
    print("  ✓ 侧车 JSON：图本身没元数据时也能读出提示词/参数")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    print("跑 %d 个工具兼容解析用例：" % len(tests))
    for t in tests:
        t()
    print("全部通过 ✓")
