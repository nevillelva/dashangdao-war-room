# -*- coding: utf-8 -*-
"""
etf_tab.py —— 戰情室「💰 ETF月配」分頁（R99新增）
計算全部在 etf_core.py（純函式、已離線測試）；這裡只負責讀寫 Supabase 與畫面。
資料表：etf_master / etf_dividend_events（排程 etf_dividend_sync 每日寫入）、etf_trades（使用者手動記買賣）。
三張表皆開 RLS 且收回 anon/authenticated 權限，只有後端 service_role 金鑰可存取。
"""
import datetime as dt
import json

import pandas as pd
import streamlit as st

import etf_core as E
import etf_holdings as H
import etf_paste as P
import etf_dca as D
import etf_portfolio as PF
import etf_combo as CB
import etf_qc as QC

TPE = dt.timezone(dt.timedelta(hours=8))


def _today():
    return dt.datetime.now(TPE).date()


def _fetch_all(sb, table, cols="*", order=None):
    rows, off = [], 0
    while True:
        q = sb.table(table).select(cols)
        if order:
            q = q.order(order)
        r = q.range(off, off + 999).execute()
        rows += r.data or []
        if len(r.data or []) < 1000:
            break
        off += 1000
    return rows


@st.cache_data(ttl=600, show_spinner=False)
def _load_market(_sb):
    return _fetch_all(_sb, "etf_master"), _fetch_all(_sb, "etf_dividend_events")


def _cfg_read(sb, key, default):
    """system_config 讀取（壞掉回預設，不讓單一設定拖垮分頁）。"""
    try:
        r = sb.table("system_config").select("config_value").eq("config_key", key).limit(1).execute().data or []
        if r:
            v = json.loads(r[0]["config_value"]) if isinstance(r[0]["config_value"], str) else r[0]["config_value"]
            return v if isinstance(v, type(default)) else default
    except Exception:
        pass
    return default


def _cfg_save(sb, key, value):
    sb.table("system_config").upsert({"config_key": key, "config_value": json.dumps(value, ensure_ascii=False)},
                                     on_conflict="config_key").execute()


def _load_trades(sb):
    return _fetch_all(sb, "etf_trades", order="trade_date")


def _fmt_money(x):
    return "—" if x is None else f"{x:,.0f}"


def _lots(sh):
    return f"{sh / E.LOT:g}張" if sh >= E.LOT and sh % E.LOT == 0 else f"{sh:,.0f}股"


@st.cache_data(ttl=1800, show_spinner=False)
def _load_holdings(_sb, cutoff):
    """近約45天內的持股快照（各檔通常只有 1~2 個 as_of）。"""
    rows, off = [], 0
    while True:
        r = (_sb.table("etf_holdings").select("symbol,as_of,stock_code,stock_name,weight,shares,source")
             .gte("as_of", cutoff).range(off, off + 999).execute())
        rows += r.data or []
        if len(r.data or []) < 1000:
            break
        off += 1000
    return rows


def _snapshots(hrows):
    """{symbol: [(as_of, [rows])]} 由新到舊。"""
    by = {}
    for r in hrows:
        by.setdefault(r["symbol"], {}).setdefault(str(r["as_of"]), []).append(r)
    return {s: sorted(d.items(), key=lambda kv: kv[0], reverse=True) for s, d in by.items()}


def _render_holdings_tab(sb, master, name_map, held_values, today):
    try:
        hrows = _load_holdings(sb, (today - dt.timedelta(days=45)).isoformat())
    except Exception as e:
        st.error(f"讀取持股資料失敗：{type(e).__name__}: {e}")
        return
    snaps = _snapshots(hrows)
    if not snaps:
        st.info("還沒有持股資料。到 GitHub Actions 手動執行「ETF持股與規模同步」，之後每個交易日 19:50 自動更新。")
        return
    mm = {m["symbol"]: m for m in master}
    st.warning("持股資料已停止自動更新（2026-10-12）：元大、復華官網與 MoneyDJ 的使用條款不允許未經授權以程式取用或重製資料，"
               "以下為最後一次取得的快照，請看『持股日期』判斷新舊。")
    st.caption("資料來源（歷史快照）：元大/復華投信官網、MoneyDJ。重疊度是**下限估計**；沒有免費來源的『受益人數』『官方周轉率』不提供，"
               "周轉率以相鄰兩次持股快照的權重變動自算。")
    # ---- 規模/集中度/周轉
    rows = []
    for sym, lst in sorted(snaps.items()):
        cur_d, cur = lst[0]
        ws = sorted((float(r["weight"]) for r in cur if r.get("weight") is not None), reverse=True)
        m = mm.get(sym, {})
        aum = m.get("aum_twd")
        _tsmc = next((float(r["weight"]) for r in cur if str(H.norm_key(r)) == "2330" and r.get("weight") is not None), 0.0)
        _kind = {"dividend": "高股息", "cap": "市值型", "theme": "主題", "bond": "債券", "lev": "槓反"}.get(E.etf_kind(sym, name_map.get(sym, "")), "其他")
        if E.is_active_etf(sym, name_map.get(sym, "")):
            _kind = "主動式"
        row = {"代號": sym, "名稱": name_map.get(sym, ""), "類型": _kind, "集中度": E.concentration_tag(_tsmc if ws else None, sum(ws[:3]) if ws else None, sum(ws[:10]) if ws else None),
               "台積電%": round(_tsmc, 1), "規模(億)": round(float(aum) / 1e8, 1) if aum else None,
               "持股日期": cur_d, "來源": {"yuanta": "元大", "fhtrust": "復華", "moneydj": "MoneyDJ"}.get(cur[0].get("source"), cur[0].get("source")),
               "揭露檔數": len(ws), "前3大合計%": round(sum(ws[:3]), 1), "前10大合計%": round(sum(ws[:10]), 1)}
        if len(lst) > 1:
            t = H.holdings_turnover(lst[1][1], cur)
            row.update({"周轉替代%": t["turnover_pct"], "新增檔": t["added"], "剔除檔": t["dropped"], "比對基準日": lst[1][0]})
        rows.append(row)
    df = pd.DataFrame(rows)
    q = st.text_input("搜尋代號/名稱", key="etf_hold_q")
    if q:
        df = df[df["代號"].str.contains(q.upper(), na=False) | df["名稱"].str.contains(q, na=False)]
    st.dataframe(df, width="stretch", hide_index=True)
    st.markdown("---")
    # ---- 重疊度
    st.subheader("持股重疊度（加權重疊％＝共同持股取較小權重加總）")
    held_syms = [s for s, v in held_values.items() if v > 0 and s in snaps]
    default_sel = held_syms or [s for s in ("0056", "00878", "00919", "00929", "00940", "00713") if s in snaps]
    sel = st.multiselect("選擇要比較的 ETF", sorted(snaps), default=default_sel[:8], key="etf_ov_sel",
                         format_func=lambda s: f"{s} {name_map.get(s, '')}")
    if len(sel) >= 2:
        mat = {a: {b: (100.0 if a == b else H.overlap(snaps[a][0][1], snaps[b][0][1])["overlap_pct"]) for b in sel} for a in sel}
        mdf = pd.DataFrame(mat).T
        mdf.index = [f"{s} {name_map.get(s, '')}" for s in sel]
        mdf.columns = [f"{s}" for s in sel]
        st.dataframe(mdf.round(1), width="stretch")
        c1, c2 = st.columns(2)
        a = c1.selectbox("A", sel, key="etf_ov_a")
        b = c2.selectbox("B", [x for x in sel if x != a], key="etf_ov_b")
        ov = H.overlap(snaps[a][0][1], snaps[b][0][1])
        st.write(f"**{a} × {b}**：加權重疊 {ov['overlap_pct']}%，共同持股 {ov['common']} 檔（A 揭露 {ov['n_a']} 檔、B 揭露 {ov['n_b']} 檔）")
        wa, wb = H.weights(snaps[a][0][1]), H.weights(snaps[b][0][1])
        nm = {H.norm_key(r): (r.get("stock_name") or H.norm_key(r)) for s_ in (a, b) for r in snaps[s_][0][1]}
        st.dataframe(pd.DataFrame([{"股票": f"{k} {nm.get(k, '')}", f"{a}權重%": wa[k], f"{b}權重%": wb[k]} for k in ov["shared"][:30]]),
                     width="stretch", hide_index=True)
        high = [(a1, b1, mat[a1][b1]) for i, a1 in enumerate(sel) for b1 in sel[i + 1:] if mat[a1][b1] >= 50]
        if high:
            st.warning("重疊 ≥50% 的組合（買兩檔等於幾乎買同一籃子）：" + "；".join(f"{x}×{y} {v:.0f}%" for x, y, v in high))
    else:
        st.info("至少選兩檔。")
    # ---- 我的合併曝險
    st.markdown("---")
    st.subheader("我的持倉合併曝險（把所有持有的 ETF 攤開，看實際壓在哪幾檔股票）")
    hv = {s: v for s, v in held_values.items() if v > 0}
    if not hv:
        st.info("還沒有持倉（到「買賣紀錄」輸入後這裡會自動計算）。")
    else:
        miss = [s for s in hv if s not in snaps]
        exp = H.combined_exposure({s: snaps[s][0][1] for s in hv if s in snaps}, {s: v for s, v in hv.items() if s in snaps})
        if miss:
            st.caption("以下持有的 ETF 沒有持股資料，未納入曝險：" + "、".join(miss))
        if exp:
            st.dataframe(pd.DataFrame([{"股票": f"{k} {n}", "合併曝險%": v, "被幾檔ETF持有": c} for k, n, v, c in exp[:20]]),
                         width="stretch", hide_index=True)
            top = exp[0]
            if top[2] >= 10:
                st.warning(f"單一股票 {top[1]} 占你整體 ETF 部位約 {top[2]}%，集中度偏高。")
            st.caption("曝險＝各檔 ETF 市值占比 × 該檔持股權重；只含各 ETF 揭露的持股，現金/期貨未計。")


