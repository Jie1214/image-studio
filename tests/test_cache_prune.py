"""上传缓存清理逻辑的回归测试（纯 stdlib，在临时目录里跑，不碰真实缓存）：

    .venv/Scripts/python.exe tests/test_cache_prune.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app.cache as cache  # noqa: E402


def make_batch(root: Path, name: str, size: int, age_hours: float):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "a.png").write_bytes(b"x" * size)
    t = time.time() - age_hours * 3600
    os.utime(d, (t, t))
    os.utime(d / "a.png", (t, t))
    return d


def with_tmp(fn):
    def wrapper():
        with tempfile.TemporaryDirectory() as td:
            up = Path(td) / "uploads"
            th = Path(td) / "thumbs"
            up.mkdir()
            th.mkdir()
            old_up, old_th = cache.upload_dir, cache.thumb_dir
            cache.upload_dir = lambda: up
            cache.thumb_dir = lambda: th
            try:
                fn(up, th)
            finally:
                cache.upload_dir, cache.thumb_dir = old_up, old_th
    return wrapper


@with_tmp
def test_old_batches_removed_newest_kept(up, th):
    make_batch(up, "20260920-010000", 1000, age_hours=30)   # 30 小时前 → 该删
    make_batch(up, "20260920-020000", 1000, age_hours=28)   # 28 小时前 → 该删
    make_batch(up, "20260920-120000", 1000, age_hours=1)    # 1 小时前 → 保留
    r = cache.prune(keep_hours=24, keep_newest=1, max_bytes=0, thumbs=False)
    left = sorted(p.name for p in up.iterdir())
    assert left == ["20260920-120000"], left
    assert sorted(r["removed"]) == ["20260920-010000", "20260920-020000"], r["removed"]
    assert r["freed_bytes"] == 2000, r["freed_bytes"]
    print("  ✓ 超过保留时长的批次被删，最新的留着（释放 %d B）" % r["freed_bytes"])


@with_tmp
def test_newest_never_removed_even_if_old(up, th):
    make_batch(up, "20260901-000000", 1000, age_hours=999)  # 很旧，但只有这一批
    r = cache.prune(keep_hours=1, keep_newest=1, max_bytes=0, thumbs=False)
    assert list(up.iterdir()), "最新一批被误删了"
    assert r["removed"] == [], r["removed"]
    print("  ✓ 无论多旧，最新一批都不会被删（正在用的那批）")


@with_tmp
def test_capacity_cap_prunes_oldest(up, th):
    for i in range(5):
        make_batch(up, "20260920-%02d0000" % i, 1_000_000, age_hours=0.1 * i)
    r = cache.prune(keep_hours=999, keep_newest=1, max_bytes=2_500_000, thumbs=False)
    left = sorted(p.name for p in up.iterdir())
    assert len(left) == 2, left                     # 2.5MB 上限 → 只留最新 2 批
    assert "20260920-000000" in left, left          # 最新的必须在（这个名字是 i=0，age 最小）
    assert r["freed_bytes"] == 3_000_000, r["freed_bytes"]
    print("  ✓ 超过容量上限时从最旧的删，最新一批始终保留（剩 %d 批）" % len(left))


@with_tmp
def test_clear_all_keeps_only_newest(up, th):
    for i in range(4):
        make_batch(up, "20260920-%02d0000" % i, 500, age_hours=0.1)
    r = cache.prune(all_=True, keep_newest=1, max_bytes=0, thumbs=False)
    assert len(list(up.iterdir())) == 1, list(up.iterdir())
    assert len(r["removed"]) == 3, r["removed"]
    print("  ✓ 「只留最新一批」清掉其余全部（%d 批）" % len(r["removed"]))


@with_tmp
def test_thumbs_pruned_by_age(up, th):
    old = th / "old.jpg"
    old.write_bytes(b"y" * 4096)
    t = time.time() - 48 * 3600
    os.utime(old, (t, t))
    fresh = th / "fresh.jpg"
    fresh.write_bytes(b"y" * 100)
    r = cache.prune(keep_hours=24, keep_newest=1, max_bytes=0, thumbs=True)
    assert not old.exists() and fresh.exists()
    assert r["thumbs_freed"] == 4096, r["thumbs_freed"]
    print("  ✓ 缩略图缓存按时间清理（缩略图丢了会自动重建）")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    print("跑 %d 个上传缓存清理用例：" % len(tests))
    for t in tests:
        t()
    print("全部通过 ✓")
