# -*- coding: utf-8 -*-
"""
premarket_tab.py —— 戰情室「📰 早盤情報」分頁（2026-10-07 新增）
資料由排程 premarket_brief（05:30）／premarket_supplement（08:00）／news_collect（19:45、21:40）寫入：
  premarket_brief（每日一列 jsonb）、mops_events（官方重大訊息分類結果）、news_items（新聞標題＋連結）、premarket_outcome（事後檢驗）。
四張表皆開 RLS 且沒有任何 policy，只有後端 service_role 金鑰可存取。
顏色慣例同全站（紅漲綠跌）：偏多＝紅、偏空＝綠。本頁所有內容僅供自己研究參考，不是投資建議，戰情室沒有下單功能。
"""
import datetime as dt

import pandas as pd
import streamlit as st

TPE = dt.timezone(dt.timedelta(hours=8))
BIAS_COLOR = {"偏多": "#ff4d4d", "偏空": "#00c853", "中性": "#9fb3c8"}


def _paged(make_query, page=1000, cap=20000):
    out, off = [], 0
    while off < cap:
        rows = make_query().range(off, off + page - 1).execute().data or []
        out += rows
        if len(rows) < page:
            break
        off += page
    return out


# ------------------------------------------------------------------ 純函式（可離線測試）

def outcome_stats(rows):
    """premarket_outcome 列 → 依方向（偏多/偏空）統計。命中＝報酬方向與預期方向相同（偏多要漲、偏空要跌）。
    回傳 list[dict]：方向、樣本數、1日命中率、1日平均、5日樣本、5日命中率、5日平均。"""
    out = []
    for lab, d in (("偏多", 1), ("偏空", -1)):
        rs = [r for r in rows if r.get("direction") == d and r.get("ret_1d") is not None]
        if not rs:
            continue
        hit1 = sum(1 for r in rs if r["ret_1d"] * d > 0) / len(rs) * 100
        avg1 = sum(r["ret_1d"] for r in rs) / len(rs)
        r5 = [r for r in rows if r.get("direction") == d and r.get("ret_5d") is not None]
        hit5 = (sum(1 for r in r5 if r["ret_5d"] * d > 0) / len(r5) * 100) if r5 else None
        avg5 = (sum(r["ret_5d"] for r in r5) / len(r5)) if r5 else None
        out.append({"方向": lab, "樣本數": len(rs), "1日命中率%": round(hit1, 1), "1日平均報酬%": round(avg1, 2),
                    "5日樣本": len(r5), "5日命中率%": None if hit5 is None else round(hit5, 1),
                    "5日平均報酬%": None if avg5 is None else round(avg5, 2)})
    return out


def category_stats(rows):
    by = {}
    for r in rows:
        if r.get("ret_1d") is None or not r.get("category"):
            continue
        by.setdefault((r["category"], r["direction"]), []).append(r["ret_1d"] * r["direction"])
    res = [{"事件類別": c, "方向": "偏多" if d == 1 else "偏空", "樣本數": len(v), "命中率%": round(sum(1 for x in v if x > 0) / len(v) * 100, 1),
            "平均(依方向)%": round(sum(v) / len(v), 2)} for (c, d), v in by.items()]
    return sorted(res, key=lambda x: -x["樣本數"])


def bias_badge(b):
    return f"<span style='color:{BIAS_COLOR.get(b, '#9fb3c8')}; font-weight:bold;'>{b}</span>"


def filter_picks(picks, mode):
    if mode == "偏多":
        return [p for p in picks if p.get("bias") == "偏多"]
    if mode == "偏空":
        return [p for p in picks if p.get("bias") == "偏空"]
    if mode == "我的持倉／雷達":
        return [p for p in picks if p.get("own")]
    return picks


# ------------------------------------------------------------------ 讀取

@st.cache_data(ttl=300, show_spinner=False)
def _load_dates(_sb):
    r = _sb.table("premarket_brief").select("brief_date,status,sent_at").order("brief_date", desc=True).limit(40).execute().data or []
    return r


