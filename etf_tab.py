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
        st.caption("未計入綜合所得稅（股利併入所得或 28% 分離課稅，依個人身分而定）。證交稅 ETF 賣出 0.1%、手續費 0.1425% 已計入損益。"
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
    t_pos, t_cash, t_plan, t_trade, t_scan, t_hold = st.tabs(
        ["📦 我的持倉與損益", "💵 領息明細與預估", "🎯 月領規劃器", "📒 買賣紀錄", "🔎 ETF 配息一覽", "🧩 持股重疊與規模"])

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

    # ------------------------------------------------------------------ 規劃器
    with t_plan:
        st.subheader("我想每月領多少？")
        pick = st.radio("目標月領（實領，扣健保/匯費後的 12 個月平均）",
                        ["1 萬", "2 萬", "3 萬", "5 萬", "自訂"], horizontal=True, index=1, key="etf_target_pick")
        presets = {"1 萬": 10000, "2 萬": 20000, "3 萬": 30000, "5 萬": 50000}
        if pick == "自訂":
            target = st.number_input("自訂月領金額(元)", min_value=1000, max_value=1_000_000, value=15000, step=1000,
                                     key="etf_target_custom")
        else:
            target = presets[pick]
        c1, c2, c3 = st.columns(3)
        lot_mode = c1.radio("買進單位", ["整張(1000股)", "零股(1股)"], horizontal=True, key="etf_lot_mode")
        include_bond = c2.checkbox("納入債券ETF(代號結尾B)", value=False, key="etf_bond")
        max_y = c3.slider("排除殖利率高於(%)", 5, 30, 15, key="etf_maxy",
                          help="過高殖利率常含本金返還或一次性收益，不具延續性，預設排除。")
        lot = E.LOT if lot_mode.startswith("整張") else 1
        cands = E.candidate_table(master, events, today)
        if not cands:
            st.info("尚無候選 ETF（等待資料同步）。")
        else:
            plans = E.suggest_combo(cands, "auto", include_bond=include_bond, max_yield_pct=max_y)
            cmap = {c["symbol"]: c for c in cands}
            labels = [p["label"] for p in plans] + ["✋ 自己挑選"]
            choice = st.selectbox("方案", labels, key="etf_plan_choice")
            if choice == "✋ 自己挑選":
                opts = [f"{c['symbol']} {c['name']}（{E.FREQ_LABEL.get(c['freq'], '')}｜殖利率{c['yield_pct']:.1f}%）" for c in cands]
                sel = st.multiselect("選 1~6 檔", opts, max_selections=6, key="etf_self_pick")
                picks = [cmap[s.split(" ")[0]] for s in sel]
            else:
                picks = next(p["picks"] for p in plans if p["label"] == choice)
            weights = None
            if len(picks) > 1:
                st.caption("每檔分擔的「年領金額」占比（預設平均）")
                wcols = st.columns(len(picks))
                weights = {}
                for col, p in zip(wcols, picks):
                    weights[p["symbol"]] = col.number_input(f"{p['symbol']}", 0, 100, int(round(100 / len(picks))),
                                                            step=5, key=f"etf_w_{p['symbol']}")
                if sum(weights.values()) <= 0:
                    weights = None
            if not picks:
                st.info("請選擇至少 1 檔，或調整上面的條件（例如放寬殖利率上限）。")
            else:
                r = E.plan_income(target, picks, weights, lot=lot, apply_nhi=apply_nhi, apply_fee=apply_fee, default_ratio=default_ratio)
                if not r:
                    st.warning("所選標的沒有可用的配息資料。")
                else:
                    m = st.columns(4)
                    m[0].metric("需要本金", _fmt_money(r["capital"]))
                    m[1].metric("平均每月實領", _fmt_money(r["avg_monthly"]))
                    m[2].metric("最低/最高月份", f"{r['min_month']:,.0f} / {r['max_month']:,.0f}")
                    m[3].metric("加權殖利率", f"{r['blended_yield_pct']:.2f}%")
                    st.dataframe(pd.DataFrame([{"代號": x["symbol"], "名稱": x["name"], "類型": E.FREQ_LABEL.get(x["freq"], ""),
                                                "現價": x["price"], "買進": _lots(x["shares"]), "資金": round(x["capital"]),
                                                "年領(稅前)": round(x["annual_gross"]), "殖利率%": round(x["yield_pct"], 2),
                                                "每次約領": round(x["per_payment_avg"])} for x in r["rows"]]),
                                 width="stretch", hide_index=True)
                    mn = r["monthly_net"]
                    st.bar_chart(pd.DataFrame({"實領": [mn[k] for k in range(1, 13)]},
                                              index=[f"{k}月" for k in range(1, 13)]))
                    if r["months_with_income"] < 12:
                        st.warning(f"這個組合一年只有 {r['months_with_income']} 個月有入帳；要「每月都有」請改用月配ETF，或季配三檔錯開。")
                    st.caption("計算方式：把近 12 個月每次配息的『發放月份』逐月加總（逐檔逐次扣費），反覆放大股數直到 12 個月平均實領達標，"
                                   "再依整張/零股進位。是以過去配息推算的「情境試算」，不是保證；ETF 配息會隨收益與淨值波動，價格也會漲跌。")
                    young = [p["symbol"] for p in picks if p.get("young")]
                    if young:
                        st.warning("以下標的上市（或首次配息）未滿 1 年，近 12 個月配息不足一整年份，年領會被低估、也不一定代表常態："
                                   + "、".join(young))
                    hi = [p["symbol"] for p in picks if p["yield_pct"] >= 12]
                    if hi:
                        st.warning("殖利率 ≥ 12% 的標的（" + "、".join(hi) + "）常有部分配息來自資本利得（價差變現），"
                                   "漲勢中才配得出來；要看「含息總報酬」與配息組成，不要只看殖利率。")
                    low = [x for x in r["rows"] if x["n_events"] < 3]
                    if low:
                        st.warning("以下標的近 12 個月配息次數不足 3 次（新上市或不定期），推算誤差大：" + "、".join(x["symbol"] for x in low))

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
        _pos_h = E.position_summary(trades, price_map)
        _render_holdings_tab(sb, master, name_map, {s_: (p_["market_value"] or 0) for s_, p_ in _pos_h.items() if p_["shares"] > 0}, today)
