# -*- coding: utf-8 -*-
"""
校正記憶與 OCR 快取（存於持久磁碟）
- 記憶：使用者把某頁「改對」時，記住「表頭指紋 → 正確的表 key」，下次自動命中。
- 快取：以頁面內容雜湊為鍵，存 OCR 文字，重跑同檔不再 OCR。
資料路徑預設 /data，可用環境變數 DATA_DIR 覆寫。
"""
import os
import re
import json
import hashlib
import threading

DATA_DIR = os.environ.get("DATA_DIR", "/data")
MEM_PATH = os.path.join(DATA_DIR, "corrections.json")
CACHE_PATH = os.path.join(DATA_DIR, "ocr_cache.json")

_lock = threading.Lock()
_mem = None      # {fingerprint: {"key":..., "count":n, "sample":表頭}}
_cache = None    # {page_hash: ocr_text}


def _ensure_dir():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        return True
    except Exception:
        return False


def storage_ok():
    """持久磁碟是否可寫。"""
    try:
        _ensure_dir()
        test = os.path.join(DATA_DIR, ".write_test")
        with open(test, "w") as f:
            f.write("ok")
        os.remove(test)
        return True
    except Exception:
        return False


def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    if not _ensure_dir():
        return False
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def _mem_get():
    global _mem
    if _mem is None:
        _mem = _load(MEM_PATH, {})
    return _mem


def _cache_get():
    global _cache
    if _cache is None:
        _cache = _load(CACHE_PATH, {})
    return _cache


# ---------- 表頭指紋 ----------
def fingerprint(text):
    """
    由表頭文字取「指紋」：正規化去空白、去日期/數字/頁碼等易變雜訊，
    取前 40 個中文字，讓同一張表每月都得到穩定且一致的指紋。
    """
    n = re.sub(r"\s+", "", text or "")
    # 去掉常見易變片段
    n = re.sub(r"中華民國.{0,30}?日", "", n)
    n = re.sub(r"中華民國\d+年\d+月[份份]?", "", n)
    n = re.sub(r"\d{2,}[-/]\d+", "", n)     # 日期、頁碼 314-1 之類
    n = re.sub(r"[0-9,\.]+", "", n)         # 純數字金額
    # 只保留中文字與括號（表名關鍵所在）
    n = re.sub(r"[^\u4e00-\u9fff（）()]", "", n)
    return n[:40]


# ---------- 記憶：查詢與寫入 ----------
def lookup(text):
    """回傳記憶命中的表 key，或 None。需指紋夠長才算數（避免空泛命中）。"""
    fp = fingerprint(text)
    if len(fp) < 6:
        return None
    m = _mem_get()
    rec = m.get(fp)
    return rec["key"] if rec else None


def remember(text, key):
    """記住：此表頭指紋 → 正確的表 key。key 為 None 或 'front' 時忽略。"""
    if not key or key == "front":
        return False
    fp = fingerprint(text)
    return remember_by_fp(fp, key, text[:60])


def remember_by_fp(fp, key, sample=""):
    """直接以指紋記憶（前端已算好指紋時用）。"""
    if not key or key == "front" or not fp or len(fp) < 6:
        return False
    with _lock:
        m = _mem_get()
        rec = m.get(fp, {"key": key, "count": 0, "sample": sample})
        rec["key"] = key
        rec["count"] = rec.get("count", 0) + 1
        rec["sample"] = sample or rec.get("sample", "")
        m[fp] = rec
        return _save(MEM_PATH, m)


def forget_by_fp(fp):
    """清除某指紋的記憶（使用者把記憶改回未指定時用）。"""
    with _lock:
        m = _mem_get()
        if fp in m:
            del m[fp]
            return _save(MEM_PATH, m)
    return False


def mem_stats():
    m = _mem_get()
    return {"entries": len(m)}


# ---------- OCR 快取 ----------
def page_hash(img_bytes):
    return hashlib.sha1(img_bytes).hexdigest()


def cache_get_ocr(h):
    return _cache_get().get(h)


def cache_put_ocr(h, text):
    with _lock:
        c = _cache_get()
        c[h] = text
        return _save(CACHE_PATH, c)