@st.cache_data(ttl=300, show_spinner=False)
def _load_brief(_sb, d):
    r = _sb.table("premarket_brief").select("*").eq("brief_date", d).limit(1).execute().data or []
    return r[0] if r else None


@st.cache_data(ttl=300, show_spinner=False)
def _load_events(_sb, start, end):
    return _paged(lambda: _sb.table("mops_events").select("ev_date,ev_time,market,symbol,name,category,direction,importance,subject,event_date")
                  .gte("ev_date", start).lte("ev_date", end).order("ev_date", desc=True))


@st.cache_data(ttl=300, show_spinner=False)
def _load_news(_sb, since_iso):
    return _paged(lambda: _sb.table("news_items").select("source,title,url,published_at,symbols,ai_ok")
                  .gte("published_at", since_iso).order("published_at", desc=True), cap=3000)


@st.cache_data(ttl=600, show_spinner=False)
def _load_outcomes(_sb):
    return _paged(lambda: _sb.table("premarket_outcome").select("brief_date,symbol,category,direction,ret_1d,ret_5d").order("brief_date", desc=True), cap=5000)


# ------------------------------------------------------------------ 畫面

def _pick_card(p):
    head = f"{p.get('name') or p['symbol']}（{p['symbol']}）" + (f"｜{p['sector']}" if p.get("sector") else "") + ("｜📌 持倉/雷達" if p.get("own") else "")
    st.markdown(f"**{head}**　{bias_badge(p.get('bias', '中性'))}", unsafe_allow_html=True)
    for e in p.get("events", [])[:4]:
        extra = f"（年增 {e['growth_pct']:+.1f}%）" if e.get("growth_pct") is not None else ""
        st.markdown(f"- `{e.get('category')}` {(e.get('ev_time') or '')[:5]} {e.get('subject')}{extra}")
        for f in e.get("flags") or []:
            st.caption(f"　⚑ {f}")
    if p.get("why"):
        st.markdown(f"　**原因：** {p['why']}")
    if p.get("risk"):
        st.markdown(f"　**風險：** {p['risk']}")
    if p.get("tech"):
        st.caption(f"　{p['tech']}")


