# -*- coding: utf-8 -*-
"""
編碼＋目次生成
- 依辨識出的段落，對正文頁蓋頁碼（機關碼-N），第 10 項起（可設定）
- 生成目次頁（fitz 原生正體中文字型，文字可選取），插在前置之後、正文之前
- 重組輸出最終 PDF（封面/檢核表保留、舊目次丟棄、目次重生）
"""
import fitz
import io
import re
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


def _file_sort_key(pages):
    """決定一個檔在合併順序中的位置：前置最前、其餘依其主要表的目次順序。"""
    order_pos = {k: i for i, k in enumerate(R.ORDER)}
    keys = [p.get("key") for p in pages if p.get("key")]
    fronts = sum(1 for p in pages if p["kind"] == "front")
    if keys:
        return min(order_pos.get(k, 998) for k in keys)
    if fronts and fronts >= len(pages) / 2:
        return -10                       # 封面/檢核表等前置 → 最前
    return 997                           # 認不出的 → 靠後，交由人工


def merge_sorted(file_list, use_ocr=True):
    """
    多檔合併：file_list=[(name, bytes), ...]
    逐檔辨識其主要表，依目次順序排序後合併為單一 PDF。
    回傳 (merged_bytes, report)。
    """
    items = []
    for idx, (name, data) in enumerate(file_list):
        try:
            res = R.analyze(data, use_ocr=use_ocr, want_thumbs=False)
            pages = res["pages"]
        except Exception:
            pages = []
        sk = _file_sort_key(pages) if pages else 997
        primary = None
        for p in pages:
            if p.get("key"):
                primary = p["key"]; break
        items.append({"upload_idx": idx, "name": name, "data": data,
                      "sort": sk, "primary": primary,
                      "primary_name": R.NAME.get(primary, "（未辨識）"),
                      "page_count": len(pages)})
    items.sort(key=lambda it: (it["sort"], it["upload_idx"]))

    out = fitz.open()
    for it in items:
        src = fitz.open(stream=it["data"], filetype="pdf")
        out.insert_pdf(src)
        src.close()
    merged = out.tobytes(deflate=True)
    out.close()
    report = [{"name": it["name"], "primary": it["primary"],
               "primary_name": it["primary_name"], "pages": it["page_count"]}
              for it in items]
    return merged, report


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


def _toc_font():
    """目次字型：優先使用者機器上的標楷體(kaiu/DFKai-SB)，否則用隨附開源楷體(UKai)，再退回內建。"""
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (r"C:\Windows\Fonts\kaiu.ttf",
              "/usr/share/fonts/truetype/arphic/ukai.ttc",
              os.path.join(here, "assets", "ukai.ttc")):
        if os.path.exists(p):
            try:
                return fitz.Font(fontfile=p)
            except Exception:
                pass
    return fitz.Font("china-t")


def render_toc(ranges, org, roc_year, month, school):
    """生成目次頁，回傳 fitz.Document（1~多頁）。"""
    entries = _toc_entries(ranges)
    font = _toc_font()                    # 標楷體
    W, H = 842, 595                       # A4 橫式(landscape)，與實檔一致
    mL, mR = 130, 712                     # 左右邊界（善用橫式寬度）
    top0 = 150                            # 首列 y
    lh = 26                               # 行高
    bottom = 548
    per = max(1, int((bottom - top0) / lh))

    doc = fitz.open()

    def draw_header(page):
        tw = fitz.TextWriter(page.rect)
        def center(text, y, size):
            w = font.text_length(text, size)
            tw.append(((W - w) / 2, y), text, font=font, fontsize=size)
        center(school, 60, 18)
        center("目　次", 92, 22)
        center("中華民國 %s 年 %s 月份" % (roc_year, month), 122, 14)
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


def _clear_old_codes(page, org):
    """真正移除頁面上任何殘留的『org-數字』頁碼文字（避免重複、或位置不一致的舊碼）。
    只移除文字層，掃描底圖(影像)保留不動；不塗底色，避免蓋到掃描頁的抬頭。"""
    pat = re.compile(r'^\s*%s-\d+\s*$' % re.escape(org))
    try:
        blocks = page.get_text("dict")["blocks"]
    except Exception:
        return
    rects = []
    for b in blocks:
        for l in b.get("lines", []):
            for s in l["spans"]:
                if pat.match(s["text"]):
                    rects.append(fitz.Rect(s["bbox"]))
    if not rects:
        return
    for r in rects:
        page.add_redact_annot(r, fill=None)     # fill=None：只去文字、不塗色
    try:
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)  # 不動影像
    except Exception:
        page.apply_redactions()


_CODE_LINE_RE = re.compile(r'^\s*\d+-\d+\s*$')


def _visual_rotation(page):
    """判斷頁面內容的視覺方向(0/90/180/270)。
    先看可抽取的水平內文量；抽不到(如陽信網銀 Type3 側躺表格)再用 OCR 的 OSD 判方向。"""
    horiz = 0
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            d = l.get("dir", (1, 0))
            if abs(d[0]) > 0.7:
                for s in l["spans"]:
                    t = s["text"].strip()
                    if t and not _CODE_LINE_RE.match(t):
                        horiz += len(t)
    if horiz >= 40:
        return 0
    try:
        import pytesseract, io
        from PIL import Image
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
        return int(osd.get("rotate", 0)) % 360
    except Exception:
        return 0


