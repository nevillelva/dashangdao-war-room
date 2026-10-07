#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_revenue.py —— F2「營收動能」5 年回測（2026-10-07，跑在 GitHub Actions；結果只寫私有表 ui_selftest_reports）

問題：月營收年增率（YoY）高、且加速的股票，公告後做多，勝率／期望值是否『真的』比同一天的其他股票好？
做法（避免未來函數與自欺）：
  • 營收所屬月份 M 的公告期限是「M+1 月 10 日」前（上市櫃公司法定）。保守起見，訊號日＝M+1 月 11 日（含）之後的第一個交易日，
    收盤後確認，隔日開盤進場（比實際公告晚，不會用到還沒公布的資料）。
  • 同一個公告日，母體內『符合條件』vs『不符合條件』的股票用完全相同的出場規則比較 → 差距才是營收訊號的貢獻（扣掉當時大盤漲跌）。
  • 出場：停利／停損／最長持有日的網格（含目前實盤使用的 12%/15%/20 日），報酬已扣來回成本 0.585%；同日停損停利皆觸及先算停損（保守）。
  • 樣本內(IS)＝前 60% 日期、樣本外(OOS)＝後 40%；通過標準（全部要達到）：
      IS n≥100、OOS n≥50；IS/OOS 勝率皆 >50% 且期望值皆 >0；OOS 勝率與期望值皆贏過同日不符合條件者；
      逐年穩定（有 ≥20 筆的年份中 ≥60% 的年份勝率 >50%）。
  • 多重檢定誠實處理：另報『樣本內挑最好的一組出場 → 直接看樣本外』的結果，並與『隨機抽同樣筆數的非訊號』做同樣流程的命中率比較。
【限制】母體只含現在仍在市場的大成交值個股（存活者偏誤）；營收資料取 FinMind（可能有補件/修正，採事後版本）；
  同一天多檔訊號互相關（有效樣本比筆數少）；5 年只含一段多空循環，結論不等於未來。
