"""模型 / LoRA 库的回归测试（自己造 safetensors 样本，不碰真实模型目录）：

    .venv/Scripts/python.exe tests/test_models.py
"""
import json
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.models import model_info, read_header, scan_models, search, summarize, trigger_words  # noqa: E402


def make_safetensors(path: Path, metadata=None, tensors=None):
    """写一个合法的 safetensors 头部（不写真实权重，够解析用）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    header = {}
    if metadata:
        header["__metadata__"] = {k: (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
                                  for k, v in metadata.items()}
    for k in (tensors or {"lora_unet_x.weight": [4, 4]}):
        header[k] = {"dtype": "F32", "shape": [4, 4], "data_offsets": [0, 64]}
    blob = json.dumps(header, ensure_ascii=False).encode("utf-8")
    with path.open("wb") as f:
        f.write(struct.pack("<Q", len(blob)))
        f.write(blob)
        f.write(b"\x00" * 64)
    return path


LORA_META = {
    "ss_base_model_version": "sdxl_base_v1-0",
    "ss_network_module": "networks.lora",
    "ss_network_dim": "32",
    "ss_network_alpha": "16",
    "ss_output_name": "detail_tweaker_xl",
    "ss_num_train_images": "1200",
    "ss_epoch": "10",
    "modelspec.architecture": "stable-diffusion-xl-v1-base/lora",
    "ss_tag_frequency": json.dumps({"img": {"masterpiece": 900, "1girl": 870, "detailed face": 500,
                                            "silver hair": 300, "marble": 120}}),
}


def test_read_header_and_triggers():
    with tempfile.TemporaryDirectory() as td:
        p = make_safetensors(Path(td) / "detail_tweaker_xl.safetensors", LORA_META)
        h = read_header(p)
        assert not h["error"] and h["metadata"]["ss_network_dim"] == "32", h
        assert h["tensor_count"] == 1
        tw = trigger_words(h["metadata"])
        assert tw[:3] == ["masterpiece", "1girl", "detailed face"], tw     # 按词频排序
        print("  ✓ 读 safetensors 头部 + 从 ss_tag_frequency 提触发词（词频排序）")


def test_explicit_trained_words_win():
    with tempfile.TemporaryDirectory() as td:
        p = make_safetensors(Path(td) / "x.safetensors",
                             {"ss_trained_words": "abc style, oil painting", "ss_tag_frequency": '{"a":{"zzz":9}}'})
        tw = trigger_words(read_header(p)["metadata"])
        assert tw == ["abc style", "oil painting"], tw
        print("  ✓ 有显式 trained_words 时优先用它")


def test_model_info_kind_and_preview():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "models"
        lora = make_safetensors(root / "loras" / "风格" / "ink_jade.safetensors", LORA_META)
        ckpt = make_safetensors(root / "checkpoints" / "krea2-turbo.safetensors",
                               {"modelspec.architecture": "flux-1-dev/lora", "ss_base_model_version": "flux1"})
        (lora.with_suffix(".preview.png")).write_bytes(b"\x89PNG\r\n\x1a\n")      # 造一个预览图
        li = model_info(lora)
        assert li["kind"] == "LoRA" and li["base_model"] == "sdxl_base_v1-0", li
        assert li["preview"].endswith("ink_jade.preview.png"), li["preview"]
        assert li["labels"].get("dim") == "32" and li["labels"].get("训练图片数") == "1200"
        ci = model_info(ckpt)
        assert ci["kind"] == "Checkpoint" and ci["base_model"] == "flux1", ci
        assert ci["triggers"] == [], ci["triggers"]
        print("  ✓ 按目录判类型（loras→LoRA）+ 找预览图 + 元数据标签")


def test_scan_and_search():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "models"
        make_safetensors(root / "loras" / "detail_tweaker_xl.safetensors", LORA_META)
        make_safetensors(root / "loras" / "anime_lineart.safetensors",
                         {"ss_base_model_version": "sd_v1-5", "ss_tag_frequency": '{"a":{"lineart":50}}'})
        make_safetensors(root / "checkpoints" / "animagineXL.safetensors", {"ss_base_model_version": "sdxl_base_v1-0"})
        (root / "loras" / "readme.txt").write_text("ignore me", encoding="utf-8")
        r = scan_models([str(root)])
        assert r["ok"] and r["count"] == 3, r
        s = summarize(r["models"])
        assert {k["kind"]: k["n"] for k in s["kinds"]} == {"LoRA": 2, "Checkpoint": 1}, s
        assert s["no_trigger"] == 1, s                                  # animagineXL 没有触发词
        assert [m["name"] for m in search(r["models"], q="marble")] == ["detail_tweaker_xl.safetensors"]
        assert [m["name"] for m in search(r["models"], q="lineart")] == ["anime_lineart.safetensors"]
        assert [m["name"] for m in search(r["models"], kind="LoRA")] == ["anime_lineart.safetensors",
                                                                        "detail_tweaker_xl.safetensors"]
        assert [m["name"] for m in search(r["models"], base="sd_v1-5")] == ["anime_lineart.safetensors"]
        assert len(search(r["models"], only_triggered=True)) == 2
        assert scan_models([str(Path(td) / "nope")])["errors"], "不存在的目录要报错而不是崩"
        print("  ✓ 扫描目录（只收模型文件）+ 按触发词/类型/底模过滤 + 汇总统计")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    print("跑 %d 个模型库用例：" % len(tests))
    for t in tests:
        t()
    print("全部通过 ✓")