# ====================================================================== 領息規劃器（2026-10-06 改版：資金→選股→每月實領→稅後，一頁完成）
_KIND_ORDER = ["dividend", "cap", "theme", "bond"]
_RANK_OPTS = {"殖利率高": "yield", "近1年含息總報酬高": "total", "Sharpe 高": "sharpe", "最大回撤小": "lowrisk"}
_GROUP_NOTE = {"A": "除息 1/4/7/10 月", "B": "除息 2/5/8/11 月", "C": "除息 3/6/9/12 月", "M": "月配"}


def _cand_label(c):
    tot = f"{c['ret_1y_total']:.0f}%" if c.get("ret_1y_total") is not None else "—"
    dd = f"{c['mdd']:.0f}%" if c.get("mdd") is not None else "—"
    return (f"{c['symbol']} {c['name']}｜{E.KIND_LABEL.get(c['kind'], '')}/{E.FREQ_LABEL.get(c['freq'], '')}"
            f"｜殖利率 {c['yield_pct']:.1f}%｜近1年含息 {tot}｜回撤 {dd}")


def _render_planner(master, events, today, apply_nhi, apply_fee, default_ratio, sb=None):
    cands_all = E.candidate_table(master, events, today)
    if not cands_all:
        st.info("尚無候選 ETF（等待資料同步）。")
        return
    st.caption("三步：① 填本金與稅率 → ② 挑 ETF（高股息、市值型都可以，也可混搭）→ ③ 看每月實領與稅。"
               "所有金額都用『近 12 個月實際配息』推算（保守口徑），過去不代表未來。")
    st.subheader("① 資金與稅")
    mode = st.radio("你想知道什麼", ["💰 我有本金，每月能領多少", "🎯 我要每月領 X，要準備多少本金", "📦 我決定買 N 張，每月實領多少"],
                    horizontal=True, key="etfp_mode")
    by_lots = mode.startswith("📦")      # 直接輸入張數（社群貼文常見：「他用 1 檔 25 張達標」→ 拿同一把尺驗證）
    forward = mode.startswith("💰") or by_lots
    c1, c2, c3 = st.columns(3)
    if by_lots:
        capital = None
        target = None
        c1.caption("下方每一檔直接填『買幾張』；本金＝張數 × 現價，不需另外輸入。")
    elif forward:
        capital = c1.number_input("本金(元)", min_value=50_000, max_value=500_000_000, value=700_000, step=50_000, key="etfp_capital")
        target = None
    else:
        capital = None
        target = c1.number_input("目標『稅後』每月實領(元)", min_value=1_000, max_value=2_000_000, value=20_000, step=1_000, key="etfp_target")
    lot_mode = c2.radio("買進單位", ["整張(1000股)", "零股(1股)"], horizontal=True, key="etfp_lot")
    bracket_pct = c3.select_slider("你的綜所稅邊際稅率", options=[0, 5, 12, 20, 30, 40], value=12, key="etfp_bracket",
                                   format_func=lambda x: f"{x}%",
                                   help="看你『綜合所得淨額』落在哪個級距（約：60 萬內 5%、約 60~135 萬 12%、約 135~270 萬 20%、約 270~500 萬 30%、更高 40%；"
                                        "門檻每隔幾年隨物價微調，確切金額以財政部當年度公告為準）。"
                                        "不確定就選 12%。邊際稅率 ≤30% 時合併計稅（抵減 8.5%）通常比分開計稅 28% 省。")
    lot = E.LOT if lot_mode.startswith("整張") else 1
    f1, f2, f3 = st.columns(3)
    kinds = f1.multiselect("納入的 ETF 類型", _KIND_ORDER, default=["dividend", "cap"], format_func=lambda k: E.KIND_LABEL[k], key="etfp_kinds")
    rank_label = f2.selectbox("自動建議的排序依據", list(_RANK_OPTS), key="etfp_rank",
                              help="只用過去事實排序，不預測。只看殖利率容易買到『配息高但價差下跌』的標的，建議對照『近1年含息總報酬』。")
    max_y = f3.slider("排除殖利率高於(%)", 5, 30, 15, key="etfp_maxy", help="過高殖利率常含本金返還或一次性收益，不具延續性。")
    with st.expander("進階篩選", expanded=False):
        a1, a2, a3 = st.columns(3)
        allow_young = a1.checkbox("納入上市未滿一年的標的", value=False, key="etfp_young")
        include_active = a2.checkbox("納入主動式 ETF（代號 A 結尾）", value=False, key="etfp_active")
        min_ev = a3.slider("高股息類：近12月至少配息次數", 1, 12, 3, key="etfp_minev")
    rank_by = _RANK_OPTS[rank_label]
    pool = E.filter_candidates(cands_all, tuple(kinds), include_bond=("bond" in kinds), include_active=include_active,
                               allow_young=allow_young, max_yield_pct=max_y, min_events=min_ev)
    pool.sort(key=lambda c: (-E.rank_value(c, rank_by), -c["yield_pct"], c["symbol"]))
    if not pool:
        st.info("目前的篩選條件下沒有候選。放寬類型、殖利率上限或進階篩選。")
        return

    st.subheader("② 挑選要買的 ETF")
    pm = st.radio("挑選方式", ["🪜 季配三檔錯開（A/B/C，每月都有入帳）", "📅 月配", "✋ 自己挑（任意 1~8 檔）"], key="etfp_pickmode")
    cmap = {_cand_label(c): c for c in pool}
    picks = []
    if pm.startswith("🪜"):
        st.caption("A＝除息 1/4/7/10 月、B＝2/5/8/11 月、C＝3/6/9/12 月（例：0056＝A、00878＝B、00919＝C）；各組挑一檔，全年 12 個月都有配息入帳。"
                   "預設是所選排序依據的第一名，可自行更換；要某檔不在清單裡，改用『自己挑』。")
        lad = E.suggest_ladder(pool, rank_by, top=8)
        cols = st.columns(3)
        for col, g in zip(cols, ("A", "B", "C")):
            opts = [_cand_label(c) for c in lad[g]]
            ch = col.selectbox(f"{g} 組（{_GROUP_NOTE[g]}）", ["（不選）"] + opts, index=1 if opts else 0, key=f"etfp_ladder_{g}")
            if ch in cmap:
                picks.append(cmap[ch])
            if not opts:
                col.caption("此組目前沒有符合條件的標的。")
        caps = [c for c in pool if c["kind"] == "cap"]
        if caps:
            ch = st.selectbox("＋ 加一檔市值型（選配：配息少，但價差成長是主要來源；價差目前停徵所得稅）",
                              ["（不加）"] + [_cand_label(c) for c in caps[:15]], key="etfp_cap_extra")
            if ch in cmap:
                picks.append(cmap[ch])
    elif pm.startswith("📅"):
        monthly = [c for c in pool if c["freq"] == "monthly"]
        if not monthly:
            st.info("目前篩選下沒有月配 ETF。")
        sel = st.multiselect("月配 ETF（1~4 檔）", [_cand_label(c) for c in monthly], default=[_cand_label(c) for c in monthly[:1]],
                             max_selections=4, key="etfp_monthly")
        picks = [cmap[x] for x in sel if x in cmap]
    else:
        sel = st.multiselect("任意挑 1~8 檔（依上面的排序依據排列）", [_cand_label(c) for c in pool], max_selections=8, key="etfp_free")
        picks = [cmap[x] for x in sel if x in cmap]
    if not picks:
        st.info("請至少選一檔。")
        return
    kw = dict(default_ratio=default_ratio, apply_nhi=apply_nhi, apply_fee=apply_fee, bracket=bracket_pct / 100.0)
    if by_lots:
        st.caption(f"每檔買進張數（{'1 張＝1000 股' if lot != 1 else '零股模式下 1 單位＝1 股'}）：")
        lcols = st.columns(min(len(picks), 4))
        share_map = {}
        for i, p in enumerate(picks):
            n_ = lcols[i % len(lcols)].number_input(f"{p['symbol']} {p['name']}（現價 {p['price']:g}）", 0, 100_000, 25 if len(picks) == 1 else 10, 1,
                                                    key=f"etfp_lots_{p['symbol']}")
            share_map[p["symbol"]] = int(n_) * (lot if lot != 1 else 1)
        _sig_part = sorted(share_map.items())
        _calc = (lambda sm=share_map: E.evaluate_holdings(picks, sm, **kw) if any(v > 0 for v in sm.values()) else None)
    else:
        st.caption("占比（%，自動正規化）：" + ("每檔分到多少『本金』" if forward else "每檔分擔多少『每月領息』"))
        wcols = st.columns(min(len(picks), 4))
        weights = {}
        for i, p in enumerate(picks):
            weights[p["symbol"]] = wcols[i % len(wcols)].number_input(f"{p['symbol']} {p['name']}", 0, 100, int(round(100 / len(picks))), 5,
                                                                        key=f"etfp_w_{p['symbol']}")
        if sum(weights.values()) <= 0:
            weights = None
        _sig_part = sorted((weights or {}).items())
        if forward:
            _calc = (lambda w=weights: E.plan_by_capital(capital, picks, w, lot=lot, **kw))
        else:
            _calc = (lambda w=weights: E.plan_for_after_tax(target, picks, w, lot=lot, **kw))
    # 【2026-10-10 老闆指示】選完參數按『計算』才跑重運算；之後任何輸入變更都要重新按計算，不會每點一下就跑
    _sig = repr((by_lots, capital, target, lot, bracket_pct, mode, sorted(p["symbol"] for p in picks), _sig_part,
                 float(default_ratio), bool(apply_nhi), bool(apply_fee), tuple(sorted(kinds)), rank_label, max_y, min_ev, allow_young, include_active))
    st.subheader("③ 結果")
    if st.button("🧮 計算 / 重新計算", type="primary", key="etfp_calc_btn"):
        st.session_state["etfp_sig"] = _sig
    if st.session_state.get("etfp_sig") != _sig:
        st.info("設定好後按上方『🧮 計算 / 重新計算』才會計算；目前設定尚未計算，或已變更。")
        return
    r = _calc()
    if not r or not r["rows"]:
        st.warning("資金不夠買到所選標的（整張）或標的沒有配息資料。可改『零股』、增加本金或換標的。")
        return
    m = st.columns(4)
    m[0].metric("實際投入", _fmt_money(r["cost"]))
    m[1].metric("平均每月實領（稅前）", _fmt_money(r["monthly_avg_net"]), help="已扣二代健保補充保費與匯費，未扣綜所稅")
    m[2].metric("平均每月（再扣綜所稅後）", _fmt_money(r["monthly_avg_after_tax"]))
    m[3].metric("稅後年化殖利率", f"{r['yield_after_tax_pct']:.2f}%")
    st.caption(f"買進手續費約 {r['buy_fee']:,.0f}" + (f"｜剩餘現金 {r['cash_left']:,.0f}" if (forward and not by_lots) else "")
               + f"｜全年稅後實領 {r['annual_net_after_tax']:,.0f}｜稅前毛殖利率 {r['yield_gross_pct']:.2f}%")
    if not forward and not r.get("achieved"):
        st.warning("以目前所選標的與占比，反推 10 輪仍未達到目標，數字僅供參考（可能配息資料不足）。")
    st.bar_chart(pd.DataFrame({"實領（稅前）": [r["monthly_net"][k] for k in range(1, 13)]}, index=[f"{k:02d}月" for k in range(1, 13)]))
    st.caption("橫軸是『入帳（發放）月份』，由近 12 個月每次配息的發放月推算；已逐檔逐次扣補充保費與匯費。")
    if r["months_with_income"] < 12:
        st.warning(f"這個組合一年只有 {r['months_with_income']} 個月有入帳；要『每月都有』請 A/B/C 各選一檔，或選月配 ETF。")
    _src_map = {p["symbol"]: p.get("ratio_src") or "" for p in picks}
    st.dataframe(pd.DataFrame([{"代號": x["symbol"], "名稱": x["name"], "類型": E.KIND_LABEL.get(x["kind"], ""),
                                "配息節奏": E.FREQ_LABEL.get(x["freq"], ""), "組": x["ex_group"] or "",
                                "現價": x["price"], "買進": _lots(x["shares"]), "資金": round(x["capital"]),
                                "年領(稅前)": round(x["annual_gross"]), "殖利率%": round(x["yield_pct"], 2),
                                "每次約領": round(x["per_payment_avg"]), "單次54C基數": x["max_payment_base"],
                                "採用54C占比%": round(x["ratio_used"] * 100, 1),
                                "占比來源": _src_map.get(x["symbol"], "")} for x in r["rows"]]),
                 width="stretch", hide_index=True)
    st.caption("占比來源：" + E.RATIO_SRC_HINT)
    # C4：槓桿／反向商品
    _lev = [p["symbol"] for p in picks if E.is_leveraged(p["symbol"], p["name"])]
    if _lev:
        st.error("含槓桿／反向商品：" + "、".join(_lev) + "。這類商品每日重設、無配息，長期持有會因波動產生耗損；"
                 "『每月領息』對它不適用，請當成短期交易工具，不要放進領息組合。")
    # C3：小額配息的匯費占比
    _small = [x for x in r["rows"] if x.get("per_payment_avg") is not None and float(x["per_payment_avg"]) < 2000]
    if _small:
        _worst = min(_small, key=lambda x: float(x["per_payment_avg"]))
        _share = E.fee_share(float(_worst["per_payment_avg"]))
        st.warning("每次入帳不到 2,000 元：" + "、".join(x["symbol"] for x in _small) + f"。匯費 10 元在 {_worst['symbol']} 約佔每次入帳的 {_share * 100:.1f}%，"
                   "積少成多，可考慮改季配或加大單次部位。")
    # C1：組合內持股重疊（下限估計，同一份持股快照）
    if sb is not None and len(picks) >= 2:
        try:
            _cut = (today - dt.timedelta(days=45)).isoformat()
            _snaps = _snapshots(_load_holdings(sb, _cut))
            _syms = [x["symbol"] for x in picks]
            _warn = []
            for _i, _a in enumerate(_syms):
                for _b in _syms[_i + 1:]:
                    if _a in _snaps and _b in _snaps:
                        _ov = H.overlap(_snaps[_a][0][1], _snaps[_b][0][1])
                        if _ov["overlap_pct"] >= 50:
                            _warn.append(f"{_a}×{_b} {_ov['overlap_pct']:.0f}%")
            if _warn:
                st.warning("組合內有高度重疊（買兩檔近似買同一籃子，分散效果有限）：" + "；".join(_warn) + "。重疊為下限估計（只計揭露的持股）。")
        except Exception as _e:  # noqa: BLE001
            st.caption(f"（重疊檢查暫時無法計算：{type(_e).__name__}）")

    # ---- 口徑對照：社群貼文的「月領」常是樂觀口徑
    b = r["basis_monthly"]
    base = b["trailing"]
    if base > 0:
        st.markdown("**同一組合，不同『年領』口徑差多少**（貼文常用最近一次或最高一次年化；本頁一律用近12個月實際）")
        st.dataframe(pd.DataFrame([
            {"口徑": "近 12 個月實際配息（本頁採用、保守）", "平均每月（稅前毛額）": round(base), "相對保守口徑": "—"},
            {"口徑": "最近一次配息 × 年配次數", "平均每月（稅前毛額）": round(b["latest"]), "相對保守口徑": f"{(b['latest'] / base - 1) * 100:+.0f}%"},
            {"口徑": "單次最高 × 年配次數（最樂觀）", "平均每月（稅前毛額）": round(b["peak"]), "相對保守口徑": f"{(b['peak'] / base - 1) * 100:+.0f}%"}]),
            width="stretch", hide_index=True)

    # ---- 稅與費用
    t = r["income_tax"]
    with st.expander("🧾 稅與費用明細（二代健保補充保費／綜所稅）", expanded=True):
        x = st.columns(4)
        x[0].metric("補充保費(全年)", _fmt_money(r["annual_nhi"]))
        x[1].metric("綜所稅增減(較省方式)", _fmt_money(t["best_tax"]), help="負數＝可退稅")
        x[2].metric("匯費(全年)", _fmt_money(r["annual_fee"]))
        x[3].metric("應稅股利(全年)", _fmt_money(r["taxable_dividend"]), help="＝配息毛額 × 採用的 54C 占比")
        st.caption("較省的計稅方式：" + ("合併計稅（抵減 8.5%）" if t["best"] == "combined" else "分開計稅 28%"))
        st.dataframe(pd.DataFrame([
            {"計稅方式": "合併計稅：股利×邊際稅率 − 8.5%抵減(上限8萬)", "全年稅額": round(t["combined"]), "抵減額": round(t["credit"])},
            {"計稅方式": "分開計稅：股利 × 28%", "全年稅額": round(t["separate"]), "抵減額": 0}]), width="stretch", hide_index=True)
        if t["combined"] < 0:
            st.info("合併計稅為負數＝8.5% 抵減額大於應納稅額，隔年申報後可退稅（所得稅率低的人反而「賺」到）。")
        if r["nhi_hits"]:
            st.warning("這些標的單次配息的 54C 基數 ≥ 2 萬，每次都會被扣 2.11% 補充保費：" + "、".join(r["nhi_hits"]) +
                       "。補充保費是『每檔每次』分開判斷，把同樣的錢分散到更多檔、讓每次都 < 2 萬就不用繳。")
        if r["nhi_near"]:
            st.info("這些標的單次 54C 基數已達門檻的 75% 以上（再多買一點就會被扣）：" + "、".join(r["nhi_near"]))
        st.caption(
            f"• **補充保費 2.11%**：只算配息中的『股利(54C)』那一塊，單次 ≥ 2 萬就扣整筆（不是只扣超過的部分）。目前 54C 占比預設 {default_ratio:.0%}（保守＝全算）；"
            "到上方『稅費設定』可逐檔改成投信公告的實際占比（例如該次配息主要來自資本利得，占比就低、可能完全不用扣），占比每次配息都可能不同。\n"
            "• **綜所稅**：配息中屬股利的部分併入所得（合併計稅或分開 28% 擇優）；證券交易所得部分停徵、收益平準金不計。賣出 ETF 另有證交稅 0.1%、手續費 0.1425%。\n"
            "• 這裡只算『多了這筆股利，稅增減多少』，沒計免稅額/扣除額、加入股利後跳級距、利息所得與海外所得（基本所得額）；含海外標的的 ETF 配息組成不同，僅供參考。"
            "實際以國稅局試算/申報為準，非稅務建議。")
    warns = []
    young = [x["symbol"] for x in r["rows"] if x["young"]]
    if young:
        warns.append("上市（或首次配息）未滿 1 年、近 12 月配息不足整年，年領可能被低估也不代表常態：" + "、".join(young))
    hi = [x["symbol"] for x in r["rows"] if x["yield_pct"] >= 12]
    if hi:
        warns.append("殖利率 ≥ 12%（" + "、".join(hi) + "）常有部分配息來自資本利得，要看含息總報酬與配息組成，不要只看殖利率。")
    low = [x["symbol"] for x in r["rows"] if x["n_events"] < 3 and x["freq"] not in ("semiannual", "annual")]
    if low:
        warns.append("近 12 月配息次數不足 3 次、推算誤差大：" + "、".join(low))
    fgn = [x["symbol"] for x in r["rows"] if x["foreign"]]
    if fgn:
        warns.append("含海外標的（" + "、".join(fgn) + "）：配息組成與稅制和純台股 ETF 不同，本頁稅額只供參考。")
    for w in warns:
        st.warning(w)

    with st.expander("📊 高股息 vs 市值型：事實比較（各類型中位數，不是預測）", expanded=False):
        ks = E.kind_summary(cands_all)
        if ks:
            st.dataframe(pd.DataFrame([{"類型": v["label"], "檔數": v["n"], "殖利率%": v["yield_med"], "近1年含息總報酬%": v["total_med"],
                                        "最大回撤%": v["mdd_med"], "年化波動%": v["vol_med"]} for v in ks.values()]),
                         width="stretch", hide_index=True)
        st.caption("• 高股息型：現金流多、適合『要領錢』；配息中屬股利的部分要併入所得稅、單次大額還有補充保費，價差常較小或為負。\n"
                   "• 市值型/寬基：殖利率低，但報酬主要來自價差——ETF 價差（證券交易所得）目前停徵，賣出只有 0.1% 證交稅，稅負反而較輕。\n"
                   "• 兩者可以混搭（核心＝市值型、衛星＝高股息）；比較時請看『含息總報酬』與『最大回撤』，不要只看殖利率。")



