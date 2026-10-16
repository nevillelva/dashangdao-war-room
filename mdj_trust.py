"""MoneyDJ「個股法人持股」(zcl) 逐日資料：外資/投信/自營商買賣超、估計持股、持股比重。
【2026-10-16】用途：取得逐日「投信估計持股」與推算的投信持股比率（老闆指示接入）。
頁面註記：交易所不公告自營商與投信庫存，估計持股＝依每日買賣超推算，並以基金季持股明細調整 → 估計值，非官方，須標示。
投信持股比率(%) = 投信估計持股 ÷ 總股數；總股數由本頁自身推算＝(三者估計持股合計) ÷ 三大法人持股比重（不依賴外部股數表）。
自設區間只提供一年內查詢。低頻率抓取（每檔每次 1 請求、間隔 ≥1.2 秒）。
"""
from __future__ import annotations
import re
from datetime import date
from lxml import html as LH

BASE = "https://concords.moneydj.com/z/zc/zcl/zcl.djhtm"


def roc_to_iso(s: str):
    m = re.fullmatch(r"(\d{2,3})/(\d{1,2})/(\d{1,2})", (s or "").strip())
    if not m:
        return None
    y = int(m.group(1)) + 1911
    try:
        return date(y, int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def _n(s):
    s = (s or "").replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def parse(html_text: str):
    """回傳 (rows, rejected)。每列 11 欄：日期、買賣超×4、估計持股×4、外資比重、三大法人比重。
    驗證：日期可解析；數字齊全；三者估計持股相加＝合計（誤差≤1 張）；比重 0~100；三大法人比重 ≥ 外資比重。"""
    doc = LH.fromstring(html_text)
    rows, rejected = [], 0
    for tr in doc.xpath("//tr"):
        tds = [re.sub(r"\s+", "", td.text_content()) for td in tr.xpath("./td")]
        if len(tds) != 11:
            continue
        d = roc_to_iso(tds[0])
        if not d:
            continue
        v = [_n(x) for x in tds[1:]]
        if any(x is None for x in v):
            rejected += 1
            continue
        f_net, t_net, d_net, tot_net, f_sh, t_sh, d_sh, tot_sh, f_pct, inst_pct = v
        if abs((f_sh + t_sh + d_sh) - tot_sh) > 1 or not (0 <= f_pct <= 100 and 0 <= inst_pct <= 100) or inst_pct < f_pct or inst_pct <= 0:
            rejected += 1
            continue
        total_shares = tot_sh / (inst_pct / 100.0)   # 張
        rows.append({"date": d, "f_net": f_net, "t_net": t_net, "d_net": d_net, "f_sh": f_sh, "t_sh": t_sh, "d_sh": d_sh,
                     "tot_sh": tot_sh, "f_pct": f_pct, "inst_pct": inst_pct,
                     "t_pct": round(t_sh / total_shares * 100, 4), "shares_implied": round(total_shares, 0)})
    return rows, rejected


def stability_ok(rows, tol=0.5):
    """推算總股數在整段內不應劇變（增資/減資會造成真實變動，故容忍 50%，只擋解析錯位）；回傳 (ok, 最大相對偏離)。"""
    if len(rows) < 3:
        return True, 0.0
    xs = sorted(r["shares_implied"] for r in rows)
    med = xs[len(xs) // 2]
    dev = max(abs(x / med - 1) for x in xs)
    return dev <= tol, dev
