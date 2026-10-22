"""【2026-10-17 老闆：每天篩出太多股票、無法集中火力 → 交叉比對濃縮】盤前主戰清單 → 10:00 修正。

設計（依目前回測證據，標註樣本數與限制見專案文件《查指令新規則回測與最佳方案_20261017》）：
- 四條軌道，各自只留前幾名，總數 ≤ 14 檔（2026-10-20 加入查7 漲停隔日賣、查8 放寬到 4 檔）：
  1. 波段主力（最多 3 檔，2026-10-19 老闆核可；2026-10-21 起大盤季線下只留 1 檔、建議抱 20 日）：查3∧查2。
     2026-10-18 基本面補齊 300 檔後重跑 5 年（隨機 300 檔、10 日比大盤）：整體 +0.30%（樣本外 +0.52%）；
     加「查6 營收雙增」+0.76%（樣本外 +1.49%），沒有查6 −0.06% → 查6 優先；每天只挑 1 檔樣本外 +1.50%、3 檔 +0.82%、5 檔 +0.66%。
     排序＝沒有⚠️追高警示 → 有查6 → 沒有減分標籤（價值分≥70）→ 價值分低者優先。
  2. 短線動能（最多 3 檔）：主力偵測「週轉率雙訊號（2 個型態以上）∧ 營收年增>20%」，且前晚評分沒有落在空方（>−6）。
     近 40 天實盤 5 日超額 +2.2%（t 4.1）；5 年回測代理版（量比≥2＋創20日新高＋營收年增>20%）只在成交值前 150 大熱門股有效
     （+1.02%、樣本外 +1.56%），隨機母體為負（−1.01%）→ 只當短線觀察、限熱門大型股。排序＝創 20 日新高、法人連買、週轉率。
  3. 空方觀察（最多 2 檔）：查8（昨日強勢＋今天續漲＝隔日沖倒貨），大盤多頭才列，量比高優先；隔天開盤比昨收高≥3% 且 <7% 才當沖空（2026-10-20 由 1% 提高：1~3% 回測無優勢；2026-10-21 加上限 7%：開高≥7% 離漲停太近、62% 收漲停回補不了）。
- 盤中修正：多方不再用 10:00 剔除，改建議 12:30～13:20 下單（2026-10-21 盤中時點全網格回測）；空方看已驗證的空方第三關
  （10:00 收盤＜開盤；候選上 t≈±2.4）。不合格剔除、資料不足保留但標註。
純函式、可離線測試。"""
from __future__ import annotations

import re

# 2026-10-17 深度回測做多主力標籤，三個母體（兩個隨機母體 150／300 檔、成交值前 150 檔）都同方向的只有一個：
# 「價值分≥70」反而較差（隨機 300 檔：有 +0.87%／沒有 +3.28%；其他兩個母體同方向）。其餘標籤（外資買超、KD金叉、貼MA20、
# 大盤多空、創新高…）在不同母體方向相反，不採用。→ 波段主力排序只用「價值分 60~69 優先」，其餘視為可互換。
DEFAULT_GOOD_TAGS = ("c6v",)   # 2026-10-19：查6 營收雙增優先（5 年 300 檔：有 +0.76%／無 −0.06%，樣本外 +1.49%）
DEFAULT_BAD_TAGS = ("vs70",)
TAG_FROM_CMD = {"查1.": "c1", "查9.": "c9", "查6.": "c6v", "查4.": "c4v"}
N_SWING, N_MOM, N_SHORT, N_OD = 3, 3, 4, 4   # 2026-10-20 老闆：查7／查8 也接進主戰清單（查8 由 2 檔放寬到 4 檔、查7 最多 4 檔）


def _pfx(c):
    m = re.match(r"\s*(查\s*\d+)\s*\.?", str(c))
    return (m.group(1).replace(" ", "") + ".") if m else None


