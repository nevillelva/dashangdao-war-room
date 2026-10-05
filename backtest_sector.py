#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_sector.py —— 「依族群(細產業)」分開計算做多／做空勝率，找出哪些『族群×進場規則×出場(停利/停損/持有日)』
組合在近 2 年能達到 5 成以上勝率（2026-10-06，跑在 GitHub Actions；結果只寫私有表 ui_selftest_reports）

【使用者要求（10/6）】做多做空的勝率規則改成依各類族群計算、不用全體市場算；研究分別哪種組合能達到五成以上勝率；
回測要以近 2 年資料為主，不能只看一個多月。

【資料窗】下載 5 年日K（需 360 日暖機給「官網式分數」），但「判定用」只取最近 2 年(eval_years)的訊號：
  樣本內(IS)＝評估窗前 60%，樣本外(OOS)＝後 40%（約 9~10 個月，樣本比 7:3 多）。評估窗之前的訊號只當『前期參考』，不納入判定。
【做多/做空成交】訊號日收盤後成立 → 隔日開盤進場；日K同日停利停損皆觸及 → 先停損(保守)；跳空越過停損/停利價以開盤價成交；
  做多成本＝來回 0.585%（手續費買賣各0.1425%＋賣出證交稅0.3%，不計折讓）；
  做空成本＝0.585% ＋ 0.10%（融券手續費/利息的粗估），做空以『價格下跌』為獲利。
【通過標準（沿用先前嚴格標準，但改成族群層級）】
  樣本數 IS≥80、OOS≥40；IS 與 OOS 勝率皆 >50%；IS 與 OOS 期望值皆 >0（已扣成本）；
  OOS 勝率與期望值都贏過『同族群、同方向、同出場的隨機進場基準』（扣掉族群自身漲跌趨勢）；
  逐季穩定（有≥10筆的季度中，≥60% 的季度勝率>50%，且至少 3 個季度）。
  另列『勝率達標但期望值≤0』(只是出場結構讓勝率好看、長期不賺錢)，明確標示不建議。
【多重檢定的誠實處理】族群×規則×出場 的組合數很大，純運氣也會有一些「通過」。所以報告另外算：
  『樣本內挑最好的一組出場 → 直接看樣本外表現』的命中率，並與『隨機進場基準』用同一做法的命中率比較；
  兩者差不多就代表沒有真正的優勢。
【限制】母體只含現在仍在市場的大成交值個股（存活者偏誤）；族群分類用現在的分類；做空未模擬借券/融券不足、漲停鎖死無法回補、
  除權息停資；同一天多檔訊號互相關（有效樣本比筆數少）；近 2 年的多空行情只有一段，結論不等於未來。
