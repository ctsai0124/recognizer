# -*- coding: utf-8 -*-
"""
會計月報辨識核心
- 抽字（PyMuPDF）比對表頭，順序約束切段
- OCR 為可插拔模組：主機安裝 Tesseract(chi_tra) 後，對「抽不到字」的頁才會呼叫
- 產生每頁縮圖供前端確認掃描段邊界
"""
import re
import base64
import fitz  # PyMuPDF
import memory

# ---------- 目次順序與各表比對規則 ----------
ORDER = ["i1", "i2", "i3", "ltdebt", "i4", "i5", "i6", "i7", "i8", "i9", "i10",
         "bank", "i11", "i12", "i13", "i14", "i15", "i16",
         "lease1", "lease2", "lease3", "i17", "i18", "yr", "i19"]

NAME = {
    "i1": "基金來源、用途及餘絀表", "i2": "主要業務計畫執行明細表", "i3": "資本資產明細表",
    "i4": "固定資產建設改良擴充執行情形明細表", "i5": "平衡表", "i6": "收入支出表",
    "i7": "預算執行與會計收支對照表", "i8": "各項費用彙計表", "i9": "平衡表科目明細表",
    "i10": "銀行存款差額解釋表", "bank": "對帳單", "i11": "保管(證)品月報",
    "i12": "保管品對帳單", "i13": "市有財產增減結存表", "i14": "財產折舊月報表",
    "i15": "財產增減月報表", "i16": "電腦軟體增減結存表", "i17": "市有財產增減結存表(半年報)",
    "i18": "財產增減表(半年報)", "i19": "原始憑證留存代辦、受委託機關（構）、學校或民間團體明細表",
    "ltdebt": "長期負債明細表",
    "lease1": "租賃資產增減報表", "lease2": "租賃資產增減結存表", "lease3": "租賃資產折舊月報表",
    "yr": "市有財產增減結存表(年報)",
}
INC = {
    "i1": ["基金來源、用途及餘絀表"], "i2": ["主要業務計畫執行明細表"], "i3": ["資本資產明細表"],
    "i4": ["固定資產建設改良擴充執行情形明細表"], "i5": ["平衡表"], "i6": ["收入支出表"],
    "i7": ["預算執行與會計收支對照表"], "i8": ["各項費用彙計表"], "i9": ["平衡表科目明細表"],
    "i10": ["銀行存款差額解釋表"], "bank": ["公庫存款對帳單", "庫款支付對帳單", "存款對帳單", "台幣存摺存款查詢", "台幣歸戶查詢", "交易明細查詢"],
    "i11": ["保管品月報", "保管(證)品月報", "保管品月報表"], "i12": ["保管品對帳單"],
    "i13": ["市有財產增減結存表"], "i14": ["財產折舊月報表"],
    "i15": ["財產增減表(月報)", "財產增減月報表"], "i16": ["電腦軟體增減結存表"],
    "i17": ["市有財產增減結存表(半年報)"], "i18": ["財產增減表(半年報)"],
    "i19": ["原始憑證留存代辦", "原始憑證留存"],
    "ltdebt": ["長期負債明細表"],
    "lease1": ["租賃資產增減報表"], "lease2": ["租賃資產增減結存表"], "lease3": ["租賃資產折舊月報表"],
    "yr": ["市有財產增減結存表(年報)"],
}
EXC = {"i5": ["科目明細"], "bank": ["保管品"], "i11": ["對帳單"], "i13": ["半年", "年報"], "i15": ["半年"], "yr": ["半年"]}
# 硬性要求：半年報項目必須出現「半年」字樣，避免月報被誤判為半年報
REQUIRE = {"i17": ["半年"], "i18": ["半年"], "yr": ["年報"]}

# UTR 程式代號錨點（OCR 對代號較穩，可當強錨；月報/半年報同代號者靠順序約束區分）
UTR = {"i13": "022", "i14": "270", "i15": "011", "i17": "022", "i18": "011", "yr": "022"}  # 取3碼數字，容忍OCR把UTR誤讀

# 掃描表慣用頁數（供前端預填掃描段邊界；使用者可改）
DEFAULT_PAGES = {k: 1 for k in ORDER}
DEFAULT_PAGES.update({"i3": 2, "i5": 3, "i7": 2, "i8": 4, "i9": 6, "i15": 2, "i18": 2, "bank": 4})
# 條件式與年報表預設頁數
for _k in ["ltdebt","lease1","lease2","lease3","yr"]: DEFAULT_PAGES.setdefault(_k,1)


def _norm(s):
    return re.sub(r"\s+", "", s or "")


