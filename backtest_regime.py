#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_regime.py —— 近 5 年、「盤勢」分層、逐族群找最高勝率組合、單檔走前驗證、進場時機比較
（2026-10-06，跑在 GitHub Actions；結果只寫私有表 ui_selftest_reports，日誌只印統計數字）

【使用者要求（10/6 第二輪）】
  1. 盤勢會影響勝率 → 進出場也要參考盤勢。
  3. 針對每個族群分別測試出最高勝率的組合，需配合盤勢；每一檔篩選股都要 5 成勝率；單一樣本拉到近五年為主。
  4. 研究如何把勝率提高到五成；若規則是死的，找出方法配合規則拉高勝率。
  7. 要更準進場 → 研究分析測試最佳進場方案。

【與 backtest_sector.py 的差別】
  · 判定窗＝最近 5 年（下載 7 年，前 2 年只給 MA/分數暖機）；樣本內(IS)＝前 60%(≈3 年，含 2022 空頭)、樣本外(OOS)＝後 40%(≈2 年)。
  · 每筆訊號多記「訊號日的盤勢旗標」（regime.py 14 種，位元遮罩）、跳空幅度、個股編號 → 可依盤勢/跳空/個股分層。
  · 同一族群×規則，再依「盤勢」切開比較，並一律與『同族群、同盤勢、同出場的隨機進場』比（把盤勢與族群本身的漲跌扣掉）。
  · 年度穩定（取代季度）：5 個年度中，筆數≥12 的年度至少 3 個、且 ≥60% 的年度勝率>50%。
  · 虛無對照：隨機抽樣訊號（NULL 家族）走完全相同的流程，量「純運氣」會產生多少假通過。
  · 盤勢效果檢定 regime_lift：旗標成立 vs 不成立的勝率差，要在樣本內、樣本外、且多數年度同號才算數。
  · 單檔檢定：樣本內單檔勝率≥50% 的個股，到樣本外還站得住嗎？（決定「單檔閘門」有沒有用）
  · 進場時機：隔日開盤／跳空分層／低接限價(-1/-2/-3%)／次日收盤確認，同樣出場、同樣成本下比較。
【成交／成本】同 backtest_sector.py：訊號日收盤後成立、隔日開盤進場、同日停利停損先算停損、做多 0.585%、做空 0.685%。
【限制】存活者偏誤、族群用現在分類、做空不模擬借券限制、限價單當日只能用日K推估（進場當天停利只計收盤）、
  盤勢旗標用母體等權重指數（非加權指數），訊號互相關使有效樣本少於筆數。
