# -*- coding: utf-8 -*-
"""图库索引：把「读图参数」解析过的结果累积进 SQLite，支持跨目录按提示词 / 模型 / LoRA 搜索。

设计要点：
- 纯 stdlib（sqlite3），库文件放在 work/library.sqlite；解析结果里的模型/LoRA/采样参数以 JSON 存文本列。
- 有 FTS5 就用全文索引（搜提示词很快），没有就退回 LIKE。
- 按 path 主键 upsert；文件大小 + mtime 变了才需要重新解析（由调用方判断）。
- 每次调用开一个连接（配合 WAL，多线程下安全），不共享连接对象。
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import PROJECT_DIR

DB_PATH = PROJECT_DIR / "work" / "library.sqlite"
_LOCK = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
  path TEXT PRIMARY KEY,
  name TEXT, dir TEXT, bytes INTEGER, w INTEGER, h INTEGER, format TEXT,
  mtime REAL, tool TEXT, ok INTEGER, meta_source TEXT,
  models TEXT, loras TEXT, sampler TEXT, positive TEXT, negative TEXT,
  node_count INTEGER, updated REAL
);
CREATE INDEX IF NOT EXISTS idx_images_tool ON images(tool);
CREATE INDEX IF NOT EXISTS idx_images_mtime ON images(mtime);
CREATE INDEX IF NOT EXISTS idx_images_name ON images(name);
"""


def db_path() -> Path:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return DB_PATH


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path()), timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error:
        pass
    return con


def has_fts(con: sqlite3.Connection) -> bool:
    try:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x)")
        con.execute("DROP TABLE IF EXISTS _fts_probe")
        return True
    except sqlite3.Error:
        return False


