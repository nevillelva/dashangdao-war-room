"""【2026-10-13】Telegram 重複推播回歸測試（離線，不連網）。
1) 新聞標題去重：同一則新聞被不同頻道轉載（網址不同、標題只差標點／後綴）只留一則。
2) 持倉段落已列出的新聞，不在「新增新聞」段落重複出現。
3) 濾網回測防重複與看門狗冷卻原子化：靜態檢查程式結構存在。"""
import re
import sys
from datetime import datetime

failures = []


def check(name, cond, detail=""):
    if not cond:
        failures.append(f"{name} {detail}")
        print("FAIL:", name, detail)
    else:
        print("PASS:", name)


def main():
    import premarket_stage as ps

    k1 = ps.title_key("AI伺服器類股今難逃跌勢？美發起337調查 立訊、富士康與廣達等廠商捲入 (鉅亨網台股)")
    k2 = ps.title_key("AI伺服器類股今難逃跌勢？美發起337調查，立訊、富士康與廣達等廠商捲入")
    check("標題正規化：標點與空白差異視為同一則", k1[:30] == k2[:30], f"{k1[:30]} vs {k2[:30]}")

    items = [
        {"source": "cnyes_tw", "url": "https://x/1", "title": "台積電法說會 利多不漲", "published_at": "2026-10-12T05:40:00", "source_name": "鉅亨網", "ai_ok": True},
        {"source": "cnyes_top", "url": "https://x/2", "title": "台積電法說會，利多不漲！", "published_at": "2026-10-12T05:41:00", "source_name": "鉅亨網", "ai_ok": False},
        {"source": "cnyes_top", "url": "https://x/3", "title": "外資撤出三星", "published_at": "2026-10-12T05:42:00", "source_name": "中央社", "ai_ok": True},
    ]
    ai, lk, rows = ps.pick_news(items, datetime(2026, 10, 12, 5, 0), [], [], {})
    titles = [r["title"] for r in rows]
    check("新聞去重：跨頻道同標題只留一則", len(rows) == 2, f"rows={titles}")

    src = open("premarket_stage.py", encoding="utf-8").read()
    check("持倉段落排除：own_news 關鍵字排除存在", "_own_keys" in src and "title_key(n[\"title\"]) not in _own_keys" in src)

    sched = open("system_scheduler.py", encoding="utf-8").read()
    fb = sched[sched.index("def stage_filter_backtest(sb):"):]
    fb = fb[:fb.index("\ndef ", 10)] if "\ndef " in fb[10:] else fb
    check("濾網回測防重複：有完成紀錄略過與認領列", "filter_backtest_claim" in fb and "已有完成紀錄" in fb)

    js = open("warroom_monitor_worker.js", encoding="utf-8").read()
    check("看門狗冷卻原子化：PATCH 條件更新＋ignore-duplicates", "dispatched_at=lt." in js and "resolution=ignore-duplicates" in js)
    check("看門狗把執行中認領列也算已派發", "_claim" in js)

    if failures:
        print(f"\n{len(failures)} 項失敗")
        return 1
    print("\n全部通過")
    return 0


if __name__ == "__main__":
    sys.exit(main())