def _render_dca(master, events, today, apply_nhi, apply_fee):
    """【P4｜C2】定期定額達標試算：價格不變、只計配息；三種殖利率口徑並列，回傳『區間』而非單一數字。"""
    st.subheader("🎯 定期定額達標試算")
    st.caption("假設：**價格不變、只計配息**（不預測漲跌）；殖利率固定，依三種口徑各算一次，呈現區間。"
               "扣二代健保與匯費，未扣綜所稅；配息是過去事實，不代表未來。槓桿／反向商品不納入。")
    cands = [c for c in E.candidate_table(master, events, today) if not E.is_leveraged(c["symbol"], c["name"]) and c.get("price")]
    if not cands:
        st.info("尚無可用的 ETF 資料。")
        return
    cands.sort(key=lambda c: (-(c.get("yield_pct") or 0), c["symbol"]))
    cmap = {_cand_label(c): c for c in cands}
    d1, d2, d3 = st.columns([3, 1, 1])
    pick = d1.selectbox("試算的 ETF（單檔）", list(cmap), key="etfd_pick")
    reinvest = d2.checkbox("配息再投入", value=True, key="etfd_reinv")
    horizon = d3.select_slider("期限（年）", options=[5, 10, 20, 30], value=30, key="etfd_hz")
    m1, m2, m3 = st.columns(3)
    start_cap = m1.number_input("起始本金(元)", 0, 500_000_000, 0, 10_000, key="etfd_start")
    monthly = m2.number_input("每月定額投入(元)", 0, 10_000_000, 10_000, 1_000, key="etfd_monthly")
    tkind = m3.radio("目標", ["月領（稅前實領）", "本金"], horizontal=True, key="etfd_kind")
    if tkind.startswith("月領"):
        tval = st.number_input("目標每月實領(元)", 1_000, 2_000_000, 20_000, 1_000, key="etfd_tv_income")
        tk = "income"
    else:
        tval = st.number_input("目標本金(元)", 100_000, 5_000_000_000, 5_000_000, 100_000, key="etfd_tv_capital")
        tk = "capital"
    if st.button("🧮 試算", type="primary", key="etfd_btn"):
        c = cmap[pick]
        rt = D.range_table(c, tk, float(tval), float(start_cap), float(monthly), reinvest=reinvest,
                           horizon_years=int(horizon), apply_nhi=apply_nhi, apply_fee=apply_fee)
        st.dataframe(pd.DataFrame([{**x, "達標年數": D.fmt_years(x["達標年數"])} for x in rt]), width="stretch", hide_index=True)
        ys = [x["達標年數"] for x in rt if x["達標年數"] is not None]
        if ys:
            st.success(f"區間：約 {D.fmt_years(min(ys))} ～ {D.fmt_years(max(ys))}（保守口徑較慢）。以上為假設下的推算，不是承諾。")
        else:
            st.warning(f"三種口徑在 {horizon} 年內都沒有達到目標。可提高每月投入、降低目標，或拉長期限。")
    else:
        st.caption("設定好後按『🧮 試算』。")


