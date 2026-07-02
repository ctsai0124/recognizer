# -*- coding: utf-8 -*-
"""
會計月報辨識服務（全包式：同時提供前端網頁與辨識 API）
部署：Zeabur 以 Dockerfile 建置，監聽 $PORT。
"""
import os
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import recognizer

app = FastAPI(title="會計月報辨識服務", version="0.1")


@app.get("/api/health")
def health():
    return {"ok": True, "ocr_available": recognizer.ocr_available()}


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


# 靜態前端（放最後，讓上面的 /api/* 先被比對）
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
