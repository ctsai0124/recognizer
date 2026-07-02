# 會計月報辨識服務

逐頁辨識會計月報各表、切出段落，供後續「編碼＋目次」使用。
全包式服務：同一支 FastAPI 同時提供前端網頁與辨識 API。

## 功能

- 上傳一份 PDF（可為已合併的整份月報，或單張報表）。
- 逐頁讀表頭文字（PyMuPDF 抽字），依目次順序切出各表段落。
- 抽不到字的掃描頁，可開啟 **OCR**（Tesseract 正體中文）輔助辨識表頭。
- 前端顯示每頁縮圖與判定，並可逐頁下拉校正，重算段落。
- 辨識結果供人工確認；編碼與目次生成於確認後接續（下一階段）。

## 專案結構

```
app/
├─ main.py            FastAPI 進入點（/api/recognize、/api/health、靜態網頁）
├─ recognizer.py      辨識核心（抽字比對、順序約束切段、OCR 可插拔、縮圖）
├─ static/index.html  前端網頁
├─ requirements.txt
├─ Dockerfile         安裝 tesseract-ocr-chi-tra
└─ README.md
```

## 部署到 Zeabur（建議）

1. 將 `app/` 內容推上 GitHub（讓 `Dockerfile` 位於專案根目錄）。
2. Zeabur 建立服務 → 從該 GitHub repo 佈署。Zeabur 會偵測到 `Dockerfile` 並建置。
   - 映像中已安裝 `tesseract-ocr` 與正體中文語言包，OCR 開箱即用。
3. 服務會監聽 Zeabur 提供的 `PORT`。佈署完成後開網址即可使用。

> 若不需要 OCR，可移除 Dockerfile 中的 tesseract 安裝行，改用 Zeabur 的 Python 佈署（啟動指令：
> `uvicorn main:app --host 0.0.0.0 --port $PORT`）。此時前端 OCR 勾選將無效果。

## 本機執行

```bash
cd app
pip install -r requirements.txt
# OCR 需另行安裝：Debian/Ubuntu → apt-get install tesseract-ocr tesseract-ocr-chi-tra
uvicorn main:app --reload --port 8080
# 瀏覽 http://localhost:8080
```

## API

### `POST /api/recognize`
- form-data：`file`（PDF）、`ocr`（true/false）、`thumbnails`（true/false）
- 回傳：
  ```json
  {
    "page_count": 49,
    "pages": [
      {"page":7,"kind":"text","key":"i1","label":"基金來源、用途及餘絀表",
       "header":"...","text_len":1659,"ocr":false,"thumb":"data:image/jpeg;base64,..."}
    ],
    "sections": [
      {"kind":"auto","key":"i1","name":"基金來源、用途及餘絀表","start":7,"end":7},
      {"kind":"uncertain","name":"待確認（掃描或未辨識）","start":36,"end":38,
       "candidates":[{"key":"...","name":"...","default_pages":1}]}
    ],
    "ocr_used": true,
    "ocr_available": true
  }
  ```
- `kind`：`text`（抽字或 OCR 命中）、`scan`（掃描無字）、`unknown`（有字未對應）、`front`（前置）。

### `GET /api/health`
- 回傳 `{"ok":true,"ocr_available":true|false}`。

## 目次母版（三種格式）

- **固定 16 項**（每月）：基金來源…電腦軟體增減結存表。
- **條件式**（有檔才出現、預設隱藏）：長期負債明細表（附屬表內）、租賃資產增減報表／增減結存表／折舊月報表（i16 之後）。
- **6 月**：＋市有財產增減結存表(半年報)、財產增減表(半年報)、原始憑證留存。
- **12 月**：＋市有財產增減結存表(半年報)、財產增減表(半年報)、市有財產增減結存表(年報)、原始憑證留存。
- 目次序號依「實際存在的表」自動連號；標題用「中華民國 OOO 年度 O 月份」，頁碼兩位補零「第01頁至第02頁」。
- 市有財產三版（月報／半年報／年報）以字樣硬性區分：年報須「年報且無半年」、半年報須「半年」、月報須「無半年且無年報」。

## 辨識邏輯重點

- **順序約束**：只允許往目次後段比對，避免整串位移。
- **最長關鍵字優先**：一頁命中多表時取最像正式表名者；並列衝突則不猜、標待確認。
- **OCR 容錯**：OCR 文字採 LCS 模糊比對；月報／半年報以「半年」字樣硬性區分，讀不清寧可留待確認。
- **前置保護**：正文起點（第一張文字表）之前一律視為前置，且不進行 OCR，避免封面／檢核表干擾。


## 本版已驗證（115年6月真實素材）

- 個別上傳的 8 個原始檔（差額解釋表、財政局、保管金、午餐、教育儲蓄、獎學金孳息、捐贈定存、離職儲金）在開啟 OCR 後**全部自動歸位正確**。
- 整份合併檔：抽字 12 段自動、開 OCR 後 19 段自動。
- 新增：OCR 前以 OSD 自動轉正（處理側躺掃描頁，如財政局、陽信查詢畫面）。
- 修正：單獨上傳的掃描報表不再誤判為「前置」，改標「掃描頁（待指定）」。


## 編碼＋目次生成（/api/build）

辨識確認後，可產生最終 PDF：
- **編碼**：自第 10 項起，於每頁底部置中蓋「機關碼-N」（1~9 項由系統直出、已有碼，不重複蓋）。頁碼＝正文頁序（不含前置）。
- **目次**：依「實際存在的表」自動生成（fitz 原生正體中文，文字可選取），插在封面/檢核表之後、正文之前；舊目次自動丟棄重生。格式：標題「中華民國 OOO 年 O 月份」、頁碼「第N頁至第M頁」不補零、甲/乙/丙三層、跨頁重複表頭。
- 端點：`POST /api/build`（form: file, org, roc_year, month, school, stamp, add_toc, page_keys）。page_keys 傳入使用者校正後的逐頁判定，成品以校正為準。
- 已用 115年6月實檔驗證：目次 20/20 頁碼與實檔完全吻合；蓋碼位置與實檔一致（底部置中）。

## 已知限制

- 掃描頁的段落邊界（尤其對帳單掃描頁、財產月報／半年報相鄰處）可能略有偏移，需人工於逐頁檢視微調。
- OCR 準確度受掃描品質、紅章、傾斜影響。
- 條件式表（長期負債、租賃資產三表）與年報表已納入辨識清單，但因目前無實際檔案，關鍵字尚未以真檔驗證；有檔時再校正。