def _price_state(sb):
    """各檔最後同步日（etf_price_sync 寫入的 system_config 狀態；手動匯入也會更新）。"""
    st_ = _cfg_read(sb, "etf_price_sync_state_v1", {})
    return {k: str(v) for k, v in st_.items() if v}


def _load_prices(sb, sym):
    """從 etf_price_daily 讀單檔日收盤（分頁），回傳 [(date, close)]。"""
    out, off = [], 0
    while True:
        r = (sb.table("etf_price_daily").select("trade_date,close").eq("symbol", sym)
             .order("trade_date").range(off, off + 999).execute())
        rows = r.data or []
        out += [(str(x["trade_date"])[:10], float(x["close"])) for x in rows]
        if len(rows) < 1000:
            break
        off += 1000
    return out


def _load_divs(sb, sym):
    """從 etf_dividend_result 讀單檔現金配息（FinMind 除權息結果，未還原），回傳 [(ex_date, cash)]。"""
    try:
        r = (sb.table("etf_dividend_result").select("ex_date,cash").eq("symbol", sym)
             .order("ex_date").range(0, 999).execute())
        return [(str(x["ex_date"])[:10], float(x["cash"])) for x in (r.data or [])]
    except Exception:
        return []


def _load_splits(sb, sym):
    """官方分割／反分割（FinMind TaiwanStockSplitPrice，排程存在 system_config），回傳 [(date, factor)]。"""
    corp = _cfg_read(sb, "etf_corp_actions_v1", {})
    return [(x["date"], float(x["factor"])) for x in (corp.get("splits") or []) if x.get("symbol") == sym and x.get("factor")]


def _render_portfolio(master, events, sb):
    """【P5】股債配置試算。日收盤由 etf_price_sync 每日自動同步（FinMind TaiwanStockPrice，未還原）；
    另保留『手動貼 CSV』作為補充，同樣經過驗證。"""
    st.subheader("📈 股債配置試算（P5）")
    st.caption("資料來源：FinMind（每日收盤後自動同步）。日收盤與配息皆為未還原、同口徑：配息取自除權息結果，分割依官方分割紀錄調整。"
               "回測假設：每月底再平衡、含除息配息、未扣手續費與稅、歷史一段期間，通過不代表未來。")
    if sb is None:
        st.error("Supabase 未連線，無法讀取價格資料。")
        return
    _last = {m["symbol"]: m.get("last_price") for m in master if m.get("last_price") not in (None, "")}
    state = _price_state(sb)
    with st.expander("⬆️ 手動補充價格（貼上 CSV，會經過同樣的驗證）", expanded=False):
        st.caption("格式：symbol,date,close（或 FinMind 原生欄位 stock_id,date,…,close）。來源網址必填（https）。")
        p_url = st.text_input("資料來源網址（https）", key="etf_pf_url")
        p_csv = st.text_area("貼上 CSV", height=160, key="etf_pf_csv")
        if st.button("驗證並寫入", key="etf_pf_save"):
            _syms = [m["symbol"] for m in master]
            _ok, _res = PF.parse_price_csv(p_csv, _syms, p_url)
            if not _ok:
                st.error("退回：" + "；".join(_res))
            else:
                _okc, _probs = PF.cross_check(_res, _last)
                if not _okc:
                    st.error("退回（與現價不符）：" + "；".join(_probs))
                else:
                    try:
                        import etf_price_sync as PS
                        for _sym, _rows in _res.items():
                            PS.upsert_manual(sb, _sym, _rows, p_url)
                            _mx = max(d for d, _ in _rows)
                            if _mx > state.get(_sym, ""):
                                state[_sym] = _mx
                        _cfg_save(sb, "etf_price_sync_state_v1", state)
                        st.success(f"已寫入 {len(_res)} 檔（{sum(len(v) for v in _res.values())} 筆）。")
                        st.rerun()
                    except Exception as e:
                        st.error(f"寫入失敗：{type(e).__name__}: {e}")
    if not state:
        st.info("尚未有價格資料。等待排程『etf_price_sync』執行（或手動執行該 stage），之後這裡會列出可選的 ETF。")
        return
    names = {m["symbol"]: (m.get("name") or "") for m in master}
    st.dataframe(pd.DataFrame([{"代號": s_, "名稱": names.get(s_, ""), "最後同步日": d_}
                               for s_, d_ in sorted(state.items())]), width="stretch", hide_index=True)
    _opts = {f"{s_} {names.get(s_, '')}".strip(): s_ for s_ in sorted(state)}
    if len(_opts) < 2:
        st.info("需要至少兩檔有資料的 ETF 才能試算。")
        return
    c1, c2, c3 = st.columns([2, 2, 2])
    stock_lbl = c1.selectbox("股票型 ETF", list(_opts), key="etf_pf_stock")
    bond_lbl = c2.selectbox("債券型 ETF", [o for o in _opts if o != stock_lbl], key="etf_pf_bond")
    sw = c3.slider("股票比例（%）", 0, 100, 60, 5, key="etf_pf_w")
    if st.button("🧮 試算股債配置", type="primary", key="etf_pf_btn"):
        _info = {}
        def _monthly(lbl):
            sym = _opts[lbl]
            rows, divs, sps = _load_prices(sb, sym), _load_divs(sb, sym), _load_splits(sb, sym)
            _info[lbl] = {"divs": len(divs), "first_div": divs[0][0] if divs else "", "splits": len(sps),
                          "breaks": PF.count_breaks(rows, splits=sps)}
            return PF.monthly_returns(PF.total_return_daily(rows, divs, splits=sps)) if len(rows) >= 2 else {}
        _sm, _bm = _monthly(stock_lbl), _monthly(bond_lbl)
        _r = PF.mix_backtest(_sm, _bm, sw / 100.0)
        if _r["period"] is None:
            st.warning("兩檔沒有重疊的月份，無法試算。")
        else:
            _rows = []
            for lab, key in (("股債組合", "mix"), (f"純股票 {stock_lbl}", "stock_only"), (f"純債券 {bond_lbl}", "bond_only")):
                m_ = _r[key]
                _rows.append({"組合": lab, "月數": m_["months"], "年化報酬%": m_["cagr_pct"], "年化波動%": m_["vol_pct"],
                              "最大回撤%": m_["mdd_pct"], "最差月%": m_["worst_month_pct"], "正報酬月占比%": m_["positive_months_pct"]})
            st.dataframe(pd.DataFrame(_rows), width="stretch", hide_index=True)
            for _lbl in (stock_lbl, bond_lbl):
                _i = _info[_lbl]
                st.caption(f"{_lbl}：配息 {_i['divs']} 筆" + (f"（最早 {_i['first_div']}）" if _i["divs"] else "（尚無配息資料：本來不配息，或尚未同步）")
                           + f"；官方分割 {_i['splits']} 次已調整；未解釋跳動 {_i['breaks']} 日（報酬計為 0）。")
            st.line_chart(pd.DataFrame({"股債組合（累積）": [e for _, e in _r["series"]]}, index=[m for m, _ in _r["series"]]))
            st.caption(f"期間 {_r['period']['start']} ～ {_r['period']['end']}（{_r['mix']['months']} 個月，每月再平衡）。"
                       "最大回撤以月底權益曲線計算，會低估日內回撤。" + ("" if _r["mix"]["sample_ok"] else f"樣本不足 {PF.MIN_MONTHS} 個月，結果僅供參考。"))
    else:
        st.caption("設定好後按『🧮 試算股債配置』。")


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_prices(_sb, sym):
    """【第6項】同一檔價格 1 小時內只讀一次，避免切換權重時重複打資料庫。"""
    return _load_prices(_sb, sym)


