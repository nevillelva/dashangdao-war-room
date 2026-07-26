"""
warroom_core.py — 作戰室共用核心模組（R39 新增）

【為什麼要有這個檔案】
這個專案從一開始就是「網頁版 warroom_v160.py」跟「排程版 system_scheduler.py」
兩份獨立程式碼，各自維護一套訊號計算/常數/抓價邏輯。這個結構性問題已經造成
至少3次真實事故：

  1. round 24：system_scheduler.py 的 FINMIND_TOKEN 直接當變數用、從沒被賦值，
     stage_signal 執行到最後一步就 crash——這個 bug 從最初就存在，直到某次
     手動觸發認真看log才被抓到。
  2. round 36：排程自己的 stage_health 健康檢查邏輯，跟網頁版是兩套獨立實作，
     round 24 修好網頁版後排程那份完全沒跟著改，導致 Telegram 永久誤報。
  3. system_scheduler.py 的 compute_signal_for() 一直是「精簡版訊號計算」——
     只有均線/爆量/ATR，完全沒有籌碼/基本面/大盤位階這些網頁版早就有的因子，
     而且 ATR 算法是簡化版（只看當日高低，沒有計入跳空缺口的真實波動）。

這個檔案的目的：把「純計算」（不需要 Streamlit 快取、不需要 DB 連線）的核心
邏輯集中在這裡，網頁版與排程版都從這裡 import，同一套邏輯只維護一份。

【設計鐵律】這個檔案絕對不能 import streamlit——排程在 GitHub Actions 上跑，
沒有 Streamlit runtime，import 下去會直接炸掉。任何需要 @st.cache_data 的
函式（例如抓價的快取包裝）留在 warroom_v160.py，只有「用已經拿到的資料做
計算」這一段搬進來這裡。

【R39 這輪的範圍，刻意保守】只搬移「已經是獨立、乾淨的純函式」的部分：
常數、determine_signal、三戰區評分、ATR/停損停利計算、MIS即時報價抓取。
沒有搬 calculate_signals_worker 整個函式本身（它有 347 行，深度耦合
Streamlit 快取的抓價呼叫，直接動它風險太高）——那個函式留在網頁版，
但改成呼叫這裡的 determine_signal/score_zone1-3，不再自己定義一份。

排程端目前的評分（compute_signal_for）本輪「不」直接切換成完整版
determine_signal——因為那需要排程額外抓籌碼/基本面資料，是更大的改動，
規劃在 R41（屆時本來就要幫排程加上新因子所需的資料抓取）。這輪排程端
只換掉兩個地方：(1) ATR 改用這裡的正確版本（原本簡化版會低估跳空日的
波動）；(2) 防守線倍數改讀這裡的 DEF_LINE_ATR_MULT，不再各自寫死 0.5，
確保這個數字以後不會再兩邊不同步。
"""
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import pandas as pd


# ==============================================================================
# 一、共用 HTTP session（跟 fetch_twse_mis_batch 一起搬過來，兩邊共用同一組
#     重試設定，不再各自建一份可能設定不一致的 session）
# ==============================================================================
GOV_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*"
}