def init(con: sqlite3.Connection) -> bool:
    """建表；有 FTS5 顺便建全文索引表。返回是否用 FTS。"""
    con.executescript(SCHEMA)
    fts = has_fts(con)
    if fts:
        con.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS images_fts USING fts5(
            positive, negative, models, loras, name, tool, path UNINDEXED)""")
    con.commit()
    return fts


def _row_from_meta(m: Dict[str, Any]) -> tuple:
    f = m.get("file") or {}
    models = m.get("models") or []
    loras = m.get("loras") or []
    return (
        str(f.get("path") or ""), str(f.get("name") or ""), str(Path(str(f.get("path") or "")).parent),
        int(f.get("bytes") or 0), int(f.get("w") or 0), int(f.get("h") or 0), str(f.get("format") or ""),
        float(f.get("mtime") or 0), str(m.get("tool") or ""), 1 if m.get("ok") else 0, str(m.get("meta_source") or ""),
        json.dumps(models, ensure_ascii=False), json.dumps(loras, ensure_ascii=False),
        json.dumps(m.get("sampler") or {}, ensure_ascii=False),
        (m.get("positive") or "")[:20000], (m.get("negative") or "")[:20000],
        int(m.get("total_nodes") or 0), time.time(),
    )


def add_many(metas: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """把解析结果写进索引（按 path upsert）。"""
    rows = [_row_from_meta(m) for m in metas if (m.get("file") or {}).get("path")]
    if not rows:
        return {"ok": True, "added": 0, "updated": 0, "skipped": 0}
    with _LOCK:
        con = connect()
        try:
            fts = init(con)
            cur = con.cursor()
            added = updated = 0
            for r in rows:
                path = r[0]
                exists = cur.execute("SELECT 1 FROM images WHERE path=?", (path,)).fetchone() is not None
                cur.execute("""INSERT INTO images(path,name,dir,bytes,w,h,format,mtime,tool,ok,meta_source,
                               models,loras,sampler,positive,negative,node_count,updated)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                               ON CONFLICT(path) DO UPDATE SET
                                 name=excluded.name, dir=excluded.dir, bytes=excluded.bytes, w=excluded.w,
                                 h=excluded.h, format=excluded.format, mtime=excluded.mtime, tool=excluded.tool,
                                 ok=excluded.ok, meta_source=excluded.meta_source, models=excluded.models,
                                 loras=excluded.loras, sampler=excluded.sampler, positive=excluded.positive,
                                 negative=excluded.negative, node_count=excluded.node_count, updated=excluded.updated""", r)
                if exists:
                    updated += 1
                else:
                    added += 1
                if fts:
                    cur.execute("DELETE FROM images_fts WHERE path=?", (path,))
                    tok = _fts_text(r)
                    if tok:
                        cur.execute("""INSERT INTO images_fts(positive,negative,models,loras,name,tool,path)
                                       VALUES(?,?,?,?,?,?,?)""",
                                    (r[14], r[15], _names(r[11]), _names(r[12]), r[1], r[8], path))
            con.commit()
            return {"ok": True, "added": added, "updated": updated, "fts": fts, "total": count(con)}
        finally:
            con.close()


def _names(models_json: str) -> str:
    try:
        return " ".join(str(x.get("name") or "") for x in json.loads(models_json or "[]"))
    except Exception:                                            # noqa: BLE001
        return ""


def _fts_text(r: tuple) -> str:
    """FTS5 索引内容太短就别建（避免噪声）。"""
    return " ".join([r[14] or "", r[15] or "", _names(r[11]), _names(r[12]), r[1] or ""]).strip()


def count(con: Optional[sqlite3.Connection] = None) -> int:
    own = con is None
    con = con or connect()
    try:
        return int(con.execute("SELECT COUNT(*) FROM images").fetchone()[0])
    finally:
        if own:
            con.close()


def _row_out(r: sqlite3.Row) -> Dict[str, Any]:
    def j(v, default):
        try:
            return json.loads(v) if v else default
        except Exception:                                        # noqa: BLE001
            return default
    return {
        "path": r["path"], "name": r["name"], "dir": r["dir"], "bytes": r["bytes"], "w": r["w"], "h": r["h"],
        "format": r["format"], "tool": r["tool"], "ok": bool(r["ok"]), "meta_source": r["meta_source"],
        "models": j(r["models"], []), "loras": j(r["loras"], []), "sampler": j(r["sampler"], {}),
        "positive": r["positive"] or "", "negative": r["negative"] or "",
        "total_nodes": r["node_count"] or 0, "from_library": True,
    }


def search(q: str = "", tool: str = "", model: str = "", has_prompt: bool = False,
           limit: int = 300, offset: int = 0) -> Dict[str, Any]:
    """搜图库：q 走 FTS5（或 LIKE 兜底），tool/model 是精确/包含过滤。"""
    q = (q or "").strip()
    limit = max(1, min(int(limit or 300), 2000))
    with _LOCK:
        con = connect()
        try:
            fts = init(con)
            where, args = [], []
            if tool:
                where.append("i.tool LIKE ?")
                args.append("%" + tool + "%")
            if model:
                where.append("(i.models LIKE ? OR i.loras LIKE ?)")
                args += ["%" + model + "%", "%" + model + "%"]
            if has_prompt:
                where.append("(LENGTH(i.positive) > 0 OR LENGTH(i.negative) > 0)")
            base = "FROM images i"
            if q:
                if fts:
                    base += " JOIN images_fts f ON f.path = i.path"
                    where.insert(0, "images_fts MATCH ?")
                    args.insert(0, _fts_query(q))
                else:
                    like = "%" + q + "%"
                    where.append("(i.positive LIKE ? OR i.negative LIKE ? OR i.models LIKE ? OR i.loras LIKE ?"
                                 " OR i.name LIKE ? OR i.tool LIKE ?)")
                    args += [like] * 6
            sql_where = (" WHERE " + " AND ".join(where)) if where else ""
            order = " ORDER BY (SELECT NULL)" if q and fts else " ORDER BY i.updated DESC"
            rows = con.execute("SELECT i.* %s%s LIMIT ? OFFSET ?" % (base, sql_where),
                               args + [limit, max(0, int(offset or 0))]).fetchall()
            total = con.execute("SELECT COUNT(*) %s%s" % (base, sql_where), args).fetchone()[0]
            return {"ok": True, "total": int(total), "returned": len(rows), "fts": fts, "q": q,
                    "items": [_row_out(r) for r in rows]}
        except sqlite3.Error as exc:
            return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc), "items": []}
        finally:
            con.close()


def _fts_query(q: str) -> str:
    """把用户输入变成安全的 FTS 查询：按词 AND，中文/标点做转义，避免语法报错。"""
    parts = [p for p in re.split(r"[\s,]+", q) if p]
    safe = []
    for p in parts:
        p = p.replace('"', "")
        if p:
            safe.append('"%s"' % p)
    return " AND ".join(safe) or '""'


def stats() -> Dict[str, Any]:
    with _LOCK:
        con = connect()
        try:
            fts = init(con)
            total = con.execute("SELECT COUNT(*) FROM images").fetchone()[0]
            withprompt = con.execute("SELECT COUNT(*) FROM images WHERE LENGTH(positive)>0 OR LENGTH(negative)>0").fetchone()[0]
            comfy = con.execute("SELECT COUNT(*) FROM images WHERE tool LIKE 'ComfyUI%'").fetchone()[0]
            bytool = [{"tool": r[0] or "（无）", "n": r[1]} for r in con.execute(
                "SELECT tool, COUNT(*) n FROM images GROUP BY tool ORDER BY n DESC LIMIT 12")]
            bymodel = [{"model": r[0], "n": r[1]} for r in con.execute(
                "SELECT models, COUNT(*) n FROM images WHERE models <> '[]' GROUP BY models ORDER BY n DESC LIMIT 8")]
            last = con.execute("SELECT MAX(updated) FROM images").fetchone()[0] or 0
            return {"ok": True, "db": str(db_path()), "total": total, "with_prompt": withprompt, "comfy": comfy,
                    "tools": bytool, "models": bymodel, "fts": fts,
                    "updated": time.strftime("%Y-%m-%d %H:%M", time.localtime(last)) if last else ""}
        finally:
            con.close()


def clear(what: str = "all") -> Dict[str, Any]:
    """清空索引（只删索引，不动图片文件）。"""
    with _LOCK:
        con = connect()
        try:
            init(con)
            before = count(con)
            con.execute("DELETE FROM images")
            try:
                con.execute("DELETE FROM images_fts")
            except sqlite3.Error:
                pass
            con.commit()
            return {"ok": True, "removed": before, "total": 0}
        finally:
            con.close()


def known(paths: List[str]) -> Dict[str, float]:
    """返回 {path: mtime}，用于判断哪些图片解析过、是否需要重解析。"""
    if not paths:
        return {}
    with _LOCK:
        con = connect()
        try:
            init(con)
            out: Dict[str, float] = {}
            for chunk in [paths[i:i + 400] for i in range(0, len(paths), 400)]:
                qs = ",".join("?" * len(chunk))
                for r in con.execute("SELECT path, mtime FROM images WHERE path IN (%s)" % qs, chunk):
                    out[r["path"]] = r["mtime"]
            return out
        finally:
            con.close()
