"""
早盤情報的『排程編排層』（2026-10-07）——把 premarket.py 的純邏輯接上 Supabase／推播／AI。
所有外部依賴都從 deps（SimpleNamespace）注入，方便用假物件測試；system_scheduler 只放薄薄的包裝。

deps 需要的欄位：
  now()                  -> 台北時間 datetime
  prev_trading_day(date) -> 前一交易日 date（或 None）
  clean_symbol(raw)      -> 乾淨代號
  finnhub_quote(sym)     -> dict(ok, dp, c…)；finnhub_forex(base, quote) -> dict
  call_ai(system, prompt)-> (ok, text)
  send(text)             -> bool（Telegram 全部成功）
  get_config(key, default)
  sleep(sec)
隱私：Actions 日誌是公開的 → 這裡只 print「數量與狀態」，不 print 持倉、個股名稱或推薦內容。
"""
import json
import re
from datetime import datetime, timedelta

import premarket as pm

SEND_WAIT_UNTIL = "05:48"       # 證交所重大訊息檔還沒更新時，最晚等到這個時間
POLL_SECONDS = 180
EXDIV_LOOKAHEAD = 7


def _pg_all(make_query, page=1000, cap=20000):
    out, off = [], 0
    while off < cap:
        rows = make_query().range(off, off + page - 1).execute().data or []
        out.extend(rows)
        if len(rows) < page:
            break
        off += page
    return out


# ------------------------------------------------------------------ 資料蒐集

def load_own(sb, deps):
    """持倉＋雷達：{代號: 名稱}。失敗回空 dict。"""
    own = {}
    try:
        for r in (sb.table("system_portfolio").select("symbol,name").in_("status", ["holding", "pending"]).execute().data or []):
            s = deps.clean_symbol(r.get("symbol"))
            if s:
                own[s] = r.get("name") or ""
    except Exception as e:  # noqa: BLE001
        print(f"[早盤] 讀取模擬倉失敗：{type(e).__name__}")
    try:
        res = sb.table("user_state").select("state_value").eq("state_key", "commander_main").limit(1).execute()
        if res.data:
            st = res.data[0].get("state_value") or {}
            for grp in ("portfolio", "pinned_stocks"):
                for k, v in (st.get(grp) or {}).items():
                    s = deps.clean_symbol(k)
                    if s:
                        nm = (v.get("name") if isinstance(v, dict) else "") or ""
                        own.setdefault(s, nm)
    except Exception as e:  # noqa: BLE001
        print(f"[早盤] 讀取自選/持倉失敗：{type(e).__name__}")
    return own


def load_liquid(sb, date_s):
    """前一交易日成交值 {代號: 成交值}（快照只涵蓋部分股票）。"""
    try:
        rows = _pg_all(lambda: sb.table("twse_market_snapshot").select("symbol,trading_value").eq("trade_date", date_s).order("symbol"))
        return {str(r["symbol"]): float(r["trading_value"]) for r in rows if r.get("trading_value") is not None}
    except Exception as e:  # noqa: BLE001
        print(f"[早盤] 讀取成交值失敗：{type(e).__name__}")
        return None


def load_sector_map(deps):
    try:
        raw = deps.get_config("sector_map_v1", "")
        d = raw if isinstance(raw, dict) else (json.loads(raw) if isinstance(raw, str) and raw.strip() else {})
        return d.get("map") or {}
    except Exception:  # noqa: BLE001
        return {}


def regime_line(deps, expected_asof):
    try:
        raw = deps.get_config("regime_state_v1", "")
        st = raw if isinstance(raw, dict) else (json.loads(raw) if isinstance(raw, str) and raw.strip() else {})
        if not st:
            return ""
        labels = st.get("labels") or {}
        flags = st.get("flags_true") or []
        nm = "、".join(labels.get(f, f) for f in flags) or "一般盤勢"
        asof = str(st.get("asof") or "")[:10]
        stale = "（資料日 " + asof + "，非最新）" if asof and expected_asof and asof < expected_asof else ""
        import short_gate as sg
        ok, _ = sg.short_regime_ok(st, expected_asof)
        return f"盤勢：{nm}{stale}｜舊評分做空今日{'可進場' if ok else '不放行'}"
    except Exception:  # noqa: BLE001
        return ""


