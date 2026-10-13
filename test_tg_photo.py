"""【2026-10-13】圖片收件測試（離線）：辨識引擎的純函式、收件匣圖片流程（記憶體假資料庫、注入假引擎）。
最後一段若本機有 rapidocr 與中文字型，會用真的 OCR 對一張合成表格圖做冒煙測試。"""
import base64
import sys
from datetime import date

import image_intake as I
import tg_inbox as ti

fails = []
TODAY = date(2026, 10, 13)


def check(name, cond):
    print(("PASS: " if cond else "FAIL: ") + name)
    if not cond:
        fails.append(name)


# ───────────── image_intake 純函式 ─────────────
check("去除 markdown 圍欄與 | 欄線與分隔列",
      I.normalize_table_text("```\n| 代號 | 日期 |\n|---|---|\n| 2330 | 2026-10-12 |\n```") == "代號\t日期\n2330\t2026-10-12")
check("Tab 分隔的內容原樣保留", I.normalize_table_text("代號\t日期\n2330\t2026-10-12") == "代號\t日期\n2330\t2026-10-12")
check("逗號分隔的內容原樣保留", I.normalize_table_text("代號,日期\n2330,2026-10-12") == "代號,日期\n2330,2026-10-12")
check("兩個以上空白分欄轉成 Tab", I.normalize_table_text("代號   日期\n2330   2026-10-12") == "代號\t日期\n2330\t2026-10-12")
check("空輸入回傳空字串", I.normalize_table_text("") == "" and I.normalize_table_text(None) == "")


def box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


items = [(box(450, 28, 534, 57), "收盤價", 0.9), (box(28, 27, 87, 58), "代號", 0.95),
         (box(201, 27, 257, 58), "日期", 0.4),
         (box(451, 91, 543, 118), "1,020.5", 0.9), (box(30, 91, 93, 116), "2330", 0.9), (box(201, 92, 343, 116), "2026-10-12", 0.9)]
text, lows = I.ocr_items_to_text(items)
check("OCR 結果依位置重組成列與欄（亂序輸入也正確）", text == "代號\t日期\t收盤價\n2330\t2026-10-12\t1,020.5")
check("低信心格子被標出", lows == [("日期", 0.4)])
check("OCR 併成一段的相鄰欄位（兩個以上空白）會被拆開",
      I.ocr_items_to_text([(box(0, 0, 100, 20), "2330  1,020", 0.9)])[0] == "2330\t1,020")
check("沒有任何文字回傳空", I.ocr_items_to_text([])[0] == "")


class FakeEngine:
    def __init__(self, res): self.res = res
    def __call__(self, data): return self.res, 0.1


r = I.ocr_extract(b"x", engine=FakeEngine([(box(0, 0, 50, 20), "代號", 0.5), (box(100, 0, 150, 20), "日期", 0.9)]))
check("ocr_extract 成功並附低信心提醒", r["ok"] and r["text"] == "代號\t日期" and any("代號" in w for w in r["warnings"]))
check("ocr_extract 沒辨識到文字 → 明確失敗", not I.ocr_extract(b"x", engine=FakeEngine([]))["ok"])
check("ocr_extract 引擎丟例外 → 明確失敗不丟出", not I.ocr_extract(b"x", engine=lambda d: (_ for _ in ()).throw(RuntimeError("x")))["ok"])
check("ocr_extract 空圖片 → 失敗", not I.ocr_extract(b"", engine=FakeEngine([]))["ok"])

seen = {}
def fake_analyze(data, mime, prompt=None, max_tokens=None):
    seen.update(prompt=prompt, max_tokens=max_tokens)
    return {"ok": True, "text": "```\n代號\t日期\n2330\t2026-10-12\n```", "model": "m"}


v = I.vision_extract(b"x", "image/png", analyze=fake_analyze)
check("vision_extract 帶表格提示詞並整理輸出", v["ok"] and v["text"] == "代號\t日期\n2330\t2026-10-12" and "Tab" in seen["prompt"] and seen["max_tokens"] >= 3000)
check("vision_extract 提醒數字要對照原圖", any("對照原圖" in w for w in v["warnings"]))
check("vision_extract 模型失敗 → 帶出原因",
      I.vision_extract(b"x", analyze=lambda *a, **k: {"ok": False, "error": "限流/額度不足", "text": None})["error"] == "限流/額度不足")
