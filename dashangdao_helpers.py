"""
dashangdao_helpers.py
──────────────────────
【R98續110新增，深層系統檢視：dashangdao.py拆檔第一階段】

這裡收錄的是從dashangdao.py搬出來的「純函式」——不呼叫st.*、不碰
session_state、不依賴dashangdao.py自己專屬的全域變數（如SUPABASE_CONN/
SQLITE_CONN這類連線物件）、也不呼叫任何還留在dashangdao.py裡的其他函式。

篩選方法：不是憑肉眼判斷，是用Python直譯器編譯後的`__code__.co_names`
（這是Python自己分辨「區域變數 vs 全域變數」的權威依據，比自己手刻的
AST掃描可靠——手刻的方法在這次搬移前實際踩過兩次坑：一次漏抓
try/except區塊裡的全域賦值，一次把函式內部的區域變數誤判成全域依賴）。
每一個搬過來的函式都逐一驗證：①零全域變數依賴 ②零呼叫其他dashangdao
專屬函式 ③零st.*/session_state引用，三個條件同時成立才搬。

這只是拆檔的第一階段（39個函式，約1,080行），dashangdao.py主體的UI渲染
流程（st.markdown/st.button照頁面順序執行的部分）完全沒有動，那部分
風險太高，這次不處理。
"""
import json
import os
import tempfile
import pandas as pd
import yfinance as yf

from warroom_core import (
    DEF_LINE_ATR_MULT, _SESSION, fetch_market_turnover_ranking_with_value,
    get_threshold,
)


def fetch_market_turnover_ranking():
    """
    【R97改為薄包裝，本體已搬進warroom_core.py的
    fetch_market_turnover_ranking_with_value()】保留這個函式名稱與原本
    回傳格式（純代碼清單），既有呼叫端不用改。
    """
    return [c for c, _val, _ex in fetch_market_turnover_ranking_with_value()]


def _style_pnl_columns(df, cols):
    """
    【V160 新增】損益/報酬%欄位上色：紅=正（賺）、綠=負（賠），符合台股「紅漲綠跌」慣例。
    總指揮官回報：目前這些數字都沒有顏色，要一個個讀數字判斷正負很難一眼掃過去。

    用 pandas Styler 上色；若環境缺 matplotlib（Styler某些功能依賴它）導致失敗，
    優雅退回不上色的原始表格，不讓這個裝飾性功能搞掛整個績效表的顯示。
    """
    def _color(val):
        try:
            v = float(val)
        except (TypeError, ValueError):
            return ''
        if v > 0:
            return 'color: #ff4d4d; font-weight: bold;'
        if v < 0:
            return 'color: #00c853; font-weight: bold;'
        return ''
    try:
        _valid = [c for c in cols if c in df.columns]
        # 【V160 修復】Styler 會取消 Streamlit 原本的自動數字格式化，導致
        # 100.0 被顯示成 100.000000（總指揮官回報「數字太長佔版面」）。
        # 這裡明確指定四捨五入到小數點後2位。用 na_rep 避免空值顯示成 nan。
        return (df.style
                  .map(_color, subset=_valid)
                  .format(precision=2, na_rep="—", thousands=","))
    except Exception:
        try:
            # 舊版 pandas 用 applymap（新版才有 map），兩個都試一次
            return (df.style
                      .applymap(_color, subset=[c for c in cols if c in df.columns])
                      .format(precision=2, na_rep="—", thousands=","))
        except Exception:
            return df