def card_tags(matched, card):
    """把隔夜掃描的命中指令＋卡片摘要轉成 cmd_deep_bt.FOCUS_TAGS 同名的標籤集合。"""
    card = card or {}
    t = {TAG_FROM_CMD[p] for p in (_pfx(c) for c in (matched or [])) if p in TAG_FROM_CMD}
    vs = card.get("value_score")
    if vs is not None and float(vs) >= 70:
        t.add("vs70")
    if card.get("hi20_break"):
        t.add("hi20")
    if (card.get("f_buy") or 0) > 0:
        t.add("fb3")
    if (card.get("t_buy") or 0) > 0:
        t.add("tb")
    n = card.get("near_ma20_pct")
    if n is not None and 0 <= float(n) <= 2:
        t.add("near2")
    if "金叉" in str(card.get("kdj_str") or ""):
        t.add("kgold")
    if card.get("is_red") or (card.get("gain") or 0) > 0:
        t.add("red")
    return t


TAG_LABEL = {"vs70": "價值分≥70", "fb3": "外資近日買超", "near2": "貼MA20 0~2%", "hi20": "創20日新高", "tb": "投信買超",
             "kgold": "KD金叉", "red": "收紅K", "c6v": "營收雙增", "c4v": "投信連買", "c1": "查1", "c9": "查9"}


SWING_HOLD_NOTE = "建議抱約 20 個交易日（5 年全上市：抱 10 日平均≈0、抱 20 日 +0.63%；大盤季線上 +1.61%）"
SWING_BEAR_NOTE = "⚠️大盤在季線下：只留 1 檔、小部位（5 年全上市 1,089 檔：季線下抱 20 日平均 −1.60%、勝率 39%，樣本內外都為負）"


def rank_swing(rows, good_tags=None, n=N_SWING, bad_tags=None, bull=None):
    """rows：overnight_scan_results 列（symbol, matched_commands, score, card_snapshot）。只取查3∧查2。
    排序：沒有⚠️追高警示優先 → 分數（有加分標籤 +1、沒有減分標籤 +1）→ 價值分（減分清單含價值分≥70 時，低的優先）。
    【2026-10-21】5 年全上市指令專屬回測（cmd_playbook_bt）：做多主力抱 20 日 +0.63%（10 日≈0）；加權在季線上 +1.61%（n=7,031，樣本內 +1.91／外 +1.34）、
    季線下 −1.60%（n=3,078，−1.47／−1.94）→ 季線下只留第 1 名並標小部位；理由欄附建議持有天數。"""
    good = set(DEFAULT_GOOD_TAGS if good_tags is None else good_tags) - {"c1", "c9"}   # 查1/查9 是追高警示，不當加分
    bad = set(DEFAULT_BAD_TAGS if bad_tags is None else bad_tags)
    out = []
    for r in rows or []:
        ps = {_pfx(c) for c in (r.get("matched_commands") or [])}
        if not ("查2." in ps and "查3." in ps):
            continue
        card = r.get("card_snapshot") or {}
        tags = card_tags(r.get("matched_commands"), card)
        warn = bool(ps & {"查1.", "查9.", "查8."})
        hit = sorted(tags & good)
        miss_bad = sorted(bad - tags)
        has_bad = sorted(tags & bad)
        vs = float(card.get("value_score") or 0)
        why = "查3∧查2"
        if hit:
            why += "｜加分：" + "、".join(TAG_LABEL.get(t, t) for t in hit)
        if has_bad:
            why += "｜減分：" + "、".join(TAG_LABEL.get(t, t) for t in has_bad)
        if warn:
            why += "｜⚠️追高警示"
        why += "｜" + (SWING_BEAR_NOTE if bull is False else SWING_HOLD_NOTE)
        out.append({"symbol": r["symbol"], "track": "波段主力", "direction": "long", "cmds": list(r.get("matched_commands") or []),
                    "rank": (0 if warn else 1, len(hit), len(miss_bad), -vs if "vs70" in bad else vs), "why": why})
    out.sort(key=lambda x: x["rank"], reverse=True)
    return out[:(1 if bull is False else n)]