def render_premarket_tab(sb):
    st.title("📰 早盤情報")
    if sb is None:
        st.error("Supabase 未連線，早盤情報需要資料庫。")
        return
    st.caption("每個交易日 05:30 產生（證交所重大訊息檔約 05:24 更新）、08:00 補充；同步推播到 Telegram。"
               "事件分類是**規則判斷**，AI 只負責把公告原文整理成白話，不能新增事實。僅供自己研究參考，**不是投資建議，戰情室沒有下單功能**。")
    try:
        dates = _load_dates(sb)
    except Exception as e:  # noqa: BLE001
        st.error(f"讀取失敗：{type(e).__name__}: {e}（表是否已建立？）")
        return
    if not dates:
        st.info("還沒有早盤情報資料。第一次要等下一個交易日 05:30，或到 GitHub Actions 手動執行 `system_scheduler`"
                "（stage 選 `premarket_brief`、premarket_mode 填 `force`）。")
        return
    labels = [f"{d['brief_date']}（{'已推播' if d.get('sent_at') else d.get('status') or '—'}）" for d in dates]
    idx = st.selectbox("日期", range(len(dates)), format_func=lambda i: labels[i], key="pm_date")
    day = dates[idx]["brief_date"]
    row = _load_brief(sb, day)
    if not row:
        st.warning("該日沒有資料")
        return
    b = row.get("brief") or {}
    sup = row.get("supplement") or {}
    gen = b.get("generated_hm", "")
    st.markdown(f"**{day}** 產生於 {gen}" + (f"｜08:00 補充：{sup.get('generated_hm')}" if sup else ""))
    if b.get("us_line"):
        st.markdown("🌎 " + b["us_line"])
    if b.get("regime_line"):
        st.markdown("🌡️ " + b["regime_line"])
    ss = b.get("source_status") or {}
    if ss.get("material_stale"):
        st.warning(f"證交所重大訊息檔當時尚未更新（最新只到 {ss.get('material_latest') or '?'}）；"
                   "08:00 補充會再抓一次。")
    if b.get("ai_overview"):
        st.info("🧭 " + b["ai_overview"])

    t1, t2, t3, t4, t5, t6 = st.tabs(["🎯 重點個股", "📋 全部公告", "📅 行事曆", "📰 新聞與連結", "📈 事後檢驗", "ℹ️ 來源與說明"])

    with t1:
        picks = b.get("picks") or []
        mode = st.radio("篩選", ["全部", "偏多", "偏空", "我的持倉／雷達"], horizontal=True, key="pm_filter")
        shown = filter_picks(picks, mode)
        if not shown:
            st.caption("沒有符合的個股。")
        for p in shown:
            with st.container(border=True):
                _pick_card(p)
        if sup:
            st.markdown("---")
            st.subheader(f"🔔 08:00 補充（{sup.get('generated_hm', '')}）")
            if sup.get("overview"):
                st.info(sup["overview"])
            for p in sup.get("picks") or []:
                with st.container(border=True):
                    _pick_card(p)
            if sup.get("own_flags"):
                st.markdown("**持倉提醒：** " + "、".join(sup["own_flags"]))
            for n in sup.get("news") or []:
                st.markdown(f"- [{n['title']}]({n['url']})（{n['source']}）")
            if not (sup.get("picks") or sup.get("own_flags") or sup.get("news")):
                st.caption("05:30 之後沒有新增達門檻的事項。")

    with t2:
        end = day
        start = (dt.datetime.strptime(day, "%Y-%m-%d") - dt.timedelta(days=4)).strftime("%Y-%m-%d")
        try:
            evs = _load_events(sb, start, end)
        except Exception as e:  # noqa: BLE001
            st.error(f"讀取公告失敗：{type(e).__name__}")
            evs = []
        if evs:
            df = pd.DataFrame(evs)
            cats = sorted(df["category"].dropna().unique())
            c1, c2, c3 = st.columns([2, 1, 2])
            sel = c1.multiselect("類別", cats, default=[c for c in cats if c not in ("例行公告", "其他")], key="pm_cats")
            min_imp = c2.slider("最低重要度", 0, 5, 1, key="pm_imp")
            q = c3.text_input("代號或公司名稱", key="pm_q").strip()
            f = df[df["category"].isin(sel) & (df["importance"].fillna(0) >= min_imp)]
            if q:
                f = f[f["symbol"].astype(str).str.contains(q) | f["name"].astype(str).str.contains(q)]
            f = f.sort_values(["importance", "ev_date", "ev_time"], ascending=[False, False, False])
            show = f.rename(columns={"ev_date": "日期", "ev_time": "時間", "symbol": "代號", "name": "公司", "category": "類別",
                                     "importance": "重要度", "subject": "主旨", "market": "市場", "event_date": "事件日"})
            st.dataframe(show[["日期", "時間", "代號", "公司", "類別", "重要度", "主旨", "事件日"]].head(500), hide_index=True, width="stretch")
            st.caption(f"{start}～{end} 共 {len(df)} 件、篩選後 {len(f)} 件（最多顯示 500）。重要度 0＝例行公告（更名、章程…）。")
        else:
            st.caption("這段期間沒有公告資料（或尚未寫入）。")

    with t3:
        cal = b.get("calendar") or {}
        st.markdown("**🎤 近 7 日法說會**")
        st.write("、".join(f"{x['name']}{x['symbol']}（{x['date'][5:]}）" for x in cal.get("meetings", [])) or "—")
        st.markdown("**💰 近 7 日除權息**")
        st.write("、".join(f"{x.get('name') or x['symbol']}（{x['date'][5:]}）" for x in cal.get("exdiv", [])) or "—")
        st.markdown("**🔔 持倉／雷達提醒**")
        st.write("、".join(cal.get("own_flags", [])) or "—")
        st.caption("法說會日期來自公司『召開法人說明會』重大訊息的內文（官方沒有獨立的法說會行事曆資料集）；處置／注意／除權息來自證交所與櫃買中心 OpenAPI。")

    with t4:
        try:
            since = (dt.datetime.strptime(day, "%Y-%m-%d") - dt.timedelta(days=2)).replace(tzinfo=TPE).isoformat()
            news = _load_news(sb, since)
        except Exception as e:  # noqa: BLE001
            st.error(f"讀取新聞失敗：{type(e).__name__}")
            news = []
        srcs = sorted({n["source"] for n in news})
        pick_src = st.multiselect("來源", srcs, default=srcs, key="pm_src")
        only_sym = st.checkbox("只看含股票代號的", value=False, key="pm_onlysym")
        k = 0
        for n in news:
            if n["source"] not in pick_src or (only_sym and not n.get("symbols")):
                continue
            k += 1
            if k > 150:
                break
            t = (n.get("published_at") or "")[5:16].replace("T", " ")
            tag = "" if n.get("ai_ok") else "　`僅連結`"
            sy = f"　{'、'.join(n['symbols'])}" if n.get("symbols") else ""
            st.markdown(f"- {t}　[{n['title']}]({n['url']})　<small>{n['source']}</small>{sy}{tag}", unsafe_allow_html=True)
        if not news:
            st.caption("沒有新聞資料。")
        pod = b.get("podcast") or []
        if pod:
            st.markdown("**🎙️ Podcast 最新一集（只列標題與連結）**")
            for p in pod:
                st.markdown(f"- [{p['title']}]({p.get('url', '')})（{p['name']}，{(p.get('published_at') or '')[:10]}）")

    with t5:
        try:
            outs = _load_outcomes(sb)
        except Exception as e:  # noqa: BLE001
            st.error(f"讀取失敗：{type(e).__name__}")
            outs = []
        st.caption("追蹤『早盤情報挑出的偏多／偏空個股』後續實際走勢：報酬＝當日收盤相對『情報前一交易日收盤』，5 日＝第 5 個交易日收盤。"
                   "命中＝走勢方向與事件方向一致。只能追蹤資料庫快照涵蓋的股票；樣本少時不要下結論。")
        stats = outcome_stats(outs)
        if stats:
            st.dataframe(pd.DataFrame(stats), hide_index=True, width="stretch")
            cs = category_stats(outs)
            if cs:
                st.markdown("**依事件類別（1 日）**")
                st.dataframe(pd.DataFrame(cs), hide_index=True, width="stretch")
        else:
            st.caption("還沒有足夠的追蹤資料（每天 05:30 會回填前幾天的結果）。")

    with t6:
        st.markdown(
            "**資料來源（皆為公開、可合法取用的管道）**\n"
            "- 官方重大訊息：證交所 OpenAPI `t187ap04_L`、櫃買中心 OpenAPI `mopsfin_t187ap04_O`（政府資料開放授權條款 1.0）。"
            "公開資訊觀測站網頁依證交所使用條款禁止自動化存取，所以改用上述官方 API 取得同一份資料。\n"
            "- 新聞：中央社、鉅亨網 RSS（標題可送 AI）；Yahoo 股市、經濟日報、MoneyDJ RSS **只顯示標題與連結，完全不進 AI**"
            "（經濟日報／MoneyDJ 的 robots 明文禁止 AI/ML 用途）。\n"
            "- 股癌：只列公開 Podcast RSS 的集數標題與連結。Vocus 沙龍逐字稿為付費內容，**不抓取**。\n"
            "- 不使用：CMoney（無公開 API、著作權保留）、券商研究報告（登入／驗證碼牆）、Google News RSS（條款禁止）。\n"
            "- 美股與匯率：Finnhub。\n\n"
            f"{b.get('attribution') or '資料來源：臺灣證券交易所／證券櫃檯買賣中心 OpenAPI'}\n\n"
            "**限制**：重大訊息檔約 05:24 才更新，所以 05:30 版涵蓋到『前一個交易日～今晨』；之後的公告靠 08:00 補充。"
            "分類與方向是關鍵字規則，可能誤判，**請以公告原文為準**。"
        )
        st.caption(f"來源抓取狀態：{ss.get('twse', '')}／{ss.get('tpex', '')}｜新聞：{ss.get('news', '')}｜AI：{b.get('ai_status', '')}")