def fetch_us(deps):
    us = {}
    for sym in ("SOXX", "QQQ", "SPY", "TSM", "NVDA"):
        try:
            us[sym] = deps.finnhub_quote(sym)
        except Exception:  # noqa: BLE001
            us[sym] = {"ok": False}
    try:
        us["USDTWD"] = deps.finnhub_forex("USD", "TWD")
    except Exception:  # noqa: BLE001
        us["USDTWD"] = {"ok": False}
    return us


def collect_material(session, since_date, expected_date):
    """抓上市＋上櫃重大訊息。回傳 (events, status{...})。"""
    st = {}
    events = []
    rows_l, s1 = pm.fetch_json(pm.TWSE_MATERIAL_URL, session)
    rows_o, s2 = pm.fetch_json(pm.TPEX_MATERIAL_URL, session)
    st["twse"], st["tpex"] = s1, s2
    fresh, latest = pm.material_freshness(rows_l, expected_date) if rows_l is not None else (False, None)
    _, latest_o = pm.material_freshness(rows_o, expected_date) if rows_o is not None else (False, None)
    st["material_latest"], st["material_latest_tpex"] = latest, latest_o
    st["material_stale"] = not fresh
    st["n_twse_rows"], st["n_tpex_rows"] = len(rows_l or []), len(rows_o or [])
    if rows_l:
        events += pm.build_events(rows_l, "L", since_date)
    if rows_o:
        events += pm.build_events(rows_o, "O", since_date)
    return events, st


def collect_calendar(session, today_s):
    end = (datetime.strptime(today_s, "%Y-%m-%d") + timedelta(days=EXDIV_LOOKAHEAD)).strftime("%Y-%m-%d")
    urls = [pm.TWSE_EXDIV_URL, pm.TWSE_PUNISH_URL, pm.TWSE_NOTETRANS_URL, pm.TPEX_EXDIV_URL, pm.TPEX_DISPOSAL_URL, pm.TPEX_WARNING_URL]
    got, status = [], {}
    for u in urls:
        rows, st = pm.fetch_json(u, session)
        got.append(rows or [])
        status[u.rsplit("/", 1)[-1]] = st if rows is None else f"ok({len(rows)})"
    cal = pm.build_calendar(got[0], got[1], got[2], got[3], got[4], got[5], today_s, end)
    return cal, status


def load_meetings(sb, today_s, events):
    """今天起 7 天內的法說會：先看 DB 裡先前公告的，再補上這次抓到的。"""
    end = (datetime.strptime(today_s, "%Y-%m-%d") + timedelta(days=7)).strftime("%Y-%m-%d")
    seen, out = set(), []
    try:
        rows = (sb.table("mops_events").select("symbol,name,event_date").eq("category", "法說會")
                .gte("event_date", today_s).lte("event_date", end).order("event_date").limit(400).execute().data or [])
    except Exception:  # noqa: BLE001
        rows = []
    for r in rows + [e for e in events if e.get("category") == "法說會"]:
        d = r.get("event_date")
        if d and today_s <= d <= end and (r["symbol"], d) not in seen:
            seen.add((r["symbol"], d))
            out.append({"symbol": r["symbol"], "name": r.get("name", ""), "date": d})
    out.sort(key=lambda x: (x["date"], x["symbol"]))
    return out


def own_flags(own, cal, meetings):
    fl = []
    disp = {x["symbol"]: x for x in cal.get("disposal", [])}
    note = {x["symbol"] for x in cal.get("notice", [])}
    exd = {x["symbol"]: x for x in cal.get("exdiv", [])}
    meet = {x["symbol"]: x for x in meetings}
    for s, nm in own.items():
        lab = f"{nm or s}({s})"
        if s in disp:
            fl.append(f"{lab} 處置中至{disp[s]['until'][5:]}")
        if s in note:
            fl.append(f"{lab} 列注意股")
        if s in exd:
            fl.append(f"{lab} {exd[s]['date'][5:]}除權息")
        if s in meet:
            fl.append(f"{lab} {meet[s]['date'][5:]}法說會")
    return fl


def news_since(prev_td, now):
    d = datetime(prev_td.year, prev_td.month, prev_td.day, 13, 30, tzinfo=pm.TPE)
    return d