"""
import os
import sys
import json
import time
import math
import argparse
import itertools

import numpy as np
import pandas as pd

import backtest_rules as br
import backtest_winrate_tuning as bt
import backtest_cmd_rules as bc
import sector_map as sm

NO = 9.99
TP_GRID = (0.03, 0.05, 0.08, 0.12, 0.15, 0.20, NO)
SL_GRID = (0.05, 0.08, 0.10, 0.15, NO)
HOLD_GRID = (5, 10, 20)
EXITS = list(itertools.product(TP_GRID, SL_GRID, HOLD_GRID))
MAX_HOLD = max(HOLD_GRID)
COOLDOWN = 20
SHORT_EXTRA_COST = 0.001
THRESH = 0.50
MIN_N_IS = int(os.environ.get("BT_MIN_N_IS") or 80)
MIN_N_OOS = int(os.environ.get("BT_MIN_N_OOS") or 40)
MIN_Q_N, MIN_Q_COUNT, Q_SHARE = 10, 3, 0.6
IS_FRACTION = 0.60
BASE = "隨機進場(基準)"
NULL_K, NULL_PS = 48, (0.08, 0.15, 0.30, 0.60)   # 『虛無家族』：從隨機進場裡隨機抽 8/15/30/60% 當「訊號」，各 12 組；用來量「純運氣」會產生多少假通過
NULL_PREFIX = "NULL"
ALL_ID = -1
ALL_NAME = "全體市場(對照)"
SIDES = ("long", "short")
COST = {"long": br.COST_ROUND_TRIP, "short": br.COST_ROUND_TRIP + SHORT_EXTRA_COST}
LIVE_EXIT = (0.12, 0.15, 20)          # 目前實盤『爆量回檔』規則使用的出場


def exit_label(tp, sl, hold):
    return bc.exit_label(tp, sl, hold)


# ------------------------------------------------------------------ 統計
def wilson_lo(k, n, z=1.96):
    if n <= 0:
        return 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - r) / d


def win_stats(R):
    """R: (n, n_exit) 淨報酬矩陣 → (n, win[n_exit], exp[n_exit])"""
    n = R.shape[0]
    if n == 0:
        z = np.zeros(R.shape[1])
        return 0, z, z
    return n, (R > 0).mean(axis=0), R.mean(axis=0)


# ------------------------------------------------------------------ 成交模擬（做多/做空共用）
def simulate(df, t_idx, side):
    """對訊號日 t_idx（收盤後成立）→ 隔日開盤進場；回傳 (淨報酬矩陣 [n_sig, n_exit], 進場日期)。
    做空：把價格反向（有利=下跌），邏輯與做多完全相同，所以兩邊用同一段成交邏輯、不會各寫一套而分歧。"""
    o, h, l, c = (df[k].values.astype(float) for k in ("Open", "High", "Low", "Close"))
    e = np.asarray(t_idx, dtype=int) + 1
    E = o[e]
    rows = e[:, None] + np.arange(MAX_HOLD)[None, :]
    Ec = E[:, None]
    if side == "long":
        Hm, Lm, Om, Cm = h[rows] / Ec - 1, l[rows] / Ec - 1, o[rows] / Ec - 1, c[rows] / Ec - 1
    else:
        Hm, Lm, Om, Cm = -(l[rows] / Ec - 1), -(h[rows] / Ec - 1), -(o[rows] / Ec - 1), -(c[rows] / Ec - 1)
    cost = COST[side]
    ar = np.arange(len(e))
    out = np.empty((len(e), len(EXITS)), dtype=np.float32)
    for j, (tp, sl, hold) in enumerate(EXITS):
        H, L, O = Hm[:, :hold], Lm[:, :hold], Om[:, :hold]
        tp_hit, sl_hit = H >= tp, L <= -sl
        any_tp, any_sl = tp_hit.any(axis=1), sl_hit.any(axis=1)
        f_tp = np.where(any_tp, tp_hit.argmax(axis=1), hold + 1)
        f_sl = np.where(any_sl, sl_hit.argmax(axis=1), hold + 1)
        ret = Cm[:, hold - 1].copy()
        use_sl = any_sl & (f_sl <= f_tp)                   # 同日兩者皆觸發 → 先停損(保守)
        use_tp = any_tp & ~use_sl
        k_sl, k_tp = np.clip(f_sl, 0, hold - 1), np.clip(f_tp, 0, hold - 1)
        ret = np.where(use_sl, np.minimum(-sl, np.where(f_sl > 0, O[ar, k_sl], -sl)), ret)
        ret = np.where(use_tp, np.maximum(tp, np.where(f_tp > 0, O[ar, k_tp], tp)), ret)
        out[:, j] = ret - cost
    return out, df.index[e]


# ------------------------------------------------------------------ 進場規則家族
def _bool(m):
    return np.nan_to_num(np.asarray(m, dtype=float), nan=0.0).astype(bool)


def _mk_index_sets(F, df, eval_start):
    """布林遮罩 → 訊號日索引：先做 20 日冷卻、再只留『進場日 >= 評估窗前期起點』（保留前期供參考），且留得出 MAX_HOLD 日。"""
    n = len(df)
    res = {}
    for k, m in F.items():
        idx = np.where(m)[0]
        idx = idx[(idx >= 0) & (idx + 1 + MAX_HOLD < n)]
        res[k] = bt.apply_cooldown(idx) if len(idx) else idx
    return res


def long_families(df, breadth):
    c = df["Close"]
    n = len(df)
    s15 = br.official_score_series(c).values
    liq = br.liq_ok_array(df)
    ret3 = (c / c.shift(3) - 1).values
    ma60 = c.rolling(60).mean().values
    b = np.nan_to_num(breadth.reindex(df.index).values, nan=0.0)
    base = liq & np.isfinite(s15)
    ov = {
        "": np.ones(n, bool),
        " +寬度≥50%": b >= 0.5,
        " +3日回檔≥3%": ret3 <= -0.03,
        " +3日回檔≥5%": ret3 <= -0.05,
        " +站上MA60": c.values > ma60,
        " +分數≥12": s15 >= 12,
    }
    sigs = bc.command_signals(df)
    F = {BASE: base}
    for code, nm in bc.CMD_NAMES.items():
        for suf, m in ov.items():
            F[nm + suf] = base & sigs[code] & _bool(m)
    F["【參考】分數≥12 且3日回檔≥5%"] = base & (s15 >= 12) & (ret3 <= -0.05)
    out = _mk_index_sets(F, df, None)
    ev = br.find_chuan_e_events(df, 60, 0.4, 0.03, 3, 10, entries_only=True)
    idx = np.array(sorted({e[0] - 1 for e in ev}), dtype=int)
    if len(idx):
        idx = idx[(idx >= 0) & (idx + 1 + MAX_HOLD < n) & base[idx] & (b[idx] >= 0.4)]
    out["穿山惡龍 MA60/前漲40% +寬度≥40%"] = bt.apply_cooldown(idx) if len(idx) else idx
    return out


SHORT_NAMES = {
    "s_longblack": "空1 長黑K",
    "s_engulf": "空2 長黑吞噬",
    "s_brk20": "空3 跌破月線(MA20)",
    "s_brk60": "空4 跌破季線(MA60)",
    "s_bear": "空5 空頭排列且收在MA5下",
    "s_kd": "空6 KD高檔死叉(K>80)",
    "s_3black": "空7 三連黑",
    "s_volblack": "空8 放量收黑(量比≥1.5)",
    "s_burst": "空9 爆量(量比≥2)",
    "s_top": "空10 高檔轉折(20日漲≥25%且長黑)",
}


def short_signals(df):
    o, h, l, c, v = (df[k].values.astype(float) for k in ("Open", "High", "Low", "Close", "Volume"))
    s = pd.Series
    atr = bc._atr14(df)
    ma5, ma20, ma60 = (s(c).rolling(p).mean().values for p in (5, 20, 60))
    vr = (s(v) / s(v).rolling(5).mean()).values
    K, D = bc._kd(df)
    body = np.abs(c - o)
    sh = lambda a, k: np.r_[np.full(k, np.nan), a[:-k]]
    c1, o1, c2, o2 = sh(c, 1), sh(o, 1), sh(c, 2), sh(o, 2)
    K1, D1 = sh(K, 1), sh(D, 1)
    ma20_1, ma60_1 = sh(ma20, 1), sh(ma60, 1)
    sig = body > atr * 0.5
    black = c < o
    ret20 = c / sh(c, 20) - 1
    out = {
        "s_longblack": black & sig,
        "s_engulf": black & (c1 > o1) & (c < o1) & (o > c1) & sig,
        "s_brk20": (c < ma20) & (c1 >= ma20_1),
        "s_brk60": (c < ma60) & (c1 >= ma60_1),
        "s_bear": (ma5 < ma20) & (ma20 < ma60) & (c < ma5),
        "s_kd": (K < D) & (K1 >= D1) & (K1 > 80),
        "s_3black": black & (c1 < o1) & (c2 < o2) & (c < c1) & (c1 < c2),
        "s_volblack": (vr >= 1.5) & black & (c < c1),
        "s_burst": vr >= 2.0,
        "s_top": (ret20 >= 0.25) & black & sig,
    }
    return {k: _bool(m) for k, m in out.items()}


def short_families(df, breadth):
    c = df["Close"]
    n = len(df)
    s15 = br.official_score_series(c).values
    liq = br.liq_ok_array(df)
    ret3 = (c / c.shift(3) - 1).values
    ma60 = c.rolling(60).mean().values
    b = np.nan_to_num(breadth.reindex(df.index).values, nan=0.0)
    base = liq & np.isfinite(s15)
    ov = {
        "": np.ones(n, bool),
        " +寬度≤40%": b <= 0.4,
        " +3日反彈≥3%": ret3 >= 0.03,
        " +3日反彈≥5%": ret3 >= 0.05,
        " +收在MA60下": c.values < ma60,
        " +分數≤3": s15 <= 3,
    }
    sigs = short_signals(df)
    F = {BASE: base}
    for code, nm in SHORT_NAMES.items():
        for suf, m in ov.items():
            F[nm + suf] = base & sigs[code] & _bool(m)
    return _mk_index_sets(F, df, None)


FAMILY_FN = {"long": long_families, "short": short_families}


# ------------------------------------------------------------------ 族群對照
def load_sector_map(symbols, sb=None, synthetic=False):
    """回傳 ({symbol: 族群}, 來源說明)。優先 FinMind TaiwanStockInfo（含 token 輪替前的第一組），失敗退回 Supabase 備援快取。"""
    if synthetic:
        names = ["族群甲", "族群乙", "族群丙"]
        return {s: names[i % 3] for i, s in enumerate(symbols)}, "synthetic"
    import requests
    tokens = [t.strip() for t in (os.environ.get("FINMIND_TOKEN") or "").split(",") if t.strip()][:1] + [None]
    for tk in tokens:
        for _ in range(3):
            try:
                params = {"dataset": "TaiwanStockInfo"}
                if tk:
                    params["token"] = tk
                r = requests.get("https://api.finmindtrade.com/api/v4/data", params=params, timeout=60)
                rows = (r.json() or {}).get("data") or []
                m = sm.build_sector_map(rows)
                if len(m) > 500:
                    return m, f"FinMind TaiwanStockInfo（{len(rows)}列→{len(m)}檔）"
            except Exception as e:
                print(f"[族群] FinMind 失敗：{type(e).__name__}")
            time.sleep(2)
    if sb is not None:
        try:
            d = sb.table("system_config").select("config_value").eq("config_key", "industry_map_backup_cache").execute().data
            if d:
                flat = json.loads(d[0]["config_value"])
                m = sm.sector_map_from_flat(flat)
                if m:
                    return m, f"Supabase 備援快取（{len(m)}檔，粗分類較多）"
        except Exception as e:
            print(f"[族群] 備援快取失敗：{type(e).__name__}")
    return {}, "無"


# ------------------------------------------------------------------ 主流程
def build_acc(prices, sec_id_by_symbol, breadth, sides, t0):
    """acc[(side,fam)] = list of (sec[n], dates[n]int64, R[n,n_exit])"""
    acc = {}
    for si, (sym, df) in enumerate(prices.items()):
        sid = sec_id_by_symbol[sym]
        for side in sides:
            fams = FAMILY_FN[side](df, breadth)
            base_idx = fams.get(BASE, np.array([], dtype=int))
            for k in range(NULL_K):
                rng = np.random.default_rng(100003 * (k + 1) + 17 * si + (0 if side == "long" else 1))
                fams[f"{NULL_PREFIX}{k:02d}"] = base_idx[rng.random(len(base_idx)) < NULL_PS[k % len(NULL_PS)]] if len(base_idx) else base_idx
            for fam, idx in fams.items():
                if len(idx) == 0:
                    continue
                R, dates = simulate(df, idx, side)
                acc.setdefault((side, fam), []).append(
                    (np.full(len(idx), sid, dtype=np.int16), dates.values.astype("datetime64[ns]").astype(np.int64), R))
        if (si + 1) % 100 == 0:
            print(f"  進度 {si + 1}/{len(prices)}  {time.time() - t0:.0f}s")
    return acc


def evaluate(acc, sector_names, eval_start, split, sides):
    """對每個 (side,fam,sector) 算 IS/OOS/前期 的 n、勝率、期望（向量化 over 出場網格）。回傳 groups, cat。"""
    es, sp = np.datetime64(eval_start, "ns").astype(np.int64), np.datetime64(split, "ns").astype(np.int64)
    groups, cat = {}, {}
    for (side, fam), parts in acc.items():
        sec = np.concatenate([p[0] for p in parts])
        dts = np.concatenate([p[1] for p in parts])
        R = np.concatenate([p[2] for p in parts], axis=0)
        cat[(side, fam)] = (sec, dts, R)
        for sid in [ALL_ID] + sorted(set(sec.tolist())):
            m = np.ones(len(sec), bool) if sid == ALL_ID else (sec == sid)
            d, Rm = dts[m], R[m]
            is_m, oos_m, pre_m = (d >= es) & (d < sp), d >= sp, d < es
            g = {}
            for nm, mm in (("IS", is_m), ("OOS", oos_m), ("PRE", pre_m)):
                g[nm] = win_stats(Rm[mm])
            groups[(side, fam, sid)] = g
    return groups, cat


def quarter_stable(sec, dts, R, sid, j):
    m = np.ones(len(sec), bool) if sid == ALL_ID else (sec == sid)
    d = pd.DatetimeIndex(dts[m].astype("datetime64[ns]"))
    r = R[m, j]
    q = d.to_period("Q")
    ok_q = tot_q = 0
    for qq in sorted(set(q)):
        rr = r[np.asarray(q == qq)]
        if len(rr) >= MIN_Q_N:
            tot_q += 1
            ok_q += int((rr > 0).mean() > THRESH)
    stable = tot_q >= MIN_Q_COUNT and ok_q >= Q_SHARE * tot_q
    return stable, ok_q, tot_q


def build_report(acc, prices, sector_names, sec_id_by_symbol, eval_start, split, args, src_note, t0):
    sides = [s for s in SIDES if any(k[0] == s for k in acc)]
    groups, cat = evaluate(acc, sector_names, eval_start, split, sides)
    sid_name = {i: n for i, n in enumerate(sector_names)}
    sid_name[ALL_ID] = ALL_NAME
    nex = len(EXITS)

    def rec(side, fam, sid, j, extra=None):
        g = groups[(side, fam, sid)]
        tp, sl, hold = EXITS[j]
        o = {"sector": sid_name[sid], "family": fam, "label": exit_label(tp, sl, hold), "tp": tp, "sl": sl, "hold": hold}
        for nm in ("IS", "OOS", "PRE"):
            n, w, e = g[nm]
            o[nm] = {"n": int(n), "win": round(float(w[j]), 4), "exp_pct": round(float(e[j]) * 100, 3)}
        pr = o["PRE"]
        o["pre_ok"] = bool(pr["n"] >= 30 and pr["win"] > THRESH and pr["exp_pct"] > 0)   # 評估窗『之前』的資料也站得住腳（參考，不納入判定）
        o["OOS"]["wlo"] = round(wilson_lo(round(o["OOS"]["win"] * o["OOS"]["n"]), o["OOS"]["n"]), 4)
        bk = (side, BASE, sid)
        if bk in groups:
            bn, bw, be = groups[bk]["OOS"]
            o["base_OOS"] = {"n": int(bn), "win": round(float(bw[j]), 4), "exp_pct": round(float(be[j]) * 100, 3)}
        ak = (side, fam, ALL_ID)
        if ak in groups and sid != ALL_ID:
            an, aw, ae = groups[ak]["OOS"]
            o["ALL_OOS"] = {"n": int(an), "win": round(float(aw[j]), 4), "exp_pct": round(float(ae[j]) * 100, 3)}
        if extra:
            o.update(extra)
        return o

    out = {"long": {}, "short": {}}
    for side in sides:
        passed, tierb = [], []
        n_tests = 0
        null_tests = null_passed = null_tierb = 0
        pairs_real = pairs_real_pass = pairs_null = pairs_null_pass = 0   # 以「(規則×族群)組」為單位：一組有幾個出場通過只算一次（出場彼此高度相關）
        sel_fam, sel_base, sel_null = [], [], []     # 樣本內挑最好出場 → 樣本外結果（真規則／隨機基準／虛無家族）
        for (sd, fam, sid), g in groups.items():
            if sd != side or sid == ALL_ID:
                continue
            nI, wI, eI = g["IS"]
            nO, wO, eO = g["OOS"]
            if nI < MIN_N_IS or nO < MIN_N_OOS:
                continue
            is_null = fam.startswith(NULL_PREFIX)
            if fam != BASE and not is_null:
                n_tests += nex
                pairs_real += 1
            if is_null:
                null_tests += nex
                pairs_null += 1
            # 樣本內挑選（只看樣本內）：勝率>50% 且期望>0 → 取期望最高；再看樣本外
            cand = (wI > THRESH) & (eI > 0)
            if cand.any():
                jj = int(np.argmax(np.where(cand, eI, -1e9)))
                (sel_base if fam == BASE else sel_null if is_null else sel_fam).append(
                    {"sector": sid_name[sid], "family": fam, "win_oos": float(wO[jj]), "exp_oos": float(eO[jj]), "n_oos": int(nO)})
            if fam == BASE:
                continue
            bg = groups.get((side, BASE, sid))
            if bg is None:
                continue
            bnO, bwO, beO = bg["OOS"]
            win2 = (wI > THRESH) & (wO > THRESH)
            exp2 = (eI > 0) & (eO > 0)
            beat = (eO > beO) & (wO > bwO)
            pair_hit = False
            for j in np.where(win2 & exp2 & beat)[0]:
                stb, okq, totq = quarter_stable(*cat[(side, fam)], sid, int(j))
                if stb:
                    pair_hit = True
                    if is_null:
                        null_passed += 1
                    else:
                        passed.append(rec(side, fam, sid, int(j), {"q_ok": okq, "q_tot": totq}))
            if pair_hit:
                if is_null:
                    pairs_null_pass += 1
                else:
                    pairs_real_pass += 1
            if is_null:
                null_tierb += int((win2 & ~exp2).sum())
            else:
                for j in np.where(win2 & ~(exp2))[0]:
                    tierb.append(rec(side, fam, sid, int(j)))
        passed.sort(key=lambda r: -r["OOS"]["exp_pct"])
        tierb.sort(key=lambda r: -r["OOS"]["win"])

        def hit(lst):
            if not lst:
                return {"n": 0}
            return {"n": len(lst), "oos_win_gt50": round(float(np.mean([x["win_oos"] > THRESH for x in lst])), 4),
                    "oos_exp_gt0": round(float(np.mean([x["exp_oos"] > 0 for x in lst])), 4),
                    "oos_both": round(float(np.mean([(x["win_oos"] > THRESH) and (x["exp_oos"] > 0) for x in lst])), 4),
                    "oos_win_mean": round(float(np.mean([x["win_oos"] for x in lst])), 4)}
        per_sector = {}
        for r in passed:
            per_sector.setdefault(r["sector"], []).append(r)
        # 每族群最多留 12 筆（依樣本外期望），其餘只計數；總量另設上限
        keep = []
        for sname, lst in per_sector.items():
            keep += lst[:12]
        keep.sort(key=lambda r: -r["OOS"]["exp_pct"])
        tb_keep, seen = [], {}
        for r in tierb:
            if seen.get(r["sector"], 0) < 5:
                tb_keep.append(r)
                seen[r["sector"]] = seen.get(r["sector"], 0) + 1
        # 族群基準表：隨機進場在各族群的 IS/OOS 勝率與期望（實盤出場 + 持有10日 + 持有5日）
        ref = []
        refs = [LIVE_EXIT, (NO, NO, 10), (NO, NO, 5), (NO, NO, 20)]
        for sid in [ALL_ID] + sorted({k[2] for k in groups if k[0] == side and k[2] != ALL_ID}):
            g = groups.get((side, BASE, sid))
            if not g:
                continue
            row = {"sector": sid_name[sid], "n_symbols": int(sum(1 for v in sec_id_by_symbol.values() if sid == ALL_ID or v == sid))}
            for tp, sl, hold in refs:
                j = EXITS.index((tp, sl, hold))
                row[exit_label(tp, sl, hold)] = {nm: {"n": int(g[nm][0]), "win": round(float(g[nm][1][j]), 4),
                                                        "exp_pct": round(float(g[nm][2][j]) * 100, 3)} for nm in ("IS", "OOS")}
            ref.append(row)
        out[side] = {"n_tests": n_tests, "n_passed": len(passed), "n_tierB": len(tierb),
                     "pass_rate_pct": round(len(passed) / n_tests * 100, 3) if n_tests else None,
                     "pairs": {"real_tested": pairs_real, "real_passed": pairs_real_pass,
                               "real_rate_pct": round(pairs_real_pass / pairs_real * 100, 2) if pairs_real else None,
                               "null_tested": pairs_null, "null_passed": pairs_null_pass,
                               "null_rate_pct": round(pairs_null_pass / pairs_null * 100, 2) if pairs_null else None,
                               "expected_false_pairs_in_real": round(pairs_null_pass / pairs_null * pairs_real, 1) if pairs_null else None},
                     "null_calibration": {"n_tests": null_tests, "n_passed": null_passed, "n_tierB": null_tierb,
                                          "pass_rate_pct": round(null_passed / null_tests * 100, 3) if null_tests else None,
                                          "expected_false_pass_in_real": round(null_passed / null_tests * n_tests, 1) if null_tests else None},
                     "selected_oos_null": hit(sel_null),
                     "passed_by_sector": {k: len(v) for k, v in sorted(per_sector.items(), key=lambda kv: -len(kv[1]))},
                     "selected_oos_signals": hit(sel_fam), "selected_oos_random_baseline": hit(sel_base),
                     "passed_top": keep[:int(args.top)], "tierB_top": tb_keep[:int(args.top)], "sector_ref": ref}
    # 市場背景：等權重母體評估窗報酬
    ctx = {}
    try:
        rets = []
        for df in prices.values():
            x = df["Close"]
            x = x[x.index >= eval_start]
            if len(x) > 20:
                rets.append(float(x.iloc[-1] / x.iloc[0] - 1))
        ctx = {"eqw_return_eval_window_pct": round(float(np.mean(rets)) * 100, 1), "median_pct": round(float(np.median(rets)) * 100, 1)}
        half = []
        for df in prices.values():
            x = df["Close"]
            a, b2 = x[(x.index >= eval_start) & (x.index < split)], x[x.index >= split]
            if len(a) > 10 and len(b2) > 10:
                half.append((float(a.iloc[-1] / a.iloc[0] - 1), float(b2.iloc[-1] / b2.iloc[0] - 1)))
        if half:
            ctx["eqw_IS_pct"] = round(float(np.mean([h[0] for h in half])) * 100, 1)
            ctx["eqw_OOS_pct"] = round(float(np.mean([h[1] for h in half])) * 100, 1)
    except Exception as e:
        ctx = {"err": f"{type(e).__name__}: {e}"}
    sizes = {}
    for v in sec_id_by_symbol.values():
        sizes[sector_names[v]] = sizes.get(sector_names[v], 0) + 1
    return {"kind": "backtest_sector", "tag": args.tag, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "n_symbols": len(prices), "sector_source": src_note, "sectors": dict(sorted(sizes.items(), key=lambda kv: -kv[1])),
            "window": {"eval_start": str(pd.Timestamp(eval_start).date()), "split": str(pd.Timestamp(split).date()),
                       "end": str(max(df.index[-1] for df in prices.values()).date()), "eval_years": args.eval_years},
            "grid": {"tp": list(TP_GRID), "sl": list(SL_GRID), "hold": list(HOLD_GRID)},
            "cost": {k: round(v * 100, 3) for k, v in COST.items()},
            "thresholds": {"win": THRESH, "min_n_IS": MIN_N_IS, "min_n_OOS": MIN_N_OOS},
            "market_context": ctx, "elapsed_s": round(time.time() - t0), **out}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=int(os.environ.get("BT_N") or 600))
    ap.add_argument("--download-years", type=int, default=int(os.environ.get("BT_DL_YEARS") or 5))
    ap.add_argument("--eval-years", type=float, default=float(os.environ.get("BT_EVAL_YEARS") or 2))
    ap.add_argument("--sides", default=os.environ.get("BT_SIDES") or "long,short")
    ap.add_argument("--min-sector", type=int, default=int(os.environ.get("BT_MIN_SECTOR") or 12))
    ap.add_argument("--top", type=int, default=int(os.environ.get("BT_TOP") or 400))
    ap.add_argument("--tag", default=os.environ.get("BT_TAG") or "")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()
    t0 = time.time()
    sides = [s for s in args.sides.split(",") if s in SIDES]
    sb = None
    if not args.synthetic and not args.no_upload:
        from supabase import create_client
        sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    elif not args.synthetic and os.environ.get("SUPABASE_URL"):
        from supabase import create_client
        sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    prices = br.synthetic_prices() if args.synthetic else br.download_prices(br.load_universe(args.n), args.download_years)
    if len(prices) < 5:
        print("❌ 有效股票太少")
        sys.exit(1)
    smap, src = load_sector_map(list(prices), sb, args.synthetic)
    final, counts = sm.merge_small(smap, list(prices), 3 if args.synthetic else args.min_sector)
    sector_names = sorted(counts, key=lambda k: (-counts[k], k))
    sid_of = {n: i for i, n in enumerate(sector_names)}
    sec_id = {s: sid_of[final[s]] for s in prices}
    print(f"母體 {len(prices)} 檔；族群 {len(sector_names)} 個（{src}）；最大 {sector_names[0]}={counts[sector_names[0]]}")
    end = max(df.index[-1] for df in prices.values())
    eval_start = pd.Timestamp(end - pd.Timedelta(days=int(round(365.25 * args.eval_years))))
    ev_days = sorted({d for df in prices.values() for d in df.index if d >= eval_start})
    split = ev_days[int(len(ev_days) * IS_FRACTION)]
    print(f"評估窗 {eval_start.date()} ~ {end.date()}（{len(ev_days)} 個交易日）；樣本內/外切點 {split.date()}；出場網格 {len(EXITS)} 組；方向 {sides}")
    breadth = bt.market_breadth(prices)
    acc = build_acc(prices, sec_id, breadth, sides, t0)
    report = build_report(acc, prices, sector_names, sec_id, eval_start, split, args, src, t0)
    for side in sides:
        r = report[side]
        print(f"[{side}] 有效測試 {r['n_tests']} 組，全條件通過 {r['n_passed']} 組，勝率達標但期望≤0 {r['n_tierB']} 組")
    print(f"耗時 {time.time() - t0:.0f}s")
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as f:   # 公開頁面只放精簡統計，策略細節存私有表
            f.write("族群回測完成：" + "；".join(f"{s} 測試{report[s]['n_tests']}組/通過{report[s]['n_passed']}組" for s in sides) + "（細節存私有表）\n")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False)
    if args.no_upload or args.synthetic:
        return
    sb.table("ui_selftest_reports").insert({"run_id": os.environ.get("GITHUB_RUN_ID", ""),
                                            "summary": "backtest_sector" + (f":{args.tag}" if args.tag else ""),
                                            "report": report}).execute()
    print("✅ 已寫入 Supabase ui_selftest_reports")


if __name__ == "__main__":
    main()