def rank_momentum(smart_rows, score_map=None, exclude=(), n=N_MOM, bull=None):
    """smart_money_candidates 列：patterns, rev_yoy, broke_20d_high, foreign_streak, trust_streak, turnover_pct。
    大盤在季線下不列：5 年回測（隨機 300 檔）動能型（量比≥2＋創20日新高）空頭時 10 日比大盤 −0.63%（t −2.7）。"""
    if bull is False:
        return []
    score_map = score_map or {}
    out = []
    for r in smart_rows or []:
        sym = r.get("symbol")
        if not sym or sym in exclude:
            continue
        np_ = len(r.get("patterns") or [])
        yoy = r.get("rev_yoy")
        sc = score_map.get(sym)
        if np_ < 2 or yoy is None or float(yoy) <= 20 or (sc is not None and float(sc) <= -6):
            continue
        inst = (r.get("foreign_streak") or 0) >= 3 or (r.get("trust_streak") or 0) >= 3
        b20 = bool(r.get("broke_20d_high"))
        out.append({"symbol": sym, "track": "短線動能", "direction": "long",
                    "rank": (int(b20) + int(inst), float(r.get("turnover_pct") or 0)),
                    "why": f"主力偵測{np_}訊號＋營收年增{float(yoy):.0f}%" + ("｜創20日新高" if b20 else "") + ("｜法人連買" if inst else "")})
    out.sort(key=lambda x: x["rank"], reverse=True)
    return out[:n]


def rank_short(rows, n=N_SHORT, exclude=(), bull=None, bull20=None):
    """查8（隔日沖倒貨）→ 隔天開盤比昨收高 3～7% 才當沖空。排序＝量比高優先。
    【2026-10-21 改】大盤濾網反過來：5 年全上市 1,089 檔（cmd_playbook_bt，算進收漲停補不了）——加權在 20 日線「下」平均 +0.87%、
    勝率 63%（n=793，樣本內 +1.18／外 +0.68），在 20 日線「上」−0.20%（n=1,780）；60 日線下 +0.58%／線上 −0.02%。
    與 c8_mkt_bt（成交值前 800 檔）同方向。原本「空頭不列」是 300 檔小樣本的結論，大樣本兩次都不成立 → 改成「加權在 20 日線上不列」。"""
    if bull20 is True:
        return []
    out = []
    for r in rows or []:
        ps = {_pfx(c) for c in (r.get("matched_commands") or [])}
        if "查8." not in ps or r.get("symbol") in exclude:
            continue
        card = r.get("card_snapshot") or {}
        vr = float(card.get("vol_ratio") or 0)
        out.append({"symbol": r["symbol"], "track": "空方觀察", "direction": "short", "rank": (vr,),
                    "cmds": list(r.get("matched_commands") or []), "ref_close": card.get("price") or r.get("price"),
                    "why": f"查8隔日沖倒貨（量比{vr:.1f}）｜開盤比昨收高 3～7% 才當沖空（≥7% 離漲停太近、回補不了，不做），收盤前回補"
                           + ("｜也中查7：已持有→開盤賣" if "查7." in ps else "")})
    out.sort(key=lambda x: x["rank"], reverse=True)
    return out[:n]


def rank_short_dt(dt_flags, n=N_SHORT, exclude=(), bull=None):
    """【2026-10-19】昨日漲停＋隔日沖分點（自建名單）買超佔成交量 ≥20%，今天開盤比昨收高>1% 才當沖空。
    5 年回測（隨機 300 檔＋追蹤池，6,579 個漲停事件，名單只用 2024/3 前資料建立、2024/3 後驗證）：樣本外 n=290、
    當沖空扣成本 +0.80%/筆、t 3.08（沒有分點條件的漲停股只有 +0.37%、t 1.66）；大盤多頭 +0.86%（n=236）。
    大盤空頭樣本小（n=54）且不顯著 → 與查8 一致，大盤在季線下不列。dt_flags：[{symbol, pct, ref_close}]。"""
    if bull is False:
        return []
    out = []
    for r in sorted(dt_flags or [], key=lambda x: -float(x.get("pct") or 0)):
        if r.get("symbol") in exclude or float(r.get("pct") or 0) < 20:
            continue
        out.append({"symbol": r["symbol"], "track": "空方觀察", "direction": "short", "ref_close": r.get("ref_close"),
                    "cmds": [], "why": f"昨日漲停＋隔日沖分點買超{float(r['pct']):.0f}%｜開盤比昨收高 1～7% 才當沖空，收盤前回補（5年樣本外：帳面 +0.60%，算進收漲停補不了的損失約 −0.15%、n=190 → 沒有明確優勢，小部位或只觀察）"})
    return out[:n]