check("vision_extract 呼叫丟例外 → 明確失敗", not I.vision_extract(b"x", analyze=lambda *a, **k: 1 / 0)["ok"])
check("extract 依引擎分派", I.extract("vision", b"x", analyze=fake_analyze)["engine"] == "vision"
      and I.extract("ocr", b"x", ocr_engine=FakeEngine([(box(0, 0, 50, 20), "a", 0.9)]))["engine"] == "ocr")
check("extract 未知引擎 → 失敗", not I.extract("magic", b"x")["ok"])
check("兩種引擎都列在選項", set(I.ENGINES) == {"vision", "ocr"})


# ───────────── 收件匣圖片流程 ─────────────
class Q:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.f, self.mode, self.payload, self.lim, self.desc = [], "select", None, None, False

    def select(self, *_a): self.mode = "select"; return self
    def eq(self, k, v): self.f.append(lambda r: r.get(k) == v); return self
    def in_(self, k, vs): self.f.append(lambda r: r.get(k) in vs); return self
    def order(self, k, desc=False): self.desc = desc; return self
    def limit(self, n): self.lim = n; return self
    def update(self, p): self.mode, self.payload = "update", p; return self
    def insert(self, p): self.mode, self.payload = "insert", p; return self
    def delete(self): self.mode = "delete"; return self

    def execute(self):
        t = self.db[self.name]
        if self.mode == "insert":
            row = dict(self.payload); row["id"] = len(t) + 1; t.append(row)
            return type("R", (), {"data": [row]})
        hit = [r for r in t if all(f(r) for f in self.f)]
        if self.mode == "update":
            for r in hit:
                r.update(self.payload)
            return type("R", (), {"data": hit})
        if self.mode == "delete":
            for r in hit:
                t.remove(r)
            return type("R", (), {"data": hit})
        hit.sort(key=lambda r: r.get("id", 0), reverse=self.desc)
        return type("R", (), {"data": hit[: self.lim] if self.lim else hit})


class DB(dict):
    def table(self, n): return Q(self, n)


def mk():
    db = DB(telegram_inbox=[], manual_feed_batches=[], telegram_inbox_images=[])
    return db


IMG = b"\x89PNG-fake-bytes"
GOODTXT = "代號\t日期\t收盤\n2330\t2026-10-12\t1,020\n"


def add_photo(db, i=1, created="2026-10-13T01:00:00+00:00"):
    db["telegram_inbox"].append({"id": i, "kind": "photo", "file_id": "F", "text": "", "status": "new", "created_at": created})
    db["telegram_inbox_images"].append({"inbox_id": i, "mime": "image/png", "size_bytes": len(IMG), "data_b64": base64.b64encode(IMG).decode()})


db = mk(); add_photo(db)
rows = ti.fetch_new(db)
ph, others = ti.split_pending(rows)
check("尚未辨識的圖片與一般訊息分開", len(ph) == 1 and others == [])
check("尚未辨識的圖片不會進入合併分組", ti.group_rows(others) == [])
got = ti.fetch_image(db, 1)
check("可讀回圖片位元組與格式", got == (IMG, "image/png"))
check("沒有圖片時回傳 None", ti.fetch_image(db, 99) is None)

calls = []
def fake_extractor(engine, data, mime):
    calls.append((engine, data, mime))
    return {"ok": True, "engine": engine, "text": GOODTXT, "warnings": ["「代號」信心 0.50，請對照原圖"], "error": ""}


res = ti.extract_photo(db, db["telegram_inbox"][0], "ocr", extractor=fake_extractor)
check("辨識成功並寫回文字、引擎、備註", res["ok"] and db["telegram_inbox"][0]["text"] == GOODTXT and db["telegram_inbox"][0]["engine"] == "ocr"
      and db["telegram_inbox"][0]["note"].startswith("辨識:ocr") and calls[0][:1] == ("ocr",) and calls[0][1] == IMG)
ph, others = ti.split_pending(ti.fetch_new(db))
check("辨識後這張圖進入一般分組流程", ph == [] and len(ti.group_rows(others)) == 1)
check("辨識後的內容通過驗證預覽", ti.preview_group(others, today=TODAY)["ok"])

ok = ti.save_text(db, 1, GOODTXT.replace("1,020", "1,021"))
check("人工修改辨識結果", ok and "1,021" in db["telegram_inbox"][0]["text"])
check("空白內容不覆蓋", not ti.save_text(db, 1, "   "))