def get_safe_session():
    session = requests.Session()
    session.headers.update(GOV_HEADERS)
    retry = Retry(
        total=3, backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    return session


_SESSION = get_safe_session()


# ==============================================================================
# 二、核心常數（單一來源，網頁版與排程版都從這裡讀，不再各自寫死）
# ==============================================================================
# 防守線 = MA5 - 此倍數×ATR。V158起具名常數，V160-R39起兩邊共用同一個值，
# 不再各自寫死可能漂移。這個數字總指揮官已經確認維持 0.5（規格書曾建議1.5，
# 已明確否決，見交接文件「教訓」章節）。
DEF_LINE_ATR_MULT = 0.5

# 大盤破20MA時的防守線縮緊倍數（R38規劃：0.5→0.35，等比例對應規格書原本
# 建議的1.5→1.0的縮緊比例），R43三層風控引擎會用到，先在這裡定義好。
DEF_LINE_ATR_MULT_TIGHTENED = 0.35

# 常見券商分點清單（籌碼校正/隔日沖標記共用），下拉選單用
COMMON_BROKER_BRANCHES = [
    "凱基-台北", "凱基-信義", "凱基-松山", "元大-台北", "元大-桃園",
    "富邦-新店", "富邦-建成", "國泰-敦南", "國泰-中和",
    "群益金鼎-三重", "永豐金-建成", "永豐金-中山",
    "統一-嘉義", "統一-南屯", "新光", "國票-敦北",
    "花旗環球", "港商麥格理", "摩根士丹利", "美林", "瑞銀",
    "香港上海匯豐", "台灣摩根大通", "美商高盛",
]


# ==============================================================================
# 三、技術指標純計算（不需要任何快取，不牽涉外部連線）
# ==============================================================================
def calculate_atr(df, period=14):
    """
    真實波動幅度 (True Range 版本)——同時考慮當日高低差、跳空缺口。
    這是網頁版原本就在用的正確算法；排程版舊版只用 (high-low).mean()，
    漏掉跳空缺口的波動，會系統性低估有跳空的股票的ATR。R39起排程改用這版。
    """
    high, low = df['High'], df['Low']
    prev_close = df['Close'].shift(1)
    true_range = pd.concat([high - low,
                            (high - prev_close).abs(),
                            (low - prev_close).abs()], axis=1).max(axis=1)
    atr = true_range.rolling(window=period).mean()
    if atr.empty:
        return 0.0
    last_val = atr.iloc[-1]
    return float(last_val) if pd.notna(last_val) else 0.0


def build_trade_zones(current_price, ma5, ma20, atr, hist=None, def_line_mult=None):
    """
    防守線/短線停利點/移動停利計算。

    def_line_mult 預設 None 時用模組常數 DEF_LINE_ATR_MULT——外部呼叫端
    （例如R43的大盤位階風控）可以傳入 DEF_LINE_ATR_MULT_TIGHTENED 來縮緊。
    """
    mult = def_line_mult if def_line_mult is not None else DEF_LINE_ATR_MULT
    def_line = round(ma5 - atr * mult, 2)
    atk_zone = round(current_price + atr, 2)
    buffer_pct = ((current_price - def_line) / current_price) * 100 if current_price > 0 else 0

    trail_stop, bb_upper, high_20 = 0.0, 0.0, 0.0
    if hist is not None and len(hist) >= 20:
        high_20 = float(hist['High'].tail(20).max())
        trail_stop = round(high_20 - 1.5 * atr, 2)
        std20 = float(hist['Close'].tail(20).std())
        bb_upper = round(ma20 + 2.0 * std20, 2)

    trail_active = bool(trail_stop > 0 and current_price > trail_stop)

    return {'atk_zone': atk_zone, 'def_line': def_line, 'buffer_pct': round(buffer_pct, 2),
            'atr': round(atr, 2), 'trail_stop': trail_stop, 'trail_active': trail_active,
            'bb_upper': bb_upper, 'high_20': round(high_20, 2)}


# ==============================================================================
# 四、核心評分邏輯（多因子共振評分引擎的現況版本，R40起會改成因子註冊表架構）
# ==============================================================================
def determine_signal(current_price, ma5, ma20, foreign_buy, vol_ratio, is_open_high_close_low,
                     buffer_pct, gain=0.0, enable_doomsday=False,
                     market_bull=True, landmine=False, is_volume_dump=False):
    score = 0
    reasons = []
    if current_price > ma5 > ma20:
        score += 2; reasons.append("站穩多頭")
    elif current_price > ma5:
        score += 1; reasons.append("站上5MA")
    elif current_price < ma5:
        score -= 2; reasons.append("跌破5MA")

    if foreign_buy > 0:
        score += 1; reasons.append(f"外買{foreign_buy:,.0f}")
    elif foreign_buy < 0:
        score -= 1; reasons.append(f"外賣{abs(foreign_buy):,.0f}")

    if vol_ratio < 0.6:
        score -= 1; reasons.append("量縮力竭")
    elif vol_ratio > 2.0:
        score += 1; reasons.append("爆量")

    if is_open_high_close_low:
        score -= 2; reasons.append("開高走低轉弱")
    if buffer_pct < 1.0:
        score -= 1; reasons.append(f"緩衝僅{buffer_pct:.1f}%")

    if landmine:
        score -= 2; reasons.append("💀 基本面地雷")

    # 【任務二】大盤位階風控濾網：大盤失守 20MA → 多方訊號強制降級
    if not market_bull:
        if score >= 3:
            score = 2; reasons.append("🌧️ 大盤破20MA·降級")
        elif score >= 1:
            score = score - 1; reasons.append("🌧️ 大盤破20MA·降級")

    # 爆量下殺強制撤退（比照末日熔斷的「一票否決」設計）：
    # 爆量比>=2.0 且 當日收黑下殺，典型是主力出貨，不管技術分數多高，直接壓成偏空防守。
    if is_volume_dump:
        score = min(score, -3); reasons.append("🚨 爆量下殺·主力出貨")

    if enable_doomsday and (gain <= -7.0 or buffer_pct < 0):
        score = min(score, -3); reasons.append("💀 末日熔斷觸發")

    if score >= 3:   return "🔥 偏多攻擊", "#ff4d4d", score, reasons
    elif score >= 1: return "🟡 觀察偏多", "#ffab00", score, reasons
    elif score <= -3: return "🔵 偏空防守", "#2979ff", score, reasons
    elif score <= -1: return "⚠️ 轉弱謹慎", "#ff9100", score, reasons
    else:            return "⚖️ 中立震盪", "#888", score, reasons


def score_zone1_fundamental(c, fin_health=None):
    """
    第一戰區（基本面）小結論。只看「這家公司值不值得這個價格」——估值位階、
    獲利能力、成長性、股利。刻意不看外資買賣、不看均線位置，那些分別是
    第三、第二戰區的事，這樣三個戰區才能各自誠實表態、也才可能互相矛盾。

    直接複用已經算好的 value_score（已移除其中的外資因子，是純基本面分數）。
    """
    vs = c.get('value_score')
    if vs is None:
        return "❓ 資料不足", "#888", "缺少估值/財報資料，無法評估"

    bits = []
    pe_pct = c.get('pe_percentile')
    if pe_pct is not None:
        if pe_pct <= 20:
            bits.append(f"估值在歷史最便宜兩成({pe_pct:.0f}%)")
        elif pe_pct >= 80:
            bits.append(f"估值在歷史最貴兩成({pe_pct:.0f}%)")
        else:
            bits.append(f"估值居中({pe_pct:.0f}%)")
    _yoy = c.get('rev_yoy')
    if _yoy is not None:
        bits.append(f"營收年增{float(_yoy):+.1f}%")
    _dy = float(c.get('div_yield', 0) or 0)
    if _dy >= 3.0:
        bits.append(f"殖利率{_dy:.1f}%")
    if c.get('landmine'):
        bits.append("⚠️地雷警訊")

    score = vs
    if fin_health:
        _roe = fin_health.get('roe')
        if _roe is not None:
            if _roe >= 15:
                score += 8; bits.append(f"ROE{_roe:.1f}%")
            elif _roe < 0:
                score -= 8; bits.append(f"ROE{_roe:.1f}%(虧損)")
        _gm = fin_health.get('gross_margin')
        if _gm is not None and _gm >= 30:
            score += 5; bits.append(f"毛利率{_gm:.1f}%")
        if fin_health.get('cash_quality_note', '').startswith('🔴'):
            score -= 10; bits.append("⚠️現金流與獲利不一致")
        score = int(max(0, min(100, score)))

    reason = "、".join(bits) if bits else "資料有限"
    if score >= 65:
        return "🟢 偏多", "#00c853", f"體質偏好（{score}分）｜{reason}"
    if score >= 45:
        return "🟡 中性", "#ffab00", f"體質中性（{score}分）｜{reason}"
    return "🔴 偏空", "#ff4d4d", f"體質偏弱（{score}分）｜{reason}"


def score_zone2_technical(c):
    """
    第二戰區（技術面）小結論。只看價格結構本身：均線排列、MACD動能、
    RSI位階、乖離率、週線趨勢。刻意不看基本面、不看法人買賣。
    """
    price = float(c.get('price', 0) or 0)
    ma5 = float(c.get('ma5', 0) or 0)
    ma20 = float(c.get('ma20', 0) or 0)
    if price <= 0 or ma5 <= 0:
        return "❓ 資料不足", "#888", "缺少價格/均線資料，無法評估"

    s, bits = 0, []
    if price > ma5 > ma20 > 0:
        s += 2; bits.append("多頭排列")
    elif price < ma5:
        s -= 2; bits.append("跌破5MA")
        if ma20 > 0 and price < ma20:
            s -= 1; bits.append("亦破20MA")
    else:
        bits.append("均線糾結")

    macd_s = str(c.get('macd_str', ''))
    if '多方' in macd_s:
        s += 1; bits.append("MACD多方")
    elif '空方' in macd_s:
        s -= 1; bits.append("MACD空方")

    rsi = c.get('rsi_val')
    if rsi is not None:
        rsi = float(rsi)
        if rsi > 70:
            s -= 1; bits.append(f"RSI{rsi:.0f}過熱")
        elif rsi < 30:
            s += 1; bits.append(f"RSI{rsi:.0f}超賣")

    bias = c.get('bias_val')
    if bias is not None:
        bias = float(bias)
        if bias > 8:
            s -= 1; bits.append(f"乖離{bias:+.1f}%偏高")
        elif bias < -8:
            s += 1; bits.append(f"乖離{bias:+.1f}%超跌")

    wk = (c.get('weekly') or {}).get('trend')
    if wk == 'bull':
        s += 1; bits.append("週線偏多")
    elif wk == 'bear':
        s -= 1; bits.append("週線偏空")

    reason = "、".join(bits) if bits else "無明顯訊號"
    if s >= 2:
        return "🟢 偏多", "#00c853", f"結構偏多（{s:+d}）｜{reason}"
    if s <= -2:
        return "🔴 偏空", "#ff4d4d", f"結構偏空（{s:+d}）｜{reason}"
    return "🟡 中性", "#ffab00", f"方向不明（{s:+d}）｜{reason}"


def score_zone3_chips(c):
    """
    第三戰區（籌碼面）小結論。只看「誰在買、誰在賣、成本在哪」：外資/投信
    多天期買賣超、法人成本乖離、融資增減。外資因子從第一戰區移到這裡歸位。
    """
    f5 = float(c.get('f_5d', 0) or 0)
    f10 = float(c.get('f_10d', 0) or 0)
    t5 = float(c.get('t_5d', 0) or 0)
    has_any = any(c.get(k) is not None for k in ('f_5d', 'f_10d', 't_5d'))
    if not has_any:
        return "❓ 資料不足", "#888", "缺少法人籌碼資料，無法評估"

    s, bits = 0, []
    if f5 > 0:
        s += 1; bits.append(f"外資5日買超{f5:,.0f}張")
    elif f5 < 0:
        s -= 1; bits.append(f"外資5日賣超{abs(f5):,.0f}張")
    if f10 > 0 and f5 > 0:
        s += 1; bits.append("10日同向續買")
    elif f10 < 0 and f5 < 0:
        s -= 1; bits.append("10日同向續賣")

    if t5 > 0:
        s += 1; bits.append(f"投信5日買超{t5:,.0f}張")
    elif t5 < 0:
        s -= 1; bits.append(f"投信5日賣超{abs(t5):,.0f}張")

    fv = c.get('f_vwap') or {}
    _price_now = float(c.get('price', 0) or 0)
    if isinstance(fv, dict) and float(fv.get('vwap', 0) or 0) > 0 and _price_now > 0:
        dev = (_price_now - float(fv['vwap'])) / float(fv['vwap']) * 100
        if dev < 0:
            s += 1; bits.append(f"現價低於外資成本{abs(dev):.1f}%")
        elif dev > 15:
            s -= 1; bits.append(f"高於外資成本{dev:.1f}%（獲利了結壓力）")

    md = float(c.get('margin_diff', 0) or 0)
    if c.get('has_margin') and md > 0:
        s -= 1 if md > 500 else 0
        if md > 500:
            bits.append(f"融資增{md:,.0f}張（籌碼轉亂）")

    reason = "、".join(bits) if bits else "法人動作平淡"
    if s >= 2:
        return "🟢 偏多", "#00c853", f"籌碼偏多（{s:+d}）｜{reason}"
    if s <= -2:
        return "🔴 偏空", "#ff4d4d", f"籌碼偏空（{s:+d}）｜{reason}"
    return "🟡 中性", "#ffab00", f"籌碼中性（{s:+d}）｜{reason}"


def _fmt_zone_summary(badge, color, reason):
    """把戰區小結論渲染成一行 HTML（三區共用同一種視覺語言）。"""
    return (f'<div style="font-size:12px; margin-top:8px; padding-top:6px; '
            f'border-top:1px solid {color}44;">'
            f'<b style="color:{color};">{badge}</b> '
            f'<span style="color:#aaa;">{reason}</span></div>')


# ==============================================================================
# 五、證交所 MIS 即時報價（round38新增，抓取純函式，不含快取包裝）
# ==============================================================================
def _safe_mis_float(v):
    """MIS端點的數字欄位常常是"-"（無資料），安全轉float，失敗回None不編造。"""
    if v is None or v == "-":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def fetch_twse_mis_batch(symbol_ex_pairs):
    """
    用證交所「基本市況報導」即時報價端點抓真正的盤中即時價，解決round31-37
    一路在追的問題本質：FinMind/yfinance/證交所MI_INDEX全部都是「收盤後才
    更新」的資料源，換幾次都不會有真正即時性。這個端點不一樣——盤中約每5秒
    更新一次，是台股開發圈長年在用、多個獨立來源交叉驗證過的路徑。

    【重要】這不是證交所正式公開文件的API，是社群長期反查瀏覽器網路請求
    整理出來的（雖然穩定使用多年）。代表證交所理論上可以不預警就改版，
    這是換取真正即時性必須接受的取捨。

    symbol_ex_pairs: [(股票代號, 'tse'或'otc'), ...]。加權指數用[('t00','tse')]。
    回傳 {symbol: {price, prev_close, change_pt, change_pct, high, low, open,
                   time, date, ok}}，查不到的股票不會出現在結果裡。
    """
    if not symbol_ex_pairs:
        return {}
    results = {}
    BATCH = 100
    for i in range(0, len(symbol_ex_pairs), BATCH):
        chunk = symbol_ex_pairs[i:i + BATCH]
        ex_ch = "|".join(f"{ex}_{sym}.tw" for sym, ex in chunk)
        try:
            resp = _SESSION.get("https://mis.twse.com.tw/stock/api/getStockInfo.jsp",
                                params={"ex_ch": ex_ch, "json": "1", "delay": "0"}, timeout=6)
            data = resp.json()
            if data.get("rtcode") != "0000":
                continue
            for item in data.get("msgArray", []):
                sym = str(item.get("c", "")).strip()
                if not sym:
                    continue
                _price = None
                for _key in ("z", "o", "y"):
                    _v = item.get(_key, "-")
                    if _v and _v != "-":
                        try:
                            _price = float(_v)
                            break
                        except (ValueError, TypeError):
                            continue
                if _price is None:
                    continue
                try:
                    prev_close = float(item.get("y", "-")) if item.get("y", "-") != "-" else None
                except (ValueError, TypeError):
                    prev_close = None
                change_pt = round(_price - prev_close, 2) if prev_close else None
                change_pct = round((change_pt / prev_close) * 100, 2) if (change_pt is not None and prev_close) else None
                results[sym] = {
                    "price": _price, "prev_close": prev_close,
                    "change_pt": change_pt, "change_pct": change_pct,
                    "high": _safe_mis_float(item.get("h")), "low": _safe_mis_float(item.get("l")),
                    "open": _safe_mis_float(item.get("o")),
                    "time": item.get("t", ""), "date": item.get("d", ""),
                    "ok": True,
                }
        except Exception as e:
            print(f"[即時報價] 批次抓取失敗：{e}")
            continue
    return results
