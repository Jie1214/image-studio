"""图库索引的回归测试（在临时库里跑，不碰真实索引）：

    .venv/Scripts/python.exe tests/test_library.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app.index as lib                                           # noqa: E402


def _meta(path, pos, neg="", model="animagine.safetensors", lora=None, tool="ComfyUI", w=832, h=1216):
    return {
        "ok": True, "tool": tool, "meta_source": "tEXt prompt",
        "file": {"path": path, "name": Path(path).name, "bytes": 1234, "w": w, "h": h, "format": "PNG", "mtime": 1.5},
        "models": [{"kind": "Checkpoint", "name": model}] if model else [],
        "loras": [{"name": lora, "strength_model": 0.8}] if lora else [],
        "sampler": {"steps": 28, "cfg": 7, "seed": 1, "sampler_name": "euler"},
        "positive": pos, "negative": neg, "total_nodes": 12,
    }


def with_tmp(fn):
    def wrapper():
        with tempfile.TemporaryDirectory() as td:
            old = lib.DB_PATH
            lib.DB_PATH = Path(td) / "library.sqlite"
            try:
                fn()
            finally:
                lib.DB_PATH = old
    return wrapper


@with_tmp
def test_add_and_search_prompt():
    r = lib.add_many([
        _meta(r"E:\imgs\a.png", "1girl, marble hall, cinematic light", "worst quality"),
        _meta(r"E:\imgs\b.png", "a castle at dusk, oil painting", "blurry"),
        _meta(r"E:\imgs\c.png", "", "", tool="未检测到生成参数", model=None),
    ])
    assert r["ok"] and r["added"] == 3, r
    assert lib.count() == 3
    hits = lib.search(q="marble")
    names = [i["name"] for i in hits["items"]]
    assert names == ["a.png"], names                      # 只命中带 marble 的那张
    assert hits["items"][0]["positive"].startswith("1girl")
    hits2 = lib.search(q="castle")
    assert [i["name"] for i in hits2["items"]] == ["b.png"]
    print("  ✓ 写入 + 按提示词搜索（跨目录累积）")


@with_tmp
def test_search_by_model_lora_and_tool():
    lib.add_many([
        _meta(r"E:\imgs\a.png", "x", model="krea2-turbo.safetensors", lora="detail_tweaker.safetensors"),
        _meta(r"E:\imgs\b.png", "y", model="flux1-dev.safetensors"),
        _meta(r"E:\imgs\c.png", "z", tool="NovelAI", model="NovelAI Diffusion"),
    ])
    m = lib.search(model="krea2")
    assert [i["name"] for i in m["items"]] == ["a.png"], m["items"]
    l = lib.search(model="detail_tweaker")
    assert [i["name"] for i in l["items"]] == ["a.png"]
    t = lib.search(tool="NovelAI")
    assert [i["name"] for i in t["items"]] == ["c.png"]
    hp = lib.search(has_prompt=True)
    assert len(hp["items"]) == 3
    print("  ✓ 按模型 / LoRA / 工具 / 有无提示词过滤")


@with_tmp
def test_upsert_and_mtime():
    lib.add_many([_meta(r"E:\imgs\a.png", "first version")])
    lib.add_many([_meta(r"E:\imgs\a.png", "second version, marble")])
    assert lib.count() == 1, lib.count()                  # 同一路径是覆盖，不是新增
    assert lib.search(q="second")["returned"] == 1
    assert lib.search(q="first")["returned"] == 0         # 旧内容不该还搜得到
    known = lib.known([r"E:\imgs\a.png", r"E:\imgs\zz.png"])
    assert list(known) == [r"E:\imgs\a.png"], known       # 只有索引里有的才返回
    print("  ✓ 同路径 upsert（覆盖旧内容）+ known() 判断是否已索引")


@with_tmp
def test_fts_handles_weird_input():
    lib.add_many([_meta(r"E:\imgs\a.png", "1girl, (masterpiece:1.2),【中文】标签")])
    for bad in ['"', "a AND b", "(", "1girl (masterpiece", "【中文】"]:
        r = lib.search(q=bad)                             # 不能抛异常
        assert isinstance(r, dict) and "items" in r, (bad, r)
    assert lib.search(q="masterpiece")["returned"] == 1
    print("  ✓ FTS 查询对引号/括号/中文等输入不报错")


@with_tmp
def test_stats_and_clear():
    lib.add_many([_meta(r"E:\imgs\a.png", "x"), _meta(r"E:\imgs\b.png", "", tool="未检测到生成参数", model=None)])
    st = lib.stats()
    assert st["total"] == 2 and st["comfy"] == 1 and st["with_prompt"] == 1, st
    assert st["tools"] and st["tools"][0]["n"] == 1
    c = lib.clear()
    assert c["removed"] == 2 and lib.count() == 0
    print("  ✓ 统计（总数/带提示词/ComfyUI/工具分布）+ 清空索引")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    print("跑 %d 个图库索引用例：" % len(tests))
    for t in tests:
        t()
    print("全部通过 ✓")
