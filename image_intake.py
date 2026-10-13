"""【2026-10-13】圖片收件：把表格截圖轉成文字表格（兩種引擎，由使用者自選）。

引擎：
  vision  視覺模型（AI 看圖，直接輸出 Tab 分隔表格）：版面理解好，但數字可能「看起來合理卻讀錯」，圖片會送第三方服務
  ocr     OCR（rapidocr，本機執行，圖片不外送）：數字通常準，中文字可能認錯（實測「買超張數」被讀成「實超张數」），
          表格結構由本模組依位置重組

共同原則：辨識結果只是「草稿」，一律走 paste_feed.parse_pasted 的驗證預覽，管理員對照原圖確認後才寫入；
驗證不過不寫入。辨識失敗回傳明確原因，不假裝成功。
本模組的純函式（normalize_table_text、ocr_items_to_text）不連網、可離線測試；兩個引擎都可注入假實作。
"""
import re

ENGINES = {
    "vision": "視覺模型（AI 看圖，直接輸出表格）",
    "ocr": "OCR（本機文字辨識，圖片不外送）",
}
MAX_IMAGE_BYTES = 1_500_000          # 與 Worker 一致：超過不收，請壓縮或裁切後重傳
LOW_CONF = 0.7                        # OCR 單格信心低於此值時提醒人工核對
MAX_TEXT = 100_000

TABLE_PROMPT = (
    "這是一張表格截圖。請把表格逐列轉成純文字：第一列是表頭；同一列的欄位之間用 Tab 字元分隔；每列一行。"
    "日期用 YYYY-MM-DD；數字照圖上原樣抄（保留千分位逗號、小數點、負號與百分號）；"
    "看不清楚的格子留空，不要猜測、不要補數字。只輸出表格本身，不要任何說明、標題或 markdown 符號。"
)

_SEP_ROW = re.compile(r"^[\s|:\-+=]+$")


def normalize_table_text(raw):
    """整理模型輸出：去 markdown 圍欄與 | 欄線、把『兩個以上空白』分隔的欄位轉成 Tab。已是 Tab／逗號分隔就原樣保留。"""
    if not raw:
        return ""
    lines = []
    for ln in str(raw).replace("\r", "").splitlines():
        s = ln.rstrip()
        if s.strip().startswith("```"):
            continue
        if not s.strip() or _SEP_ROW.match(s):
            continue
        if "\t" not in s and s.count("|") >= 2:
            cells = [c.strip() for c in s.strip().strip("|").split("|")]
            s = "\t".join(cells)
        lines.append(s)
    # 整份都沒有 Tab 也沒有逗號：試著用『兩個以上空白』切欄
    if lines and not any(("\t" in ln or "," in ln) for ln in lines):
        lines = ["\t".join(re.split(r"\s{2,}", ln.strip())) for ln in lines]
    return "\n".join(lines)[:MAX_TEXT]


def _box_metrics(box):
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return min(xs), max(xs), min(ys), max(ys)