st, msg = ti.commit_group(db, others, today=TODAY)
check("確認寫入成功", st == "written" and len(db["manual_feed_batches"]) == 1)
check("寫入後圖片位元組立即刪除", db["telegram_inbox_images"] == [])

# 退回也刪圖
db = mk(); add_photo(db)
check("退回成功", ti.reject_group(db, db["telegram_inbox"], "看不清楚"))
check("退回後圖片位元組立即刪除", db["telegram_inbox_images"] == [])

# 辨識失敗：不寫回文字
db = mk(); add_photo(db)
bad = ti.extract_photo(db, db["telegram_inbox"][0], "vision",
                       extractor=lambda e, d, m: {"ok": False, "engine": e, "text": "", "warnings": [], "error": "限流/額度不足"})
check("辨識失敗：回報原因且不改動內容", (not bad["ok"]) and bad["error"] == "限流/額度不足" and db["telegram_inbox"][0]["text"] == "")
# 圖片不存在
db = mk(); add_photo(db); db["telegram_inbox_images"].clear()
check("找不到圖片 → 明確失敗", not ti.extract_photo(db, db["telegram_inbox"][0], "ocr", extractor=fake_extractor)["ok"])
# 已被處理過的列不覆寫
db = mk(); add_photo(db); db["telegram_inbox"][0]["status"] = "done"
check("已處理過的列不被覆寫", not ti.extract_photo(db, db["telegram_inbox"][0], "ocr", extractor=fake_extractor)["ok"] and db["telegram_inbox"][0]["text"] == "")

# 連續：圖片辨識後的表格與上一則文字合併（共用表頭）
db = mk()
db["telegram_inbox"].append({"id": 1, "kind": "text", "text": "代號\t日期\t收盤\n2330\t2026-10-12\t1,020", "status": "new", "created_at": "2026-10-13T01:00:00+00:00"})
add_photo(db, 2, "2026-10-13T01:00:30+00:00")
ti.extract_photo(db, db["telegram_inbox"][1], "vision",
                 extractor=lambda e, d, m: {"ok": True, "engine": e, "text": "代號\t日期\t收盤\n2317\t2026-10-12\t210.5", "warnings": [], "error": ""})
grp = ti.group_rows(ti.split_pending(ti.fetch_new(db))[1])
check("文字訊息與辨識後的圖片相隔 30 秒 → 合併成一批、表頭只留一份",
      len(grp) == 1 and len(grp[0]) == 2 and ti.preview_group(grp[0], today=TODAY)["ok"] and len(ti.preview_group(grp[0], today=TODAY)["rows"]) == 2)

# ───────────── 真 OCR 冒煙測試（本機有套件與字型才跑）─────────────
try:
    import glob
    import io
    from PIL import Image, ImageDraw, ImageFont
    ok_ocr, why = I.ocr_available()
    fonts = glob.glob("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    if ok_ocr and fonts:
        font = ImageFont.truetype(fonts[0], 28, index=2)
        rows_ = [["代號", "日期", "收盤價"], ["2330", "2026-10-12", "1,020.5"], ["0050", "2026-10-12", "74.35"], ["00878", "2026-10-12", "23.18"]]
        im = Image.new("RGB", (700, 60 * len(rows_) + 40), "white")
        d = ImageDraw.Draw(im)
        for i, r in enumerate(rows_):
            for x, c in zip((30, 200, 450), r):
                d.text((x, 20 + i * 60), c, font=font, fill="black")
        buf = io.BytesIO(); im.save(buf, "PNG")
        out = I.ocr_extract(buf.getvalue())
        lines = out["text"].splitlines() if out["ok"] else []
        check("真 OCR：辨識成功且共 4 列", out["ok"] and len(lines) == 4)
        check("真 OCR：代號、日期、數字全部讀對", lines[1:] == ["2330\t2026-10-12\t1,020.5", "0050\t2026-10-12\t74.35", "00878\t2026-10-12\t23.18"])
        check("真 OCR：結果可通過表格驗證（代號、日期欄存在時）",
              ti.parse_pasted(out["text"].replace(lines[0], "代號\t日期\t收盤價", 1), today=TODAY)["ok"])
    else:
        print(f"SKIP: 本機沒有 OCR 或中文字型（{why}）")
except ImportError as e:
    print(f"SKIP: 真 OCR 冒煙測試需要 PIL：{e}")

if fails:
    print(f"\n{len(fails)} 項失敗")
    sys.exit(1)
print("\n全部通過")