def _ensure_schema(conn):
    conn.execute('''
        CREATE TABLE IF NOT EXISTS inst_holding (
            date TEXT, symbol TEXT,
            foreign_buy REAL, trust_buy REAL, dealer_buy REAL,
            margin REAL, big_holder REAL, big_holder_date TEXT,
            PRIMARY KEY (date, symbol)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS big_holder_history (
            code TEXT, date TEXT, percent REAL,
            PRIMARY KEY (code, date)
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_inst_symbol ON inst_holding(symbol, date DESC)')

    # 【V158 新增】命中率回測持久化：一次 run 對應多筆訊號明細，結果永久保存，
    # 不用每次重開網頁就砍掉重測，也能拿不同 ATR 倍數的歷史 run 互相比較。
    conn.execute('''
        CREATE TABLE IF NOT EXISTS backtest_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_time TEXT, stock_list TEXT, years INTEGER,
            atr_multiplier REAL, enable_doomsday INTEGER, use_market_regime INTEGER,
            sample_count INTEGER, mode TEXT DEFAULT 'technical'
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS backtest_signals (
            run_id INTEGER, stock TEXT, date TEXT, signal TEXT,
            future_3d_ret REAL, future_10d_ret REAL, is_breached INTEGER, filter_name TEXT
        )
    ''')
    # 【V159】舊版 V158 建出來的 DB 沒有 mode / filter_name 欄位，CREATE TABLE IF NOT EXISTS
    # 不會幫已存在的表補欄位，這裡用 ALTER TABLE 做遷移安全升級；欄位已存在時會丟例外，忽略即可。
    for alter_sql in ("ALTER TABLE backtest_runs ADD COLUMN mode TEXT DEFAULT 'technical'",
                      "ALTER TABLE backtest_signals ADD COLUMN filter_name TEXT"):
        try:
            conn.execute(alter_sql)
        except Exception:
            pass
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bt_run ON backtest_signals(run_id)')
    conn.commit()


def build_card_text_report(c):
    """
    【V160 B#12】把整張戰卡轉成純文字報告，供一鍵複製貼到外部AI分析。
    包含三大戰區所有關鍵數據。
    """
    lines = []
    lines.append(f"【{c.get('name')} ({c.get('code')}) 戰情快照】")
    lines.append(f"現價 {c.get('price')} | 漲跌 {c.get('gain')}% | 決策判定 {c.get('signal_text')}（評分{c.get('score')}）")
    lines.append("")
    lines.append("[第一戰區 基本財報估價]")
    lines.append(f"營收年增 {c.get('rev_yoy')}% ({c.get('rev_month')}) | 月增 {c.get('rev_mom')}%")
    lines.append(f"PE {c.get('pe')}（歷史百分位 {c.get('pe_percentile')}%）| EPS {c.get('eps')}")
    lines.append(f"便宜價 {c.get('cheap_price')} | 合理價 {c.get('fair_price')} | 樂觀價 {c.get('dream_price')} | 殖利率防守價 {c.get('def_price')}")
    lines.append(f"殖利率 {c.get('div_yield')}% | 綜合價值分數 {c.get('value_score')} | 地雷 {'是' if c.get('landmine') else '否'}")
    lines.append("")
    lines.append("[第二戰區 技術防守]")
    lines.append(f"5MA {c.get('ma5')} | 20MA {c.get('ma20')} | 60MA {c.get('ma60')}")
    lines.append(f"MACD {c.get('macd_str')} | RSI {c.get('rsi_val')} | 乖離率 {c.get('bias_val')}%")
    lines.append(f"短線停利點 {c.get('atk_zone')} | 防守停損 {c.get('def_line')}（緩衝 {c.get('buffer_pct')}%）| ATR {c.get('atr_val')}")
    lines.append(f"動態移動停利 {c.get('trail_stop')} | 布林上軌 {c.get('bb_upper')} | 爆量比 {c.get('vol_ratio')}")
    lines.append("")
    lines.append("[第三戰區 三大法人籌碼]")
    lines.append(f"外資 單日 {c.get('f_buy')}張 | 5日 {c.get('f_5d')}張 | 10日 {c.get('f_10d')}張")
    lines.append(f"投信 單日 {c.get('t_buy')}張 | 5日 {c.get('t_5d')}張 | 10日 {c.get('t_10d')}張")
    lines.append(f"自營商 {c.get('d_buy')}張 | 融資增減 {c.get('margin_diff')}張 | 千張大戶 {c.get('big_holder')}%")
    lines.append("")
    lines.append("請以台灣股市操盤幕僚身分，針對以上數據做多空分析與明日進出場建議。")
    return "\n".join(lines)


def compute_trail_stop(side, entry, peak, atr, mult=2.0, activate_mult=1.0):
    """
    【V160 延伸4】計算移動停利線。回傳 (停利線, 是否已啟動)。

    設計重點（刻意寫清楚，讓判斷邏輯可被檢視）：
      1. peak = 進場後的「最高價」（做多）或「最低價」（做空），是單調的——
         只會往有利方向更新，不會退回去。這是移動停利的核心語意，
         跟戰卡上那個「近20日最高-1.5ATR」不同（那是滾動窗，不綁進場點）。
      2. 只有在獲利超過 activate_mult × ATR 之後才啟動，否則一進場就掛一條
         很近的停損線，等於把正常波動當成出場訊號，會被洗掉。
      3. 未啟動時回傳 (0, False)，呼叫端就沿用原本的固定防守線，不會變成沒有停損。
    """
    if atr <= 0 or entry <= 0 or peak <= 0:
        return 0.0, False
    if side == 'long':
        # 獲利幅度不足 → 還不啟動
        if peak - entry < activate_mult * atr:
            return 0.0, False
        return round(peak - mult * atr, 2), True
    else:  # short：peak 存的是進場後最低價
        if entry - peak < activate_mult * atr:
            return 0.0, False
        return round(peak + mult * atr, 2), True


def safe_json_write(filepath, data):
    dir_name = os.path.dirname(os.path.abspath(filepath)) or "."
    with tempfile.NamedTemporaryFile('w', dir=dir_name, delete=False, suffix='.tmp', encoding='utf-8') as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=4)
        tmp_path = tmp.name
    os.replace(tmp_path, filepath)


def _clean_symbol(raw):
    """
    【R95續23新增】股票代號清洗——跟system_scheduler.py的_clean_symbol是
    同一個問題的兩邊各一份修復（兩個檔案不共用模組，scheduler獨立在
    GitHub Actions跑）。這次總指揮官的log截圖顯示網頁版本身也在對
    "$5304"這種帶$前綴的代號打yfinance，每個代號連續失敗4次（.TW/.TWO
    各重試一次）——這代表雲端存的portfolio/pinned_stocks資料本身就帶著
    $前綴髒污，網頁版直接原樣載入使用，從未清洗過。這是續6只修了排程端
    症狀、沒發現網頁版有同一個病根的地方，見下面_clean_symbol_keyed_dict
    在hydrate_state_from_cloud()套用的地方。
    """
    s = str(raw).strip()
    if s.startswith('$'):
        s = s[1:].strip()
    return s


def calc_real_profit(cost, price, qty=1):
    if cost <= 0 or price <= 0:
        return 0, 0
    buy_val = cost * qty * 1000
    sell_val = price * qty * 1000
    profit = (sell_val - buy_val
              - max(20, int(buy_val * 0.001425))
              - max(20, int(sell_val * 0.001425))
              - int(sell_val * 0.003))
    return profit, (profit / buy_val) * 100 if buy_val > 0 else 0


def calc_real_profit_v2(entry_price, current_price, qty=1, side='long'):
    """
    【V160 新增：觀察區轉持倉做空支援】方向感知的損益計算，取代原本
    只支援做多的 calc_real_profit（該函式保留不變，向下相容——凡是沒傳
    side的舊呼叫端行為完全不受影響）。

    台灣證交稅（賣出時課徵0.3%）的課稅時機依方向而不同：
      做多：買進(entry)不課稅，賣出(exit)才課稅——稅算在 exit_val 上。
      做空：放空賣出(entry，你是先賣)才課稅，回補買進(exit)不課稅——
            稅算在 entry_val 上。這不是隨便選的，是台灣證券交易稅的
            實際課稅規則（賣出動作本身觸發課稅，不分是「多單出場賣」
            還是「空單進場放空賣」，都是賣出動作）。

    手續費（買賣雙邊各0.1425%，最低20元）維持雙邊都收，這點多空一致。

    回傳 (損益金額, 報酬率%)，報酬率以進場成本(entry_val)為分母，
    多空兩邊定義一致，可以直接放在同一張表格裡比較。
    """
    if entry_price <= 0 or current_price <= 0:
        return 0, 0
    entry_val = entry_price * qty * 1000
    exit_val = current_price * qty * 1000
    fee_entry = max(20, int(entry_val * 0.001425))
    fee_exit = max(20, int(exit_val * 0.001425))
    if side == 'short':
        tax = int(entry_val * 0.003)
        profit = entry_val - exit_val - fee_entry - fee_exit - tax
    else:
        tax = int(exit_val * 0.003)
        profit = exit_val - entry_val - fee_entry - fee_exit - tax
    roi = (profit / entry_val * 100) if entry_val > 0 else 0
    return profit, roi


def build_short_trade_zones(current_price, ma5, atr, hist=None):
    """
    【V160 新增：觀察區轉持倉做空支援】做空持倉的防守線／移動停利計算，
    做多版本(build_trade_zones，在warroom_core.py)的鏡像對照。

    做空短線防守線 = MA5 + DEF_LINE_ATR_MULT×ATR（0.5倍，總指揮官確認沿用
    跟做多同一個倍數，不採用規格書原本建議的1.5倍——這個決定在R39就確認過
    一次，這裡延續同一個決定，不重新引入分歧）。現價「站上」這條線代表
    走勢轉強、做空該停損。

    做空移動停利 = 20日最低價 + 1.5×ATR——這裡刻意採用「跟現有做多版本
    完全相同的參數」(20日窗口、1.5倍ATR)，但方向鏡像：做多版本是
    「20日最高價 − 1.5×ATR」(停利線在現價之下、隨價格上漲往上移動、
    保護多單獲利)；做空要保護的是「價格下跌」的獲利，所以停利線必須
    在現價之上、隨價格下跌往下移動——對應公式是「20日最低價 + 1.5×ATR」，
    不是把做多公式原封不動照抄（那樣方向會反過來，變成停利線在現價下方，
    對做空毫無意義）。這個鏡像關係已經在回覆總指揮官時說明過。

    回傳跟 build_trade_zones 對稱的欄位名，方便UI共用同一套顯示邏輯。
    """
    def_line = round(ma5 + DEF_LINE_ATR_MULT * atr, 2)
    atk_zone = round(current_price - atr, 2)   # 做空的「進攻延伸區」對稱地往下

    trail_stop, low_20 = 0.0, 0.0
    if hist is not None and len(hist) >= 20:
        low_20 = float(hist['Low'].tail(20).min())
        trail_stop = round(low_20 + 1.5 * atr, 2)

    # 做空的移動停利只有在「現價仍低於停利線」時才是有效的持股保護
    trail_active = bool(trail_stop > 0 and current_price < trail_stop)

    return {'atk_zone': atk_zone, 'def_line': def_line, 'atr': round(atr, 2),
            'trail_stop': trail_stop, 'trail_active': trail_active, 'low_20': round(low_20, 2)}


def calc_volume_change(today_vol_lots, yesterday_vol_lots):
    vol_diff = today_vol_lots - yesterday_vol_lots
    vol_pct = ((vol_diff / yesterday_vol_lots) * 100) if yesterday_vol_lots else 0.0
    if vol_diff > 0:
        label, icon = f"量增 +{vol_diff:,.0f}張", "🔥"
    elif vol_diff < 0:
        label, icon = f"量縮 {vol_diff:,.0f}張", "🧊"
    else:
        label, icon = "量平", "➖"
    return f"{icon} {label} | {vol_pct:+.1f}%"


def _roc_date_to_display(date_str):
    """
    【V160 新增】把日期字串轉成好讀的西元日期。同時處理兩種來源格式：
      - TWSE 預告表：民國年 YYYMMDD（例：'1150729' = 2026-07-29）
      - FinMind 股利政策表：西元 ISO 格式（例：'2026-07-29'，本身已經可讀，原樣回傳）
    格式不對就照原樣回傳，不猜。
    """
    s = str(date_str).strip()
    if len(s) == 10 and s[4] == '-' and s[7] == '-':   # 已經是西元 ISO 格式
        return s
    if len(s) == 7 and s.isdigit():
        roc_y, m, d = int(s[:3]), int(s[3:5]), int(s[5:7])
        return f"{roc_y + 1911}-{m:02d}-{d:02d}"
    if len(s) == 8 and s.isdigit():   # 保險：萬一哪天格式改回西元年
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def parse_broker_csv(raw_bytes):
    """
    【V160 新增：單檔分點CSV拖曳區「隔日沖照妖鏡」】解析證交所買賣日報表查詢
    系統（bsr.twse.com.tw/bshtm/）下載的CSV——這個格式是總指揮官提供2303.csv
    範例檔驗證過的：Big5編碼、每行「兩筆記錄並排」(序號,券商,價格,買進股數,
    賣出股數,,序號,券商,價格,買進股數,賣出股數)，不是單純一列一筆的標準CSV。

    正確性驗證原理：買賣日報表裡「總買進股數必定等於總賣出股數」（每一筆成交
    都有一個買方一個賣方）——解析完後這兩個數字若相等，代表解析完整、
    沒有漏行也沒有重複計算。

    回傳 DataFrame[券商, 買進股數, 賣出股數]（單一券商在同一份報表可能出現在
    多個價位，這裡先回傳明細，彙總留給呼叫端依需求處理），或 None（解析失敗，
    例如檔案不是這個格式）。
    """
    try:
        text = raw_bytes.decode('big5', errors='ignore')
    except Exception:
        return None
    lines = text.split('\n')
    if len(lines) < 4:
        return None
    recs = []
    for ln in lines[3:]:   # 前3行是標題列
        parts = [p.strip() for p in ln.split(',')]
        if len(parts) < 5:
            continue
        for blk in (parts[0:5], parts[6:11] if len(parts) >= 11 else []):
            if len(blk) < 5 or not blk[1]:
                continue
            try:
                recs.append({'券商': blk[1], '買進股數': int(blk[3] or 0), '賣出股數': int(blk[4] or 0)})
            except (ValueError, IndexError):
                continue
    if not recs:
        return None
    return pd.DataFrame(recs)


def _sort_key(code):
    try:
        return (0, int(code))   # 純數字代碼優先，按數值排序
    except ValueError:
        return (1, code)


def _yf_ticker(sym):
    """新版 yfinance 對 requests.Session 有相容性問題，做雙軌降級。"""
    try:
        return yf.Ticker(sym, session=_SESSION)
    except Exception:
        return yf.Ticker(sym)


def evaluate_overnight_gate(macro, market_bull=True):
    """
    【V160 R43 更新】開盤前總經閘門——跟排程端 system_scheduler.py 的
    classify_gate_mode 改用同一套三態設計（多頭順風/對沖模式/恐慌熔斷），
    取代原本的binary正常/暫緩。這裡是網頁版HUD的純顯示用途，不直接下單
    （真正的下單決策在排程那邊），但用同一套判斷邏輯、同一組門檻，避免
    使用者在網頁上看到「隔夜平穩」，但排程那邊其實已經進入對沖或熔斷模式
    的認知落差。

    回傳 (status, reason)，status: 'bull' / 'hedge' / 'panic'（配合舊有
    呼叫端預期的2元組格式，只是status的可能值從2種變成3種）。
    """
    if not macro:
        return 'bull', '無隔夜資料，預設多頭順風'

    sox = macro.get('費城半導體', {})
    tsm = macro.get('台積電ADR', {})
    sox_pct = sox.get('pct') if sox.get('ok') else None
    tsm_pct = tsm.get('pct') if tsm.get('ok') else None

    if (sox_pct is not None and sox_pct <= -2.0) or (tsm_pct is not None and tsm_pct <= -2.5):
        _sox_disp = f"{sox_pct:+.1f}%" if sox_pct is not None else "無資料"
        _tsm_disp = f"{tsm_pct:+.1f}%" if tsm_pct is not None else "無資料"
        return 'panic', f"🚨 恐慌熔斷：費半{_sox_disp}／台積電ADR{_tsm_disp}"
    elif sox_pct is not None and -1.9 <= sox_pct <= -0.5 and not market_bull:
        return 'hedge', f"🟡 對沖模式：費半{sox_pct:+.1f}%且大盤破20MA"
    else:
        return 'bull', '🟢 多頭順風：隔夜平穩或上漲'


def apply_timeframe_resonance(verdict, score, weekly):
    """
    【V160 延伸3】用週線趨勢調整日線結論，回傳 (調整後verdict, 說明字串或None)。

    調整規則（刻意保守，只降級不升級）：
      - 日線看多但週線走空 → 降級（這是「反彈而非反轉」的典型樣態）
      - 日線看空但週線走多 → 降級空方力道（避免在多頭回檔時搶空）
      - 週線資料不足(unknown) → 完全不調整，並且不顯示共振資訊，不假裝有判斷
    刻意「只降級不升級」的原因：升級等於放大部位風險，而週線同向本來就已經
    反映在日線分數裡了，再加成會變成重複計算同一個訊號。
    """
    wt = weekly.get('trend', 'unknown')
    if wt == 'unknown':
        return verdict, None
    bullish_verdicts = ('🔥 建議進攻', '🟡 觀望偏多')
    bearish_verdicts = ('🔵 建議撤退', '⚠️ 轉弱警戒')

    if verdict in bullish_verdicts and wt == 'bear':
        return '🟡 觀望偏多' if verdict == '🔥 建議進攻' else '⚖️ 中性等待', \
               "⛰️ 週線仍空：日線轉強但週線結構未翻多，較可能是反彈而非反轉，已降級"
    if verdict in bearish_verdicts and wt == 'bull':
        return '⚖️ 中性等待' if verdict == '🔵 建議撤退' else '⚖️ 中性等待', \
               "⛰️ 週線仍多：日線轉弱但週線結構仍多頭，較可能是回檔而非轉空，已降級"
    if verdict in bullish_verdicts and wt == 'bull':
        return verdict, "✅ 日週同步偏多：多時間框架共振，訊號可信度較高"
    if verdict in bearish_verdicts and wt == 'bear':
        return verdict, "✅ 日週同步偏空：多時間框架共振，訊號可信度較高"
    return verdict, None


def compute_risk_metrics(closed_trades, min_samples=10, open_positions=None):
    """
    【V160 R44 新增】風報比(盈虧比) + 最大拉回(MDD) + 累積報酬率曲線。

    風報比 = 已平倉平均獲利金額 / 平均虧損金額（絕對值）——數字越高代表
    「贏的時候贏得比輸的時候輸得多」，是比單純勝率更能反映策略真實期望值
    的指標（勝率60%但賺1賠3，整體還是虧錢；勝率40%但賺3賠1，整體是賺錢的）。

    最大拉回(MDD) = 用平倉紀錄依時間序累加成淨值曲線，找出「從最高點到
    最低點」的最大跌幅百分比。

    【R67改善】原本的限制：MDD只計入已平倉損益，沒把「還沒平倉的浮動虧損」
    算進去，數字會比真實風險樂觀（少算了抱著虧損部位不賣那段時間的痛苦）。
    完整解法需要「每天記錄持倉市值」的歷史，我們沒有；但有一個實務上有效的
    近似：把「當下持倉的未實現損益」當作淨值曲線的最後一個點接上去。

    這樣算出來的 max_drawdown_incl_open 回答的是真正該問的問題——
    「如果現在把所有部位清掉，我從歷史最高點到現在總共回落多少」。
    它會抓到「已平倉看起來很賺，但現在抱著三檔大虧的股票不肯認賠」這種
    最危險的情況，那正是純已平倉MDD完全看不到的盲點。

    open_positions：list of dict，每筆要有 realized_roi 欄位語意的未實現
    報酬率（呼叫端算好傳進來，這裡不重算，避免跟畫面上的損益數字不一致）。
    不傳就維持原本只算已平倉的行為，完全向下相容。

    樣本數 < min_samples(預設10) 時不給任何數字——回傳 sample_count 讓
    呼叫端顯示「累積中 X/10筆」，不是假裝有統計意義的結果硬要顯示出來。
    """
    n = len(closed_trades)
    if n < min_samples:
        return {'ready': False, 'sample_count': n, 'min_samples': min_samples}

    rois = [float(t.get('realized_roi', 0) or 0) for t in closed_trades]
    pnls = [float(t.get('realized_pnl', 0) or 0) for t in closed_trades]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]

    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    profit_factor = round(avg_win / avg_loss, 2) if avg_loss > 0 else None

    # 依exit_date排序，累加報酬率曲線算MDD
    sorted_trades = sorted(closed_trades, key=lambda t: t.get('exit_date', ''))
    cum_ret = 0.0
    equity_curve = []
    peak = 0.0
    max_dd = 0.0
    for t in sorted_trades:
        cum_ret += float(t.get('realized_roi', 0) or 0)
        equity_curve.append({'date': t.get('exit_date', ''), 'cum_return': round(cum_ret, 2)})
        peak = max(peak, cum_ret)
        dd = peak - cum_ret
        max_dd = max(max_dd, dd)

    win_rate = round(sum(1 for x in pnls if x > 0) / n * 100, 1)

    # 【R67新增】把當下持倉的未實現損益接在曲線最後，算出含未實現的MDD
    max_dd_incl_open = None
    open_unrealized_roi = None
    if open_positions:
        open_unrealized_roi = sum(float(p.get('realized_roi', 0) or 0) for p in open_positions)
        _cum_now = cum_ret + open_unrealized_roi
        _peak_incl = max(peak, _cum_now)
        max_dd_incl_open = round(max(max_dd, _peak_incl - _cum_now), 2)
        equity_curve.append({'date': '現在(含未實現)', 'cum_return': round(_cum_now, 2)})

    return {
        'ready': True, 'sample_count': n,
        'profit_factor': profit_factor, 'avg_win': round(avg_win, 0), 'avg_loss': round(avg_loss, 0),
        'win_rate': win_rate, 'max_drawdown_pct': round(max_dd, 2), 'equity_curve': equity_curve,
        'max_drawdown_incl_open': max_dd_incl_open,
        'open_unrealized_roi': round(open_unrealized_roi, 2) if open_unrealized_roi is not None else None,
        'open_count': len(open_positions) if open_positions else 0,
    }


def summarize_calibration(rows):
    """
    把校正紀錄整理成可讀的準確度摘要。
    回傳 dict：筆數、平均絕對誤差%、中位數誤差%、是否偏高/偏低（有系統性偏差就講出來）。
    """
    if not rows:
        return None
    errs = [float(r['error_pct']) for r in rows if r.get('error_pct') is not None]
    if not errs:
        return None
    abs_errs = sorted(abs(e) for e in errs)
    n = len(abs_errs)
    median_abs = abs_errs[n // 2] if n % 2 else (abs_errs[n // 2 - 1] + abs_errs[n // 2]) / 2
    mean_signed = sum(errs) / len(errs)
    # 系統性偏差判定：平均帶符號誤差明顯偏離0，代表估計法一致地高估或低估
    if mean_signed > 3:
        bias = "系統性高估"
    elif mean_signed < -3:
        bias = "系統性低估"
    else:
        bias = "無明顯系統性偏差"
    return {
        'count': len(errs),
        'mean_abs_err': round(sum(abs_errs) / len(abs_errs), 2),
        'median_abs_err': round(median_abs, 2),
        'mean_signed_err': round(mean_signed, 2),
        'bias': bias,
        'within_10pct': round(100.0 * sum(1 for e in abs_errs if e <= 10) / len(abs_errs), 1),
    }


def build_rotation_advice(rows):
    """
    【V160 延伸1】把熱力圖數字轉成「所以我該往哪找股票」的結論。
    判讀標準寫死並公開，讓你知道建議怎麼來的，不是黑箱。

    【V160 新增：雙引擎族群透視】加入「平均數 vs 中位數」夾擊判讀——
    均值代表極端爆發力(少數飆股拉動)，中位數代表產業普及率(過半數公司的
    真實狀況)。兩者一起看能戳破「假族群起漲」：均值很高但中位數很低，
    代表只有少數龍頭在漲、底層公司其實沒跟上。

    只有 rev_sample_count 有值(該產業至少5檔有YoY資料)的產業才會套用這三條
    規則——樣本不足的產業，均值/中位數本身就不可信，套用判讀規則只會產生
    誤導性的結論，不如不判讀。

    【優先順序，總指揮官確認過】「衰退偽裝」比「龍頭領漲」優先檢查——
    這兩條規則在數學上會重疊(龍頭領漲要求median<5%，衰退偽裝要求median<0%，
    median<0必然也<5)，衰退偽裝是風險警告，蓋過樂觀解讀比較安全，
    不能讓兩條同時成立時系統只顯示比較好聽的那個。
    """
    if not rows:
        return ["資料不足，無法判讀族群輪動。"]
    out = []
    strong = [r for r in rows if r['5日%'] is not None and r['5日%'] > 2]
    weak = [r for r in rows if r['5日%'] is not None and r['5日%'] < -2]
    # 短期轉強：5日明顯強於20日 → 資金剛開始流入，屬於「起漲」型態
    turning = [r for r in rows
               if r['5日%'] is not None and r['20日%'] is not None
               and r['5日%'] > 1 and r['5日%'] > r['20日%']]

    if turning:
        names = "、".join(f"{r['產業']}({r['5日%']:+.1f}%)" for r in turning[:3])
        out.append(f"🚀 **資金剛流入（5日強於20日，起漲型態）**：{names} "
                   f"—— 這類族群短期動能剛轉強，是選股優先掃描的方向。")
    if strong:
        names = "、".join(f"{r['產業']}({r['5日%']:+.1f}%)" for r in strong[:3])
        out.append(f"🔥 **近5日最強族群**：{names} —— 順勢做多優先在這裡面找。")
    if weak:
        names = "、".join(f"{r['產業']}({r['5日%']:+.1f}%)" for r in weak[:3])
        out.append(f"🔵 **近5日最弱族群**：{names} —— 做多要避開；如果你做空，這裡是主戰場。")
    if not strong and not weak:
        out.append("⚖️ 各產業近5日漲跌都在 ±2% 內，沒有明顯的族群輪動，"
                   "這種盤選股要更依賴個股本身的訊號，族群過濾幫助有限。")

    # 【R95新增】資金佔比＋動能組合訊號——單看動能，小池子噴出5%跟真正
    # 主力大金流噴出5%看起來一樣強，這裡疊上資金佔比(今天成交值佔全市場
    # 比例)。門檻：資金佔比>=5%且5日%>2%，合理但主觀的起始值。
    combo = [r for r in rows
             if r.get('資金佔比%') is not None and r['資金佔比%'] >= 5
             and r['5日%'] is not None and r['5日%'] > 2]
    if combo:
        combo_sorted = sorted(combo, key=lambda r: r['資金佔比%'], reverse=True)
        names = "、".join(f"{r['產業']}(資金佔比{r['資金佔比%']:.1f}%／5日{r['5日%']:+.1f}%)"
                         for r in combo_sorted[:3])
        out.append(f"💰 **資金重兵＋動能雙強**：{names} —— 這不只是噴出來的小池子，"
                   f"是真正有大量資金駐紮、同時動能也轉強的族群，比單看5日%的訊號更有份量。")

    # 【V160 新增】平均vs中位數夾擊判讀——只對有足夠營收樣本的產業套用
    for r in rows:
        if r.get('rev_sample_count') is None:
            continue
        d5, mean, median = r['5日%'], r.get('yoy_mean'), r.get('yoy_median')
        if d5 is None or mean is None or median is None or d5 <= 1.5:
            continue
        if median < 0:
            out.append(f"⚠️ **衰退偽裝族群：{r['產業']}**（5日{d5:+.1f}%，"
                       f"營收YoY均值{mean:+.1f}%／中位數{median:+.1f}%）—— "
                       f"熱錢正在炒作，但過半數公司營收處於衰退。此為純籌碼資金戰，"
                       f"隨時有獲利了結崩盤風險，操作需嚴守技術面停損，見好就收。")
        elif mean > 15 and median < 5:
            out.append(f"🚀 **龍頭領漲族群：{r['產業']}**（5日{d5:+.1f}%，"
                       f"營收YoY均值{mean:+.1f}%／中位數{median:+.1f}%）—— "
                       f"少數極端飆股拉動整個產業，底層半數公司其實未見成長。"
                       f"操作必須『強者恆強只買龍頭』，切忌盲目追價同族群的無基之彈跟風股。")
        elif median > 10 and mean > 10:
            out.append(f"🌟 **全面繁榮族群：{r['產業']}**（5日{d5:+.1f}%，"
                       f"營收YoY均值{mean:+.1f}%／中位數{median:+.1f}%）—— "
                       f"資金湧入且過半數公司營收強勁。產業雨露均霑，不僅龍頭強勢，"
                       f"佈局二線落後補漲股也具備基本面保護傘。")

    out.append("＿＿＿\n提醒：這是「同產業分類」的族群強弱，不是真正的供應鏈上下游關聯；"
               "且統計只涵蓋本次掃描池內的股票，不是全市場普查。營收YoY統計來自"
               "最近一次全市場掃描，樣本<5檔的產業不顯示營收判讀。")
    return out


def calc_rsi(df, period=14):
    delta = df['Close'].diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))


def calc_bias(df, period=20):
    ma = df['Close'].rolling(period).mean()
    return (df['Close'] - ma) / (ma + 1e-9) * 100


def calc_disposal_risk_proxy(hist, vol_ratio):
    """
    【V157 新增，簡化版風險提示，非官方模型】
    證交所實際的注意股／處置股判定，涉及證券交易法規約 9 項主法條、12 項副法條，
    且門檻依股價級距、上市／上櫃分別調整，本系統沒有能力也不打算重現完整規則。
    這裡只用市場最常被引用的「六個營業日累計漲跌幅 + 成交量異常倍增」作為粗略代理，
    純粹是「這檔股票最近激進程度已經到需要提高警覺」的提醒，不是精準預測，
    也不保證與官方公告一致，請勿單獨依賴此標籤做交易決策。
    """
    if hist is None or len(hist) < 7:
        return {'flag': False, 'level': 'none', 'six_day_gain': 0.0}
    close6 = float(hist['Close'].iloc[-7])
    close0 = float(hist['Close'].iloc[-1])
    six_day_gain = ((close0 - close6) / close6 * 100) if close6 > 0 else 0.0
    abs_gain = abs(six_day_gain)

    # 【R88新增】改讀可調整門檻，不再寫死數字——側欄「🎛️門檻參數調整」
    # 面板改的值，這裡會直接生效。
    _gain_high = get_threshold('six_day_gain_high')
    _gain_watch = get_threshold('six_day_gain_watch')
    _vol_surge = get_threshold('vol_ratio_surge')

    if abs_gain >= _gain_high or (abs_gain >= _gain_watch and vol_ratio >= _vol_surge):
        level = 'high'
    elif abs_gain >= _gain_watch or (abs_gain >= _gain_watch * 0.6 and vol_ratio >= _vol_surge * 0.9):
        level = 'watch'
    else:
        level = 'none'

    return {'flag': level != 'none', 'level': level, 'six_day_gain': round(six_day_gain, 1)}


def _fmt_closing_strength(c):
    """
    【R96新增】收盤強弱代查的顯示區塊——策略框架圖「波段續抱資格三關·
    第三關」：收盤位置決定強弱。跟 _fmt_main_force_cost 同一種「單獨一小塊，
    抓不到就明講」風格，缺值時不畫這塊（正常不會缺值，因為 open/high/low/
    close 是戰卡運算最早期就一定會有的資料，這裡防呆純粹避免舊快取資料
    沒有這個欄位時整頁崩潰）。

    【R96追加】標籤加上滑動說明（跟PE等既有欄位同一套m-tooltip機制）——
    總指揮官反映光看「收高檔(100%)」這種數字，沒有上下文解釋，看不懂
    這個百分比代表什麼意思。
    """
    cs = c.get('closing_strength')
    if not cs:
        return ""
    color = {"strong": "#ff4d4d", "weak": "#00e676", "neutral": "#aaa"}.get(cs.get('verdict'), "#aaa")
    shadow_tag = (' <span style="color:#f1c40f; font-size:11px;">⚠️長上影</span>'
                  if cs.get('has_long_upper_shadow') else "")
    _tip = ("收盤價落在「當日最高價～最低價」區間裡的百分位：100%＝收在當日最高點，"
            "0%＝收在當日最低點。≥75%（前25%高檔區）→明天有戲；≤25%（後25%低檔區）"
            "→今天該走；其餘為中段區。")
    return (f'<div style="font-size:12px; border-top:1px dashed #444; padding-top:6px; '
            f'margin-top:6px; color:#aaa;">'
            f"<span class='m-tooltip'>📍 收盤強弱<span class='m-tooltiptext'>{_tip}</span></span>："
            f'<strong style="color:{color};">{cs.get("label")}（{cs.get("pct")}%）</strong>'
            f'{shadow_tag}<div style="font-size:11px; color:#888; margin-top:2px;">'
            f'{cs.get("detail")}</div></div>')


def _fmt_volume_followthrough(c):
    """
    【R96新增】量能達標代查的顯示區塊——策略框架圖「波段續抱資格三關·
    第二關」：股價創新高，成交量是否跟得上。跟 _fmt_closing_strength 同一種
    「單獨一小塊，抓不到就明講」風格。verdict='unknown'（找不到攻擊K棒基準）
    時只用灰色淡淡顯示一行提示，不用紅綠強調色，避免讓「沒有基準可比較」
    看起來像是某種警訊——那只是「還沒有夠格的攻擊K棒可以拿來比較」，跟
    weak（有基準、但量能真的不足）意義不同，顏色要分開。

    【R96追加】標籤加上滑動說明，理由跟 _fmt_closing_strength 一致。
    """
    vf = c.get('volume_followthrough')
    if not vf:
        return ""
    _tip = ("先找出近20個交易日內最近一根「攻擊K棒」（爆量收紅的起漲點），比較「今天成交量」"
            "占「攻擊K棒成交量」的百分比——但只有在今天創近20日新高時才判斷："
            "≥80%→量能達標，有新資金進場；<50%→量能不足，沒人願意高檔承接；"
            "沒創新高時這一關先不適用（不是不合格，是還沒輪到判斷）。")
    if vf.get('verdict') == 'unknown':
        return (f'<div style="font-size:11px; color:#666; border-top:1px dashed #444; '
                f'padding-top:6px; margin-top:6px;">'
                f"<span class='m-tooltip'>📊 量能達標<span class='m-tooltiptext'>{_tip}</span></span>："
                f'{vf.get("detail")}</div>')
    color = {"strong": "#ff4d4d", "weak": "#00e676", "neutral": "#aaa"}.get(vf.get('verdict'), "#aaa")
    _ratio_txt = f"{vf.get('ratio_pct')}%" if vf.get('ratio_pct') is not None else "—"
    return (f'<div style="font-size:12px; border-top:1px dashed #444; padding-top:6px; '
            f'margin-top:6px; color:#aaa;">'
            f"<span class='m-tooltip'>📊 量能達標<span class='m-tooltiptext'>{_tip}</span></span>："
            f'<strong style="color:{color};">{vf.get("label")}（{_ratio_txt}）</strong>'
            f'<div style="font-size:11px; color:#888; margin-top:2px;">'
            f'{vf.get("detail")}</div></div>')


def _fmt_pullback_health(c):
    """
    【R96新增】拉回體檢母關的顯示區塊——策略框架圖整合Step 3，合併新A-1
    (盤中)/新B-1(波段)。跟前兩關（收盤強弱/量能達標）同一種顯示風格。
    verdict='unknown'時（找不到攻擊基準，或攻擊K棒本身就是最新一根、
    還沒有拉回可以體檢）同樣用灰色淡淡顯示，不用紅綠強調色，理由跟
    _fmt_volume_followthrough一致。

    【R96追加】標籤加上滑動說明，理由跟前兩關一致。這裡固定用swing模式的
    說明文字（目前戰卡日線版只跑swing模式），intraday模式的說明留給之後
    5分K版本的顯示函式另外處理，不在這裡混講兩種模式增加混淆。
    """
    ph = c.get('pullback_health')
    if not ph:
        return ""
    _tip = ("先找出近20個交易日內最近一根「攻擊K棒」，以那根K棒本身的最高價～最低價"
            "為0%~100%的參考範圍，看現在的價格拉回到這個範圍的第幾%位置（超過100%代表"
            "現在價格已經比攻擊K棒當時的最高點還高）：≥50%（守住一半以上）→續抱合格；"
            "<33%（跌破三分之一）或跌破攻擊K棒最低點（起漲點）→出場訊號。")
    if ph.get('verdict') == 'unknown':
        return (f'<div style="font-size:11px; color:#666; border-top:1px dashed #444; '
                f'padding-top:6px; margin-top:6px;">'
                f"<span class='m-tooltip'>🔄 拉回體檢<span class='m-tooltiptext'>{_tip}</span></span>："
                f'{ph.get("detail")}</div>')
    color = {"strong": "#ff4d4d", "weak": "#00e676", "neutral": "#aaa"}.get(ph.get('verdict'), "#aaa")
    _price_txt = f"{ph.get('price_pct')}%位置" if ph.get('price_pct') is not None else "—"
    return (f'<div style="font-size:12px; border-top:1px dashed #444; padding-top:6px; '
            f'margin-top:6px; color:#aaa;">'
            f"<span class='m-tooltip'>🔄 拉回體檢<span class='m-tooltiptext'>{_tip}</span></span>："
            f'<strong style="color:{color};">{ph.get("label")}（{_price_txt}）</strong>'
            f'<div style="font-size:11px; color:#888; margin-top:2px;">'
            f'{ph.get("detail")}</div></div>')


def _fmt_rebound_health(c):
    """
    【R96新增】反彈健康度的顯示區塊——累積清單第6項，依批次五分析修正版：
    急殺當下量大是正常生理反應，真正的判斷點在「反彈階段」的量。跟
    _fmt_pullback_health是對稱的一組（一個看多頭攻擊後拉回，一個看空頭
    急殺後反彈），顯示風格一致。
    """
    rh = c.get('rebound_health')
    if not rh:
        return ""
    _tip = ("先找出近20個交易日內最近一根「急殺K棒」（爆量收黑），比較之後反彈階段的"
            "平均量 ÷ 急殺當天的量：<70%（反彈量縮）→賣壓在減輕，虛跌可以等；"
            "≥100%（反彈量增）但股價彈不回去→賣壓沒減輕，有人趁反彈倒貨，該走就走。")
    if rh.get('verdict') == 'unknown':
        return (f'<div style="font-size:11px; color:#666; border-top:1px dashed #444; '
                f'padding-top:6px; margin-top:6px;">'
                f"<span class='m-tooltip'>📉 反彈健康度<span class='m-tooltiptext'>{_tip}</span></span>："
                f'{rh.get("detail")}</div>')
    color = {"strong": "#ff4d4d", "weak": "#00e676", "neutral": "#aaa"}.get(rh.get('verdict'), "#aaa")
    _vt = f"{rh.get('vol_ratio_pct')}%" if rh.get('vol_ratio_pct') is not None else "—"
    return (f'<div style="font-size:12px; border-top:1px dashed #444; padding-top:6px; '
            f'margin-top:6px; color:#aaa;">'
            f"<span class='m-tooltip'>📉 反彈健康度<span class='m-tooltiptext'>{_tip}</span></span>："
            f'<strong style="color:{color};">{rh.get("label")}（{_vt}）</strong>'
            f'<div style="font-size:11px; color:#888; margin-top:2px;">'
            f'{rh.get("detail")}</div></div>')


def _fmt_trend_regime_tag(c):
    """
    【R96新增】趨勢/趨勢中休息/盤整三態徽章——總指揮官明確要求放在戰卡
    最前面（跟股票名稱同一行），因為這是「判斷框架」本身：同一個訊號
    在不同態下意義不同（例如RSI偏低，趨勢股是空頭佔優、盤整股是超賣
    機會、趨勢中休息是正常整理），要先知道現在是哪一態，後面看其他
    判斷才有正確的參考框架。跟k_tags同一種徽章樣式，掛在同一行。

    trend_regime為None時（均線缺值，通常是上市時間太短不足60日均線）
    回傳空字串，不畫這個徽章——沒有足夠資料時，不該顯示一個看似確定
    的分類。
    """
    regime = c.get('trend_regime')
    if not regime:
        return ""
    _cfg = {
        'trending': ("🚀 趨勢股", "#4a1515", "#ff8080",
                     "MA5/20/60分散(未糾結)，有明確趨勢方向，RSI判斷用動能追蹤版。"),
        'trend_resting': ("😴 趨勢中休息", "#4a3a10", "#f1c40f",
                          "均線短期糾結，但過去約半年內曾出現明顯漲幅、且還沒被大部分回吃，"
                          "研判是大趨勢中的健康整理，不是真的沒方向。RSI判斷用趨勢休息版"
                          "（比動能版保守、比均值回歸版謹慎）。"),
        'ranging': ("📦 區間盤整", "#1a3a4a", "#5ac8fa",
                    "均線糾結，且過去約半年內沒有出現明顯趨勢（或曾經有漲幅但已被大部分"
                    "回吃），研判是真正的區間震盪，RSI判斷用均值回歸版（高了留意回檔、"
                    "低了留意反彈）。"),
    }
    if regime not in _cfg:
        return ""
    label, bg, fg, tip = _cfg[regime]
    # 【R96修復】徽章位在卡片最頂端，加m-tooltip-down這個class覆蓋展開
    # 方向(往下展開)，避免說明文字被螢幕邊界切掉。m-tooltip本身要保留，
    # 兩個class要同時掛在同一個span上。
    return (f"<span class='m-tooltip m-tooltip-down k-tag' style='background:{bg}; color:{fg};'>{label}"
            f"<span class='m-tooltiptext'>{tip}</span></span>")


def _fmt_order_book_pressure(c):
    """
    【R96新增】五檔買盤結構的顯示區塊——策略框架圖整合Step 5（新A-3／
    附件38）。跟前三關同一種顯示風格。這一關資料只在盤中才會有（收盤後
    /非交易時段查不到掛單，attach_live_quotes那邊查不到即時報價時
    c.get('order_book')就會是None，這裡直接不畫這塊，不強行顯示過時的
    盤中資料）。
    """
    ob = c.get('order_book')
    if not ob:
        return ""
    # 【R96修復——文字沒跟上功能升級】原本這裡的tooltip還寫著「還沒做到
    # 成交是打在買價還是賣價...系統還沒接上」，但內外盤成交比率這個功能
    # 上幾輪已經做完了（見evaluate_order_book_pressure的outer_volume/
    # inner_volume參數）——總指揮官反映看到這個舊警語，會誤以為功能還沒
    # 做，這裡改成依data_completeness動態顯示正確的完整度說明，不再是
    # 寫死的「還沒接上」。
    if ob.get('data_completeness') == 'full':
        _tip = ("五檔委買（買方掛單）總張數 ÷ 五檔委賣（賣方掛單）總張數：≥1.5倍→買盤掛單墊高；"
                "≤0.67倍→賣盤掛單較重。同時已疊加外盤/內盤成交比率（tick rule逐筆分類）：買盤墊高"
                "+外盤成交為主=真買；買盤雖厚但內盤成交為主=疑似偷出貨，可信度較高的完整判斷。")
    else:
        _tip = ("五檔委買（買方掛單）總張數 ÷ 五檔委賣（賣方掛單）總張數：≥1.5倍→買盤掛單墊高；"
                "≤0.67倍→賣盤掛單較重；其餘為均衡。⚠️ 這次沒有拿到外盤/內盤成交比率資料"
                "（可能是今天5分K還沒收集到足夠資料、或尚未執行supabase_migration_r96_"
                "outer_inner_volume.sql），只做到「掛單厚不厚」，判斷還不完整，僅供參考。")
    if ob.get('verdict') == 'unknown':
        return (f'<div style="font-size:11px; color:#666; border-top:1px dashed #444; '
                f'padding-top:6px; margin-top:6px;">'
                f"<span class='m-tooltip'>📖 五檔買盤<span class='m-tooltiptext'>{_tip}</span></span>："
                f'{ob.get("detail")}</div>')
    color = {"strong": "#ff4d4d", "weak": "#00e676", "neutral": "#aaa"}.get(ob.get('verdict'), "#aaa")
    _ratio_txt = f"{ob.get('depth_ratio')}倍" if ob.get('depth_ratio') is not None else "—"
    _thicken_tag = ""
    if ob.get('is_thickening') is True:
        _thicken_tag = ' <span style="color:#f1c40f; font-size:11px;">📈買盤墊高中</span>'
    return (f'<div style="font-size:12px; border-top:1px dashed #444; padding-top:6px; '
            f'margin-top:6px; color:#aaa;">'
            f"<span class='m-tooltip'>📖 五檔買盤<span class='m-tooltiptext'>{_tip}</span></span>："
            f'<strong style="color:{color};">{ob.get("label")}（{_ratio_txt}）</strong>'
            f'{_thicken_tag}<div style="font-size:11px; color:#888; margin-top:2px;">'
            f'{ob.get("detail")}</div></div>')


def _fmt_today_liquidity(c):
    """
    【R96新增】今日流動性過濾器的顯示區塊——累積清單第9項。跟五檔買盤
    同一種顯示風格，資料只在有即時報價時才會有（attach_live_quotes
    查不到即時累計量時c.get('liquidity')就會是None，這裡直接不畫這塊）。
    """
    liq = c.get('liquidity')
    if not liq:
        return ""
    _tip = ("今天累計到目前為止的真實成交量 ÷ 近5日平均成交量：≥60%→流動性充足，"
            "可積極找標的；≤30%→量能清淡，滑價大，進場容易被磨損，建議觀望。")
    if liq.get('verdict') == 'unknown':
        return ""   # 資料不足時安靜不顯示，不強行畫一個灰色空白區塊
    color = {"adequate": "#ff4d4d", "thin": "#00e676", "moderate": "#aaa"}.get(liq.get('verdict'), "#aaa")
    _pct_txt = f"{liq.get('pct_of_avg')}%" if liq.get('pct_of_avg') is not None else "—"
    return (f'<div style="font-size:12px; border-top:1px dashed #444; padding-top:6px; '
            f'margin-top:6px; color:#aaa;">'
            f"<span class='m-tooltip'>💧 今日流動性<span class='m-tooltiptext'>{_tip}</span></span>："
            f'<strong style="color:{color};">{liq.get("label")}（{_pct_txt}）</strong>'
            f'<div style="font-size:11px; color:#888; margin-top:2px;">'
            f'{liq.get("detail")}</div></div>')


def _fmt_day_trader_and_margin(c):
    """
    【R96新增】當沖佔比+融資餘額籌碼濾網的顯示區塊——累積清單第5項，
    依附件26。兩個判斷合併在同一塊顯示（都屬於「市場情緒」這個主題），
    任一個沒有資料就只顯示有資料的那個，兩個都沒有就整塊不顯示。
    """
    dtr = c.get('day_trader_ratio')
    mgr = c.get('margin_regime')
    _parts = []

    if dtr and dtr.get('verdict') != 'unknown':
        _color = {"strong": "#ff4d4d", "weak": "#00e676", "neutral": "#aaa"}.get(dtr.get('verdict'), "#aaa")
        _parts.append(f'<div>當沖佔比：<strong style="color:{_color};">{dtr.get("label")}'
                      f'（{dtr.get("ratio_pct")}%）</strong></div>')
    if mgr and mgr.get('verdict') != 'unknown':
        _color = {"strong": "#ff4d4d", "weak": "#00e676", "neutral": "#aaa"}.get(mgr.get('verdict'), "#aaa")
        _parts.append(f'<div>融資水位：<strong style="color:{_color};">{mgr.get("label")}</strong></div>')

    if not _parts:
        return ""

    _tip = ("依附件26：融資餘額低檔/下降+當沖佔比<30%=散戶還沒進場、情緒偏冷，續抱空間還在；"
            "融資餘額創高+當沖佔比>40%=散戶大量進場接盤、投機過熱，主力容易趁高檔出貨。")
    return (f'<div style="font-size:12px; border-top:1px dashed #444; padding-top:6px; '
            f'margin-top:6px; color:#aaa;">'
            f"<span class='m-tooltip'>🎯 籌碼情緒<span class='m-tooltiptext'>{_tip}</span></span>："
            f'<div style="font-size:11px; margin-top:2px;">{"".join(_parts)}</div></div>')


def _fmt_vwap_position(c):
    """
    【R96新增】VWAP位置的顯示區塊——累積清單第7項，Step 1收盤強弱的補充
    判斷角度（用均價線，不是用當日高低區間百分位）。只在有5分K資料時
    才會有值（attach_live_quotes批次查Supabase算出來的），沒有資料時
    安靜不顯示。
    """
    vp = c.get('vwap_position')
    if not vp or vp.get('verdict') == 'unknown':
        return ""
    color = {"strong": "#ff4d4d", "weak": "#00e676"}.get(vp.get('verdict'), "#aaa")
    _tip = ("用今天的5分K反推近似VWAP（成交量加權平均價），現價站上VWAP=多方守住，"
            "明天有機會延續；跌破VWAP=空方壓境，該注意風險。（依附件29「收盤前30分鐘的方向表態」，"
            "均價線是當天多空的分水嶺）")
    return (f'<div style="font-size:12px; border-top:1px dashed #444; padding-top:6px; '
            f'margin-top:6px; color:#aaa;">'
            f"<span class='m-tooltip'>📐 VWAP位置<span class='m-tooltiptext'>{_tip}</span></span>："
            f'<strong style="color:{color};">{vp.get("label")}（{vp.get("deviation_pct"):+.2f}%）</strong>'
            f'<div style="font-size:11px; color:#888; margin-top:2px;">VWAP≈{vp.get("vwap")}</div></div>')


def _fmt_daytrade_verdict_banner(c):
    """
    【R96新增】當沖建議橫幅——顯示evaluate_daytrade_recommendation()的
    綜合結論，跟波段建議橫幅並列但分開顯示、分開的顏色邏輯（不是同一套
    determine_signal()評分）。verdict值對應色彩沿用這個app既有的紅漲綠跌
    慣例：積極/偏多用紅色系，避開/否決用藍色系（呼應「🔵偏空防守」的
    既有配色），中性/資料不足用灰色。

    沒有daytrade_recommendation資料時（精簡路徑，fetch_intraday_extras=
    False，例如戰情速覽——雖然速覽根本不會呼叫這個函式，但持倉/雷達的
    完整卡片萬一這次沒查到當沖延伸資料，也不該顯示一個誤導的橫幅）
    完全不顯示，不留空白區塊或錯誤的「資料不足」大字報。
    """
    dr = c.get('daytrade_recommendation')
    if not dr or dr.get('verdict') == 'unknown':
        return ""

    _style = {
        'veto': ("#0d2b5c", "#2979ff", "🔵"),
        'avoid': ("#0d2b5c", "#2979ff", "🔵"),
        'watch_negative': ("#3a2f0d", "#f1c40f", "🟡"),
        'neutral': ("#2a2a2a", "#aaaaaa", "⚪"),
        'watch_positive': ("#3a2f0d", "#f1c40f", "🟡"),
        'aggressive': ("#5c1a0d", "#ff4d4d", "🔥"),
    }.get(dr['verdict'], ("#2a2a2a", "#aaaaaa", "⚪"))
    _bg, _color, _icon = _style

    _score_txt = f"分數 {dr['score']:+d}" if dr.get('score') is not None else ""
    _veto_note = f"（{dr['veto_reason']}）" if dr.get('veto_reason') else ""

    return (f'<div style="background:{_bg}; border:1px solid {_color}; border-radius:6px; '
            f'padding:10px 12px; margin-bottom:10px;">'
            f'<div style="font-size:10px; color:#888; margin-bottom:2px;">⚡ 當沖建議</div>'
            f'<div style="display:flex; justify-content:space-between; align-items:center;">'
            f'<span style="font-size:18px; font-weight:bold; color:{_color};">'
            f'{_icon} {dr["label"]}{_veto_note}</span>'
            f'<span style="font-size:11px; color:#888;">{_score_txt}</span></div>'
            f'<div style="font-size:12px; color:#ddd; margin-top:4px;">{dr.get("detail", "")}</div></div>')


def _fmt_main_force_cost(c):
    """
    【V160 延伸2】主力成本免費替代估計的顯示區塊。

    刻意把三個數字分開列而不是合成一個「主力成本」：它們語意不同——
    VWAP20/60 是「整體市場平均成本」，爆量日均價才偏向「大資金成本」。
    合成一個數字會讓你無法判斷該信哪個，也無法跟籌碼K線對照校正。
    抓不到就明講「資料不足」，不填假數字。
    """
    mf = c.get('mf_cost')
    if not mf:
        return ('<div style="font-size:12px; color:#888; border-top:1px dashed #444; '
                'padding-top:6px; margin-top:6px;">📐 主力成本估計：股價資料不足，無法估算</div>')

    def _one(label, val, dev, tip):
        if val is None:
            return f'<span style="color:#666;">{label} —</span>'
        dev_color = "#ff4d4d" if (dev or 0) > 0 else ("#00c853" if (dev or 0) < 0 else "#888")
        dev_txt = f'<span style="color:{dev_color};">({dev:+.1f}%)</span>' if dev is not None else ""
        return (f"<span class='m-tooltip' style='color:#aaa;'>{label}"
                f"<span class='m-tooltiptext'>{tip}</span></span> "
                f"<strong style='color:#00d2ff;'>{val}</strong> {dev_txt}")

    parts = [
        _one("VWAP20", mf.get('vwap20'), mf.get('dev_vwap20'),
             "近20日成交量加權平均價＝短期市場平均成本。現價高於它代表短線持有者平均在賺。"),
        _one("VWAP60", mf.get('vwap60'), mf.get('dev_vwap60'),
             "近60日成交量加權平均價＝中期市場平均成本，比VWAP20更能代表波段持有者的成本。"),
        _one(f"爆量均價({mf.get('heavy_days', 0)}日)", mf.get('heavy_vwap'), mf.get('dev_heavy'),
             "只取近60日成交量最大的25%個交易日算加權均價。大單進場通常伴隨爆量，"
             "所以這個數字比一般VWAP更偏向「大資金的成本」，是分點主力成本的免費近似。"),
    ]
    return (f'<div style="font-size:12px; border-top:1px dashed #444; padding-top:6px; '
            f'margin-top:6px; color:#aaa;">📐 <b style="color:#f1c40f;">主力成本估計</b>'
            f'<span style="color:#666;">（免費替代，非分點實際成本）</span><br>'
            f'{" ｜ ".join(parts)}</div>')


def _fmt_vwap(c, key, label, color):
    """把 VWAP 區塊壓成單行 HTML；無資料時明確顯示原因，不用 0 帶過。"""
    v = c.get(key)
    price = float(c.get('price', 0) or 0)
    tip = ("<span class='m-tooltiptext'>回推法人「連續同方向買/賣超」區間，以每日典型價(H+L+C)/3"
           "對法人張數加權，估算其真實平均成本。現價低於買超成本＝法人套牢，反彈易遇解套賣壓；"
           "現價高於買超成本＝法人有浮額獲利，拉抬意願較高。</span>")
    if not v:
        return (f"<div style='font-size:12px; color:#a8bccf;'>{label}: <span class='m-tooltip'>"
                f"— 需先同步近日籌碼{tip}</span></div>")
    dev = ((price - v['vwap']) / v['vwap'] * 100) if v['vwap'] > 0 else 0.0
    dev_c = "#ff4d4d" if dev > 0 else "#00FF00"
    # 【R96修復】原本這裡「連續N日(±X張)」的顏色用呼叫端傳進來的固定color參數
    # （外資固定紅、投信固定黃），不管實際是買超還是賣超都一樣——「連續賣超」
    # 顯示紅色，違反這個app「紅漲綠跌」的既有慣例（賣超是偏空訊號，該用綠色）。
    # 改成依v['side']判斷：買超用紅、賣超用綠，不再用呼叫端傳入的color（那個
    # 參數保留給呼叫端未來若有其他用途，這裡先不用它決定這個顏色）。
    _side_color = "#ff4d4d" if v['side'] == '買超' else "#00c853"
    return (f"<div style='font-size:12px; color:#bbb;'><span class='m-tooltip'>{label}{tip}</span>: "
            f"連續{v['side']} <strong style='color:{_side_color};'>{v['days']}日 ({v['lots']:+,}張)</strong> | "
            f"成本 <strong style='color:#00d2ff;'>{v['vwap']:.2f}元</strong> | "
            f"現價乖離 <strong style='color:{dev_c};'>{dev:+.1f}%</strong></div>")


def _pick_col(cols, must_all, must_none=()):
    for c in cols:
        s = str(c)
        if all(k in s for k in must_all) and not any(k in s for k in must_none):
            return c
    return None


def _detect_mops_industry(cols):
    """
    【R98續33新增，總指揮官方向：MOPS財報自助上傳，避免每次都要透過對話
    貼SQL消耗大量資源】用CSV表頭的欄位組合，自動判斷這份MOPS財報CSV
    屬於哪個產業別（不需要總指揮官自己選，系統自動辨識）。判斷順序刻意
    講究：先判斷「有沒有這個產業獨有的欄位」，避免用太籠統的關鍵字
    (例如「收益」兩個字，一般業的「其他收益及費損淨額」欄位裡也有這
    兩個字，若隨便用substring比對會誤判)。

    回傳 (industry_note, revenue_col, gross_profit_col, operating_income_col,
          net_income_col, eps_col) 或 None（辨識不出來）。
    """
    eps_col = next((c for c in cols if c.startswith('基本每股盈餘')), None)
    if '保險服務結果' in cols:
        return ('保險業', '保險服務結果', None, '營業利益（損失）', '本期淨利（淨損）', eps_col)
    if '營業收入' in cols:
        gp_col = '營業毛利（毛損）淨額' if '營業毛利（毛損）淨額' in cols else None
        return ('一般業', '營業收入', gp_col, '營業利益（損失）', '本期淨利（淨損）', eps_col)
    if '保險其他營業成本' in cols:
        return ('金控業', '淨收益', None, None, '本期稅後淨利（淨損）', eps_col)
    if '呆帳費用、承諾及保證責任準備提存' in cols:
        return ('銀行業', '利息淨收益', None, None, '本期稅後淨利（淨損）', eps_col)
    if '收益' in cols and '支出及費用' in cols:
        return ('證券期貨業', '收益', None, '營業利益', '本期淨利（淨損）', eps_col)
    if '收入' in cols and '支出' in cols:
        return ('其他業', '收入', None, None, '本期淨利（淨損）', eps_col)
    return None


def build_backtest_advice(summary_df):
    """
    【V160 新增】把回測數字轉成「所以我該怎麼做」的總結建議。

    總指揮官回報：回測跑完只給一張表，還要自己解讀。這裡直接把結論講白：
    哪個訊號值得照做、哪個訊號在這檔股票身上不準、樣本夠不夠。

    判讀標準（刻意寫死並公開，讓你知道建議是怎麼來的，不是黑箱）：
      勝率 ≥ 60% 且樣本 ≥ 10 → 值得照做
      勝率 45~60%           → 跟丟銅板差不多，需搭配其他條件
      勝率 < 45% 且樣本 ≥ 10 → 這檔在此訊號上反指標，反向思考
      樣本 < 10             → 樣本太少，不做結論（不是「不準」，是「不知道」）
    """
    if summary_df is None or summary_df.empty:
        return ["樣本不足，無法產生建議。"]

    good, bad, weak, thin = [], [], [], []
    for _, r in summary_df.iterrows():
        sig = r.get('訊號', '')
        n = int(r.get('樣本數', 0) or 0)
        wr = r.get('10日勝率%', r.get('3日勝率%'))
        if n < 10 or wr is None or (isinstance(wr, float) and pd.isna(wr)):
            thin.append(f"{sig}（樣本{n}）")
            continue
        wr = float(wr)
        if wr >= 60:
            good.append(f"{sig}：勝率 {wr:.0f}%／樣本 {n}")
        elif wr < 45:
            bad.append(f"{sig}：勝率 {wr:.0f}%／樣本 {n}")
        else:
            weak.append(f"{sig}：勝率 {wr:.0f}%／樣本 {n}")

    out = []
    if good:
        out.append("✅ **值得照做**：" + "；".join(good)
                   + " —— 這些訊號在這檔股票上歷史命中率夠高，出現時可提高信心。")
    if bad:
        out.append("🔄 **反指標**：" + "；".join(bad)
                   + " —— 勝率低於擲硬幣，這檔在此訊號出現時反而常走反向，別照做。")
    if weak:
        out.append("⚖️ **不具參考性**：" + "；".join(weak)
                   + " —— 接近隨機，單看這個訊號等於沒有優勢，必須搭配籌碼或大盤條件。")
    if thin:
        out.append("📭 **樣本不足**：" + "、".join(thin)
                   + " —— 樣本太少不下結論。這是「還不知道」，不是「不準」，可拉長回測年數再看。")
    if not (good or bad):
        out.append("⚠️ 整體結論：這檔股票沒有任何訊號達到可信賴的勝率水準，"
                   "代表它的走勢對這套技術訊號不敏感，建議別把它當主力標的。")
    out.append("＿＿＿\n提醒：以上只是這**單一檔股票**的歷史統計，"
               "不等於整體策略勝率，也不保證未來重現。要看策略整體表現請用「手動vs系統PK」。")
    return out


def assess_filter_stability(walkforward_df):
    """
    【R77新增】把滾動驗證的結果，濃縮成「這個濾網穩不穩定」的判讀，不用
    自己盯著一堆數字猜。

    判讀邏輯：算每個濾網在所有窗口間命中率的標準差。標準差小＝各期間表現
    接近（穩定，是「高原區」）；標準差大＝某些期間好、某些期間差（不穩定，
    可能只是特定市場環境下的「孤峰」巧合，不是普遍有效的訊號）。

    這是簡單的統計判讀，不是複雜模型——標準差門檻(15/25個百分點)是合理但
    主觀的起始值，之後可以根據實際觀察到的分佈調整，不是寫死不能改的鐵律。

    回傳DataFrame[濾網條件, 窗口數, 命中率平均%, 命中率標準差, 穩定性判讀]，
    依命中率平均由高到低排序。
    """
    if walkforward_df.empty:
        return pd.DataFrame()
    out = []
    for f, grp in walkforward_df.groupby('濾網條件'):
        rates = grp['3日勝率%']
        n = len(rates)
        mean_rate = round(rates.mean(), 1)
        std_rate = round(rates.std(), 1) if n > 1 else None
        if n < 2:
            verdict = "⚪ 只有1個窗口，還無法判斷穩定性"
        elif std_rate is not None and std_rate < 15:
            verdict = "🟢 穩定（高原區，各期間表現接近）"
        elif std_rate is not None and std_rate < 25:
            verdict = "🟡 中等波動（部分期間效果較弱）"
        else:
            verdict = "🔴 高度不穩定（疑似孤峰，可能只在特定市況有效）"
        out.append({
            '濾網條件': f, '窗口數': n, '命中率平均%': mean_rate,
            '命中率標準差': std_rate if std_rate is not None else '—',
            '穩定性判讀': verdict,
        })
    return pd.DataFrame(out).sort_values('命中率平均%', ascending=False)