def _stamp(page, text, org, size=11):
    """蓋頁碼：先清掉殘留舊碼，再依內容『視覺方向』把碼擺到視覺正下方、且讀向正確不橫躺。
    - 正立頁：底部置中(直式距底≈30pt、橫式≈8pt)。
    - 內容側躺的對帳單頁(如陽信網銀匯出)：碼跟著側躺，印在內容的正下方，讀起來仍是『314-頁碼』。"""
    _clear_old_codes(page, org)
    W, H = page.rect.width, page.rect.height
    fname = "helv"                         # 頁碼為 ASCII，內建字型即可
    tl = fitz.get_text_length(text, fontname=fname, fontsize=size)
    vr = _visual_rotation(page)
    if vr == 90:                           # 內容需順時針90°轉正 → 碼側躺貼左緣、垂直置中(dir 0,1)
        page.insert_text((15, (H - tl) / 2), text, fontname=fname, fontsize=size, rotate=270)
    elif vr == 270:                        # 內容需逆時針90°轉正 → 碼側躺貼右緣(dir 0,-1)
        page.insert_text((W - 15, (H + tl) / 2), text, fontname=fname, fontsize=size, rotate=90)
    elif vr == 180:                        # 內容上下顛倒 → 碼倒著印在頁面頂端(即視覺正下方)
        page.insert_text(((W + tl) / 2, 8 + size), text, fontname=fname, fontsize=size, rotate=180)
    else:                                  # 正立：底部置中，直/橫式不同距底
        margin = 8 if W > H else 30
        page.insert_text(((W - tl) / 2, H - margin), text, fontname=fname, fontsize=size)


def prep_stamp(img_bytes, thresh=235):
    """去白底：接近白色的背景轉透明，只留印文；並裁掉透明邊。回傳 PNG bytes。"""
    from PIL import Image
    import numpy as np
    im = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    arr = np.array(im)
    r, g, b = arr[:, :, 0].astype(int), arr[:, :, 1].astype(int), arr[:, :, 2].astype(int)
    whiteish = (r >= thresh) & (g >= thresh) & (b >= thresh)
    arr[:, :, 3] = np.where(whiteish, 0, arr[:, :, 3])
    im2 = Image.fromarray(arr, "RGBA")
    bbox = im2.getbbox()
    if bbox:
        im2 = im2.crop(bbox)
    out = io.BytesIO()
    im2.save(out, "PNG")
    return out.getvalue()


CM = 28.3465  # 1 公分 = 28.35 pt


def overlay_chop(page, chop_png, width_cm=3.5, margin_cm=1.0):
    """在頁面右下角疊上章（保持比例）。"""
    from PIL import Image
    im = Image.open(io.BytesIO(chop_png))
    iw, ih = im.size
    w = width_cm * CM
    h = w * ih / iw
    W, H = page.rect.width, page.rect.height
    m = margin_cm * CM
    x1, y1 = W - m, H - m
    rect = fitz.Rect(x1 - w, y1 - h, x1, y1)
    page.insert_image(rect, stream=chop_png, overlay=True, keep_proportion=True)


def stamp_preview(pdf_bytes, chop_bytes, use_ocr=True, width_cm=3.5, margin_cm=1.0):
    """回傳一張「蓋好章的對帳單頁」預覽 PNG bytes（找第一頁 bank）。無對帳單則回 None。"""
    res = R.analyze(pdf_bytes, use_ocr=use_ocr, want_thumbs=False)
    bank_page = next((p["page"] for p in res["pages"] if p.get("key") == "bank"), None)
    if bank_page is None:
        return None
    chop = prep_stamp(chop_bytes)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[bank_page - 1]
    overlay_chop(page, chop, width_cm, margin_cm)
    pix = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4))
    out = pix.tobytes("png")
    doc.close()
    return out


def build_final(pdf_bytes, org="314", roc_year="115", month="6",
                school="高雄市立七賢國民中學", use_ocr=True,
                code_from=CODE_START_KEY, stamp=True, add_toc=True,
                pages_override=None, chop_bytes=None,
                chop_width_cm=3.5, chop_margin_cm=1.0):
    """回傳 (最終PDF bytes, 摘要dict)。
    pages_override: 若提供(使用者校正後的逐頁)，則以其為準，不再自動辨識。
    chop_bytes: 若提供，於所有「對帳單」頁右下角蓋章(自動去白底)。"""
    if pages_override:
        pages = pages_override
    else:
        res = R.analyze(pdf_bytes, use_ocr=use_ocr, want_thumbs=False)
        pages = res["pages"]
    ranges, body_idx, front_count = _body_ranges(pages)

    order_pos = {k: i for i, k in enumerate(R.ORDER)}
    stamp_start_idx = None
    if code_from in ranges:
        stamp_start_idx = ranges[code_from][0]

    chop_png = prep_stamp(chop_bytes) if chop_bytes else None

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

    # 3) 正文（保留原頁），第 code_from 項起蓋碼；對帳單頁右下角蓋章
    bank_stamped = 0
    body_pages = [p for p in pages if p["kind"] != "front"]
    for p in body_pages:
        out.insert_pdf(src, from_page=p["page"] - 1, to_page=p["page"] - 1)
        idx = body_idx[p["page"]]
        if stamp and stamp_start_idx is not None and idx >= stamp_start_idx:
            _stamp(out[-1], "%s-%d" % (org, idx), org)
        if chop_png and p.get("key") == "bank":
            overlay_chop(out[-1], chop_png, chop_width_cm, chop_margin_cm)
            bank_stamped += 1

    data = out.tobytes(deflate=True)
    summary = {
        "sections": {k: {"start": v[0], "end": v[1]} for k, v in ranges.items()},
        "front_count": front_count,
        "stamp_start_index": stamp_start_idx,
        "toc_entries": len(_toc_entries(ranges)),
        "bank_pages_stamped": bank_stamped,
    }
    out.close(); src.close()
    return data, summary