def ocr_items_to_text(items, low_conf=LOW_CONF):
    """OCR 逐段結果 → Tab 分隔表格文字。items: [(box(4 個角點), text, score)]。
    依垂直位置分列（中心點差距小於字高 60% 視為同一列），同列依水平位置排序，欄位以 Tab 相連。
    回傳 (text, low_conf_cells)，low_conf_cells 為 [(文字, 信心)]，供畫面提醒人工核對。"""
    segs = []
    for box, text, score in items or []:
        t = (text or "").strip()
        if not t:
            continue
        x0, x1, y0, y1 = _box_metrics(box)
        segs.append({"x0": x0, "x1": x1, "yc": (y0 + y1) / 2.0, "h": max(y1 - y0, 1.0), "t": t, "s": float(score or 0)})
    if not segs:
        return "", []
    segs.sort(key=lambda s: s["yc"])
    med_h = sorted(s["h"] for s in segs)[len(segs) // 2]
    rows, cur = [], [segs[0]]
    for s in segs[1:]:
        if abs(s["yc"] - (sum(c["yc"] for c in cur) / len(cur))) <= 0.6 * med_h:
            cur.append(s)
        else:
            rows.append(cur)
            cur = [s]
    rows.append(cur)
    lines, lows = [], []
    for r in rows:
        r.sort(key=lambda s: s["x0"])
        cells = []
        for s in r:
            # 同一段文字裡有兩個以上空白，視為 OCR 把相鄰欄位併成一段
            cells.extend(c for c in re.split(r"\s{2,}", s["t"]) if c)
            if s["s"] < low_conf:
                lows.append((s["t"], round(s["s"], 3)))
        lines.append("\t".join(cells))
    return "\n".join(lines)[:MAX_TEXT], lows


_OCR = {"engine": None, "error": None}


def _get_ocr():
    if _OCR["engine"] is None and _OCR["error"] is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _OCR["engine"] = RapidOCR()
        except Exception as e:  # noqa: BLE001  # 缺套件或缺系統函式庫（如 libGL）時，OCR 選項改為不可用而不是整頁壞掉
            _OCR["error"] = f"{type(e).__name__}: {str(e)[:120]}"
    return _OCR["engine"]


def ocr_available():
    """回傳 (可用嗎, 不可用原因)。"""
    eng = _get_ocr()
    return (eng is not None), (_OCR["error"] or "")


def ocr_extract(image_bytes, engine=None):
    """用 OCR 辨識圖片。engine 可注入（測試用），需可呼叫並回傳 (結果清單, 耗時)。
    回傳 {ok, text, engine, warnings, error}。"""
    if not image_bytes:
        return {"ok": False, "engine": "ocr", "text": "", "warnings": [], "error": "沒有圖片內容"}
    eng = engine or _get_ocr()
    if eng is None:
        return {"ok": False, "engine": "ocr", "text": "", "warnings": [], "error": f"OCR 在這個環境無法使用（{_OCR['error']}）"}
    try:
        res, _ = eng(image_bytes)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "engine": "ocr", "text": "", "warnings": [], "error": f"OCR 辨識失敗（{type(e).__name__}）"}
    text, lows = ocr_items_to_text(res or [])
    if not text.strip():
        return {"ok": False, "engine": "ocr", "text": "", "warnings": [], "error": "OCR 沒有辨識到任何文字"}
    warn = [f"「{t}」信心 {s:.2f}，請對照原圖" for t, s in lows[:12]]
    if len(lows) > 12:
        warn.append(f"另有 {len(lows) - 12} 格信心偏低")
    return {"ok": True, "engine": "ocr", "text": text, "warnings": warn, "error": ""}


def vision_extract(image_bytes, mime="image/jpeg", analyze=None):
    """用視覺模型辨識。analyze 可注入（測試用），簽名同 dashangdao_helpers.analyze_intel_image。"""
    if not image_bytes:
        return {"ok": False, "engine": "vision", "text": "", "warnings": [], "error": "沒有圖片內容"}
    if analyze is None:
        try:
            from dashangdao_helpers import analyze_intel_image as analyze
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "engine": "vision", "text": "", "warnings": [], "error": f"視覺模型模組無法載入（{type(e).__name__}）"}
    try:
        r = analyze(image_bytes, mime, prompt=TABLE_PROMPT, max_tokens=3500)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "engine": "vision", "text": "", "warnings": [], "error": f"視覺模型呼叫失敗（{type(e).__name__}）"}
    if not r or not r.get("ok") or not (r.get("text") or "").strip():
        return {"ok": False, "engine": "vision", "text": "", "warnings": [], "error": (r or {}).get("error") or "視覺模型沒有回傳內容"}
    text = normalize_table_text(r["text"])
    if not text.strip():
        return {"ok": False, "engine": "vision", "text": "", "warnings": [], "error": "視覺模型的回覆整理後是空的"}
    warn = ["視覺模型可能把數字讀成看起來合理但錯誤的值，請逐格對照原圖再確認寫入。"]
    return {"ok": True, "engine": "vision", "text": text, "warnings": warn, "error": "", "model": r.get("model", "")}


def extract(engine, image_bytes, mime="image/jpeg", **inject):
    """依使用者選的引擎辨識。engine ∈ ENGINES。"""
    if engine == "vision":
        return vision_extract(image_bytes, mime, analyze=inject.get("analyze"))
    if engine == "ocr":
        return ocr_extract(image_bytes, engine=inject.get("ocr_engine"))
    return {"ok": False, "engine": engine, "text": "", "warnings": [], "error": f"未知的辨識引擎：{engine}"}
