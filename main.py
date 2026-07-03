# -*- coding: utf-8 -*-
"""
會計月報辨識服務（全包式：同時提供前端網頁與辨識 API）
部署：Zeabur 以 Dockerfile 建置，監聽 $PORT。
"""
import os
import json
from fastapi import FastAPI, UploadFile, File, Form, Body
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import recognizer
import memory
import builder

app = FastAPI(title="會計月報辨識服務", version="0.2")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "ocr_available": recognizer.ocr_available(),
        "storage_ok": memory.storage_ok(),
        "memory": memory.mem_stats(),
    }


@app.post("/api/recognize")
async def recognize(
    files: list[UploadFile] = File(...),
    ocr: bool = Form(False),
    thumbnails: bool = Form(True),
):
    """輸入一或多份 PDF。多份時先依目次順序自動合併，再逐頁辨識。"""
    blobs = []
    for f in files:
        if not f.filename.lower().endswith(".pdf"):
            return JSONResponse({"error": "請上傳 PDF 檔：" + f.filename}, status_code=400)
        blobs.append((f.filename, await f.read()))
    merge_report = None
    if len(blobs) == 1:
        data = blobs[0][1]
    else:
        data, merge_report = builder.merge_sorted(blobs, use_ocr=ocr)
    try:
        result = recognizer.analyze(data, use_ocr=ocr, want_thumbs=thumbnails)
    except Exception as e:  # noqa
        return JSONResponse({"error": "辨識失敗：{}".format(e)}, status_code=400)
    if merge_report is not None:
        result["merge_report"] = merge_report
        result["merged"] = True
    return result


@app.post("/api/remember")
def remember(payload: dict = Body(...)):
    """記錄使用者更正：{fp, key, sample}。key='front' 或空 → 忽略；key='__clear__' → 清除該指紋。"""
    fp = (payload or {}).get("fp", "")
    key = (payload or {}).get("key", "")
    sample = (payload or {}).get("sample", "")
    if not fp:
        return {"ok": False, "reason": "no_fingerprint"}
    if key == "__clear__":
        return {"ok": memory.forget_by_fp(fp), "action": "clear"}
    ok = memory.remember_by_fp(fp, key, sample)
    return {"ok": ok, "memory": memory.mem_stats()}


@app.post("/api/stamp_preview")
async def stamp_preview(
    files: list[UploadFile] = File(...),
    stamp_image: UploadFile = File(...),
    ocr: bool = Form(True),
):
    """回傳一張「蓋好章的對帳單頁」預覽 PNG（供確認去背與位置）。"""
    blobs = []
    for f in files:
        if not f.filename.lower().endswith(".pdf"):
            return JSONResponse({"error": "請上傳 PDF 檔：" + f.filename}, status_code=400)
        blobs.append((f.filename, await f.read()))
    data = blobs[0][1] if len(blobs) == 1 else builder.merge_sorted(blobs, use_ocr=ocr)[0]
    chop = await stamp_image.read()
    try:
        png = builder.stamp_preview(data, chop, use_ocr=ocr)
    except Exception as e:  # noqa
        return JSONResponse({"error": "預覽失敗：{}".format(e)}, status_code=400)
    if png is None:
        return JSONResponse({"error": "找不到對帳單頁，無法預覽蓋章"}, status_code=400)
    return Response(content=png, media_type="image/png")


@app.post("/api/build")
async def build(
    files: list[UploadFile] = File(...),
    org: str = Form("314"),
    roc_year: str = Form("115"),
    month: str = Form("6"),
    school: str = Form("高雄市立七賢國民中學"),
    ocr: bool = Form(True),
    stamp: bool = Form(True),
    add_toc: bool = Form(True),
    page_keys: str = Form(""),
    stamp_image: UploadFile = File(None),
):
    """產生編碼＋目次的最終 PDF。多檔時先合併。stamp_image 若提供→對帳單頁右下角蓋章。"""
    blobs = []
    for f in files:
        if not f.filename.lower().endswith(".pdf"):
            return JSONResponse({"error": "請上傳 PDF 檔：" + f.filename}, status_code=400)
        blobs.append((f.filename, await f.read()))
    override = None
    if len(blobs) == 1:
        data = blobs[0][1]
        if page_keys:
            try:
                override = json.loads(page_keys)
            except Exception:
                override = None
    else:
        data, _ = builder.merge_sorted(blobs, use_ocr=ocr)
    chop_bytes = await stamp_image.read() if stamp_image is not None else None
    try:
        out, summary = builder.build_final(
            data, org=org, roc_year=roc_year, month=month, school=school,
            use_ocr=ocr, stamp=stamp, add_toc=add_toc, pages_override=override,
            chop_bytes=chop_bytes)
    except Exception as e:  # noqa
        return JSONResponse({"error": "產生失敗：{}".format(e)}, status_code=400)
    fname = "%s年%s月_會計月報.pdf" % (roc_year, month)
    from urllib.parse import quote
    return Response(
        content=out, media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(fname)},
    )


# 靜態前端（放最後，讓上面的 /api/* 先被比對）
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
