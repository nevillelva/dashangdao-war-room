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
        st.caption("未計入綜合所得稅（股利併入所得或 28% 分離課稅，依個人身分而定）。證交稅 ETF 賣出 0.1%、手續費 0.1425% 已計入損益。")

    t_pos, t_cash, t_plan, t_trade, t_scan = st.tabs(
        ["📦 我的持倉與損益", "💵 領息明細與預估", "🎯 月領規劃器", "📒 買賣紀錄", "🔎 ETF 配息一覽"])

    # ------------------------------------------------------------------ 我的持倉
    with t_pos:
        pos = E.position_summary(trades, price_map)
        held = {s: p for s, p in pos.items() if p["shares"] > 0}
        cf = E.dividend_cashflows(trades, events, today, apply_nhi, apply_fee)
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
            cf = E.dividend_cashflows(trades, events, today, apply_nhi, apply_fee)
            pj = E.project_income(trades, events, today, apply_nhi, apply_fee, months=12)
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
                r = E.plan_income(target, picks, weights, lot=lot, apply_nhi=apply_nhi, apply_fee=apply_fee)
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
                             "次數": c["n_events"], "發放月份": "/".join(str(k) for k in sorted(c["pay_months"])),
                             "最近除息": c["last_ex"], "下次除息(已公告)": c["next_ex"]})
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
            st.caption("殖利率＝近12個月實際配息 ÷ 現價，為過去事實，不代表未來；請留意配息是否含本金返還與淨值走勢。")