def _is_front(text):
    n = _norm(text)
    if "目次" in n:
        return "目次"
    if "自行檢核表" in n:
        return "檢核表"
    if "會計月報" in n and len(n) < 40:
        return "封面"
    return None


def _lcs_ratio(kw, n):
    """kw 與 n 的最長共同子序列 / len(kw)；容忍 OCR 少量錯字。"""
    m, ln = len(kw), len(n)
    if m == 0:
        return 0.0
    dp = [0] * (ln + 1)
    for i in range(1, m + 1):
        prev = 0
        ch = kw[i - 1]
        for j in range(1, ln + 1):
            tmp = dp[j]
            dp[j] = prev + 1 if ch == n[j - 1] else (dp[j] if dp[j] >= dp[j - 1] else dp[j - 1])
            prev = tmp
    return dp[ln] / m


def _distinct_matches(text, allowed):
    """回傳 text 命中的不同表 key 集合（供防呆：列表頁會命中很多表）。"""
    n = _norm(text)
    hit = set()
    for k in allowed:
        for kw in INC[k]:
            if _norm(kw) in n:
                hit.add(k); break
    return hit


def _match_key(text, allowed, fuzzy=False):
    """在 allowed 內回傳命中且關鍵字最長者；並列或未命中回傳 None。
    fuzzy=True（OCR 文字用）除完全相符外，長度≥6 之表名允許 LCS≥0.8 容錯命中，並納入 UTR 錨點。"""
    n = _norm(text)
    if len(n) < 8:
        return None
    cands = []
    for k in allowed:
        best = 0
        pos = 10 ** 9                          # 該表關鍵字/代號在文中最早出現位置
        for kw in INC[k]:
            w = _norm(kw)
            if w in n:
                best = max(best, len(w)); pos = min(pos, n.find(w))
            elif fuzzy and len(w) >= 6 and _lcs_ratio(w, n) >= 0.8:
                best = max(best, len(w) - 1)   # 容錯命中略降權，完全相符優先
        if fuzzy and k in UTR:
            mm = re.search(r"U[TIL1J]R\s*0*" + UTR[k], n)
            if mm:
                best = max(best, 20); pos = min(pos, mm.start())   # UTR 代號為強錨
        if best > 0 and not any(_norm(e) in n for e in EXC.get(k, [])) \
                and all(_norm(rq) in n for rq in REQUIRE.get(k, [])):
            cands.append((best, -pos, k))       # 權重高、位置前者優先
    if not cands:
        return None
    cands.sort(reverse=True)
    top = cands[0][:2]
    # 僅在權重與位置都相同時才視為真衝突、不猜
    if sum(1 for c in cands if c[:2] == top) > 1:
        return None
    return cands[0][2]


# ---------- OCR（可插拔） ----------
def ocr_available():
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _ocr_page(page):
    try:
        import pytesseract
        from PIL import Image
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        # 以影像位元組雜湊做快取鍵：同一頁重跑不再 OCR
        try:
            h = memory.page_hash(pix.tobytes("png"))
            cached = memory.cache_get_ocr(h)
            if cached is not None:
                return cached
        except Exception:
            h = None
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        # 先用 OSD 偵測方向並轉正（處理側躺/倒置的掃描頁）
        try:
            osd = pytesseract.image_to_osd(img)
            m = re.search(r"Rotate:\s*(\d+)", osd)
            deg = int(m.group(1)) if m else 0
            if deg:
                img = img.rotate(-deg, expand=True)
        except Exception:
            pass
        text = pytesseract.image_to_string(img, lang="chi_tra") or ""
        if h:
            try:
                memory.cache_put_ocr(h, text)
            except Exception:
                pass
        return text
    except Exception:
        return ""


def _thumb(page, width=150):
    zoom = width / page.rect.width
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    try:
        data = pix.tobytes("jpeg")
        mime = "jpeg"
    except Exception:
        data = pix.tobytes("png")
        mime = "png"
    return "data:image/{};base64,{}".format(mime, base64.b64encode(data).decode())