"""
import os
import sys
import json
import time
import math
import argparse
import itertools
import datetime as dt

import numpy as np
import pandas as pd

import backtest_rules as br

TP_GRID = (0.05, 0.08, 0.12, 0.20)
SL_GRID = (0.08, 0.10, 0.15)
HOLD_GRID = (10, 20)
EXITS = list(itertools.product(TP_GRID, SL_GRID, HOLD_GRID))
MAX_HOLD = max(HOLD_GRID)
LIVE_EXIT = (0.12, 0.15, 20)
IS_FRACTION = 0.60
MIN_N_IS, MIN_N_OOS = 100, 50
MIN_Y_N, Y_SHARE = 20, 0.6

# 營收條件（只用公告當下已知的資訊）
FAMILIES = {
    "yoy>=20": lambda r: r["yoy"] >= 0.20,
    "yoy>=30": lambda r: r["yoy"] >= 0.30,
    "yoy>=50": lambda r: r["yoy"] >= 0.50,
    "yoy>=30且加速": lambda r: r["yoy"] >= 0.30 and r["accel"] is not None and r["accel"] >= 0.10,
    "累計3月yoy>=30且月增>0": lambda r: r["yoy3"] is not None and r["yoy3"] >= 0.30 and r["mom"] is not None and r["mom"] > 0,
    "創12月新高且yoy>=20": lambda r: r["new_high"] and r["yoy"] >= 0.20,
    # 2026-10-07 實盤規則：上面兩條各自通過後，合併成一條『營收動能』（任一成立）；合併後必須自己也通過同一套檢定
    "營收動能(新高且yoy>=20 或 累計3月yoy>=30且月增>0)": lambda r: (
        (r["new_high"] and r["yoy"] >= 0.20) or
        (r["yoy3"] is not None and r["yoy3"] >= 0.30 and r["mom"] is not None and r["mom"] > 0)),
}
LAYER_REGIMES = ["all", "no_stress", "stress", "up20", "dn20", "up60", "dn60", "b50", "b40_lo", "dd8", "dd10_deep", "calm", "wild", "shock5"]
LAYER_FAMS = ["創12月新高且yoy>=20", "累計3月yoy>=30且月增>0", "營收動能(新高且yoy>=20 或 累計3月yoy>=30且月增>0)"]


# ------------------------------------------------------------------ 營收特徵（純函式）
def announce_signal_date(year, month):
    """營收所屬 (year, month) → 訊號日期（次月 11 日；保守：法定公告期限 10 日之後）。回傳 'YYYY-MM-DD'。"""
    y, m = (year + 1, 1) if month == 12 else (year, month + 1)
    return f"{y:04d}-{m:02d}-11"


def revenue_features(rows):
    """rows：[(year, month, revenue)]（同一檔，任意順序，可重複→取最後一筆）。回傳 list[dict]：
    year, month, sig_date, yoy, mom, yoy3, accel, new_high；缺去年同月者略過。
      yoy＝本月／去年同月−1；mom＝本月／上月−1；yoy3＝近3月合計／去年同期3月合計−1；
      accel＝本月 yoy − 前 3 個月 yoy 平均（營收年增是否加速）；new_high＝本月營收為近 12 個月最高。"""
    by = {}
    for y, m, rv in rows:
        try:
            if rv is not None and float(rv) > 0:
                by[(int(y), int(m))] = float(rv)
        except (TypeError, ValueError):
            continue

    def shift(y, m, k):
        t = y * 12 + (m - 1) - k
        return t // 12, t % 12 + 1

    def yoy_of(y, m):
        a, b = by.get((y, m)), by.get((y - 1, m))
        return (a / b - 1) if a and b else None

    out = []
    for (y, m) in sorted(by):
        yoy = yoy_of(y, m)
        if yoy is None:
            continue
        prev = by.get(shift(y, m, 1))
        mom = (by[(y, m)] / prev - 1) if prev else None
        cur3 = [by.get(shift(y, m, k)) for k in range(3)]
        old3 = [by.get((shift(y, m, k)[0] - 1, shift(y, m, k)[1])) for k in range(3)]
        yoy3 = (sum(cur3) / sum(old3) - 1) if all(cur3) and all(old3) else None
        prior = [yoy_of(*shift(y, m, k)) for k in (1, 2, 3)]
        accel = (yoy - sum(prior) / 3) if all(p is not None for p in prior) else None
        prev11 = [by.get(shift(y, m, k)) for k in range(1, 12)]
        new_high = bool(all(prev11) and by[(y, m)] > max(prev11))       # 嚴格大於前 11 個月（持平不算新高）
        out.append({"year": y, "month": m, "sig_date": announce_signal_date(y, m), "yoy": yoy, "mom": mom,
                    "yoy3": yoy3, "accel": accel, "new_high": new_high})
    return out


# ------------------------------------------------------------------ 出場模擬（向量化；與 backtest_winrate_tuning.simulate_family 同邏輯，網格不同）
def simulate_exits(df, t_idx, exits=EXITS, max_hold=MAX_HOLD, cost=br.COST_ROUND_TRIP):
    """t_idx：訊號日在 df 的位置陣列（要求 t+1+max_hold ≤ len-1）。回傳 {(tp,sl,hold): 淨報酬陣列}。"""
    t_idx = np.asarray(t_idx, dtype=int)
    if len(t_idx) == 0:
        return {}
    o, h, l, c = (df[k].values for k in ("Open", "High", "Low", "Close"))
    e = t_idx + 1
    E = o[e]
    K = np.arange(max_hold)
    rows = e[:, None] + K[None, :]
    Hm, Lm, Om, Cm = (x[rows] / E[:, None] - 1 for x in (h, l, o, c))
    res = {}
    for tp, sl, hold in exits:
        H, L, O = Hm[:, :hold], Lm[:, :hold], Om[:, :hold]
        tp_hit, sl_hit = H >= tp, L <= -sl
        any_tp, any_sl = tp_hit.any(axis=1), sl_hit.any(axis=1)
        f_tp = np.where(any_tp, tp_hit.argmax(axis=1), hold + 1)
        f_sl = np.where(any_sl, sl_hit.argmax(axis=1), hold + 1)
        ret = Cm[:, hold - 1].copy()
        use_sl = any_sl & (f_sl <= f_tp)
        use_tp = any_tp & ~use_sl
        k_sl, k_tp = np.clip(f_sl, 0, hold - 1), np.clip(f_tp, 0, hold - 1)
        ar = np.arange(len(e))
        ret = np.where(use_sl, np.minimum(-sl, np.where(f_sl > 0, O[ar, k_sl], -sl)), ret)
        ret = np.where(use_tp, np.maximum(tp, np.where(f_tp > 0, O[ar, k_tp], tp)), ret)
        res[(tp, sl, hold)] = ret - cost
    return res


# ------------------------------------------------------------------ 統計
def cell(r):
    r = np.asarray(r, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n == 0:
        return {"n": 0, "win": None, "exp_pct": None}
    return {"n": int(n), "win": round(float((r > 0).mean()), 4), "exp_pct": round(float(r.mean()) * 100, 3)}


def evaluate_family(mask, dates, R, split, exit_key):
    """mask：布林（符合條件）；dates：datetime64 陣列；R：該出場的報酬陣列；split：IS/OOS 分界日。
    回傳 dict：IS/OOS 的 訊號/非訊號 cell、逐年表、是否通過。"""
    mask = np.asarray(mask, dtype=bool)
    ok = np.isfinite(R)
    ins = dates < split
    out = {}
    for nm, sel in (("IS", ins), ("OOS", ~ins)):
        out[nm] = {"sig": cell(R[sel & mask & ok]), "non": cell(R[sel & ~mask & ok])}
    years = pd.DatetimeIndex(dates).year
    yt = {}
    for y in sorted(set(years)):
        r = R[(years == y) & mask & ok]
        if len(r) >= 1:
            yt[int(y)] = cell(r)
    big = [v for v in yt.values() if v["n"] >= MIN_Y_N]
    year_ok = len(big) >= 2 and sum(1 for v in big if v["win"] > 0.5) / len(big) >= Y_SHARE
    i, o = out["IS"]["sig"], out["OOS"]["sig"]
    on = out["OOS"]["non"]
    passed = bool(i["n"] >= MIN_N_IS and o["n"] >= MIN_N_OOS and i["win"] > 0.5 and o["win"] > 0.5
                  and i["exp_pct"] > 0 and o["exp_pct"] > 0
                  and on["n"] > 0 and o["win"] > on["win"] and o["exp_pct"] > on["exp_pct"] and year_ok)
    out.update({"years": yt, "year_ok": year_ok, "passed": passed, "exit": list(exit_key)})
    return out


def run_backtest(prices, revenue, null_reps=200, seed=7):
    """prices：{sym: OHLCV df}；revenue：{sym: [(year, month, revenue)]}。回傳 report dict（不含時間戳）。"""
    recs = []     # 每列＝一次公告事件：sym, date, features, 各出場報酬
    per_exit = {k: [] for k in EXITS}
    for sym, df in prices.items():
        rows = revenue.get(sym)
        if not rows:
            continue
        feats = revenue_features(rows)
        if not feats:
            continue
        idx = df.index
        sig_dates = pd.to_datetime([f["sig_date"] for f in feats])
        pos = idx.searchsorted(sig_dates, side="left")              # 次月 11 日（含）之後第一個交易日
        keep = [(f, int(p)) for f, p in zip(feats, pos) if p < len(idx) and p + 1 + MAX_HOLD <= len(idx) - 1
                and (idx[p] - pd.Timestamp(f["sig_date"])).days <= 7]
        if not keep:
            continue
        sim = simulate_exits(df, [p for _, p in keep])
        for (f, p) in keep:
            recs.append({"sym": sym, "date": idx[p], **f})
        for k in EXITS:
            per_exit[k].append(sim[k])
    if not recs:
        return {"error": "沒有任何可用事件（營收或價格資料不足）"}
    dates = np.array([np.datetime64(r["date"]) for r in recs])
    order = np.sort(dates)
    split = order[int(len(order) * IS_FRACTION)]
    R_by_exit = {k: np.concatenate(v) for k, v in per_exit.items()}
    rows_feat = recs

    report = {"n_events": len(recs), "n_symbols": len({r["sym"] for r in recs}),
              "split": str(split)[:10], "date_range": [str(order[0])[:10], str(order[-1])[:10]],
              "families": {}, "live_exit": list(LIVE_EXIT)}
    masks = {}
    for fam, fn in FAMILIES.items():
        masks[fam] = np.array([bool(fn(r)) for r in rows_feat])
    rng = np.random.default_rng(seed)
    # 盤勢分層（實盤出場）：同一批事件依『訊號日的盤勢旗標』切開，訊號 vs 同旗標下的非訊號
    try:
        import regime as _rg
        _fl = _rg.regime_flags(_rg.regime_frame(prices))
        _ev_dates = pd.DatetimeIndex([r["date"] for r in rows_feat])
        _ev_fl = _fl.reindex(_ev_dates).fillna(False)
        Rl = R_by_exit[LIVE_EXIT]
        layers = {}
        for fam in LAYER_FAMS:
            if fam not in masks:
                continue
            lay = {}
            for rgm in LAYER_REGIMES:
                rm = _ev_fl[rgm].values.astype(bool) if rgm in _ev_fl else np.zeros(len(rows_feat), bool)
                lay[rgm] = {nm: {"sig": cell(Rl[sel & rm & masks[fam] & np.isfinite(Rl)]),
                                 "non": cell(Rl[sel & rm & ~masks[fam] & np.isfinite(Rl)])}
                            for nm, sel in (("IS", dates < split), ("OOS", dates >= split))}
            layers[fam] = lay
        report["regime_layers"] = layers
    except Exception as e:  # noqa: BLE001
        report["regime_layers_error"] = f"{type(e).__name__}: {e}"
    for fam, mask in masks.items():
        if mask.sum() == 0:
            report["families"][fam] = {"n_signal": 0}
            continue
        table = {}
        for k in EXITS:
            table[str(k)] = evaluate_family(mask, dates, R_by_exit[k], split, k)
        live = table[str(LIVE_EXIT)]
        # 樣本內挑最好出場（期望值最高且 n 夠）→ 看樣本外
        cand = [(v["IS"]["sig"]["exp_pct"], k) for k, v in table.items()
                if v["IS"]["sig"]["n"] >= MIN_N_IS and v["IS"]["sig"]["exp_pct"] is not None]
        best = max(cand)[1] if cand else None
        n_pass = sum(1 for v in table.values() if v["passed"])
        report["families"][fam] = {
            "n_signal": int(mask.sum()), "n_pass_exits": n_pass, "n_exits": len(table),
            "live_exit": {"IS": live["IS"], "OOS": live["OOS"], "years": live["years"], "passed": live["passed"], "year_ok": live["year_ok"]},
            "best_is_exit": None if best is None else {"exit": best, "IS": table[best]["IS"], "OOS": table[best]["OOS"], "passed": table[best]["passed"]},
            "passed_exits": [k for k, v in table.items() if v["passed"]][:12],
        }
        # 虛無對照：從『非訊號』隨機抽同樣筆數當成訊號，同樣流程（IS 挑最好 → OOS）看 OOS 勝率>50%且期望值>0 的比例
        hits, tot = 0, 0
        n_sig = int(mask.sum())
        pool = np.where(~mask)[0]
        if len(pool) >= n_sig and n_sig >= MIN_N_IS:
            for _ in range(null_reps):
                pick = np.zeros(len(mask), dtype=bool)
                pick[rng.choice(pool, size=n_sig, replace=False)] = True
                bestk, bestv = None, -1e9
                for k in EXITS:
                    a = cell(R_by_exit[k][pick & (dates < split)])
                    if a["n"] >= MIN_N_IS and a["exp_pct"] > bestv:
                        bestk, bestv = k, a["exp_pct"]
                if bestk is None:
                    continue
                o = cell(R_by_exit[bestk][pick & (dates >= split)])
                tot += 1
                if o["n"] >= MIN_N_OOS and o["win"] > 0.5 and o["exp_pct"] > 0:
                    hits += 1
            report["families"][fam]["null_oos_hit_rate"] = None if tot == 0 else round(hits / tot, 3)
    base = {}
    for k in (LIVE_EXIT,):
        base = {"all_events_live_exit": {"IS": cell(R_by_exit[k][dates < split]), "OOS": cell(R_by_exit[k][dates >= split])}}
    report["baseline"] = base
    return report


# ------------------------------------------------------------------ 文字摘要（公開日誌只印這個：不含個股、不含細節）
def public_summary(report):
    if report.get("error"):
        return "營收回測失敗：" + report["error"]
    ok = [f for f, v in report["families"].items() if v.get("live_exit", {}).get("passed")]
    return (f"營收回測完成：事件 {report['n_events']} 筆／{report['n_symbols']} 檔；"
            f"家族 {len(report['families'])} 組，用實盤出場通過 {len(ok)} 組（細節存私有表）")


# ------------------------------------------------------------------ 資料取得
def fetch_revenue(symbols, token, start="2020-01-01", sleep=0.2, max_fail=60, diag=None):
    """FinMind TaiwanStockMonthRevenue 逐檔抓（官方 API）。token 可為逗號分隔多組（用不到就換下一組）。
    回傳 {sym: [(year, month, revenue)]}；diag（dict）會填入失敗原因統計（不含 token）。連續失敗太多就中止。"""
    import requests
    diag = diag if diag is not None else {}
    tokens = [t.strip() for t in (token or "").split(",") if t.strip()] or [None]
    ti = 0
    out, fail = {}, 0
    for i, s in enumerate(symbols):
        done = False
        while not done:
            try:
                params = {"dataset": "TaiwanStockMonthRevenue", "data_id": s, "start_date": start}
                if tokens[ti]:
                    params["token"] = tokens[ti]
                r = requests.get("https://api.finmindtrade.com/api/v4/data", params=params, timeout=30)
                msg = ""
                try:
                    js = r.json()
                    msg = str(js.get("msg", ""))
                except ValueError:
                    js = {}
                limited = r.status_code in (402, 429) or "limit" in msg.lower() or "illegal" in msg.lower() or "level" in msg.lower()
                if limited and ti + 1 < len(tokens):
                    ti += 1                       # 這組額度用完／無效 → 換下一組再試同一檔
                    continue
                if r.status_code == 200 and js.get("data"):
                    rows = [(d.get("revenue_year"), d.get("revenue_month"), d.get("revenue")) for d in js["data"]]
                    out[s] = rows
                else:
                    fail += 1
                    key = f"HTTP{r.status_code}:{msg[:60]}"
                    diag[key] = diag.get(key, 0) + 1
                    if limited:
                        print(f"[營收] 額度/權限問題（{key}），已取得 {len(out)} 檔，停止")
                        return out
                done = True
            except Exception as e:  # noqa: BLE001
                fail += 1
                key = type(e).__name__
                diag[key] = diag.get(key, 0) + 1
                done = True
        if fail >= max_fail and not out:
            print("[營收] 連續失敗且沒有任何成功，停止")
            break
        time.sleep(sleep)
        if (i + 1) % 100 == 0:
            print(f"[營收] {i + 1}/{len(symbols)}，成功 {len(out)}")
    return out


def synthetic(n=30, seed=3):
    """離線自測：價格為隨機漫步、營收為隨機成長（兩者無關聯）→ 不該通過任何家族。"""
    prices = br.synthetic_prices(n=n, days=1300, seed=seed)
    rng = np.random.default_rng(seed)
    rev = {}
    for s, df in prices.items():
        y0 = df.index[0].year
        rows, base = [], 1e8
        for y in range(y0, df.index[-1].year + 1):
            for m in range(1, 13):
                base *= float(np.exp(rng.normal(0.005, 0.12)))
                rows.append((y, m, base))
        rev[s] = rows
    return prices, rev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=int(os.environ.get("BT_N") or 600))
    ap.add_argument("--years", type=int, default=int(os.environ.get("BT_YEARS") or 5))
    ap.add_argument("--tag", default=os.environ.get("BT_TAG", ""))
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()
    t0 = time.time()
    fetch_diag = {}
    if args.synthetic:
        prices, rev = synthetic()
    else:
        syms = br.load_universe(args.n)
        prices = br.download_prices(syms, args.years)
        rev = fetch_revenue(list(prices), os.environ.get("FINMIND_TOKEN", ""), diag=fetch_diag)
    report = run_backtest(prices, rev, null_reps=int(os.environ.get("BT_NULL_REPS") or 200))
    report["fetch_diag"] = fetch_diag
    report["ts"] = dt.datetime.now(dt.timezone.utc).isoformat()
    report["elapsed_s"] = round(time.time() - t0)
    report["n_universe"] = len(prices)
    report["n_with_revenue"] = len(rev)
    print(public_summary(report))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False)
    if args.no_upload or args.synthetic:
        return
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    sb.table("ui_selftest_reports").insert({"run_id": os.environ.get("GITHUB_RUN_ID", ""),
                                            "summary": "backtest_revenue" + (f":{args.tag}" if args.tag else ""),
                                            "report": report}).execute()
    print("✅ 已寫入 Supabase ui_selftest_reports")


if __name__ == "__main__":
    main()
