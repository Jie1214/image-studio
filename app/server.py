# -*- coding: utf-8 -*-
"""HTTP 服务（只用标准库）：静态页 + 扫描 / 上传 / 预览 / 批量压缩 / 缩略图 / 打包下载。"""
from __future__ import annotations

import json
import mimetypes
import os
import queue
import shutil
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import (PROJECT_DIR, ensure_config_file, load_config, output_dir, save_config,
               thumb_dir, upload_dir)
from .imaging import Settings, compress_file, make_thumb, open_image
from .jobs import get_job, scan_dir, start_job, zip_results

STATIC_DIR = PROJECT_DIR / "static"
JSON_LIMIT = 200 * 1024 * 1024


def _json_default(o):
    return str(o)


class Handler(BaseHTTPRequestHandler):
    server_version = "ImageStudio/1.0"
    protocol_version = "HTTP/1.1"

    # ---------------- 基础工具 ----------------
    def log_message(self, fmt, *args):        # 安静点：只记错误
        if str(args[1] if len(args) > 1 else "").startswith(("4", "5")):
            print("[http] %s - %s" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes, ctype: str = "application/octet-stream",
              extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, data: Any, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False, default=_json_default).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return b""
        if n > JSON_LIMIT:
            raise ValueError("请求体过大")
        return self.rfile.read(n)

    def _json_body(self) -> Dict[str, Any]:
        raw = self._body()
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8", "replace"))

    def _query(self) -> Dict[str, str]:
        q = urllib.parse.urlparse(self.path).query
        return {k: v[0] for k, v in urllib.parse.parse_qs(q).items()}

    # ---------------- 路径白名单 ----------------
    def _allowed(self, path: str) -> bool:
        try:
            p = Path(path).resolve()
        except Exception:
            return False
        cfg = load_config()
        roots = [Path(r).resolve() for r in (cfg.get("input_roots") or []) if str(r).strip()]
        roots += [output_dir().resolve(), upload_dir().resolve(), thumb_dir().resolve(),
                  (PROJECT_DIR / "work").resolve()]
        for r in roots:
            try:
                p.relative_to(r)
                return True
            except ValueError:
                continue
        return False

    # ---------------- GET ----------------
    def do_GET(self):      # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/api/config":
                return self._json({"ok": True, "config": load_config(), "project": str(PROJECT_DIR),
                                   "output_dir": str(output_dir())})
            if path == "/api/thumb":
                return self._thumb()
            if path == "/api/file":
                return self._file()
            if path == "/api/job":
                job = get_job(self._query().get("id", ""))
                if not job:
                    return self._json({"ok": False, "error": "任务不存在"}, 404)
                return self._json({"ok": True, "job": job.snapshot()})
            if path == "/api/zip":
                job = get_job(self._query().get("id", ""))
                if not job:
                    return self._json({"ok": False, "error": "任务不存在"}, 404)
                z = zip_results(job)
                if not z:
                    return self._json({"ok": False, "error": "没有可打包的产物"}, 400)
                data = Path(z).read_bytes()
                name = urllib.parse.quote(Path(z).name)
                return self._send(200, data, "application/zip",
                                  {"Content-Disposition": "attachment; filename*=UTF-8''%s" % name})
            if path == "/api/cache":
                # 上传缓存多大、几批、什么时候的（浏览器上传的副本都在 work/uploads）
                from .cache import cache_info
                return self._json(cache_info())
            if path == "/api/library/stats":
                from .index import stats as lib_stats
                return self._json(lib_stats())
            if path == "/api/library/search":
                from .index import search as lib_search
                qs = self._query()
                return self._json(lib_search(q=qs.get("q", ""), tool=qs.get("tool", ""), model=qs.get("model", ""),
                                             has_prompt=(qs.get("has_prompt") == "1"),
                                             limit=int(qs.get("limit") or 300)))
            if path == "/api/stats":
                cfg = load_config()
                od = output_dir()
                n = sum(1 for _ in od.rglob("*") if _.is_file()) if od.exists() else 0
                return self._json({"ok": True, "output_dir": str(od), "output_files": n,
                                   "workers": cfg.get("workers"), "roots": cfg.get("input_roots")})
            return self._json({"ok": False, "error": "未知接口 %s" % path}, 404)
        except Exception as exc:      # noqa: BLE001
            return self._json({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}, 500)

    def do_POST(self):     # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/config":
                return self._json({"ok": True, "config": save_config(self._json_body())})
            if path == "/api/scan":
                b = self._json_body()
                root = (b.get("dir") or "").strip()
                if not root:
                    return self._json({"ok": False, "error": "请填写目录"}, 400)
                return self._json(scan_dir(root, bool(b.get("recursive", True))))
            if path == "/api/upload":
                return self._upload()
            if path == "/api/preview":
                return self._preview()
            if path == "/api/compress":
                b = self._json_body()
                files = [f for f in (b.get("files") or []) if str(f).strip()]
                if not files:
                    return self._json({"ok": False, "error": "没有待处理文件"}, 400)
                sub = b.get("subdir") or time.strftime("压缩_%Y%m%d-%H%M%S")
                job = start_job(files, b.get("settings") or {}, out_dir=b.get("out_dir"),
                                workers=b.get("workers"), subdir=sub)
                return self._json({"ok": True, "job": job.snapshot()})
            if path == "/api/cancel":
                b = self._json_body()
                job = get_job(b.get("id") or "")
                if not job:
                    return self._json({"ok": False, "error": "任务不存在"}, 404)
                job.cancel = True
                return self._json({"ok": True, "job": job.snapshot()})
            if path == "/api/meta":
                b = self._json_body()
                from .metadata import read_params
                plist = b.get("paths")
                if plist:
                    out = []
                    for pp in plist[:800]:
                        try:
                            if not Path(pp).is_file() or not self._allowed(pp):
                                out.append({"ok": False, "file": {"name": Path(pp).name, "path": pp},
                                            "notes": ["跳过：文件不存在或不在允许范围"], "models": [], "loras": [],
                                            "kinds": {}, "sampler": {}, "positive": "", "negative": "",
                                            "embeddings": [], "node_census": {}, "tool": "跳过"})
                                continue
                            out.append(read_params(pp))
                        except Exception as exc:       # noqa: BLE001
                            out.append({"ok": False, "file": {"name": Path(pp).name, "path": pp},
                                        "notes": ["解析出错：%s" % exc], "models": [], "loras": [], "kinds": {},
                                        "sampler": {}, "positive": "", "negative": "", "embeddings": [],
                                        "node_census": {}, "tool": "出错"})
                    return self._json({"ok": True, "metas": out})
                p = b.get("path") or ""
                if not (Path(p).is_file() if p else False):
                    return self._json({"ok": False, "error": "文件不存在"}, 404)
                if not self._allowed(p):
                    return self._json({"ok": False, "error": "路径不在允许范围"}, 403)
                from .metadata import read_params
                return self._json({"ok": True, "meta": read_params(p)})
            if path == "/api/reveal":
                b = self._json_body()
                target = b.get("path") or str(output_dir())
                p = Path(target)
                if p.is_file():
                    p = p.parent
                if not p.exists():
                    return self._json({"ok": False, "error": "目录不存在"}, 400)
                try:
                    os.startfile(str(p))       # Windows
                    return self._json({"ok": True, "opened": str(p)})
                except Exception as exc:       # noqa: BLE001
                    return self._json({"ok": False, "error": str(exc)}, 500)
            if path == "/api/allow_dir":
                # 用户手写目录 = 明确意图：把它加进「可扫描目录」，避免读取时被白名单挡住
                b = self._json_body()
                d = (b.get("dir") or "").strip()
                if not d:
                    return self._json({"ok": False, "error": "请填写目录"}, 400)
                p = Path(d)
                if not p.exists():
                    return self._json({"ok": False, "error": "目录不存在：%s" % d}, 400)
                if p.is_file():
                    p = p.parent
                rp = str(p.resolve())
                cfg = load_config()
                roots = [str(r) for r in (cfg.get("input_roots") or []) if str(r).strip()]
                if not any(str(Path(r).resolve()) == rp for r in roots):
                    roots.append(rp)
                    save_config({"input_roots": roots})
                    return self._json({"ok": True, "added": True, "roots": roots})
                return self._json({"ok": True, "added": False, "roots": roots})
            if path == "/api/cache":
                # 上传缓存多大、几批、什么时候的（浏览器上传的副本都在 work/uploads）
                from .cache import cache_info
                return self._json(cache_info())
            if path == "/api/library/add":
                from .index import add_many
                b = self._json_body()
                items = b.get("items") or []
                if not isinstance(items, list):
                    raise ValueError("items 必须是数组")
                res = add_many(items)
                return self._json(res)
            if path == "/api/library/clear":
                from .index import clear as lib_clear
                return self._json(lib_clear())
            if path == "/api/cache/clear":
                from .cache import prune
                b = self._json_body()
                what = (b.get("what") or "old").strip()          # old=按时间规则清 / all=只留最新一批
                return self._json(prune(all_=(what == "all")))
            if path == "/api/list_dirs":
                # 目录填错了怎么办：把「这一层/上一层真实存在的文件夹」列出来让用户点，不用手打路径
                b = self._json_body()
                d = (b.get("dir") or "").strip()
                if not d:
                    return self._json({"ok": False, "error": "请填写目录"}, 400)
                p = Path(d)
                parent = p
                if not parent.is_dir():
                    parent = p.parent
                if not parent.is_dir():
                    return self._json({"ok": False, "error": "上一级目录也不存在：%s" % parent}, 400)
                dirs, files = [], 0
                try:
                    for child in sorted(parent.iterdir(), key=lambda x: x.name.lower()):
                        try:
                            if child.name.startswith((".", "$")):
                                continue
                            if child.is_dir():
                                dirs.append({"name": child.name, "path": str(child)})
                            else:
                                files += 1
                        except OSError:
                            continue
                        if len(dirs) >= 300:
                            break
                except OSError as exc:
                    return self._json({"ok": False, "error": "读不到这个目录：%s" % exc}, 400)
                return self._json({"ok": True, "queried": d, "exists": Path(d).is_dir(),
                                   "listing": str(parent), "dirs": dirs, "files": files,
                                   "truncated": len(dirs) >= 300})
            if path == "/api/classify/scan":
                b = self._json_body()
                root = (b.get("dir") or "").strip()
                if not root:
                    return self._json({"ok": False, "error": "请填写要分类的目录"}, 400)
                from .classify import scan_classify, RULE_COMFY
                # 跳过输出目录，避免把产物又搬进产物
                res = scan_classify(root, bool(b.get("recursive", True)),
                                    b.get("rule") or RULE_COMFY,
                                    deep=bool(b.get("deep", False)),
                                    skip_dirs=[b.get("out_dir") or str(output_dir())])
                return self._json(res)
            if path == "/api/classify/run":
                b = self._json_body()
                items = b.get("items") or []
                if not items:
                    return self._json({"ok": False, "error": "没有待分类的条目"}, 400)
                from .classify import run_classify
                res = run_classify(items, b.get("root") or "", b.get("out_dir") or str(output_dir()),
                                   names=b.get("names"), mode=b.get("mode") or "copy",
                                   conflict=b.get("conflict") or "rename",
                                   preserve_tree=b.get("preserve_tree", True),
                                   confirm=bool(b.get("confirm")))
                return self._json(res, 200 if res.get("ok") else 400)
            # 未知路由：先把请求体读干净再回 404，否则残留字节会污染同一条 keep-alive 连接上的后续请求
            try:
                self._body()
            except Exception:
                pass
            return self._json({"ok": False, "error": "未知接口 %s" % path}, 404)
        except ValueError as exc:      # 请求体不是合法 JSON / 参数不合法 → 400，别报成 500 让人看不懂
            return self._json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:      # noqa: BLE001
            return self._json({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}, 500)

    # ---------------- 具体实现 ----------------
    def _static(self, rel: str):
        rel = rel.split("?")[0].replace("\\", "/").lstrip("/")
        target = (STATIC_DIR / rel).resolve()
        try:
            target.relative_to(STATIC_DIR.resolve())
        except ValueError:
            return self._json({"ok": False, "error": "非法路径"}, 403)
        if not target.exists() or not target.is_file():
            return self._json({"ok": False, "error": "文件不存在 %s" % rel}, 404)
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        return self._send(200, target.read_bytes(), ctype)

    def _file(self):
        q = self._query()
        p = q.get("path", "")
        if not p or not Path(p).is_file():
            return self._json({"ok": False, "error": "文件不存在"}, 404)
        if not self._allowed(p):
            return self._json({"ok": False, "error": "该路径不在允许范围内（可在设置里把它加进「可扫描目录」）"}, 403)
        f = Path(p)
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        extra = {}
        if q.get("dl"):
            import urllib.parse as _up
            extra["Content-Disposition"] = "attachment; filename*=UTF-8''%s" % _up.quote(f.name)
        return self._send(200, f.read_bytes(), ctype, extra)

    def _thumb(self):
        q = self._query()
        p = q.get("path", "")
        box = int(q.get("w") or 320)
        if not p or not Path(p).is_file():
            return self._json({"ok": False, "error": "文件不存在"}, 404)
        if not self._allowed(p):
            return self._json({"ok": False, "error": "路径不在允许范围"}, 403)
        f = Path(p)
        key = "%s_%d_%d_%d" % (abs(hash(str(f))), f.stat().st_mtime, f.stat().st_size, box)
        dst = thumb_dir() / (key + ".jpg")
        if not dst.exists():
            if make_thumb(f, dst, box=box) is None:
                return self._json({"ok": False, "error": "缩略图生成失败"}, 500)
        return self._send(200, dst.read_bytes(), "image/jpeg")

    def _upload(self):
        ct = self.headers.get("Content-Type") or ""
        if "multipart/form-data" not in ct:
            return self._json({"ok": False, "error": "需要 multipart/form-data"}, 400)
        boundary = ct.split("boundary=", 1)[-1].strip().strip('"')
        raw = self._body()
        parts = _parse_multipart(raw, boundary)
        sess = upload_dir() / time.strftime("%Y%m%d-%H%M%S")
        sess.mkdir(parents=True, exist_ok=True)
        saved: List[str] = []
        for name, data in parts:
            safe = Path(name).name
            if not safe:
                continue
            dst = sess / safe
            i = 1
            while dst.exists():
                dst = sess / ("%s_%d%s" % (Path(safe).stem, i, Path(safe).suffix))
                i += 1
            dst.write_bytes(data)
            saved.append(str(dst))
        from .jobs import probe
        return self._json({"ok": True, "files": [probe(p) for p in saved], "dir": str(sess)})

    def _preview(self):
        b = self._json_body()
        p = b.get("path") or ""
        if not Path(p).is_file():
            return self._json({"ok": False, "error": "文件不存在"}, 404)
        if not self._allowed(p):
            return self._json({"ok": False, "error": "路径不在允许范围"}, 403)
        st = Settings.from_dict(b.get("settings") or {})
        dst_dir = PROJECT_DIR / "work" / "previews"
        dst_dir.mkdir(parents=True, exist_ok=True)
        r = compress_file(p, st, dst_dir, suffix="_预览", overwrite=True)
        if not r.get("ok"):
            return self._json({"ok": False, "error": r.get("error")}, 500)
        return self._json({"ok": True, "result": r})

    # 兼容某些客户端
    def do_HEAD(self):     # noqa: N802
        self.do_GET()


def _parse_multipart(raw: bytes, boundary: str) -> List[tuple]:
    """极简 multipart 解析：返回 [(filename, bytes), ...]（Python 3.13 已移除 cgi 模块）。"""
    out: List[tuple] = []
    if not boundary:
        return out
    sep = b"--" + boundary.encode("utf-8", "ignore")
    chunks = raw.split(sep)
    for ch in chunks:
        ch = ch.strip(b"\r\n")
        if not ch or ch.startswith(b"--"):
            continue
        head, _, body = ch.partition(b"\r\n\r\n")
        if not _:
            continue
        headers = head.decode("utf-8", "replace").split("\r\n")
        name = ""
        for h in headers:
            if h.lower().startswith("content-disposition") and "filename=" in h:
                name = h.split("filename=")[-1].strip().strip('"')
        if name:
            out.append((name, body.rstrip(b"\r\n")))
    return out


def serve(host: str = "127.0.0.1", port: int = 8720) -> None:
    ensure_config_file()
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    print("=" * 52)
    print("  图像工坊已启动 → http://%s:%d" % (host, port))
    print("  输出目录：%s" % output_dir())
    print("  关闭这个窗口即停止服务")
    print("=" * 52)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
