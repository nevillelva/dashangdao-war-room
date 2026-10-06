# -*- coding: utf-8 -*-
"""
etf_tab.py —— 戰情室「💰 ETF月配」分頁（R99新增）
計算全部在 etf_core.py（純函式、已離線測試）；這裡只負責讀寫 Supabase 與畫面。
資料表：etf_master / etf_dividend_events（排程 etf_dividend_sync 每日寫入）、etf_trades（使用者手動記買賣）。
三張表皆開 RLS 且收回 anon/authenticated 權限，只有後端 service_role 金鑰可存取。
"""
import datetime as dt

import pandas as pd
import streamlit as st

import etf_core as E
import etf_holdings as H

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
    st.caption("資料來源：元大/復華投信官網（每日、含基金規模）；其他投信用 MoneyDJ（月資料、只含揭露的前段持股、無規模）。"
               "所以重疊度是**下限估計**；沒有免費來源的『受益人數』『官方周轉率』不提供，周轉率改用相鄰兩次持股快照的權重變動自算（累積第二次快照後才會出現）。")
    # ---- 規模/集中度/周轉
    rows = []
    for sym, lst in sorted(snaps.items()):
        cur_d, cur = lst[0]
        ws = sorted((float(r["weight"]) for r in cur if r.get("weight") is not None), reverse=True)
        m = mm.get(sym, {})
        aum = m.get("aum_twd")
        row = {"代號": sym, "名稱": name_map.get(sym, ""), "規模(億)": round(float(aum) / 1e8, 1) if aum else None,
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


def _render_planner(master, events, today, apply_nhi, apply_fee, default_ratio):
    cands_all = E.candidate_table(master, events, today)
    if not cands_all:
        st.info("尚無候選 ETF（等待資料同步）。")
        return
    st.caption("三步：① 填本金與稅率 → ② 挑 ETF（高股息、市值型都可以，也可混搭）→ ③ 看每月實領與稅。"
               "所有金額都用『近 12 個月實際配息』推算（保守口徑），過去不代表未來。")
    st.subheader("① 資金與稅")
    mode = st.radio("你想知道什麼", ["💰 我有本金，每月能領多少", "🎯 我要每月領 X，要準備多少本金"], horizontal=True, key="etfp_mode")
    forward = mode.startswith("💰")
    c1, c2, c3 = st.columns(3)
    if forward:
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
    st.caption("占比（%，自動正規化）：" + ("每檔分到多少『本金』" if forward else "每檔分擔多少『每月領息』"))
    wcols = st.columns(min(len(picks), 4))
    weights = {}
    for i, p in enumerate(picks):
        weights[p["symbol"]] = wcols[i % len(wcols)].number_input(f"{p['symbol']} {p['name']}", 0, 100, int(round(100 / len(picks))), 5,
                                                                    key=f"etfp_w_{p['symbol']}")
    if sum(weights.values()) <= 0:
        weights = None

    kw = dict(default_ratio=default_ratio, apply_nhi=apply_nhi, apply_fee=apply_fee, bracket=bracket_pct / 100.0)
    r = E.plan_by_capital(capital, picks, weights, lot=lot, **kw) if forward else E.plan_for_after_tax(target, picks, weights, lot=lot, **kw)
    st.subheader("③ 結果")
    if not r or not r["rows"]:
        st.warning("資金不夠買到所選標的（整張）或標的沒有配息資料。可改『零股』、增加本金或換標的。")
        return
    m = st.columns(4)
    m[0].metric("實際投入", _fmt_money(r["cost"]))
    m[1].metric("平均每月實領（稅前）", _fmt_money(r["monthly_avg_net"]), help="已扣二代健保補充保費與匯費，未扣綜所稅")
    m[2].metric("平均每月（再扣綜所稅後）", _fmt_money(r["monthly_avg_after_tax"]))
    m[3].metric("稅後年化殖利率", f"{r['yield_after_tax_pct']:.2f}%")
    st.caption(f"買進手續費約 {r['buy_fee']:,.0f}" + (f"｜剩餘現金 {r['cash_left']:,.0f}" if forward else "")
               + f"｜全年稅後實領 {r['annual_net_after_tax']:,.0f}｜稅前毛殖利率 {r['yield_gross_pct']:.2f}%")
    if not forward and not r.get("achieved"):
        st.warning("以目前所選標的與占比，反推 10 輪仍未達到目標，數字僅供參考（可能配息資料不足）。")
    st.bar_chart(pd.DataFrame({"實領（稅前）": [r["monthly_net"][k] for k in range(1, 13)]}, index=[f"{k:02d}月" for k in range(1, 13)]))
    st.caption("橫軸是『入帳（發放）月份』，由近 12 個月每次配息的發放月推算；已逐檔逐次扣補充保費與匯費。")
    if r["months_with_income"] < 12:
        st.warning(f"這個組合一年只有 {r['months_with_income']} 個月有入帳；要『每月都有』請 A/B/C 各選一檔，或選月配 ETF。")
    st.dataframe(pd.DataFrame([{"代號": x["symbol"], "名稱": x["name"], "類型": E.KIND_LABEL.get(x["kind"], ""),
                                "配息節奏": E.FREQ_LABEL.get(x["freq"], ""), "組": x["ex_group"] or "",
                                "現價": x["price"], "買進": _lots(x["shares"]), "資金": round(x["capital"]),
                                "年領(稅前)": round(x["annual_gross"]), "殖利率%": round(x["yield_pct"], 2),
                                "每次約領": round(x["per_payment_avg"]), "單次54C基數": x["max_payment_base"],
                                "採用54C占比%": round(x["ratio_used"] * 100, 1)} for x in r["rows"]]),
                 width="stretch", hide_index=True)

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



def render_etf_tab(sb):
    st.title("💰 ETF 月配／季配 領息規劃與損益")
    if sb is None:
        st.error("Supabase 未連線，ETF 分頁需要資料庫。")
        return
    today = _today()
    try:
        master, events = _load_market(sb)
        trades = _load_trades(sb)
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

    with st.expander("⚙️ 稅費設定", expanded=False):
        c1, c2 = st.columns(2)
        apply_nhi = c1.checkbox("扣二代健保補充保費 2.11%（單次給付 ≥ 2 萬才扣）", value=True, key="etf_nhi")
        apply_fee = c2.checkbox("扣配息匯費 10 元／筆", value=True, key="etf_fee")
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

    ratios = {m["symbol"]: float(m["div_income_ratio"]) for m in master if m.get("div_income_ratio") not in (None, "")}
    default_ratio = default_pct / 100.0
    # 2026-10-06 改版：6 個分頁精簡為 4 個（規劃器為首頁；持倉＋領息明細合併；一覽＋持股重疊合併）
    t_plan, t_pos, t_trade, t_more = st.tabs(["🎯 領息規劃器", "📦 我的持倉與領息", "📒 買賣紀錄", "🔎 ETF 一覽與持股"])
    t_cash, t_scan, t_hold = t_pos, t_more, t_more

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
        _render_planner(master, events, today, apply_nhi, apply_fee, default_ratio)

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