def pick_news(items, since_dt, cand_syms, own_syms, name_to_code):
    """整理新聞：過濾時間、抽代號、去重；回傳 (news_ai, news_links, all_items)。"""
    seen, rows = set(), []
    for it in items:
        key = pm.news_hash(it["source"], it["url"])
        if key in seen:
            continue
        seen.add(key)
        if it.get("published_at"):
            try:
                if datetime.fromisoformat(it["published_at"]) < since_dt:
                    continue
            except ValueError:
                pass
        it = dict(it, url_hash=key, symbols=pm.extract_symbols(it["title"], name_to_code))
        rows.append(it)

    def score(it):
        s = 0
        if set(it["symbols"]) & set(own_syms):
            s += 2
        if set(it["symbols"]) & set(cand_syms):
            s += 1
        return (-s, it.get("published_at") or "")

    rows_sorted = sorted(rows, key=lambda x: x.get("published_at") or "", reverse=True)
    rows_sorted.sort(key=lambda x: score(x)[0])
    ai = [dict(n, source=n["source_name"]) for n in rows_sorted if n["ai_ok"]]
    lk = [dict(n, source=n["source_name"]) for n in rows_sorted if not n["ai_ok"]]
    return ai, lk, rows


# ------------------------------------------------------------------ AI

def ai_enrich(deps, cands, news_ai, us_line):
    """回傳 (overview, {symbol: {direction, reason, risk}}, 狀態字串)。AI 失敗不影響主流程。"""
    if not cands:
        return "", {}, "無候選"
    prompt = pm.build_ai_prompt(cands, [n["title"] for n in news_ai], us_line)
    try:
        ok, text = deps.call_ai(pm.AI_SYSTEM, prompt)
    except Exception as e:  # noqa: BLE001
        return "", {}, f"AI 例外：{type(e).__name__}"
    if not ok:
        return "", {}, "AI 失敗"
    parsed = pm.parse_ai_json(text, {c["symbol"] for c in cands})
    if not parsed:
        return "", {}, "AI 回覆無法解析"
    return parsed["overview"], {i["symbol"]: i for i in parsed["items"]}, f"AI ok({len(parsed['items'])})"


def merge_ai(picks, ai_items):
    for p in picks:
        a = ai_items.get(p["symbol"])
        auto = "；".join(pm.event_line(e, 40) for e in p["events"][:1])
        p["why"] = (a or {}).get("reason") or auto
        if a and a.get("risk"):
            p["risk"] = a["risk"]
        if a:
            sign = (a["direction"] > 0) - (a["direction"] < 0)
            rule = {"偏多": 1, "偏空": -1}.get(p["bias"], 0)
            if sign and rule and sign != rule:
                p["risk"] = (p.get("risk", "") + "｜AI 與規則判斷方向不同，請看公告原文").strip("｜")
            p["ai_direction"] = a["direction"]
    return picks


def attach_context(sb, picks, sector_map):
    """板塊＋昨晚掃描命中（只讀）。"""
    syms = [p["symbol"] for p in picks]
    scan = {}
    if syms:
        try:
            last = (sb.table("overnight_scan_results").select("scan_date").order("scan_date", desc=True).limit(1).execute().data or [])
            if last:
                rows = (sb.table("overnight_scan_results").select("symbol,matched_commands")
                        .eq("scan_date", last[0]["scan_date"]).in_("symbol", syms).execute().data or [])
                for r in rows:
                    mc = r.get("matched_commands")
                    scan[r["symbol"]] = "、".join(mc[:2]) if isinstance(mc, list) else str(mc or "")[:30]
        except Exception as e:  # noqa: BLE001
            print(f"[早盤] 讀取昨晚掃描失敗：{type(e).__name__}")
    for p in picks:
        p["sector"] = sector_map.get(p["symbol"], "")
        if scan.get(p["symbol"]):
            p["tech"] = "昨晚掃描命中 " + scan[p["symbol"]]
    return picks


# ------------------------------------------------------------------ 結果追蹤