def rank_overnight(rows, n=N_OD, exclude=()):
    """【2026-10-20 老闆：查7 也要接進主戰清單】查7（漲停收盤＋前日上升趨勢＋大盤多頭）。名單在漲停當天收盤後才出來，
    收盤買不到 → 這裡的用途是：已持有的今天 09:00 開盤賣（5 年回測 +2.07%/筆、勝率 70%）；沒持有不追買（隔天開盤追買為負）。
    同時中查8 且開盤比昨收高>1% → 可另做一筆開盤當沖空。排序＝同時中查8 優先、量比高優先。"""
    out = []
    for r in rows or []:
        ps = {_pfx(c) for c in (r.get("matched_commands") or [])}
        if "查7." not in ps or r.get("symbol") in exclude:
            continue
        card = r.get("card_snapshot") or {}
        vr = float(card.get("vol_ratio") or 0)
        both = "查8." in ps
        out.append({"symbol": r["symbol"], "track": "漲停隔日賣", "direction": "exit", "rank": (int(both), vr),
                    "cmds": list(r.get("matched_commands") or []), "ref_close": card.get("price") or r.get("price"),
                    "why": "查7 昨日漲停收盤｜已持有→今天 09:00 開盤賣、不追買" + ("｜同時中查8：開盤高>1% 可另做當沖空" if both else "")})
    out.sort(key=lambda x: x["rank"], reverse=True)
    return out[:n]


def build_preopen(scan_rows, smart_rows, score_map=None, good_tags=None, bad_tags=None, bull=None, dt_flags=None, bull20=None):
    """bull：大盤是否在季線（MA60）之上；bull20：是否在 20 日線之上。波段主力多空都有效不受影響；
    短線動能空頭不列；查8 當沖空改成「加權在 20 日線上不列」（2026-10-21 大樣本回測，見 rank_short）。"""
    sw = rank_swing(scan_rows, good_tags, bad_tags=bad_tags, bull=bull)
    # 【2026-10-19】曾依 300 檔結果改「空頭只留第 1 名、半倉」，擴充到 600 檔後不成立（空頭持有 20 日前 3 檔 +0.48%、比大盤 +1.46%、
    # t 3.6；前 1 檔 +1.08%）→ 還原：大盤多空都照常列 3 檔（多頭 +2.34% 較強，空頭報酬較小但仍為正）。
    mo = rank_momentum(smart_rows, score_map, exclude={x["symbol"] for x in sw}, bull=bull)
    ex = {x["symbol"] for x in sw + mo}
    sd = rank_short_dt(dt_flags, exclude=ex, bull=bull)
    sh = sd + rank_short(scan_rows, n=max(0, N_SHORT - len(sd)), exclude=ex | {x["symbol"] for x in sd}, bull=bull, bull20=bull20)
    od = rank_overnight(scan_rows, exclude=ex | {x["symbol"] for x in sh})
    lst = sw + mo + sh + od
    cmap = {r.get("symbol"): list(r.get("matched_commands") or []) for r in scan_rows or []}
    for x in lst:
        x.pop("rank", None)
        x.setdefault("cmds", cmap.get(x["symbol"], []))
    return lst


