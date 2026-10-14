# -*- coding: utf-8 -*-
"""【2026-10-14】網站「自助回測」面板：輸入進場分數、出場方式、成本 → 自動回測 → 通過／待觀察／不通過。
引擎在 bt_lab.py（純函式、有測試）；資料是 5 年面板（bt_dataset 表，由 GitHub Actions 的 backtest_oldscore 匯出）。
只讀價格與重建分數，不碰持倉或下單。"""
import datetime as dt

import pandas as pd
import streamlit as st

import bt_dataset_io as dio
import bt_lab as B

ICON = {"通過": "✅ 通過", "待觀察": "🟡 待觀察", "不通過": "❌ 不通過"}


def pct(v, d=2):
    return "—" if v is None or v != v else f"{v * 100:+.{d}f}%"


def num(v, d=2):
    return "—" if v is None or v != v else ("∞" if v == float("inf") else f"{v:.{d}f}")


def result_table(res):
    """把 bt_lab.run 的結果整理成一張比較表（純函式）。"""
    rows = []
    for label, r in res["results"].items():
        s = r["summary"]
        if not s.get("n"):
            rows.append({"出場方式": label, "判定": ICON.get(r["verdict"], r["verdict"]), "筆數": 0})
            continue
        eq = s.get("equity") or {}
        rows.append({
            "出場方式": label, "判定": ICON.get(r["verdict"], r["verdict"]), "筆數": s["n"], "訊號日數": s["signal_days"],
            "勝率": f"{s['win'] * 100:.1f}%", "平均(扣成本)": pct(s["mean_net"]), "中位數": pct(s["median_net"]),
            "獲利因子": num(s["pf"]), "平均持有(日)": f"{s['avg_hold']:.1f}",
            "樣本內": pct(s["is"]["mean_net"]), "樣本外": pct(s["oos"]["mean_net"]),
            "贏同期大盤": pct(s.get("excess_market")), "贏隨機(樣本外)": pct(s.get("excess_random_oos")),
            "t值(訊號日)": num(s.get("t_cluster")), "最大回撤(等權)": pct(eq.get("mdd")) if eq else "—",
            "年化(等權)": pct(eq.get("cagr")) if eq else "—", "夏普": num(eq.get("sharpe")) if eq else "—",
            "最差單筆": pct(s["worst"]), "未出場(略過)": s.get("open_n", 0),
        })
    return pd.DataFrame(rows)


@st.cache_resource(ttl=6 * 3600, show_spinner="讀取 5 年回測資料集…")
def _load_dataset(_sb):
    meta, panel = dio.load(_sb, "main")
    if panel is None:
        return None, None
    return meta, B.prepare(panel)