# ---------- 主分析 ----------
def analyze(pdf_bytes, use_ocr=False, want_thumbs=True):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []
    ptr = 0

    # 前置區判定：先用「抽字」找出第一張文字表的頁；其前為前置區(不 OCR)。
    # 若整份找不到任何文字表(例如單獨上傳的掃描報表)，則無前置區、整份視為正文(照樣 OCR)。
    raws = [doc[i].get_text() or "" for i in range(len(doc))]
    first_body = None
    tmp = 0
    for i, raw in enumerate(raws):
        if _is_front(raw):
            continue
        k = _match_key(raw, ORDER[tmp:])
        if k:
            first_body = i
            break
    front_zone_end = first_body if first_body is not None else 0  # 之前的頁為前置區

    for i in range(len(doc)):
        page = doc[i]
        raw = raws[i]
        n = _norm(raw)
        header = re.sub(r"\s+", " ", raw).strip()[:60]
        kind, key, label = None, None, None
        ocr_used_here = False
        mem_hit = False
        fp_text = raw            # 供指紋/記憶的文字來源（掃描頁改用 OCR 文字）

        front = _is_front(raw)
        in_front_zone = (first_body is not None) and (i < front_zone_end)

        if front or in_front_zone:
            kind, label = "front", (front or "封面／前置")
        else:
            key = _match_key(raw, ORDER[ptr:])
            # 抽字命中不到 → 先查記憶庫（便宜，掃描前先試）
            if key is None and len(n) >= 6:
                mk = memory.lookup(raw)
                if mk and mk in ORDER[ptr:]:
                    key, mem_hit = mk, True
            # 仍無 → OCR，OCR 文字再比對＋查記憶
            if key is None and use_ocr and len(n) < 15:
                otext = _ocr_page(page)
                if _norm(otext):
                    ocr_used_here = True
                    fp_text = otext
                    distinct = _distinct_matches(otext, ORDER[ptr:])
                    if len(distinct) < 3:
                        k2 = _match_key(otext, ORDER[ptr:], fuzzy=True)
                        if k2:
                            key = k2
                            header = re.sub(r"\s+", " ", otext).strip()[:60]
                        else:
                            mk = memory.lookup(otext)
                            if mk and mk in ORDER[ptr:]:
                                key, mem_hit = mk, True
                                header = re.sub(r"\s+", " ", otext).strip()[:60]
            if key:
                ptr = ORDER.index(key)
                kind, label = "text", NAME[key]
            elif len(n) < 15:
                kind, label = "scan", "掃描頁（無文字，待指定）"
            else:
                kind, label = "unknown", "有文字但未對應任何表"

        p = {"page": i + 1, "kind": kind, "key": key, "label": label,
             "header": header, "text_len": len(n), "ocr": ocr_used_here,
             "mem": mem_hit, "fp": memory.fingerprint(fp_text)}
        if want_thumbs:
            p["thumb"] = _thumb(page)
        pages.append(p)

    sections = _build_sections(pages)
    result = {
        "page_count": len(doc),
        "pages": pages,
        "sections": sections,
        "ocr_used": use_ocr,
        "ocr_available": ocr_available(),
    }
    doc.close()
    return result


def _build_sections(pages):
    """把逐頁結果整併成段落：文字段(auto) 與 掃描/未辨識段(uncertain)。"""
    sections = []
    cur = None

    def close():
        nonlocal cur
        if cur:
            sections.append(cur)
            cur = None

    for p in pages:
        if p["kind"] == "front":
            close()
            if cur is None and (not sections or sections[-1]["kind"] != "front"):
                sections.append({"kind": "front", "name": "封面／檢核表／目次（前置）",
                                 "start": p["page"], "end": p["page"], "key": None})
            else:
                sections[-1]["end"] = p["page"]
            continue
        if p["kind"] == "text":
            if cur and cur.get("key") == p["key"]:
                cur["end"] = p["page"]
            else:
                close()
                cur = {"kind": "auto", "key": p["key"], "name": NAME[p["key"]],
                       "start": p["page"], "end": p["page"]}
        else:  # scan / unknown
            if cur and cur["kind"] == "uncertain":
                cur["end"] = p["page"]
            else:
                close()
                cur = {"kind": "uncertain", "key": None, "name": "待確認（掃描或未辨識）",
                       "start": p["page"], "end": p["page"]}
    close()

    # 為每個 uncertain 段補上「依順序推測應含哪些表」
    text_keys = [s["key"] for s in sections if s["kind"] == "auto"]
    for idx, s in enumerate(sections):
        if s["kind"] != "uncertain":
            continue
        prev_key = None
        for j in range(idx - 1, -1, -1):
            if sections[j]["kind"] == "auto":
                prev_key = sections[j]["key"]; break
        next_key = None
        for j in range(idx + 1, len(sections)):
            if sections[j]["kind"] == "auto":
                next_key = sections[j]["key"]; break
        lo = ORDER.index(prev_key) + 1 if prev_key else 0
        hi = ORDER.index(next_key) if next_key else len(ORDER)
        cand = [k for k in ORDER[lo:hi]]
        s["candidates"] = [{"key": k, "name": NAME[k], "default_pages": DEFAULT_PAGES[k]} for k in cand]
    return sections