def long_entry_hint(bars5, prev_close=None):
    """多方 10:00 進場提示（2026-10-21 定版：10:00 前不破開盤 0.5% 且 10:00 收在開盤上）。
    5 年盤中回測（intraday_hist_bt id=591，168 檔、覆蓋 87%）：合格 n=16,739 隔日超額 +0.10%（t 2.4，樣本內 +0.10／外 +0.11），
    不合格 −0.03%；效果小。【2026-10-21 改】盤中時點全網格回測（intraday_timing_bt id=605）以「波段持有天數」看，守住開盤對查3／查6 反而較差、
    做多主力樣本內外不一致 → 降為「參考」，不再剔除多方（見 revise_at_10）。開盤 5 分K 突破（orb5）+0.21%（n=3,752）略好但樣本少。
    bars5：當天 5 分K list[dict(bar_time 起始時間, open, high, low, close)]。回傳 verdict pass/fail/unknown 與說明。"""
    bs = sorted([b for b in (bars5 or []) if str(b.get("bar_time", "")) < "10:00"], key=lambda b: b["bar_time"])
    if len(bs) < 10 or bs[0].get("bar_time") != "09:00":
        return {"verdict": "unknown", "label": "10:00 前 5 分K 不足，無法判斷"}
    o = float(bs[0]["open"])
    lo = min(float(b["low"]) for b in bs)
    c = float(bs[-1]["close"])
    if lo >= o * 0.995 and c > o:
        return {"verdict": "pass", "label": f"10:00 前守住開盤（最低 {lo:g}≥開盤 {o:g}×0.995）且收在開盤上"}
    return {"verdict": "fail", "label": ("10:00 前跌破開盤" if lo < o * 0.995 else "10:00 收在開盤下") + "（對波段沒有分辨力，不剔除）"}


# 【2026-10-20】查8 當沖空的開高門檻：5 年回測（成交值前 500 檔，conflict_bt）開高 1~3% 平均 −0.06%（無優勢）、≥3% 平均 +0.73%（IS +1.28／OOS +0.63）。
# 分點≥20% 的漲停股（cmds 為空）另有自己的 5 年回測，維持 1%。
SHORT_MIN_GAP_C8 = 3.0
# 【2026-10-21 老闆：大盤大漲、個股近漲停時做當沖空有問題】c8_mkt_bt（成交值前 800 檔、5 年、n=6,538）：開高≥7%（離漲停≤3%）有 62% 收在漲停、
# 回補不了；把回不了的單留到隔天開盤回補，平均 −1.31%（樣本外 −1.71%、t −3.8），帳面（假設收盤價能補）雖 +1.5% 但不實際 → 不做。
SHORT_MAX_GAP = 7.0
# 盤中（c8_intraday_mkt，140 檔、5 年、n=459）：10:00 時個股離漲停≤1% → 83% 鎖死到收盤、平均 −5.8%、勝率 8%（IS −7.0／OOS −5.5、n=107）。
NEAR_LIMIT_UP_PCT = 1.0
SHORT_RISK_NOTE = ("回測勝率約五成、帳面平均約 +0.7%/筆（假設收盤價補得到），約 5% 機率虧>6%；"
                   "收在漲停就補不了（實際期望接近 0）；停損 3% 會把優勢吃光，所以小部位、不硬凹")


# 【2026-10-21 盤中進場時點全網格 5 年回測（intraday_timing_bt id=605，168 檔）】
# 多方：各指令都是「越晚買越好」，12:30～13:20 進入平台區——比開盤買平均多賺 做多主力 +0.17～0.20、查6 +0.18、查3 +0.15、查2 +0.14、
# 動能 +0.32 個百分點（樣本內外都為正、t 3～5）；10:00「守住開盤」對波段沒有分辨力（查3／查6 反而變差）→ 不再用它剔除多方。
# 【2026-10-22 多時點組合回測 id=686】做多改成「條件過了才在 13:20 買」：09:30 價≥開盤+0.5%＋12:30 價≥當日均價（各指令另有細則，見 tail_check.py）。
# 查3 勝率 60%／66%（樣本內／外）、查6 50%／57%、查4 52%／54%；13:20 系統自動確認並推播。
LONG_ENTRY_NOTE = "13:20 依多時點規則確認才買：09:30 ≥ 開盤 +0.5%＋12:30 ≥ 當日均價（系統 13:20 推播結果；回測查3 勝率 60～66%、查6 50～57%）"
# 查7 已持有者隔天賣點（n=2,828 筆漲停鎖死收盤）：開高≥5% 開盤賣最好（+7.2%）；開高 0～5% 09:05～09:15 略好（+1.63% vs 開盤 +1.39%）；
# 開低（<昨收）開盤賣 −2.95%、撐到收盤 −1.50%（樣本內外一致）→ 開低不要開盤殺。
def exit_plan(gap):
    if gap >= 5:
        return "開高≥5%：09:00 開盤直接賣（5 年：平均 +7.2%、勝率 100%）"
    if gap >= 0:
        return "開高 0～5%：09:05～09:15 賣（5 年：+1.63%，比開盤賣 +1.39% 略好）"
    return "開低：不要開盤殺，撐到收盤前再賣（5 年：收盤賣 −1.50%，開盤賣 −2.95%，樣本內外一致）"