def render_bt_lab(sb):
    st.caption("輸入條件，程式自動回測並給判定；不用每次請人分析。**僅供研究，不是投資建議，戰情室沒有下單功能。**")
    if sb is None:
        st.error("Supabase 未連線")
        return
    meta, D = _load_dataset(sb)
    if D is None:
        st.warning("還沒有回測資料集。到 GitHub Actions 執行「手動-舊評分波段5年診斷與修復回測」，把 export 填 1，完成後這裡就能用。")
        return
    first, last = pd.Timestamp(D["dates"][0]).date(), pd.Timestamp(D["dates"][-1]).date()
    st.caption(f"資料集：{meta.get('universe_n', len(D['syms']))} 檔、{first}～{last}（建置 {str(meta.get('built_at', ''))[:10]}）。"
               "分數是 **5 年重建版**（價量均線、外資投信、月營收；基本面地雷等無法重建的因子視為不觸發），與網頁即時戰卡分數不完全相同。")

    with st.form("bt_lab_form"):
        c1, c2, c3 = st.columns(3)
        score_min = c1.number_input("進場：分數 ≥", value=6.0, step=1.0, key="btl_smin")
        mode = c2.radio("進場方式", ["首次跨越門檻那天", "每天符合都算（同檔持有中不重複）"], key="btl_mode")
        entry_at = c3.radio("成交時點", ["訊號隔日開盤", "訊號隔日收盤"], key="btl_at",
                            help="分數是收盤後才算出來的，最快隔天才能成交；用訊號當天價格等於偷看答案，所以不提供。")
        c4, c5 = st.columns(2)
        dr = c4.date_input("回測期間", value=(first, last), min_value=first, max_value=last, key="btl_range")
        use_max = c5.checkbox("設定分數上限", value=False, key="btl_usemax")
        score_max = c5.number_input("分數 ≤", value=10.0, step=1.0, key="btl_smax") if use_max else None

        st.markdown("**出場方式**（可複選，同一批進場一起比較）")
        e1, e2 = st.columns(2)
        holds = e1.multiselect("固定持有（交易日）", [3, 5, 10, 15, 20, 30, 40, 60], default=[5, 10, 20, 30], key="btl_holds")
        use_tpsl = e2.checkbox("停利／停損", value=False, key="btl_tpsl")
        t1, t2, t3 = st.columns(3)
        tp = t1.number_input("停利 %", value=12.0, step=1.0, key="btl_tp") if use_tpsl else 12.0
        sl = t2.number_input("停損 %", value=10.0, step=1.0, key="btl_sl") if use_tpsl else 10.0
        tn = t3.number_input("最長持有(日)", value=20, step=1, key="btl_tn") if use_tpsl else 20
        o1, o2, o3 = st.columns(3)
        use_ma = o1.checkbox("收盤跌破 MA20", value=False, key="btl_ma")
        use_atr = o2.checkbox("ATR 移動停損", value=False, key="btl_atr")
        atr_k = o2.number_input("ATR 倍數", value=3.0, step=0.5, key="btl_atrk") if use_atr else 3.0
        use_sc = o3.checkbox("分數跌破", value=False, key="btl_sc")
        sc_y = o3.number_input("跌破 Y", value=2.0, step=1.0, key="btl_scy") if use_sc else 2.0

        st.markdown("**成本**")
        k1, k2, k3 = st.columns(3)
        disc = k1.slider("手續費折扣（1.0＝不打折）", 0.2, 1.0, 1.0, 0.05, key="btl_disc")
        slip = k2.number_input("單邊滑價（基點，1＝0.01%）", value=0.0, step=1.0, key="btl_slip")
        mh = k3.number_input("最長持有上限(日)", value=60, step=5, help="分數／MA20／ATR 出場一直沒觸發時的強制出場日", key="btl_mh")
        go = st.form_submit_button("🚀 執行回測", width="stretch")

    if go:
        exits = [{"type": "hold", "n": int(n)} for n in holds]
        if use_tpsl:
            exits.append({"type": "tpsl", "tp": tp / 100, "sl": sl / 100, "n": int(tn)})
        if use_ma:
            exits.append({"type": "ma20"})
        if use_atr:
            exits.append({"type": "atr", "k": float(atr_k)})
        if use_sc:
            exits.append({"type": "score_below", "y": float(sc_y)})
        if not exits:
            st.warning("請至少選一種出場方式")
            return
        start, end = (dr[0], dr[1]) if isinstance(dr, (tuple, list)) and len(dr) == 2 else (first, last)
        spec = B.Spec(score_min=float(score_min), score_max=score_max, mode="first_cross" if mode.startswith("首次") else "every_day",
                      entry_at="next_open" if entry_at.endswith("開盤") else "next_close", start=str(start), end=str(end),
                      fee_disc=float(disc), slip_bps=float(slip), max_hold=int(mh), exits=exits)
        st.session_state["btl_tried"] = st.session_state.get("btl_tried", 0) + 1
        with st.spinner("回測中（約 10～40 秒）…"):
            res = B.run(D, spec, n_tried=st.session_state["btl_tried"])
        st.session_state["btl_last"] = (res, spec)

    last_run = st.session_state.get("btl_last")
    if not last_run:
        return
    res, spec = last_run
    st.markdown(f"**結果**：符合條件的訊號 {res['n_entries']} 筆；來回成本 {res['cost'] * 100:.3f}%；本次連線已試 {st.session_state.get('btl_tried', 1)} 組條件")
    tbl = result_table(res)
    st.dataframe(tbl, hide_index=True, width="stretch")
    st.download_button("下載結果表 CSV", tbl.to_csv(index=False).encode("utf-8-sig"), "bt_lab_result.csv", "text/csv", key="btl_dl")
    for label, r in res["results"].items():
        with st.expander(f"{ICON.get(r['verdict'], r['verdict'])}｜{label}"):
            if r["reasons"]:
                st.markdown("**判定理由**：" + "；".join(r["reasons"]))
            s = r["summary"]
            if s.get("n"):
                ys = s.get("years") or {}
                if ys:
                    st.markdown("**逐年平均（扣成本）**：" + "　".join(f"{y}：{pct(v['mean_net'])}（{v['n']}筆）" for y, v in sorted(ys.items())))
                st.markdown(f"樣本內 {s['is']['n']} 筆 {pct(s['is']['mean_net'])}／樣本外 {s['oos']['n']} 筆 {pct(s['oos']['mean_net'])}；"
                            f"同期大盤平均 {pct(s.get('mean_market'))}；隨機同日同檔數（扣成本）{pct(s.get('random_net'))}")
            for w in r["warnings"]:
                st.caption("⚠️ " + w)
    st.caption("判定規則（事先固定，不看結果調整）：樣本 ≥200 筆且 ≥30 個訊號日；樣本外扣成本後平均 >0；樣本外贏過同日隨機挑；"
               "以訊號日為單位的 t 值 ≥2；逐年多數為正。全部達成才是『通過』，樣本外 ≤0 是『不通過』，其餘『待觀察』。"
               "同一天的訊號高度相關，所以 t 值以「訊號日」為單位，不是以筆數。")