@st.cache_data(ttl=900, show_spinner=False)
def _cached_qc_raw(_sb):
    """【2026-10-13】價格跳動查證所需資料（大幅跳動清單、官方分割、除息、人工查證結果），15 分鐘讀一次。
    讀取失敗回傳 None，畫面改為保守處理（不開放組合回測），不假裝沒事。"""
    try:
        jr = QC.load_jump_rows(_sb)
        return {"jumps": jr, "splits": QC.load_splits(_sb), "divs": QC.load_divs(_sb, [r["symbol"] for r in jr]),
                "qc": QC.load_qc_rows(_sb), "foreign": QC.load_foreign_symbols(_sb)}
    except Exception:  # noqa: BLE001
        return None


def _wide_symbols(master, name_map):
    """無單日漲跌幅限制的商品：槓桿／反向型，或名稱帶國外關鍵字；國外成分另由持股資料交叉判斷（_raw['foreign']）。"""
    out = set()
    for m in master:
        sym = m.get("symbol")
        nm = name_map.get(sym, "") or m.get("name") or ""
        if sym and (E.etf_kind(sym, nm) == "lev" or E.is_foreign(nm)):
            out.add(sym)
    return out


def _render_combo_backtest(sb, master, name_map):
    """【2026-10-13 第6項】ETF 組合歷史回測頁。
    邊界：價格為 etf_price_daily 日收盤（未還原除權息）→ 只反映價差，不含息，會低估含息總報酬。
    只回看歷史（年化報酬、年化波動、最大回撤、年度再平衡），不做未來資產預估；不是投資建議。"""
    st.markdown("### 🧪 ETF 組合歷史回測（過去約 5 年）")
    st.caption("價格來自 etf_price_daily 的日收盤，**未還原除權息**，所以這裡的報酬只反映價差、**不含息**，"
               "會低估含息的總報酬。只做歷史回看，不預測未來，不是投資建議。最多 5 檔。")
    if sb is None:
        st.info("資料庫未連線，無法回測。")
        return
    options = sorted({m["symbol"] for m in master if m.get("symbol")})
    if not options:
        st.info("ETF 價格資料還沒同步，無法回測。")
        return
    # 【2026-10-13 老闆指示】價格有大幅跳動的 ETF：先查證、再標記。沒查到依據的只標「資料待確認」並暫不開放選用，
    # 不直接說資料異常；查證完成（登錄到 etf_price_qc）後自動開放。
    _raw = _cached_qc_raw(sb)
    if _raw is None:
        st.warning("價格跳動的查證資料暫時讀不到，為避免把未確認的價格算進報酬，組合回測先不開放；稍後重新整理再試。")
        return
    qc_ctx = QC.build_ctx(_raw["jumps"], _raw["splits"], _raw["divs"], _raw["qc"],
                          _wide_symbols(master, name_map) | set(_raw.get("foreign") or ()))
    _pend = QC.pending_by_symbol(_raw["jumps"], qc_ctx)
    if _pend:
        _held = [s_ for s_ in options if s_ in _pend]
        options = [s_ for s_ in options if s_ not in _pend]
        with st.expander(f"🔎 {len(_held)} 檔 ETF 資料待確認（暫不開放選用）", expanded=False):
            st.caption("這不是判定資料有錯：這些 ETF 的價格有單日大幅變動，目前還沒查到公告、除息或同日市場行情可以說明，"
                       "所以先不算進報酬。查證完成並登錄後會自動開放。")
            _tbl = [{"代號": s_, "名稱": name_map.get(s_, ""), "日期": i["date"], "單日變動%": i["pct"], "還缺的依據": i["reason"]}
                    for s_ in _held for i in _pend[s_]]
            st.dataframe(pd.DataFrame(_tbl), width="stretch", hide_index=True)
    if not options:
        st.info("目前沒有可選的 ETF（全部都在查證中）。")
        return
    picked = st.multiselect("選 1～5 檔 ETF", options, max_selections=5,
                            format_func=lambda s: f"{s} {name_map.get(s, '')}".strip(), key="etf_bt_codes")
    if not picked:
        st.info("請先選 1～5 檔 ETF。")
        return
    n = len(picked)
    base = round(100 / n, 2)
    weights = {}
    for i, sym in enumerate(picked):
        default = base if i < n - 1 else round(100 - base * (n - 1), 2)
        weights[sym] = st.number_input(f"{sym} 權重 %", min_value=0.0, max_value=100.0, value=float(default),
                                       step=1.0, key=f"etf_bt_w_{sym}")
    total = sum(weights.values())
    if abs(total - 100.0) > 0.01:
        st.warning(f"權重加總 {total:.2f}%，需為 100% 才會計算。")
    rb = st.radio("再平衡", ["每年再平衡（回到目標權重）", "買入後不調整"], horizontal=True, key="etf_bt_rb")
    rebalance = "annual" if rb.startswith("每年") else "none"

    series = {}
    with st.spinner("讀取價格資料…"):
        for sym in picked:
            series[sym] = _cached_prices(sb, sym)
    if any(not series[s] for s in picked):
        missing = [s for s in picked if not series[s]]
        st.error(f"這幾檔沒有價格資料：{'、'.join(missing)}")
        return

    r = CB.backtest(series, weights, rebalance=rebalance, qc_ctx=qc_ctx)
    if not r["ok"]:
        (st.warning if r.get("pending") else st.error)(r["message"])
        return
    st.caption(f"回測區間 {r['from']} ～ {r['to']}（約 {r['years']} 年，共同交易日 {r['n_days']} 天）｜{r['note']}")
    if r.get("adjustments"):
        _split_txt = "；".join(f"{s} {e['date']} 分割／反分割還原 ÷{e['factor']:g}（{e['reason']}）"
                              for s, evs in r["adjustments"].items() for e in evs if e.get("factor"))
        _other = [(s, e) for s, evs in r["adjustments"].items() for e in evs if not e.get("factor")]
        if _split_txt:
            st.caption(f"已依確認過的分割紀錄還原價格（價格資料本身未還原）：{_split_txt}")
        if _other:
            st.caption(f"另有 {len(_other)} 次單日大幅變動已查有依據（除息、市場同步或真實行情），不調整：" +
                       "；".join(f"{s} {e['date']} {e['pct']:+.1f}%（{e['reason']}）" for s, e in _other[:6]) +
                       ("…" if len(_other) > 6 else ""))
    c1, c2, c3 = st.columns(3)
    c1.metric("年化報酬（價格、未含息）", f"{r['cagr_pct']:+.2f}%" if r["cagr_pct"] is not None else "—")
    c2.metric("年化波動", f"{r['vol_pct']:.2f}%")
    c3.metric("最大回撤", f"-{r['mdd_pct']:.2f}%")
    try:
        _curve = pd.Series({d: v for d, v in r["curve"]}, name="組合淨值（起始=1）")
        _curve.index = pd.to_datetime(_curve.index)
        st.line_chart(_curve)
    except Exception:  # noqa: BLE001
        st.caption("（走勢圖暫時無法產生，數字仍有效）")

    with st.expander("各檔單獨回測（同一區間，對照用）", expanded=False):
        _rows = []
        for sym in picked:
            s1 = CB.backtest({sym: series[sym]}, {sym: 100}, rebalance="none", qc_ctx=qc_ctx)
            if s1["ok"]:
                _rows.append({"代號": sym, "名稱": name_map.get(sym, ""), "年化報酬%(未含息)": s1["cagr_pct"],
                              "年化波動%": s1["vol_pct"], "最大回撤%": s1["mdd_pct"]})
            else:
                _rows.append({"代號": sym, "名稱": name_map.get(sym, ""), "年化報酬%(未含息)": None,
                              "年化波動%": None, "最大回撤%": None})
        st.dataframe(pd.DataFrame(_rows), width="stretch", hide_index=True)
    st.caption("不做未來資產預估；歷史數字不代表未來績效。配息請以「領息規劃器」的實際配息紀錄為準。")