def levels_from_daily(df, before=None):
    """【2026-10-22 老闆：主戰區要有戰卡最有用的價位】用日K（未還原）最後一根（訊號日＝上一交易日）算與戰卡同義的價位：
    ref＝收盤、ma5＝近 5 日收盤均、atr＝近 14 日真實波幅平均；戰卡防守線＝MA5−0.5×ATR、短線上緣＝收盤＋1×ATR。
    資料不足（<20 根）或有缺值回 None。before＝今天日期字串：當天（盤中未完成）那根不算。純函式。"""
    try:
        if before is not None and len(df):
            df = df[[str(i)[:10] < before for i in df.index]]
        h, l, c = (df[k].astype(float).to_numpy() for k in ("High", "Low", "Close"))
    except Exception:  # noqa: BLE001
        return None
    n = len(c)
    if n < 20 or not (c[-1] > 0) or any(x != x for x in (h[-15:].tolist() + l[-15:].tolist() + c[-15:].tolist())):
        return None
    tr = [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(n - 14, n)]
    atr = sum(tr) / 14
    ma5 = float(c[-5:].mean())
    ref = float(c[-1])
    return {"ref": round(ref, 2), "ma5": round(ma5, 2), "atr": round(atr, 2), "def": round(ma5 - 0.5 * atr, 2), "atk": round(ref + atr, 2),
            "date": str(df.index[-1])[:10] if hasattr(df, "index") and len(df.index) else None}


# 停損／進場上限（2026-10-22 5 年回測 cmd_playbook_bt id=650，成交值不限、1,089 檔：做多主力 n=10,109、動能 n=35,594；觸價停損、跳空以開盤價出）：
# ‧做多主力抱 20 日：不設停損 +0.63%（樣本外 +0.75）；−10% 停損 +0.57%（外 +0.50）、最差 5% 由 −13.5% 縮到 −10.6%；−5% 太緊（外 +0.20）；
#   戰卡防守線（MA5−0.5ATR）當停損 99.7% 會被洗出、勝率 17%、平均 −0.35% → 不可當波段停損。→ 只掛 −10% 當「災難停損」（控尾端風險，不是增加報酬）。
# ‧短線動能抱 5 日：全母體本來就是負的（−0.79%，只對熱門前 150 名有效，熱門子集沒有單獨驗證停損）；−5% 停損 −0.76%、最差 5% −11.3%→−5.6%，被停損 38% → 掛 −5%。
# ‧進場上限：做多主力「開盤已超出戰卡上緣（昨收＋1ATR）」n=148 反而 +2.6%、開高 1～2% +1.9%（n=908），沒有追高變差的證據 → 不設上限；
#   動能開盤比昨收高 0～1% −0.73%、2～3% −1.16%、3～5% −1.42%（樣本內外同向）→ 高於昨收 2% 以上不追。
LONG_STOP_PCT = {"波段主力": 10.0, "短線動能": 5.0}       # 進場價下跌 X% 停損（觸價）
LONG_CHASE_PCT = {"波段主力": None, "短線動能": 2.0}       # 價格高於昨收 X% 以上不追（None＝不設）
SHORT_COVER_PCT = 9.0                                     # 空方回補線＝昨收×(1+9%)＝離漲停 1%（c8_intraday_mkt：10:00 離漲停≤1% 83% 鎖死）


