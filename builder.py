# -*- coding: utf-8 -*-
"""
編碼＋目次生成
- 依辨識出的段落，對正文頁蓋頁碼（機關碼-N），第 10 項起（可設定）
- 生成目次頁（fitz 原生正體中文字型，文字可選取），插在前置之後、正文之前
- 重組輸出最終 PDF（封面/檢核表保留、舊目次丟棄、目次重生）
"""
import fitz
import recognizer as R

# 目次階層（含條件式表；只有實際存在的表才會列出）
TOC_STRUCT = [
    ("甲、預算執行報表", [
        ("壹、主要表", ["i1"]),
        ("貳、附屬表", ["i2", "i3", "ltdebt", "i4"]),
    ]),
    ("乙、會計報表", [
        ("壹、主要表", ["i5", "i6"]),
    ]),
    ("丙、參考表", [
        (None, ["i7", "i8", "i9", "i10", "bank", "i11", "i12", "i13", "i14",
                "i15", "i16", "lease1", "lease2", "lease3", "i17", "i18", "yr", "i19"]),
    ]),
]
CN = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
      "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十"]

CODE_START_KEY = "i10"   # 工具自第 10 項起蓋碼（1~9 由系統直出、已有碼）


def _body_ranges(pages):
    """由逐頁 key 計算每個表的正文頁碼範圍(1-based，不含前置)。
    回傳: ranges={key:(start,end)}, body_index_of_page={pdf_page:idx}, front_count"""
    ranges = {}
    body_idx = {}
    idx = 0
    order_pos = {k: i for i, k in enumerate(R.ORDER)}
    for p in pages:
        if p["kind"] == "front":
            continue
        idx += 1
        body_idx[p["page"]] = idx
        k = p.get("key")
        if k:
            if k not in ranges:
                ranges[k] = [idx, idx]
            else:
                ranges[k][1] = idx
    front_count = sum(1 for p in pages if p["kind"] == "front")
    return ranges, body_idx, front_count


def _toc_entries(ranges):
    """依階層產生目次項目(只列存在的表)，附層級與自動連號。"""
    entries = []
    for lv0, groups in TOC_STRUCT:
        present0 = any(k in ranges for _, leaves in groups for k in leaves)
        if not present0:
            continue
        entries.append({"lv": 0, "text": lv0})
        for lv1, leaves in groups:
            present1 = any(k in ranges for k in leaves)
            if not present1:
                continue
            if lv1:
                entries.append({"lv": 1, "text": lv1})
            c = 0
            for k in leaves:
                if k not in ranges:
                    continue
                c += 1
                s, e = ranges[k]
                entries.append({"lv": 2, "text": "%s、%s" % (CN[c - 1], R.NAME[k]),
                                "range": "第%d頁至第%d頁" % (s, e)})
    return entries


def render_toc(ranges, org, roc_year, month, school):
    """生成目次頁，回傳 fitz.Document（1~多頁）。"""
    entries = _toc_entries(ranges)
    font = fitz.Font("china-t")           # 內建正體中文字型
    W, H = 595, 842                       # A4 直式
    mL, mR = 72, 523                      # 左右邊界
    top0 = 175                            # 首列 y
    lh = 27                               # 行高
    bottom = 780
    per = int((bottom - top0) / lh)

    doc = fitz.open()

    def draw_header(page):
        tw = fitz.TextWriter(page.rect)
        def center(text, y, size):
            w = font.text_length(text, size)
            tw.append(((W - w) / 2, y), text, font=font, fontsize=size)
        center(school, 70, 18)
        center("目　次", 105, 22)
        center("中華民國 %s 年 %s 月份" % (roc_year, month), 138, 14)
        tw.write_text(page)

    i = 0
    while i < len(entries) or i == 0:
        page = doc.new_page(width=W, height=H)
        draw_header(page)
        tw = fitz.TextWriter(page.rect)
        y = top0
        count = 0
        while i < len(entries) and count < per:
            e = entries[i]
            indent = mL + e["lv"] * 22
            size = 15 if e["lv"] < 2 else 14
            tw.append((indent, y), e["text"], font=font, fontsize=size)
            if e.get("range"):
                rw = font.text_length(e["range"], 13)
                tw.append((mR - rw, y), e["range"], font=font, fontsize=13)
            y += lh
            i += 1
            count += 1
        tw.write_text(page)
        if i >= len(entries):
            break
    return doc


def _stamp(page, text, size=11):
    """在頁面蓋頁碼：一律底部置中（與實檔一致，直式橫式皆然）。"""
    W, H = page.rect.width, page.rect.height
    font = fitz.Font("china-t")
    w = font.text_length(text, size)
    tw = fitz.TextWriter(page.rect)
    tw.append(((W - w) / 2, H - 20), text, font=font, fontsize=size)
    tw.write_text(page)


def build_final(pdf_bytes, org="314", roc_year="115", month="6",
                school="高雄市立七賢國民中學", use_ocr=True,
                code_from=CODE_START_KEY, stamp=True, add_toc=True,
                pages_override=None):
    """回傳 (最終PDF bytes, 摘要dict)。
    pages_override: 若提供(使用者校正後的逐頁 [{page,kind,key}...])，則以其為準，不再自動辨識。"""
    if pages_override:
        pages = pages_override
    else:
        res = R.analyze(pdf_bytes, use_ocr=use_ocr, want_thumbs=False)
        pages = res["pages"]
    ranges, body_idx, front_count = _body_ranges(pages)

    order_pos = {k: i for i, k in enumerate(R.ORDER)}
    from_pos = order_pos.get(code_from, 0)
    stamp_start_idx = None
    if code_from in ranges:
        stamp_start_idx = ranges[code_from][0]

    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    out = fitz.open()

    # 1) 前置：保留封面/檢核表，丟棄舊目次
    front_pages = [p for p in pages if p["kind"] == "front"]
    for p in front_pages:
        raw = src[p["page"] - 1].get_text() or ""
        if "目次" in raw.replace(" ", ""):
            continue                       # 丟棄舊目次
        out.insert_pdf(src, from_page=p["page"] - 1, to_page=p["page"] - 1)

    # 2) 新目次
    if add_toc:
        toc = render_toc(ranges, org, roc_year, month, school)
        out.insert_pdf(toc)
        toc.close()

    # 3) 正文（保留原頁），第 code_from 項起蓋碼
    body_pages = [p for p in pages if p["kind"] != "front"]
    for p in body_pages:
        out.insert_pdf(src, from_page=p["page"] - 1, to_page=p["page"] - 1)
        idx = body_idx[p["page"]]
        if stamp and stamp_start_idx is not None and idx >= stamp_start_idx:
            _stamp(out[-1], "%s-%d" % (org, idx))

    data = out.tobytes(deflate=True)
    summary = {
        "sections": {k: {"start": v[0], "end": v[1]} for k, v in ranges.items()},
        "front_count": front_count,
        "stamp_start_index": stamp_start_idx,
        "toc_entries": len(_toc_entries(ranges)),
    }
    out.close(); src.close()
    return data, summary
