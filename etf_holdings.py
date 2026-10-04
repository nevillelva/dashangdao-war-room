#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
etf_holdings.py —— ETF 持股明細 / 基金規模(淨資產) 同步，並提供「持股重疊度」「周轉率替代指標」純函式（R99新增）

【資料來源（2026-10-04 在 GitHub Actions 實測）】
  方案A（投信官網，每日更新，含規模）
    ・元大：yuantaetfs.com/product/detail/{代號}/ratio（SPA，需無頭瀏覽器；頁面文字有 交易日期/基金資產總淨值/流通單位數/成分代碼+名稱+數量+權重）
    ・復華：fhtrust.com.tw/api/assetsExcel/{ETFxx}/{YYYYMMDD}（Excel：淨值、單位數、持股代號/股數/金額/權重）
  方案B（備援，涵蓋所有投信，但持股是月資料、只有前約50檔、沒有規模）
    ・MoneyDJ：moneydj.com/etf/x/basic/basic0007a.xdjhtm?etfid={代號}.tw（持股明細表，股票名稱/持股千股/比例）
  國泰官網(Akamai)、玩股網(Cloudflare)從 Actions 會被擋；證交所 ETF e添富條款禁止腳本下載，皆不使用。
【沒有免費來源的】受益人數、官方周轉率。周轉率改用「相鄰兩次持股快照的權重變動」自算（見 holdings_turnover）。
【資料表】etf_holdings(symbol, as_of, stock_name, stock_code, weight, shares, source)；etf_master 另加 aum_twd/units_out/aum_date/holdings_date/holdings_source。
"""
import os
import re
import io
import sys
import time
import datetime as dt

# ------------------------------------------------------------------ 純函式（可離線測試）
NUM = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def to_float(x):
    if x is None:
        return None
    m = NUM.search(str(x))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_moneydj_holdings(html):
    """MoneyDJ basic0007a：回傳 (as_of 'YYYY-MM-DD'|None, [{'stock_name','shares','weight'}])。
    持股明細拆成多張表（每張欄位：股票名稱/持股(千股)/比例/增減），全部合併。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    txt = soup.get_text(" ", strip=True)
    m = re.search(r"持股明細\s*資料日期[:：]\s*(\d{4})/(\d{2})/(\d{2})", txt)
    as_of = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
    out, seen = [], set()
    for t in soup.find_all("table"):
        rows = [[c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])] for tr in t.find_all("tr")]
        if not rows or [x.strip() for x in rows[0][:3]] != ["股票名稱", "持股(千股)", "比例"]:
            continue
        for r in rows[1:]:
            if len(r) < 3 or not r[0]:
                continue
            w = to_float(r[2])
            if w is None or r[0] in seen:
                continue
            seen.add(r[0])
            sh = to_float(r[1])
            out.append({"stock_name": r[0], "shares": (sh * 1000 if sh is not None else None), "weight": w})
    return as_of, out


def parse_yuanta_text(txt):
    """元大 ratio 頁 body 文字 → dict(as_of, nav_total, units, rows[{stock_code,stock_name,shares,weight}])。"""
    d = {"as_of": None, "aum_twd": None, "units_out": None, "rows": []}
    m = re.search(r"交易日期[:：]\s*(\d{4})/(\d{2})/(\d{2})", txt)
    if m:
        d["as_of"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"基金資產總淨值[^\d]*\$?\s*([\d,]+(?:\.\d+)?)", txt)
    if m:
        d["aum_twd"] = to_float(m.group(1))
    m = re.search(r"基金在外流通單位數[^\d]*([\d,]+)", txt)
    if m:
        d["units_out"] = to_float(m.group(1))
    i = txt.find("基金權重-股票")
    seg = txt[i:] if i >= 0 else txt
    j = seg.find("基金權重-期貨")
    if j > 0:
        seg = seg[:j]
    seen = set()
    for mm in re.finditer(r"(?<![\d.])(\d{4})\s+(\S+)\s+(\d{3,})\s+(\d{1,3}(?:\.\d+)?)(?![\d])", seg):
        code, name, qty, w = mm.group(1), mm.group(2), float(mm.group(3)), float(mm.group(4))
        if code in seen:
            continue
        seen.add(code)
        d["rows"].append({"stock_code": code, "stock_name": name, "shares": qty, "weight": w})
    return d