def price_lines(x, open_px=None):
    """主戰區「價位與動作」：回傳 [(標籤, 文字)]。價位由昨收／ATR／MA5 算出；沒有昨收（lv 缺）就不給。
    做多：進場價（12:30～13:20 市價，動能高於昨收 2% 不追）＋回測定版的停損；戰卡防守線只當短線參考（不是波段停損）。
    做空：開盤進場帶、必補價位、漲停價；持有者賣（查7）：三種開盤情境的價位。"""
    lv = x.get("lv") or {}
    ref = lv.get("ref") or x.get("ref_close")
    out = []
    d = x.get("direction")
    if not ref:
        return out
    ref = float(ref)
    if d == "long":
        cap = LONG_CHASE_PCT.get(x.get("track"))
        if cap:
            out.append(("進場價", f"13:20 確認後市價；高於 {ref * (1 + cap / 100):.2f}（昨收 {ref:g} +{cap:g}%）不追"))
        else:
            out.append(("進場價", f"13:20 確認後市價（09:30≥開盤+0.5%＋12:30≥均價；昨收 {ref:g}）"))
        sp = LONG_STOP_PCT.get(x.get("track"))
        if sp:
            out.append(("停損", f"進場價 −{sp:g}%（約 {ref * (1 - sp / 100):.2f}，觸價出；災難停損，控制最壞情況）"))
        if lv.get("def"):
            out.append(("戰卡防守線", f"{lv['def']:g}（短線參考；波段不能拿來停損，會被洗出）"))
    elif d == "short":
        need = SHORT_MIN_GAP_C8 if x.get("cmds") else 1.0
        out.append(("開盤進場帶", f"{ref * (1 + need / 100):.2f}～{ref * (1 + SHORT_MAX_GAP / 100):.2f}（昨收 {ref:g} 的 +{need:g}%～+{SHORT_MAX_GAP:g}%；高於上緣不做）"))
        out.append(("回補", f"{ref * (1 + SHORT_COVER_PCT / 100):.2f} 前必補（離漲停 1%）；平時收盤前補｜漲停 {ref * 1.1:.2f}"))
    elif d == "exit":
        out.append(("賣點", f"開高≥5%（≥{ref * 1.05:.2f}）開盤賣；0～5% 09:05～09:15 賣；開低（<{ref:g}）撐到收盤前｜漲停 {ref * 1.1:.2f}"))
    return out


def limit_up_dist_pct(ref_close, px):
    """距漲停（占昨收 %）。漲停價以昨收×1.1 近似（未計跳動單位）。"""
    return (float(ref_close) * 1.1 - float(px)) / float(ref_close) * 100