def update_outcomes(sb, deps, today_s, days_back=12):
    """把近期早盤情報的個股回填 ret_1d／ret_5d（用 twse_market_snapshot 收盤價，快照沒有的略過）。回傳寫入列數。"""
    n = 0
    try:
        briefs = (sb.table("premarket_brief").select("brief_date,brief").lt("brief_date", today_s)
                  .order("brief_date", desc=True).limit(days_back).execute().data or [])
    except Exception:  # noqa: BLE001
        return 0
    for b in briefs:
        bd = b["brief_date"]
        picks = [p for p in ((b.get("brief") or {}).get("picks") or []) if not p.get("own") and p.get("bias") in ("偏多", "偏空")]
        if not picks:
            continue
        try:
            prev = deps.prev_trading_day(datetime.strptime(bd, "%Y-%m-%d").date())
            if not prev:
                continue
            syms = [p["symbol"] for p in picks]
            closes = {}
            rows = _pg_all(lambda: sb.table("twse_market_snapshot").select("symbol,trade_date,close_price")
                           .in_("symbol", syms).gte("trade_date", prev.strftime("%Y-%m-%d")).order("trade_date"))
            for r in rows:
                if r.get("close_price"):
                    closes.setdefault(r["symbol"], {})[r["trade_date"]] = float(r["close_price"])
            out = []
            for p in picks:
                cl = closes.get(p["symbol"], {})
                ds = sorted(d for d in cl if d >= bd)
                c0 = cl.get(prev.strftime("%Y-%m-%d"))
                if not c0 or not ds:
                    continue
                row = {"brief_date": bd, "symbol": p["symbol"], "category": (p["events"][0].get("category") if p["events"] else ""),
                       "direction": 1 if p["bias"] == "偏多" else -1, "score": p.get("rank_score"), "close_prev": c0,
                       "ret_1d": round((cl[ds[0]] / c0 - 1) * 100, 2), "updated_at": datetime.utcnow().isoformat()}
                if len(ds) >= 5:
                    row["ret_5d"] = round((cl[ds[4]] / c0 - 1) * 100, 2)
                out.append(row)
            if out:
                sb.table("premarket_outcome").upsert(out, on_conflict="brief_date,symbol").execute()
                n += len(out)
        except Exception as e:  # noqa: BLE001
            print(f"[早盤] 結果追蹤失敗（{bd}）：{type(e).__name__}")
    return n


# ------------------------------------------------------------------ 持久化

def persist_events(sb, events):
    rows = [{"ev_key": e["ev_key"], "ev_date": e["ev_date"], "ev_time": e.get("ev_time"), "market": e["market"], "symbol": e["symbol"],
             "name": e.get("name"), "subject": (e.get("subject") or "")[:200], "clause": e.get("clause"), "category": e.get("category"),
             "direction": e.get("direction"), "importance": e.get("importance"), "event_date": e.get("event_date"),
             "body_excerpt": (e.get("body") or "")[:200]} for e in events if e.get("importance", 0) >= 1]
    n = 0
    for i in range(0, len(rows), 200):
        try:
            sb.table("mops_events").upsert(rows[i:i + 200], on_conflict="ev_key", ignore_duplicates=True).execute()
            n += len(rows[i:i + 200])
        except Exception as e:  # noqa: BLE001
            print(f"[早盤] 寫入 mops_events 失敗：{type(e).__name__}: {str(e)[:100]}")
    return n


def persist_news(sb, items, now):
    rows = [{"url_hash": it["url_hash"], "source": it["source"], "title": it["title"][:300], "url": it["url"][:600],
             "published_at": it.get("published_at"), "symbols": it.get("symbols") or [], "ai_ok": bool(it["ai_ok"])} for it in items]
    n = 0
    for i in range(0, len(rows), 200):
        try:
            sb.table("news_items").upsert(rows[i:i + 200], on_conflict="url_hash", ignore_duplicates=True).execute()
            n += len(rows[i:i + 200])
        except Exception as e:  # noqa: BLE001
            print(f"[早盤] 寫入 news_items 失敗：{type(e).__name__}: {str(e)[:100]}")
    return n


def slim_pick(p):
    q = {k: p.get(k) for k in ("symbol", "name", "bias", "own", "rank_score", "sector", "why", "risk", "tech", "ai_direction")}
    q["events"] = [{"category": e.get("category"), "subject": (e.get("subject") or "")[:120], "ev_time": e.get("ev_time"),
                    "importance": e.get("importance"), "direction": e.get("direction"), "flags": e.get("flags") or [],
                    "ev_date": e.get("ev_date"), "growth_pct": e.get("growth_pct")} for e in p["events"][:4]]
    return q