def parse_fhtrust_excel(content):
    """復華 Excel → dict(as_of, aum_twd, units_out, rows)。"""
    import pandas as pd
    df = pd.read_excel(io.BytesIO(content), header=None)
    d = {"as_of": None, "aum_twd": None, "units_out": None, "rows": []}
    col0 = [str(x).strip() if x == x else "" for x in df[0].tolist()]
    for i, v in enumerate(col0):
        m = re.match(r"日期[:：]\s*(\d{4})/(\d{2})/(\d{2})", v)
        if m:
            d["as_of"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        if v.startswith("基金資產淨值") and i + 1 < len(col0):
            d["aum_twd"] = to_float(col0[i + 1])
        if v.startswith("基金在外流通單位數") and i + 1 < len(col0):
            d["units_out"] = to_float(col0[i + 1])
    hdr = next((i for i, v in enumerate(col0) if v == "證券代號"), None)
    if hdr is not None:
        for i in range(hdr + 1, len(df)):
            code = col0[i]
            if not re.fullmatch(r"\d{4}", code):
                continue
            row = df.iloc[i]
            d["rows"].append({"stock_code": code, "stock_name": str(row[1]).strip(), "shares": to_float(row[2]),
                              "weight": to_float(row[4])})
    return d


def norm_key(r):
    return r.get("stock_code") or r.get("stock_name")


def weights(rows):
    out = {}
    for r in rows or []:
        w = r.get("weight")
        k = norm_key(r)
        if k and w is not None:
            out[k] = out.get(k, 0.0) + float(w)
    return out


def overlap(rows_a, rows_b):
    """加權重疊度(%) = Σ min(wA, wB)，與共同持股檔數。兩邊都只含揭露的前段持股，所以是『下限估計』。"""
    a, b = weights(rows_a), weights(rows_b)
    common = set(a) & set(b)
    return {"overlap_pct": round(sum(min(a[k], b[k]) for k in common), 2), "common": len(common),
            "n_a": len(a), "n_b": len(b), "shared": sorted(common, key=lambda k: -min(a[k], b[k]))}


def holdings_turnover(prev_rows, cur_rows):
    """相鄰兩次持股快照的單向周轉替代指標(%) = ½ Σ|w_t − w_{t−1}|（未扣價格漂移，會略高估）；另回傳新增/剔除檔數。"""
    p, c = weights(prev_rows), weights(cur_rows)
    keys = set(p) | set(c)
    t = 0.5 * sum(abs(c.get(k, 0.0) - p.get(k, 0.0)) for k in keys)
    return {"turnover_pct": round(t, 2), "added": len(set(c) - set(p)), "dropped": len(set(p) - set(c))}


def combined_exposure(holdings_by_etf, values):
    """我的持倉合併曝險：Σ_ETF (ETF市值占總市值 × 持股權重%)。holdings_by_etf={etf:[rows]}, values={etf:市值}。回傳 [(key, 名稱, 曝險%, 被幾檔持有)] 由大到小。"""
    tot = sum(v for v in values.values() if v > 0) or 0
    if not tot:
        return []
    agg, names, cnt = {}, {}, {}
    for etf, rows in holdings_by_etf.items():
        share = values.get(etf, 0) / tot
        for r in rows:
            k = norm_key(r)
            if not k or r.get("weight") is None:
                continue
            agg[k] = agg.get(k, 0.0) + share * float(r["weight"])
            names[k] = r.get("stock_name") or k
            cnt[k] = cnt.get(k, 0) + 1
    return sorted(((k, names[k], round(v, 2), cnt[k]) for k, v in agg.items()), key=lambda x: -x[2])


# ------------------------------------------------------------------ 同步（需要網路；跑在 Actions）
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Accept-Language": "zh-TW,zh;q=0.9"}
EXCL_NAME = re.compile(r"美國|美債|全球|日本|日經|韓國|中國|滬深|上證|歐洲|越南|印度|納斯達克|S&P|標普|費半|那斯達克|公司債|債|特別股|反1|正2|黃金|原油|期貨|REITs|不動產|美元|港")


def is_domestic_equity(sym, name):
    if re.search(r"[BDLRU]$", sym):
        return False
    return not EXCL_NAME.search(name or "")


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def load_name_map(token_raw):
    """股票簡稱 → 代號（FinMind TaiwanStockInfo）。失敗回空 dict（持股仍存名稱）。"""
    try:
        import validate_etf_sources as V
        rows, err = V.fm_get("TaiwanStockInfo", token_raw)
        m = {}
        for r in rows or []:
            sid, nm = str(r.get("stock_id", "")).strip(), str(r.get("stock_name", "")).strip()
            if re.fullmatch(r"\d{4}", sid) and nm:
                m.setdefault(nm, sid)
        print(f"[名稱表] {len(m)} 檔 {('錯誤:' + str(err)) if err else ''}")
        return m
    except Exception as e:
        print(f"[名稱表] 失敗 {type(e).__name__}: {e}")
        return {}


def fetch_yuanta(symbols):
    """用無頭瀏覽器載入元大各檔 ratio 頁。回傳 {symbol: parsed}。"""
    out = {}
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print(f"[元大] playwright 不可用 {e}")
        return out
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(user_agent=UA["User-Agent"], locale="zh-TW")
        for sym in symbols:
            page = ctx.new_page()
            try:
                page.goto(f"https://www.yuantaetfs.com/product/detail/{sym}/ratio", wait_until="networkidle", timeout=60000)
                try:
                    page.wait_for_selector("text=商品代碼", timeout=15000)
                except Exception:
                    pass
                # 成分股預設只列前 5 檔，需點「展開」才會列出全部
                for _ in range(6):
                    try:
                        btn = page.locator("text=/^展開$/").first
                        if btn.count() == 0 or not btn.is_visible():
                            break
                        btn.click(timeout=3000)
                        page.wait_for_timeout(800)
                    except Exception:
                        break
                d = parse_yuanta_text(page.inner_text("body"))
                if d["rows"]:
                    out[sym] = d
                else:
                    print(f"  [元大] {sym} 沒解析到持股")
            except Exception as e:
                print(f"  [元大] {sym} 失敗 {type(e).__name__}: {str(e)[:100]}")
            finally:
                page.close()
            time.sleep(0.5)
        b.close()
    print(f"[元大] 取得 {len(out)}/{len(symbols)}")
    return out


def fetch_fhtrust(symbols, today):
    import requests
    out = {}
    try:
        r = requests.get("https://www.fhtrust.com.tw/ETF/etf_detail/ETF23", headers=UA, timeout=40)
        r.encoding = "utf-8"
        idmap = {m.group(2): m.group(1) for m in re.finditer(r'for="(ETF\d+)">\s*([0-9A-Z]+)_', r.text)}
    except Exception as e:
        print(f"[復華] 清單失敗 {e}")
        return out
    for sym in symbols:
        fid = idmap.get(sym)
        if not fid:
            continue
        for back in range(0, 7):
            d0 = today - dt.timedelta(days=back)
            if d0.weekday() >= 5:
                continue
            try:
                r = requests.get(f"https://www.fhtrust.com.tw/api/assetsExcel/{fid}/{d0.strftime('%Y%m%d')}", headers=UA, timeout=40)
                if r.ok and len(r.content) > 2000 and "excel" in r.headers.get("content-type", "").lower():
                    d = parse_fhtrust_excel(r.content)
                    if d["rows"]:
                        out[sym] = d
                        break
            except Exception as e:
                print(f"  [復華] {sym} {d0} 失敗 {type(e).__name__}")
        time.sleep(0.5)
    print(f"[復華] 取得 {len(out)}/{len(symbols)}")
    return out


def fetch_moneydj(symbols, delay=1.2):
    import requests
    out = {}
    for sym in symbols:
        try:
            r = requests.get(f"https://www.moneydj.com/etf/x/basic/basic0007a.xdjhtm?etfid={sym}.tw", headers=UA, timeout=40)
            r.encoding = "utf-8"
            if r.ok:
                as_of, rows = parse_moneydj_holdings(r.text)
                if rows:
                    out[sym] = {"as_of": as_of, "rows": rows, "aum_twd": None, "units_out": None}
        except Exception as e:
            print(f"  [MoneyDJ] {sym} 失敗 {type(e).__name__}")
        time.sleep(delay)
    print(f"[MoneyDJ] 取得 {len(out)}/{len(symbols)}")
    return out


def save(sb, sym, d, source, name_map, today):
    as_of = d.get("as_of") or today.isoformat()
    rows, seen = [], set()
    for r in d["rows"]:
        if (r.get("stock_name") or "") in seen:
            continue
        seen.add(r.get("stock_name") or "")
        code = r.get("stock_code") or name_map.get(r.get("stock_name", ""))
        rows.append({"symbol": sym, "as_of": as_of, "stock_name": r.get("stock_name") or "", "stock_code": code,
                     "weight": r.get("weight"), "shares": r.get("shares"), "source": source})
    for grp in _chunks(rows, 400):
        sb.table("etf_holdings").upsert(grp, on_conflict="symbol,as_of,stock_name").execute()
    upd = {"symbol": sym, "holdings_date": as_of, "holdings_source": source}
    if d.get("aum_twd"):
        upd.update({"aum_twd": d["aum_twd"], "units_out": d.get("units_out"), "aum_date": as_of})
    upd.pop("symbol")
    sb.table("etf_master").update(upd).eq("symbol", sym).execute()
    return len(rows)


def run_sync(sb, today=None, token_raw="", full_moneydj=None):
    today = today or dt.date.today()
    master, off = [], 0
    while True:
        r = sb.table("etf_master").select("symbol,name,active,holdings_date,holdings_source").range(off, off + 999).execute()
        master += r.data or []
        if len(r.data or []) < 1000:
            break
        off += 1000
    cands = [m for m in master if m.get("active") is not False and is_domestic_equity(m["symbol"], m.get("name"))]
    print(f"[候選] 國內股票型 ETF {len(cands)} 檔")
    name_map = load_name_map(token_raw)
    yuanta = [m["symbol"] for m in cands if (m.get("name") or "").startswith("元大")]
    fh = [m["symbol"] for m in cands if (m.get("name") or "").startswith("復華")]
    got, summary = {}, {}
    yd = fetch_yuanta(yuanta)
    for s, d in yd.items():
        got[s] = save(sb, s, d, "yuanta", name_map, today)
    fd = fetch_fhtrust(fh, today)
    for s, d in fd.items():
        got[s] = save(sb, s, d, "fhtrust", name_map, today)
    # MoneyDJ 備援：issuer 沒取到的；月資料，每週一或從未取過的才抓，避免每天打一輪
    rest = [m for m in cands if m["symbol"] not in got]
    if full_moneydj is None:
        full_moneydj = today.weekday() == 0
    todo = [m["symbol"] for m in rest if full_moneydj or not m.get("holdings_date")]
    md = fetch_moneydj(todo)
    for s, d in md.items():
        got[s] = save(sb, s, d, "moneydj", name_map, today)
    summary = {"candidates": len(cands), "yuanta": len(yd), "fhtrust": len(fd), "moneydj": len(md), "saved_etfs": len(got)}
    print(f"[完成] {summary}")
    return summary


def main():
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    run_sync(sb, token_raw=os.environ.get("FINMIND_TOKEN", ""), full_moneydj=("--full" in sys.argv) or None)


if __name__ == "__main__":
    main()
