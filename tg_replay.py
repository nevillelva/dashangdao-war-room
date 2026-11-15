"""【2026-11-15】Telegram 分流回放（唯讀）：把近 N 天的訊息餵給 tg_policy.classify()，輸出 push／alert／digest 三類則數與疑似誤殺清單。

資料來源（都只讀）：
  ・system_config tg_digest:日期   → 當時被降噪、沒推播的訊息（每則只留前 160 字）
  ・ui_selftest_reports tg_shadow:日期 → 影子記錄（tg_shadow.py）：分類、原因、前 80 字、實際走向（sent／digest／dedup／direct）
  注意：影子記錄上線之前「已送出」的訊息沒有留底，無法回放；回放涵蓋範圍會隨影子記錄累積變長。
疑似誤殺（要人看的清單，不自動改規則）：
  ① digest 但訊息文字含交易關鍵字（降噪原因屬明確降噪詞者除外——那是設計）；
  ② 影子記錄當時實際送出（sent）、現行規則卻會歸 digest（規則漂移）；
  ③ 影子記錄當時被降噪（digest）、現行規則卻會 push（反向漂移，供參考）。
用法：python tg_replay.py [--days 30] [--show] [--input 檔.json] [--save]
  預設只印數量；--show 才印清單（僅限本機看，公開日誌請勿用）；--save 把清單寫私有表（summary=tg_replay）。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import tg_policy as P

TAIPEI_TZ = timezone(timedelta(hours=8))


def replay(items):
    """items：[{m, route(可無), tier(可無), src}]。回傳 {counts, by_src, suspects, drift}。純函式。"""
    counts = {"push": 0, "alert": 0, "digest": 0}
    by_src = {}
    suspects, drift = [], []
    for it in items or []:
        m = str(it.get("m") or "")
        tier, why = P.classify(m)
        counts[tier] += 1
        s = by_src.setdefault(it.get("src", "?"), {"push": 0, "alert": 0, "digest": 0})
        s[tier] += 1
        if tier == "digest" and not why.startswith("降噪："):
            keys = [k for k in P.PUSH_KEYS if k in m]
            if keys:
                suspects.append({"kind": "digest含交易字", "key": keys[0], "why": why, "m": m[:60], "src": it.get("src")})
        route, rec_tier = it.get("route"), it.get("tier")
        if route == "sent" and tier == "digest":
            drift.append({"kind": "當時送出、現行歸digest", "why": why, "m": m[:60]})
        elif route == "digest" and tier == "push":
            drift.append({"kind": "當時降噪、現行歸push", "why": why, "m": m[:60]})
        elif route == "direct" and tier == "digest":
            drift.append({"kind": "直送（未經分流）、現行會歸digest", "why": why, "m": m[:60]})
    return {"n": sum(counts.values()), "counts": counts, "by_src": by_src, "suspects": suspects, "drift": drift}


def _paged(make_q, page=1000):
    out, start = [], 0
    while True:
        rows = make_q().range(start, start + page - 1).execute().data or []
        out += rows
        if len(rows) < page:
            return out
        start += page


def load_from_db(sb, days=30, today=None):
    """讀 tg_digest 與 tg_shadow。回傳 (items, 涵蓋說明 dict)。唯讀。"""
    today = today or datetime.now(TAIPEI_TZ).date()
    items, digest_days, shadow_days = [], 0, set()
    for k in range(days):
        d = (today - timedelta(days=k)).strftime("%Y-%m-%d")
        try:
            rows = sb.table("system_config").select("config_value").eq("config_key", f"tg_digest:{d}").limit(1).execute().data or []
        except Exception:  # noqa: BLE001
            rows = []
        if rows:
            v = rows[0].get("config_value")
            try:
                v = json.loads(v) if isinstance(v, str) else (v or [])
            except Exception:  # noqa: BLE001
                v = []
            if v:
                digest_days += 1
            items += [{"m": r.get("m", ""), "src": "tg_digest", "route": "digest"} for r in v if isinstance(r, dict)]
    since = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        rows = _paged(lambda: sb.table("ui_selftest_reports").select("summary,report").like("summary", "tg_shadow:%")
                      .gte("summary", f"tg_shadow:{since}").order("created_at"))
    except Exception:  # noqa: BLE001
        rows = []
    for r in rows:
        rep = r.get("report") or {}
        if isinstance(rep, dict) and rep.get("m") is not None:
            shadow_days.add(str(r.get("summary"))[10:])
            items.append({"m": rep.get("m", ""), "src": "tg_shadow", "route": rep.get("route"), "tier": rep.get("tier")})
    return items, {"digest_days": digest_days, "shadow_days": len(shadow_days),
                   "note": "影子記錄上線前已送出的訊息沒有留底，無法回放"}


def public_line(res, cov=None):
    c = res["counts"]
    s = (f"回放 {res['n']} 則：push {c['push']}／alert {c['alert']}／digest {c['digest']}；"
         f"疑似誤殺 {len(res['suspects'])} 則、規則漂移 {len(res['drift'])} 則")
    if cov:
        s += f"（tg_digest {cov['digest_days']} 天、影子記錄 {cov['shadow_days']} 天）"
    return s


def save(sb, res, cov, run_id=""):
    sb.table("ui_selftest_reports").delete().eq("summary", "tg_replay").execute()
    sb.table("ui_selftest_reports").insert({"run_id": run_id, "summary": "tg_replay",
                                            "report": {"at": datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M"), "coverage": cov, **res}}).execute()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--input")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args(argv)
    sb = None
    if a.input:
        with open(a.input, encoding="utf-8") as f:
            items, cov = json.load(f), None
    else:
        from supabase import create_client
        url, key = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_KEY", "")
        if not url or not key:
            print("缺少 SUPABASE_URL／SUPABASE_KEY（只驗證存在與否，不印值）；可改用 --input 檔.json")
            return 1
        sb = create_client(url, key)
        items, cov = load_from_db(sb, a.days)
    res = replay(items)
    print(public_line(res, cov))
    if a.show:
        for x in res["suspects"] + res["drift"]:
            print(f"  [{x['kind']}] {x.get('key', '')} {x['why']} ｜ {x['m']}")
    if a.save and sb is not None:
        save(sb, res, cov, os.environ.get("GITHUB_RUN_ID", ""))
        print("清單已寫入私有表 ui_selftest_reports（summary=tg_replay）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
