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
    file: UploadFile = File(...),
    ocr: bool = Form(False),
    thumbnails: bool = Form(True),
):
    """輸入一份 PDF，回傳逐頁判定與整併段落。ocr=true 時對抽不到字的頁嘗試 OCR。"""
    if not file.filename.lower().endswith(".pdf"):
        return JSONResponse({"error": "請上傳 PDF 檔"}, status_code=400)
    data = await file.read()
    try:
        result = recognizer.analyze(data, use_ocr=ocr, want_thumbs=thumbnails)
    except Exception as e:  # noqa
        return JSONResponse({"error": "辨識失敗：{}".format(e)}, status_code=400)
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


@app.post("/api/build")
async def build(
    file: UploadFile = File(...),
    org: str = Form("314"),
    roc_year: str = Form("115"),
    month: str = Form("6"),
    school: str = Form("高雄市立七賢國民中學"),
    ocr: bool = Form(True),
    stamp: bool = Form(True),
    add_toc: bool = Form(True),
    page_keys: str = Form(""),
):
    """產生編碼＋目次的最終 PDF。page_keys 若提供(JSON:[{page,kind,key}...])則以其為準。"""
    if not file.filename.lower().endswith(".pdf"):
        return JSONResponse({"error": "請上傳 PDF 檔"}, status_code=400)
    data = await file.read()
    override = None
    if page_keys:
        try:
            override = json.loads(page_keys)
        except Exception:
            override = None
    try:
        out, summary = builder.build_final(
            data, org=org, roc_year=roc_year, month=month, school=school,
            use_ocr=ocr, stamp=stamp, add_toc=add_toc, pages_override=override)
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