# ------------------------------------------------------------------ 主流程

def run_brief(sb, deps, force=False, dry=False, wait_until=SEND_WAIT_UNTIL):
    """05:30 早盤情報。回傳 dict（status, n_picks, n_events, messages…）。"""
    now = deps.now()
    today = now.date()
    today_s = today.strftime("%Y-%m-%d")
    out = {"date": today_s, "status": "init"}
    if not force and not dry:
        try:
            ex = sb.table("premarket_brief").select("sent_at,status").eq("brief_date", today_s).limit(1).execute().data or []
            if ex and ex[0].get("sent_at"):
                out["status"] = "already_sent"
                print(f"[早盤] {today_s} 已發送過，略過（要重發請設 PREMARKET_FORCE=1）")
                return out
        except Exception:  # noqa: BLE001
            pass
    prev = deps.prev_trading_day(today)
    if not prev:
        out["status"] = "no_prev_trading_day"
        return out
    prev_s = prev.strftime("%Y-%m-%d")
    session = pm.make_session()

    try:
        n_out = update_outcomes(sb, deps, today_s)
        print(f"[早盤] 結果追蹤更新 {n_out} 列")
    except Exception as e:  # noqa: BLE001
        print(f"[早盤] 結果追蹤失敗：{type(e).__name__}")

    events, mstat = collect_material(session, prev_s, prev_s)
    # 重大訊息檔還沒更新 → 等到 wait_until（證交所約 05:24 才產生新檔）
    while mstat["material_stale"] and wait_until and deps.now().strftime("%H:%M") < wait_until:
        print(f"[早盤] 重大訊息檔尚未更新（最新 {mstat['material_latest']}），{POLL_SECONDS}s 後重抓")
        deps.sleep(POLL_SECONDS)
        events, mstat = collect_material(session, prev_s, prev_s)
    print(f"[早盤] 重大訊息：上市 {mstat['n_twse_rows']} 列／上櫃 {mstat['n_tpex_rows']} 列→事件 {len(events)} 件；"
          f"stale={mstat['material_stale']} 最新={mstat['material_latest']}｜twse={mstat['twse']} tpex={mstat['tpex']}")

    cal, cstat = collect_calendar(session, today_s)
    meetings = load_meetings(sb, today_s, events)
    cal["meetings"] = meetings
    news_items, nstat = pm.fetch_news(session)
    pod = pm.fetch_podcast_latest(session)
    print(f"[早盤] 新聞來源：{nstat}｜行事曆：{cstat}")

    own = load_own(sb, deps)
    liquid = load_liquid(sb, prev_s)
    sector_map = load_sector_map(deps)
    us = fetch_us(deps)
    us_line = pm.us_market_line(us)

    picks = pm.rank_events(events, own_symbols=list(own), liquid=liquid)
    for p in picks:
        if not p.get("name") and own.get(p["symbol"]):
            p["name"] = own[p["symbol"]]
    # 持倉雖然沒事件也想知道（行事曆／處置），這裡只放有事件的；沒事件的持倉走 cal.own_flags
    picks = picks[:25]
    name_to_code = {p["name"]: p["symbol"] for p in picks if p.get("name")}
    name_to_code.update({v: k for k, v in own.items() if v})
    since_dt = news_since(prev, now)
    news_ai, news_links, news_rows = pick_news(news_items, since_dt, [p["symbol"] for p in picks], list(own), name_to_code)

    cands = [p for p in picks if not p["own"]][:14] + [p for p in picks if p["own"]][:6]
    overview, ai_items, ai_st = ai_enrich(deps, cands, [n for n in news_ai[:30]], us_line)
    print(f"[早盤] {ai_st}")
    picks = merge_ai(picks, ai_items)
    picks = attach_context(sb, picks, sector_map)
    cal["own_flags"] = own_flags(own, cal, meetings)

    brief = {
        "date": today_s, "generated_hm": now.strftime("%H:%M"), "us_line": us_line, "regime_line": regime_line(deps, prev_s),
        "ai_overview": overview, "ai_status": ai_st,
        "source_status": dict(mstat, news=nstat, calendar=cstat),
        "picks": picks, "calendar": cal,
        "news_ai": [{"title": n["title"], "source": n["source"], "url": n["url"], "published_at": n.get("published_at"), "symbols": n["symbols"]} for n in news_ai[:15]],
        "news_links": [{"title": n["title"], "source": n["source"], "url": n["url"], "published_at": n.get("published_at"), "symbols": n["symbols"]} for n in news_links[:15]],
        "podcast": pod, "attribution": pm.OPEN_DATA_ATTRIBUTION,
    }
    msgs = pm.format_messages(brief)
    out.update(n_picks=len(picks), n_events=len(events), n_news=len(news_rows), n_msgs=len(msgs), messages=msgs, ai=ai_st,
               stale=mstat["material_stale"])

    if dry:
        out["status"] = "dry"
        return out

    persist_events(sb, events)
    persist_news(sb, news_rows, now)
    saved = {"brief_date": today_s, "generated_at": datetime.utcnow().isoformat(), "status": "draft",
             "brief": dict(brief, picks=[slim_pick(p) for p in picks])}
    try:
        sb.table("premarket_brief").upsert(saved, on_conflict="brief_date").execute()
    except Exception as e:  # noqa: BLE001
        print(f"[早盤] 寫入 premarket_brief 失敗：{type(e).__name__}: {str(e)[:120]}")
    all_ok = True
    for m in msgs:
        all_ok = deps.send(m) and all_ok
    try:
        sb.table("premarket_brief").update({"sent_at": datetime.utcnow().isoformat() if all_ok else None,
                                            "status": "sent" if all_ok else "send_failed"}).eq("brief_date", today_s).execute()
    except Exception:  # noqa: BLE001
        pass
    out["status"] = "sent" if all_ok else "send_failed"
    return out


