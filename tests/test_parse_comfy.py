"""ComfyUI 提示词/参数解析的回归测试（纯 stdlib，直接跑）：

    .venv/Scripts/python.exe tests/test_parse_comfy.py

覆盖的都是真机上踩过的坑：
  1) 采样器节点被改名/换包（KSampler_A1111）→ 以前按类名匹配，整条链都不走，提示词全空
  2) 正向 conditioning 被 ConditioningZeroOut 抹掉 → 图里明明有文本，界面却显示「没有正向提示词」
  3) 尺寸来自节点链接（TTResolutionSelector）→ 以前会把 ["82",0] 这种链接写进采样参数
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.metadata import parse_comfy  # noqa: E402


def g(nodes):
    return parse_comfy(json.dumps(nodes))


def test_renamed_sampler_follows_prompt_chain():
    """KSampler_A1111（A1111 兼容节点）+ BNK_CLIPTextEncodeAdvanced + 中间夹 InpaintModelConditioning。"""
    r = g({
        "10001": {"class_type": "ECHOCheckpointLoaderSimple", "inputs": {"ckpt_name": "animagineXL.safetensors"}},
        "10019": {"class_type": "VAELoader", "inputs": {"vae_name": "sdxl_vae.safetensors"}},
        "10021": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1520, "batch_size": 2}},
        "10035": {"class_type": "BNK_CLIPTextEncodeAdvanced", "inputs": {"clip": ["10001", 1], "text": "1girl, solo, big breasts, white hair"}},
        "10036": {"class_type": "BNK_CLIPTextEncodeAdvanced", "inputs": {"clip": ["10001", 1], "text": "worst quality, low quality, lowres"}},
        "11002": {"class_type": "KSampler_A1111", "inputs": {
            "model": ["10001", 0], "positive": ["10035", 0], "negative": ["10036", 0],
            "latent_image": ["10021", 0], "seed": 54692680, "steps": 30, "cfg": 5.5,
            "sampler_name": "euler_ancestral", "scheduler": "normal", "denoise": 1.0}},
        "11030": {"class_type": "InpaintModelConditioning", "inputs": {
            "positive": ["10035", 0], "negative": ["10036", 0], "vae": ["10019", 0],
            "pixels": ["11019", 0], "mask": ["11028", 1]}},
        "11033": {"class_type": "KSampler_A1111", "inputs": {
            "model": ["11031", 0], "positive": ["11030", 0], "negative": ["11030", 1],
            "latent_image": ["11030", 2], "seed": 111, "steps": 20, "cfg": 5.0,
            "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": 0.6}},
    })
    assert "1girl, solo, big breasts" in r["positive"], r["positive"]
    assert "worst quality" in r["negative"], r["negative"]
    assert "worst quality" not in r["positive"], "负向串进正向"
    assert r["sampler"]["seed"] == 54692680
    assert r["sampler"]["steps"] == 30 and r["sampler"]["sampler_name"] == "euler_ancestral"
    assert r["sampler"]["width"] == 1024 and r["sampler"]["height"] == 1520
    assert all(not isinstance(v, list) for v in r["sampler"].values()), r["sampler"]
    print("  ✓ 改名采样器（KSampler_A1111）→ 提示词 + 采样参数都读到了")


def test_standard_sampler_still_works():
    r = g({
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd15.safetensors"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "a cat, masterpiece"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "blurry"}},
        "4": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["5", 0],
            "seed": 7, "steps": 25, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 768, "batch_size": 1}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["4", 0], "vae": ["1", 2]}},
    })
    assert r["positive"] == "a cat, masterpiece" and r["negative"] == "blurry", (r["positive"], r["negative"])
    assert r["sampler"]["seed"] == 7 and r["sampler"]["sampler_name"] == "euler"
    print("  ✓ 标准 KSampler 工作流没被改坏")


def test_zeroed_positive_falls_back_to_text_nodes():
    """正向被 ConditioningZeroOut 抹掉时，也要把图里的提示词捞出来（而不是显示「没有正向提示词」）。"""
    r = g({
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd15.safetensors"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "a castle at dusk"}},
        "3": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["2", 0]}},
        "4": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["3", 0], "negative": ["3", 0], "latent_image": ["5", 0],
            "seed": 1, "steps": 20, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
    })
    assert "a castle at dusk" in r["positive"], r["positive"]
    assert "ConditioningZeroOut" not in r["positive"]
    print("  ✓ 正向被 zero-out 时仍能捞到图里的提示词")


def test_linked_size_not_written_as_link():
    """尺寸靠链接给（TTResolutionSelector）时不能把链接写进采样参数；种子在独立节点上也要认。"""
    r = g({
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "flux.safetensors"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "a robot"}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": ["82", 0], "height": ["82", 1], "batch_size": 1}},
        "82": {"class_type": "TTResolutionSelector", "inputs": {"width": 1024, "height": 1536}},
        "90": {"class_type": "Seed (rgthree)", "inputs": {"seed": 12345}},
        "4": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["2", 0], "negative": ["2", 0], "latent_image": ["5", 0],
            "steps": 8, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
    })
    assert r["sampler"]["width"] == 1024 and r["sampler"]["height"] == 1536, r["sampler"]
    assert r["sampler"]["seed"] == 12345, r["sampler"]
    assert all(not isinstance(v, list) for v in r["sampler"].values()), r["sampler"]
    print("  ✓ 链接尺寸不写进参数、独立种子节点也能认")


def test_lora_tag_loader_text_not_taken_as_prompt():
    r = g({
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd15.safetensors"}},
        "10": {"class_type": "LoraTagLoader", "inputs": {"model": ["1", 0], "clip": ["1", 1], "text": "<lora:a.safetensors:0.5>"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["10", 1], "text": "true prompt"}},
        "4": {"class_type": "KSampler", "inputs": {
            "model": ["10", 0], "positive": ["2", 0], "negative": ["2", 0], "latent_image": ["5", 0],
            "seed": 1, "steps": 20, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
    })
    assert r["positive"] == "true prompt", r["positive"]
    print("  ✓ LoRA 标签加载器的 text 不会被当成提示词")


def test_impact_wildcard_encode_positive():
    """IMPACT 的 ImpactWildcardEncode：文本在 populated_text / wildcard_text 里，以前这两个键名不认 → 正向空。"""
    r = g({
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "xl.safetensors"}},
        "10": {"class_type": "ImpactWildcardEncode", "inputs": {
            "model": ["1", 0], "clip": ["1", 1], "mode": "populate", "seed": 1,
            "wildcard_text": "1girl, {red|blue} hair, __style__",
            "populated_text": "1girl, red hair, marble studio, masterpiece"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "bad quality, worst quality"}},
        "4": {"class_type": "KSampler", "inputs": {
            "model": ["10", 0], "positive": ["10", 0], "negative": ["3", 0], "latent_image": ["5", 0],
            "seed": 1, "steps": 20, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 768, "batch_size": 1}},
    })
    assert r["positive"] == "1girl, red hair, marble studio, masterpiece", r["positive"]
    assert r["negative"] == "bad quality, worst quality", r["negative"]
    print("  ✓ ImpactWildcardEncode：读 populated_text（不再只说「没有正向提示词」）")


def test_impact_wildcard_encode_empty_populated_falls_back():
    r = g({
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "xl.safetensors"}},
        "10": {"class_type": "ImpactWildcardEncode", "inputs": {
            "model": ["1", 0], "clip": ["1", 1], "populated_text": "", "wildcard_text": "castle, sunset"}},
        "4": {"class_type": "KSampler", "inputs": {
            "model": ["10", 0], "positive": ["10", 0], "negative": ["10", 0], "latent_image": ["5", 0],
            "seed": 1, "steps": 20, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
    })
    assert r["positive"] == "castle, sunset", r["positive"]
    print("  ✓ populated_text 为空时退回 wildcard_text")


def test_text_through_showanything_and_switch():
    """正向走 ConditioningZeroOut + 文本经 easy showAnything / Any Switch 转进来（Krea2 改图工作流那种接法）。"""
    r = g({
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "Krea2-turbo.safetensors"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": ["30", 0]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "bad quality, blurry"}},
        "30": {"class_type": "easy showAnything", "inputs": {"anything": ["31", 0]}},
        "31": {"class_type": "String Literal", "inputs": {"string": "a marble statue, cinematic light"}},
        "4": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["2", 0]}},
        "5": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["4", 0], "negative": ["3", 0], "latent_image": ["6", 0],
            "seed": 389183363332409, "steps": 8, "cfg": 1.0, "sampler_name": "euler",
            "scheduler": "simple", "denoise": 1.0}},
        "6": {"class_type": "EmptyLatentImage", "inputs": {"width": 2560, "height": 1080, "batch_size": 1}},
        "88": {"class_type": "Any Switch (rgthree)", "inputs": {"any_1": ["31", 0], "any_2": ["2", 0]}},
    })
    assert "marble statue" in r["positive"], r["positive"]
    assert r["negative"] == "bad quality, blurry", r["negative"]
    assert r["sampler"]["steps"] == 8 and r["sampler"]["cfg"] == 1.0
    print("  ✓ 文本经 easy showAnything / String Literal 转进来也能读到")


def test_zero_out_side_has_no_prompt():
    """负向走 ConditioningZeroOut（Krea2 那种「不要负向」的接法）：负向要空，不能把正向文本抄过来。"""
    r = g({
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "Krea2-turbo-Ink_Jade.safetensors"}},
        "7": {"class_type": "CR Text", "inputs": {"text": "A multi-view character turnaround layout, white background"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": ["7", 0]}},
        "3": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["2", 0]}},
        "5": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["6", 0],
            "seed": 596033570655159, "steps": 8, "cfg": 1.0, "sampler_name": "euler",
            "scheduler": "simple", "denoise": 1.0}},
        "6": {"class_type": "EmptyLatentImage", "inputs": {"width": 1920, "height": 1080, "batch_size": 1}},
    })
    assert "character turnaround" in r["positive"], r["positive"]
    assert r["negative"] == "", "负向被 zero-out，不该出现正向的文本：%r" % r["negative"]
    print("  ✓ 负向走 ConditioningZeroOut 时留空（不会把正向抄到负向）")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    print("跑 %d 个解析回归用例：" % len(tests))
    for t in tests:
        t()
    print("全部通过 ✓")