"""
import os
import sys
import json
import time
import math
import argparse

import numpy as np
import pandas as pd

import backtest_rules as br
import backtest_winrate_tuning as bt
import backtest_sector as bs
import regime as rg
import sector_map as sm

THRESH = 0.50
N_YEARS = 5
YEAR_NS = int(365.25 * 86400 * 1e9)
MIN_N_IS_SR, MIN_N_OOS_SR = 60, 40         # 族群×盤勢 層級的最低樣本
MIN_N_IS_ALL, MIN_N_OOS_ALL = 200, 120     # 全體層級
MIN_STORE_IS, MIN_STORE_OOS = 25, 15       # 低於這個連統計都不存
LIVE_MIN_N_IS, LIVE_MIN_N_OOS = 30, 20     # 實盤規則（事先指定的少數組合）
MIN_Y_N, MIN_Y_COUNT, Y_SHARE = 12, 3, 0.6
LIVE_EXIT = bs.LIVE_EXIT
LIVE_J = bs.EXITS.index(LIVE_EXIT)
NO = bs.NO
REF_EXITS = [LIVE_EXIT, (NO, NO, 20), (0.05, NO, 10), (0.03, NO, 5)]
LIVE_FAMS = dict(bs.LIVE_FAMS)
BASE = bs.BASE
ALL_ID = bs.ALL_ID
ALL_NAME = bs.ALL_NAME
NULL_PREFIX = bs.NULL_PREFIX
NULL_PS = bs.NULL_PS
ENTRY_FAMS = [BASE] + list(LIVE_FAMS.values())
ENTRY_REGIMES = ["all", "up60", "b50_up60", "up60_rise"]
GAP_BINS = [-np.inf, -0.02, -0.005, 0.005, 0.02, 0.04, np.inf]
GAP_LABELS = ["跳空低開>2%", "低開0.5~2%", "平開±0.5%", "高開0.5~2%", "高開2~4%", "高開>4%"]
ENTRY_MODES = [("open", 0.0), ("limit", 0.01), ("limit", 0.02), ("limit", 0.03), ("confirm", 0.0)]
ENTRY_LABEL = {("open", 0.0): "隔日開盤進場(現行)", ("limit", 0.01): "限價低接 -1%(以訊號日收盤計)",
               ("limit", 0.02): "限價低接 -2%", ("limit", 0.03): "限價低接 -3%",
               ("confirm", 0.0): "次日收盤確認(收紅且不低於訊號日收盤才進)"}


# ------------------------------------------------------------------ 進場時機變體（只做多）
def simulate_entry(df, t_idx, mode, x=0.0):
    """回傳 (R[n_filled, n_exit], 進場日 DatetimeIndex, keep 遮罩(長度=len(t_idx))、進場價)。只支援做多。
    open   ：隔日開盤（與 bs.simulate 完全一致）。
    limit  ：隔日限價單 L = 訊號日收盤×(1-x)，當日最低 ≤ L 才成交；開盤已 ≤ L 則以開盤成交，否則以 L 成交。
             盤中成交的情況，進場當天的停利只認「收盤」（不知道最高價發生在成交前或後，保守不計）；停損仍用當日最低（成交後才會碰到）。
    confirm：隔日收盤確認：當日收盤 ≥ 開盤 且 ≥ 訊號日收盤 才在『當日收盤』進場，從次日起計出場。"""
    o, h, l, c = (df[k].values.astype(float) for k in ("Open", "High", "Low", "Close"))
    t = np.asarray(t_idx, dtype=int)
    e = t + 1
    if mode == "open":
        R, dts = bs.simulate(df, t, "long")
        return R, dts, np.ones(len(t), bool), o[e]
    if mode == "limit":
        L = c[t] * (1.0 - x)
        keep = l[e] <= L
        t, e, L = t[keep], e[keep], L[keep]
        at_open = o[e] <= L
        E = np.where(at_open, o[e], L)
        rows = e[:, None] + np.arange(bs.MAX_HOLD)[None, :]
        Ec = E[:, None]
        Hm, Lm, Om, Cm = h[rows] / Ec - 1, l[rows] / Ec - 1, o[rows] / Ec - 1, c[rows] / Ec - 1
        Hm[:, 0] = np.where(at_open, h[e] / E - 1, c[e] / E - 1)      # 盤中成交：進場當天停利只認收盤
        R = bs.exit_returns(Hm, Lm, Om, Cm, bs.COST["long"])
        return R, df.index[e], keep, E
    if mode == "confirm":
        keep = (c[e] >= o[e]) & (c[e] >= c[t]) & (e + 1 + bs.MAX_HOLD < len(df))
        t, e = t[keep], e[keep]
        E = c[e]
        rows = (e + 1)[:, None] + np.arange(bs.MAX_HOLD)[None, :]
        Ec = E[:, None]
        Hm, Lm, Om, Cm = h[rows] / Ec - 1, l[rows] / Ec - 1, o[rows] / Ec - 1, c[rows] / Ec - 1
        R = bs.exit_returns(Hm, Lm, Om, Cm, bs.COST["long"])
        return R, df.index[e], keep, E
    raise ValueError(mode)


# ------------------------------------------------------------------ 資料累積
def build_acc(prices, sec_id, sym_id, bits_by_date, breadth, sides, eval_start, null_k, do_entry, t0):
    """acc[(side,fam)] = list of (sec int16[n], dts int64[n], R f32[n,nex], bits u32[n], sym int32[n], gap f32[n])
       acc_entry[(fam,mode,x)] = 同上（只做多；已濾掉沒成交的）；sig_dates[fam] = 該家族所有訊號的『進場日』(算成交率)。"""
    acc, acc_entry, sig_dates = {}, {}, {}
    es = np.datetime64(eval_start, "ns").astype(np.int64)
    for si, (sym, df) in enumerate(prices.items()):
        sid = sec_id[sym]
        o, c = df["Open"].values.astype(float), df["Close"].values.astype(float)
        bits = bits_by_date.reindex(df.index).fillna(1).values.astype(np.uint32)
        dns = df.index.values.astype("datetime64[ns]").astype(np.int64)
        for side in sides:
            fams = bs.FAMILY_FN[side](df, breadth)
            base_idx = fams.get(BASE, np.array([], dtype=int))
            for k in range(null_k):
                rng = np.random.default_rng(100003 * (k + 1) + 17 * si + (0 if side == "long" else 1))
                fams[f"{NULL_PREFIX}{k:02d}"] = (base_idx[rng.random(len(base_idx)) < NULL_PS[k % len(NULL_PS)]]
                                                 if len(base_idx) else base_idx)
            for fam, idx in fams.items():
                idx = np.asarray(idx, dtype=int)
                if len(idx):
                    idx = idx[dns[idx + 1] >= es]          # 只留「進場日在 5 年判定窗內」的訊號
                if len(idx) == 0:
                    continue
                R, dates = bs.simulate(df, idx, side)
                gap = (o[idx + 1] / c[idx] - 1.0).astype(np.float32)
                acc.setdefault((side, fam), []).append(
                    (np.full(len(idx), sid, dtype=np.int16), dates.values.astype("datetime64[ns]").astype(np.int64), R,
                     bits[idx], np.full(len(idx), sym_id[sym], dtype=np.int32), gap))
                if do_entry and side == "long" and fam in ENTRY_FAMS:
                    sig_dates.setdefault(fam, []).append(dates.values.astype("datetime64[ns]").astype(np.int64))
                    for mode, x in ENTRY_MODES:
                        if mode == "open":
                            continue
                        if len(idx) == 0:
                            continue
                        R2, d2, keep, _E = simulate_entry(df, idx, mode, x)
                        if len(R2) == 0:
                            continue
                        acc_entry.setdefault((fam, mode, x), []).append(
                            (np.full(len(R2), sid, dtype=np.int16), d2.values.astype("datetime64[ns]").astype(np.int64), R2,
                             bits[idx][keep], np.full(len(R2), sym_id[sym], dtype=np.int32), gap[keep]))
        if (si + 1) % 100 == 0:
            print(f"  進度 {si + 1}/{len(prices)}  {time.time() - t0:.0f}s", flush=True)
    return acc, acc_entry, sig_dates


def cat_acc(acc):
    cat = {}
    for key, parts in acc.items():
        cat[key] = tuple(np.concatenate([p[i] for p in parts], axis=0) for i in range(6))
    return cat


# ------------------------------------------------------------------ 統計
def grp_stats(R):
    n, w, e = bs.win_stats(R)
    return int(n), w.astype(np.float32), e.astype(np.float32)


def eval_groups(cat, sector_ids, es_ns, sp_ns, min_is=MIN_STORE_IS, min_oos=MIN_STORE_OOS):
    """groups[(side,fam,sid,reg)] = {"IS":(n,win[],exp[]), "OOS":(...)}；只保留 n_IS>=min_is 且 n_OOS>=min_oos 的組。"""
    groups = {}
    for (side, fam), (sec, dts, R, bits, symi, gap) in cat.items():
        is_m = (dts >= es_ns) & (dts < sp_ns)
        oos_m = dts >= sp_ns
        reg_m = {r: rg.bit_mask(bits, r) for r in rg.REGIME_NAMES}
        for sid in [ALL_ID] + list(sector_ids):
            sm_ = np.ones(len(sec), bool) if sid == ALL_ID else (sec == sid)
            if not sm_.any():
                continue
            for r in rg.REGIME_NAMES:
                m = sm_ & reg_m[r]
                mi, mo = m & is_m, m & oos_m
                ni, no_ = int(mi.sum()), int(mo.sum())
                if ni < min_is or no_ < min_oos:
                    continue
                groups[(side, fam, sid, r)] = {"IS": grp_stats(R[mi]), "OOS": grp_stats(R[mo])}
    return groups


def year_ok(dts, r, es_ns):
    yb = np.clip((dts - es_ns) // YEAR_NS, 0, N_YEARS - 1)
    ok = tot = 0
    for y in range(N_YEARS):
        m = yb == y
        if int(m.sum()) >= MIN_Y_N:
            tot += 1
            ok += int((r[m] > 0).mean() > THRESH)
    return (tot >= MIN_Y_COUNT and ok >= Y_SHARE * tot), ok, tot


def year_table(dts, r, es_ns):
    yb = np.clip((dts - es_ns) // YEAR_NS, 0, N_YEARS - 1)
    out = []
    for y in range(N_YEARS):
        m = yb == y
        n = int(m.sum())
        out.append({"y": y + 1, "n": n, "win": round(float((r[m] > 0).mean()), 4) if n else None,
                    "exp_pct": round(float(r[m].mean()) * 100, 3) if n else None})
    return out


def msk(cat, key, sid, reg):
    """(side,fam) 在某族群×盤勢下的布林遮罩（不複製大矩陣）。reg=None 不分盤勢；'not:xxx' 表示該旗標不成立。
    回傳 (m, dts, R, symi, gap)，呼叫端用 dts[m]、R[m, j] 取值。"""
    sec, dts, R, bits, symi, gap = cat[key]
    m = np.ones(len(sec), bool) if sid == ALL_ID else (sec == sid)
    if reg:
        if reg.startswith("not:"):
            m = m & ~rg.bit_mask(bits, reg[4:])
        else:
            m = m & rg.bit_mask(bits, reg)
    return m, dts, R, symi, gap


def mk_rec(g, j, extra=None):
    tp, sl, hold = bs.EXITS[j]
    o = {"label": bs.exit_label(tp, sl, hold), "tp": tp, "sl": sl, "hold": hold}
    for nm in ("IS", "OOS"):
        n, w, e = g[nm]
        o[nm] = {"n": int(n), "win": round(float(w[j]), 4), "exp_pct": round(float(e[j]) * 100, 3)}
    if extra:
        o.update(extra)
    return o


def _pct(a, q):
    return round(float(np.percentile(a, q)) * 100, 1) if len(a) else None


# ------------------------------------------------------------------ 報告
def build_report(cat, cat_entry, sig_dates, groups, prices, sector_names, sec_id, es, sp, args, src_note, t0, regime_info):
    es_ns = np.datetime64(es, "ns").astype(np.int64)
    sp_ns = np.datetime64(sp, "ns").astype(np.int64)
    sid_name = {i: n for i, n in enumerate(sector_names)}
    sid_name[ALL_ID] = ALL_NAME
    sides = [s for s in bs.SIDES if any(k[0] == s for k in cat)]
    out = {}
    for side in sides:
        out[side] = side_report(side, cat, groups, sector_names, sid_name, es_ns, sp_ns)
    rep = {"kind": "backtest_regime", "tag": args.tag, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "n_symbols": len(prices), "sector_source": src_note,
           "window": {"eval_start": str(pd.Timestamp(es).date()), "split": str(pd.Timestamp(sp).date()),
                      "end": str(max(df.index[-1] for df in prices.values()).date()), "eval_years": args.eval_years},
           "regimes": {k: rg.REGIME_LABEL[k] for k in rg.REGIME_NAMES}, "regime_now": regime_info,
           "cost": {k: round(v * 100, 3) for k, v in bs.COST.items()},
           "thresholds": {"win": THRESH, "min_n_IS_sector_regime": MIN_N_IS_SR, "min_n_OOS_sector_regime": MIN_N_OOS_SR,
                          "year_rule": f"筆數≥{MIN_Y_N}的年度≥{MIN_Y_COUNT}個，且≥{int(Y_SHARE*100)}%年度勝率>50%"},
           "market_context": market_context(prices, es, sp), "elapsed_s": round(time.time() - t0), **out}
    if cat_entry is not None and ("long" in sides):
        rep["entry_timing"] = entry_timing_report(cat, cat_entry, sig_dates, es_ns, sp_ns)
    return rep


def market_context(prices, es, sp):
    try:
        rets, half = [], []
        for df in prices.values():
            x = df["Close"]
            xe = x[x.index >= es]
            if len(xe) > 20:
                rets.append(float(xe.iloc[-1] / xe.iloc[0] - 1))
            a, b2 = x[(x.index >= es) & (x.index < sp)], x[x.index >= sp]
            if len(a) > 10 and len(b2) > 10:
                half.append((float(a.iloc[-1] / a.iloc[0] - 1), float(b2.iloc[-1] / b2.iloc[0] - 1)))
        return {"eqw_return_eval_window_pct": round(float(np.mean(rets)) * 100, 1), "median_pct": round(float(np.median(rets)) * 100, 1),
                "eqw_IS_pct": round(float(np.mean([h[0] for h in half])) * 100, 1), "eqw_OOS_pct": round(float(np.mean([h[1] for h in half])) * 100, 1)}
    except Exception as e:
        return {"err": f"{type(e).__name__}: {e}"}


def regime_lift(side, cat, es_ns, sp_ns, sid=ALL_ID, fam=BASE):
    """隨機進場(家族 BASE)在『旗標成立 vs 不成立』的勝率與期望（實盤出場），含樣本內/外與逐年；用來判斷盤勢濾網是否真有用。"""
    key = (side, fam)
    if key not in cat:
        return []
    rows = []
    for r in rg.REGIME_NAMES:
        if r == "all":
            continue
        m1_, dts_, R_, _, _ = msk(cat, key, sid, r)
        m0_, _, _, _, _ = msk(cat, key, sid, "not:" + r)
        d1, r1 = dts_[m1_], R_[m1_, LIVE_J]
        d0, r0 = dts_[m0_], R_[m0_, LIVE_J]
        row = {"regime": r, "label": rg.REGIME_LABEL[r]}
        ok = True
        for nm, lo, hi in (("IS", es_ns, sp_ns), ("OOS", sp_ns, None)):
            m1 = (d1 >= lo) & ((d1 < hi) if hi else True)
            m0 = (d0 >= lo) & ((d0 < hi) if hi else True)
            n1, n0 = int(m1.sum()), int(m0.sum())
            if n1 < 30 or n0 < 30:
                ok = False
                break
            w1, w0 = float((r1[m1] > 0).mean()), float((r0[m0] > 0).mean())
            e1, e0 = float(r1[m1].mean()), float(r0[m0].mean())
            row[nm] = {"n_on": n1, "n_off": n0, "win_on": round(w1, 4), "win_off": round(w0, 4), "lift_pp": round((w1 - w0) * 100, 1),
                       "exp_on_pct": round(e1 * 100, 3), "exp_off_pct": round(e0 * 100, 3)}
        if not ok:
            continue
        y1, y0 = year_table(d1, r1, es_ns), year_table(d0, r0, es_ns)
        yl = []
        for a, b in zip(y1, y0):
            yl.append({"y": a["y"], "n_on": a["n"], "n_off": b["n"], "win_on": a["win"], "win_off": b["win"],
                       "lift_pp": (round((a["win"] - b["win"]) * 100, 1) if (a["win"] is not None and b["win"] is not None and a["n"] >= 20 and b["n"] >= 20) else None)})
        row["years"] = yl
        lifts = [y["lift_pp"] for y in yl if y["lift_pp"] is not None]
        row["years_same_sign"] = f"{sum(1 for v in lifts if v > 0)}/{len(lifts)}"
        row["robust"] = bool(row["IS"]["lift_pp"] > 0 and row["OOS"]["lift_pp"] > 0 and len(lifts) >= 3 and sum(1 for v in lifts if v > 0) >= 0.8 * len(lifts)
                             and row["IS"]["win_on"] > THRESH and row["OOS"]["win_on"] > THRESH)
        rows.append(row)
    rows.sort(key=lambda x: -(x["IS"]["lift_pp"] + x["OOS"]["lift_pp"]))
    return rows


def regime_table(side, cat, es_ns, sp_ns, fam=BASE, sid=ALL_ID):
    """某家族在各盤勢（旗標成立時）的勝率/期望：實盤出場 + 幾個參考出場，IS/OOS/逐年。"""
    key = (side, fam)
    rows = []
    if key not in cat:
        return rows
    for r in rg.REGIME_NAMES:
        m_, dts_, R_, _, _ = msk(cat, key, sid, r)
        d = dts_[m_]
        row = {"regime": r, "label": rg.REGIME_LABEL[r]}
        for tp, sl, hold in REF_EXITS:
            j = bs.EXITS.index((tp, sl, hold))
            rj = R_[m_, j]
            e = {}
            for nm, lo, hi in (("IS", es_ns, sp_ns), ("OOS", sp_ns, None)):
                mm = (d >= lo) & ((d < hi) if hi else True)
                n = int(mm.sum())
                e[nm] = {"n": n, "win": round(float((rj[mm] > 0).mean()), 4) if n else None,
                         "exp_pct": round(float(rj[mm].mean()) * 100, 3) if n else None}
            row[bs.exit_label(tp, sl, hold)] = e
        row["years_live_exit"] = year_table(d, R_[m_, LIVE_J], es_ns)
        rows.append(row)
    return rows


def side_report(side, cat, groups, sector_names, sid_name, es_ns, sp_ns):
    nex = len(bs.EXITS)
    sectors = list(range(len(sector_names)))
    G = {k[1:]: v for k, v in groups.items() if k[0] == side}          # (fam,sid,reg) → stats
    # ---- 1) 盤勢效果（隨機進場；全體與每族群）
    lift_all = regime_lift(side, cat, es_ns, sp_ns)
    lift_sector = {}
    for sid in sectors:
        rows = regime_lift(side, cat, es_ns, sp_ns, sid=sid)
        if rows:
            lift_sector[sid_name[sid]] = [{"regime": x["regime"], "IS_lift_pp": x["IS"]["lift_pp"], "OOS_lift_pp": x["OOS"]["lift_pp"],
                                           "win_on_IS": x["IS"]["win_on"], "win_on_OOS": x["OOS"]["win_on"],
                                           "years_same_sign": x["years_same_sign"], "robust": x["robust"]} for x in rows]
    regime_persist = {}
    for x in lift_all:
        sgn = 0
        tot = 0
        for sn, rows in lift_sector.items():
            for y in rows:
                if y["regime"] == x["regime"]:
                    tot += 1
                    sgn += int(y["IS_lift_pp"] > 0 and y["OOS_lift_pp"] > 0)
        regime_persist[x["regime"]] = {"sectors_tested": tot, "sectors_lift_positive_both": sgn}
    # ---- 2) 規則 × 族群 × 盤勢：通過判定與虛無對照
    hits, null_pairs, null_pass, real_pairs, real_pass = [], 0, 0, 0, 0
    by_reg = {r: {"real_pairs": 0, "real_pass": 0, "null_pairs": 0, "null_pass": 0} for r in rg.REGIME_NAMES}
    fam_reg = {}                      # (fam,reg) → [測試族群數, 通過族群數]
    sel_real, sel_base, sel_null = [], [], []
    for (fam, sid, reg), g in G.items():
        if sid == ALL_ID:
            continue
        nI, wI, eI = g["IS"]
        nO, wO, eO = g["OOS"]
        if nI < MIN_N_IS_SR or nO < MIN_N_OOS_SR:
            continue
        is_null = fam.startswith(NULL_PREFIX)
        is_base = fam == BASE
        cand = (wI > THRESH) & (eI > 0)
        if cand.any():
            jj = int(np.argmax(np.where(cand, eI, -1e9)))
            rec = {"win_oos": float(wO[jj]), "exp_oos": float(eO[jj])}
            (sel_null if is_null else sel_base if is_base else sel_real).append(rec)
        win2 = (wI > THRESH) & (wO > THRESH)
        exp2 = (eI > 0) & (eO > 0)
        if is_base:
            beat = np.ones(nex, bool)
        else:
            bg = G.get((BASE, sid, reg))
            if bg is None:
                continue
            bnO, bwO, beO = bg["OOS"]
            beat = (eO > beO) & (wO > bwO)
        base_pass = win2 & exp2
        if not is_base:
            fam_reg.setdefault((fam, reg), [0, 0])[0] += 1
            if is_null:
                null_pairs += 1
                by_reg[reg]["null_pairs"] += 1
            else:
                real_pairs += 1
                by_reg[reg]["real_pairs"] += 1
        pair_hit = False
        if base_pass.any():
            m_, dts_, R_, _, _ = msk(cat, (side, fam), sid, reg)
            d_ = dts_[m_]
        for j in np.where(base_pass)[0]:
            ok, okq, totq = year_ok(d_, R_[m_, j], es_ns)
            if not ok:
                continue
            tier = "A" if bool(beat[j]) else "B"
            if not is_base and tier == "A":
                pair_hit = True
            if is_null:
                continue
            kind = "rule" if not is_base else ("sector_only" if reg == "all" else "regime_sector")
            r = mk_rec(g, int(j), {"sector": sid_name[sid], "family": fam, "regime": reg, "tier": tier, "kind": kind,
                                   "years_ok": f"{okq}/{totq}"})
            hits.append(r)
        if pair_hit:
            fam_reg[(fam, reg)][1] += 1
            if is_null:
                null_pass += 1
                by_reg[reg]["null_pass"] += 1
            else:
                real_pass += 1
                by_reg[reg]["real_pass"] += 1
    # 虛無家族的通過需要另外數（上面的迴圈對 null 只算 pair_hit，不存 hits）
    # ---- 3) 每族群最佳（最高勝率且穩健）
    per_sector = {}
    for h in hits:
        per_sector.setdefault(h["sector"], []).append(h)

    def rank_key(h):
        return (min(h["IS"]["win"], h["OOS"]["win"]), min(h["IS"]["exp_pct"], h["OOS"]["exp_pct"]))
    best = {}
    for sname, lst in per_sector.items():
        d = {}
        for kind in ("rule", "regime_sector", "sector_only"):
            xs = sorted([h for h in lst if h["kind"] == kind and (kind != "rule" or h["tier"] == "A")], key=rank_key, reverse=True)
            seen, keep = set(), []
            for h in xs:
                kk = (h["family"], h["regime"])
                if kk in seen:
                    continue
                seen.add(kk)
                keep.append(h)
                if len(keep) >= 3:
                    break
            d[kind] = keep
        tb = sorted([h for h in lst if h["kind"] == "rule" and h["tier"] == "B"], key=rank_key, reverse=True)[:2]
        d["rule_tierB"] = tb
        best[sname] = d
    # 規則×盤勢 在多少族群通過 vs 虛無分布
    min_sec = min(8, len(sector_names))
    nullr = {}
    for (fam, reg), (t, p) in fam_reg.items():
        if fam.startswith(NULL_PREFIX) and t >= min_sec:
            nullr.setdefault(reg, []).append(p / t)
    fam_rows = []
    for (fam, reg), (t, p) in fam_reg.items():
        if fam.startswith(NULL_PREFIX) or t < min_sec:
            continue
        nr = nullr.get(reg, [])
        p95 = float(np.percentile(nr, 95)) if nr else None
        fam_rows.append({"family": fam, "regime": reg, "sectors_tested": t, "sectors_passed": p, "rate_pct": round(p / t * 100, 1),
                         "null_p95_pct": round(p95 * 100, 1) if p95 is not None else None,
                         "above_null_p95": bool(p95 is not None and p / t > p95)})
    fam_rows.sort(key=lambda r: (-r["rate_pct"], r["family"]))

    def hit(lst):
        if not lst:
            return {"n": 0}
        return {"n": len(lst), "oos_win_gt50": round(float(np.mean([x["win_oos"] > THRESH for x in lst])), 4),
                "oos_exp_gt0": round(float(np.mean([x["exp_oos"] > 0 for x in lst])), 4),
                "oos_both": round(float(np.mean([(x["win_oos"] > THRESH) and (x["exp_oos"] > 0) for x in lst])), 4)}
    # ---- 4) 實盤兩條規則 × 族群 × 盤勢（實盤出場，事先指定）
    live = []
    for rule, fam in LIVE_FAMS.items():
        for sid in [ALL_ID] + sectors:
            for reg in rg.REGIME_NAMES:
                g = G.get((fam, sid, reg))
                if g is None:
                    continue
                nI, wI, eI = g["IS"]
                nO, wO, eO = g["OOS"]
                r0 = mk_rec(g, LIVE_J, {"rule": rule, "sector": sid_name[sid], "regime": reg})
                r0["n_enough"] = bool(nI >= LIVE_MIN_N_IS and nO >= LIVE_MIN_N_OOS)
                r0["gate_ok"] = bool(r0["n_enough"] and wI[LIVE_J] > THRESH and wO[LIVE_J] > THRESH and eI[LIVE_J] > 0 and eO[LIVE_J] > 0)
                live.append(r0)
    # ---- 5) 隨機進場在各族群×盤勢（實盤出場）
    rnd = []
    for sid in [ALL_ID] + sectors:
        for reg in rg.REGIME_NAMES:
            g = G.get((BASE, sid, reg))
            if g is None:
                continue
            rnd.append(mk_rec(g, LIVE_J, {"sector": sid_name[sid], "regime": reg}))
    # ---- 6) 單檔走前驗證：樣本內單檔勝率≥50%的個股，樣本外還站得住嗎
    persist = []
    for fam in [BASE] + list(LIVE_FAMS.values()):
        for reg in ("all", "up60", "b50_up60"):
            key = (side, fam)
            if key not in cat:
                continue
            m_, dts_, R_, symi_, _ = msk(cat, key, ALL_ID, reg)
            p = symbol_persistence(dts_[m_], R_[m_, LIVE_J], symi_[m_], es_ns, sp_ns)
            if p:
                p.update({"family": fam, "regime": reg})
                persist.append(p)
    return {"n_pairs_tested": {"real": real_pairs, "null": null_pairs},
            "pairs": {"real_passed_tierA": real_pass, "real_rate_pct": round(real_pass / real_pairs * 100, 2) if real_pairs else None,
                      "null_passed": null_pass, "null_rate_pct": round(null_pass / null_pairs * 100, 2) if null_pairs else None,
                      "expected_false_in_real": round(null_pass / null_pairs * real_pairs, 1) if null_pairs else None},
            "by_regime_pairs": by_reg,
            "selected_oos": {"real": hit(sel_real), "base": hit(sel_base), "null": hit(sel_null)},
            "regime_table_base_all": regime_table(side, cat, es_ns, sp_ns),
            "regime_lift_base_all": lift_all, "regime_lift_by_sector": lift_sector, "regime_lift_persist": regime_persist,
            "family_regime_summary": fam_rows[:120],
            "best_by_sector": best, "live_rules": live, "random_sector_regime": rnd, "symbol_persistence": persist}


def symbol_persistence(d, r, symi, es_ns, sp_ns, min_is=6):
    """同一組訊號(家族×盤勢、實盤出場)：樣本內單檔筆數≥min_is 者，依樣本內單檔勝率分『≥50%』『<50%』兩群，看兩群在樣本外的整體勝率。
    若『≥50% 群』在樣本外明顯較好，單檔閘門才有意義。"""
    is_m, oos_m = (d >= es_ns) & (d < sp_ns), d >= sp_ns
    syms = np.unique(symi)
    good, bad = [], []
    dist_is = []
    for s in syms:
        m = symi == s
        ni = int((m & is_m).sum())
        if ni < min_is:
            continue
        wi = float((r[m & is_m] > 0).mean())
        dist_is.append(wi)
        (good if wi >= THRESH else bad).append(s)
    if len(good) < 10 or len(bad) < 10:
        return None

    def agg(group):
        m = np.isin(symi, group) & oos_m
        n = int(m.sum())
        return {"n_symbols": len(group), "n_oos": n, "win_oos": round(float((r[m] > 0).mean()), 4) if n else None,
                "exp_oos_pct": round(float(r[m].mean()) * 100, 3) if n else None}
    # 全期每檔勝率分布
    allw = []
    for s in syms:
        m = symi == s
        if int(m.sum()) >= 6:
            allw.append(float((r[m] > 0).mean()))
    return {"is_good": agg(good), "is_bad": agg(bad),
            "symbols_all_period_ge50_pct": round(float(np.mean([w >= THRESH for w in allw])) * 100, 1) if allw else None,
            "symbols_all_period_median_win": round(float(np.median(allw)), 3) if allw else None, "n_symbols_all_period": len(allw)}


def _mode_stats(d, R, es_ns, sp_ns):
    row = {}
    for tp, sl, hold in REF_EXITS:
        j = bs.EXITS.index((tp, sl, hold))
        e = {}
        for nm, lo, hi in (("IS", es_ns, sp_ns), ("OOS", sp_ns, None)):
            mm = (d >= lo) & ((d < hi) if hi else True)
            n = int(mm.sum())
            e[nm] = {"n": n, "win": round(float((R[mm, j] > 0).mean()), 4) if n else None,
                     "exp_pct": round(float(R[mm, j].mean()) * 100, 3) if n else None}
        row[bs.exit_label(tp, sl, hold)] = e
    return row


def entry_timing_report(cat, cat_entry, sig_dates, es_ns, sp_ns):
    """做多：各進場方式在同樣出場下的比較（全體、依盤勢）；另算『隔日開盤』依跳空幅度分層。"""
    out = {"modes": {}, "gap": {}}
    for fam in ENTRY_FAMS:
        key = ("long", fam)
        if key not in cat:
            continue
        dsig = np.concatenate(sig_dates.get(fam, [np.array([], dtype=np.int64)]))
        tot_is = int(((dsig >= es_ns) & (dsig < sp_ns)).sum())
        tot_oos = int((dsig >= sp_ns).sum())
        for reg in ENTRY_REGIMES:
            rows = []
            for mode, x in ENTRY_MODES:
                if mode == "open":
                    m_, dts_, R_, _, _ = msk(cat, key, ALL_ID, reg)
                    d, R = dts_[m_], R_[m_]
                    fill = {"IS": 1.0, "OOS": 1.0}
                else:
                    ck = (fam, mode, x)
                    if ck not in cat_entry:
                        continue
                    sec, dts, R_, bits, symi, gap = cat_entry[ck]
                    mm_ = rg.bit_mask(bits, reg)
                    d, R = dts[mm_], R_[mm_]
                    fill = ({"IS": round(float(((dts >= es_ns) & (dts < sp_ns)).sum()) / max(1, tot_is), 3),
                             "OOS": round(float((dts >= sp_ns).sum()) / max(1, tot_oos), 3)} if reg == "all" else None)
                row = {"mode": ENTRY_LABEL[(mode, x)], **_mode_stats(d, R, es_ns, sp_ns)}
                if fill:
                    row["fill_rate"] = fill
                rows.append(row)
            out["modes"][f"{fam}｜{reg}"] = rows
        sec, dts, R, bits, symi, gap = cat[key]
        for reg in ("all", "up60"):
            m0 = rg.bit_mask(bits, reg)
            rows = []
            for lo, hi, lab in zip(GAP_BINS[:-1], GAP_BINS[1:], GAP_LABELS):
                m = m0 & (gap >= lo) & (gap < hi)
                rows.append({"gap": lab, **_mode_stats(dts[m], R[m], es_ns, sp_ns)})
            out["gap"][f"{fam}｜{reg}"] = rows
    return out


# ------------------------------------------------------------------ 給實盤用的精簡參考表
def build_ref(report):
    """壓成 system_config.regime_policy_ref_v1：盤勢旗標定義、全體隨機進場在各盤勢的勝率（含盤勢效果檢定）、
    每族群×規則×盤勢（實盤出場）的 IS/OOS 與閘門、每族群最佳組合。純函式。"""
    ref = {"asof": str(report.get("ts", ""))[:10], "window": report.get("window"), "cost": report.get("cost"),
           "regimes": report.get("regimes"), "live_exit": bs.exit_label(*LIVE_EXIT),
           "market_context": report.get("market_context"),
           "note": "近5年判定窗；樣本內前60%/樣本外後40%；勝率與期望已扣成本；期望單位為每筆淨報酬%。"}
    for side in bs.SIDES:
        r = report.get(side)
        if not r:
            continue
        d = {"lift": [{"regime": x["regime"], "IS_lift_pp": x["IS"]["lift_pp"], "OOS_lift_pp": x["OOS"]["lift_pp"],
                       "win_on_IS": x["IS"]["win_on"], "win_on_OOS": x["OOS"]["win_on"], "win_off_IS": x["IS"]["win_off"],
                       "win_off_OOS": x["OOS"]["win_off"], "years_same_sign": x["years_same_sign"], "robust": x["robust"]}
                      for x in r.get("regime_lift_base_all", [])],
             "sectors": {}}
        for row in r.get("random_sector_regime", []):
            s = d["sectors"].setdefault(row["sector"], {"random": {}, "rules": {}, "best": {}})
            s["random"][row["regime"]] = {"IS": row["IS"], "OOS": row["OOS"]}
        for row in r.get("live_rules", []):
            s = d["sectors"].setdefault(row["sector"], {"random": {}, "rules": {}, "best": {}})
            s["rules"].setdefault(row["rule"], {})[row["regime"]] = {"IS": row["IS"], "OOS": row["OOS"], "gate_ok": row["gate_ok"], "n_enough": row["n_enough"]}
        for sname, bd in (r.get("best_by_sector") or {}).items():
            s = d["sectors"].setdefault(sname, {"random": {}, "rules": {}, "best": {}})
            s["best"] = {k: [{"family": h["family"], "regime": h["regime"], "label": h["label"], "IS": h["IS"], "OOS": h["OOS"], "years_ok": h["years_ok"]}
                             for h in v] for k, v in bd.items()}
        ref[side] = d
    if "entry_timing" in report:
        ref["entry_timing_note"] = "進場時機比較見私有報告 backtest_regime（entry_timing）"
    return ref


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=int(os.environ.get("BT_N") or 900))
    ap.add_argument("--download-years", type=int, default=int(os.environ.get("BT_DL_YEARS") or 7))
    ap.add_argument("--eval-years", type=float, default=float(os.environ.get("BT_EVAL_YEARS") or 5))
    ap.add_argument("--sides", default=os.environ.get("BT_SIDES") or "long,short")
    ap.add_argument("--min-sector", type=int, default=int(os.environ.get("BT_MIN_SECTOR") or 12))
    ap.add_argument("--null-k", type=int, default=int(os.environ.get("BT_NULL_K") or 24))
    ap.add_argument("--tag", default=os.environ.get("BT_TAG") or "")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--no-entry", action="store_true")
    ap.add_argument("--json-out", default="")
    ap.add_argument("--persist-ref", action="store_true", default=(os.environ.get("BT_PERSIST_REF") == "1"))
    args = ap.parse_args()
    t0 = time.time()
    sides = [s for s in args.sides.split(",") if s in bs.SIDES]
    sb = None
    if not args.synthetic and (not args.no_upload or os.environ.get("SUPABASE_URL")):
        from supabase import create_client
        sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    prices = br.synthetic_prices() if args.synthetic else br.download_prices(br.load_universe(args.n), args.download_years)
    if len(prices) < 5:
        print("❌ 有效股票太少")
        sys.exit(1)
    smap, src = bs.load_sector_map(list(prices), sb, args.synthetic)
    final, counts = sm.merge_small(smap, list(prices), 3 if args.synthetic else args.min_sector)
    sector_names = sorted(counts, key=lambda k: (-counts[k], k))
    sid_of = {n: i for i, n in enumerate(sector_names)}
    sec_id = {s: sid_of[final[s]] for s in prices}
    sym_id = {s: i for i, s in enumerate(prices)}
    print(f"母體 {len(prices)} 檔；族群 {len(sector_names)} 個（{src}）；最大 {sector_names[0]}={counts[sector_names[0]]}")
    end = max(df.index[-1] for df in prices.values())
    eval_start = pd.Timestamp(end - pd.Timedelta(days=int(round(365.25 * args.eval_years))))
    ev_days = sorted({d for df in prices.values() for d in df.index if d >= eval_start})
    split = ev_days[int(len(ev_days) * bs.IS_FRACTION)]
    print(f"判定窗 {eval_start.date()} ~ {end.date()}（{len(ev_days)} 個交易日）；樣本內/外切點 {split.date()}；出場網格 {len(bs.EXITS)} 組；方向 {sides}", flush=True)
    F = rg.regime_frame(prices)
    flags = rg.regime_flags(F)
    bits = rg.flags_to_bits(flags)
    now_flags = flags.iloc[-1]
    regime_info = {"asof": str(flags.index[-1].date()), "flags_true": [k for k in rg.REGIME_NAMES if bool(now_flags[k])],
                   "breadth20_pct": round(float(F["b20"].iloc[-1]) * 100, 1) if pd.notna(F["b20"].iloc[-1]) else None}
    print("今日盤勢旗標：", regime_info, flush=True)
    breadth = bt.market_breadth(prices)
    acc, acc_entry, sig_dates = build_acc(prices, sec_id, sym_id, bits, breadth, sides, eval_start, args.null_k,
                                          (not args.no_entry) and ("long" in sides), t0)
    print(f"訊號模擬完成 {time.time() - t0:.0f}s；家族數 {len(acc)}", flush=True)
    cat = cat_acc(acc)
    del acc
    cat_entry = cat_acc(acc_entry) if acc_entry else None
    es_ns = np.datetime64(eval_start, "ns").astype(np.int64)
    sp_ns = np.datetime64(split, "ns").astype(np.int64)
    groups = eval_groups(cat, list(range(len(sector_names))), es_ns, sp_ns)
    print(f"分組統計完成 {time.time() - t0:.0f}s；組數 {len(groups)}", flush=True)
    report = build_report(cat, cat_entry, sig_dates, groups, prices, sector_names, sec_id, eval_start, split, args, src, t0, regime_info)
    for side in sides:
        r = report[side]
        print(f"[{side}] 規則×族群×盤勢 組：真 {r['n_pairs_tested']['real']}、虛無 {r['n_pairs_tested']['null']}；"
              f"A級通過 真 {r['pairs']['real_passed_tierA']}（{r['pairs']['real_rate_pct']}%）／虛無 {r['pairs']['null_passed']}（{r['pairs']['null_rate_pct']}%）")
        rb = [x["label"] for x in r["regime_lift_base_all"] if x["robust"]]
        print(f"[{side}] 盤勢效果穩健（樣本內外同向且多數年度同向）：{rb}")
    print(f"耗時 {time.time() - t0:.0f}s")
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as f:
            f.write("近5年盤勢回測完成：" + "；".join(f"{s} A級通過{report[s]['pairs']['real_passed_tierA']}組" for s in sides) + "（細節存私有表）\n")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False)
    if args.no_upload or args.synthetic:
        return
    sb.table("ui_selftest_reports").insert({"run_id": os.environ.get("GITHUB_RUN_ID", ""),
                                            "summary": "backtest_regime" + (f":{args.tag}" if args.tag else ""),
                                            "report": report}).execute()
    print("✅ 已寫入 Supabase ui_selftest_reports")
    if args.persist_ref:
        sb.table("system_config").upsert({"config_key": "regime_policy_ref_v1",
                                          "config_value": json.dumps(build_ref(report), ensure_ascii=False),
                                          "description": "盤勢×族群×規則 近5年回測參考表（backtest_regime.py 產生）"}, on_conflict="config_key").execute()
        print("✅ 已寫入 system_config.regime_policy_ref_v1")


if __name__ == "__main__":
    main()