def run_supplement(sb, deps, dry=False):
    """08:00 補充：重抓重大訊息／新聞，只報『05:30 之後新增』的＋今日行事曆＋持倉提醒。永遠發送一則。"""
    now = deps.now()
    today = now.date()
    today_s = today.strftime("%Y-%m-%d")
    prev = deps.prev_trading_day(today)
    prev_s = prev.strftime("%Y-%m-%d") if prev else today_s
    out = {"date": today_s, "status": "init"}
    session = pm.make_session()
    events, mstat = collect_material(session, prev_s, prev_s)
    news_items, nstat = pm.fetch_news(session)
    own = load_own(sb, deps)
    liquid = load_liquid(sb, prev_s)

    try:
        known = {r["ev_key"] for r in _pg_all(lambda: sb.table("mops_events").select("ev_key").gte("ev_date", prev_s).order("ev_key"))}
    except Exception:  # noqa: BLE001
        known = set()
    try:
        known_news = {r["url_hash"] for r in _pg_all(lambda: sb.table("news_items").select("url_hash").gte("fetched_at", (now - timedelta(days=3)).astimezone(pm.timezone.utc).isoformat()).order("url_hash"))}
    except Exception:  # noqa: BLE001
        known_news = set()
    try:
        has_brief = bool(sb.table("premarket_brief").select("brief_date").eq("brief_date", today_s).limit(1).execute().data)
    except Exception:  # noqa: BLE001
        has_brief = False
    if not has_brief:     # 05:30 那份沒產生 → 全部都算新增
        known, known_news = set(), set()
    new_events = [e for e in events if e["ev_key"] not in known]
    cal, cstat = collect_calendar(session, today_s)
    meetings = load_meetings(sb, today_s, events)
    cal["meetings"] = meetings
    own_f = own_flags(own, cal, meetings)
    name_to_code = {e["name"]: e["symbol"] for e in new_events if e.get("name")}
    name_to_code.update({v: k for k, v in own.items() if v})
    picks = pm.rank_events(new_events, own_symbols=list(own), liquid=liquid)
    picks = [p for p in picks if (not p["own"]) or p["events"]][:12]
    news_ai, news_links, news_rows = pick_news(news_items, news_since(prev or today, now), [p["symbol"] for p in picks], list(own), name_to_code)
    fresh_news = [n for n in news_rows if n["url_hash"] not in known_news]
    fresh_ai = [dict(n, source=n["source_name"]) for n in fresh_news if n["ai_ok"]][:8]
    fresh_lk = [dict(n, source=n["source_name"]) for n in fresh_news if not n["ai_ok"]][:4]
    own_news = [n for n in fresh_news if set(n["symbols"]) & set(own)][:5]

    overview, ai_items, ai_st = ai_enrich(deps, picks, fresh_ai, "")
    picks = merge_ai(picks, ai_items)

    L = [f"🔔 早盤補充 {today_s[5:7]}/{today_s[8:10]} {now.strftime('%H:%M')}"]
    if mstat["material_stale"]:
        L.append(f"⚠️ 證交所重大訊息檔仍停在 {mstat['material_latest'] or '?'}，以下可能不完整。")
    if overview:
        L.append("🧭 " + overview)
    if picks:
        L.append("\n🆕 05:30 之後新增的重大事件")
        for i, p in enumerate(picks, 1):
            L.append(f"{i}. {p.get('name') or p['symbol']}({p['symbol']}) {p['bias']}{'｜你的持倉/雷達' if p['own'] else ''}")
            for e in p["events"][:2]:
                L.append(f"   • {pm.event_line(e)}")
            if p.get("why"):
                L.append(f"   原因：{p['why']}")
            if p.get("risk"):
                L.append(f"   風險：{p['risk']}")
    else:
        L.append("\n（05:30 之後沒有新增達門檻的重大事件）")
    today_meet = [m for m in meetings if m["date"] == today_s]
    today_ex = [x for x in cal.get("exdiv", []) if x["date"] == today_s]
    if today_meet or today_ex:
        L.append("\n📅 今天")
        if today_meet:
            L.append("🎤 法說會：" + "、".join(f"{m['name']}{m['symbol']}" for m in today_meet[:12]))
        if today_ex:
            L.append("💰 除權息：" + "、".join(f"{x['name'] or x['symbol']}" for x in today_ex[:12]))
    if own_f:
        L.append("\n🔔 持倉提醒：" + "、".join(own_f[:10]))
    if own_news:
        L.append("\n📌 與你持倉/雷達相關的新聞")
        L.extend(f"• {n['title']}（{n['source_name']}）" for n in own_news)
    if fresh_ai:
        L.append("\n📰 新增新聞（中央社／鉅亨）")
        L.extend(f"• {n['title']}（{n['source']}）" for n in fresh_ai[:6])
    if fresh_lk:
        L.append("\n🔗 其他媒體標題（僅連結，不經 AI）")
        L.extend(f"• {n['title']}（{n['source']}）{n['url']}" for n in fresh_lk)
    L.append("\nℹ️ 僅供自己研究參考，不是投資建議。完整內容見網站「📰 早盤情報」。")
    msg = "\n".join(L)
    out.update(n_new_events=len(new_events), n_picks=len(picks), n_fresh_news=len(fresh_news), message=msg, ai=ai_st)
    if dry:
        out["status"] = "dry"
        return out
    persist_events(sb, events)
    persist_news(sb, news_rows, now)
    ok = deps.send(msg)
    try:
        sb.table("premarket_brief").upsert({"brief_date": today_s, "supplement": {
            "generated_hm": now.strftime("%H:%M"), "n_new_events": len(new_events), "overview": overview,
            "picks": [slim_pick(p) for p in picks], "own_flags": own_f,
            "news": [{"title": n["title"], "source": n["source_name"], "url": n["url"]} for n in (own_news + fresh_ai[:6])][:10]},
            "supplement_sent_at": datetime.utcnow().isoformat() if ok else None}, on_conflict="brief_date").execute()
    except Exception as e:  # noqa: BLE001
        print(f"[早盤補充] 寫入失敗：{type(e).__name__}: {str(e)[:100]}")
    out["status"] = "sent" if ok else "send_failed"
    return out


def run_news_collect(sb, deps):
    """傍晚／深夜順手收新聞標題（不推播）。回傳寫入筆數。"""
    session = pm.make_session()
    items, st = pm.fetch_news(session)
    seen, rows = set(), []
    for it in items:
        k = pm.news_hash(it["source"], it["url"])
        if k in seen:
            continue
        seen.add(k)
        rows.append(dict(it, url_hash=k, symbols=pm.extract_symbols(it["title"])))
    n = persist_news(sb, rows, deps.now())
    print(f"[新聞蒐集] {st}｜寫入 {n} 筆")
    return n
