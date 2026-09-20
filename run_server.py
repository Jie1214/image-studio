# -*- coding: utf-8 -*-
"""启动入口：双击 run.bat 走这里。

    .venv\\Scripts\\python.exe run_server.py                # 默认 127.0.0.1:8720
    .venv\\Scripts\\python.exe run_server.py --port 8721
    .venv\\Scripts\\python.exe run_server.py --no-browser
"""
from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app import ensure_config_file, load_config, output_dir   # noqa: E402
from app.server import serve                                   # noqa: E402


def is_our_instance(host: str, port: int) -> bool:
    try:
        with urllib.request.urlopen("http://%s:%d/api/stats" % (host, port), timeout=2) as r:
            return b'"ok": true' in r.read(200)
    except Exception:
        return False


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def wait_port_free(host: str, port: int, seconds: float = 4.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if port_free(host, port):
            return True
        time.sleep(0.25)
    return port_free(host, port)


def resolve_port(host: str, want: int, tries: int = 8):
    if port_free(host, want):
        return want, ""
    if is_our_instance(host, want):
        return -want, "图像工坊已经在这个端口上运行了 → http://%s:%d" % (host, want)
    if wait_port_free(host, want):
        return want, "端口 %d 刚空出来，继续使用" % want
    for p in range(want + 1, want + tries):
        if port_free(host, p):
            return p, "端口 %d 被占用，已改用 %d" % (want, p)
    return 0, "端口 %d~%d 都被占用，请换一个" % (want, want + tries - 1)


def main() -> int:
    ap = argparse.ArgumentParser(description="图像工坊 · 批量压缩")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--host", default="")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    ensure_config_file()
    try:
        cfg = load_config()
    except Exception as exc:      # noqa: BLE001
        print("[错误] 读取配置失败：%s: %s" % (type(exc).__name__, exc))
        return 1

    host = args.host or cfg.get("host") or "127.0.0.1"
    want = int(args.port or cfg.get("port") or 8720)
    port, note = resolve_port(host, want)
    if port < 0:
        url = "http://%s:%d" % (host, -port)
        print("=" * 52)
        print(note)
        print("=" * 52)
        if not args.no_browser:
            webbrowser.open(url)
        return 0
    if port == 0:
        print("[错误] %s" % note)
        return 1
    if note:
        print("[提示] %s" % note)

    url = "http://%s:%d" % (host, port)
    print("=" * 52)
    print("  图像工坊 · 批量压缩 → %s" % url)
    print("  输出目录：%s" % output_dir())
    print("  关闭这个窗口即停止服务")
    print("=" * 52)
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        serve(host=host, port=port)
    except OSError as exc:
        print("[错误] 无法监听 %s:%d → %s" % (host, port, exc))
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