def revise_at_10(preopen, gate_rows=None, long_hints=None, open_px=None, last_px=None):
    """preopen：build_preopen 結果；gate_rows：intraday_gate_results 當天列（symbol, direction, gate3_verdict, overall_verdict）；
    long_hints：{symbol: long_entry_hint 結果}。回傳 keep / drop 兩個 list（每筆附 reason）。"""
    g = {(r.get("symbol"), r.get("direction")): r for r in (gate_rows or [])}
    long_hints = long_hints or {}
    keep, drop = [], []
    for x in preopen or []:
        x = dict(x)
        if x["direction"] == "short" and x.get("ref_close"):
            # 查8：開盤判斷（不是 10:00）。開盤比昨收高 ≥3% → 開盤當沖空；1~3% 回測無優勢，剔除
            o = (open_px or {}).get(x["symbol"])
            need = SHORT_MIN_GAP_C8 if x.get("cmds") else 1.0
            if o is None:
                x["reason"] = f"還沒有開盤價（09:00 開盤後自動顯示；保留、人工確認：開盤比昨收高≥{need:g}% 才空）"
                x["brief"] = f"09:00 後看開盤：比昨收高≥{need:g}%、<{SHORT_MAX_GAP:g}% 才空"
                keep.append(x)
            elif o >= float(x["ref_close"]) * (1 + SHORT_MAX_GAP / 100):
                x["reason"] = (f"開盤 {o:g} 已比昨收 {float(x['ref_close']):g} 高 {(o / float(x['ref_close']) - 1) * 100:.1f}%（≥{SHORT_MAX_GAP:g}%、離漲停只剩 "
                               f"{limit_up_dist_pct(x['ref_close'], o):.1f}%）：回測有 62% 收在漲停、回補不了，不做")
                drop.append(x)
            elif o >= float(x["ref_close"]) * (1 + need / 100):
                x["reason"] = (f"開盤 {o:g} 比昨收 {float(x['ref_close']):g} 高 {(o / float(x['ref_close']) - 1) * 100:.1f}%：開盤當沖空、收盤前回補"
                               f"｜風險：{SHORT_RISK_NOTE}")
                x["brief"] = "開盤當沖空、收盤前回補"
                lp = (last_px or {}).get(x["symbol"])
                if lp:
                    dist = limit_up_dist_pct(x["ref_close"], lp)
                    if dist <= NEAR_LIMIT_UP_PCT:
                        x["reason"] = (f"⚠️ 目前 {float(lp):g} 離漲停只剩 {max(dist, 0):.1f}%：回測此時 83% 鎖死到收盤、平均 −5.8%、勝率僅 8%（n=107）；"
                                       f"已鎖死就補不了，持有者請處理、沒空的不要進｜" + x["reason"])
                        x["brief"] = f"⚠️現價離漲停僅 {max(dist, 0):.1f}%：沒空的不要進，持有者處理"
                keep.append(x)
            else:
                x["reason"] = f"開盤只比昨收高 {(o / float(x['ref_close']) - 1) * 100:+.1f}%（未達 {need:g}%），回測此時當沖空無優勢，不做"
                drop.append(x)
        elif x["direction"] == "exit":
            # 查7：不看 10:00，開盤就賣；同時中查8 且開高>1% 可另做當沖空
            o = (open_px or {}).get(x["symbol"])
            both = any(_pfx(c) == "查8." for c in (x.get("cmds") or []))
            if o is None or not x.get("ref_close"):
                x["reason"] = "已持有→看開盤：開高≥5% 開盤賣、開高 0～5% 09:05～09:15 賣、開低撐到收盤前賣；沒持有不追買"
                x["brief"] = "持有者：看開盤分三種賣法；沒持有不追買"
            else:
                gap = (o / float(x["ref_close"]) - 1) * 100
                x["reason"] = f"已持有（開盤 {o:g}，比昨收 {gap:+.1f}%）→ {exit_plan(gap)}；沒持有不追買"
                x["brief"] = f"持有者（開盤 {gap:+.1f}%）：" + ("開盤直接賣" if gap >= 5 else ("09:05～09:15 賣" if gap >= 0 else "撐到收盤前賣，不開盤殺"))
                if both and gap >= SHORT_MIN_GAP_C8:
                    x["reason"] += "｜同時中查8且開高≥3%：可另做開盤當沖空、13:20 前回補"
            keep.append(x)
        elif x["direction"] == "short":
            r = g.get((x["symbol"], "short")) or {}
            v = r.get("gate3_verdict")
            if v == "fail" or r.get("overall_verdict") == "fail":
                x["reason"] = "空方第三關不合格（10:00 已收復開盤，反彈有力）" if v == "fail" else (r.get("overall_label") or "空方三關不合格")
                drop.append(x)
            else:
                x["reason"] = "空方第三關合格（10:00 收盤＜開盤）" if v == "pass" else "空方第三關資料不足（保留、人工確認）"
                x["brief"] = "空方第三關合格" if v == "pass" else "空方第三關資料不足，人工確認"
                keep.append(x)
        else:
            # 【2026-10-21】多方不再用 10:00 守住開盤剔除（對波段沒有分辨力），改給最佳下單時段；10:00 狀態只當參考
            h = long_hints.get(x["symbol"]) or {"verdict": "unknown", "label": "10:00 前資料不足"}
            x["reason"] = f"{LONG_ENTRY_NOTE}｜參考：{h['label']}"
            x["brief"] = "13:20 確認才買：09:30≥開盤+0.5%＋12:30≥均價"
            keep.append(x)
    return {"keep": keep, "drop": drop}