def render_etf_tab(sb):
    st.title("💰 ETF 月配／季配 領息規劃與損益")
    if sb is None:
        st.error("Supabase 未連線，ETF 分頁需要資料庫。")
        return
    today = _today()
    try:
        master, events = _load_market(sb)
        trades = _load_trades(sb)
        # 【2026-10-10 A1】已公告、尚未除息的配息併入事件清單（人工輸入，來源標 manual_announced）
        events = E.merge_announced(events, _cfg_read(sb, "etf_manual_announced_v1", []))
        # 【2026-10-10 A2】54C 占比以『逐次配息台帳』為準（排程沿用＋人工確認），取代單一數字
        _ledger = _cfg_read(sb, "etf_composition_v1", {})
        _nhi_mode = str(_cfg_read(sb, "etf_nhi_mode_v1", "combined"))
        if _nhi_mode not in E.NHI_MODES:
            _nhi_mode = "combined"
        # 【2026-10-12 P2｜老闆指示：逐檔確認後再開】債券ETF 健保口徑：只有逐檔確認清單內（附 https 來源）的債券 ETF，
        # 且台帳與人工覆寫都沒有占比時，才把健保基數設為 0；其餘沿用預設占比（保守）。
        _bond_ok = _cfg_read(sb, E.BOND_CONFIRM_KEY, {})
        for _m in master:
            _cur = E.nhi_base_ratio(_ledger, _m["symbol"], _nhi_mode)
            _manual = _m.get("div_income_ratio")
            _bond_applied = False
            if _cur is not None:
                _m["div_income_ratio"] = _cur
            elif _manual in (None, ""):
                _ov = E.bond_override_for(_m["symbol"], E.etf_kind(_m["symbol"], _m.get("name") or ""), _bond_ok)
                if _ov is not None:
                    _m["div_income_ratio"] = _ov
                    _bond_applied = True
            # 【P1】占比來源標示（規劃結果表與 ETF 一覽都會顯示）
            _m["_ratio_src"] = E.ratio_provenance(_ledger, _m["symbol"], master_val=_manual, bond_rule=_bond_applied)["label"]
    except Exception as e:
        st.error(f"讀取 ETF 資料表失敗：{type(e).__name__}: {e}")
        return
    if not master:
        st.warning("ETF 資料還沒同步。到 GitHub Actions 手動執行 `system_scheduler`（stage 選 `etf_dividend_sync`），"
                   "之後每個交易日 19:40 會自動更新。")
    price_map = {m["symbol"]: m.get("last_price") for m in master}
    name_map = {m["symbol"]: (m.get("name") or "") for m in master}
    last_upd = max((str(m.get("price_date") or "") for m in master), default="")
    st.caption(f"資料更新：現價 {last_upd or '—'}｜ETF {len(master)} 檔、配息事件 {len(events)} 筆。"
               "配息為「過去實際配息」推算，**不代表未來會配相同金額**；發放日若資料源沒提供，以該檔歷史「除息→發放」天數中位數估算（標示「估」）。")

    # 【P3｜2026-10-12】貼上公告：老闆手動複製，系統解析＋核對事件表後才寫入（不自動抓取網頁）
    with st.expander("📋 貼上投信公告（手動複製→解析→核對事件表→確認寫入）", expanded=False):
        st.caption("到證交所或投信頁面，『手動』複製該檔收益分配公告的文字貼上。系統會解析占比與除息日，並核對事件表的除息日與配發金額。"
                   "來源網址必填（https）。這裡不會自動抓取網頁。")
        pc1, pc2 = st.columns([1, 3])
        p_sym = pc1.text_input("ETF 代號", key="etf_paste_sym", placeholder="例如 00940")
        p_url = pc2.text_input("來源網址（https）", key="etf_paste_url", placeholder="公告頁面的網址")
        p_txt = st.text_area("貼上公告文字", height=160, key="etf_paste_txt")
        if st.button("解析與核對", key="etf_paste_check"):
            _res = P.check_paste(p_txt, p_sym, p_url, events)
            _res["_sym"], _res["_url"] = str(p_sym or "").strip().upper(), str(p_url or "").strip()
            st.session_state["etf_paste_res"] = _res
        _pr = st.session_state.get("etf_paste_res")
        if _pr:
            if not _pr["ok"]:
                st.error("退回：" + _pr["reason"])
            else:
                _p = _pr["parsed"]
                _c = _p["composition"]
                st.success(f"核對通過：{_pr['row']['symbol']} 除息日 {_pr['row']['ex_date']}｜公告金額 {_p['amount']} 元＝事件表 {_pr['cash']} 元")
                st.caption(f"占比（投信預估）：股利(54C) {_c.get('div', 0) * 100:.2f}%｜利息(5A) {_c.get('int', 0) * 100:.2f}%｜"
                           f"資本利得 {_c.get('gain', 0) * 100:.2f}%｜平準金 {_c.get('eq', 0) * 100:.2f}%｜權利金 {_c.get('prem', 0) * 100:.2f}%｜其他 {_c.get('oth', 0) * 100:.2f}%")
                _led_now = _cfg_read(sb, "etf_composition_v1", {})
                _old = next((r for r in _led_now.get(_pr["row"]["symbol"], []) if r.get("ex_date") == _pr["row"]["ex_date"]), None)
                if _old and _old.get("ratio_54c") is not None:
                    st.warning(f"此除息日已有占比 {float(_old['ratio_54c']) * 100:.2f}%（來源：{_old.get('source', '')}）；確認寫入會以本次公告覆寫。")
                if st.button("✅ 確認寫入台帳", key="etf_paste_write", type="primary"):
                    _all_ex = {}
                    for _e in events or []:
                        _all_ex.setdefault(str(_e.get("symbol") or "").strip().upper(), set()).add(str(_e.get("ex_date") or "")[:10])
                    _new, _applied, _rej = E.apply_sourced(_led_now, [_pr["row"]], E.latest_ex_by_symbol(events), min_sources=1, all_ex=_all_ex)
                    if _applied:
                        try:
                            _cfg_save(sb, "etf_composition_v1", _new)
                            st.session_state.pop("etf_paste_res", None)
                            _load_market.clear()
                            st.rerun()
                        except Exception as e:
                            st.error(f"寫入失敗：{type(e).__name__}: {e}")
                    else:
                        st.error("台帳驗證未通過：" + "；".join(r for _, r in _rej))
    with st.expander("⚙️ 稅費設定", expanded=False):
        c1, c2 = st.columns(2)
        apply_nhi = c1.checkbox("扣二代健保補充保費 2.11%（單次給付 ≥ 2 萬才扣）", value=True, key="etf_nhi")
        apply_fee = c2.checkbox("扣配息匯費 10 元／筆", value=True, key="etf_fee")
        # 【P2 逐檔確認】取代原本的全域勾選：每檔附來源才生效
        st.markdown("**債券 ETF 健保口徑：逐檔確認為海外利息（不計二代健保）**")
        st.caption("依據：富蘭克林華美投信稱債券型 ETF 配息多數來自海外、不列入補充保費（uanalyze 2025-11-06 轉載），但未逐檔列出。"
                   "因此只對你逐檔確認、附來源（例如投信公開說明書或基金資訊頁）的債券 ETF 生效；未確認者沿用預設占比（保守計入）。"
                   "有台帳占比或人工覆寫者不受影響。")
        _bond_list = sorted(m["symbol"] for m in master if E.etf_kind(m["symbol"], m.get("name") or "") == "bond")
        with st.form("etf_bond_confirm_form", clear_on_submit=True):
            bc = st.columns([2, 3, 2])
            b_sym = bc[0].selectbox("債券 ETF", [s_ for s_ in _bond_list if s_ not in _bond_ok] or ["（皆已確認）"])
            b_url = bc[1].text_input("來源網址（https）", placeholder="投信公開說明書／基金資訊頁")
            b_note = bc[2].text_input("備註", placeholder="例：投資美國公債")
            if st.form_submit_button("確認此檔為海外利息"):
                _err = E.validate_bond_confirm(b_sym, E.etf_kind(b_sym, name_map.get(b_sym, "")), b_url, [m["symbol"] for m in master])
                if _err:
                    st.error(_err)
                else:
                    _bond_ok[b_sym] = {"source": b_url.strip(), "note": (b_note or "").strip(), "confirmed_at": str(today)}
                    try:
                        _cfg_save(sb, E.BOND_CONFIRM_KEY, _bond_ok)
                        _load_market.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"儲存失敗：{type(e).__name__}: {e}")
        if _bond_ok:
            st.dataframe(pd.DataFrame([{"代號": k, "名稱": name_map.get(k, ""), "來源": v.get("source", ""), "備註": v.get("note", ""),
                                        "確認日": v.get("confirmed_at", "")} for k, v in sorted(_bond_ok.items())]),
                         width="stretch", hide_index=True)
            _rm = st.multiselect("取消確認", sorted(_bond_ok), key="etf_bond_rm")
            if _rm and st.button("取消所選", key="etf_bond_rm_btn"):
                for k in _rm:
                    _bond_ok.pop(k, None)
                _cfg_save(sb, E.BOND_CONFIRM_KEY, _bond_ok)
                _load_market.clear()
                st.rerun()
        default_pct = st.slider("預設「股利所得(54C)占比」%", 0, 100, 100, 5, key="etf_default_ratio",
                                help="二代健保只對配息中的『股利或盈餘所得(54C)』計費；財產交易所得(資本利得)與收益平準金不計。"
                                     "每次配息組成都可能不同，查不到時預設 100%＝保守地全部計費。可在下方逐檔覆寫為投信公告的實際占比。")
        st.caption("本分頁的『實領』只扣補充保費與匯費，未扣綜合所得稅（綜所稅請到「🎯 領息規劃器」依你的稅率試算：合併計稅抵減 8.5% 或分開 28%）。證交稅 ETF 賣出 0.1%、手續費 0.1425% 已計入損益。"
                   "例（今周刊 2026-09-29）：00919 近期 54C=0%（不扣）、00878=9.9%、0056=34.96%，且占比每次配息都可能變。")
        with st.form("etf_ratio_form", clear_on_submit=True):
            rc = st.columns([2, 2, 1])
            r_sym = rc[0].text_input("逐檔覆寫：ETF代號", placeholder="例如 00919")
            r_pct = rc[1].number_input("54C 占比 %（投信「收益分配組成」公告）", 0.0, 100.0, 0.0, 0.01)
            r_clear = rc[2].checkbox("清除覆寫")
            r_ok = st.form_submit_button("儲存占比")
        if r_ok:
            r_sym = r_sym.strip().upper()
            if r_sym not in price_map:
                st.error(f"{r_sym or '(空白)'} 不在 ETF 清單內。")
            else:
                try:
                    sb.table("etf_master").update({"div_income_ratio": None if r_clear else round(r_pct / 100, 4)}).eq("symbol", r_sym).execute()
                    # 【2026-10-10】同時確認台帳中該檔最近一次除息的占比（清除＝該次留空，改用上一次）
                    _ex = E.latest_ex_by_symbol(events).get(r_sym)
                    if _ex:
                        _led2 = _cfg_read(sb, "etf_composition_v1", {})
                        if r_clear:   # 清除＝該次占比留空，規劃器改用上一次有數字的占比
                            for _rr in _led2.get(r_sym, []):
                                if _rr.get("ex_date") == _ex["ex_date"]:
                                    _rr.update({"ratio_54c": None, "status": "confirmed", "source": "清除(留空)"})
                        else:
                            _led2 = E.confirm_record(_led2, r_sym, _ex["ex_date"], r_pct / 100, source="人工確認(ETF分頁)")
                        _cfg_save(sb, "etf_composition_v1", _led2)
                    _load_market.clear()
                    st.success("已儲存。")
                    st.rerun()
                except Exception as e:
                    st.error(f"儲存失敗：{type(e).__name__}: {e}")
        _saved = [(m["symbol"], m.get("name") or "", float(m["div_income_ratio"]) * 100) for m in master
                  if m.get("div_income_ratio") not in (None, "")]
        if _saved:
            st.dataframe(pd.DataFrame(_saved, columns=["代號", "名稱", "54C占比%"]), width="stretch", hide_index=True)
        st.caption("配息組成沒有免費的結構化資料源，需參考各投信「收益分配」公告（或財經新聞整理）手動填入；沒填的檔案一律用上面的預設占比。")
        _led = _cfg_read(sb, "etf_composition_v1", {})
        _pend = [(sym, recs[-1]["ex_date"]) for sym, recs in _led.items() if recs and recs[-1].get("status") == "carried"]
        if _pend:
            st.warning("待確認的 54C 占比（排程偵測到新配息，先沿用上次數字）：" + "、".join(f"{a} 除息 {b}" for a, b in sorted(_pend))
                       + "。請依投信公告在下方覆寫，確認後轉為正式紀錄。")
        else:
            st.caption("逐次配息台帳：目前沒有待確認項目。")
        with st.form("etf_ann_form", clear_on_submit=True):
            ac = st.columns([2, 2, 2, 2])
            a_sym = ac[0].text_input("已公告配息：ETF 代號", placeholder="例如 0056", key="etf_ann_sym")
            a_ex = ac[1].text_input("除息日 YYYY-MM-DD", placeholder="2026-10-22", key="etf_ann_ex")
            a_cash = ac[2].number_input("每單位配息(元，預估亦可)", 0.0, 100.0, 0.0, 0.01, key="etf_ann_cash")
            a_pay = ac[3].text_input("發放日 YYYY-MM-DD（可空）", key="etf_ann_pay")
            a_ok = st.form_submit_button("加入已公告配息")
        if a_ok:
            a_sym = a_sym.strip().upper()
            try:
                dt.date.fromisoformat(a_ex.strip())
                if a_pay.strip():
                    dt.date.fromisoformat(a_pay.strip())
                if a_sym not in price_map:
                    st.error(f"{a_sym or '(空白)'} 不在 ETF 清單內。")
                elif a_cash <= 0:
                    st.error("每單位配息需大於 0。")
                else:
                    _ann = _cfg_read(sb, "etf_manual_announced_v1", [])
                    _ann.append({"symbol": a_sym, "ex_date": a_ex.strip(), "cash_per_unit": a_cash,
                                 "pay_date": a_pay.strip() or None})
                    _cfg_save(sb, "etf_manual_announced_v1", _ann)
                    st.success("已加入。下次重新整理即會納入規劃器與持倉的『預估入帳』。")
                    st.rerun()
            except ValueError:
                st.error("日期格式需為 YYYY-MM-DD。")
        _ann_now = _cfg_read(sb, "etf_manual_announced_v1", [])
        if _ann_now:
            st.dataframe(pd.DataFrame(_ann_now), width="stretch", hide_index=True)
            if st.button("清空已公告配息（手動清單）", key="etf_ann_clear"):
                _cfg_save(sb, "etf_manual_announced_v1", [])
                st.rerun()

    ratios = {m["symbol"]: float(m["div_income_ratio"]) for m in master if m.get("div_income_ratio") not in (None, "")}
    default_ratio = default_pct / 100.0
    # 2026-10-06 改版：6 個分頁精簡為 4 個（規劃器為首頁；持倉＋領息明細合併；一覽＋持股重疊合併）
    t_plan, t_pos, t_trade, t_more, t_bt = st.tabs(["🎯 領息規劃器", "📦 我的持倉與領息", "📒 買賣紀錄", "🔎 ETF 一覽與持股", "🧪 組合歷史回測"])
    t_cash, t_scan, t_hold = t_pos, t_more, t_more
    # 【2026-10-13 第6項】組合歷史回測：只讀 etf_price_daily 既有資料，不做未來預估
    with t_bt:
        _render_combo_backtest(sb, master, name_map)

    # ------------------------------------------------------------------ 我的持倉
    with t_pos:
        pos = E.position_summary(trades, price_map)
        held = {s: p for s, p in pos.items() if p["shares"] > 0}
        cf = E.dividend_cashflows(trades, events, today, apply_nhi, apply_fee, ratios, default_ratio)
        received = sum(r["net"] for r in cf if r["status"] == "received")
        if not trades:
            st.info("還沒有買賣紀錄。到「📒 買賣紀錄」輸入你買的 ETF，這裡就會算出持股、損益與每月/每季可領多少。")
        else:
            rows = []
            for s, p in sorted(pos.items()):
                if p["shares"] <= 0 and abs(p["realized"]) < 1e-9:
                    continue
                rows.append({"代號": s, "名稱": name_map.get(s, ""), "持股": _lots(p["shares"]),
                             "均價(含費)": round(p["avg_cost"], 3) if p["shares"] > 0 else None,
                             "現價": p["price"], "成本": round(p["cost_basis"]),
                             "市值": round(p["market_value"]) if p["market_value"] is not None else None,
                             "未實現損益": round(p["unrealized"]) if p["unrealized"] is not None else None,
                             "未實現%": round(p["unrealized_pct"], 2) if p["unrealized_pct"] is not None else None,
                             "已實現損益": round(p["realized"]),
                             "累計實領息": round(sum(r["net"] for r in cf if r["symbol"] == s and r["status"] == "received"))})
            df = pd.DataFrame(rows)
            tot_cost = sum(p["cost_basis"] for p in held.values())
            tot_mv = sum((p["market_value"] or 0) for p in held.values())
            unreal = sum((p["unrealized"] or 0) for p in held.values())
            realized = sum(p["realized"] for p in pos.values())
            total_ret = unreal + realized + received
            m = st.columns(5)
            m[0].metric("持有成本", _fmt_money(tot_cost))
            m[1].metric("目前市值", _fmt_money(tot_mv))
            m[2].metric("價差損益(未實現)", _fmt_money(unreal), f"{(unreal / tot_cost * 100):.2f}%" if tot_cost else None)
            m[3].metric("累計已入帳股息", _fmt_money(received))
            m[4].metric("含息總損益", _fmt_money(total_ret),
                        f"{(total_ret / tot_cost * 100):.2f}%" if tot_cost else None,
                        help="含息總損益 = 未實現價差 + 已實現價差 + 已入帳股息(扣健保/匯費後)")
            st.dataframe(df, width="stretch", hide_index=True)
            if any(p["oversold"] for p in pos.values()):
                st.warning("有標的賣出股數超過當時持股，已以持股為上限計算——請檢查買賣紀錄。")
            if any(p["price"] is None for p in held.values()):
                st.caption("⚠️ 部分持股查無現價（可能是新上市或同步尚未涵蓋），市值/損益僅計入有現價的標的。")

            st.subheader("目前持股「領息」彙總（以近 12 個月實際配息推估）")
            grp = E.holdings_by_frequency(trades, events, price_map, today)
            if not grp:
                st.caption("目前沒有持股。")
            else:
                gr = []
                for fq, g in grp.items():
                    gr.append({"類型": E.FREQ_LABEL.get(fq, fq), "檔數": g["count"], "市值": round(g["market_value"]),
                               "預估年領(稅前)": round(g["annual"]), "平均每月": round(g["monthly_avg"]),
                               "每次約領": round(g["per_payment"]), "年領/成本%": round(g["yield_pct_on_cost"], 2)
                               if g["yield_pct_on_cost"] is not None else None})
                st.dataframe(pd.DataFrame(gr), width="stretch", hide_index=True)
                monthly = sum(g["monthly_avg"] for fq, g in grp.items() if fq == "monthly")
                quarter = sum(g["per_payment"] for fq, g in grp.items() if fq == "quarterly")
                st.info(f"月配類：平均每月約領 **{monthly:,.0f}** 元；季配類：每季約領 **{quarter:,.0f}** 元"
                        f"（全部持股合計平均每月約 **{sum(g['monthly_avg'] for g in grp.values()):,.0f}** 元，稅前）。")

    # ------------------------------------------------------------------ 領息明細
    with t_cash:
        st.divider()
        st.header("💵 領息明細與未來預估")
        if not trades:
            st.info("先到「📒 買賣紀錄」輸入持股。")
        else:
            cf = E.dividend_cashflows(trades, events, today, apply_nhi, apply_fee, ratios, default_ratio)
            pj = E.project_income(trades, events, today, apply_nhi, apply_fee, months=12, ratios=ratios, default_ratio=default_ratio)
            st.subheader("未來 12 個月入帳預估（實領）")
            st.caption("「已公告」＝資料源已有除息日的實際配息；「推估」＝以各檔去年同期配息套用你目前股數，僅供參考。")
            if pj:
                b_ann = E.monthly_buckets([r for r in pj if r["kind"] == "announced"])
                b_prj = E.monthly_buckets([r for r in pj if r["kind"] == "projected"])
                months = sorted(set(b_ann) | set(b_prj))
                chart = pd.DataFrame({"已公告": [b_ann.get(m, 0) for m in months],
                                      "推估": [b_prj.get(m, 0) for m in months]}, index=months)
                st.bar_chart(chart)
                tot = sum(r["net"] for r in pj)
                st.metric("未來12個月預估實領合計", _fmt_money(tot), f"平均每月 {tot / 12:,.0f}")
            else:
                st.caption("沒有可預估的未來配息（可能持股的 ETF 尚無配息資料）。")
            st.subheader("配息入帳明細（含已領與待發）")
            if cf:
                lab = {"received": "✅ 已入帳", "pending": "⏳ 已除息待發放", "upcoming": "📅 已公告尚未除息"}
                df = pd.DataFrame([{"狀態": lab[r["status"]], "代號": r["symbol"], "名稱": name_map.get(r["symbol"], ""),
                                    "除息日": r["ex_date"], "發放日": f"{r['pay_date']}{'(估)' if r['pay_estimated'] else ''}",
                                    "當時持股": _lots(r["shares"]), "每單位": r["cash_per_unit"], "稅前": r["gross"],
                                    "健保": r["nhi"], "匯費": r["fee"], "實領": r["net"]} for r in reversed(cf)])
                st.dataframe(df, width="stretch", hide_index=True)
                st.caption("領息資格：除息日「前一個營業日收盤」仍持有才有；除息日當天或之後才買不算。")
            else:
                st.caption("持股期間內尚無配息事件。")

    # ------------------------------------------------------------------ 規劃器（見 _render_planner）
    with t_plan:
        _render_planner(master, events, today, apply_nhi, apply_fee, default_ratio, sb)
        st.divider()
        _render_dca(master, events, today, apply_nhi, apply_fee)
        st.divider()
        _render_portfolio(master, events, sb)

    # ------------------------------------------------------------------ 買賣紀錄
    with t_trade:
        st.subheader("新增交易")
        syms = sorted(price_map.keys())
        with st.form("etf_trade_form", clear_on_submit=True):
            c = st.columns(5)
            sym = c[0].text_input("ETF代號", placeholder="例如 00878")
            d = c[1].date_input("成交日", value=today, max_value=today)
            side = c[2].selectbox("買/賣", ["buy", "sell"], format_func=lambda x: "買進" if x == "buy" else "賣出")
            unit = c[3].selectbox("單位", ["張", "股"])
            qty = c[4].number_input("數量", min_value=0.0, value=1.0, step=1.0)
            c2 = st.columns(3)
            price = c2[0].number_input("成交價", min_value=0.0, value=0.0, step=0.01, format="%.2f")
            fee = c2[1].number_input("手續費(0=自動以0.1425%估)", min_value=0.0, value=0.0, step=1.0)
            note = c2[2].text_input("備註(選填)")
            ok = st.form_submit_button("➕ 新增")
        if ok:
            sym = sym.strip().upper()
            shares = qty * (E.LOT if unit == "張" else 1)
            if not sym or price <= 0 or shares <= 0:
                st.error("請填代號、成交價與數量。")
            elif syms and sym not in price_map:
                st.error(f"{sym} 不在 ETF 清單內（只收台股 ETF）。")
            else:
                try:
                    sb.table("etf_trades").insert({"symbol": sym, "trade_date": d.isoformat(), "side": side,
                                                   "shares": shares, "price": price, "fee": fee,
                                                   "note": note or None}).execute()
                    st.success("已新增。")
                    st.rerun()
                except Exception as e:
                    st.error(f"寫入失敗：{type(e).__name__}: {e}")
        st.subheader("紀錄")
        if trades:
            df = pd.DataFrame(trades)[["id", "trade_date", "symbol", "side", "shares", "price", "fee", "note"]]
            df["side"] = df["side"].map({"buy": "買進", "sell": "賣出"})
            st.dataframe(df.iloc[::-1], width="stretch", hide_index=True)
            did = st.selectbox("刪除一筆(輸入 id)", [None] + [t["id"] for t in reversed(trades)], key="etf_del_id")
            if did is not None and st.button("🗑️ 刪除這筆", key="etf_del_btn"):
                try:
                    sb.table("etf_trades").delete().eq("id", did).execute()
                    st.rerun()
                except Exception as e:
                    st.error(f"刪除失敗：{type(e).__name__}: {e}")
        else:
            st.caption("尚無紀錄。")

    # ------------------------------------------------------------------ ETF 一覽
    with t_scan:
        st.header("🔎 ETF 配息一覽")
        if st.toggle('▸ 載入「ETF 配息一覽」（打開才計算）', value=False, key='etf_lz_scan'):
            cands = E.candidate_table(master, events, today)
            if not cands:
                st.info("尚無資料。")
            else:
                f1, f2, f3 = st.columns(3)
                ftype = f1.multiselect("類型", ["monthly", "quarterly", "semiannual", "annual", "irregular"],
                                       default=["monthly", "quarterly"], format_func=lambda x: E.FREQ_LABEL[x], key="etf_scan_f")
                ymax = f2.slider("殖利率上限(%)", 5, 40, 20, key="etf_scan_y")
                q = f3.text_input("搜尋代號/名稱", key="etf_scan_q")
                rows = []
                for c in cands:
                    if c["freq"] not in ftype or c["yield_pct"] > ymax:
                        continue
                    if q and q.upper() not in c["symbol"].upper() and q not in c["name"]:
                        continue
                    rows.append({"代號": c["symbol"], "名稱": c["name"], "類型": E.FREQ_LABEL[c["freq"]], "現價": c["price"],
                                 "近12月每單位": round(c["annual"], 3), "殖利率%": round(c["yield_pct"], 2),
                                 "近1年價差%": round(c["ret_1y_price"], 1) if c["ret_1y_price"] is not None else None,
                                 "近1年含息總報酬%": round(c["ret_1y_total"], 1) if c["ret_1y_total"] is not None else None,
                                 "年化波動%": c["vol"], "最大回撤%": c["mdd"], "Sharpe": c["sharpe"], "Beta(對0050)": c["beta"],
                                 "54C占比%": round(c["ratio"] * 100, 1) if c["ratio"] is not None else None,
                                 "占比來源": c.get("ratio_src", ""),
                                 "未滿1年": "是" if c["young"] else "",
                                 "次數": c["n_events"], "發放月份": "/".join(str(k) for k in sorted(c["pay_months"])),
                                 "最近除息": c["last_ex"], "下次除息(已公告)": c["next_ex"]})
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
                st.caption("殖利率＝近12個月實際配息 ÷ 現價，為過去事實，不代表未來。**含息總報酬＝(現價 − 一年前價 + 近12月配息) ÷ 一年前價**："
                           "高殖利率若伴隨價差下跌，總報酬可能不如低殖利率；配息中資本利得的占比越高，越依賴行情。"
                           "年化波動／最大回撤／Sharpe／Beta 以近一年還原收盤(含息)計算(Sharpe 無風險利率取 1.5%，Beta 對 0050)；"
                           "選配息型 ETF 偏好：波動小、回撤小、Beta 低、Sharpe 高，並對照總報酬，不要只看配息。")
            new_rows = [m for m in master if m.get("first_seen")]
            new_rows.sort(key=lambda m: str(m["first_seen"]), reverse=True)
            gone_rows = [m for m in master if m.get("active") is False]
            if new_rows or gone_rows:
                st.subheader("清單異動（系統每日自動偵測）")
                if new_rows:
                    st.caption("新增 ETF（首次出現在清單的日期）")
                    st.dataframe(pd.DataFrame([{"首次出現": m["first_seen"], "代號": m["symbol"], "名稱": m.get("name") or "",
                                                "上市日": m.get("listed_date"), "現價": m.get("last_price")} for m in new_rows[:30]]),
                                 width="stretch", hide_index=True)
                if gone_rows:
                    st.caption("已從清單消失（可能下市或改名），規劃器不再納入")
                    st.dataframe(pd.DataFrame([{"代號": m["symbol"], "名稱": m.get("name") or ""} for m in gone_rows[:30]]),
                                 width="stretch", hide_index=True)

    with t_hold:
        st.divider()
        st.header("🧩 持股重疊與規模")
        if st.toggle('▸ 載入「持股重疊與規模」（較耗時，打開才計算）', value=False, key='etf_lz_hold'):
            _pos_h = E.position_summary(trades, price_map)
            _render_holdings_tab(sb, master, name_map, {s_: (p_["market_value"] or 0) for s_, p_ in _pos_h.items() if p_["shares"] > 0}, today)
