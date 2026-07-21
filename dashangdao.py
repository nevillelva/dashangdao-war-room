# ==============================================================================
# 54088 戰情室 V156 — 量化擴張 · 神盾修復版
# 相對 V155 的變更請見檔尾 CHANGELOG
# ==============================================================================
import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, time as dt_time
import re
import time
import json
import os
import io
import requests
import warnings
import urllib3
import concurrent.futures
from openai import OpenAI
import tempfile
import sqlite3
import threading
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 【新增】讓子執行緒也能使用 st.cache_data（否則多執行緒掃描時快取會失效並噴警告）
try:
    from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
except Exception:  # 舊版 Streamlit 相容
    def add_script_run_ctx(*a, **k): return None
    def get_script_run_ctx(*a, **k): return None

# ==============================================================================
# 一、 系統最高安全防禦與法規合規宣告
# ==============================================================================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings('ignore')

GOV_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*"
}

USER_DB_FILE = "54088_database.json"
SQLITE_DB_FILE = "54088_inst_history.db"

# 【任務一】API 錯誤極致透明化：統一錯誤字串，禁止用 0.0 帶過
# 【V160】建置版本標記 —— 側邊欄會顯示。用途：一眼確認雲端跑的是不是最新檔，
# 避免「回報的bug其實早就修好了，只是部署的是舊版」這種來回。
# 【V160】版本標記機制：總指揮官要求「每次更新都要有版本，才知道有沒有複製到正確版本」。
# 這是唯一的版本真相來源——每次交付新檔案時必須同步更新這兩行，側邊欄會顯示。
BUILD_VERSION = "作戰室 正式版 v1.0 (2026-07-21 Round28)"
BUILD_NOTES = "情報注入面板：自動偵測改為確認制(避免撞名誤判)＋新增已綁定標的批次移除/清空"

# 【V160】掃描條件代號 → 完整條件敘述 的對照表。
# 總指揮官回報：血統只顯示「查13」看不出當初是用什麼條件掃到的。
# 這張表在建構掃描條件清單時填入，戰卡渲染時用來補上完整說明（滑鼠移上去可看）。
SCAN_COMMAND_MAP = {}

# 【V160 新增】出場原因中文對照。總指揮官回報畫面上直接顯示英文代碼（take_profit等）
# 不好判讀。這裡集中管理一份對照表，所有顯示出場原因的地方都呼叫 _exit_reason_zh()，
# 不要各自寫自己的翻譯（避免以後改一個地方漏改別的地方，字典分散在多處會對不齊）。
EXIT_REASON_ZH = {
    'stop_loss': '🔴 停損',
    'take_profit': '🟢 停利',
    'trail_stop': '📈 移動停利',
    'manual': '🧪 手動平倉',
    'duplicate_skip': '⏭️ 重複略過',
    'duplicate_holding_cleanup': '🧹 重複持倉清除',
    'duplicate_cleanup_0719': '🧹 歷史重複清理',
    'duplicate_closed_cleanup_0719': '🧹 歷史重複清理',
}


def _exit_reason_zh(reason):
    """把出場原因代碼轉成中文。代碼不在對照表裡就照原樣顯示，不隱藏、不猜。"""
    if not reason:
        return '—'
    return EXIT_REASON_ZH.get(reason, str(reason))


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
            return df   # 上色失敗就退回原始表格，不讓功能整個掛掉

# 【V160 新增】主力成本校正輸入用的常見券商分點清單，供下拉選單挑選，避免手打錯字
# （籌碼K線上常見的大型券商/分點；不是窮舉全部分點，清單外的可選「其他」手動輸入）。
COMMON_BROKER_BRANCHES = [
    "凱基-台北", "凱基-信義", "凱基-松山", "元大-台北", "元大-桃園",
    "富邦-新店", "富邦-建成", "國泰-敦南", "國泰-中和",
    "群益金鼎-三重", "永豐金-建成", "永豐金-中山",
    "統一-嘉義", "統一-南屯", "新光", "國票-敦北",
    "花旗環球", "港商麥格理", "摩根士丹利", "美林", "瑞銀",
    "香港上海匯豐", "台灣摩根大通", "美商高盛",
]

ERR_RATE_LIMIT = "[⛔ API限流]"
ERR_NO_DATA    = "[📭 官方未公佈]"
ERR_CONN       = "[🔌 連線失敗]"
# 【V160 新增】FinMind 部分資料集限 backer/sponsor 付費方案（例如股東持股分級表
# TaiwanStockHoldingSharesPer＝千張大戶、台股分點資料表＝券商分點）。
# 這種情況原本會被歸類成「限流」，標籤會誤導成「等一下再查就好」，
# 實際上再等也不會有資料——必須用獨立標籤講清楚。
ERR_PERMISSION = "[🔒 需付費方案]"

# 估價模型參數（可自行調整）
PE_FAIR_MULT   = 15.0   # 合理本益比
PE_DREAM_MULT  = 20.0   # 樂觀本益比
YIELD_DEF_RATE = 0.05   # 殖利率防守價：以 5% 殖利率回推
PE_LANDMINE    = 30.0   # 地雷觸發本益比門檻
DEF_LINE_ATR_MULT = 0.5  # 防守線 = MA5 - 此倍數×ATR（V158 起具名常數，讓回測能引用同一個預設值做驗證）


class FinMindAPIError(Exception):
    def __init__(self, reason, detail=""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


def _expand_blood_line(bl):
    """
    【V160】把血統字串裡的「查N」換成完整條件敘述。

    總指揮官回報：只看到「查13」不知道當初是用什麼條件掃到這檔的，
    之後要回頭檢討「哪種條件選出來的股票勝率高」就無從查起。
    對照表若還沒建好（例如尚未按過掃描），就原樣回傳，不編造。
    """
    if not bl:
        return ""
    out = str(bl)
    for tag, desc in sorted(SCAN_COMMAND_MAP.items(), key=lambda kv: -len(kv[0])):
        if tag in out:
            out = out.replace(tag, f"{tag}（{desc}）")
    return out


def get_current_or_last_trading_date():
    """
    【V160 新增】回傳「今天若是交易日就用今天，否則往前找到最近的交易日」。

    get_last_trading_date() 是固定從「昨天」起算往前找，適合用在「要抓已收盤資料」
    的情境；但建倉日不一樣 —— 平日盤中/盤後建倉就該記今天。
    週六日或非交易時段執行時，才往前retreat到最近交易日，
    避免把建倉日寫成 07/18(六) 這種沒開盤的日期。
    （註：這裡只處理週末，國定假日仍可能落空，屬已知限制。）
    """
    d = datetime.now()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime('%Y-%m-%d')


def get_last_trading_date():
    d = datetime.now() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime('%Y-%m-%d')


@st.cache_resource
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

# 【V160 新增】記住每檔股票上次成功的市場後綴（.TW上市 或 .TWO上櫃）。
# 這是「開機/重整要等5-10分鐘」的第二個根因：get_real_stock_data_yfinance
# 原本每次都從 .TW 開始試，對上櫃股來說前兩次注定失敗、但還是要各自等到逾時
# 才換下一種格式。這個 dict 活在 process 層級（不是 session，st.cache_data的
# 180秒過期也不影響它），一旦某檔成功過就記住，之後同一個容器的生命週期內
# 都會直接先試對的格式，省掉重複的失敗嘗試。純粹是加速用的提示，不影響正確性
# ——就算猜錯，原本的四種嘗試順序還是會照跑一次，只是排列順序變了。
_EXT_HINT = {}

# ==============================================================================
# 二、 資料庫架構（SQLite + 原子寫入 JSON + 防崩潰鎖）
# ==============================================================================
DB_LOCK = threading.Lock()


def get_db_conn():
    conn = sqlite3.connect(SQLITE_DB_FILE, check_same_thread=False, timeout=15)
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


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


def init_sqlite_db():
    with DB_LOCK:
        conn = get_db_conn()
        _ensure_schema(conn)
        return conn


SQLITE_CONN = init_sqlite_db()

_LAST_GOOD_LOCK = threading.Lock()
_LAST_GOOD_REVENUE = {}


def safe_upsert_big_holder(code, date_str, percent_value):
    is_valid = (percent_value is not None and percent_value != ''
                and isinstance(percent_value, (int, float)) and percent_value > 0.0)
    if not is_valid:
        return False
    local_ok = False
    with DB_LOCK:
        try:
            SQLITE_CONN.execute("""
                INSERT INTO big_holder_history (code, date, percent) VALUES (?, ?, ?)
                ON CONFLICT(code, date) DO UPDATE SET percent = excluded.percent
            """, (code, date_str, percent_value))
            SQLITE_CONN.commit()
            local_ok = True
        except Exception:
            local_ok = False
    # 【V160 雙寫】雲端寫失敗不影響本機結果（盡力而為，不阻斷主流程）
    sb_upsert_big_holder(code, date_str, percent_value)
    return local_ok


def get_latest_big_holder(code):
    with DB_LOCK:
        try:
            cursor = SQLITE_CONN.cursor()
            cursor.execute(
                "SELECT date, percent FROM big_holder_history WHERE code = ? AND percent > 0 ORDER BY date DESC LIMIT 1",
                (code,))
            row = cursor.fetchone()
            if row:
                return {'date': row[0], 'percent': row[1]}
            return None
        except Exception:
            return None


def get_db_stats():
    with DB_LOCK:
        try:
            cursor = SQLITE_CONN.cursor()
            cursor.execute("SELECT COUNT(DISTINCT date) FROM inst_holding")
            days = cursor.fetchone()[0]
            cursor.execute("SELECT date, COUNT(symbol) FROM inst_holding GROUP BY date ORDER BY date DESC LIMIT 5")
            details = cursor.fetchall()
            return days, details
        except Exception:
            return 0, []


def get_inst_data_from_db(symbol, limit=30):
    """【擴充】預設抓 30 日，供連續買賣超 VWAP 回推使用。"""
    with DB_LOCK:
        try:
            df = pd.read_sql(
                'SELECT * FROM inst_holding WHERE symbol=? ORDER BY date DESC LIMIT ?',
                SQLITE_CONN, params=(symbol, limit))
            return df
        except Exception:
            return pd.DataFrame()


# ==============================================================================
# 二之二、Supabase 雲端大腦 — 雙軌架構 (V160 新增)
# ------------------------------------------------------------------------------
# 設計哲學（呼應總指揮官的需求：資料要穩、但掃描不能變慢）：
#   - 讀取：一律走本機 SQLite（毫秒級），掃描 300 檔不受網路延遲拖累
#   - 寫入：同時寫本機 SQLite + Supabase（雙寫），SQLite 保這次 session 的速度，
#           Supabase 保長期不被 Streamlit Cloud 容器重開清空
#   - 開機：從 Supabase 同步最近的籌碼資料回填本機 SQLite，補回被清空的資料
#   - 降級保護：secrets 沒設定 / 套件沒安裝 / 連線失敗時，自動退回「純本機 SQLite
#           模式」，程式照常運作、絕不崩潰。使用者晚點補上 secrets 就自動生效。
#
# 重要：Supabase 的寫入採「盡力而為」——雲端寫失敗不影響本機寫成功，也不影響
#       主流程，只在後台記一筆警告。本機才是這次 session 的權威來源。
# ==============================================================================
SUPABASE_ENABLED = False
SUPABASE_CONN = None
_SUPABASE_INIT_MSG = "尚未初始化"


def _init_supabase():
    """
    嘗試建立 Supabase 連線。任何一步失敗都安全降級為 None，並記錄原因。
    回傳 (client_or_None, enabled_bool, message)。
    """
    try:
        from supabase import create_client
    except Exception:
        return None, False, "supabase 套件未安裝（純本機模式運行）"
    try:
        url = st.secrets["supabase"]["SUPABASE_URL"]
        key = st.secrets["supabase"]["SUPABASE_KEY"]
    except Exception:
        return None, False, "secrets 未設定 supabase 區塊（純本機模式運行）"
    if not url or not key or "你的專案" in str(url):
        return None, False, "secrets 的 SUPABASE_URL/KEY 尚未填入有效值（純本機模式運行）"
    try:
        client = create_client(url, key)
        return client, True, "Supabase 雙軌已啟用"
    except Exception as e:
        return None, False, f"Supabase 連線建立失敗，降級純本機模式：{e}"


@st.cache_resource
def get_supabase():
    """全域快取的 Supabase client（含啟用狀態與訊息）。"""
    client, enabled, msg = _init_supabase()
    return {"client": client, "enabled": enabled, "msg": msg}


_sb_pack = get_supabase()
SUPABASE_CONN = _sb_pack["client"]
SUPABASE_ENABLED = _sb_pack["enabled"]
_SUPABASE_INIT_MSG = _sb_pack["msg"]


def _sb_safe(fn, *args, **kwargs):
    """
    包裝所有 Supabase 呼叫：未啟用直接回 None，發生例外只記警告不中斷主流程。
    回傳 (ok_bool, result_or_None)。
    """
    if not SUPABASE_ENABLED or SUPABASE_CONN is None:
        return False, None
    try:
        return True, fn(*args, **kwargs)
    except Exception as e:
        try:
            print(f"[Supabase 警告] {getattr(fn, '__name__', 'call')} 失敗: {e}")
        except Exception:
            pass
        return False, None


# ---- 雙寫：三大法人籌碼 ----
def sb_upsert_inst_holding(rows):
    """
    rows: list of dict，每筆含 date/symbol/foreign_buy/trust_buy/dealer_buy/margin。
    對應 Supabase inst_holding 表，用 (date, symbol) 為衝突鍵做 upsert。
    【V160】分批寫入（每批 500 筆），避免單次 payload 過大被拒或逾時。
    """
    def _do_batch(batch_payload):
        return SUPABASE_CONN.table("inst_holding").upsert(batch_payload, on_conflict="date,symbol").execute()

    payload = []
    for r in rows:
        payload.append({
            "date": r["date"], "symbol": r["symbol"],
            "foreign_buy": r.get("foreign_buy", 0), "trust_buy": r.get("trust_buy", 0),
            "dealer_buy": r.get("dealer_buy", 0), "margin": r.get("margin", 0),
            "big_holder": r.get("big_holder", 0), "big_holder_date": r.get("big_holder_date", ""),
        })

    all_ok = True
    BATCH = 500
    for i in range(0, len(payload), BATCH):
        ok, _ = _sb_safe(_do_batch, payload[i:i + BATCH])
        all_ok = all_ok and ok
    return all_ok


# ---- 雙寫：千張大戶 ----
def sb_upsert_big_holder(code, date_str, percent_value):
    def _do():
        data = {"code": code, "date": date_str, "percent": percent_value}
        return SUPABASE_CONN.table("big_holder_history").upsert(data, on_conflict="code,date").execute()
    ok, _ = _sb_safe(_do)
    return ok


# ---- 開機同步：從 Supabase 回填本機 SQLite ----
def _sb_fetch_all(table_name, gte_col=None, gte_val=None, page_size=1000):
    """
    【V160 修復】supabase-py 單次查詢預設最多回傳 1000 筆。這裡用 .range() 分頁，
    一批一批撈直到撈完，突破 1000 筆上限。回傳所有 row 的 list。
    任何一批失敗就停止並回傳目前已撈到的資料（盡力而為，不中斷主流程）。
    """
    all_rows = []
    start = 0
    while True:
        def _do():
            q = SUPABASE_CONN.table(table_name).select("*")
            if gte_col is not None and gte_val is not None:
                q = q.gte(gte_col, gte_val)
            # range 是包含兩端的閉區間，所以每批抓 page_size 筆
            return q.range(start, start + page_size - 1).execute()
        ok, res = _sb_safe(_do)
        if not ok or res is None or not getattr(res, "data", None):
            break
        batch = res.data
        all_rows.extend(batch)
        if len(batch) < page_size:   # 最後一批（不足一頁）→ 撈完了
            break
        start += page_size
        if start > 500000:           # 安全上限，避免異常情況無限迴圈
            break
    return all_rows


def sync_from_supabase_on_boot(days_back=None, progress_cb=None):
    """
    App 開機時呼叫一次：把 Supabase 上最近 days_back 天的籌碼 + 大戶資料，
    回填本機 SQLite。這樣就算 Streamlit Cloud 容器把本機 DB 清空，開機一次就補回。
    只在 Supabase 啟用時執行；未啟用直接跳過（純本機模式）。
    回傳補回的筆數 (inst_rows, bh_rows)，失敗回 (0, 0)。

    【V160】days_back 改為可從 system_config 調整（側邊欄「⚙️開機回填天數設定」），
    預設仍是45天。總指揮官若覺得每次重開容器等太久，可以縮小這個天數換取更快登入——
    這只影響「本機讀取快取」的涵蓋範圍，Supabase 雲端的完整歷史不受影響，
    之後要看更久的資料，個股同步/查詢仍會即時從雲端補齊。

    【V160】progress_cb：可選的進度回報函式，簽名 progress_cb(pct, label)，
    pct 是 0.0~1.0。總指揮官要求把 spinner 換成百分比進度條，這是資料來源。
    沒傳就完全不影響原本行為（純本機模式或排程呼叫時就不需要）。
    """
    def _report(pct, label):
        if progress_cb:
            try:
                progress_cb(pct, label)
            except Exception:
                pass   # 進度回報失敗不該影響實際同步
    if days_back is None:
        try:
            days_back = int(float(sb_get_config('boot_refill_days', '45')))
        except (TypeError, ValueError):
            days_back = 45
    if not SUPABASE_ENABLED or SUPABASE_CONN is None:
        return 0, 0
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
    inst_rows = bh_rows = 0
    _report(0.05, "連線雲端中")

    # 【V160 修復】用分頁撈取，把 45 天內全部籌碼撈回來（不再只有第一批1000筆）
    _report(0.15, "下載籌碼資料中")
    inst_data = _sb_fetch_all("inst_holding", gte_col="date", gte_val=cutoff)
    _report(0.45, f"寫入籌碼資料（{len(inst_data):,} 筆）")
    if inst_data:
        try:
            # 【V160 效能修復】總指揮官回報：每次登入都要轉2-3分鐘。根因是這裡原本
            # 逐列 Python 迴圈呼叫 SQLITE_CONN.execute()——單檔同步（round 4修復後）
            # 每次會寫入40天歷史，用久了 inst_holding 累積到45天視窗內可能有上萬筆，
            # 逐筆 execute() 的 Python/SQLite 呼叫開銷疊加起來就是這2-3分鐘的來源。
            # 改用 executemany() 把整批資料一次性交給 SQLite 底層處理，減少的是
            # Python 層的呼叫次數，不是資料量本身——效果通常是數十倍加速。
            _rows_tuples = [
                (r.get("date"), r.get("symbol"), r.get("foreign_buy", 0), r.get("trust_buy", 0),
                 r.get("dealer_buy", 0), r.get("margin", 0), r.get("big_holder", 0),
                 r.get("big_holder_date", ""))
                for r in inst_data
            ]
            with DB_LOCK:
                SQLITE_CONN.executemany('''
                    INSERT INTO inst_holding (date, symbol, foreign_buy, trust_buy, dealer_buy, margin, big_holder, big_holder_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date, symbol) DO UPDATE SET
                        foreign_buy=excluded.foreign_buy, trust_buy=excluded.trust_buy,
                        dealer_buy=excluded.dealer_buy, margin=excluded.margin
                ''', _rows_tuples)
                SQLITE_CONN.commit()
            inst_rows = len(inst_data)
        except Exception as e:
            print(f"[Supabase 開機同步] 回填 inst_holding 失敗: {e}")

    _report(0.70, "下載大戶資料中")
    bh_data = _sb_fetch_all("big_holder_history", gte_col="date", gte_val=cutoff)
    _report(0.85, f"寫入大戶資料（{len(bh_data):,} 筆）")
    if bh_data:
        try:
            # 同樣改用 executemany，過濾邏輯（percent>0）先在 Python list 端做完
            _bh_tuples = [(r.get("code"), r.get("date"), r.get("percent"))
                         for r in bh_data if r.get("percent") and r.get("percent") > 0]
            if _bh_tuples:
                with DB_LOCK:
                    SQLITE_CONN.executemany('''
                        INSERT INTO big_holder_history (code, date, percent) VALUES (?, ?, ?)
                        ON CONFLICT(code, date) DO UPDATE SET percent=excluded.percent
                    ''', _bh_tuples)
                    SQLITE_CONN.commit()
            bh_rows = len(bh_data)
        except Exception as e:
            print(f"[Supabase 開機同步] 回填 big_holder_history 失敗: {e}")

    return inst_rows, bh_rows


# ---- 系統設定表：可在網頁上調整的參數（例如每日系統選股總額） ----
def sb_get_config(config_key, default=None):
    """讀系統設定；Supabase 未啟用或查無資料時回 default。"""
    def _do():
        return SUPABASE_CONN.table("system_config").select("config_value").eq("config_key", config_key).limit(1).execute()
    ok, res = _sb_safe(_do)
    if ok and res is not None and getattr(res, "data", None):
        try:
            return res.data[0]["config_value"]
        except Exception:
            return default
    return default


def push_all_local_to_supabase(progress_cb=None):
    """
    【V160】手動補推：把本機 SQLite 的全部籌碼 + 大戶資料補推到 Supabase。
    用途：雙寫功能上線前匯入的舊資料、或 Supabase 當機期間漏寫的資料，一鍵補平。
    upsert 以主鍵為衝突鍵，重複推不會產生重複列（冪等）。
    回傳 (inst_pushed, bh_pushed)。
    """
    if not SUPABASE_ENABLED or SUPABASE_CONN is None:
        return 0, 0
    inst_pushed = bh_pushed = 0

    # 籌碼
    with DB_LOCK:
        try:
            inst_df = pd.read_sql('SELECT * FROM inst_holding', SQLITE_CONN)
        except Exception:
            inst_df = pd.DataFrame()
    if not inst_df.empty:
        rows = inst_df.to_dict('records')
        BATCH = 500
        for i in range(0, len(rows), BATCH):
            batch = rows[i:i + BATCH]
            def _do_inst():
                return SUPABASE_CONN.table("inst_holding").upsert(batch, on_conflict="date,symbol").execute()
            ok, _ = _sb_safe(_do_inst)
            if ok:
                inst_pushed += len(batch)
            if progress_cb:
                progress_cb('inst', min(i + BATCH, len(rows)), len(rows))

    # 大戶
    with DB_LOCK:
        try:
            bh_df = pd.read_sql('SELECT * FROM big_holder_history WHERE percent > 0', SQLITE_CONN)
        except Exception:
            bh_df = pd.DataFrame()
    if not bh_df.empty:
        rows = bh_df.to_dict('records')
        BATCH = 500
        for i in range(0, len(rows), BATCH):
            batch = rows[i:i + BATCH]
            def _do_bh():
                return SUPABASE_CONN.table("big_holder_history").upsert(batch, on_conflict="code,date").execute()
            ok, _ = _sb_safe(_do_bh)
            if ok:
                bh_pushed += len(batch)
            if progress_cb:
                progress_cb('bh', min(i + BATCH, len(rows)), len(rows))

    return inst_pushed, bh_pushed


def log_intel_performance(symbol, source, tag):
    """
    【V160 B#13】情報準確度追蹤：情報輸入當下只記錄一筆待辦，base_price 留 0，
    之後由「計算情報準確度」時再補抓歷史基準價（用 intel_date 當天的收盤）。
    【V160 效能修復】不在儲存當下同步抓 yfinance 報價——10檔各抓一次會讓儲存卡好幾分鐘。
    """
    def _do():
        data = {"symbol": symbol, "source": source, "tag": tag,
                "intel_date": datetime.now().strftime('%Y-%m-%d'), "base_price": 0.0}
        return SUPABASE_CONN.table("intel_performance").insert(data).execute()
    _sb_safe(_do)


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


def synthesize_three_way_review(card_text, review_a, review_b, review_c):
    """
    【V160 B#12】三方會審總結：把原始戰卡數據 + 三份外部AI分析，
    餵給 NVIDIA API 做整合總結（在戰情室內完成，不用再開外部AI）。
    """
    if not NVIDIA_API_KEY:
        return "⚠️ NVIDIA 未連線（API key 未設定），無法產生總結。"
    try:
        client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=NVIDIA_API_KEY)
    except Exception as e:
        return f"⚠️ NVIDIA client 建立失敗：{e}"
    prompt = (f"以下是一檔股票的原始戰情數據，以及三份來自不同AI的分析報告。"
              f"請你以首席戰略幕僚身分，整合三方觀點，指出共識與分歧，並給出最終明確的操作結論。\n\n"
              f"=== 原始戰情數據 ===\n{card_text}\n\n"
              f"=== A分析 ===\n{review_a}\n\n=== B分析 ===\n{review_b}\n\n=== C分析 ===\n{review_c}\n\n"
              f"請用繁體中文輸出：【三方共識】、【三方分歧】、【最終操作結論與進出場價位】")
    for model_id in get_nim_models():
        try:
            completion = client.chat.completions.create(
                model=model_id,
                messages=[{"role": "system", "content": "你是台灣股市首席戰略幕僚，整合多方分析給出果斷結論。繁體中文。"},
                          {"role": "user", "content": prompt}],
                # 【V160 修復】15s 對推理型模型太短：總指揮官回報 5 個模型有 4 個
                # 「連線逾時(15s)」。DeepSeek/GLM 這類模型跑一份戰略分析
                # 經常要 30~60 秒，逾時是被我們自己切斷的，不是模型壞掉。
                temperature=0.3, max_tokens=1500, timeout=90
            )
            return completion.choices[0].message.content
        except Exception:
            continue
    return "⚠️ NVIDIA 三個模型都無法使用，無法產生總結。"


def compute_forward_return(symbol, base_price, intel_date_str, trading_days):
    """
    【V160 B#13】算某檔股票從 intel_date 起算、trading_days 個交易日後的報酬率。
    無未來函數：用歷史股價，若未到期（資料不足）回 None。
    【V160】base_price 為 0 時（儲存當下沒抓），從歷史補抓 intel_date 當天收盤當基準。
    """
    try:
        tk = _yf_ticker(f"{symbol}.TW")
        hist = tk.history(period="6mo")
        if hist.empty:
            tk = _yf_ticker(f"{symbol}.TWO")
            hist = tk.history(period="6mo")
        hist = hist.dropna(subset=['Close'])
        if hist.empty:
            return None
        hist.index = hist.index.strftime('%Y-%m-%d')
        dates = list(hist.index)
        after = [d for d in dates if d >= intel_date_str]
        if not after:
            return None
        # base_price 為 0 → 用 intel_date 當天（或次一交易日）收盤補
        if not base_price or base_price <= 0:
            base_price = float(hist.loc[after[0], 'Close'])
        if base_price <= 0 or len(after) <= trading_days:
            return None   # 未到期或無效基準
        target_price = float(hist.loc[after[trading_days], 'Close'])
        return round((target_price - base_price) / base_price * 100, 2)
    except Exception:
        return None


def get_intel_accuracy_summary(custom_days=None):
    """
    【V160 B#13】情報來源準確度彙總：依「來源」分組，算 3/10/20 日（+自訂天數）平均報酬與勝率。
    從 Supabase intel_performance 讀所有紀錄，即時補算報酬（無未來函數）。
    """
    if not SUPABASE_ENABLED:
        return pd.DataFrame(), pd.DataFrame()
    rows = _sb_fetch_all("intel_performance")
    if not rows:
        return pd.DataFrame(), pd.DataFrame()

    windows = [3, 10, 20]
    if custom_days and custom_days not in windows:
        windows.append(custom_days)

    enriched = []
    for r in rows:
        sym, src, tag = r.get('symbol'), r.get('source', '未知'), r.get('tag', '')
        bp, idate = r.get('base_price'), r.get('intel_date')
        rec = {'symbol': sym, 'source': src, 'tag': tag}
        for w in windows:
            rec[f'ret_{w}'] = compute_forward_return(sym, bp, idate, w)
        enriched.append(rec)
    edf = pd.DataFrame(enriched)

    def _summarize(group_col):
        out = []
        for key, sub in edf.groupby(group_col):
            row = {group_col: key, '樣本數': len(sub)}
            for w in windows:
                col = f'ret_{w}'
                valid = sub[col].dropna()
                if len(valid) > 0:
                    row[f'{w}日勝率%'] = round((valid > 0).mean() * 100, 1)
                    row[f'{w}日均報酬%'] = round(valid.mean(), 2)
                else:
                    row[f'{w}日勝率%'] = None
                    row[f'{w}日均報酬%'] = None
            out.append(row)
        return pd.DataFrame(out)

    return _summarize('source'), _summarize('tag')


def get_manual_vs_system_pk():
    """
    【V160 B#14】手動加入 vs 系統查詢 勝率PK：從 watchlist_entry_log 讀取，
    依 source_type（manual vs 查X）分兩組，算「加入日到今天」的報酬率與勝率。
    """
    if not SUPABASE_ENABLED:
        return pd.DataFrame()
    rows = _sb_fetch_all("watchlist_entry_log")
    if not rows:
        return pd.DataFrame()

    manual_rets, system_rets = [], []
    for r in rows:
        sym, stype, edate, eprice = r.get('symbol'), r.get('source_type', ''), r.get('entry_date'), r.get('entry_price')
        try:
            tk = _yf_ticker(f"{sym}.TW")
            hist = tk.history(period="1y").dropna(subset=['Close'])
            if hist.empty:
                tk = _yf_ticker(f"{sym}.TWO")
                hist = tk.history(period="1y").dropna(subset=['Close'])
            if hist.empty:
                continue
            # entry_price 為 0 → 從歷史補 entry_date 當天（或次一交易日）收盤
            if not eprice or eprice <= 0:
                hist_idx = hist.copy()
                hist_idx.index = hist_idx.index.strftime('%Y-%m-%d')
                after = [d for d in hist_idx.index if d >= edate]
                if not after:
                    continue
                eprice = float(hist_idx.loc[after[0], 'Close'])
            if not eprice or eprice <= 0:
                continue
            cur_price = float(hist['Close'].iloc[-1])
            ret = (cur_price - eprice) / eprice * 100
            if stype == 'manual':
                manual_rets.append(ret)
            else:
                system_rets.append(ret)
        except Exception:
            continue

    def _stats(rets, label):
        if not rets:
            return {'選股方式': label, '樣本數': 0, '平均報酬%': None, '勝率%': None}
        import statistics
        return {'選股方式': label, '樣本數': len(rets),
                '平均報酬%': round(statistics.mean(rets), 2),
                '勝率%': round(sum(1 for x in rets if x > 0) / len(rets) * 100, 1)}

    return pd.DataFrame([_stats(manual_rets, '👤 手動選股'), _stats(system_rets, '🤖 系統查詢')])


def log_watchlist_entry(symbol, source_type):
    """【V160 B#14】記錄一檔加入雷達的來源(manual/查X)、日期，供勝率PK。
    【V160 效能】不在當下抓報價（勝率PK計算時再從歷史補 entry_date 收盤），避免加入卡頓。"""
    def _do():
        data = {"symbol": symbol, "source_type": source_type,
                "entry_date": datetime.now().strftime('%Y-%m-%d'), "entry_price": 0.0, "is_active": 1}
        return SUPABASE_CONN.table("watchlist_entry_log").insert(data).execute()
    _sb_safe(_do)


def sb_set_config(config_key, config_value, description=""):
    def _do():
        data = {"config_key": config_key, "config_value": str(config_value), "description": description}
        return SUPABASE_CONN.table("system_config").upsert(data, on_conflict="config_key").execute()
    ok, _ = _sb_safe(_do)
    return ok


# ==============================================================================
# 二之四、系統自主選股模擬倉引擎 (V160 A階段)
# ------------------------------------------------------------------------------
# 全自動選股+進出場，同時做多、做空兩個模擬倉，比較勝率更客觀。
# 兩段式排程（由 GitHub Actions 觸發，也可在網頁手動觸發測試）：
#   1. 22:00 訊號產生：全市場掃描，選出做多候選(建議進攻)與做空候選(建議撤退/偏空)
#   2. 隔日 9:01 執行：用開盤價進場（前置 8:55 總經閘門檢查，劇變則暫緩）
# 出場規則B（全自動）：多單跌破防守線停損 / 觸及短線停利點停利；空單反向。
# ==============================================================================
def get_system_capital():
    """讀每日系統選股總額（可在網頁調整，存 system_config）。預設30萬。"""
    v = sb_get_config('system_pick_daily_capital', '300000')
    try:
        return int(float(v))
    except Exception:
        return 300000


def get_trail_config():
    """
    【V160 延伸4】ATR 移動停利設定。存 system_config，可在網頁開關與調參。

    為什麼要有這個功能：原本的出場規則B是「固定停利點」，一碰到就出場，
    這會在真正的大波段行情裡提早砍掉獲利（賺賠比被壓低）。移動停利改成
    「隨著價格往有利方向走，停損線跟著往上抬，只有回檔超過 N×ATR 才出場」，
    讓賺的單能抱久一點。

    注意：移動停利提高的是「賺賠比」，不是「勝率」——實務上它甚至可能小幅
    降低勝率（因為部分原本會碰到固定停利的單，改成回檔出場時價格較低）。
    所以做成可開關，讓總指揮官能自己A/B比較，而不是我單方面替你決定。

    回傳 dict：enabled（是否啟用）、mult（回檔幾倍ATR出場）、activate_mult（獲利幾倍ATR才啟動）。
    """
    enabled = sb_get_config('trail_stop_enabled', '0') == '1'
    try:
        mult = float(sb_get_config('trail_stop_mult', '2.0'))
    except (TypeError, ValueError):
        mult = 2.0
    try:
        act = float(sb_get_config('trail_stop_activate_mult', '1.0'))
    except (TypeError, ValueError):
        act = 1.0
    return {'enabled': enabled, 'mult': mult, 'activate_mult': act}


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


def sb_update_peak_price(position_id, peak):
    """把最新的進場後極值寫回 Supabase，讓移動停利線能單調前進。"""
    def _do():
        return (SUPABASE_CONN.table("system_portfolio")
                .update({"peak_price": peak}).eq("id", position_id).execute())
    _sb_safe(_do)


def sb_insert_system_portfolio(entries):
    """批次寫入系統模擬倉持倉。"""
    if not entries:
        return False
    def _do():
        return SUPABASE_CONN.table("system_portfolio").insert(entries).execute()
    ok, _ = _sb_safe(_do)
    return ok


def sb_get_system_holdings(status='holding'):
    """讀系統模擬倉持倉。"""
    def _do():
        return SUPABASE_CONN.table("system_portfolio").select("*").eq("status", status).execute()
    ok, res = _sb_safe(_do)
    if ok and res is not None and getattr(res, "data", None):
        return res.data
    return []


def sb_get_system_occupied():
    """
    【V160 修復】取得「已被佔用」的標的集合，同時涵蓋 holding（已持倉）與 pending（待執行）。

    為什麼需要這個：原本選股只排除 status='holding' 的標的，但排程流程是
    22:00 選股寫入 pending → 隔日 9:01 才轉 holding。若同一天選股跑了兩次
    （手動測試 + 排程各一次），第二次看不到第一次留下的 pending 紀錄，
    就會對同一檔重複建倉，隔日兩筆一起轉 holding、之後各出場一次
    （症狀：Telegram 出場通知同一檔出現兩次、獲利%完全相同）。

    回傳 (occupied_long, occupied_short) 兩個 set。
    """
    def _do():
        return (SUPABASE_CONN.table("system_portfolio")
                .select("symbol,side,status")
                .in_("status", ["holding", "pending"]).execute())
    ok, res = _sb_safe(_do)
    rows = res.data if (ok and res is not None and getattr(res, "data", None)) else []
    occ_long = {r.get('symbol') for r in rows if r.get('side') == 'long' and r.get('symbol')}
    occ_short = {r.get('symbol') for r in rows if r.get('side') == 'short' and r.get('symbol')}
    return occ_long, occ_short


def sb_log_system_run(run_date, stage, picked, executed, gate_status, note):
    def _do():
        data = {"run_date": run_date, "stage": stage, "picked_count": picked,
                "executed_count": executed, "gate_status": gate_status, "note": note}
        return SUPABASE_CONN.table("system_run_log").insert(data).execute()
    _sb_safe(_do)


def system_select_candidates(config_payload, scan_pool, top_n=5):
    """
    【V160 A階段】系統自動選股：掃描 scan_pool，回傳 (long_candidates, short_candidates)。
    做多候選：決策判定「偏多攻擊」(評分>=3)，排除地雷/處置風險。
    做空候選：決策判定「偏空防守」(評分<=-3)，排除處置風險。
    各依評分絕對值排序取前 top_n。
    【V160 修復】排除已經持有中的標的（同方向），避免重複執行時對同一檔重複加碼、
    產生像「加高被買兩次、進場價還不一樣」這種重複持倉。
    【V160 修復2】排除範圍從「只看 holding」擴大為「holding + pending」，
    因為 pending（已選股、待隔日開盤執行）也已經佔用了這檔標的的名額，
    否則同一天選股跑兩次會產生兩筆重複倉。
    """
    held_long, held_short = sb_get_system_occupied()

    longs, shorts = [], []
    for code in scan_pool:
        c = calculate_signals_worker(code, config_payload)
        if not c or c.get('error'):
            continue
        sig = c.get('signal_text', '')
        score = c.get('score', 0)
        d_risk = (c.get('disposal_risk') or {}).get('level', 'none')
        if d_risk == 'high':      # 排除處置風險高的
            continue
        if '偏多攻擊' in sig and score >= 3 and not c.get('landmine') and code not in held_long:
            longs.append(c)
        elif '偏空防守' in sig and score <= -3 and code not in held_short:
            shorts.append(c)
    longs.sort(key=lambda x: x.get('score', 0), reverse=True)
    shorts.sort(key=lambda x: x.get('score', 0))
    return longs[:top_n], shorts[:top_n]


def system_build_entries(candidates, side, run_date, total_capital, trigger_source='manual'):
    """把候選轉成進場明細（依檔數平分資金，用開盤價/現價當進場價）。
    【V160】同時記錄選股理由，供之後分析高勝率標的的共同特徵。
    【V160 修復】防守線/停利點原本不分方向，直接套用做多式技術指標（MA5-0.5ATR當防守、
    price+1ATR當停利），這對做空來說方向是顛倒的——做空的防守線應該在進場價「上方」
    （漲破才停損），停利點應該在「下方」（跌破才停利）。現在依 side 給對應方向的正確數值，
    跟 system_check_exits 實際使用的出場規則（多單 defl<cur→停損／short entry×1.03→停損）
    保持一致，畫面顯示的數字才不會誤導。
    """
    if not candidates:
        return []
    per_capital = total_capital / len(candidates)
    entries = []
    for c in candidates:
        price = float(c.get('price', 0) or 0)
        if price <= 0:
            continue
        shares = int(per_capital / (price * 1000))   # 張數（1張=1000股）
        if shares < 1:
            shares = 1
        reasons = c.get('reasons', [])
        reason_text = (f"{c.get('signal_text', '')}（評分{c.get('score')}）｜"
                       f"{'、'.join(reasons) if reasons else '技術面達標'}｜"
                       f"爆量比{float(c.get('vol_ratio', 0) or 0):.1f} RSI{float(c.get('rsi_val', 0) or 0):.0f} "
                       f"外資5日{float(c.get('f_5d', 0) or 0):+.0f}張")
        if side == 'long':
            def_line = float(c.get('def_line', 0) or 0)       # 進場價下方，跌破停損
            take_profit = float(c.get('atk_zone', 0) or 0)    # 進場價上方，觸及停利
        else:
            def_line = round(price * 1.03, 2)                 # 做空：進場價上方，漲破停損
            take_profit = round(price * 0.95, 2)              # 做空：進場價下方，跌破停利
        entries.append({
            "symbol": c.get('code'), "name": c.get('name'),
            "entry_date": run_date, "entry_price": price, "shares": shares,
            "capital": round(shares * price * 1000, 0),
            "def_line": def_line,
            "take_profit": take_profit,
            "status": "holding", "side": side,   # 'long' or 'short'
            "select_reason": reason_text,   # 【V160】選股理由
            # 【V160 新增】來源標記：manual=網頁手動測試鈕，scheduler=排程自動。
            # 讓你能分辨績效表裡哪些是真正的自動化成果。
            "trigger_source": trigger_source,
        })
    return entries


def system_check_exits(config_payload):
    """
    【V160 A階段】檢查系統持倉是否觸發出場（出場規則B）。
    多單：現價跌破防守線→停損，或觸及短線停利點→停利。
    空單：現價漲破防守線(進場價上方停損)→停損，或跌到目標→停利。
    回傳觸發出場的清單。
    """
    holdings = sb_get_system_holdings('holding')
    trail_cfg = get_trail_config()
    exits = []
    for h in holdings:
        code = h.get('symbol')
        c = calculate_signals_worker(code, config_payload)
        if not c or c.get('error'):
            continue
        cur = float(c.get('price', 0) or 0)
        if cur <= 0:
            continue
        side = h.get('side', 'long')
        entry = float(h.get('entry_price', 0) or 0)
        defl = float(h.get('def_line', 0) or 0)
        tp = float(h.get('take_profit', 0) or 0)

        # 【V160 延伸4】更新「進場後極值」並算移動停利線。
        # peak_price 若還沒有值（舊資料或剛進場），就用進場價當起點。
        # 【V160 上線前健檢修復】原本讀 c.get('atr')，但戰卡寫入的 key 其實是
        # 'atr_val'——永遠讀不到、恆為0，而 compute_trail_stop 在 atr<=0 時直接
        # 回傳「不啟動」，等於**整個 ATR 移動停利功能從未真正運作過**。
        # 這是靜默失敗：功能看起來有建好、開關也能切，但實際上永遠不會觸發。
        _atr = float(c.get('atr_val', 0) or 0)
        _stored_peak = float(h.get('peak_price', 0) or 0)
        if side == 'long':
            _peak = max(_stored_peak if _stored_peak > 0 else entry, cur)
        else:
            _peak = min(_stored_peak if _stored_peak > 0 else entry, cur) if entry > 0 else cur
        if trail_cfg['enabled'] and abs(_peak - _stored_peak) > 1e-9:
            sb_update_peak_price(h['id'], round(_peak, 2))
        _trail_line, _trail_on = (compute_trail_stop(
            side, entry, _peak, _atr, trail_cfg['mult'], trail_cfg['activate_mult'])
            if trail_cfg['enabled'] else (0.0, False))

        exit_reason = None
        if side == 'long':
            # 移動停利啟動後，用「較高的那條線」當停損——移動停利只會收緊、不會放鬆，
            # 避免出現「因為啟用移動停利反而讓停損變寬」這種本末倒置的情況。
            _eff_stop = max(defl, _trail_line) if _trail_on else defl
            if _eff_stop > 0 and cur <= _eff_stop:
                exit_reason = 'trail_stop' if (_trail_on and _trail_line >= defl) else 'stop_loss'
            elif tp > 0 and cur >= tp and not _trail_on:
                # 移動停利啟動後就不再用固定停利點出場（那正是它要解決的「提早下車」問題）
                exit_reason = 'take_profit'
        else:  # short
            # 空單：漲破進場價3%停損，跌破進場價5%停利（不依賴 tp 欄位，用固定幅度）
            _fixed_stop = entry * 1.03 if entry > 0 else 0.0
            _eff_stop = min(_fixed_stop, _trail_line) if (_trail_on and _trail_line > 0) else _fixed_stop
            if _eff_stop > 0 and cur >= _eff_stop:
                exit_reason = 'trail_stop' if (_trail_on and _trail_line <= _fixed_stop) else 'stop_loss'
            elif entry > 0 and cur <= entry * 0.95 and not _trail_on:
                exit_reason = 'take_profit'
        if exit_reason:
            shares = int(h.get('shares', 0) or 0)
            if side == 'long':
                pnl = (cur - entry) * shares * 1000
            else:
                pnl = (entry - cur) * shares * 1000
            roi = (pnl / (entry * shares * 1000) * 100) if entry > 0 and shares > 0 else 0.0
            exits.append({**h, 'exit_price': cur, 'exit_reason': exit_reason,
                          'realized_pnl': round(pnl, 0), 'realized_roi': round(roi, 2)})
    return exits


def system_apply_exits(exits):
    """把出場更新寫回 Supabase（status→closed）。"""
    for e in exits:
        def _do():
            return SUPABASE_CONN.table("system_portfolio").update({
                "status": "closed", "exit_date": datetime.now().strftime('%Y-%m-%d'),
                "exit_price": e['exit_price'], "exit_reason": e['exit_reason'],
                "realized_pnl": e['realized_pnl'], "realized_roi": e['realized_roi'],
            }).eq("id", e['id']).execute()
        _sb_safe(_do)


def system_check_add_reduce(config_payload):
    """
    【V160 新功能】依訊號判斷加碼/攤平（兩者都做）。回傳待執行的加減碼動作清單。
    規則（每檔各上限一次，避免無限加碼燒光資金）：
    - 順勢加碼：多單「已獲利(>2%)」且「訊號再轉強(評分≥4)」且「尚未加碼過」→ 加碼
    - 逆勢攤平：多單「接近防守線(現價在防守線1~5%上方)」但「訊號未完全轉空(評分>-3)」
      且「尚未攤平過」→ 攤平
    - 空單邏輯鏡像相反。
    加碼資金來源：剩餘資金平分（用 get_system_capital / 當前持倉數估算每檔可加額度）。
    """
    holdings = sb_get_system_holdings('holding')
    if not holdings:
        return []
    # 剩餘資金估算：每日總額扣掉已投入，平分給「還能加碼的檔數」
    daily_cap = get_system_capital()
    invested = sum(float(h.get('capital', 0) or 0) for h in holdings)
    remaining = max(0, daily_cap - invested)
    actions = []
    for h in holdings:
        code = h.get('symbol')
        c = calculate_signals_worker(code, config_payload)
        if not c or c.get('error'):
            continue
        cur = float(c.get('price', 0) or 0)
        if cur <= 0:
            continue
        side = h.get('side', 'long')
        entry = float(h.get('entry_price', 0) or 0)
        defl = float(h.get('def_line', 0) or 0)
        score = c.get('score', 0)
        add_count = int(h.get('add_count', 0) or 0)       # 已加碼次數
        reduce_count = int(h.get('reduce_count', 0) or 0) # 已攤平次數
        roi_now = ((cur - entry) / entry * 100) if side == 'long' and entry > 0 else \
                  ((entry - cur) / entry * 100) if entry > 0 else 0

        action = None
        if side == 'long':
            # 順勢加碼：已獲利 + 訊號再轉強 + 沒加碼過
            if roi_now > 2.0 and score >= 4 and add_count < 1:
                action = 'add'
            # 逆勢攤平：接近防守線但未完全轉空 + 沒攤平過
            elif defl > 0 and defl < cur <= defl * 1.05 and score > -3 and reduce_count < 1:
                action = 'reduce'
        else:  # short
            if roi_now > 2.0 and score <= -4 and add_count < 1:
                action = 'add'
            elif entry > 0 and entry * 0.95 <= cur < entry and score < 3 and reduce_count < 1:
                action = 'reduce'

        if action:
            # 加碼張數：用剩餘資金平分（保守估：剩餘 / 目前持倉檔數 / 股價）
            per_add = remaining / max(1, len(holdings))
            add_shares = int(per_add / (cur * 1000)) if cur > 0 else 0
            if add_shares < 1:
                add_shares = 1
            actions.append({
                'id': h['id'], 'symbol': code, 'side': side, 'action': action,
                'price': cur, 'add_shares': add_shares, 'score': score, 'roi_now': round(roi_now, 2),
                'old_shares': int(h.get('shares', 0) or 0), 'old_entry': entry,
                'add_count': add_count, 'reduce_count': reduce_count,
            })
    return actions


def system_apply_add_reduce(actions):
    """
    把加減碼動作寫回 Supabase：更新張數、重算加權平均進場成本、累加加/減碼次數。
    加權平均：新成本 = (舊張數×舊成本 + 加碼張數×加碼價) / 總張數
    """
    for a in actions:
        old_shares = a['old_shares']
        add_shares = a['add_shares']
        old_entry = a['old_entry']
        add_price = a['price']
        new_shares = old_shares + add_shares
        new_avg = ((old_shares * old_entry + add_shares * add_price) / new_shares) if new_shares > 0 else old_entry
        update_fields = {
            "shares": new_shares,
            "entry_price": round(new_avg, 2),
            "capital": round(new_shares * new_avg * 1000, 0),
        }
        if a['action'] == 'add':
            update_fields["add_count"] = a['add_count'] + 1
        else:
            update_fields["reduce_count"] = a['reduce_count'] + 1

        def _do():
            return SUPABASE_CONN.table("system_portfolio").update(update_fields).eq("id", a['id']).execute()
        _sb_safe(_do)


def get_system_portfolio_stats():
    """
    【V160 A階段】系統模擬倉績效統計：分多空兩組，算已實現勝率/報酬 + 未實現持倉。
    回傳 dict。
    """
    holding = sb_get_system_holdings('holding')
    closed = sb_get_system_holdings('closed')

    def _side_stats(records, side):
        subset = [r for r in records if r.get('side') == side]
        if not subset:
            return {'筆數': 0, '勝率%': None, '平均報酬%': None, '總損益': 0}
        rois = [float(r.get('realized_roi', 0) or 0) for r in subset]
        pnls = [float(r.get('realized_pnl', 0) or 0) for r in subset]
        wins = sum(1 for x in rois if x > 0)
        return {'筆數': len(subset), '勝率%': round(wins / len(subset) * 100, 1),
                '平均報酬%': round(sum(rois) / len(rois), 2), '總損益': round(sum(pnls), 0)}

    return {
        'long_closed': _side_stats(closed, 'long'),
        'short_closed': _side_stats(closed, 'short'),
        'holding_count': len(holding),
        'holding': holding,
        'closed': closed,   # 【V160 新增】原始已結算清單，供績效摘要表的細節展開用
    }


def init_session_state():
    defaults = {
        'db_loaded': False, 'pinned_stocks': {"2303": "手動強制加入", "5871": "手動強制加入"},
        'portfolio': {}, 'revenue_override': {}, 'dividend_override': {},
        'bigholder_override': {}, 'scan_results': [], 'scan_mode': "",
        'active_key_index': 0, 'single_ai_trigger': "", 'single_ai_report': {},
        'intelligence_pool': {}, 'analysis_history': {}, 'last_refresh': time.time(),
        'last_uploaded_csv': None, 'trigger_scan': False,
        'anomaly_snapshot': {}, 'anomaly_log': [],
        'sb_synced': False, 'sb_sync_result': (0, 0),
        'authenticated': False, 'cloud_hydrated': False,
        'observe_stocks': {}, 'card_cache': {}, 'card_cache_token': ''
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


init_session_state()


def safe_json_write(filepath, data):
    dir_name = os.path.dirname(os.path.abspath(filepath)) or "."
    with tempfile.NamedTemporaryFile('w', dir=dir_name, delete=False, suffix='.tmp', encoding='utf-8') as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=4)
        tmp_path = tmp.name
    os.replace(tmp_path, filepath)


def load_and_isolate_db():
    if not st.session_state.get('db_loaded', False):
        if os.path.exists(USER_DB_FILE):
            try:
                with open(USER_DB_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    st.session_state.pinned_stocks = data.get("pinned_stocks", st.session_state.pinned_stocks)
                    st.session_state.observe_stocks = data.get("observe_stocks", {})
                    st.session_state.portfolio = data.get("portfolio", {})
                    st.session_state.revenue_override = data.get("revenue_override", {})
                    st.session_state.dividend_override = data.get("dividend_override", {})
                    st.session_state.bigholder_override = data.get("bigholder_override", {})
                    st.session_state.intelligence_pool = data.get("intelligence_pool", {})
                    st.session_state.analysis_history = data.get("analysis_history", {})
            except Exception:
                pass

        now_ts = datetime.now().timestamp()
        for d_dict in [st.session_state.revenue_override,
                       st.session_state.bigholder_override,
                       st.session_state.dividend_override]:
            for k in list(d_dict.keys()):
                if now_ts - d_dict[k].get('ts', now_ts) > 7 * 86400:
                    del d_dict[k]
        st.session_state.db_loaded = True


def save_local_db_isolated():
    payload = {
        "pinned_stocks": st.session_state.get('pinned_stocks', {}),
        "observe_stocks": st.session_state.get('observe_stocks', {}),
        "portfolio": st.session_state.get('portfolio', {}),
        "revenue_override": st.session_state.get('revenue_override', {}),
        "dividend_override": st.session_state.get('dividend_override', {}),
        "bigholder_override": st.session_state.get('bigholder_override', {}),
        "intelligence_pool": st.session_state.get('intelligence_pool', {}),
        "analysis_history": st.session_state.get('analysis_history', {})
    }
    safe_json_write(USER_DB_FILE, payload)
    # 【V160 第二階段】狀態同步雲端：整包使用者狀態寫進 Supabase user_state 表，
    # 這樣換裝置登入、或容器清空後，都能從雲端把雷達/持倉/情報讀回來。
    sb_save_user_state(payload)


# ==============================================================================
# 二之三、使用者狀態雲端化 + 登入牆 (V160 第二階段)
# ------------------------------------------------------------------------------
# 把原本只存在本機 54088_database.json 的雷達/持倉/情報等狀態，改成同時存 Supabase
# user_state 表。登入後從雲端讀回，做到「登入即有資料、不用存手機上」。
# 一樣有降級保護：Supabase 沒連上時，退回原本純本機 JSON 模式，不影響運作。
# ==============================================================================
USER_STATE_KEY = "commander_main"   # 單一使用者，固定一把 key


def sb_save_user_state(payload):
    """把整包使用者狀態 upsert 進 Supabase user_state 表（單筆 JSONB）。"""
    def _do():
        data = {"state_key": USER_STATE_KEY, "state_value": payload}
        return SUPABASE_CONN.table("user_state").upsert(data, on_conflict="state_key").execute()
    ok, _ = _sb_safe(_do)
    return ok


def sb_load_user_state():
    """從 Supabase 讀回使用者狀態；未啟用或查無資料回 None。"""
    def _do():
        return SUPABASE_CONN.table("user_state").select("state_value").eq("state_key", USER_STATE_KEY).limit(1).execute()
    ok, res = _sb_safe(_do)
    if ok and res is not None and getattr(res, "data", None):
        try:
            return res.data[0]["state_value"]
        except Exception:
            return None
    return None


def hydrate_state_from_cloud():
    """
    開機時（每 session 一次）從雲端把使用者狀態灌進 session_state。
    雲端有資料就用雲端的（較新、跨裝置一致）；雲端沒有就維持本機 JSON 載入的結果。
    """
    if not SUPABASE_ENABLED:
        return False
    cloud = sb_load_user_state()
    if not cloud or not isinstance(cloud, dict):
        return False
    for k in ("pinned_stocks", "observe_stocks", "portfolio", "revenue_override", "dividend_override",
              "bigholder_override", "intelligence_pool", "analysis_history"):
        if k in cloud and cloud[k]:
            st.session_state[k] = cloud[k]
    return True


def require_login():
    """
    登入牆：未登入時顯示密碼輸入畫面並 st.stop() 擋住後續所有 UI。
    密碼沿用 secrets 的 commander_pin（總指揮官選 A：一個密碼走天下）。
    """
    if st.session_state.get('authenticated', False):
        return
    st.markdown("<h1 style='text-align:center; color:#f1c40f; margin-top:60px;'>🚀 作戰室 正式版 v1.0</h1>",
                unsafe_allow_html=True)
    st.markdown("<p style='text-align:center; color:#888;'>總指揮官身分驗證</p>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        pin_input = st.text_input("請輸入指揮密碼", type="password", key="login_pin_input")
        if st.button("🔓 登入作戰室", use_container_width=True):
            if pin_input == str(COMMANDER_PIN):
                st.session_state['authenticated'] = True
                # 登入成功當下，從雲端灌一次狀態（跨裝置一致）
                hydrated = hydrate_state_from_cloud()
                st.session_state['cloud_hydrated'] = hydrated
                st.rerun()
            else:
                st.error("密碼錯誤，請重新輸入。")
    st.stop()


load_and_isolate_db()

# 【V160】開機時從 Supabase 同步一次籌碼到本機（每個 session 只跑一次，避免每次 rerun 都打雲端）
if SUPABASE_ENABLED and not st.session_state.get('sb_synced', False):
    # 【V160 修復】總指揮官回報：每次重新登入都要轉2-3分鐘。
    # 根因：這裡把 Supabase 最近45天的籌碼資料整批回填到本機，隨著你用單檔精準同步
    # 累積的歷史越多（每次同步會寫入40天），這個回填要處理的筆數就越多。
    # Streamlit Cloud 閒置一段時間後會把容器睡眠，你重新登入時等於是全新容器、
    # 全新 session，這個回填就得整個重跑一次——這是雲端同步架構下的已知取捨，
    # 不是功能壞掉。這裡至少讓你看到進度文字，不會覺得畫面卡死。
    # 【V160 改版】總指揮官要求把「小人跑」的 spinner 換成 0-100% 進度條，
    # 這樣才知道還要等多久、也才看得出來是真的在動還是卡住了。
    # 這裡把回填拆成有明確階段的步驟，每完成一步就更新百分比。
    _boot_prog = st.progress(0.0, text="☁️ 準備從雲端回填資料...")

    def _boot_progress_cb(pct, label):
        """給 sync_from_supabase_on_boot 回報進度用。pct 是 0.0~1.0。"""
        try:
            _boot_prog.progress(min(1.0, max(0.0, pct)), text=f"☁️ {label}（{pct*100:.0f}%）")
        except Exception:
            pass   # 進度條更新失敗不該讓整個開機流程掛掉

    _inst_n, _bh_n = sync_from_supabase_on_boot(progress_cb=_boot_progress_cb)
    _boot_prog.progress(1.0, text=f"✅ 回填完成（籌碼 {_inst_n:,} 筆、大戶 {_bh_n:,} 筆）")
    time.sleep(0.3)
    _boot_prog.empty()
    st.session_state['sb_synced'] = True
    st.session_state['sb_sync_result'] = (_inst_n, _bh_n)

API_READY, FINMIND_READY = True, True
try:
    COMMANDER_PIN = st.secrets.radar_secrets.commander_pin
    NVIDIA_API_KEY = st.secrets.radar_secrets.get("nvidia_api_key", "").strip()
    if not NVIDIA_API_KEY:
        API_READY = False

    SECRET_FINMIND = st.secrets.radar_secrets.get("finmind_token", "")
    FINMIND_TOKENS = [k.strip() for k in SECRET_FINMIND.split(",") if k.strip()]
    if not FINMIND_TOKENS or FINMIND_TOKENS[0] == "":
        FINMIND_TOKENS, FINMIND_READY = [""], False
except Exception:
    API_READY, FINMIND_READY, COMMANDER_PIN, NVIDIA_API_KEY, FINMIND_TOKENS = False, False, "54088", "", [""]


def get_active_fm_token():
    idx = st.session_state.get('active_key_index', 0) % max(1, len(FINMIND_TOKENS))
    return FINMIND_TOKENS[idx]


# ==============================================================================
# 【V160 修復】FinMind 多帳號額度輪替
# ------------------------------------------------------------------------------
# 原本 active_key_index 只有「初始化成 0」和「被讀取一次」，全程式沒有任何地方
# 把它加一 —— 也就是說「用完一個帳號自動換下一個」根本沒有實作，實際上永遠
# 只用第一組 token，額度是 600 而不是預期的 1500。
#
# 這裡改成模組層級的輪替索引（不用 st.session_state，因為掃描是跑在
# ThreadPoolExecutor 的工作執行緒裡，那裡碰 session_state 會出事）。
# 額度鏈：token1(600) → token2(600) → 訪客不帶token(300) = 1500/小時。
# ==============================================================================
_FM_KEY_LOCK = threading.Lock()
_FM_KEY_INDEX = 0          # 目前用到第幾組 token
_FM_KEY_EXHAUSTED = {}     # {token: 何時被判定額度用盡的 timestamp}
_FM_COOLDOWN_SEC = 900     # 被判定用盡後，15 分鐘內不再優先使用


def _fm_token_chain():
    """回傳這次請求要依序嘗試的憑證清單：目前索引起算的所有 token，最後補上訪客額度('')。"""
    tokens = [t for t in FINMIND_TOKENS if t]
    if not tokens:
        return [""]                       # 完全沒設 token，只能用訪客額度
    with _FM_KEY_LOCK:
        start = _FM_KEY_INDEX % len(tokens)
    ordered = tokens[start:] + tokens[:start]
    now = time.time()
    # 把還在冷卻中的 token 排到後面（不是直接丟掉，因為額度可能已經回補）
    fresh = [t for t in ordered if now - _FM_KEY_EXHAUSTED.get(t, 0) > _FM_COOLDOWN_SEC]
    cooling = [t for t in ordered if t not in fresh]
    return fresh + cooling + [""]         # 最後才動用訪客額度


def _fm_mark_exhausted(token):
    """標記某組 token 額度用盡，並把輪替索引推到下一組。"""
    global _FM_KEY_INDEX
    tokens = [t for t in FINMIND_TOKENS if t]
    if not tokens:
        return
    with _FM_KEY_LOCK:
        if token:
            _FM_KEY_EXHAUSTED[token] = time.time()
            if token in tokens:
                _FM_KEY_INDEX = (tokens.index(token) + 1) % len(tokens)


def get_fm_quota_status():
    """給側邊欄顯示用：目前用第幾組、哪些在冷卻。"""
    tokens = [t for t in FINMIND_TOKENS if t]
    with _FM_KEY_LOCK:
        idx = _FM_KEY_INDEX % max(1, len(tokens)) if tokens else 0
    now = time.time()
    rows = []
    for i, t in enumerate(tokens):
        left = _FM_COOLDOWN_SEC - (now - _FM_KEY_EXHAUSTED.get(t, 0))
        state = f"冷卻中({int(left/60)}分)" if left > 0 else "可用"
        rows.append(f"帳號{i + 1}：{state}" + ("　◀ 目前使用" if i == idx else ""))
    rows.append("訪客額度：最後備援")
    return rows


# ==============================================================================
# 三、 基礎運算與 API 取資料核心
# ==============================================================================
def safe_float(val):
    """
    【重大修復】V155 的 safe_float 會用 .replace('-', '') 把負號整個刪掉，
    導致證交所 CSV 的「賣超」被寫成「買超」，籌碼方向全面反向。
    這裡改為正確解析正負號與會計括號負值。
    """
    if val is None:
        return 0.0
    try:
        if pd.isna(val):
            return 0.0
    except Exception:
        pass
    s = str(val).strip().upper()
    if s in ('', '-', '--', 'NA', 'N/A', 'NONE', 'NAN'):
        return 0.0
    s = s.replace(',', '').replace(' ', '')
    if s.startswith('(') and s.endswith(')'):   # 會計負值 (1,234)
        s = '-' + s[1:-1]
    m = re.search(r'-?\d+(?:\.\d+)?', s)
    try:
        return float(m.group()) if m else 0.0
    except Exception:
        return 0.0


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


def _finmind_get_once(url, params, max_retries=3, timeout=6):
    """單一憑證的請求（含重試）。憑證輪替由 _finmind_get 負責。"""
    last_reason, last_detail = "unknown", ""
    for attempt in range(max_retries):
        try:
            res = _SESSION.get(url, params=params, timeout=timeout)
            if res.status_code == 429:
                last_reason, last_detail = "rate_limited", "HTTP 429"
                time.sleep(1.5 * (attempt + 1))
                continue
            if res.status_code != 200:
                last_reason, last_detail = "http_error", f"HTTP {res.status_code}"
                time.sleep(0.8 * (attempt + 1))
                continue
            payload = res.json()
            if payload.get('msg') != 'success':
                msg = str(payload.get('msg', ''))
                _m = msg.lower()
                # 【V160 修復】先判斷「方案權限不足」，再判斷「額度用盡」。
                # 兩者都可能回 200＋msg，但意義完全不同：權限不足再等也沒用。
                if ('sponsor' in _m or 'backer' in _m or 'permission' in _m
                        or 'not allow' in _m or 'upgrade' in _m or '權限' in msg):
                    raise FinMindAPIError('permission_denied', msg)
                # FinMind 的額度用盡有時是 200 + msg，不是 429
                if 'limit' in _m or '402' in msg:
                    raise FinMindAPIError('rate_limited', msg)
                last_reason, last_detail = "api_rejected", msg
                time.sleep(0.8 * (attempt + 1))
                continue
            if not payload.get('data'):
                raise FinMindAPIError('empty_data', 'API 回傳成功但 data 為空')
            return payload
        except FinMindAPIError:
            raise
        except requests.exceptions.Timeout:
            last_reason, last_detail = "timeout", f"逾時 {timeout}s"
            time.sleep(0.8 * (attempt + 1))
        except requests.exceptions.RequestException as e:
            last_reason, last_detail = "connection_error", str(e)
            time.sleep(0.8 * (attempt + 1))
    raise FinMindAPIError(last_reason, last_detail)


def _finmind_get(url, params, max_retries=3, timeout=6):
    """
    【V160 修復】FinMind 請求入口 —— 真正把「多帳號額度輪替」接上。

    先前的問題：_fm_token_chain() / _fm_mark_exhausted() 雖然寫好了，但整份程式
    沒有任何地方呼叫它們，是死程式碼。實際送出請求的路徑是各個 fetch 函式自己
    塞 params['token'] = get_active_fm_token()，而 active_key_index 從初始化成 0
    之後就再也沒有被加一 —— 也就是說永遠只用第一組 token，
    額度是 600/小時而不是預期的 1500。

    現在改成：呼叫端傳進來的 token 一律忽略，由這裡依序試
    token1 → token2 → 訪客額度（不帶 token），任一組被判定額度用盡就
    標記冷卻並自動換下一組。額度鏈 600 + 600 + 300 = 1500/小時。

    只有「額度用盡」和「權限不足」才換下一組；
    「查無資料」是資料本身的問題，換帳號也一樣，直接回報不浪費額度。
    """
    base = {k: v for k, v in params.items() if k != 'token'}
    last_exc = None
    for cred in _fm_token_chain():
        p = dict(base)
        if cred:
            p['token'] = cred
        try:
            return _finmind_get_once(url, p, max_retries=max_retries, timeout=timeout)
        except FinMindAPIError as e:
            if e.reason == 'rate_limited':
                _fm_mark_exhausted(cred)   # 標記冷卻並把索引推到下一組
                last_exc = e
                continue
            if e.reason == 'permission_denied':
                # 另一組帳號有可能是不同方案等級，值得再試一次
                last_exc = e
                continue
            raise                          # empty_data / 連線問題：換帳號無意義
    raise last_exc if last_exc else FinMindAPIError('unknown', '所有憑證皆無法取得資料')


@st.cache_resource
def _get_smart_cache_store():
    """
    【V160】process-wide 持久字典，跨頁面重整/跨使用者session都共用同一份（不像
    session_state 每次重新整理就重置）。用來實作「已知的成功值固定保留，只有真的
    抓到新資料才覆蓋」的快取邏輯。
    """
    return {}


def _is_ok_value(v):
    """判斷一筆結果是不是成功：優先看'ok'欄位，沒有的話看'error'欄位，都沒有就當成功。"""
    if isinstance(v, dict):
        if 'ok' in v:
            return bool(v.get('ok'))
        if 'error' in v:
            return v.get('error') is None
    return bool(v)


def _smart_cached_call(cache_key, fetch_fn, recheck_interval=1800, fail_retry=120):
    """
    【V160】千張大戶／月營收這類資料，本質上是「有新的才會變，沒新的就固定不動」
    （營收一個月才更新一次、大戶一週才更新一次），所以快取邏輯改成：
    - 已經抓到成功值 → 這個值會被「固定保留」，之後每隔 recheck_interval（預設30分鐘）
      才去檢查一次「有沒有新資料出來」；檢查成功且真的有新值，才覆蓋舊值。
    - 如果那次檢查剛好失敗（暫時性問題）→ 繼續沿用上一次成功的舊值顯示，
      不會突然從「有數字」變回「官方未公佈」，畫面不會忽有忽無。
    - 只有「從來沒有成功過」的情況，才會顯示查詢失敗，而且會用較短的 fail_retry
      （預設2分鐘）鼓勵盡快重試，直到第一次成功為止。
    """
    store = _get_smart_cache_store()
    now_ts = time.time()
    entry = store.get(cache_key)

    # 還沒到重新檢查的時間點 → 不管上次是成功還失敗，直接沿用，不打API
    if entry and (now_ts - entry['checked_ts']) < entry.get('recheck', recheck_interval):
        return entry['value']

    new_value = fetch_fn()
    if _is_ok_value(new_value):
        # 查詢成功：覆蓋成新值（可能是全新資料，也可能剛好跟舊值一樣，都沒關係）
        store[cache_key] = {'value': new_value, 'checked_ts': now_ts, 'recheck': recheck_interval}
        return new_value

    # 這次查詢失敗：如果之前有成功過的舊值，繼續沿用舊值顯示，只是縮短下次重試間隔
    if entry and _is_ok_value(entry['value']):
        store[cache_key] = {'value': entry['value'], 'checked_ts': now_ts, 'recheck': fail_retry}
        return entry['value']

    # 從來沒有成功過 → 顯示這次的失敗結果，但很快就會重試
    store[cache_key] = {'value': new_value, 'checked_ts': now_ts, 'recheck': fail_retry}
    return new_value


def _reason_to_label(reason):
    if reason == 'rate_limited':
        return ERR_RATE_LIMIT
    if reason == 'permission_denied':
        return ERR_PERMISSION
    if reason in ('timeout', 'connection_error', 'http_error'):
        return ERR_CONN
    return ERR_NO_DATA


def _fetch_finmind_revenue_impl(symbol, token, max_lookback=1200):
    """
    【V160 關鍵修復】月營收年增/月增改為「自己算」。

    根因：舊版讀 row['revenue_YearOnYearRatio'] 和 row['revenue_MonthOverMonthRatio']，
    但依 FinMind 官方 schema，TaiwanStockMonthRevenue 只有
    date / stock_id / country / revenue / revenue_month / revenue_year / create_time
    —— 那兩個比率欄位根本不存在。每一列都取到 None，被 pd.isna() 全部略過，
    所以這個功能 100% 永遠回「查無資料」，跟快取、跟帳號額度都無關。

    正確做法：抓原始 revenue，自己算
      月增 MoM = (本月 - 上月) / 上月 × 100
      年增 YoY = (本月 - 去年同月) / 去年同月 × 100
    因為 YoY 需要去年同月，起始回看天數必須 >= 400 天（舊版 120 天連一年都不到）。
    """
    url = 'https://api.finmindtrade.com/api/v4/data'
    lookback = 500                      # 至少涵蓋去年同月（YoY 需要）
    df = None
    last_err = "empty_data"
    while df is None and lookback <= max_lookback:
        start_date = (datetime.now() - timedelta(days=lookback)).strftime('%Y-%m-%d')
        params = {'dataset': 'TaiwanStockMonthRevenue', 'data_id': symbol, 'start_date': start_date}
        try:
            payload = _finmind_get(url, params)
            tmp = pd.DataFrame(payload.get('data', []))
            if not tmp.empty:
                df = tmp
            else:
                lookback *= 2
        except FinMindAPIError as e:
            last_err = e.reason
            if last_err in ('rate_limited', 'permission_denied'):
                break                   # 換帳號已在底層試過，這裡不再重打
            lookback *= 2

    if df is not None and not df.empty and 'revenue' in df.columns:
        d = df.copy()
        d['revenue'] = pd.to_numeric(d['revenue'], errors='coerce')
        d['revenue_year'] = pd.to_numeric(d.get('revenue_year'), errors='coerce')
        d['revenue_month'] = pd.to_numeric(d.get('revenue_month'), errors='coerce')
        d = d.dropna(subset=['revenue', 'revenue_year', 'revenue_month'])
        if not d.empty:
            # 用「營收所屬年月」排序，不是用公布日期（公布日可能同月多筆）
            d = d.sort_values(['revenue_year', 'revenue_month'])
            d = d.drop_duplicates(subset=['revenue_year', 'revenue_month'], keep='last')
            # 建索引方便查上月／去年同月
            by_ym = {(int(r['revenue_year']), int(r['revenue_month'])): float(r['revenue'])
                     for _, r in d.iterrows()}
            latest = d.iloc[-1]
            y, m = int(latest['revenue_year']), int(latest['revenue_month'])
            cur = float(latest['revenue'])

            prev_y, prev_m = (y - 1, 12) if m == 1 else (y, m - 1)
            prev_rev = by_ym.get((prev_y, prev_m))
            last_year_rev = by_ym.get((y - 1, m))

            mom = round((cur - prev_rev) / prev_rev * 100, 2) if prev_rev else None
            yoy = round((cur - last_year_rev) / last_year_rev * 100, 2) if last_year_rev else None

            if yoy is not None or mom is not None:
                result = {'yoy': yoy, 'mom': mom, 'month': f"{m:02d}月",
                          'stale': False, 'ok': True}
                with _LAST_GOOD_LOCK:
                    _LAST_GOOD_REVENUE[symbol] = result
                return result
            last_err = "empty_data"      # 有資料但湊不出可比較的基期

    with _LAST_GOOD_LOCK:
        last_good = _LAST_GOOD_REVENUE.get(symbol)
    if last_good:
        stale = dict(last_good)
        stale['stale'] = True
        return stale

    # 【任務一】不再用 0.0 混過去，明確標示失敗原因
    return {'yoy': None, 'mom': None, 'month': _reason_to_label(last_err), 'stale': False, 'ok': False}


def fetch_financial_health(symbol, token):
    """
    【V160 新增】深度財報分析：毛利率、ROE、營業現金流品質。

    背景：總指揮官問財報狗免費版能查的 ROE/毛利/現金流我們能不能做。
    查證後確認可行：FinMind 的綜合損益表/資產負債表/現金流量表都是免費資料集
    （跟我們已經在用的月營收表同等級，data_id 模式免費，只有「一次拿全市場」
    才需要付費會員，我們一直都是一檔一檔查，不受影響）。

    刻意只做三個指標，不做財報狗那種50+指標的全套：
      1. 毛利率 = 毛利/營收：反映定價能力與競爭優勢，是最基本也最重要的一個
      2. ROE（用最近一季稅後淨利年化 / 母公司權益）：反映股東資金的使用效率
      3. 現金流品質 = 營業現金流 / 稅後淨利：這是財報狗的招牌指標之一，
         比率遠低於1代表「帳上有賺錢但收不到現金」，是財報作假或營運品質
         惡化的早期警訊，比單看EPS更難被美化

    這三個是「30秒判斷要不要繼續看」等級的重點指標，不是要取代財報狗的深度研究，
    定位仍是快篩——真的要做投資決策，還是建議去財報狗查完整的多年度趨勢。

    回傳 dict 或 None（資料不足時誠實回報，不編造）。
    """
    def _fetch(dataset, stock_id):
        url = 'https://api.finmindtrade.com/api/v4/data'
        params = {'dataset': dataset, 'data_id': stock_id,
                  'start_date': (datetime.now() - timedelta(days=450)).strftime('%Y-%m-%d')}
        if token:
            params['token'] = token
        try:
            payload = _finmind_get(url, params)
            return pd.DataFrame(payload.get('data', []))
        except FinMindAPIError:
            return pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    def _latest(df, type_name):
        """從長格式表(date/stock_id/type/value)取出某個type的最新一筆數值。"""
        if df.empty or 'type' not in df.columns:
            return None
        sub = df[df['type'] == type_name]
        if sub.empty:
            return None
        sub = sub.sort_values('date')
        return safe_float(sub.iloc[-1]['value']), str(sub.iloc[-1]['date'])

    fs = _fetch('TaiwanStockFinancialStatements', symbol)
    bs = _fetch('TaiwanStockBalanceSheet', symbol)
    cf = _fetch('TaiwanStockCashFlowsStatement', symbol)

    if fs.empty and bs.empty and cf.empty:
        return None

    # 【注意】FinMind 綜合損益表沒有直接叫 "Revenue" 的欄位，實務上用 GrossProfit
    # 反推毛利率的分母，優先找 "TotalConsolidatedProfit" 系列都不穩定，改用
    # 最穩健的作法：如果損益表沒有明確營收欄位，改用月營收表的當季加總值當分母
    # （辨識營收欄位名稱可能因公司/年度而略有差異，找不到就誠實回報缺料）
    gp = _latest(fs, 'GrossProfit')
    rev_candidates = ['Revenue', 'OperatingRevenue', 'NetRevenue']
    rev = None
    for rc in rev_candidates:
        rev = _latest(fs, rc)
        if rev:
            break
    net_income = _latest(fs, 'IncomeAfterTaxes')
    equity = _latest(bs, 'EquityAttributableToOwnersOfParent')
    op_cash = _latest(cf, 'CashFlowsFromOperatingActivities')

    result = {'quarter_date': None, 'gross_margin': None, 'roe': None,
              'cash_quality': None, 'cash_quality_note': None, 'ok': False}

    if gp and rev and rev[0] and rev[0] != 0:
        result['gross_margin'] = round(gp[0] / rev[0] * 100, 1)
        result['quarter_date'] = gp[1]
        result['ok'] = True

    if net_income and equity and equity[0] and equity[0] != 0:
        # 單季淨利年化（×4）/ 權益，是近似值不是精確年度ROE，但用來快篩方向足夠
        result['roe'] = round(net_income[0] * 4 / equity[0] * 100, 1)
        result['quarter_date'] = result['quarter_date'] or net_income[1]
        result['ok'] = True

    if op_cash and net_income and net_income[0]:
        ratio = op_cash[0] / net_income[0]
        result['cash_quality'] = round(ratio, 2)
        if net_income[0] > 0 and ratio < 0.5:
            result['cash_quality_note'] = "⚠️ 營業現金流遠低於淨利，獲利品質可能不佳"
        elif net_income[0] > 0 and op_cash[0] < 0:
            result['cash_quality_note'] = "🔴 帳上有賺錢但營業現金流是負的，需留意"
        elif ratio >= 1:
            result['cash_quality_note'] = "✅ 營業現金流優於淨利，獲利品質良好"
        result['ok'] = True

    return result if result['ok'] else None


def fetch_financial_health_cached(symbol, token):
    """
    【V160】按需查詢的包裝層。財報一季才更新一次，不需要跟著全市場掃描一起打，
    那樣400檔掃描會多消耗1200次API額度（3張表×400檔），對免費額度是災難性的浪費。
    改成只有使用者在戰卡展開查詢時才呼叫，並用長效快取（6小時才重查一次）記住結果，
    同一次使用中重複展開同一檔不會重複打API。
    """
    cache_key = f"fin_health:{symbol}"
    return _smart_cached_call(cache_key, lambda: fetch_financial_health(symbol, token),
                              recheck_interval=21600, fail_retry=300)


def fetch_finmind_revenue(symbol, token, max_lookback=1200):
    """
    【V160】改用智慧快取（成功20小時／失敗2分鐘），取代原本固定TTL的 st.cache_data。
    月營收本來就是月頻資料，收盤後到隔天開盤前完全不會變，長時間快取成功結果很安全；
    失敗時短快取則讓查詢能快速自我修復，不會卡住一整天。

    【V160 關鍵修復】這裡原本預設 max_lookback=400，但內層 _fetch_finmind_revenue_impl
    的起始回看天數是 500（算年增需要去年同月）。while 迴圈條件是
    `lookback <= max_lookback`，500 <= 400 一開始就是假，
    導致迴圈一次都沒跑、連一次 API 都沒打，就直接回報「查無資料」。
    這個 bug 讓月營收從功能上線後就 100% 必然失敗，跟快取、跟帳號額度、
    跟股票代號完全無關——不管抓哪一檔都一樣會踩到。
    現在改成 1200，跟內層函式自己的預設值一致，且 1200 > 500 起跳值，迴圈才會真的執行。
    """
    cache_key = f"revenue:{symbol}:{token}"
    return _smart_cached_call(cache_key, lambda: _fetch_finmind_revenue_impl(symbol, token, max_lookback))


def _parse_holding_level_lower(level):
    """
    解析 FinMind 股東持股分級表的 HoldingSharesLevel 字串，回傳該級距的「下界股數」。

    實際會遇到的格式（依官方 schema 與 TDCC 公布格式）：
        '1-999'            → 1
        '1000-5000'        → 1000
        '100001-200000'    → 100001
        '1,000,001以上'     → 1000001
        '1000001以上'       → 1000001
    無法解析時回傳 None（由呼叫端 dropna 濾掉），不猜、不填 0，
    避免把無效級距誤當成小額股東拉低大戶比例。
    """
    if level is None:
        return None
    s = str(level).replace(',', '').replace('，', '').strip()
    m = re.search(r'\d+', s)
    if not m:
        return None
    try:
        return float(m.group())
    except (TypeError, ValueError):
        return None


def _fetch_big_holder_with_recursion_impl(code, token, target_date, initial_lookback=20, max_lookback=180):
    url = 'https://api.finmindtrade.com/api/v4/data'
    target_dt = datetime.strptime(target_date, "%Y-%m-%d")
    lookback = initial_lookback
    last_err = "empty_data"
    while lookback <= max_lookback:
        start_date = (target_dt - timedelta(days=lookback)).strftime('%Y-%m-%d')
        params = {'dataset': 'TaiwanStockHoldingSharesPer', 'data_id': code,
                  'start_date': start_date, 'end_date': target_date}
        if token:
            params['token'] = token
        try:
            payload = _finmind_get(url, params)
            raw = payload.get('data', [])
            if raw:
                df = pd.DataFrame(raw)
                # 【V160 關鍵修復】HoldingSharesLevel 依 FinMind 官方 schema 是「字串型級距」，
                # 實際值長這樣：'1-999'、'1000-5000'、'100001-200000'、'1000001以上'。
                # 舊寫法 pd.to_numeric('1-999') 必然變 NaN，接著 dropna 會把整張表刪光，
                # 導致永遠 empty →「📭官方未公佈」永久顯示（這才是真正的根因，
                # 不是快取、不是 TTL）。更早的寫死 >= 15 同樣對不上這個 schema。
                # 正確做法：解析每個級距的「下界股數」，挑出 >= 1,000,000 股（＝1000張）
                # 的級距加總，這才是「千張大戶」的定義。
                df['_lower'] = df['HoldingSharesLevel'].apply(_parse_holding_level_lower)
                df = df.dropna(subset=['_lower'])
                if not df.empty:
                    latest_date_all = df['date'].max()
                    day_df = df[df['date'] == latest_date_all]
                    if not day_df.empty:
                        # 千張＝1000張＝1,000,000股；取下界達標的所有級距
                        big = day_df[day_df['_lower'] >= 1_000_000]
                        if big.empty:
                            # 保險：若 schema 改版導致沒有任何級距達標，
                            # 退而取當日最高級距（維持舊有意圖，不會整個失效）
                            big = day_df[day_df['_lower'] == day_df['_lower'].max()]
                        df = big
                if not df.empty:
                    latest_date = df['date'].max()
                    pct = round(df[df['date'] == latest_date]['percent'].sum(), 2)
                    return {'big_holder': pct,
                            'big_holder_date': latest_date,
                            'is_stale': latest_date != target_date,
                            'error': None}
            last_err = "empty_data"
        except FinMindAPIError as e:
            last_err = e.reason
            if last_err == 'rate_limited':
                break
        lookback *= 2

    label = _reason_to_label(last_err)
    return {'big_holder': label, 'big_holder_date': label, 'is_stale': False, 'error': label}


def fetch_big_holder_with_recursion(code, token, target_date, initial_lookback=20, max_lookback=180):
    """
    【V160】改用智慧快取（成功20小時／失敗2分鐘），取代原本固定TTL的 st.cache_data。
    千張大戶是週頻資料，收盤後到隔天開盤前不會變，長時間快取成功結果很安全；
    失敗時短快取則讓查詢能快速自我修復。
    """
    cache_key = f"big_holder:{code}:{token}:{target_date}"
    return _smart_cached_call(cache_key, lambda: _fetch_big_holder_with_recursion_impl(
        code, token, target_date, initial_lookback, max_lookback))


@st.cache_data(ttl=86400, show_spinner=False)
def _fetch_finmind_dividend_impl(symbol, token, max_lookback=1200):
    """
    【V160 新增】TWSE 除權除息「預告」表（TWT48U_ALL）是前瞻性的，只列近期即將發生的事件——
    事件一旦過了，通常就會從表裡被移除，不會保留歷史。所以「已經除完權息、但已經是
    幾天前甚至更早」的股票（總指揮官回報的南亞科、環球晶就是這種情況）在預告表裡
    會直接查無此股，顯示「無近期資訊」，但這不是抓取失敗，是這個資料源本質上的限制。

    備援：FinMind 的股利政策表 TaiwanStockDividend 是「已公告股利」的永久紀錄，不會
    隨事件過去而消失，用來補這個缺口。取最近一筆公告，加總現金股利兩個子項
    （盈餘轉增資 + 法定盈餘公積），用除息交易日判斷過去/未來。
    """
    url = 'https://api.finmindtrade.com/api/v4/data'
    lookback = 500
    df = None
    last_err = "empty_data"
    while df is None and lookback <= max_lookback:
        start_date = (datetime.now() - timedelta(days=lookback)).strftime('%Y-%m-%d')
        params = {'dataset': 'TaiwanStockDividend', 'data_id': symbol, 'start_date': start_date}
        try:
            payload = _finmind_get(url, params)
            tmp = pd.DataFrame(payload.get('data', []))
            if not tmp.empty:
                df = tmp
            else:
                lookback *= 2
        except FinMindAPIError as e:
            last_err = e.reason
            if last_err in ('rate_limited', 'permission_denied'):
                break
            lookback *= 2

    if df is not None and not df.empty:
        d = df.copy()
        # 用公告日期排序取最新一筆已公告的股利政策
        sort_col = 'AnnouncementDate' if 'AnnouncementDate' in d.columns else 'date'
        d = d.sort_values(sort_col)
        latest = d.iloc[-1]
        cash = (safe_float(latest.get('CashEarningsDistribution', 0))
                + safe_float(latest.get('CashStatutorySurplus', 0)))
        stock = (safe_float(latest.get('StockEarningsDistribution', 0))
                + safe_float(latest.get('StockStatutorySurplus', 0)))
        ex_date = str(latest.get('CashExDividendTradingDate') or
                     latest.get('StockExDividendTradingDate') or '').strip()
        if cash > 0 or stock > 0:
            return {'cash': cash, 'stock': stock, 'ex_date': ex_date, 'ok': True}
        last_err = "empty_data"

    return {'cash': 0.0, 'stock': 0.0, 'ex_date': '', 'ok': False,
            'reason': _reason_to_label(last_err)}


def fetch_finmind_dividend_fallback(symbol, token, max_lookback=1200):
    cache_key = f"dividend_fallback:{symbol}:{token}"
    return _smart_cached_call(cache_key, lambda: _fetch_finmind_dividend_impl(symbol, token, max_lookback))


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


def _classify_dividend_date(date_str):
    """
    【V160 新增】判斷這個除權息日期是「已經過去」還是「還沒到」，回傳 'past'／'future'／'unknown'。
    同時處理民國格式（TWSE）與西元ISO格式（FinMind 備援來源）。
    總指揮官回報：原本只顯示一串數字日期，要自己心算比對今天日期才知道是不是已經除完了，
    容易誤判成「還沒資料」。這裡直接把結論算出來。
    """
    s = str(date_str).strip()
    try:
        if len(s) == 10 and s[4] == '-' and s[7] == '-':
            div_date = datetime.strptime(s, '%Y-%m-%d').date()
        elif len(s) == 7 and s.isdigit():
            roc_y, m, d = int(s[:3]), int(s[3:5]), int(s[5:7])
            div_date = datetime(roc_y + 1911, m, d).date()
        elif len(s) == 8 and s.isdigit():
            div_date = datetime(int(s[:4]), int(s[4:6]), int(s[6:8])).date()
        else:
            return 'unknown'
        return 'past' if div_date < datetime.now().date() else 'future'
    except (ValueError, TypeError):
        return 'unknown'


def fetch_twse_dividends():
    """
    【V160 關鍵修復】除權息預告表一直抓不到資料，原因跟營收/大戶是同一類 bug：
    端點路徑和欄位名稱都對不上證交所實際的 API schema。

    錯的地方：
      - URL 少了 `_ALL` 尾碼（`TWT48U` 不是有效端點，`TWT48U_ALL` 才是）
      - 欄位名稱寫的是中文（'股票代號'／'現金股利'／'除權息日期'），
        但這個 openapi 端點實際回傳的是英文欄位：
        Date／Code／Name／Exdividend／StockDividendRatio／
        SubscriptionRatio／CashDividend／SharesOffered 等
    中文欄位在英文回應裡永遠找不到 → item.get(...) 全部回傳空字串／0 →
    畫面上永遠「無日期」，不是資料真的沒有，是根本沒讀到欄位。
    """
    divs = {}
    try:
        res = _SESSION.get("https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL", timeout=5)
        if res.status_code == 200:
            for item in res.json():
                c = str(item.get('Code', '')).strip()
                if len(c) == 4:
                    cash_div = safe_float(item.get('CashDividend', 0))
                    stock_div = safe_float(item.get('StockDividendRatio', 0))
                    divs[c] = {'date': str(item.get('Date', '')).strip(),
                               'cash': cash_div, 'stock': stock_div}
    except Exception:
        pass
    return divs


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_stock_names():
    """
    【V160 修復】名稱對照表改以 FinMind TaiwanStockInfo 為主源。

    先前只用 TWSE BWIBBU_ALL（本益比/殖利率/淨值比）＋ TPEx 本益比分析當來源，
    但那兩個端點只涵蓋「有本益比資料」的個股 —— 虧損股、無配息股會被排除。
    這造成兩個問題：
      (1) 名稱查不到就退回顯示代號（總指揮官看到 2409 名稱欄顯示 2409）
      (2) 更嚴重：GLOBAL_MARKET_CODES 是直接取這份表的 keys，
          等於「全市場掃描池」從一開始就漏掉這些個股，根本掃不到。
    改用 TaiwanStockInfo（涵蓋上市/上櫃/興櫃全市場）當主源，
    原本兩個端點降為補充，抓不到名稱時仍退回顯示代號，不編造。
    """
    names = {}
    # 主源：FinMind TaiwanStockInfo（全市場）
    try:
        payload = _finmind_get('https://api.finmindtrade.com/api/v4/data',
                               {'dataset': 'TaiwanStockInfo'}, max_retries=2, timeout=15)
        for item in payload.get('data', []) or []:
            c = str(item.get('stock_id', '')).strip()
            n = str(item.get('stock_name', '')).strip()
            if len(c) == 4 and c.isdigit() and n:
                names[c] = n
    except Exception:
        pass                      # 主源失敗就靠下面的備援，不讓整個名稱表掛掉

    # 備援：TWSE / TPEx 公開端點
    for url in ["https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL",
                "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"]:
        try:
            res = _SESSION.get(url, timeout=5)
            if res.status_code == 200:
                for item in res.json():
                    c = str(item.get('Code', item.get('SecuritiesCompanyCode', ''))).strip()
                    n = str(item.get('Name', item.get('CompanyName', ''))).strip()
                    if len(c) == 4 and c.isdigit() and n:
                        names.setdefault(c, n)
        except Exception:
            pass
    for k, v in {"2330": "台積電", "2303": "聯電", "2317": "鴻海", "2308": "台達電",
                 "5871": "中租-KY", "3481": "群創", "2454": "聯發科",
                 "2409": "友達"}.items():
        names.setdefault(k, v)
    return names


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_all_institutional_by_date(target_date, token=None):
    """
    ⚠️【目前未啟用 — 需要 FinMind 付費方案】⚠️

    這個函式用 FinMind「不帶 data_id 的全市場模式」一次抓當日整個市場的三大法人。
    Round19 建置時我假設這是免費功能，**這個假設是錯的**——總指揮官實測後回報
    http_error，查證確認免費帳號呼叫這個模式會收到 "Your level is free." 錯誤，
    那是 sponsor/backer 付費方案專屬的功能。

    保留這段程式碼的原因：如果哪天升級 FinMind 付費方案，把側邊欄的批次同步
    改回呼叫這個函式就能立刻用（一次呼叫解決全市場，比逐檔同步有效率得多）。
    在那之前，側邊欄改用「批次同步我關注的股票」——逐檔呼叫免費的單檔模式，
    只涵蓋持倉/雷達/觀察清單，額度完全在免費方案內。

    回傳 (rows, error_reason)。
    """
    url = 'https://api.finmindtrade.com/api/v4/data'
    # 【V160 修復】總指揮官實測 7/17（週五、正常交易日）回報「沒有取得資料」。
    # 查證 FinMind 官方文件的全市場模式範例，發現只傳 start_date、不傳 end_date——
    # 原本程式碼兩個都傳，可能是導致查不到資料的原因（單日模式跟區間模式的API
    # 行為可能不同）。改成只傳 start_date，並在拿到結果後自己過濾只留目標日期，
    # 這樣不管 FinMind 實際上是回傳單日還是一段區間，行為都是可預期、正確的。
    params = {'dataset': 'TaiwanStockInstitutionalInvestorsBuySell', 'start_date': target_date}
    if token:
        params['token'] = token
    try:
        payload = _finmind_get(url, params)
        df = pd.DataFrame(payload.get('data', []))
        if df.empty:
            return [], "FinMind 回傳空結果（可能該日尚未公布，或選到非交易日）"
        if 'date' in df.columns:
            df = df[df['date'].astype(str) == str(target_date)]
        if df.empty:
            return [], f"回應中沒有 {target_date} 這天的資料（可能該日尚未公布）"
        df['net'] = (pd.to_numeric(df['buy'], errors='coerce').fillna(0)
                     - pd.to_numeric(df['sell'], errors='coerce').fillna(0))
        piv = df.pivot_table(index=['date', 'stock_id'], columns='name',
                             values='net', aggfunc='sum').reset_index()
        rows = []
        for _, r in piv.iterrows():
            sym = str(r['stock_id']).strip()
            if not sym:
                continue
            rows.append({
                'date': str(r['date']),
                'symbol': sym,
                'foreign_buy': int(float(r.get('Foreign_Investor', 0) or 0) / 1000),
                'trust_buy': int(float(r.get('Investment_Trust', 0) or 0) / 1000),
                'dealer_buy': int(float(r.get('Dealer', 0) or 0) / 1000),
            })
        return rows, None
    except FinMindAPIError as e:
        # 【V160】把實際的 HTTP 狀態碼一起顯示出來——例如 402 代表方案權限不足、
        # 403 代表拒絕存取，兩者的處理方式完全不同，只寫「連線失敗」看不出差別。
        return [], f"API錯誤：{_reason_to_label(e.reason)}｜{e.reason}｜{e.detail}"
    except Exception as e:
        return [], f"例外：{type(e).__name__}: {e}"


def fetch_market_turnover_ranking():
    """
    【V160 新增】抓全市場「當日成交值」排行，用來把掃描池排序成「最值得看的前N檔」。

    解決的問題：GLOBAL_MARKET_CODES 原本只按股票代碼數字排序（round 14 的修正），
    所以「前400檔」其實是代碼小的400檔，跟「值不值得掃描」無關——
    代碼1101的水泥股不見得比代碼6488的環球晶更該進掃描池。

    做法：兩支免費官方端點各一次呼叫，各自涵蓋上市/上櫃全部個股：
      上市：TWSE STOCK_DAY_ALL（個股日成交資訊，含成交金額）
      上櫃：TPEx tpex_mainboard_daily_close_quotes（上櫃日收盤行情）
    依成交值由大到小排序回傳代碼清單。任一邊失敗就只用另一邊，兩邊都失敗回空 list
    （呼叫端會退回原本的代碼排序，不會整個壞掉）。
    """
    ranked = []

    # 上市
    try:
        res = _SESSION.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=8)
        if res.status_code == 200:
            for item in res.json():
                code = str(item.get('Code', '')).strip()
                if len(code) != 4 or not code.isdigit():
                    continue
                val = safe_float(item.get('TradeValue', 0))
                if val > 0:
                    ranked.append((code, val))
    except Exception as e:
        print(f"[成交值排行] 上市端點失敗：{e}")

    # 上櫃
    try:
        res = _SESSION.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
                           timeout=8)
        if res.status_code == 200:
            for item in res.json():
                code = str(item.get('SecuritiesCompanyCode', item.get('Code', ''))).strip()
                if len(code) != 4 or not code.isdigit():
                    continue
                # 櫃買欄位名稱與證交所不同，兩種都試（含千分位逗號要先清掉）
                raw = item.get('TradingAmount', item.get('TradeValue', 0))
                val = safe_float(str(raw).replace(',', ''))
                if val > 0:
                    ranked.append((code, val))
    except Exception as e:
        print(f"[成交值排行] 上櫃端點失敗：{e}")

    ranked.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in ranked]


def check_data_source_health(token=None):
    """
    【V160 新增】資料源健康度檢查——直接針對「靜默失敗」這個結構性風險。

    背景：round 6/7/9 連續三次踩到同一種坑——證交所改欄位名、營收參數矛盾、
    資料源本質限制，畫面上全都只顯示「查無資料」，沒人知道底層其實壞了，
    每次都拖了好幾輪才從畫面異常反推出來。這個函式把「壞掉」跟「本來就沒資料」
    分開，讓問題在發生當天就被發現，而不是等你察覺畫面怪怪的。

    檢查方式：對每個資料源打一次最小成本的請求，用「一定會有值的已知標的」驗證，
    回傳每個來源的 ok/失敗原因。刻意不做重試——這裡要偵測的是狀態，不是要救援。

    回傳 list of dict: {name, ok, detail}
    """
    results = []

    def _add(name, ok, detail):
        results.append({'name': name, 'ok': bool(ok), 'detail': str(detail)})

    # 1) yfinance 股價（整個系統的地基，壞了什麼都不用談）
    try:
        hist, _ = get_real_stock_data_yfinance('2330')
        _add('yfinance 股價', hist is not None and len(hist) > 20,
             f"取得 {len(hist) if hist is not None else 0} 根K棒")
    except Exception as e:
        _add('yfinance 股價', False, f"例外：{e}")

    # 2) FinMind 法人（用單檔模式測，因為「全市場模式」是付費方案專屬）
    try:
        url = 'https://api.finmindtrade.com/api/v4/data'
        params = {'dataset': 'TaiwanStockInstitutionalInvestorsBuySell',
                  'data_id': '2330',
                  'start_date': (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d')}
        if token:
            params['token'] = token
        _payload = _finmind_get(url, params)
        _n = len(_payload.get('data', []))
        _add('FinMind 法人(單檔)', _n > 0, f"2330 近10天取得 {_n} 列")
    except FinMindAPIError as e:
        _add('FinMind 法人(單檔)', False, f"{_reason_to_label(e.reason)}（{e.reason}）")
    except Exception as e:
        _add('FinMind 法人(單檔)', False, f"例外：{e}")

    # 3) FinMind 月營收（2330 一定有營收，抓不到就是壞了）
    try:
        rev = fetch_finmind_revenue('2330', token)
        _add('FinMind 月營收', bool(rev and rev.get('ok')),
             rev.get('month', '無回應') if rev else '無回應')
    except Exception as e:
        _add('FinMind 月營收', False, f"例外：{e}")

    # 4) 證交所除權息預告表（欄位名稱改過一次，最容易再壞的地方）
    try:
        divs = fetch_twse_dividends()
        _add('證交所除權息表', isinstance(divs, dict) and len(divs) > 0,
             f"取得 {len(divs) if divs else 0} 檔")
    except Exception as e:
        _add('證交所除權息表', False, f"例外：{e}")

    # 5) 成交值排行（掃描池排序依賴這個）
    try:
        rank = fetch_market_turnover_ranking()
        _add('全市場成交值排行', len(rank) > 100, f"取得 {len(rank)} 檔")
    except Exception as e:
        _add('全市場成交值排行', False, f"例外：{e}")

    # 6) 產業分類（族群輪動依賴這個）
    try:
        s2i, _ = fetch_industry_map()
        _add('FinMind 產業分類', len(s2i) > 100, f"取得 {len(s2i)} 檔")
    except Exception as e:
        _add('FinMind 產業分類', False, f"例外：{e}")

    return results


def fetch_industry_map():
    """
    【V159 新增，簡化版產業鏈】用 FinMind TaiwanStockInfo 一次性批次拉取產業分類，
    取代真正的供應鏈上下游圖譜（那個要維護一份供應鏈關聯資料庫，工程量太大）。
    這裡只做「同產業分類」，用來快速看同族群個股今日強弱，滿足「找同族群輪動股」
    這個實際需求的大部分場景，但不是真正的上下游供應鏈關聯。
    回傳 (stock_to_industry, industry_to_stocks) 兩個字典。
    """
    url = 'https://api.finmindtrade.com/api/v4/data'
    try:
        payload = _finmind_get(url, {'dataset': 'TaiwanStockInfo'}, max_retries=2, timeout=10)
        df = pd.DataFrame(payload.get('data', []))
        if df.empty or 'industry_category' not in df.columns:
            return {}, {}
        stock_to_ind = dict(zip(df['stock_id'], df['industry_category']))
        ind_to_stocks = {}
        for sid, ind in stock_to_ind.items():
            if not ind:
                continue
            ind_to_stocks.setdefault(ind, []).append(sid)
        return stock_to_ind, ind_to_stocks
    except Exception:
        return {}, {}


TW_STOCK_NAMES = fetch_stock_names()
DIVIDEND_DB = fetch_twse_dividends()
# 【V160 修復】總指揮官問「族群輪動400檔夠不夠」時發現：這份清單原本是直接照
# FinMind API 回應的原始順序（未排序），代表「前N檔」是任意子集，不是穩定、
# 可預期的樣本——用滑桿調整掃描檔數時，樣本組成會隨 API 回應順序隨機變動，
# 沒有代表性可言。改成依股票代碼數字排序，至少讓「前N檔」是穩定、可重現的子集
# （代碼小的公司在台股編碼慣例上通常是較早上市的傳產/權值股，不完美但比隨機順序好）。
# 這不是完美解（理想上該按成交量/市值排序），但零額外成本、立即可用。
def _sort_key(code):
    try:
        return (0, int(code))   # 純數字代碼優先，按數值排序
    except ValueError:
        return (1, code)        # 非純數字（如带字母的代碼）排在後面，字母序
GLOBAL_MARKET_CODES = sorted(TW_STOCK_NAMES.keys(), key=_sort_key)


@st.cache_data(ttl=3600 * 6, show_spinner=False)
def get_scan_pool_ordered():
    """
    【V160 新增】把掃描池改成「依當日成交值由大到小」排序。

    為什麼重要：掃描池滑桿設300檔時，取的應該是「最值得看的300檔」，
    而不是「代碼數字最小的300檔」。成交值是最直接的「市場關注度」代理指標——
    成交值大代表有資金在裡面，才有籌碼訊號可言；冷門股就算技術面型態漂亮，
    也常因為量太小而無法成交或滑價嚴重。

    抓不到排行時（假日、端點異常）誠實退回原本的代碼排序，不讓功能整個停擺。
    快取6小時，一天最多打2次，額度成本可忽略。
    """
    ranked = fetch_market_turnover_ranking()
    if not ranked:
        return GLOBAL_MARKET_CODES, False
    known = set(TW_STOCK_NAMES.keys())
    ordered = [c for c in ranked if c in known]
    # 排行裡沒出現的（當日無成交等）接在後面，確保沒有股票被永久排除
    rest = [c for c in GLOBAL_MARKET_CODES if c not in set(ordered)]
    return ordered + rest, True



def _yf_ticker(sym):
    """新版 yfinance 對 requests.Session 有相容性問題，做雙軌降級。"""
    try:
        return yf.Ticker(sym, session=_SESSION)
    except Exception:
        return yf.Ticker(sym)


@st.cache_data(ttl=60, show_spinner=False)
def get_market_weather_real():
    """
    【V160 修復】改成證交所官方資料優先（比 yfinance 對台股指數更準確即時），
    yfinance ^TWII 當備援。使用者回報 yfinance 顯示的大盤數字跟實際差了超過1000點，
    這種量級的落差不是單純延遲能解釋的，判斷是 yfinance 對非美股指數的資料品質問題，
    改用官方來源優先解決。
    """
    # 主要來源：證交所官方每日指數（依名稱比對「發行量加權股價指數」，不用脆弱的陣列位置）
    try:
        today_str = datetime.now().strftime('%Y%m%d')
        resp = _SESSION.get("https://www.twse.com.tw/exchangeReport/MI_INDEX",
                            params={"response": "json", "date": today_str, "type": "IND"}, timeout=6)
        data = resp.json()
        for row in data.get("data1", []) or data.get("data9", []):
            if isinstance(row, list) and len(row) >= 2 and "發行量加權股價指數" in str(row[0]):
                c_idx = float(str(row[1]).replace(",", ""))
                # 漲跌欄位格式可能因官方API版本而異，這裡保守解析：
                # 抓不到方向就顯示中性（灰色、無箭頭），優先確保「指數數值」本身正確，
                # 不冒險猜錯漲跌方向誤導判斷。
                change_pt, change_pct = 0.0, 0.0
                arrow, color = "●", "#ccc"
                try:
                    change_str = str(row[2]) if len(row) > 2 else ""
                    m = re.search(r'-?\d[\d,]*\.?\d*', change_str.replace(",", ""))
                    if m:
                        change_pt = float(m.group())
                        change_pct = round((change_pt / (c_idx - change_pt)) * 100, 2) if (c_idx - change_pt) else 0.0
                        arrow = "▲" if change_pt > 0 else ("▼" if change_pt < 0 else "▬")
                        color = "#ff4d4d" if change_pt > 0 else ("#00c853" if change_pt < 0 else "#999")
                except Exception:
                    pass
                return f"{c_idx:,.0f} ({arrow} {abs(change_pt):,.0f}點 | {change_pct:+.2f}%)", color, change_pct
    except Exception:
        pass
    # 備援：yfinance ^TWII
    try:
        tk = _yf_ticker("^TWII")
        hist = tk.history(period="10d", timeout=6)
        if not hist.empty and len(hist) >= 2:
            c_idx, prev_idx = float(hist['Close'].iloc[-1]), float(hist['Close'].iloc[-2])
            change_pt = round(c_idx - prev_idx, 2)
            change_pct = round((change_pt / prev_idx) * 100, 2) if prev_idx else 0.0
            arrow = "▲" if change_pt > 0 else ("▼" if change_pt < 0 else "▬")
            color = "#ff4d4d" if change_pt > 0 else ("#00c853" if change_pt < 0 else "#999")
            return f"{c_idx:,.0f} ({arrow} {abs(change_pt):,.0f}點 | {change_pct:+.2f}%)（備援來源）", color, change_pct
    except Exception:
        pass
    return "大盤連線中...", "#888", 0.0


@st.cache_data(ttl=300, show_spinner=False)
def get_market_regime():
    """【任務二】大盤位階風控濾網：TWII 收盤 vs 20MA。"""
    try:
        tk = _yf_ticker("^TWII")
        # 【V160 修復】總指揮官回報登入後卡在「位階濾網：計算中」5分鐘以上不動。
        # 這裡原本沒設 timeout，網路壅塞或yfinance後端變慢時會無上限地卡住，
        # 拖累整個開機流程（這個函式在頁面一開始就會被呼叫）。加上6秒逾時，
        # 抓不到就照原本的邏輯走 except 分支，不會讓使用者無限等待。
        hist = tk.history(period="3mo", timeout=6)
        hist = hist.dropna(subset=['Close'])
        if len(hist) >= 20:
            close = float(hist['Close'].iloc[-1])
            ma20 = float(hist['Close'].tail(20).mean())
            dev = (close - ma20) / ma20 * 100 if ma20 else 0.0
            return {'close': close, 'ma20': ma20, 'bull': close >= ma20,
                    'dev': dev, 'known': True}
    except Exception:
        pass
    # 抓不到大盤時「不降級」，避免誤殺；但明確標示未知
    return {'close': 0.0, 'ma20': 0.0, 'bull': True, 'dev': 0.0, 'known': False}


weather_str, weather_color, global_twii_gain = get_market_weather_real()
MARKET_REGIME = get_market_regime()


@st.cache_data(ttl=600, show_spinner=False)
def get_overnight_macro():
    """
    【V160 A階段】隔夜總經 HUD：抓那斯達克、標普500、費半SOX、美元台幣、TSM/UMC ADR。
    這些是台股（尤其電子權值）的先行指標，供開盤前判斷+系統選股閘門使用。
    每個標的獨立 try + 5秒逾時，抓不到就標示、不影響其他標的、也不會拖慢整體載入。
    【V160 移除】台指期(FITX=F)已移除——Yahoo沒有可靠的免費台指期即時資料，這類期貨
    即時報價通常是券商付費API才有，長期顯示「無資料」對總指揮官沒有實質幫助，直接拿掉。
    開盤前閘門改用那斯達克/標普/費半/NQ期貨/ES期貨判斷，準確度已足夠。
    """
    tickers = {
        '那斯達克': '^IXIC',
        '標普500': '^GSPC',
        '費城半導體': '^SOX',
        '美元台幣': 'TWD=X',
        '台積電ADR': 'TSM',
        '聯電ADR': 'UMC',
        '那斯達克期貨': 'NQ=F',    # 【V160新增】幾乎24小時交易，比昨日美股收盤更即時反映當下情緒
        '標普期貨': 'ES=F',
    }
    out = {}
    for name, sym in tickers.items():
        try:
            tk = _yf_ticker(sym)
            hist = tk.history(period="5d", timeout=5).dropna(subset=['Close'])
            if len(hist) >= 2:
                cur, prev = float(hist['Close'].iloc[-1]), float(hist['Close'].iloc[-2])
                pct = (cur - prev) / prev * 100 if prev else 0.0
                pt_change = cur - prev
                data_date = hist.index[-1].strftime('%m/%d')
                out[name] = {'value': cur, 'pct': round(pct, 2), 'pt_change': round(pt_change, 2),
                            'data_date': data_date, 'ok': True}
            else:
                out[name] = {'value': 0, 'pct': 0, 'pt_change': 0, 'data_date': '', 'ok': False}
        except Exception:
            out[name] = {'value': 0, 'pct': 0, 'pt_change': 0, 'data_date': '', 'ok': False}
    return out


def evaluate_overnight_gate(macro):
    """
    【V160 A階段】開盤前總經閘門：依隔夜表現判斷今日是否適合進場。
    劇變（美股/費半大跌）→ 回 'halted' 暫緩系統下單。
    回傳 (status, reason)。status: 'normal' / 'halted'。
    """
    if not macro:
        return 'normal', '無隔夜資料，預設正常'
    danger = []
    for key in ('那斯達克', '標普500', '費城半導體', '那斯達克期貨', '標普期貨'):
        d = macro.get(key, {})
        if d.get('ok') and d.get('pct', 0) <= -2.0:
            danger.append(f"{key} {d['pct']:+.1f}%")
    if danger:
        return 'halted', "隔夜劇變：" + "、".join(danger) + "，暫緩今日進場"
    return 'normal', '隔夜總經平穩'



@st.cache_data(ttl=120, show_spinner=False)
def calc_weekly_resonance(hist):
    """
    【V160 延伸3】多時間框架共振：把日線資料重新取樣成週線，判斷週線趨勢方向。

    為什麼要這個：目前所有訊號都基於日線。日線雜訊大，常出現「日線轉強但其實
    只是下降趨勢裡的反彈」。加上週線確認，能過濾掉相當比例的假突破——這是最
    經典的假訊號過濾器，也是「買在反彈」與「買在反轉」的分水嶺。

    成本考量：刻意用既有的日線資料 resample，不另外呼叫 yfinance 抓週線，
    所以這個功能完全不增加 API 負擔與載入時間。

    回傳 dict：
      trend: 'bull'／'bear'／'neutral'／'unknown'（資料不足時誠實回 unknown，不猜）
      close/ma5/ma10: 週線數值
      bars: 實際可用的週線根數（讓呼叫端知道樣本夠不夠）
    """
    unknown = {'trend': 'unknown', 'close': 0.0, 'ma5': 0.0, 'ma10': 0.0, 'bars': 0}
    if hist is None or len(hist) < 50:
        # 週線MA10需要10根週線＝約50個交易日，不足就誠實說不知道
        return unknown
    try:
        wk = hist.resample('W').agg({
            'Open': 'first', 'High': 'max', 'Low': 'min',
            'Close': 'last', 'Volume': 'sum'}).dropna(subset=['Close'])
        if len(wk) < 10:
            return unknown
        wk_ma5 = wk['Close'].rolling(5).mean()
        wk_ma10 = wk['Close'].rolling(10).mean()
        close = float(wk['Close'].iloc[-1])
        ma5 = float(wk_ma5.iloc[-1]) if pd.notna(wk_ma5.iloc[-1]) else 0.0
        ma10 = float(wk_ma10.iloc[-1]) if pd.notna(wk_ma10.iloc[-1]) else 0.0
        if ma5 <= 0 or ma10 <= 0:
            return unknown
        # MA5 斜率：跟上一根比，判斷週線動能方向
        prev_ma5 = float(wk_ma5.iloc[-2]) if pd.notna(wk_ma5.iloc[-2]) else ma5
        rising = ma5 > prev_ma5

        if close > ma5 and ma5 > ma10 and rising:
            trend = 'bull'
        elif close < ma5 and ma5 < ma10 and not rising:
            trend = 'bear'
        else:
            trend = 'neutral'
        return {'trend': trend, 'close': round(close, 2), 'ma5': round(ma5, 2),
                'ma10': round(ma10, 2), 'bars': len(wk)}
    except Exception:
        return unknown


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


def estimate_main_force_cost(hist, inst_df=None, big_holder_pct=None):
    """
    【V160 延伸2】主力成本的「免費替代估計」。

    背景：真正的主力成本要靠券商分點資料（籌碼K線的招牌功能），但 FinMind 的
    分點資料集限 sponsor 付費方案。這裡用免費資料做合理近似。

    三個估計來源（各有不同的成本語意，刻意分開列出而不是混成一個數字，
    因為它們代表不同的東西，混在一起會失去可解讀性）：
      1. VWAP20／VWAP60：成交量加權平均價 = 「整體市場的平均成本」。
         這是最穩健的代理，因為大資金的成交必然反映在成交量權重上。
      2. 近期爆量日均價：只取成交量前25%的交易日算加權均價。
         大單進場通常伴隨爆量，所以這個數字更偏向「大戶的成本」而非散戶。
      3. 籌碼集中度變化：大戶持股比例的變化方向（需要有大戶資料才算得出來）。

    ⚠️ 這是「估計」不是「實際分點成本」，準確度需要靠校正機制驗證
    （見 sb_log_cost_calibration）。抓不到就回 None，不編造數字。

    回傳 dict 或 None。
    """
    if hist is None or len(hist) < 20:
        return None
    try:
        df = hist.copy()
        # 典型價（TP）比單純用收盤更接近真實成交分布
        tp = (df['High'] + df['Low'] + df['Close']) / 3.0
        vol = df['Volume']

        def _vwap(n):
            t, v = tp.tail(n), vol.tail(n)
            tot = float(v.sum())
            return round(float((t * v).sum() / tot), 2) if tot > 0 else None

        vwap20, vwap60 = _vwap(20), _vwap(min(60, len(df)))

        # 爆量日加權均價：取近60日「成交量最大的前25%個交易日」
        # 【注意】原本寫 r_vol >= r_vol.quantile(0.75)，在成交量分布偏斜時會出錯——
        # 例如50天都是1000張、10天是9000張，quantile(0.75) 會落在1000（因為83%的
        # 值都是1000），用 >= 就把全部60天都選進來，爆量均價退化成普通VWAP。
        # 改用 nlargest 直接取前N大，語意明確且不受分布形狀影響。
        recent = df.tail(min(60, len(df)))
        r_tp = (recent['High'] + recent['Low'] + recent['Close']) / 3.0
        r_vol = recent['Volume']
        _n_heavy = max(3, int(len(r_vol) * 0.25))
        if len(r_vol) >= 8 and float(r_vol.sum()) > 0:
            heavy_idx = r_vol.nlargest(_n_heavy).index
            hv_tot = float(r_vol.loc[heavy_idx].sum())
            heavy_vwap = (round(float((r_tp.loc[heavy_idx] * r_vol.loc[heavy_idx]).sum() / hv_tot), 2)
                          if hv_tot > 0 else None)
            heavy_days = len(heavy_idx)
        else:
            heavy_vwap, heavy_days = None, 0

        cur = float(df['Close'].iloc[-1])
        # 現價相對各成本的乖離：正=市場平均在賺，負=市場平均套牢
        def _dev(base):
            return round((cur - base) / base * 100, 2) if base and base > 0 else None

        return {
            'vwap20': vwap20, 'vwap60': vwap60,
            'heavy_vwap': heavy_vwap, 'heavy_days': heavy_days,
            'dev_vwap20': _dev(vwap20), 'dev_vwap60': _dev(vwap60),
            'dev_heavy': _dev(heavy_vwap),
            'big_holder_pct': big_holder_pct,
            'current': round(cur, 2),
        }
    except Exception:
        return None


def sb_log_cost_calibration(symbol, our_estimate, actual_value, source_note="", broker_name=None):
    """
    【V160 延伸2 校正機制】記錄一筆「我們的估計 vs 你從籌碼K線抄回來的實際值」。

    這是總指揮官提出的構想，我認為它比功能本身更有價值：它把「猜測」變成
    「有已知誤差範圍的估計」。累積夠多筆之後，就能回答「我們的主力成本估計
    平均差多少%」——如果誤差穩定在10%內就可以信任，如果忽大忽小代表這個
    估計法在某些股票上不適用，而這個資訊本身就有用。

    【V160 新增】broker_name：記錄這筆數字是哪家券商的買均價（或"三家均值"），
    讓 summarize_calibration_by_broker 能分券商統計，回答「哪家券商的買均價
    跟我們的估計比較一致」。
    """
    def _do():
        return SUPABASE_CONN.table("cost_calibration").insert({
            "symbol": str(symbol),
            "log_date": datetime.now().strftime('%Y-%m-%d'),
            "our_estimate": float(our_estimate),
            "actual_value": float(actual_value),
            "error_pct": round((float(our_estimate) - float(actual_value))
                               / float(actual_value) * 100, 2) if float(actual_value) else None,
            "source_note": source_note,
            "broker_name": broker_name,
        }).execute()
    ok, _ = _sb_safe(_do)
    return ok


def summarize_calibration_by_broker(rows):
    """
    【V160 新增】把校正紀錄按券商分組，回答總指揮官的問題：
    「前五大券商裡，哪家的買均價數字跟我們的估計比較一致？」

    ⚠️ 誠實說明這個比較的真正意義：我們沒有「絕對正確」的主力成本可以當標準答案，
    能比的只是「哪家券商的買均價，長期下來跟我們的免費估計法算出的數字比較接近」。
    這回答的是「哪家券商的數字最貼近我們的估計」，不是「哪家券商客觀上最準」——
    如果我們的估計法本身有系統性偏差，這個排名也會跟著偏。這點必須先講清楚，
    不能讓這個功能看起來像在下一個它給不出的結論。

    回傳 dict: {券商名稱: {筆數, 平均絕對誤差, 系統性偏差}}，依平均絕對誤差排序（越準排越前面）。
    """
    if not rows:
        return {}
    by_broker = {}
    for r in rows:
        b = r.get('broker_name') or r.get('source_note') or '未分類'
        by_broker.setdefault(b, []).append(r)
    out = {}
    for b, rs in by_broker.items():
        s = summarize_calibration(rs)
        if s:
            out[b] = s
    return dict(sorted(out.items(), key=lambda kv: kv[1]['mean_abs_err']))


def sb_get_cost_calibration(symbol=None):
    """讀取校正紀錄。symbol=None 讀全部（用來算整體平均誤差）。"""
    def _do():
        q = SUPABASE_CONN.table("cost_calibration").select("*")
        if symbol:
            q = q.eq("symbol", str(symbol))
        return q.order("log_date", desc=True).limit(500).execute()
    ok, res = _sb_safe(_do)
    return res.data if (ok and res is not None and getattr(res, "data", None)) else []


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


def compute_industry_rotation(codes, stock_to_ind, min_members=3, max_scan=250):
    """
    【V160 延伸1】族群輪動熱力圖：算出各產業在 1日／5日／20日 的平均漲跌幅與資金集中度。

    為什麼這是投報率最高的一項：這是籌碼K線的核心賣點之一（產業即時、資金流向），
    但我們用「既有的產業分類 + 既有的股價資料」就能做，不需要任何付費 API。

    對勝率的實際幫助：個股會漲通常是因為整個族群在動。先確認族群趨勢再選個股，
    等於多一層過濾，能降低「選對股但選錯時機」的虧損。

    ⚠️ 誠實限制：這是「同產業分類」的族群強弱，不是真正的供應鏈上下游關聯。
    抓不到資料的股票直接略過，不用0填補（那會把整個產業的平均拉偏）。

    回傳 list of dict，依 5日報酬排序。
    """
    if not codes or not stock_to_ind:
        return []
    # 控制掃描量：產業輪動看的是族群趨勢，不需要掃全市場每一檔
    pool = list(codes)[:max_scan]
    by_ind = {}
    for code in pool:
        ind = stock_to_ind.get(code)
        if ind:
            by_ind.setdefault(ind, []).append(code)
    # 成員太少的產業統計上沒有代表性，直接不列（不是填0）
    by_ind = {k: v for k, v in by_ind.items() if len(v) >= min_members}
    if not by_ind:
        return []

    rows = []
    for ind, members in by_ind.items():
        r1, r5, r20, vols = [], [], [], []
        for code in members:
            hist, _ = get_real_stock_data_yfinance(code)
            if hist is None or len(hist) < 21:
                continue
            try:
                closes = hist['Close']
                c0 = float(closes.iloc[-1])
                if c0 <= 0:
                    continue
                c1 = float(closes.iloc[-2])
                c5 = float(closes.iloc[-6])
                c20 = float(closes.iloc[-21])
                if c1 > 0:
                    r1.append((c0 - c1) / c1 * 100)
                if c5 > 0:
                    r5.append((c0 - c5) / c5 * 100)
                if c20 > 0:
                    r20.append((c0 - c20) / c20 * 100)
                # 成交值 = 收盤 × 成交量（張），當作資金流向的代理
                vols.append(float(hist['Volume'].iloc[-1]) * c0)
            except (IndexError, ValueError, TypeError):
                continue
        if not r5:
            continue
        rows.append({
            '產業': ind,
            '檔數': len(r5),
            '1日%': round(sum(r1) / len(r1), 2) if r1 else None,
            '5日%': round(sum(r5) / len(r5), 2),
            '20日%': round(sum(r20) / len(r20), 2) if r20 else None,
            '成交值(億)': round(sum(vols) / 1e8, 2) if vols else None,
        })
    rows.sort(key=lambda x: x['5日%'], reverse=True)
    # 資金集中度：各產業成交值佔本次統計總成交值的比重
    total_val = sum(r['成交值(億)'] or 0 for r in rows)
    for r in rows:
        r['資金佔比%'] = (round((r['成交值(億)'] or 0) / total_val * 100, 2)
                        if total_val > 0 else None)
    return rows


def build_rotation_advice(rows):
    """
    【V160 延伸1】把熱力圖數字轉成「所以我該往哪找股票」的結論。
    判讀標準寫死並公開，讓你知道建議怎麼來的，不是黑箱。
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
    out.append("＿＿＿\n提醒：這是「同產業分類」的族群強弱，不是真正的供應鏈上下游關聯；"
               "且統計只涵蓋本次掃描池內的股票，不是全市場普查。")
    return out


@st.cache_data(ttl=180, show_spinner=False)
def get_real_stock_data_yfinance(symbol):
    # 【V160 關鍵修復】總指揮官回報開機/重整要等5分鐘。真正根因找到了：這個函式
    # 原本完全沒有 @st.cache_data 裝飾器。Streamlit 的執行模型是「每次任何互動
    # （點擊、勾選、拉滑桿……）都會把整支程式從頭到尾重新執行一遍」——沒有快取，
    # 代表持倉/雷達/觀察清單裡的「每一檔股票」在「每一次互動」都會重新對 yfinance
    # 打一次網路請求。如果清單裡有30-50檔，每檔抓價1-3秒，累加起來就是動輒
    # 3-5分鐘，而且不只是開機會這樣，之後每點一下畫面都會重跑一次。
    # （程式裡原本就有一行註解「讓子執行緒掛上 Streamlit context，st.cache_data
    # 才會生效」——這代表原始設計本來就預期這裡有快取，但裝飾器不知道什麼原因
    # 沒有真的加上去，這是個遺漏不是刻意設計。）
    # 加上 ttl=180（3分鐘）：對這種本來就有延遲的免費資料來源，3分鐘的快取
    # 新鮮度足夠，但能讓「同一檔股票在3分鐘內的重複互動」直接命中快取、不再
    # 重新打網路，這是目前找到影響最大的一個修復。
    # 【V160 新增】上次成功過就記住格式，優先試——省掉上櫃股每次都要先錯誤
    # 嘗試「上市格式」兩次（各等到逾時）才輪到正確格式的浪費時間。
    _hint = _EXT_HINT.get(symbol)
    _ext_order = [_hint] + [e for e in (".TW", ".TWO") if e != _hint] if _hint else [".TW", ".TWO"]

    for ext in _ext_order:
        for use_session in (True, False):
            try:
                tk = yf.Ticker(symbol + ext, session=_SESSION) if use_session else yf.Ticker(symbol + ext)
                # auto_adjust=False → 保留實際成交價，與券商報價一致
                # 【V160 修復】這是掃描/戰卡最高頻呼叫的函式，原本沒設 timeout，
                # 一檔卡住就可能拖累整個掃描/開機流程。加上8秒逾時保護。
                hist = tk.history(period="6mo", auto_adjust=False, timeout=8).dropna(subset=['Close'])
                hist = hist[hist['Volume'] > 0]
                if hist.empty or len(hist) <= 20:
                    continue
                hist = hist.copy()
                hist['Volume'] = hist['Volume'] / 1000.0   # 股 → 張
                try:
                    info = tk.info
                except Exception:
                    info = {}
                _EXT_HINT[symbol] = ext   # 記住這次成功的格式，下次直接先試
                return hist.tail(120), info
            except Exception:
                continue
    return None, {}


# ==============================================================================
# 四、 動態技術指標與 ATR 交易邏輯
# ==============================================================================
def render_kline_chart(symbol, hist):
    """
    【V160 新功能】互動式K線圖：蠟燭線 + 5/20/60MA + 成交量 + MACD動能。
    用 plotly 畫，Streamlit 內建支援。補上「數據卡片流缺視覺化K線」的短板。
    hist: get_real_stock_data_yfinance 回傳的 OHLCV DataFrame。
    """
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        st.warning("K線圖需要 plotly 套件。請在 requirements.txt 加入 plotly 後重新部署。")
        return
    if hist is None or len(hist) < 5:
        st.caption("股價資料不足，無法繪製K線圖。")
        return

    # 【V160】MACD 用完整歷史算（需要較長資料才準），再取近60日顯示
    # 【V160 修復】總指揮官回報K線圖顯示異常：蠟燭只擠在左邊一小撮、右邊一大片空白。
    # 這是 Plotly 日期軸的典型症狀——如果索引裡有任何重複或不連續的日期（例如
    # 快取交界處新舊資料合併時order亂掉），Plotly會照「實際日期跨度」畫x軸，
    # 而不是照「有幾根K棒」畫，一旦日期跨度異常放大，真正有資料的部分就會被
    # 壓縮成一小撮。防禦性修復：在最源頭（_full）就先排序、去重，這樣後面所有
    # 從 _full 算出來的 MA/RSI 用 .tail(60) 對齊到 df 索引時才不會因為兩邊索引
    # 不一致而產生對不上的NaN。再搭配下面把x軸改成「類別軸」雙重保險。
    _full = hist[~hist.index.duplicated(keep='last')].sort_index().copy()
    _ema12 = _full['Close'].ewm(span=12, adjust=False).mean()
    _ema26 = _full['Close'].ewm(span=26, adjust=False).mean()
    _dif = _ema12 - _ema26                          # DIF（快線）
    _dea = _dif.ewm(span=9, adjust=False).mean()    # DEA/MACD（慢線）
    _osc = _dif - _dea                              # 柱狀體（動能）
    _full['DIF'], _full['DEA'], _full['OSC'] = _dif, _dea, _osc

    df = _full.tail(60).copy()   # 近60個交易日
    df['MA5'] = _full['Close'].rolling(5).mean().tail(60)
    df['MA20'] = _full['Close'].rolling(20).mean().tail(60)
    df['MA60'] = _full['Close'].rolling(60).mean().tail(60)
    # 【V160 新增】RSI 用完整歷史算（14日需要足夠資料才準），再取近60日顯示，
    # 沿用既有的 calc_rsi() 函式，跟戰卡上顯示的 RSI(14) 是同一套算法，不會兩邊對不上。
    _full['RSI'] = calc_rsi(_full, period=14)
    df['RSI'] = _full['RSI'].tail(60)

    # 四個子圖：K線 / 成交量 / MACD / RSI
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True,
                        vertical_spacing=0.025, row_heights=[0.46, 0.16, 0.19, 0.19])

    # K線（台股習慣：紅漲綠跌）
    fig.add_trace(go.Candlestick(
        x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'],
        increasing_line_color='#ff4d4d', decreasing_line_color='#00c853', name='K線'), row=1, col=1)

    # 均線
    for ma, color in [('MA5', '#f1c40f'), ('MA20', '#00d2ff'), ('MA60', '#e84393')]:
        fig.add_trace(go.Scatter(x=df.index, y=df[ma], line=dict(color=color, width=1.2),
                                name=ma), row=1, col=1)

    # 成交量（顏色跟漲跌一致）
    vol_colors = ['#ff4d4d' if c >= o else '#00c853' for c, o in zip(df['Close'], df['Open'])]
    fig.add_trace(go.Bar(x=df.index, y=df['Volume'], marker_color=vol_colors,
                        name='成交量(張)'), row=2, col=1)

    # 【V160 新增】MACD：DIF快線 + DEA慢線 + 動能柱狀體（紅漲綠跌）
    osc_colors = ['#ff4d4d' if v >= 0 else '#00c853' for v in df['OSC']]
    fig.add_trace(go.Bar(x=df.index, y=df['OSC'], marker_color=osc_colors, name='MACD柱'), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['DIF'], line=dict(color='#f1c40f', width=1),
                            name='DIF'), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['DEA'], line=dict(color='#00d2ff', width=1),
                            name='DEA'), row=3, col=1)

    # 【V160 新增】RSI(14)：70/30 參考線標示超買超賣區
    fig.add_trace(go.Scatter(x=df.index, y=df['RSI'], line=dict(color='#e84393', width=1.3),
                            name='RSI(14)'), row=4, col=1)
    fig.add_hline(y=70, line=dict(color='#ff4d4d', width=0.8, dash='dot'), row=4, col=1)
    fig.add_hline(y=30, line=dict(color='#00c853', width=0.8, dash='dot'), row=4, col=1)

    fig.update_layout(
        height=760, template='plotly_dark', paper_bgcolor='#0e1117', plot_bgcolor='#0e1117',
        margin=dict(l=10, r=10, t=30, b=10), showlegend=True,
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
        xaxis_rangeslider_visible=False,
        title=dict(text=f"{symbol} {TW_STOCK_NAMES.get(symbol, '')} 近60日K線 + MACD + RSI", font=dict(size=14, color='#f1c40f')),
    )
    for _r in (1, 2, 3, 4):
        # type='category'：x軸只看「第幾根K棒」不看「實際日期差幾天」，
        # 徹底消除週末/假日空隙或任何日期不連續造成的視覺壓縮問題，
        # 不管背後資料乾不乾淨，畫出來一定是等距分佈。
        fig.update_xaxes(gridcolor='#1a2030', type='category', row=_r, col=1)
        fig.update_yaxes(gridcolor='#1a2030', row=_r, col=1)
    fig.update_yaxes(title_text="MACD", row=3, col=1)
    fig.update_yaxes(title_text="RSI", range=[0, 100], row=4, col=1)
    st.plotly_chart(fig, use_container_width=True, key=f"kline_{symbol}")


def calc_rsi(df, period=14):
    delta = df['Close'].diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))


def calc_bias(df, period=20):
    ma = df['Close'].rolling(period).mean()
    return (df['Close'] - ma) / (ma + 1e-9) * 100


def calculate_atr(df, period=14):
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


def detect_k_line_patterns_v152(df, atr_val):
    patterns = []
    if len(df) < 5:
        return patterns
    if pd.isna(atr_val) or atr_val == 0:
        atr_val = df['Close'].iloc[-1] * 0.02

    c0, c1, c2 = float(df['Close'].iloc[-1]), float(df['Close'].iloc[-2]), float(df['Close'].iloc[-3])
    o0, o1, o2 = float(df['Open'].iloc[-1]), float(df['Open'].iloc[-2]), float(df['Open'].iloc[-3])
    is_significant = abs(c0 - o0) > atr_val * 0.5

    # 【V160】三兵型態補「實體夠大」門檻：三天平均實體 > 0.3×ATR 才算數，
    # 避免把三天陰跌後的溫吞小紅K誤判成「紅三兵在噴」。
    avg_body_3 = (abs(c0 - o0) + abs(c1 - o1) + abs(c2 - o2)) / 3.0
    three_body_ok = avg_body_3 > atr_val * 0.3

    if (c0 > o0) and is_significant:
        if (c1 < o1) and c0 > o1 and o0 < c1:
            patterns.append({"text": "長紅吞噬", "class": "tag-red"})
        else:
            patterns.append({"text": "低檔長紅", "class": "tag-red"})
    if (c0 > o0) and (c1 > o1) and (c2 > o2) and (c0 > c1 > c2) and three_body_ok:
        patterns.append({"text": "紅三兵", "class": "tag-red"})
    if (c0 < o0) and is_significant:
        if (c1 > o1) and c0 < o1 and o0 > c1:
            patterns.append({"text": "長黑吞噬", "class": "tag-green"})
        else:
            patterns.append({"text": "高檔長黑", "class": "tag-green"})
    if (c0 < o0) and (c1 < o1) and (c2 < o2) and (c0 < c1 < c2) and three_body_ok:
        patterns.append({"text": "黑三兵", "class": "tag-green"})

    # 【V160】真正的「壓縮盤整」判斷（名副其實）：近5日高低區間 < 近20日平均日振幅的某比例，
    # 代表波動收斂、能量壓縮。只有在沒有其他明確型態時才標，作為「醞釀中」的提示。
    if not patterns and len(df) >= 20:
        recent5_range = float(df['High'].tail(5).max() - df['Low'].tail(5).min())
        avg20_daily_range = float((df['High'].tail(20) - df['Low'].tail(20)).mean())
        if avg20_daily_range > 0 and recent5_range < avg20_daily_range * 2.2:
            patterns.append({"text": "壓縮盤整", "class": "tag-neutral"})
    return patterns


def build_trade_zones(current_price, ma5, ma20, atr, hist=None):
    """【任務二】新增動態移動停利：近 20 日最高價回落 1.5×ATR，以及布林上軌。"""
    def_line = round(ma5 - atr * DEF_LINE_ATR_MULT, 2)
    atk_zone = round(current_price + atr, 2)
    buffer_pct = ((current_price - def_line) / current_price) * 100 if current_price > 0 else 0

    trail_stop, bb_upper, high_20 = 0.0, 0.0, 0.0
    if hist is not None and len(hist) >= 20:
        high_20 = float(hist['High'].tail(20).max())
        trail_stop = round(high_20 - 1.5 * atr, 2)
        std20 = float(hist['Close'].tail(20).std())
        bb_upper = round(ma20 + 2.0 * std20, 2)

    # 移動停利只有在「現價仍高於停利線」時才是有效的持股保護
    trail_active = bool(trail_stop > 0 and current_price > trail_stop)

    return {'atk_zone': atk_zone, 'def_line': def_line, 'buffer_pct': round(buffer_pct, 2),
            'atr': round(atr, 2), 'trail_stop': trail_stop, 'trail_active': trail_active,
            'bb_upper': bb_upper, 'high_20': round(high_20, 2)}


# ==============================================================================
# 五、【任務二】法人連續買賣超真實成本 (VWAP) + 估價模型
# ==============================================================================
def calc_inst_streak_vwap(inst_df, hist, col='foreign_buy'):
    """
    從最新一日往回推，找出同方向的「連續買超（或賣超）」區間，
    以該期間每日『典型價 (H+L+C)/3』對法人自身張數加權，算出真實持有成本。
    回傳 None 表示資料不足。
    """
    if inst_df is None or inst_df.empty or hist is None or len(hist) == 0:
        return None

    price_map = {}
    for idx, row in hist.iterrows():
        try:
            d = idx.strftime('%Y-%m-%d')
        except Exception:
            continue
        price_map[d] = (float(row['High']) + float(row['Low']) + float(row['Close'])) / 3.0

    df = inst_df.sort_values('date', ascending=False)
    rows, sign = [], 0
    for _, r in df.iterrows():
        v = safe_float(r.get(col, 0))
        if v == 0:
            break                      # 買賣超為 0 視為斷點
        s = 1 if v > 0 else -1
        if sign == 0:
            sign = s
        elif s != sign:
            break                      # 方向翻轉 → 連續區間結束
        d = str(r['date'])
        p = price_map.get(d)
        if p is None:
            break                      # 找不到對應價格，寧可停止也不亂估
        rows.append((v, p))

    if not rows:
        return None
    total_lots = sum(abs(v) for v, _ in rows)
    if total_lots <= 0:
        return None
    vwap = sum(abs(v) * p for v, p in rows) / total_lots
    net = sum(v for v, _ in rows)
    return {'side': '買超' if sign > 0 else '賣超', 'sign': sign,
            'days': len(rows), 'lots': int(round(net)), 'vwap': round(vwap, 2)}


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_pe_history(symbol, token, years=3):
    """
    【V157 新增】抓取 FinMind 每日本益比／股價淨值比／殖利率歷史序列。
    取代 V156「PE×15合理、PE×20樂觀」的固定倍數——固定倍數對電子股（常態 PE 25~35）
    跟傳產股（常態 PE 10~15）套同一把尺，會系統性誤判。改用「現在的 PE 落在這檔股票
    自己歷史分布的第幾百分位」，概念上等同財報狗的本益比河流圖，且能反映個股／產業特性。
    抓不到或樣本不足時，呼叫端會自動退回舊版固定倍數，不會整段功能掛掉。
    """
    url = 'https://api.finmindtrade.com/api/v4/data'
    start_date = (datetime.now() - timedelta(days=int(365 * years))).strftime('%Y-%m-%d')
    params = {'dataset': 'TaiwanStockPER', 'data_id': symbol, 'start_date': start_date}
    if token:
        params['token'] = token
    try:
        payload = _finmind_get(url, params, max_retries=2, timeout=8)
        df = pd.DataFrame(payload.get('data', []))
        if df.empty:
            return None
        for col in ('PER', 'PBR', 'dividend_yield'):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        return df
    except FinMindAPIError:
        return None


def build_valuation(info, curr_price, rev_yoy, f_5d, cash_div, pe_hist_df=None):
    """
    【V157 升級】戰情室專屬估價模型。
    - 有足夠歷史 PE 樣本（>=60筆）時：用「現在 PE 的歷史百分位」評分，
      並用 25/50/75 百分位 × EPS 算出便宜價／合理價／樂觀價。
    - 樣本不足時（新股、資料源沒有）：退回 V156 的固定倍數，並標記 pe_hist_ok=False，
      UI 端會提示「样本不足，退回估算」，不會假裝有精確依據。
    - 殖利率防守價：現金股利 ÷ 目標殖利率（不變）。
    - 地雷：PE 落在自身歷史最貴 20% 區間（或樣本不足時 PE > 30）且營收衰退且法人賣超。
    """
    eps = safe_float(info.get('trailingEps', 0)) if info else 0.0
    pe = round(curr_price / eps, 1) if eps > 0 and curr_price > 0 else 0.0

    percentile = None
    pe_p25 = pe_p50 = pe_p75 = 0.0
    fair_price = dream_price = cheap_price = 0.0
    pe_hist_ok = False

    valid_pe = None
    if pe_hist_df is not None and not pe_hist_df.empty and 'PER' in pe_hist_df.columns:
        valid_pe = pe_hist_df['PER'].dropna()
        valid_pe = valid_pe[valid_pe > 0]

    if valid_pe is not None and len(valid_pe) >= 60:
        pe_hist_ok = True
        pe_p25 = round(float(valid_pe.quantile(0.25)), 1)
        pe_p50 = round(float(valid_pe.quantile(0.50)), 1)
        pe_p75 = round(float(valid_pe.quantile(0.75)), 1)
        if pe > 0:
            percentile = round(float((valid_pe < pe).mean() * 100), 1)
        if eps > 0:
            cheap_price = round(pe_p25 * eps, 2)
            fair_price = round(pe_p50 * eps, 2)
            dream_price = round(pe_p75 * eps, 2)
    elif eps > 0:
        fair_price = round(eps * PE_FAIR_MULT, 2)
        dream_price = round(eps * PE_DREAM_MULT, 2)

    def_price = round(cash_div / YIELD_DEF_RATE, 2) if cash_div > 0 else 0.0

    score = 40
    if percentile is not None:
        if percentile <= 20:   score += 30     # 現在的估值落在自己歷史最便宜兩成
        elif percentile <= 40: score += 18
        elif percentile <= 60: score += 5
        elif percentile <= 80: score -= 10
        else:                  score -= 20     # 落在自己歷史最貴兩成
    elif eps > 0:
        if pe <= 12:   score += 20
        elif pe <= 18: score += 10
        elif pe > PE_LANDMINE: score -= 12
    else:
        score -= 15                                   # 虧損或無 EPS 資料

    if rev_yoy is not None:
        if rev_yoy > 20:  score += 22
        elif rev_yoy > 0: score += 12
        elif rev_yoy < -10: score -= 18
        elif rev_yoy < 0:   score -= 10

    div_y = (cash_div / curr_price * 100) if curr_price > 0 else 0.0
    if div_y >= 4.5:  score += 15
    elif div_y >= 3.0: score += 8

    # 【V160 修正】原本這裡有「外資5日買超 +10／賣超 -8」的加減分。
    # 總指揮官決定拿掉：外資買賣是「籌碼面」的東西，混進「基本面價值分數」裡會讓
    # 第一戰區的結論不純粹——一檔財報體質很好的股票，可能只因為外資短線調節就被
    # 扣分，那不是它「價值」變差了。拿掉之後第一戰區只看估值、獲利、成長、股利，
    # 外資因子改由第三戰區（籌碼面）獨立評分，兩區才能各自誠實表態、也才能互相矛盾。
    # 註：下面的 landmine（地雷）判定仍保留 f_5d，因為那是刻意設計的「跨面向複合警訊」
    # ——貴 + 營收衰退 + 外資調節三者同時成立才算，不是單一面向的分數。

    score = int(max(0, min(100, score)))

    is_expensive = (percentile is not None and percentile >= 80) or (percentile is None and eps > 0 and pe > PE_LANDMINE)
    landmine = bool(is_expensive and (rev_yoy is not None and rev_yoy < 0) and f_5d < 0)

    # 【V159 新增】PE百分位極端值提示：跟地雷警告不同，這裡不要求營收衰退或法人賣超，
    # 單純標示「現在的估值已經遠遠偏離自己過去3年的常態」，常見於重大題材重估
    # （例如被納入新供應鏈、合作題材發酵），不代表基本面轉差，只是提醒去對照消息面。
    pe_extreme = bool(percentile is not None and percentile >= 95)

    return {'eps': round(eps, 2), 'pe': pe, 'pe_percentile': percentile,
            'pe_p25': pe_p25, 'pe_p50': pe_p50, 'pe_p75': pe_p75, 'pe_hist_ok': pe_hist_ok,
            'fair_price': fair_price, 'dream_price': dream_price, 'cheap_price': cheap_price,
            'def_price': def_price, 'value_score': score, 'landmine': landmine,
            'pe_extreme': pe_extreme, 'div_y': round(div_y, 2)}


def score_zone1_fundamental(c, fin_health=None):
    """
    【V160 新增】第一戰區（基本面）小結論。

    設計原則：只看「這家公司值不值得這個價格」——估值位階、獲利能力、成長性、股利。
    刻意不看外資買賣、不看均線位置，那些分別是第三、第二戰區的事。
    這樣三個戰區才能各自誠實表態，也才可能互相矛盾——而矛盾本身就是資訊。

    直接複用已經算好的 value_score（本輪已移除其中的外資因子，成為純基本面分數），
    不另外發明一套平行的計分邏輯，避免同一件事兩套標準對不起來。

    【V160 新增】fin_health：深度財報分析結果（毛利率/ROE/現金流品質），是按需查詢
    才會有的資料（不在批次掃描裡，見 fetch_financial_health_cached 的說明），
    所以這個參數預設 None——沒查過就不影響分數，查過了才會加減分。
    這樣「查不查深度財報」完全是總指揮官自己的選擇，不會因為沒查而被扣分。

    加分規則（公開、寫死）：
      現金流品質有紅色警訊（帳上賺錢但現金流是負的）→ -10（這是比EPS更難美化的訊號，
      權重給得比毛利率/ROE本身更重）
      ROE ≥ 15% → +8／ROE < 0 → -8
      毛利率 ≥ 30% → +5（產業間毛利率差異很大，這裡門檻刻意設高一點，避免對本來就
      低毛利的傳產股不公平——低於門檻不扣分，只是不加分）

    回傳 (badge, color, reason)。資料不足時誠實回報，不猜。
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
    【V160 新增】第二戰區（技術面）小結論。

    只看價格結構本身：均線排列、MACD 動能、RSI 位階、乖離率、週線趨勢。
    刻意不看基本面、不看法人買賣。

    計分（門檻寫死並公開，讓判斷可被檢視，不是黑箱）：
      均線多頭排列(價>5MA>20MA) +2／價跌破5MA -2／價跌破20MA 再 -1
      MACD 多方動能 +1／空方動能 -1
      RSI >70 -1（過熱易回）／<30 +1（超賣易彈）
      乖離率 >8% -1（短線過度延伸）／<-8% +1
      週線偏多 +1／偏空 -1（多時間框架，跟延伸3同一份資料）
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
    【V160 新增】第三戰區（籌碼面）小結論。

    只看「誰在買、誰在賣、成本在哪」：外資/投信多天期買賣超、法人成本乖離、
    千張大戶趨勢、融資增減。本輪從第一戰區移出的外資因子，正式歸位到這裡。

    計分（門檻公開）：
      外資5日買超 +1／賣超 -1；外資10日同向再 +1/-1（持續性加權）
      投信5日買超 +1／賣超 -1（投信通常波段操作，訊號較外資乾淨）
      現價低於法人成本 +1（法人套牢區，有撐）／高於成本過多 -1
      融資大增 -1（散戶追高，籌碼變亂）
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
    # 10日與5日同向 → 代表不是單日突襲而是持續性買賣，加重權重
    if f10 > 0 and f5 > 0:
        s += 1; bits.append("10日同向續買")
    elif f10 < 0 and f5 < 0:
        s -= 1; bits.append("10日同向續賣")

    if t5 > 0:
        s += 1; bits.append(f"投信5日買超{t5:,.0f}張")
    elif t5 < 0:
        s -= 1; bits.append(f"投信5日賣超{abs(t5):,.0f}張")

    # 法人連續買賣超成本乖離：現價在法人成本之下代表法人套牢，該價位通常有防守意願
    # 【注意】f_vwap 這個 dict 裡只有 vwap/days/lots，沒有預先算好的乖離%，
    # 乖離是顯示時才用現價換算的（見 _fmt_vwap），這裡沿用同一套算法保持一致。
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
        # 融資增加代表散戶用槓桿追價，籌碼相對不安定
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

    if abs_gain >= 32 or (abs_gain >= 20 and vol_ratio >= 2.0):
        level = 'high'
    elif abs_gain >= 20 or (abs_gain >= 12 and vol_ratio >= 1.8):
        level = 'watch'
    else:
        level = 'none'

    return {'flag': level != 'none', 'level': level, 'six_day_gain': round(six_day_gain, 1)}


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

    # 【V160】爆量下殺強制撤退（比照末日熔斷的「一票否決」設計）：
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


# ==============================================================================
# 六、 核心訊號與戰區聚合
# ==============================================================================
def get_intraday_projection(vol_today):
    """
    【V157 新增】統一的「今日推估全天量」計算，讓總量列的量增縮判斷跟爆量比
    使用同一套基準，不再各算各的。
    回傳 (is_intraday, projected_vol_today, time_ratio)：
    - is_intraday=False 時，projected_vol_today 就是 vol_today 本身（已收盤或非交易日）。
    - time_ratio 過小（剛開盤）時的估算值波動很大，UI 端會加註警語，不單獨隱藏數字。
    """
    now = datetime.now()
    if now.weekday() >= 5:
        return False, vol_today, 1.0
    start_time = datetime.combine(now.date(), dt_time(9, 0))
    end_time = datetime.combine(now.date(), dt_time(13, 30))
    if now < start_time:
        return True, 0.0, 0.0
    if now > end_time:
        return False, vol_today, 1.0
    elapsed_mins = (now - start_time).total_seconds() / 60.0
    time_ratio = max(0.05, elapsed_mins / 270.0)   # 下限 0.05，避免開盤瞬間除以極小值失真爆表
    projected = vol_today / time_ratio
    return True, projected, time_ratio


def get_time_weighted_vol_ratio(vol_today, vol_5ma):
    _, projected_vol, _ = get_intraday_projection(vol_today)
    return projected_vol / vol_5ma if vol_5ma > 0 else 0.0


def calculate_signals_worker(symbol, config, ctx=None):
    # 讓子執行緒掛上 Streamlit context，st.cache_data 才會生效
    if ctx is not None:
        try:
            add_script_run_ctx(threading.current_thread(), ctx)
        except Exception:
            pass

    token = config.get('token')                     # 【修復】原本誤寫成 fm_token
    rev_override = config.get('rev_override', {})
    bh_override = config.get('bh_override', {})
    div_override = config.get('div_override', {})
    dividend_db = config.get('dividend_db', {})
    stock_names = config.get('stock_names', {})
    enable_doomsday = config.get('enable_doomsday', False)
    market_bull = config.get('market_bull', True)

    f_single = t_single = d_single = margin_diff = 0.0
    f_5d = t_5d = f_10d = t_10d = 0.0
    f_pct = t_pct = f_5d_pct = t_5d_pct = f_10d_pct = t_10d_pct = 0.0
    big_holder, big_holder_date = 0.0, ""
    latest_db_date = ""
    has_margin = False
    f_vwap = t_vwap = None

    hist, info = get_real_stock_data_yfinance(symbol)
    if hist is None or len(hist) < 21:
        return {"code": symbol, "name": stock_names.get(symbol, symbol), "error": True}

    curr_price = float(hist['Close'].iloc[-1])
    prev_price = float(hist['Close'].iloc[-2])
    open_price = float(hist['Open'].iloc[-1])
    gain = ((curr_price - prev_price) / prev_price) * 100 if prev_price > 0 else 0.0

    # 昨日強勢（供「查8」使用）
    prev2_price = float(hist['Close'].iloc[-3])
    prev_gain = ((prev_price - prev2_price) / prev2_price) * 100 if prev2_price > 0 else 0.0
    is_yesterday_strong = prev_gain > 5.0

    vol_today = int(hist['Volume'].iloc[-1])
    vol_yesterday = int(hist['Volume'].iloc[-2])

    # 【V157 修復】總量增縮列與爆量比列，現在共用同一套「今日推估全天量」基準，
    # 不再發生「總量顯示量縮、爆量比卻顯示爆量」這種自相矛盾的狀況。
    is_intraday, projected_vol_today, time_ratio = get_intraday_projection(vol_today)
    vol_for_compare = projected_vol_today if is_intraday else vol_today
    vol_change_str = calc_volume_change(vol_for_compare, vol_yesterday)
    if is_intraday:
        vol_change_str += " (今日累計推估至收盤，尚未定案)"

    prev_5_vol = hist['Volume'].iloc[-6:-1]
    vol_5d_mean = max(1, int(prev_5_vol.mean())) if len(prev_5_vol) > 0 else vol_today

    if is_intraday:
        vol_ratio = vol_for_compare / vol_5d_mean if vol_5d_mean > 0 else 0.0
        # 開盤剛過幾分鐘時 time_ratio 被下限鎖在 0.05，估算值本來就不穩，加註提醒
        stability_note = " ⚠️數據不穩" if time_ratio <= 0.05 else ""
        vol_ratio_label = f"爆量比: {vol_ratio:.1f}x (盤中估算{stability_note})"
    else:
        vol_ratio = vol_today / vol_5d_mean if vol_5d_mean > 0 else 0.0
        vol_ratio_label = f"爆量比: {vol_ratio:.1f}x"

    ma5 = float(hist['Close'].tail(5).mean())
    ma20 = float(hist['Close'].tail(20).mean())
    ma60 = float(hist['Close'].tail(60).mean()) if len(hist) >= 60 else float(hist['Close'].mean())

    exp1, exp2 = hist['Close'].ewm(span=12, adjust=False).mean(), hist['Close'].ewm(span=26, adjust=False).mean()
    macd_hist = (exp1 - exp2) - (exp1 - exp2).ewm(span=9, adjust=False).mean()
    macd_val = float(macd_hist.iloc[-1]) if not macd_hist.empty and pd.notna(macd_hist.iloc[-1]) else 0.0
    macd_str = f"多方動能 ({macd_val:+.2f})" if macd_val > 0 else f"空方動能 ({macd_val:+.2f})"
    macd_color = "#ff4d4d" if macd_val > 0 else "#00FF00"

    low_min, high_max = hist['Low'].rolling(9).min(), hist['High'].rolling(9).max()
    rsv = (hist['Close'] - low_min) / (high_max - low_min + 1e-9) * 100
    calc_k = rsv.bfill().ffill().ewm(com=2, adjust=False).mean()
    calc_d = calc_k.ewm(com=2, adjust=False).mean()
    kdj_str = (f"金叉 (K:{calc_k.iloc[-1]:.1f})" if calc_k.iloc[-1] > calc_d.iloc[-1]
               else f"死叉 (K:{calc_k.iloc[-1]:.1f})")

    rsi_val = float(calc_rsi(hist).iloc[-1]) if pd.notna(calc_rsi(hist).iloc[-1]) else 50.0
    bias_val = float(calc_bias(hist).iloc[-1]) if pd.notna(calc_bias(hist).iloc[-1]) else 0.0
    atr_val = calculate_atr(hist)

    is_open_high_close_low = (open_price > prev_price) and (curr_price < open_price)

    # 【V160】爆量下殺偵測：爆量比>=2.0 且 當日收黑 且 跌幅明顯 且 收在當日低點附近
    # → 典型主力出貨型態，供 determine_signal 強制撤退規則使用。
    day_high = float(hist['High'].iloc[-1])
    day_low = float(hist['Low'].iloc[-1])
    _day_range = day_high - day_low
    close_near_low = (_day_range > 0 and (curr_price - day_low) / _day_range <= 0.35)
    is_volume_dump = bool(vol_ratio >= 2.0 and curr_price < open_price and gain < -1.0 and close_near_low)

    # 首根長紅（供「查1」主升段突擊使用）：今紅、昨黑、實體 > 0.5 ATR
    o1, c1 = float(hist['Open'].iloc[-2]), prev_price
    body_ref = atr_val if atr_val > 0 else curr_price * 0.02
    is_first_red = (curr_price > open_price) and (c1 < o1) and (abs(curr_price - open_price) > body_ref * 0.5)

    # ---- 籌碼（SQLite 近 30 日） ----
    inst_df = get_inst_data_from_db(symbol, 30)
    if not inst_df.empty:
        latest = inst_df.iloc[0]
        latest_db_date = str(latest['date'])
        f_single = safe_float(latest['foreign_buy'])
        t_single = safe_float(latest['trust_buy'])
        d_single = safe_float(latest['dealer_buy'])
        margin_diff = safe_float(latest['margin'])
        has_margin = abs(margin_diff) > 0

        f_pct = (f_single / vol_today * 100) if vol_today > 0 else 0.0
        t_pct = (t_single / vol_today * 100) if vol_today > 0 else 0.0

        df_5d = inst_df.head(5)
        df_10d = inst_df.head(10)
        f_5d, t_5d = float(df_5d['foreign_buy'].sum()), float(df_5d['trust_buy'].sum())
        f_10d, t_10d = float(df_10d['foreign_buy'].sum()), float(df_10d['trust_buy'].sum())

        vol_5d_sum = max(1, vol_5d_mean * 5)
        vol_10d_sum = max(1, vol_5d_mean * 10)
        f_5d_pct = f_5d / vol_5d_sum * 100
        t_5d_pct = t_5d / vol_5d_sum * 100
        f_10d_pct = f_10d / vol_10d_sum * 100
        t_10d_pct = t_10d / vol_10d_sum * 100

        # 【任務二】連續買賣超真實成本 VWAP
        f_vwap = calc_inst_streak_vwap(inst_df, hist, 'foreign_buy')
        t_vwap = calc_inst_streak_vwap(inst_df, hist, 'trust_buy')

    db_bh = get_latest_big_holder(symbol)
    if db_bh:
        big_holder, big_holder_date = db_bh['percent'], db_bh['date']
    if symbol in bh_override and bh_override[symbol]:
        big_holder = bh_override[symbol].get('ratio', big_holder)
        big_holder_date = f"自訂 {bh_override[symbol].get('date', '')}"

    # ---- 營收 ----
    manual_mode = False
    rev_ok = True
    if symbol in rev_override and rev_override[symbol]:
        ov = rev_override[symbol]
        rev_yoy, rev_mom, rev_month, manual_mode = ov.get('yoy', 0.0), ov.get('mom', 0.0), ov.get('month', "自訂"), True
    else:
        fm_rev = fetch_finmind_revenue(symbol, token)
        rev_yoy, rev_mom, rev_month = fm_rev['yoy'], fm_rev['mom'], fm_rev['month']
        rev_ok = fm_rev.get('ok', True)
        if fm_rev.get('stale'):
            rev_month = f"{rev_month} (沿用)"

    # ---- 股利 ----
    cash_div = 0.0
    manual_div_mode = False
    if symbol in div_override:
        ov = div_override[symbol]
        div_display, div_yield, manual_div_mode = ov.get('display', "自訂資料"), ov.get('yield', 0.0), True
        cash_div = ov.get('cash', 0.0)
    else:
        div_info = dividend_db.get(symbol)
        if div_info:
            cash_div = div_info.get('cash', 0.0)
            d_stock = div_info.get('stock', 0.0)
            div_date_str = div_info.get('date', '')
            div_yield = (cash_div / curr_price) * 100 if curr_price > 0 else 0.0
            # 【V160 修復】原始數字是浮點數運算結果，直接印會出現 0.01999999
            # 這種假精度尾數（總指揮官回報看起來很亂）。四捨五入到小數點後2位，
            # 對股利金額來說已經足夠精確，畫面也乾淨。
            cash_div_disp = round(cash_div, 2)
            d_stock_disp = round(d_stock, 2)
            div_amount_str = (f"息 {cash_div_disp}元 + 權 {d_stock_disp}元"
                              if d_stock_disp > 0 else f"息 {cash_div_disp}元")
            # 【V160 新增】總指揮官回報：只顯示原始日期（如 1150729）看不出這是
            # 「已經除完的過去日期」還是「還沒到的未來日期」，要自己心算比對很麻煩。
            # 這裡明確判讀狀態，直接講結論，不要你猜。
            _div_date_disp = _roc_date_to_display(div_date_str)
            _div_status = _classify_dividend_date(div_date_str)
            if _div_status == 'past':
                div_display = f"✅ 已除權息完（{_div_date_disp}）| {div_amount_str}"
            elif _div_status == 'future':
                div_display = f"📅 預定 {_div_date_disp} | {div_amount_str}"
            else:
                # 日期格式不明或缺漏，但金額有抓到——照實講，不猜狀態
                div_display = f"{div_date_str or '日期未知'} | {div_amount_str}"
        else:
            # 【V160 新增】TWSE 預告表查無此股（可能已過所有近期除權息週期，事件過去後
            # 就從預告表移除了）——先試 FinMind 股利政策表當備援，那邊是永久紀錄不會消失。
            fm_div = fetch_finmind_dividend_fallback(symbol, token)
            if fm_div.get('ok'):
                cash_div = fm_div['cash']
                d_stock_fb = fm_div['stock']
                div_yield = (cash_div / curr_price) * 100 if curr_price > 0 else 0.0
                cash_div_disp, d_stock_disp = round(cash_div, 2), round(d_stock_fb, 2)
                div_amount_str = (f"息 {cash_div_disp}元 + 權 {d_stock_disp}元"
                                  if d_stock_disp > 0 else f"息 {cash_div_disp}元")
                _fb_date_disp = _roc_date_to_display(fm_div['ex_date'])
                _fb_status = _classify_dividend_date(fm_div['ex_date'])
                if _fb_status == 'past':
                    div_display = f"✅ 已除權息完（{_fb_date_disp}）| {div_amount_str}（來源：股利政策表）"
                elif _fb_status == 'future':
                    div_display = f"📅 預定 {_fb_date_disp} | {div_amount_str}（來源：股利政策表）"
                else:
                    div_display = f"{div_amount_str}（來源：股利政策表，日期未知）"
            else:
                cash_div = safe_float(info.get('dividendRate', 0.0)) if info else 0.0
                div_yield = (cash_div / curr_price) * 100 if curr_price > 0 else 0.0
                div_display = (f"無日期 | 息 {cash_div}元" if cash_div > 0
                              else "近期無除權息公告（預告表與股利政策表皆查無資料）")

    # ---- 估價模型（V157：優先用歷史 PE 百分位，樣本不足才退回固定倍數） ----
    pe_hist_df = fetch_pe_history(symbol, token)
    val = build_valuation(info, curr_price, rev_yoy if rev_ok else None, f_5d, cash_div, pe_hist_df)

    zones = build_trade_zones(curr_price, ma5, ma20, atr_val, hist)
    # 【V160 延伸3】多時間框架共振：用既有日線 resample 成週線，不額外打 API
    weekly = calc_weekly_resonance(hist)
    # 【V160 延伸2】主力成本免費替代估計（VWAP + 爆量日均價），純用既有資料
    mf_cost = estimate_main_force_cost(hist, inst_df, big_holder)
    signal_text, color_border, score, reasons = determine_signal(
        curr_price, ma5, ma20, f_single, vol_ratio, is_open_high_close_low, zones['buffer_pct'],
        gain=gain, enable_doomsday=enable_doomsday,
        market_bull=market_bull, landmine=val['landmine'], is_volume_dump=is_volume_dump
    )
    signal_bg = "#3a1515" if "攻擊" in signal_text else ("#153a20" if "防守" in signal_text else "#332b00")

    detected_patterns = detect_k_line_patterns_v152(hist, atr_val)
    disposal_risk = calc_disposal_risk_proxy(hist, vol_ratio)

    closes = hist['Close'].tail(7).tolist()
    while len(closes) < 7:
        closes.append(closes[-1] if closes else 0)
    bars, min_p, max_p = " ▂▃▄▅▆▇█", min(closes), max(closes)
    rng = max_p - min_p if max_p != min_p else 1e-9
    spark_html = "".join([
        f"<span style='color:{'#ff4d4d' if i > 0 and closes[i] > closes[i-1] else ('#00FF00' if i > 0 and closes[i] < closes[i-1] else '#888')}; font-weight:bold;'>"
        f"{bars[max(0, min(7, int((closes[i] - min_p) / rng * 7)))]}</span>" for i in range(7)])

    intraday_trend = ("📉 開高走低·弱勢收下" if is_open_high_close_low
                      else ("🔥 帶量長紅突破" if gain > 2.5 and vol_ratio > 1.2 else "⚖️ 溫和震盪換手"))

    return {
        "code": symbol, "name": stock_names.get(symbol, symbol), "price": curr_price, "gain": gain, "error": False,
        # 【V160 新增】今日開高低——總指揮官回報：有總量/量比，但看不到今天的開盤價與盤中高低點。
        # 這三個值本來就在 hist 最後一列裡，只是先前沒有帶進戰卡。
        "open_today": round(open_price, 2),
        "high_today": round(float(hist['High'].iloc[-1]), 2),
        "low_today": round(float(hist['Low'].iloc[-1]), 2),
        "prev_close": round(prev_price, 2),
        "vol": vol_today, "vol_5d_mean": vol_5d_mean, "vol_change_str": vol_change_str,
        "vol_ratio": vol_ratio, "vol_ratio_label": vol_ratio_label,
        "ma5": ma5, "ma20": ma20, "ma60": ma60,
        "macd_str": macd_str, "macd_color": macd_color, "kdj_str": kdj_str,
        "rsi_val": rsi_val, "bias_val": bias_val, "atr_val": atr_val,
        "f_buy": f_single, "t_buy": t_single, "d_buy": d_single,
        "margin_diff": margin_diff, "has_margin": has_margin,
        "big_holder": big_holder, "big_holder_date": big_holder_date,
        "f_5d": f_5d, "t_5d": t_5d, "f_10d": f_10d, "t_10d": t_10d,
        "f_pct": f_pct, "t_pct": t_pct,
        "f_5d_pct": f_5d_pct, "t_5d_pct": t_5d_pct, "f_10d_pct": f_10d_pct, "t_10d_pct": t_10d_pct,
        "f_vwap": f_vwap, "t_vwap": t_vwap,
        "atk_zone": zones['atk_zone'], "def_line": zones['def_line'], "buffer_pct": zones['buffer_pct'],
        "trail_stop": zones['trail_stop'], "trail_active": zones['trail_active'],
        "weekly": weekly,   # 【V160 延伸3】週線趨勢，供決策橫幅共振判斷用
        "mf_cost": mf_cost,  # 【V160 延伸2】主力成本免費替代估計
        "bb_upper": zones['bb_upper'], "high_20": zones['high_20'],
        "rev_yoy": rev_yoy, "rev_mom": rev_mom, "rev_month": rev_month, "rev_ok": rev_ok,
        "div_display": div_display, "div_yield": div_yield, "manual_div_mode": manual_div_mode,
        "eps": val['eps'], "pe": val['pe'], "fair_price": val['fair_price'],
        "dream_price": val['dream_price'], "cheap_price": val['cheap_price'], "def_price": val['def_price'],
        "pe_percentile": val['pe_percentile'], "pe_p25": val['pe_p25'], "pe_p50": val['pe_p50'],
        "pe_p75": val['pe_p75'], "pe_hist_ok": val['pe_hist_ok'], "pe_extreme": val['pe_extreme'],
        "value_score": val['value_score'], "landmine": val['landmine'],
        "is_first_red": is_first_red, "is_yesterday_strong": is_yesterday_strong,
        "disposal_risk": disposal_risk,
        "blood_line": config.get('pinned_stocks', {}).get(symbol, "手動強制加入"),
        "signal_text": signal_text, "color_border": color_border, "signal_bg": signal_bg,
        "score": score, "reasons": reasons, "sparkline_html": spark_html,
        "latest_db_date": latest_db_date, "intraday_str": intraday_trend,
        "manual_mode": manual_mode, "detected_patterns": detected_patterns
    }


# ==============================================================================
# 七、 視覺渲染引擎 (HTML 強制扁平化防 Markdown 斷行)
# ==============================================================================
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
    return (f"<div style='font-size:12px; color:#bbb;'><span class='m-tooltip'>{label}{tip}</span>: "
            f"連續{v['side']} <strong style='color:{color};'>{v['days']}日 ({v['lots']:+,}張)</strong> | "
            f"成本 <strong style='color:#00d2ff;'>{v['vwap']:.2f}元</strong> | "
            f"現價乖離 <strong style='color:{dev_c};'>{dev:+.1f}%</strong></div>")


def render_stock_card_ui(c, is_portfolio=False, profit=0, roi=0, ent_p=0):
    gain_v = float(c.get('gain', 0))
    gain_c = '#ff4d4d' if gain_v > 0 else ('#00FF00' if gain_v < 0 else '#aaaaaa')
    gain_b = '#3a1515' if gain_v > 0 else ('#153a20' if gain_v < 0 else '#333333')
    portfolio_header = (f"<div style='font-size:14px; margin-bottom:8px; color:#eeeeee;'>持倉成本: {ent_p} | 損益: "
                        f"<strong style='color:{'#ff4d4d' if profit > 0 else '#00FF00'};'>{int(profit):+,} 元</strong> "
                        f"({roi:+.2f}%)</div>") if is_portfolio else ""

    rev_ok = c.get('rev_ok', True)
    yoy_val = c.get('rev_yoy') if rev_ok else None
    mom_val = c.get('rev_mom') if rev_ok else None
    if yoy_val is None:
        yoy_txt, mom_txt, yoy_color, mom_color = "—", "—", "#888", "#888"
    else:
        yoy_val, mom_val = float(yoy_val), float(mom_val)
        yoy_txt, mom_txt = f"{yoy_val:.1f}%", f"{mom_val:.1f}%"
        yoy_color = "#ff4d4d" if yoy_val > 0 else ("#00FF00" if yoy_val < 0 else "#00d2ff")
        mom_color = "#ff4d4d" if mom_val > 0 else "#00FF00"

    sig_t = c.get('signal_text', '')
    # 【V160 B#1+#2】動詞化決策 + 進場價格區間：把系統術語翻成秒讀動詞，並附具體價格帶
    _def_line = float(c.get('def_line', 0) or 0)
    _atk = float(c.get('atk_zone', 0) or 0)
    _price = float(c.get('price', 0) or 0)
    if '偏多攻擊' in sig_t:
        verdict_word, verdict_color, verdict_bg = "🔥 建議進攻", "#ff4d4d", "#3a1515"
        verdict_action = f"參考區間 {_def_line:.1f}〜{_atk:.1f}｜跌破 {_def_line:.1f} 停損"
    elif '觀察偏多' in sig_t:
        verdict_word, verdict_color, verdict_bg = "🟡 觀望偏多", "#ffab00", "#332b00"
        verdict_action = f"站穩 {_price:.1f} 且量能回穩再進，防守 {_def_line:.1f}"
    elif '偏空防守' in sig_t:
        verdict_word, verdict_color, verdict_bg = "🔵 建議撤退", "#2979ff", "#152a3a"
        verdict_action = f"已轉空｜持有者減碼，空手勿接刀"
    elif '轉弱謹慎' in sig_t:
        verdict_word, verdict_color, verdict_bg = "⚠️ 轉弱警戒", "#ff9100", "#3a2a15"
        # 【V160 修復】原本一律寫「跌破 X 應出場」，但當現價已經在防守線之下（急跌股均線落後），
        # 這句話變成馬後炮（它已經跌破了卻叫你等跌破）。改成依現價 vs 防守線動態判斷：
        # 已跌破→提示結構已破、應檢視出場；還在防守線上→才是「跌破 X 應出場」的預警。
        if _def_line > 0 and _price < _def_line:
            verdict_action = f"已跌破 {_def_line:.1f} 均線防線｜結構已轉弱，反彈無力應出場"
        else:
            verdict_action = f"結構轉弱｜守住 {_def_line:.1f}，跌破應出場"
    else:
        verdict_word, verdict_color, verdict_bg = "⚖️ 中性等待", "#888", "#222"
        verdict_action = f"無明確方向｜突破 {_atk:.1f} 或跌破 {_def_line:.1f} 再表態"

    # 【V160 延伸3】多時間框架共振：用週線趨勢調整日線結論。
    # 只降級不升級——升級等於重複計算同一個訊號並放大部位風險。
    # 週線資料不足時完全不調整、也不顯示，不假裝有判斷。
    _weekly = c.get('weekly', {}) or {}
    # 【V160 新增】三個戰區各自的小結論。刻意獨立計算、允許彼此矛盾——
    # 「基本面便宜但技術面轉弱」這種分歧，混成一個總分就會被平均掉看不見。
    # 【V160】深度財報是按需查詢的，查過才會在 session_state 裡；沒查過就是 None，
    # score_zone1_fundamental 會照舊只用 value_score，不會因為沒查而扣分。
    _fh_for_score = st.session_state.get(f'fin_health_{c.get("code")}')
    _z1_badge, _z1_color, _z1_reason = score_zone1_fundamental(c, _fh_for_score)
    _z2_badge, _z2_color, _z2_reason = score_zone2_technical(c)
    _z3_badge, _z3_color, _z3_reason = score_zone3_chips(c)
    _adj_verdict, _reso_note = apply_timeframe_resonance(verdict_word, c.get('score', 0), _weekly)
    if _adj_verdict != verdict_word:
        # 降級後要一併換色，否則會出現「文字寫觀望、底色仍是進攻紅」的矛盾
        _vmap = {
            "🔥 建議進攻": ("#ff4d4d", "#3a1515"),
            "🟡 觀望偏多": ("#ffab00", "#332b00"),
            "🔵 建議撤退": ("#2979ff", "#152a3a"),
            "⚠️ 轉弱警戒": ("#ff9100", "#3a2a15"),
            "⚖️ 中性等待": ("#888", "#222"),
        }
        verdict_word = _adj_verdict
        verdict_color, verdict_bg = _vmap.get(_adj_verdict, ("#888", "#222"))
    if _reso_note:
        verdict_action = f"{verdict_action}<br><span style='color:#7ab8ff;'>{_reso_note}</span>"

    k_patterns = c.get('detected_patterns', [])
    if k_patterns:
        _kt = k_patterns[0].get('text', '')
        _kicon = "📉" if '黑' in _kt else ("🌀" if _kt == '壓縮盤整' else "🔥")
        k_text = f"{_kicon} {_kt}"
    else:
        k_text = "⚖️ 無明顯型態"
    k_tags = f"<span class='k-tag'>{k_text}</span>"
    if c.get('landmine'):
        k_tags += ("<span class='m-tooltip k-tag' style='background:#5a1010; color:#ff8080;'>💀 基本面地雷警告"
                   "<span class='m-tooltiptext'>同時滿足：估值落在自身歷史最貴區間（或PE>30）、最新月營收年減、外資近5日賣超。"
                   "高估值 + 基本面轉差 + 籌碼失守，屬於典型的高處不勝寒結構。</span></span>")

    # 【V159 新增】PE百分位極端值提示：跟地雷不同，不要求基本面轉差，
    # 純粹標示「估值已經遠離自己3年常態」，常見於重大題材重估行情。
    if c.get('pe_extreme') and not c.get('landmine'):
        pctl_disp = c.get('pe_percentile')
        k_tags += (f"<span class='m-tooltip k-tag' style='background:#1a2a4a; color:#7ab8ff;'>⚡ 估值遠離歷史常態"
                   f"<span class='m-tooltiptext'>目前PE落在近3年歷史第{pctl_disp:.0f}百分位，屬於極端偏高。"
                   f"常見於重大題材重估（如新合作案、供應鏈題材發酵），不必然代表基本面轉差，"
                   f"但建議對照近期消息面，確認題材是否具體、能否支撐目前估值，再判斷是否追高。</span></span>")

    # 【V157 新增】簡化版處置/注意股風險提示，明確標註非官方模型，避免使用者誤以為是精算結果
    d_risk = c.get('disposal_risk') or {}
    if d_risk.get('level') == 'high':
        k_tags += (f"<span class='m-tooltip k-tag' style='background:#5a3d10; color:#ffcc66;'>🚨 處置風險提示（簡化版）"
                   f"<span class='m-tooltiptext'>近6個營業日累計漲跌 {d_risk.get('six_day_gain', 0):+.1f}%，激進程度偏高。"
                   f"這只是用「六日累計漲跌+成交量異常」做的簡化代理指標，<b>不是</b>證交所官方判定模型"
                   f"（官方規則涉及近百項法規細節），僅供留意，請勿單獨依賴此標籤做交易決策。</span></span>")
    elif d_risk.get('level') == 'watch':
        k_tags += (f"<span class='m-tooltip k-tag' style='background:#3d3510; color:#e6c34d;'>⚠️ 波動偏大（簡化版）"
                   f"<span class='m-tooltiptext'>近6個營業日累計漲跌 {d_risk.get('six_day_gain', 0):+.1f}%，"
                   f"波動程度已略高於平常，非官方處置判定，僅供參考。</span></span>")

    vol_ratio = float(c.get('vol_ratio', 0))
    price, ma5, ma20 = float(c.get('price', 0)), float(c.get('ma5', 0)), float(c.get('ma20', 0))
    if vol_ratio > 1.5:
        vol_semantic = "⚠️破線殺盤" if price < ma20 else ("🔥帶量上攻" if price > ma5 else "⚠️爆量震盪")
    elif vol_ratio < 0.6:
        vol_semantic = "🧊量縮沉澱"
    else:
        vol_semantic = "⚖️溫和換手"

    tooltip_vol = ("<span class='m-tooltiptext'>爆量比 = 今日量 ÷ 前5日均量。小於0.6為量縮沉澱（多空觀望），"
                   "0.8~1.2為正常換手，大於1.5為爆量（需搭配位階判斷是攻擊或倒貨）。</span>")
    tags_html = (f"<div style='display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin-top:5px;'>"
                 f"<span class='m-tooltip' style='white-space:nowrap; display:inline-block; background:#2a2a2a; padding:2px 8px; border-radius:4px; font-size:12px; color:#e67e22;'>"
                 f"{c.get('vol_ratio_label')} [{vol_semantic}]{tooltip_vol}</span>"
                 f"<span style='white-space:nowrap; display:inline-block; background:#2a2a2a; padding:2px 8px; border-radius:4px; font-size:12px; color:#00FF00;'>"
                 f"{c.get('intraday_str')}</span></div>")

    rsi_v, bias_v = float(c.get('rsi_val', 0)), float(c.get('bias_val', 0))
    rsi_color = "#ff4d4d" if rsi_v > 70 else ("#00c853" if rsi_v < 30 else "#555")
    rsi_txt = "🔴超買" if rsi_v > 70 else ("🟢超賣" if rsi_v < 30 else "⚖️整理")
    bias_color = "#ff4d4d" if bias_v > 5 else ("#2979ff" if bias_v < -5 else "")
    bias_txt = "🔴過熱" if bias_v > 5 else ("🔵超跌" if bias_v < -5 else "")

    tooltip_rsi = ("<span class='m-tooltiptext'>相對強弱指標。大於70超買（追高風險升高，但強勢股可鈍化），"
                   "小於30超賣（短線反彈機率高）。實戰：RSI由50向上突破且帶量，是波段轉強的起手式。</span>")
    rsi_html = (f"<span class='m-tooltip'>RSI(14): <strong style='color:#fff;'>{rsi_v:.1f}</strong> "
                f"<span style='background:{rsi_color}; color:#fff; padding:2px 6px; border-radius:4px; font-weight:bold; font-size:11px;'>{rsi_txt}</span>{tooltip_rsi}</span>")

    tooltip_bias = ("<span class='m-tooltiptext'>股價與20MA的距離。起漲醞釀期通常貼近均線(0%~2%)。"
                    "大於+5%短線過熱（追價風險高，宜等回測均線）；小於-5%超跌（易有反彈，但需確認不是崩跌趨勢）。</span>")
    bias_html = (f"<span class='m-tooltip'>乖離率(20): <strong style='color:{bias_color if bias_color else '#fff'};'>{bias_v:+.2f}%</strong>"
                 + (f" <span style='background:{bias_color}; color:#fff; padding:2px 6px; border-radius:4px; font-weight:bold; font-size:11px;'>{bias_txt}</span>" if bias_txt else "")
                 + f"{tooltip_bias}</span>")

    db_date = str(c.get('latest_db_date', '') or '')
    display_date, warn_icon = " (尚無資料)", ""
    if db_date:
        try:
            dt_obj = datetime.strptime(db_date, "%Y-%m-%d")
            display_date = f" {dt_obj.strftime('%m/%d')}({['一','二','三','四','五','六','日'][dt_obj.weekday()]})"
            tooltip_warn = "<span class='m-tooltiptext'>證交所尚未更新今日籌碼，此為系統尋獲之最新一筆歷史資料。</span>"
            warn_icon = "" if db_date == datetime.now().strftime("%Y-%m-%d") else f"<span class='m-tooltip'> ⚠️{tooltip_warn}</span>"
        except Exception:
            display_date = f" ({db_date})"

    bh_val = c.get('big_holder', 0.0)
    bh_display = f"{bh_val}%" if isinstance(bh_val, (int, float)) and bh_val > 0 else str(bh_val or ERR_NO_DATA)

    sig_t = c.get('signal_text', '')
    if '攻擊' in sig_t:
        sig_tip = "實戰：帶量突破均線糾結、法人同步進場，動能強勁。可順勢operate，但務必用防守線控管。"
    elif '防守' in sig_t or '警告' in sig_t or '轉弱' in sig_t:
        sig_tip = "實戰：可能高檔倒貨、爆量下殺或破線轉弱。已持有者減碼，空手者勿接刀。"
    else:
        sig_tip = "實戰：目前盤整或溫和換手，無明確單向動能。等突破或跌破再表態。"
    tooltip_sig = (f"<span class='m-tooltiptext'><b>[評分級距說明]</b><br>🔥 偏多攻擊 (>= 3分)<br>🟡 觀察偏多 (1~2分)<br>"
                   f"⚖️ 中立震盪 (0分)<br>⚠️ 轉弱謹慎 (-1~-2分)<br>🔵 偏空防守 (<=-3分)"
                   f"<hr style='margin:4px 0; border-color:#9fb3c8;'>{sig_tip}</span>")

    vs = int(c.get('value_score', 0))
    vs_color = "#00c853" if vs >= 60 else ("#f1c40f" if vs >= 40 else "#ff4d4d")
    tooltip_vs = ("<span class='m-tooltiptext'>⚠️這是「綜合評分」不是純估值分數：同時混合了本益比位階、"
                  "營收年增動能、殖利率、外資5日籌碼進出等多個面向加權而成。所以分數高不代表「便宜」，"
                  "而是「估值+動能+籌碼」整體有利；>=60 綜合面偏多，<40 偏弱或體質轉差。看純估值請直接看上方PE百分位。</span>")

    eps_v = float(c.get('eps', 0) or 0)
    pe_v = float(c.get('pe', 0) or 0)
    pe_hist_ok = bool(c.get('pe_hist_ok'))
    pe_pctl = c.get('pe_percentile')
    pe_txt = f"{pe_v:.1f}" if pe_v > 0 else "—"
    fair_txt = f"{c.get('fair_price')}" if float(c.get('fair_price', 0) or 0) > 0 else "—"
    dream_txt = f"{c.get('dream_price')}" if float(c.get('dream_price', 0) or 0) > 0 else "—"
    cheap_txt = f"{c.get('cheap_price')}" if float(c.get('cheap_price', 0) or 0) > 0 else "—"
    defp_txt = f"{c.get('def_price')}" if float(c.get('def_price', 0) or 0) > 0 else "—"

    # 【V157】估價模型改用「歷史 PE 百分位」，每個數字各自掛獨立 tooltip，
    # 不再只有「估價模型」四個字共用一個說明框。
    if pe_hist_ok and pe_pctl is not None:
        pctl_color = "#00c853" if pe_pctl <= 30 else ("#ff4d4d" if pe_pctl >= 70 else "#f1c40f")
        pctl_txt = f"<strong style='color:{pctl_color};'>PE百分位 {pe_pctl:.0f}%</strong>"
        tooltip_pctl = (f"<span class='m-tooltiptext'>目前 PE={pe_txt} 落在這檔股票近3年歷史分布的第 {pe_pctl:.0f} 百分位"
                        f"（0%=近3年最便宜，100%=近3年最貴）。百分位法用個股自己的歷史區間比較，"
                        f"比套一個死的PE倍數更合理——電子股跟傳產股的合理本益比天差地遠。</span>")
        pe_html = f"PE <strong style='color:#fff;'>{pe_txt}</strong> <span class='m-tooltip'>({pctl_txt}){tooltip_pctl}</span>"
        tooltip_cheap = "<span class='m-tooltiptext'>近3年PE第25百分位 × EPS，股價來到這裡代表用歷史相對便宜的估值買進。</span>"
        tooltip_fair = "<span class='m-tooltiptext'>近3年PE中位數 × EPS，股價的歷史「常態」估值中樞參考。</span>"
        tooltip_dream = "<span class='m-tooltiptext'>近3年PE第75百分位 × EPS，股價來到這裡代表市場已用相對樂觀的估值定價，追高風險上升。</span>"
    else:
        pe_html = f"PE <strong style='color:#fff;'>{pe_txt}</strong> <span style='color:#888; font-size:11px;'>(樣本不足，退回估算)</span>"
        tooltip_cheap = ""
        tooltip_fair = f"<span class='m-tooltiptext'>歷史PE樣本不足（可能是新股或資料源缺漏），暫用 EPS×{int(PE_FAIR_MULT)} 粗略估算合理價，準確度較低。</span>"
        tooltip_dream = f"<span class='m-tooltiptext'>歷史PE樣本不足，暫用 EPS×{int(PE_DREAM_MULT)} 粗略估算樂觀價，準確度較低。</span>"
        cheap_txt = "—"

    tooltip_defp = (f"<span class='m-tooltiptext'>現金股利 ÷ {int(YIELD_DEF_RATE*100)}%殖利率回推的防守價。"
                    f"現價跌破此價時，長線存股資金通常會進場承接，具一定支撐意義。</span>")

    trail_txt = f"{c.get('trail_stop')}" if float(c.get('trail_stop', 0) or 0) > 0 else "—"
    trail_state = "🟢有效保護" if c.get('trail_active') else "🔴已跌破"
    bb_txt = f"{c.get('bb_upper')}" if float(c.get('bb_upper', 0) or 0) > 0 else "—"
    tooltip_trail = ("<span class='m-tooltiptext'>動態移動停利 = 近20日最高價 − 1.5×ATR。股價創新高時停利線同步上移，"
                     "跌破即代表趨勢轉弱，鎖住波段獲利。「已跌破」表示現價已低於此線，短多結構受損。</span>")
    tooltip_bb = "<span class='m-tooltiptext'>布林通道上軌 = 20MA + 2倍標準差，作為短線滿足點/壓力參考。</span>"

    html_lines = [
        f"""<div style="border:2px solid {c.get('color_border')}; border-radius:8px; padding:15px; background:#16191f; margin-bottom:12px; color:#eeeeee;">""",
        portfolio_header,
        f"""<div style="display:flex; justify-content:space-between; align-items:center;">""",
        f"""<span style="font-weight:bold; font-size:19px; color:#ffffff; display:flex; align-items:center; flex-wrap:wrap; gap:6px;">""",
        f"""{c.get('name')} <span style="color:#00d2ff; font-size:15px;">({c.get('code')})</span>{k_tags}</span>""",
        f"""<span style="font-size:13px; color:#f1c40f; white-space:nowrap;" title="{_expand_blood_line(c.get('blood_line', ''))}">{_expand_blood_line(c.get('blood_line', ''))}</span></div>""",
        f"""<div style="display:flex; justify-content:space-between; align-items:flex-end; margin:10px 0;">""",
        f"""<div style="display:flex; align-items:center;"><span style="font-size:32px; font-weight:bold; color:#ffffff;">{float(c.get('price', 0)):.2f}</span><span style="font-size:15px; color:{gain_c}; background:{gain_b}; padding:3px 8px; border-radius:4px; margin-left:10px; font-weight:bold;">{gain_v:+.2f}%</span></div>""",
        f"""<div style="font-size:14px; display:flex; align-items:center; color:#ccc;">近7日: {c.get('sparkline_html')}</div></div>""",
        # 【V160 B#1+#2】秒讀決策橫幅：價格正下方，動詞+進場價格區間，掃一眼就能決策
        f"""<div style="background:{verdict_bg}; border:1px solid {verdict_color}; border-radius:6px; padding:10px 12px; margin-bottom:10px;"><div style="display:flex; justify-content:space-between; align-items:center;"><span style="font-size:18px; font-weight:bold; color:{verdict_color};">{verdict_word}</span><span style="font-size:11px; color:#888;">評分 {c.get('score')}</span></div><div style="font-size:12px; color:#ddd; margin-top:4px;">{verdict_action}</div></div>""",
        f"""<div style="background:#0e1117; padding:8px; border-radius:4px; margin-bottom:10px;">""",
        # 【V160 新增】今日開高低一行——盤中可看到開盤價與當日高低區間，
        # 收盤後就是當日完整的 OHLC。相對昨收上色（紅漲綠跌，台股慣例）。
        (lambda _o, _h, _l, _pc: (
            f"""<div style="font-size:13px; margin-bottom:4px; color:#bbb;">"""
            f"""開: <strong style="color:{'#ff4d4d' if _o > _pc else ('#00c853' if _o < _pc else '#bbb')};">{_o}</strong> | """
            f"""高: <strong style="color:#ff4d4d;">{_h}</strong> | """
            f"""低: <strong style="color:#00c853;">{_l}</strong> | """
            f"""昨收: {_pc}</div>"""
        ) if (_o and _h and _l and _pc) else "")(
            c.get('open_today'), c.get('high_today'), c.get('low_today'), c.get('prev_close')),
        f"""<div style="font-size:13px; margin-bottom:4px;">總量: {c.get('vol'):,.0f}張 | {c.get('vol_change_str')}</div>""",
        tags_html,
        f"""</div>""",

        f"""<div class="zone-box zone-1"><div class="zone-title">❤️ 第一戰區：基本、財報與估價</div>""",
        f"""<div style="font-size:13px; margin-bottom:4px;">營收 年增 <strong style="color:#ffffff;">({c.get('rev_month')})</strong>: <strong style="color:{yoy_color};">{yoy_txt}</strong> | 月增: <strong style="color:{mom_color};">{mom_txt}</strong></div>""",
        f"""<div style="font-size:13px; margin-bottom:4px;">除權息資訊: <strong style="color:#d200ff;">{c.get('div_display')} (殖利率: {float(c.get('div_yield', 0)):.1f}%)</strong></div>""",
        f"""<div style="font-size:13px; margin-bottom:4px;">{pe_html} | <span class='m-tooltip'>便宜價{tooltip_cheap}</span> <strong style="color:#00e676;">{cheap_txt}</strong> | <span class='m-tooltip'>合理價{tooltip_fair}</span> <strong style="color:#00c853;">{fair_txt}</strong> | <span class='m-tooltip'>樂觀價{tooltip_dream}</span> <strong style="color:#ff4d4d;">{dream_txt}</strong></div>""",
        f"""<div style="font-size:13px; margin-bottom:4px;"><span class='m-tooltip'>殖利率防守價{tooltip_defp}</span>: <strong style="color:#00d2ff;">{defp_txt}</strong></div>""",
        f"""<div style="font-size:13px;"><span class='m-tooltip'>戰情室價值分數{tooltip_vs}</span>: <strong style="color:{vs_color}; font-size:15px;">{vs} 分</strong></div>""",
        _fmt_zone_summary(_z1_badge, _z1_color, _z1_reason),
        """</div>""",

        f"""<div class="zone-box zone-2"><div class="zone-title">⚔️ 第二戰區：技術、防守與移動停利</div>""",
        f"""<div style="font-size:13px; margin-bottom:4px; display:flex; justify-content:space-between;">""",
        f"""<span>5MA: <b style="color:#ffffff;">{float(c.get('ma5', 0)):.1f}</b></span><span>20MA: <b style="color:#ffffff;">{float(c.get('ma20', 0)):.1f}</b></span><span>60MA: <b style="color:#ffffff;">{float(c.get('ma60', 0)):.1f}</b></span></div>""",
        f"""<div style="font-size:13px; margin-bottom:4px; line-height:2.2;">MACD 動能: <strong style="color:{c.get('macd_color')}; margin-right:15px;">{c.get('macd_str')}</strong>{rsi_html} <span style="margin-left:15px;">{bias_html}</span></div>""",
        f"""<div style="font-size:12px; color:#aaa; margin-top:6px; border-top:1px dashed #444; padding-top:4px;">""",
        f"""<span class='m-tooltip' style='color:#ff4d4d;'>短線停利點:<span class='m-tooltiptext'>現價加上1倍ATR，是價格「可能達到」的上緣壓力參考。持有多單者可參考在此附近分批停利，不是建議買入價。真正要進場，仍應以訊號與防守線為準。</span></span> {c.get('atk_zone')} | <span class='m-tooltip' style='color:#00FF00;'>防守停損:<span class='m-tooltiptext'>MA5扣除0.5倍ATR波動緩衝，避開隨機洗盤。跌破代表短多結構破壞。</span></span> {c.get('def_line')} (緩衝 {c.get('buffer_pct')}%, <span class='m-tooltip'>ATR={float(c.get('atr_val', 0)):.2f}<span class='m-tooltiptext'>真實波動幅度，衡量近14日日均震幅。ATR越大代表洗盤越兇，停損需拉寬。</span></span>)</div>""",
        f"""<div style="font-size:12px; color:#aaa; margin-top:4px;"><span class='m-tooltip' style='color:#f1c40f;'>動態移動停利{tooltip_trail}</span>: <strong style="color:#f1c40f;">{trail_txt}</strong> ({trail_state}, 近20高 {c.get('high_20')}) | <span class='m-tooltip' style='color:#d200ff;'>布林上軌{tooltip_bb}</span>: <strong style="color:#d200ff;">{bb_txt}</strong></div>""",
        # 【V160 修復】總指揮官回報「多時間框架共振沒看到」——原本只有在
        # 「週線明確偏多/偏空 且 日線判定也偏多/偏空」時才會顯示一行提示文字，
        # 週線盤整或日線中性等待時完全不顯示任何東西，導致看起來像功能沒在運作。
        # 這裡改成不管有沒有觸發降級/共振，都固定顯示一行週線狀態，讓你能確認
        # 這個功能確實有在算，只是大多數時候週線是盤整、沒有明確方向可共振。
        (f"""<div style="font-size:12px; color:#7ab8ff; margin-top:4px;">"""
         f"""📐 週線趨勢: <strong>{ {'bull':'📈 偏多','bear':'📉 偏空','neutral':'➖ 盤整','unknown':'❓ 資料不足'}.get(_weekly.get('trend','unknown'), '❓ 資料不足') }</strong>"""
         + (f""" (收盤 {_weekly.get('close')} / MA5 {_weekly.get('ma5')} / MA10 {_weekly.get('ma10')})"""
            if _weekly.get('trend') not in (None, 'unknown') else "")
         + """</div>"""),
        _fmt_zone_summary(_z2_badge, _z2_color, _z2_reason),
        """</div>""",

        f"""<div class="zone-box zone-3"><div class="shadow-box"><div class="zone-title">📊 第三戰區：三大法人、真實成本與主力籌碼</div>""",
        f"""<div style="font-size:13px; margin-bottom:4px;"><b>[外資]</b> 單日<span style="color:#f1c40f;">({display_date}{warn_icon})</span>: <strong style="color:#ff4d4d;">{int(c.get('f_buy', 0)):+,}張 ({float(c.get('f_pct', 0)):+.2f}%)</strong><br><span style="color:#888;">　5日</span> <strong>{int(c.get('f_5d', 0)):+,}張 ({float(c.get('f_5d_pct', 0)):+.2f}%)</strong> ｜ <span style="color:#888;">10日</span> <strong>{int(c.get('f_10d', 0)):+,}張 ({float(c.get('f_10d_pct', 0)):+.2f}%)</strong></div>""",
        _fmt_vwap(c, 'f_vwap', '外資連續買賣超成本', '#ff4d4d'),
        f"""<div style="font-size:13px; margin:6px 0 4px 0;"><b>[投信]</b> 單日<span style="color:#f1c40f;">({display_date}{warn_icon})</span>: <strong style="color:#ff4d4d;">{int(c.get('t_buy', 0)):+,}張 ({float(c.get('t_pct', 0)):+.2f}%)</strong><br><span style="color:#888;">　5日</span> <strong>{int(c.get('t_5d', 0)):+,}張 ({float(c.get('t_5d_pct', 0)):+.2f}%)</strong> ｜ <span style="color:#888;">10日</span> <strong>{int(c.get('t_10d', 0)):+,}張 ({float(c.get('t_10d_pct', 0)):+.2f}%)</strong></div>""",
        _fmt_vwap(c, 't_vwap', '投信連續買賣超成本', '#f1c40f'),
        f"""<div style="font-size:12px; border-top:1px dashed #444; padding-top:6px; margin-top:6px; display:flex; justify-content:space-between; color:#aaa;"><span>千張大戶({c.get('big_holder_date') or ERR_NO_DATA}): <strong style="color:#00d2ff;">{bh_display}</strong></span><span>自營商: {int(c.get('d_buy', 0)):+,}張 | 融資增減: {int(c.get('margin_diff', 0)):+,}張{'' if c.get('has_margin') else ' (未同步)'}</span></div>""",
        _fmt_main_force_cost(c),
        _fmt_zone_summary(_z3_badge, _z3_color, _z3_reason),
        """</div></div>""",

        f"""<div style="background:{c.get('signal_bg')}; padding:10px; border-radius:5px; text-align:center; margin-top:8px;"><span class='m-tooltip' style="color:{c.get('color_border')}; font-size:15px; font-weight:bold;">決策判定：{sig_t}{tooltip_sig}</span><div style="font-size:12px; color:#888; margin-top:4px;">(評分 {c.get('score')} | {' / '.join(c.get('reasons', []))})</div></div></div>"""
    ]
    return "".join(html_lines)


# ==============================================================================
# 八、 SQLite 雙軌籌碼寫入管線
# ==============================================================================
def _pick_col(cols, must_all, must_none=()):
    for c in cols:
        s = str(c)
        if all(k in s for k in must_all) and not any(k in s for k in must_none):
            return c
    return None


def process_twse_csv(uploaded_files):
    success_files, total_rows = 0, 0
    for file_bytes in uploaded_files:
        raw_bytes = file_bytes.getvalue()
        try:
            decoded_content = raw_bytes.decode('big5', errors='ignore')
        except Exception:
            continue
        try:
            first_line = decoded_content.split('\n')[0]
            date_match = re.search(r'(\d+)年(\d+)月(\d+)日', first_line)
            file_date = (f"{int(date_match.group(1)) + 1911}-{date_match.group(2).zfill(2)}-{date_match.group(3).zfill(2)}"
                         if date_match else get_last_trading_date())

            df = pd.read_csv(io.StringIO(decoded_content), skiprows=1, thousands=',')
            cols = list(df.columns)

            # 【修復】原本 d_col 用 ('自營商','自行買賣') 比對，會先命中「自營商買進股數(自行買賣)」而非買賣超欄
            code_col = _pick_col(cols, ['代號'])
            f_col = _pick_col(cols, ['外陸資', '買賣超']) or _pick_col(cols, ['外資', '買賣超'], ['自營'])
            t_col = _pick_col(cols, ['投信', '買賣超'])
            d_col = _pick_col(cols, ['自營商', '買賣超'], ['自行買賣', '避險']) or _pick_col(cols, ['自營商', '買賣超'])

            if not code_col or not f_col:
                st.warning(f"⚠️ 欄位辨識失敗，跳過此檔（可辨識欄位：{cols[:6]}…）")
                continue

            batch_args = []
            for _, row in df.iterrows():
                code = str(row[code_col]).strip()
                if len(code) == 4 and code.isdigit():
                    # safe_float 已修復負號，賣超才不會被誤記成買超
                    f_buy = int(safe_float(row[f_col]) / 1000)
                    t_buy = int(safe_float(row[t_col]) / 1000) if t_col else 0
                    d_buy = int(safe_float(row[d_col]) / 1000) if d_col else 0
                    batch_args.append((file_date, code, f_buy, t_buy, d_buy))

            with DB_LOCK:
                SQLITE_CONN.executemany('''
                    INSERT INTO inst_holding (date, symbol, foreign_buy, trust_buy, dealer_buy, margin, big_holder, big_holder_date)
                    VALUES (?, ?, ?, ?, ?, 0.0, 0.0, '')
                    ON CONFLICT(date, symbol) DO UPDATE SET
                        foreign_buy=excluded.foreign_buy,
                        trust_buy=excluded.trust_buy,
                        dealer_buy=excluded.dealer_buy;
                ''', batch_args)
                SQLITE_CONN.commit()
            # 【V160 雙寫】同一批資料寫進 Supabase（盡力而為，失敗不影響本機）
            sb_upsert_inst_holding([
                {"date": a[0], "symbol": a[1], "foreign_buy": a[2], "trust_buy": a[3], "dealer_buy": a[4]}
                for a in batch_args
            ])
            success_files += 1
            total_rows += len(batch_args)
        except Exception as e:
            st.warning(f"⚠️ 解析失敗：{e}")

    if success_files > 0:
        st.success(f"✅ 成功強填 {success_files} 份日報、共 {total_rows:,} 檔籌碼至大腦！")
        time.sleep(1)
        st.rerun()


def fetch_margin_diff(code, token, target_date):
    """【新增】融資增減（張）。V155 的 margin 永遠是 0，導致查5/查10 永遠掃不到東西。"""
    url = 'https://api.finmindtrade.com/api/v4/data'
    start = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=10)).strftime('%Y-%m-%d')
    params = {'dataset': 'TaiwanStockMarginPurchaseShortSale', 'data_id': code,
              'start_date': start, 'end_date': target_date}
    if token:
        params['token'] = token
    try:
        payload = _finmind_get(url, params)
        df = pd.DataFrame(payload.get('data', [])).sort_values('date')
        if df.empty:
            return None
        last = df.iloc[-1]
        today_bal = safe_float(last.get('MarginPurchaseTodayBalance', 0))
        yest_bal = safe_float(last.get('MarginPurchaseYesterdayBalance', 0))
        return today_bal - yest_bal
    except FinMindAPIError:
        return None


def sync_single_stock_finmind(code):
    try:
        target_date = get_last_trading_date()
        token = get_active_fm_token()
        url = 'https://api.finmindtrade.com/api/v4/data'

        inst_success, inst_err_reason = False, None
        base_payload = {'foreign': 0, 'trust': 0, 'dealer': 0}
        inst_hist_rows = []   # 【V160】這檔近40天的法人歷史，供 5日/10日 加總用

        try:
            # 【V160 關鍵修復】原本 start_date 只帶 target_date（單一天），
            # 所以這個同步「永遠只補得到一天」，資料庫裡就只會有一列。
            #
            # 症狀：外資 單日／5日／10日 三個數字完全一樣（因為 head(5)、head(10)
            # 都只取得到那唯一一列）。上市股看不出來，是因為它們另有證交所 T86 CSV
            # 批次匯入補足歷史；但 T86 只涵蓋上市，上櫃股（6xxx）沒有任何批次來源，
            # 只能靠這裡，於是永遠卡在一天。
            #
            # FinMind 帶 start_date 不帶 end_date 會回傳「該日起至今」的全部資料，
            # 所以往前推 40 天跟只抓一天是「同樣一次 API 呼叫」—— 額度成本相同，
            # 拿到的歷史卻足夠算 5日/10日。
            _hist_start = (datetime.strptime(target_date, '%Y-%m-%d')
                           - timedelta(days=40)).strftime('%Y-%m-%d')
            params = {'dataset': 'TaiwanStockInstitutionalInvestorsBuySell',
                      'data_id': code, 'start_date': _hist_start}
            if token:
                params['token'] = token
            payload = _finmind_get(url, params)
            df = pd.DataFrame(payload.get('data', []))
            df['net'] = (pd.to_numeric(df['buy'], errors='coerce').fillna(0)
                         - pd.to_numeric(df['sell'], errors='coerce').fillna(0))
            piv = df.pivot_table(index='date', columns='name', values='net', aggfunc='sum')
            piv = piv.sort_index()

            # 【V160】把整段歷史逐日收集起來，稍後跟單日結果一起批次寫入資料庫，
            # 這樣 5日/10日 才有多列可以加總。最後一列（最新交易日）仍回填
            # base_payload 供畫面即時顯示。
            for _d in piv.index:
                _row = piv.loc[_d]
                inst_hist_rows.append((
                    str(_d), code,
                    int(_row['Foreign_Investor'] / 1000) if 'Foreign_Investor' in piv.columns else 0,
                    int(_row['Investment_Trust'] / 1000) if 'Investment_Trust' in piv.columns else 0,
                    int(_row['Dealer'] / 1000) if 'Dealer' in piv.columns else 0,
                ))

            if 'Foreign_Investor' in piv.columns:
                base_payload['foreign'] = int(piv['Foreign_Investor'].iloc[-1] / 1000)
            if 'Investment_Trust' in piv.columns:
                base_payload['trust'] = int(piv['Investment_Trust'].iloc[-1] / 1000)
            if 'Dealer' in piv.columns:
                base_payload['dealer'] = int(piv['Dealer'].iloc[-1] / 1000)
            inst_success = True
        except FinMindAPIError as e:
            inst_err_reason = e.reason

        margin_val = fetch_margin_diff(code, token, target_date)

        bh_result = fetch_big_holder_with_recursion(code, token, target_date)
        bh_success = False
        if bh_result and bh_result.get('error') is None:
            bh_success = safe_upsert_big_holder(code, bh_result['big_holder_date'], bh_result['big_holder'])

        if inst_success:
            with DB_LOCK:
                SQLITE_CONN.execute('''
                    INSERT INTO inst_holding (date, symbol, foreign_buy, trust_buy, dealer_buy, margin, big_holder, big_holder_date)
                    VALUES (?, ?, ?, ?, ?, ?, 0.0, '')
                    ON CONFLICT(date, symbol) DO UPDATE SET
                        foreign_buy=excluded.foreign_buy,
                        trust_buy=excluded.trust_buy,
                        dealer_buy=excluded.dealer_buy,
                        margin=CASE WHEN excluded.margin <> 0 THEN excluded.margin ELSE inst_holding.margin END;
                ''', (target_date, code, base_payload['foreign'], base_payload['trust'],
                      base_payload['dealer'], float(margin_val or 0.0)))

                # 【V160 修復】連同近40天歷史一起寫入，否則資料庫只有一列，
                # 5日/10日 加總會等於單日（總指揮官在 6488 上發現的症狀）。
                # margin 不覆寫（歷史融資另有來源），故這裡固定帶 0 並保留原值。
                if inst_hist_rows:
                    SQLITE_CONN.executemany('''
                        INSERT INTO inst_holding (date, symbol, foreign_buy, trust_buy, dealer_buy, margin, big_holder, big_holder_date)
                        VALUES (?, ?, ?, ?, ?, 0.0, 0.0, '')
                        ON CONFLICT(date, symbol) DO UPDATE SET
                            foreign_buy=excluded.foreign_buy,
                            trust_buy=excluded.trust_buy,
                            dealer_buy=excluded.dealer_buy;
                    ''', inst_hist_rows)
                SQLITE_CONN.commit()
            # 【V160 雙寫】歷史批次也推上雲端，換裝置/重新部署後才不會又只剩一天
            if inst_hist_rows:
                sb_upsert_inst_holding([
                    {"date": r[0], "symbol": r[1], "foreign_buy": r[2],
                     "trust_buy": r[3], "dealer_buy": r[4]}
                    for r in inst_hist_rows
                ])
            # 【V160 雙寫】單檔同步結果同步進 Supabase
            sb_upsert_inst_holding([{
                "date": target_date, "symbol": code,
                "foreign_buy": base_payload['foreign'], "trust_buy": base_payload['trust'],
                "dealer_buy": base_payload['dealer'], "margin": float(margin_val or 0.0)
            }])

        # 【V160 關鍵修復】總指揮官回報：按了「單檔精準同步」，月營收年增/月增還是抓不到。
        # 根因跟籌碼、大戶完全不同層級的 bug —— 這個按鈕從頭到尾就沒有呼叫過營收抓取函式，
        # 只同步了籌碼+融資+大戶三項，名稱雖然沒提營收，但畫面容易讓人以為「同步」=全部更新。
        # 現在讓這顆按鈕真的也去查一次月營收（智慧快取：查無資料的失敗只快取2分鐘，
        # 所以就算之前抓失敗過，這次按下去也會重新嘗試，不會被舊的失敗結果卡住）。
        rev_success = False
        try:
            rev_cache_key = f"revenue:{code}:{token}"
            _rev_cache = _get_smart_cache_store()
            _rev_cache.pop(rev_cache_key, None)   # 強制這次重查，不用舊快取（含舊失敗）
            rev_data = fetch_finmind_revenue(code, token)
            rev_success = bool(rev_data and rev_data.get('ok'))
        except Exception:
            rev_success = False

        parts = ["籌碼"]
        if margin_val is not None:
            parts.append("融資")
        if bh_success:
            parts.append("大戶")
        if rev_success:
            parts.append("營收")
        msg = f"同步完成 ({'+'.join(parts)})"
        if not bh_success:
            msg += "，⏳大戶無資料"
        if not rev_success:
            msg += "，⏳營收無資料"
        return True, msg

        error_map = {'rate_limited': ERR_RATE_LIMIT, 'timeout': "⏱️ 連線逾時",
                     'connection_error': ERR_CONN, 'empty_data': ERR_NO_DATA}
        return False, error_map.get(inst_err_reason, f"❓ 同步失敗 ({inst_err_reason})")
    except Exception as e:
        return False, f"連線異常 ({e})"


# ==============================================================================
# 九、 NVIDIA NIM 引擎
# ==============================================================================
# 【V160】NVIDIA NIM 的模型 catalog 會變動（舊模型下架、新模型上架）。
# 改成「自動探索」：優先呼叫 NIM 的 /v1/models 端點抓當前可用模型清單，
# 從中挑選偏好的聊天模型；抓失敗才退回下方的靜態候選清單。
# 這樣模型ID更新時系統會自動適應，不用每次手動改程式碼。
# 靜態候選用 2026年中實際存在的模型（deepseek-r1等舊ID已下架）。
NIM_FALLBACK_MODELS = [
    "deepseek-ai/deepseek-v3.2",
    "meta/llama-3.3-70b-instruct",
    "moonshotai/kimi-k2.5-instruct",
    "zai/glm-5.1",
    "qwen/qwen3-coder-480b",
]
# 偏好順序關鍵字：抓到 catalog 後，優先挑名字含這些關鍵字的聊天模型
NIM_PREFERRED_KEYWORDS = ["deepseek", "llama-3.3", "glm", "kimi", "qwen", "nemotron", "mistral"]


@st.cache_data(ttl=3600, show_spinner=False)
def discover_nim_models():
    """
    【V160】呼叫 NIM /v1/models 自動探索當前可用模型清單。
    成功回傳挑選後的模型ID list（依偏好排序），失敗回退靜態 fallback。
    快取1小時，避免每次推演都打一次。
    """
    if not NVIDIA_API_KEY:
        return NIM_FALLBACK_MODELS
    try:
        import requests as _rq
        resp = _rq.get("https://integrate.api.nvidia.com/v1/models",
                       headers={"Authorization": f"Bearer {NVIDIA_API_KEY}"}, timeout=8)
        if resp.status_code != 200:
            return NIM_FALLBACK_MODELS
        data = resp.json().get("data", [])
        all_ids = [m.get("id", "") for m in data if m.get("id")]
        if not all_ids:
            return NIM_FALLBACK_MODELS
        # 依偏好關鍵字挑選聊天型模型（排除 embed/rerank/vision/ocr 等非聊天模型）
            # 【V160 修復】把純程式碼模型與小參數模型也排除：總指揮官的清單裡挑到了
        # deepseek-coder-6.7b-instruct（已下架的小型 coding 模型），對戰略分析沒有用，
        # 還會佔掉前5名的挑選名額，把真正能用的大模型擠掉。
        exclude = ("embed", "rerank", "ocr", "vision", "riva", "bio", "diffusion", "guard",
                   "vila", "tts", "asr", "coder", "-1.5b", "-3b", "-6.7b", "-7b", "-8b")
        picked = []
        for kw in NIM_PREFERRED_KEYWORDS:
            for mid in all_ids:
                low = mid.lower()
                if kw in low and not any(x in low for x in exclude) and mid not in picked:
                    picked.append(mid)
        # 至少保底幾個；若挑不到就用 fallback
        return picked[:5] if picked else NIM_FALLBACK_MODELS
    except Exception:
        return NIM_FALLBACK_MODELS


def get_nim_models():
    """
    取得當前要用的模型清單（自動探索優先）。
    【V160 新功能】如果使用者在側邊欄手動選過偏好模型，把它排到最前面優先嘗試，
    其餘自動偵測到的模型仍保留在後面當備援——選的那個萬一剛好失效，不會整個掛掉，
    會自動退回下一個可用模型。
    """
    models = discover_nim_models()
    try:
        preferred_short = st.session_state.get('preferred_nim_model')
    except Exception:
        preferred_short = None
    if preferred_short:
        matched = [m for m in models if m.split('/')[-1] == preferred_short]
        if matched:
            rest = [m for m in models if m not in matched]
            return matched + rest
    return models


NIM_MODELS = NIM_FALLBACK_MODELS   # 相容舊引用；實際呼叫改用 get_nim_models()


def execute_single_stock_ai(c):
    if not NVIDIA_API_KEY:
        return "未配置 NVIDIA API 金鑰"
    client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=NVIDIA_API_KEY)
    bh = c.get('big_holder', 0)
    bh_str = f"{bh}%" if isinstance(bh, (int, float)) else str(bh)
    fv = c.get('f_vwap')
    fv_str = f"外資連續{fv['side']}{fv['days']}日，成本{fv['vwap']}元" if fv else "外資連續買賣超成本：無資料"
    yoy = c.get('rev_yoy')
    yoy_str = f"{yoy:.1f}%" if yoy is not None else "官方未公佈"

    prompt = (f"請以首席戰略幕僚身分，對 {c['name']} ({c['code']}) 進行冷血多空推演。"
              f"現價:{c['price']:.2f} | 漲跌:{c['gain']:.2f}% | 營收YoY:{yoy_str} | "
              f"PE:{c.get('pe')} | 價值分數:{c.get('value_score')} | 地雷:{'是' if c.get('landmine') else '否'} | "
              f"外資5日:{c['f_5d']:.0f}張 | {fv_str} | 大戶比例:{bh_str} | MACD:{c['macd_str']} | "
              f"防守線:{c.get('def_line')} | 移動停利:{c.get('trail_stop')}。"
              f"請分四段繁體輸出：【第一戰區財報估價小結】、【第二戰區技術面小結】、"
              f"【第三戰區籌碼成本小結】、【總指揮明日戰略總結】")
    errors = []
    for model_id in get_nim_models():
        try:
            completion = client.chat.completions.create(
                model=model_id,
                messages=[{"role": "system", "content": "你是一位冷血的台灣股市操盤幕僚。所有輸出嚴格使用繁體中文，並使用台灣金融專有名詞。直擊核心。"},
                          {"role": "user", "content": prompt}],
                temperature=0.2, max_tokens=1200, timeout=90
            )
            return f"【{model_id.split('/')[-1]} 提供分析】\n\n{completion.choices[0].message.content}"
        except Exception as e:
            # 【V160】分類錯誤，讓使用者知道是模型失效/限流/逾時，而不是籠統的「全面癱瘓」
            emsg = str(e).lower()
            short = model_id.split('/')[-1]
            if '404' in emsg or 'not found' in emsg or 'does not exist' in emsg:
                errors.append(f"{short}: 模型不存在(已下架)")
            elif '429' in emsg or 'rate' in emsg or 'quota' in emsg:
                errors.append(f"{short}: 限流/額度不足")
            elif 'timeout' in emsg or 'timed out' in emsg:
                errors.append(f"{short}: 連線逾時(90s)")
            else:
                errors.append(f"{short}: {str(e)[:40]}")
            continue
    return ("⚠️ NVIDIA 三個模型都無法使用，逐一狀態：\n- " + "\n- ".join(errors)
            + "\n\n若全是「模型不存在」，代表 NVIDIA NIM 上的模型ID已更新，需更換 NIM_MODELS 清單。")


# ==============================================================================
# 九之二、命中率回測引擎 (V158 新增，V159 擴充查1~查12完整濾網回測)
# ------------------------------------------------------------------------------
# 改編自總指揮官提供的獨立回測腳本，核心「無未來函數」骨架保留：
# 用第 i 天收盤產生訊號，量測第 i+3 / i+10 天的未來報酬，rolling 均線/ATR
# 都只用到當天為止的資料，不偷看未來。
#
# 【V158】核心技術訊號回測：只測「價量 + 均線 + 大盤位階」，不含法人與基本面。
# 【V159 新增】查1~查12 完整濾網回測（含法人籌碼與營收）：
#   FinMind 額度確認足夠後，改為對每檔股票額外拉「三大法人買賣超 + 融資融券 +
#   月營收」歷史（各 1 支 API call 涵蓋整個回測區間，不是一天一 call），並用
#   evaluate_single_condition() 這個跟正式版「即時掃描」共用的同一份判斷邏輯，
#   確保回測驗證的規則跟你實際在用的規則完全一致，不會兩邊寫兩份、之後改一邊
#   忘了改另一邊而悄悄失準。
#   月營收有揭露延遲（例如6月營收要到7月10日左右才公告），回測時只採用「當下
#   已經公告」的最新一期營收，不用當月營收去回推當月的訊號，避免未來函數。
#   殖利率（查11）本輪仍用現在的股利資料回推套用到歷史區間，屬於已知簡化，
#   在 UI 上會標註。情報雷達／黃金交叉條件無法回測（依賴使用者手動輸入的筆記，
#   沒有歷史時間戳），本輪排除在完整回測範圍外。
# ==============================================================================
def evaluate_single_condition(cmd, card, c_sources=None, selected_k_patterns=None):
    """
    單一濾網條件判斷，從即時掃描迴圈抽出成共用函式，正式掃描（AND 多條件）
    與回測（逐條件分開驗證命中率）都呼叫這裡，兩邊規則保證一致。
    """
    c_sources = c_sources or set()
    selected_k_patterns = selected_k_patterns or []
    c_price = float(card.get('price', 0) or 0)
    c_ma60 = float(card.get('ma60', 0) or 0)
    c_vol_ratio = float(card.get('vol_ratio', 0) or 0)
    c_tbuy = float(card.get('t_buy', 0) or 0)
    c_fbuy = float(card.get('f_buy', 0) or 0)
    c_margin = float(card.get('margin_diff', 0) or 0)
    c_has_margin = bool(card.get('has_margin'))
    c_rev_yoy = card.get('rev_yoy')
    c_kdj = str(card.get('kdj_str', ''))
    margin_shrink = (c_margin < 0) if c_has_margin else True

    if "情報雷達：" in cmd:
        return cmd.split("情報雷達：")[-1].strip() in c_sources
    if "情報黃金交叉" in cmd:
        return len(c_sources) >= 2
    if "查1." in cmd:
        return bool(card.get('is_first_red') and c_vol_ratio >= 2.0 and "金叉" in c_kdj)
    if "查2." in cmd:
        return bool(c_price > c_ma60 and c_vol_ratio >= 1.2)
    if "查3." in cmd:
        return bool(int(card.get('value_score', 0)) >= 60 and not card.get('landmine'))
    if "查4." in cmd:
        return bool(c_tbuy > 0)
    if "查5." in cmd:
        return bool(c_fbuy > 0 and margin_shrink)
    if "查6." in cmd:
        return bool(c_rev_yoy is not None and c_rev_yoy > 20)
    if "查8." in cmd:
        return bool(card.get('is_yesterday_strong'))
    if "查9." in cmd:
        return bool(c_vol_ratio >= 2.0)
    if "查10." in cmd:
        return bool(0 < c_vol_ratio <= 0.6 and margin_shrink)
    if "查11." in cmd:
        return bool(float(card.get('div_yield', 0)) >= 4.5)
    if "查12." in cmd:
        hit = [x.get('text') for x in card.get('detected_patterns', [])]
        return bool(selected_k_patterns and any(p in t for t in hit for p in selected_k_patterns))
    return False


def evaluate_scan_conditions(selected_cmds, card, c_sources=None, selected_k_patterns=None):
    """即時掃描用：AND 所有已選條件。"""
    for cmd in selected_cmds:
        if not evaluate_single_condition(cmd, card, c_sources, selected_k_patterns):
            return False
    return True


@st.cache_data(ttl=21600, show_spinner=False)
def fetch_twii_regime_history(years):
    """抓 TWII 歷史，算出每一天的 20MA 位階，回測時用日期查表，不用每檔股票各抓一次大盤。"""
    try:
        tk = _yf_ticker("^TWII")
        hist = tk.history(period=f"{years}y").dropna(subset=['Close'])
        if hist.empty or len(hist) < 21:
            return None
        hist = hist.copy()
        hist['MA20'] = hist['Close'].rolling(20).mean()
        hist['is_bull'] = hist['Close'] >= hist['MA20']
        hist.index = hist.index.strftime('%Y-%m-%d')
        return hist['is_bull']
    except Exception:
        return None


def _backtest_one_stock(stock_code, years, atr_multiplier, enable_doomsday, twii_regime):
    """單一股票的訊號回測迴圈，回傳該股所有訊號日的明細 list[dict]。"""
    rows = []
    try:
        tk_obj = yf.Ticker(f"{stock_code}.TW", session=_SESSION)
        df = tk_obj.history(period=f"{years}y", auto_adjust=False)
        if df.empty:
            tk_obj = yf.Ticker(f"{stock_code}.TWO", session=_SESSION)
            df = tk_obj.history(period=f"{years}y", auto_adjust=False)
        df = df.dropna(subset=['Close'])
        if df.empty or len(df) < 40:
            return rows
    except Exception:
        return rows

    df = df.copy()
    df['MA5'] = df['Close'].rolling(5).mean()
    df['MA20'] = df['Close'].rolling(20).mean()
    df['Vol_5MA'] = df['Volume'].rolling(5).mean()
    df['ATR'] = calculate_atr(df, 14)
    date_strs = df.index.strftime('%Y-%m-%d')

    for i in range(20, len(df) - 10):
        curr_price = float(df['Close'].iloc[i])
        open_price = float(df['Open'].iloc[i])
        prev_price = float(df['Close'].iloc[i - 1])
        ma5 = float(df['MA5'].iloc[i])
        ma20 = float(df['MA20'].iloc[i])
        vol_today = float(df['Volume'].iloc[i])
        vol_5ma = float(df['Vol_5MA'].iloc[i])
        atr = float(df['ATR'].iloc[i]) if pd.notna(df['ATR'].iloc[i]) else 0.0
        if pd.isna(ma5) or pd.isna(ma20) or pd.isna(vol_5ma) or vol_5ma <= 0:
            continue

        vol_ratio = vol_today / vol_5ma
        # 【修復】沿用正式版定義（開盤高於昨收、收盤低於今開），而非「單純收黑K」
        is_open_high_close_low = (open_price > prev_price) and (curr_price < open_price)

        def_line = ma5 - (atr * atr_multiplier)
        buffer_pct = ((curr_price - def_line) / curr_price) * 100 if curr_price > 0 else 0.0
        gain = ((curr_price - prev_price) / prev_price) * 100 if prev_price > 0 else 0.0

        market_bull = True
        if twii_regime is not None:
            d = date_strs[i]
            if d in twii_regime.index:
                market_bull = bool(twii_regime.loc[d])

        signal_text, _, _, _ = determine_signal(
            curr_price, ma5, ma20, foreign_buy=0, vol_ratio=vol_ratio,
            is_open_high_close_low=is_open_high_close_low, buffer_pct=buffer_pct,
            gain=gain, enable_doomsday=enable_doomsday, market_bull=market_bull, landmine=False
        )

        future_3d_ret = (float(df['Close'].iloc[i + 3]) - curr_price) / curr_price * 100 if curr_price > 0 else 0.0
        future_10d_ret = (float(df['Close'].iloc[i + 10]) - curr_price) / curr_price * 100 if curr_price > 0 else 0.0
        future_window = df.iloc[i + 1: i + 11]
        is_breached = bool((future_window['Low'] < def_line).any())

        rows.append({
            'stock': stock_code, 'date': date_strs[i], 'signal': signal_text,
            'future_3d_ret': round(future_3d_ret, 2), 'future_10d_ret': round(future_10d_ret, 2),
            'is_breached': is_breached
        })
    return rows


@st.cache_data(ttl=21600, show_spinner=False)
def fetch_institutional_history(stock_code, years, token):
    """
    【V159 新增】歷史三大法人買賣超 + 融資融券，各一支 API call 涵蓋整個回測區間
    （不是一天一 call）。三大法人與融資融券資料是證交所收盤後當天公告，用在「當天
    收盤產生訊號」沒有未來函數問題（收盤時這筆資料已經是當天可得的最新籌碼）。
    回傳以日期為 index 的 DataFrame，欄位：f_buy, t_buy, d_buy, margin_diff（單位：張）。
    """
    url = 'https://api.finmindtrade.com/api/v4/data'
    start_date = (datetime.now() - timedelta(days=int(365 * years))).strftime('%Y-%m-%d')
    out = pd.DataFrame()
    try:
        params = {'dataset': 'TaiwanStockInstitutionalInvestorsBuySell',
                  'data_id': stock_code, 'start_date': start_date}
        if token:
            params['token'] = token
        payload = _finmind_get(url, params, max_retries=2, timeout=10)
        df = pd.DataFrame(payload.get('data', []))
        if not df.empty:
            df['net'] = (pd.to_numeric(df['buy'], errors='coerce').fillna(0)
                         - pd.to_numeric(df['sell'], errors='coerce').fillna(0)) / 1000.0
            piv = df.pivot_table(index='date', columns='name', values='net', aggfunc='sum')
            out['f_buy'] = piv.get('Foreign_Investor', pd.Series(dtype=float))
            out['t_buy'] = piv.get('Investment_Trust', pd.Series(dtype=float))
            out['d_buy'] = piv.get('Dealer', pd.Series(dtype=float))
    except FinMindAPIError:
        pass

    try:
        params = {'dataset': 'TaiwanStockMarginPurchaseShortSale',
                  'data_id': stock_code, 'start_date': start_date}
        if token:
            params['token'] = token
        payload = _finmind_get(url, params, max_retries=2, timeout=10)
        mdf = pd.DataFrame(payload.get('data', []))
        if not mdf.empty:
            mdf['margin_diff'] = (pd.to_numeric(mdf.get('MarginPurchaseTodayBalance'), errors='coerce').fillna(0)
                                  - pd.to_numeric(mdf.get('MarginPurchaseYesterdayBalance'), errors='coerce').fillna(0))
            mdf = mdf.set_index('date')
            out = out.join(mdf[['margin_diff']], how='outer') if not out.empty else mdf[['margin_diff']]
    except FinMindAPIError:
        pass

    if out.empty:
        return None
    return out.fillna(0.0)


@st.cache_data(ttl=21600, show_spinner=False)
def fetch_revenue_history_lagged(stock_code, years, token, disclosure_buffer_days=10):
    """
    【V159 新增】歷史月營收年增率，處理揭露延遲避免未來函數。
    台灣上市櫃公司月營收依規定要在次月10日前公告，6月營收不會在6月的任何一天
    就先被市場知道。這裡把每一期營收的「可用日」設定為
    revenue_month最後一天 + disclosure_buffer_days（預設10天）的保守估計，
    在那天之前，回測時該股票的 rev_yoy 一律視為 None（未公佈），不會偷看未來。
    回傳：DataFrame[available_date, yoy]，用 merge_asof 對齊到訊號日期使用。
    """
    url = 'https://api.finmindtrade.com/api/v4/data'
    start_date = (datetime.now() - timedelta(days=int(365 * years) + 60)).strftime('%Y-%m-%d')
    try:
        params = {'dataset': 'TaiwanStockMonthRevenue', 'data_id': stock_code, 'start_date': start_date}
        if token:
            params['token'] = token
        payload = _finmind_get(url, params, max_retries=2, timeout=10)
        df = pd.DataFrame(payload.get('data', []))
        if df.empty:
            return None
        df['yoy'] = pd.to_numeric(df.get('revenue_YearOnYearRatio'), errors='coerce')
        df = df.dropna(subset=['yoy'])
        if df.empty:
            return None
        # revenue_year / revenue_month 標示該筆營收「所屬月份」，可用日 = 該月最後一天 + buffer
        df['period_end'] = pd.to_datetime(
            df['revenue_year'].astype(int).astype(str) + '-' + df['revenue_month'].astype(int).astype(str) + '-01'
        ) + pd.offsets.MonthEnd(0)
        df['available_date'] = df['period_end'] + pd.Timedelta(days=disclosure_buffer_days)
        df = df.sort_values('available_date')[['available_date', 'yoy']].reset_index(drop=True)
        return df
    except FinMindAPIError:
        return None
    except Exception:
        return None


def _lookup_lagged_revenue(rev_hist_df, signal_date_ts):
    """用 merge_asof 概念手動查表：找出在 signal_date 當下，「已經公告」的最新一筆營收年增率。"""
    if rev_hist_df is None or rev_hist_df.empty:
        return None
    eligible = rev_hist_df[rev_hist_df['available_date'] <= signal_date_ts]
    if eligible.empty:
        return None
    return float(eligible.iloc[-1]['yoy'])


def run_signal_backtest(stock_list, years, atr_multiplier, enable_doomsday, use_market_regime,
                         progress_callback=None, max_workers=8):
    """
    批次回測引擎（多執行緒抓歷史資料，沿用掃描功能同一套並行模式）。
    回傳 (all_rows, summary_df)。
    """
    twii_regime = fetch_twii_regime_history(years) if use_market_regime else None
    all_rows = []
    total = max(1, len(stock_list))

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_backtest_one_stock, code, years, atr_multiplier,
                                   enable_doomsday, twii_regime): code for code in stock_list}
        for i, future in enumerate(concurrent.futures.as_completed(futures)):
            if progress_callback:
                progress_callback(i + 1, total, futures[future])
            try:
                all_rows.extend(future.result())
            except Exception:
                continue

    if not all_rows:
        return all_rows, pd.DataFrame()

    res_df = pd.DataFrame(all_rows)
    summary_rows = []
    for sig in ["🔥 偏多攻擊", "🟡 觀察偏多", "⚖️ 中立震盪", "⚠️ 轉弱謹慎", "🔵 偏空防守"]:
        subset = res_df[res_df['signal'] == sig]
        count = len(subset)
        if count == 0:
            summary_rows.append({'訊號': sig, '樣本數': 0, '3日勝率%': None, '3日平均報酬%': None,
                                 '10日平均報酬%': None, '10日防守擊穿率%': None})
            continue
        win_rate_3d = (subset['future_3d_ret'] > 0).mean() * 100
        avg_ret_3d = subset['future_3d_ret'].mean()
        avg_ret_10d = subset['future_10d_ret'].mean()
        breach_rate = subset['is_breached'].mean() * 100
        summary_rows.append({
            '訊號': sig, '樣本數': count, '3日勝率%': round(win_rate_3d, 1),
            '3日平均報酬%': round(avg_ret_3d, 2), '10日平均報酬%': round(avg_ret_10d, 2),
            '10日防守擊穿率%': round(breach_rate, 1)
        })
    return all_rows, pd.DataFrame(summary_rows)


def save_backtest_run(stock_list, years, atr_multiplier, enable_doomsday, use_market_regime, all_rows):
    """把這次回測結果寫進 SQLite，永久保存，不用每次重開網頁就砍掉重測。"""
    with DB_LOCK:
        cur = SQLITE_CONN.execute('''
            INSERT INTO backtest_runs (run_time, stock_list, years, atr_multiplier,
                enable_doomsday, use_market_regime, sample_count, mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'technical')
        ''', (datetime.now().strftime('%Y-%m-%d %H:%M'), ','.join(stock_list), years,
              atr_multiplier, int(enable_doomsday), int(use_market_regime), len(all_rows)))
        run_id = cur.lastrowid
        SQLITE_CONN.executemany('''
            INSERT INTO backtest_signals (run_id, stock, date, signal, future_3d_ret, future_10d_ret, is_breached)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', [(run_id, r['stock'], r['date'], r['signal'], r['future_3d_ret'],
               r['future_10d_ret'], int(r['is_breached'])) for r in all_rows])
        SQLITE_CONN.commit()
    return run_id


def list_backtest_runs(limit=20, mode=None):
    with DB_LOCK:
        try:
            if mode:
                return pd.read_sql(
                    'SELECT run_id, run_time, stock_list, years, atr_multiplier, enable_doomsday, '
                    'use_market_regime, sample_count, mode FROM backtest_runs WHERE mode=? '
                    'ORDER BY run_id DESC LIMIT ?', SQLITE_CONN, params=(mode, limit))
            return pd.read_sql(
                'SELECT run_id, run_time, stock_list, years, atr_multiplier, enable_doomsday, '
                'use_market_regime, sample_count, mode FROM backtest_runs ORDER BY run_id DESC LIMIT ?',
                SQLITE_CONN, params=(limit,))
        except Exception:
            return pd.DataFrame()


def get_all_traded_symbols():
    """
    【V160 新增】列出系統模擬倉裡「有交易紀錄」的全部標的（去重），供單檔績效查詢用選單挑選。

    總指揮官回報：要手動輸入代號才能查，但根本不知道有哪幾檔做過交易可以查。
    這裡回傳 (symbol, name, 筆數) 的清單，依最近進場日排序在前，方便找最近交易的標的。
    """
    def _do():
        return (SUPABASE_CONN.table("system_portfolio")
                .select("symbol,name,entry_date").execute())
    ok, res = _sb_safe(_do)
    rows = res.data if (ok and res is not None and getattr(res, "data", None)) else []
    latest_date, count = {}, {}
    for r in rows:
        sym = r.get('symbol')
        if not sym:
            continue
        count[sym] = count.get(sym, 0) + 1
        d = r.get('entry_date') or ''
        if d > latest_date.get(sym, ''):
            latest_date[sym] = d
    symbols = sorted(count.keys(), key=lambda s: latest_date.get(s, ''), reverse=True)
    return [(s, TW_STOCK_NAMES.get(s, s), count[s]) for s in symbols]


def get_symbol_performance(symbol):
    """
    【V160 新增】單檔績效查詢：這檔股票在系統模擬倉裡的所有進出紀錄與累計成績。

    總指揮官回報：績效表只有多空總計，看不到「某一檔到底幫我賺多少賠多少」。
    回傳 (已結算列表, 持倉中列表, 統計dict)。抓不到就回空，不編造數字。
    """
    def _do():
        return (SUPABASE_CONN.table("system_portfolio").select("*")
                .eq("symbol", str(symbol).strip()).execute())
    ok, res = _sb_safe(_do)
    rows = res.data if (ok and res is not None and getattr(res, "data", None)) else []
    closed = [r for r in rows if r.get('status') == 'closed']
    holding = [r for r in rows if r.get('status') in ('holding', 'pending')]
    wins = [r for r in closed if float(r.get('realized_pnl') or 0) > 0]
    total_pnl = sum(float(r.get('realized_pnl') or 0) for r in closed)
    stats = {
        'closed_count': len(closed),
        'holding_count': len(holding),
        'win_rate': round(100.0 * len(wins) / len(closed), 1) if closed else None,
        'total_pnl': round(total_pnl, 0),
        'avg_roi': round(sum(float(r.get('realized_roi') or 0) for r in closed) / len(closed), 2)
                   if closed else None,
    }
    return closed, holding, stats


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


def load_backtest_summary(run_id):
    with DB_LOCK:
        try:
            df = pd.read_sql('SELECT * FROM backtest_signals WHERE run_id=?', SQLITE_CONN, params=(run_id,))
        except Exception:
            return pd.DataFrame()
    if df.empty:
        return df
    summary_rows = []
    for sig in ["🔥 偏多攻擊", "🟡 觀察偏多", "⚖️ 中立震盪", "⚠️ 轉弱謹慎", "🔵 偏空防守"]:
        subset = df[df['signal'] == sig]
        count = len(subset)
        if count == 0:
            continue
        summary_rows.append({
            '訊號': sig, '樣本數': count,
            '3日勝率%': round((subset['future_3d_ret'] > 0).mean() * 100, 1),
            '3日平均報酬%': round(subset['future_3d_ret'].mean(), 2),
            '10日平均報酬%': round(subset['future_10d_ret'].mean(), 2),
            '10日防守擊穿率%': round(subset['is_breached'].mean() * 100, 1)
        })
    return pd.DataFrame(summary_rows)


# ==============================================================================
# 九之三、查1~查12 完整濾網回測（V159 新增）
# ------------------------------------------------------------------------------
# 範圍聲明：
#   ✅ 完整點對點回測（含正確揭露時序）：查1, 查2, 查4, 查5, 查6, 查8, 查9, 查10, 查12
#   ⚠️ 簡化版：查11（殖利率）用現在的股利資料回推套用到歷史區間，非逐年精確股利
#   ❌ 不支援：查3（需要逐日精確EPS+估值百分位歷史，牽涉到財報揭露時序，工程量
#      不小，本輪不做，不列入可選清單）；情報雷達／黃金交叉（依賴使用者手動筆記，
#      沒有歷史時間戳可回測）
# ==============================================================================
def _filter_backtest_one_stock(stock_code, years, selected_cmds, selected_k_patterns,
                                token, twii_regime, market_bull_filter):
    rows = []
    try:
        tk_obj = yf.Ticker(f"{stock_code}.TW", session=_SESSION)
        df = tk_obj.history(period=f"{years}y", auto_adjust=False)
        if df.empty:
            tk_obj = yf.Ticker(f"{stock_code}.TWO", session=_SESSION)
            df = tk_obj.history(period=f"{years}y", auto_adjust=False)
        df = df.dropna(subset=['Close'])
        if df.empty or len(df) < 40:
            return rows
    except Exception:
        return rows

    df = df.copy()
    df['MA5'] = df['Close'].rolling(5).mean()
    df['MA20'] = df['Close'].rolling(20).mean()
    df['MA60'] = df['Close'].rolling(60).mean()
    df['Vol_5MA'] = df['Volume'].rolling(5).mean()
    df['ATR'] = calculate_atr(df, 14)
    low_min, high_max = df['Low'].rolling(9).min(), df['High'].rolling(9).max()
    rsv = (df['Close'] - low_min) / (high_max - low_min + 1e-9) * 100
    calc_k = rsv.bfill().ffill().ewm(com=2, adjust=False).mean()
    df['K'] = calc_k
    df['D'] = calc_k.ewm(com=2, adjust=False).mean()
    date_strs = df.index.strftime('%Y-%m-%d')

    need_inst = any(("查4." in c or "查5." in c or "查10." in c) for c in selected_cmds)
    need_kline = any("查12." in c for c in selected_cmds)

    inst_hist = fetch_institutional_history(stock_code, years, token) if need_inst else None
    rev_hist = fetch_revenue_history_lagged(stock_code, years, token) if any("查6." in c for c in selected_cmds) else None
    div_info = DIVIDEND_DB.get(stock_code)
    cash_div = div_info.get('cash', 0.0) if div_info else 0.0

    for i in range(20, len(df) - 10):
        d = date_strs[i]
        curr_price = float(df['Close'].iloc[i])
        open_price = float(df['Open'].iloc[i])
        prev_price = float(df['Close'].iloc[i - 1])
        prev2_price = float(df['Close'].iloc[i - 2])
        ma5 = float(df['MA5'].iloc[i])
        ma20 = float(df['MA20'].iloc[i])
        ma60_v = df['MA60'].iloc[i]
        ma60 = float(ma60_v) if pd.notna(ma60_v) else ma20
        vol_today = float(df['Volume'].iloc[i])
        vol_5ma = float(df['Vol_5MA'].iloc[i])
        atr = float(df['ATR'].iloc[i]) if pd.notna(df['ATR'].iloc[i]) else 0.0
        if pd.isna(ma5) or pd.isna(ma20) or pd.isna(vol_5ma) or vol_5ma <= 0:
            continue
        vol_ratio = vol_today / vol_5ma

        prev_gain = ((prev_price - prev2_price) / prev2_price * 100) if prev2_price > 0 else 0.0
        is_yesterday_strong = prev_gain > 5.0

        o1, c1 = float(df['Open'].iloc[i - 1]), prev_price
        body_ref = atr if atr > 0 else curr_price * 0.02
        is_first_red = (curr_price > open_price) and (c1 < o1) and (abs(curr_price - open_price) > body_ref * 0.5)

        k_v, d_v = float(df['K'].iloc[i]), float(df['D'].iloc[i])
        kdj_str = f"金叉 (K:{k_v:.1f})" if k_v > d_v else f"死叉 (K:{k_v:.1f})"

        detected_patterns = detect_k_line_patterns_v152(df.iloc[:i + 1], atr) if need_kline else []

        f_buy = t_buy = margin_diff = 0.0
        has_margin = False
        if inst_hist is not None and d in inst_hist.index:
            row = inst_hist.loc[d]
            f_buy = float(row.get('f_buy', 0.0) or 0.0)
            t_buy = float(row.get('t_buy', 0.0) or 0.0)
            margin_diff = float(row.get('margin_diff', 0.0) or 0.0)
            has_margin = margin_diff != 0.0

        rev_yoy = _lookup_lagged_revenue(rev_hist, df.index[i]) if rev_hist is not None else None
        div_yield = (cash_div / curr_price * 100) if curr_price > 0 else 0.0

        market_bull = True
        if market_bull_filter and twii_regime is not None and d in twii_regime.index:
            market_bull = bool(twii_regime.loc[d])
        if market_bull_filter and not market_bull:
            continue   # 大盤破20MA時，比照正式版精神：這天不納入多方濾網樣本

        card = {
            'price': curr_price, 'ma60': ma60, 'vol_ratio': vol_ratio,
            't_buy': t_buy, 'f_buy': f_buy, 'margin_diff': margin_diff, 'has_margin': has_margin,
            'rev_yoy': rev_yoy, 'kdj_str': kdj_str, 'value_score': 0, 'landmine': False,
            'is_first_red': is_first_red, 'is_yesterday_strong': is_yesterday_strong,
            'div_yield': div_yield, 'detected_patterns': detected_patterns,
        }

        future_3d_ret = (float(df['Close'].iloc[i + 3]) - curr_price) / curr_price * 100 if curr_price > 0 else 0.0
        future_10d_ret = (float(df['Close'].iloc[i + 10]) - curr_price) / curr_price * 100 if curr_price > 0 else 0.0

        for cmd in selected_cmds:
            if evaluate_single_condition(cmd, card, None, selected_k_patterns):
                rows.append({'stock': stock_code, 'date': d, 'filter': cmd,
                            'future_3d_ret': round(future_3d_ret, 2), 'future_10d_ret': round(future_10d_ret, 2)})
    return rows


def run_filter_backtest(stock_list, years, selected_cmds, selected_k_patterns, use_market_regime,
                        progress_callback=None, max_workers=6):
    """
    多執行緒跑完整濾網回測。max_workers 刻意比技術面回測(8)低一點——這裡每個任務
    多打了法人籌碼/營收兩種歷史API，即使額度夠，也不必要對FinMind太密集併發。
    """
    token = get_active_fm_token()
    twii_regime = fetch_twii_regime_history(years) if use_market_regime else None
    all_rows = []
    total = max(1, len(stock_list))

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_filter_backtest_one_stock, code, years, selected_cmds,
                                   selected_k_patterns, token, twii_regime, use_market_regime): code
                  for code in stock_list}
        for i, future in enumerate(concurrent.futures.as_completed(futures)):
            if progress_callback:
                progress_callback(i + 1, total, futures[future])
            try:
                all_rows.extend(future.result())
            except Exception:
                continue

    return all_rows, summarize_filter_backtest(all_rows)


def summarize_filter_backtest(all_rows):
    if not all_rows:
        return pd.DataFrame()
    df = pd.DataFrame(all_rows)
    summary_rows = []
    for f in sorted(df['filter'].unique()):
        subset = df[df['filter'] == f]
        count = len(subset)
        summary_rows.append({
            '濾網條件': f, '樣本數': count,
            '3日勝率%': round((subset['future_3d_ret'] > 0).mean() * 100, 1),
            '3日平均報酬%': round(subset['future_3d_ret'].mean(), 2),
            '10日平均報酬%': round(subset['future_10d_ret'].mean(), 2),
        })
    return pd.DataFrame(summary_rows)


def save_filter_backtest_run(stock_list, years, all_rows):
    with DB_LOCK:
        cur = SQLITE_CONN.execute('''
            INSERT INTO backtest_runs (run_time, stock_list, years, sample_count, mode)
            VALUES (?, ?, ?, ?, 'filter')
        ''', (datetime.now().strftime('%Y-%m-%d %H:%M'), ','.join(stock_list), years, len(all_rows)))
        run_id = cur.lastrowid
        SQLITE_CONN.executemany('''
            INSERT INTO backtest_signals (run_id, stock, date, future_3d_ret, future_10d_ret, filter_name)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', [(run_id, r['stock'], r['date'], r['future_3d_ret'], r['future_10d_ret'], r['filter'])
              for r in all_rows])
        SQLITE_CONN.commit()
    return run_id


def load_filter_backtest_summary(run_id):
    with DB_LOCK:
        try:
            df = pd.read_sql('SELECT * FROM backtest_signals WHERE run_id=?', SQLITE_CONN, params=(run_id,))
        except Exception:
            return pd.DataFrame()
    if df.empty or 'filter_name' not in df.columns:
        return pd.DataFrame()
    df = df.dropna(subset=['filter_name']).rename(columns={'filter_name': 'filter'})
    if df.empty:
        return df
    return summarize_filter_backtest(df.to_dict('records'))


# ==============================================================================
# 九之四、盤中異常偵測 (V159 新增，陽春版：僅網頁內顯示，不推播)
# ------------------------------------------------------------------------------
# 部署環境確認為 Streamlit Cloud 免費版，沒有背景執行能力，所以這裡做的是「開著
# 網頁分頁時，每隔設定分鐘數自動重新整理」的陽春版，不是真正的背景常駐監控——
# 分頁關掉就不會繼續偵測，也沒有 Line/Telegram/Email 推播（使用者選擇暫不做）。
# 偵測邏輯：拿這次重新整理算出來的爆量比/漲跌幅，跟「上一次重新整理」的快照比較，
# 抓「這次輪詢區間新突破門檻」的股票，而不是每次都重複提醒同一檔已經爆量的股票。
# ==============================================================================
def detect_intraday_anomalies(current_cards):
    prev = st.session_state.get('anomaly_snapshot', {})
    alerts = []
    new_snapshot = {}
    for c in current_cards:
        code = c.get('code', '')
        if not code:
            continue
        vr = float(c.get('vol_ratio', 0) or 0)
        gain = float(c.get('gain', 0) or 0)
        p = prev.get(code, {})
        prev_vr = float(p.get('vol_ratio', 0) or 0)
        prev_gain = float(p.get('gain', 0) or 0)

        if vr >= 2.0 and prev_vr < 2.0:
            alerts.append(f"🔥 {c.get('name')}({code}) 爆量比剛突破 2.0x（現在 {vr:.1f}x）")
        if gain >= 5.0 and prev_gain < 5.0:
            alerts.append(f"🚀 {c.get('name')}({code}) 漲幅剛突破 +5%（現在 {gain:+.2f}%）")
        if gain <= -5.0 and prev_gain > -5.0:
            alerts.append(f"📉 {c.get('name')}({code}) 跌幅剛突破 -5%（現在 {gain:+.2f}%）")

        new_snapshot[code] = {'vol_ratio': vr, 'gain': gain}

    st.session_state['anomaly_snapshot'] = new_snapshot
    st.session_state.setdefault('anomaly_log', [])
    if alerts:
        ts = datetime.now().strftime('%H:%M:%S')
        for a in alerts:
            st.session_state['anomaly_log'].insert(0, f"[{ts}] {a}")
        st.session_state['anomaly_log'] = st.session_state['anomaly_log'][:30]   # 只留最近30則
    return alerts


# ==============================================================================
# 十、 CSS 與 UI 側邊欄
# ==============================================================================
st.markdown("""<style>
div[data-testid="stSidebar"] { background-color: #12141a !important; border-right: 1px solid #333 !important; }
div[data-testid="stButton"] > button { background-color: #1e1e24 !important; border: 1px solid #444 !important; }
div[data-testid="stButton"] > button p { color: #00d2ff !important; font-weight: bold !important; font-size: 14px !important; }
.hud-box { background: linear-gradient(135deg, #1a1c23 0%, #0d1117 100%); border-radius: 10px; padding: 15px; border-left: 5px solid #ff4d4d; margin-bottom: 20px;}
.zone-box { background: #11141c; border: 1px solid #2c3e50; border-left: 4px solid #2c3e50; border-radius: 6px; padding: 12px 12px 12px 14px; margin-bottom: 12px; color:#eeeeee;}
.zone-1 { border-left-color: #e84393; }
.zone-2 { border-left-color: #00d2ff; }
.zone-3 { border-left-color: #f1c40f; }
.zone-title { color: #00d2ff; font-weight: bold; font-size: 13px; margin-bottom: 8px; border-bottom: 1px solid #2c3e50; padding-bottom: 5px; }
.k-tag { font-size:13px; background:#2c3e50; padding:3px 8px; border-radius:5px; color:#f1c40f; white-space: nowrap; display: inline-block; margin-left:8px; }
.data-chip { display:inline-block; background:#1a2030; border:1px solid #2c3e50; border-radius:4px; padding:2px 7px; margin:2px 3px 2px 0; font-size:12px; }
/* V157 修復：原本 left:50%+translateX(-50%) 置中展開，觸發文字靠近卡片左緣時
   tooltip 左半部會直接衝出邊界被裁切。改為左錨定（貼齊觸發文字左緣向右展開）
   並用 min(...) 限制最大寬度不超過視窗可視範圍，同時保留自動換行避免溢出。 */
.m-tooltip { position: relative; display: inline-block; border-bottom: 1px dotted #888; cursor: help; }
.m-tooltip .m-tooltiptext { visibility: hidden; width: max-content; max-width: min(220px, 78vw); background-color: #333; color: #fff; text-align: left; border-radius: 6px; padding: 10px; position: absolute; z-index: 999; bottom: 125%; left: 0; transform: translateX(0); opacity: 0; transition: opacity 0.3s; font-size: 12px; font-weight: normal; line-height:1.6; overflow-wrap: break-word; word-break: break-word;}
.m-tooltip:hover .m-tooltiptext { visibility: visible; opacity: 1; }
</style>""", unsafe_allow_html=True)

# 【V160 第二階段】登入牆：未通過驗證前，擋住後續所有 UI（側邊欄、主畫面）
require_login()

with st.sidebar:
    st.markdown("<h2 style='color:#f1c40f; text-align:center;'>⚙️ 戰略控制台</h2>", unsafe_allow_html=True)
    if st.button("🔄 強制重整畫面", use_container_width=True):
        st.session_state.last_refresh = time.time()
        st.rerun()

    # 【V160 新增】建置版本標記：確認雲端跑的到底是不是最新檔
    st.caption(f"🏷️ 建置版本：{BUILD_VERSION}")
    st.caption(f"本版重點：{BUILD_NOTES}")

    # 【V160 新增】登出按鈕（總指揮官回報找不到登出功能）
    if st.button("🚪 登出", use_container_width=True):
        st.session_state['authenticated'] = False
        st.rerun()

    # 【V160 新增】FinMind 額度輪替狀態，讓「現在用第幾組帳號」看得見，
    # 不用猜是不是還卡在第一組（先前輪替根本沒接上，額度只有 600 而非 1500）
    with st.expander("🔑 FinMind 額度狀態", expanded=False):
        for _row in get_fm_quota_status():
            st.caption(_row)
        st.caption("額度鏈：帳號1(600) → 帳號2(600) → 訪客(300) = 1500/小時")

    with st.expander("📥 [主攻] 官方 CSV 籌碼強填中樞", expanded=False):
        uploaded_csvs = st.file_uploader("拖曳證交所三大法人 CSV (T86)", type=['csv'],
                                         accept_multiple_files=True, key="csv_up_v3")
        if uploaded_csvs and st.button("🚀 批次強制解析回填至 SQLite", use_container_width=True):
            process_twse_csv(uploaded_csvs)

        # 【V160 改版】原本這裡想用 FinMind「不帶 data_id 的全市場模式」一次抓完整市場，
        # 但總指揮官實測後回報 http_error，查證確認：**那個模式是付費方案專屬**
        # （免費帳號呼叫會收到 "Your level is free." 錯誤）。我 round19 的假設是錯的。
        #
        # 改用確定可行的做法：不掃全市場（那本來就超出免費額度的合理範圍），
        # 改成只同步「你實際在看的股票」——持倉＋雷達＋觀察清單。
        # 這些通常30-100檔，用已經驗證能運作的逐檔同步（每檔1次API、含40天歷史），
        # 額度完全在免費方案的600次/小時內，而且正好覆蓋你真正需要的上櫃股。
        st.divider()
        st.markdown("**🔄 批次同步我關注的股票籌碼（含上櫃）**")
        st.caption("證交所 T86 CSV 只涵蓋上市，上櫃股（6xxx等）沒有批次來源。"
                   "這顆按鈕會把你的**持倉＋雷達＋觀察清單**裡的股票逐檔同步籌碼"
                   "（每檔含近40天歷史，5日/10日才算得出來）。"
                   "⚠️ FinMind 的「一次抓全市場」模式需要付費方案，免費帳號無法使用，"
                   "所以這裡改成只同步你實際關注的標的——通常30-100檔，額度綽綽有餘。")

        _watch_codes = []
        for _sec in ('portfolio', 'pinned_stocks', 'observe_stocks'):
            _watch_codes.extend(list(st.session_state.get(_sec, {}).keys()))
        _watch_codes = sorted(set(_watch_codes))
        _otc_in_list = [c for c in _watch_codes if c.startswith(('4', '5', '6', '8'))]
        st.caption(f"目前清單共 **{len(_watch_codes)}** 檔"
                   f"（其中 {len(_otc_in_list)} 檔可能是上櫃/中小型股，最需要這個同步）")

        if st.button("🔄 開始批次同步", key="batch_sync_btn", use_container_width=True,
                     disabled=not _watch_codes):
            # 【V160 修復】總指揮官回報：按下這顆按鈕後會被踢回登入畫面。
            # 最可能的原因：原本是序列迴圈，30-100檔逐一呼叫FinMind，每檔含網路延遲
            # 可能1-3秒以上，全部跑完可能要好幾分鐘完全不中斷——這種長時間阻塞很容易
            # 讓 Streamlit Cloud 在手機網路下判定連線逾時，重連後 session 就沒了，
            # 回到畫面時自然會被導回登入頁（不是登入邏輯本身有問題，是連線撐不住）。
            # 改成跟持倉/雷達/觀察區同一套 ThreadPoolExecutor，8檔同時處理，
            # DB寫入本來就有 DB_LOCK 保護，多執行緒同時寫是安全的。
            _prog = st.progress(0.0, text=f"⚙️ 同步中 0/{len(_watch_codes)}")
            _ok_n, _fail = 0, []
            _bs_ctx = get_script_run_ctx()
            _bs_done = 0
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                def _sync_with_ctx(code):
                    if _bs_ctx:
                        add_script_run_ctx(threading.current_thread(), _bs_ctx)
                    return sync_single_stock_finmind(code)
                _bs_futures = {executor.submit(_sync_with_ctx, c): c for c in _watch_codes}
                for future in concurrent.futures.as_completed(_bs_futures):
                    _c = _bs_futures[future]
                    _bs_done += 1
                    _prog.progress(_bs_done / len(_watch_codes),
                                  text=f"⚙️ 同步中 {_bs_done}/{len(_watch_codes)}"
                                       f"（{_bs_done/len(_watch_codes)*100:.0f}%）")
                    try:
                        _ok, _msg = future.result()
                        if _ok:
                            _ok_n += 1
                        else:
                            _fail.append(f"{_c}({_msg})")
                    except Exception as e:
                        _fail.append(f"{_c}({type(e).__name__})")
            _prog.progress(1.0, text="完成")
            if _ok_n:
                st.success(f"✅ 成功同步 {_ok_n}/{len(_watch_codes)} 檔")
            if _fail:
                st.warning(f"⚠️ {len(_fail)} 檔失敗：" + "、".join(_fail[:8])
                           + ("..." if len(_fail) > 8 else ""))
            time.sleep(1)
            st.rerun()

    with st.expander("🩺 資料源健康度檢查", expanded=False):
        st.caption("**這個功能是為了解決「靜默失敗」**：先前除權息欄位改名、營收參數矛盾這類問題，"
                   "畫面上都只顯示「查無資料」，看不出是資料源壞了還是本來就沒資料，"
                   "每次都拖很久才發現。這裡逐一實測每個資料源，直接告訴你誰活著、誰壞了。")
        if st.button("🩺 立即檢查所有資料源", key="health_check_btn", use_container_width=True):
            with st.spinner("逐一測試各資料源中（約20-40秒）..."):
                _health = check_data_source_health(get_active_fm_token())
            _bad = [h for h in _health if not h['ok']]
            if not _bad:
                st.success(f"✅ 全部 {len(_health)} 個資料源正常")
            else:
                st.error(f"❌ {len(_bad)} 個資料源異常，需要處理")
            st.dataframe(pd.DataFrame([{
                '資料源': h['name'],
                '狀態': '✅ 正常' if h['ok'] else '❌ 異常',
                '詳情': h['detail'],
            } for h in _health]), use_container_width=True, hide_index=True)

    with st.expander("📊 資料庫完整度與備份還原", expanded=False):
        # 【V160 新增】開機回填天數設定：總指揮官反映每次重新登入要等2-3分鐘，
        # 主因是45天回填視窗隨資料累積越撈越多。這裡讓你自己權衡「登入速度」
        # vs「本機快取涵蓋範圍」——縮小天數不影響 Supabase 雲端的完整歷史，
        # 只影響本機讀取快取涵蓋多少天。
        _cur_refill = int(float(sb_get_config('boot_refill_days', '45')))
        st.markdown("**⚙️ 開機回填天數設定**")
        st.caption("每次重新登入（尤其容器休眠後重啟）都會把這個天數內的籌碼資料從雲端"
                   "回填到本機，資料量越大等越久。縮小天數能加快登入，"
                   "不影響 Supabase 雲端的完整歷史——只是本機快取涵蓋範圍變小。")
        _new_refill = st.slider("回填天數", 7, 90, _cur_refill, 7, key="boot_refill_sld")
        if _new_refill != _cur_refill and st.button("💾 儲存並套用（下次登入生效）",
                                                     key="save_refill_days"):
            sb_set_config('boot_refill_days', str(_new_refill), '開機回填天數')
            st.success(f"✅ 已設定為 {_new_refill} 天，下次登入生效")

        db_days, db_details = get_db_stats()
        if db_days == 0:
            st.warning("⚠️ 目前大腦無籌碼資料")
        else:
            st.write(f"當前儲存天數共: {db_days} 天")
            # 【V160】總指揮官回報「前天31天、今天28天，為什麼變少」——這裡講清楚機制：
            st.caption("ℹ️ 這是**本機**資料庫的天數，不是雲端。Streamlit Cloud 每次重新部署都會"
                       "清空本機檔案，開機時只從雲端回填「最近45天內」的資料，所以天數會隨"
                       "部署與時間視窗滑動而變動。要看完整歷史請以 Supabase 雲端為準；"
                       "若覺得本機缺資料，按上方「🔼一鍵補推」可把本機補回雲端（反向補回會在開機自動做）。")
            with st.container(height=150):
                for detail in db_details:
                    st.caption(f"📅 {detail[0]}: 已存 {detail[1]} 檔籌碼")

        # 【V160】雲端同步狀態 + 手動補推
        st.divider()
        st.markdown("### ☁️ 雲端同步 (Supabase)")
        if not SUPABASE_ENABLED:
            st.caption(f"目前純本機模式：{_SUPABASE_INIT_MSG}")
        else:
            st.caption("本機資料若比雲端新（例如雙寫上線前匯入的舊資料、或Supabase當機期間漏寫），"
                       "可用下方按鈕把本機全部資料補推到雲端，兩邊同步。重複推不會產生重複列。")
            if st.button("🔼 一鍵補推本機資料到雲端", use_container_width=True):
                _push_prog = st.progress(0)
                _push_status = st.empty()

                def _push_cb(kind, done, total):
                    label = "籌碼" if kind == 'inst' else "大戶"
                    _push_status.caption(f"補推{label}：{done}/{total}")
                    _push_prog.progress(min(1.0, done / max(1, total)))

                with st.spinner("補推中，資料量大時需要一點時間..."):
                    _ip, _bp = push_all_local_to_supabase(progress_cb=_push_cb)
                _push_prog.empty()
                _push_status.empty()
                if _ip or _bp:
                    st.success(f"✅ 補推完成：籌碼 {_ip:,} 筆、大戶 {_bp:,} 筆已同步到雲端")
                else:
                    st.warning("沒有補推任何資料（可能本機無資料，或Supabase連線異常）。")

        st.divider()
        st.markdown("### 🔄 強制清除快取重新查詢")
        st.caption("千張大戶／月營收現在的邏輯：一旦抓到成功的數字，會固定保留著，"
                  "之後每30分鐘才檢查一次有沒有新資料（新的月營收、新一週的大戶數字）；"
                  "檢查失敗也不會清空舊數字，會繼續顯示上次抓到的，不會忽有忽無。"
                  "如果你想立即強制重新檢查，按下方按鈕。")
        if st.button("🔄 清除大戶／營收快取，立即重查", use_container_width=True):
            _get_smart_cache_store().clear()
            st.success("✅ 快取已清除，重新整理畫面後會強制重查最新資料")
            time.sleep(0.5)
            st.rerun()


    st.divider()
    min_volume_filter = st.slider("最低 5 日波段均量門檻 (張)", 0, 5000, 500, 100)
    scan_pool_size = st.slider("全市場掃描池大小 (檔)", 100, 1200, 300, 100)
    enable_doomsday_lock = st.checkbox("💀 開啟末日鎔斷防護鎖", value=False)
    enable_market_filter = st.checkbox("🌧️ 開啟大盤位階風控濾網 (TWII 20MA)", value=True)

    if MARKET_REGIME['known']:
        _mk_c = "#00c853" if MARKET_REGIME['bull'] else "#ff4d4d"
        _mk_t = "站上 20MA (多方環境)" if MARKET_REGIME['bull'] else "跌破 20MA (訊號強制降級)"
        st.markdown(f"<div style='font-size:12px; color:{_mk_c};'>大盤 {MARKET_REGIME['close']:,.0f} / "
                    f"20MA {MARKET_REGIME['ma20']:,.0f}（{MARKET_REGIME['dev']:+.1f}%）<br>{_mk_t}</div>",
                    unsafe_allow_html=True)
    else:
        st.caption("大盤位階：資料抓取中（暫不降級）")

    st.divider()
    st.markdown("<div style='font-size:12px; font-weight:bold;'>📡 盤中自動輪詢（陽春版）</div>", unsafe_allow_html=True)
    auto_poll_enabled = st.checkbox("開啟自動輪詢", value=False, key="auto_poll_enabled",
                                    help="部署在 Streamlit Cloud 免費版，沒有背景執行能力。這個功能只在你"
                                         "開著這個網頁分頁時有效，每隔設定的分鐘數自動重新整理一次，偵測"
                                         "雷達/持倉清單的價量異常並顯示在頁面上方。分頁關掉就不會繼續監控，"
                                         "目前也還沒接推播（Line/Telegram等），異常只會顯示在網頁上。")
    if auto_poll_enabled:
        poll_interval_min = st.slider("輪詢間隔(分鐘)", 1, 15, 3, key="poll_interval_min")
        try:
            from streamlit_autorefresh import st_autorefresh
            st_autorefresh(interval=poll_interval_min * 60 * 1000, key="autorefresh_timer")
        except ImportError:
            st.caption("⚠️ 需先安裝 `streamlit-autorefresh` 套件才能自動重新整理；"
                       "沒裝的話請手動按重新整理來輪詢。")

    st.divider()
    commands_list = ["查1.主升段突擊", "查2.魚頭慢伏支撐", "查3.價值投資與循環", "查4.投信作帳集團股",
                     "查5.籌碼外資霸王色", "查6.營收雙增爆發突破", "查8.昨日強勢動能延續",
                     "查9.均線糾結爆量突破", "查10.籌碼沉澱量縮潛伏", "查11.除權息尋寶雷達",
                     "查12.K線型態尋寶型"]

    intel_pool = st.session_state.get('intelligence_pool', {})
    existing_sources = set([src for info in intel_pool.values()
                            if isinstance(info, dict) for src in info.get("sources", [])])
    base_idx = 13
    for src in sorted(list(existing_sources)):
        commands_list.append(f"查{base_idx}. 情報雷達：{src}")
        SCAN_COMMAND_MAP[f"查{base_idx}"] = f"情報雷達：{src}"
        base_idx += 1
    if existing_sources:
        commands_list.append(f"查{base_idx}. 🏆 情報黃金交叉")
        SCAN_COMMAND_MAP[f"查{base_idx}"] = "🏆 情報黃金交叉（多個情報來源同時指向）"

    selected_cmds = st.multiselect("🎯 戰略掃描條件 (可複選交集)", commands_list, default=[])
    selected_k_patterns = []
    if any("查12" in cmd for cmd in selected_cmds):
        with st.container(border=True):
            if st.checkbox("🔥 長紅吞噬 / 低檔長紅"): selected_k_patterns.append("長紅")
            if st.checkbox("🔥 紅三兵強勢推推"): selected_k_patterns.append("紅三兵")
            if st.checkbox("💀 長黑吞噬頂部出貨"): selected_k_patterns.append("長黑")
            if st.checkbox("💀 黑三兵弱勢跌破"): selected_k_patterns.append("黑三兵")

    # 【V160 新增】全市場掃描本身也支援評分範圍篩選（不只是雷達/觀察區列表篩選），
    # 跟戰略掃描條件(查X)一起AND生效，掃描時就直接排除範圍外的，不用先掃完再篩。
    scan_score_range = st.slider("📊 掃描評分範圍篩選（只保留評分落在此區間的結果）", -10, 10, (-10, 10),
                                 key="scan_score_range")

    if st.button("🚀 執行全市場並行高速掃描", use_container_width=True, type="primary"):
        if not selected_cmds:
            st.warning("請先選擇至少一項戰略條件。")
        else:
            st.session_state.trigger_scan = True

    with st.expander("📖 統籌戰術解密說明書", expanded=False):
        st.markdown("""<div style="font-size:13px; color:#ffffff; background:#1e1e24; padding:15px; border-radius:8px;">
        <b style='color:#f1c40f;'>🎯 建議每日操作流程（提升勝率的搭配順序）</b><br><br>
        <b style='color:#00d2ff;'>①開盤前（8:55前）</b>：先看最上方「隔夜總經」HUD，確認開盤前閘門是否顯示暫緩。
        隔夜劇變時，當天寧可保守，不強求進場。<br>
        <b style='color:#00d2ff;'>②全市場掃描</b>：按「執行全市場並行高速掃描」，可疊加「戰略掃描條件」縮小範圍
        （查1~查12任選複選）。掃出來的先進<b>觀察區</b>，不要直接進常態雷達——避免雷達被還沒驗證過的股票稀釋。<br>
        <b style='color:#00d2ff;'>③交叉驗證</b>：對觀察區裡有興趣的標的，去「訊號命中率回測實驗室」查它過去3年
        該訊號的真實勝率／樣本數（樣本<30筆別信）；有情報來源的，去「情報來源準確度」看該來源歷史準不準。
        兩邊都過關，才升級到常態雷達。<br>
        <b style='color:#00d2ff;'>④決策</b>：常態雷達卡片最上方的動詞橫幅（建議進攻/觀望/撤退）+ 進場價格區間，
        是秒讀決策的核心，不用每次都展開三戰區細節。<br>
        <b style='color:#00d2ff;'>⑤持倉管理</b>：進場後移到持倉模擬倉，防守線／短線停利點會持續更新，跌破防守線是
        結構破壞的訊號。<br>
        <b style='color:#00d2ff;'>⑥每週回顧</b>：看「手動 vs 系統查詢 勝率PK」跟「系統自主選股（做多vs做空）」，
        比較這陣子是你的直覺準、還是系統的量化濾網準，用來校準自己該多信哪一邊。</div>""", unsafe_allow_html=True)

        st.markdown("""<div style="font-size:13px; color:#ffffff; background:#1e1e24; padding:15px; border-radius:8px; margin-top:10px;">
        <b style='color:#f1c40f;'>🛡️ V160 戰情室濾網大公開</b><br>
        <b style='color:#00d2ff;'>查1.</b> 首根長紅(今紅昨黑·實體>0.5ATR) + 爆量>=2.0 + KDJ金叉<br>
        <b style='color:#00d2ff;'>查2.</b> 股價站上季線(60MA) + 爆量>=1.2<br>
        <b style='color:#00d2ff;'>查3.</b> 價值分數>=60 + 無基本面地雷<br>
        <b style='color:#00d2ff;'>查4.</b> 投信單日買超>0<br>
        <b style='color:#00d2ff;'>查5.</b> 外資買超 + 融資減少(未同步融資者視為通過)<br>
        <b style='color:#00d2ff;'>查6.</b> 營收 YoY 年增 > 20%<br>
        <b style='color:#00d2ff;'>查8.</b> 昨日漲幅 > 5%<br>
        <b style='color:#00d2ff;'>查9.</b> 今日爆量比 >= 2.0x<br>
        <b style='color:#00d2ff;'>查10.</b> 爆量比 <= 0.6 (量縮>40%) + 融資減少<br>
        <b style='color:#00d2ff;'>查11.</b> 現金殖利率 >= 4.5%<br>
        <b style='color:#00d2ff;'>查12.</b> 特定K線型態 (ATR動態判定)<br>
        <b style='color:#f1c40f;'>查13+.</b> 情報雷達：只掃該來源綁定過的標的<br>
        <b style='color:#f1c40f;'>黃金交叉.</b> 同時被 2 個以上情報來源提及</div>""", unsafe_allow_html=True)

        st.markdown("""<div style="font-size:13px; color:#ffffff; background:#1e1e24; padding:15px; border-radius:8px; margin-top:10px;">
        <b style='color:#f1c40f;'>🧪 三個驗證工具，各自該用在什麼時候</b><br><br>
        <b style='color:#00d2ff;'>訊號命中率回測實驗室</b>：驗證「技術面訊號本身」準不準——某檔股票過去出現
        這個訊號時，後續3/10日漲跌如何。適合用在：你想加一檔股票進雷達前，先確認這檔股票的訊號歷史上靠不靠譜。<br>
        <b style='color:#00d2ff;'>情報來源準確度</b>：驗證「消息來源」準不準——股癌/法說會/券商報告這些來源，
        過去報過的股票後續表現如何。適合用在：你手上有多個情報來源，想知道該優先信哪個。<br>
        <b style='color:#00d2ff;'>手動vs系統勝率PK</b>：驗證「選股方式」準不準——你自己手動挑的 vs 系統演算法篩的，
        誰的歷史報酬比較好。適合用在：定期（建議每週）檢視，決定這陣子該多聽自己的判斷還是多信系統。<br><br>
        三者是不同層次：訊號驗證「這檔股票該不該信」、來源驗證「這則消息該不該信」、PK驗證「這個選股方法該不該信」，
        建議都用，各司其職，不是互相替代。</div>""", unsafe_allow_html=True)

    st.divider()
    st.markdown("<div style='font-size:12px; font-weight:bold; margin-bottom:5px;'>📡 系統連線狀態</div>",
                unsafe_allow_html=True)
    _sb_icon = "🟢" if SUPABASE_ENABLED else "⚪"
    _sb_sync = st.session_state.get('sb_sync_result', (0, 0))
    st.markdown(f"<div style='font-size:11px;'>{'🟢' if API_READY else '🔴'} NVIDIA NIM<br>"
                f"{'🟢' if FINMIND_READY else '🔴'} FinMind 線路<br>"
                f"{_sb_icon} Supabase 雲端大腦</div>", unsafe_allow_html=True)
    if SUPABASE_ENABLED:
        st.caption(f"雙軌已啟用｜開機回填 籌碼{_sb_sync[0]}筆／大戶{_sb_sync[1]}筆")
    else:
        st.caption(f"純本機模式：{_SUPABASE_INIT_MSG}")

    # 【V160 新功能】NVIDIA 模型手動選擇（預設仍是自動偵測優先 DeepSeek，但可手動切換）
    if API_READY:
        _discovered = get_nim_models()
        if _discovered:
            _model_short_names = [m.split('/')[-1] for m in _discovered]
            _prev_choice = st.session_state.get('preferred_nim_model', _model_short_names[0])
            _default_idx = _model_short_names.index(_prev_choice) if _prev_choice in _model_short_names else 0
            _picked = st.selectbox("🤖 AI推演偏好模型", _model_short_names, index=_default_idx,
                                   help="預設優先用DeepSeek(邏輯較強)。可手動切換成清單裡其他偵測到的模型。"
                                        "如果你選的那個剛好失效，系統會自動退回清單裡其他可用模型，不會整個掛掉。")
            st.session_state['preferred_nim_model'] = _picked


    with st.expander("💾 備份還原（雲端已自動同步，這裡僅供緊急還原用）", expanded=False):
        st.caption("雷達／持倉／情報／人工覆寫都已經自動同步進 Supabase 雲端，"
                   "平常不需要手動備份。這裡保留給萬一雲端出狀況時的緊急還原用，"
                   "建議一週手動存一次當保險即可，不用每天做。")
        col_dl1, col_dl2 = st.columns(2)
        with col_dl1:
            if os.path.exists(USER_DB_FILE):
                with open(USER_DB_FILE, "rb") as f:
                    st.download_button("📄 下載設定檔", f.read(), "54088_database.json",
                                       "application/json", use_container_width=True)
        with col_dl2:
            if os.path.exists(SQLITE_DB_FILE):
                with open(SQLITE_DB_FILE, "rb") as f:
                    st.download_button("🗄️ 下載籌碼庫", f.read(), "54088_inst_history.db",
                                       "application/octet-stream", use_container_width=True)

        st.divider()
        uploaded_json = st.file_uploader("上傳 54088_database.json", type=['json'], key="restore_json_v1")
        uploaded_db = st.file_uploader("上傳 54088_inst_history.db", type=['db'], key="restore_db_v1")
        if st.button("🚀 執行實體大腦覆蓋還原", use_container_width=True):
            if uploaded_json:
                with open(USER_DB_FILE, "wb") as f:
                    f.write(uploaded_json.getbuffer())
                st.success("📄 設定檔覆蓋成功！")
            if uploaded_db:
                try:
                    SQLITE_CONN.close()
                except Exception:
                    pass
                with open(SQLITE_DB_FILE, "wb") as f:
                    f.write(uploaded_db.getbuffer())
                SQLITE_CONN = get_db_conn()
                _ensure_schema(SQLITE_CONN)
                st.success("🗄️ 籌碼庫全面覆蓋還原成功！")
            time.sleep(1)
            st.rerun()


# ==============================================================================
# 十一、 主畫面
# ==============================================================================
st.title("🚀 作戰室 正式版 v1.0")

# 【V160 修復】config_payload 提前到這裡定義（原本放在檔案很後面，導致「系統自主選股」
# 面板呼叫時 config_payload 還沒被賦值，觸發 NameError）。所需材料（enable_doomsday_lock、
# enable_market_filter 等側邊欄開關）在上方側邊欄區塊已經賦值完成，這裡引用是安全的。
config_payload = {
    'token': get_active_fm_token(),
    'rev_override': st.session_state.revenue_override,
    'bh_override': st.session_state.bigholder_override,
    'div_override': st.session_state.dividend_override,
    'dividend_db': DIVIDEND_DB,
    'stock_names': TW_STOCK_NAMES,
    'pinned_stocks': st.session_state.pinned_stocks,
    'enable_doomsday': enable_doomsday_lock,
    'market_bull': (MARKET_REGIME['bull'] or not enable_market_filter),
}

_regime_badge = ("<span style='color:#00c853;'>站上20MA</span>" if MARKET_REGIME['bull']
                 else "<span style='color:#ff4d4d;'>跌破20MA·多方訊號降級</span>") if MARKET_REGIME['known'] else "<span style='color:#888;'>計算中</span>"
st.markdown(f"""<div class='hud-box'>
    <div style='color:#f1c40f; font-size:16px; font-weight:bold; margin-bottom:4px;'>📊 大將軍智慧 HUD 總覽</div>
    <div style='color:#ddd; font-size:14px;'><b>大盤氣象：</b> <span style='color:{weather_color}; font-weight:bold;'>上市大盤 {weather_str}</span> | <b>位階濾網：</b> {_regime_badge}</div>
</div>""", unsafe_allow_html=True)

# 【V160 A階段】隔夜總經 HUD：台股先行指標
_macro = get_overnight_macro()
_macro_chips = []
for _name in ('那斯達克', '標普500', '費城半導體', '那斯達克期貨', '標普期貨', '台積電ADR', '聯電ADR', '美元台幣'):
    _d = _macro.get(_name, {})
    if _d.get('ok'):
        _mc = "#ff4d4d" if _d['pct'] > 0 else ("#00c853" if _d['pct'] < 0 else "#999")
        _val_fmt = f"{_d['value']:,.2f}" if _name in ('美元台幣', '台積電ADR', '聯電ADR') else f"{_d['value']:,.0f}"
        _pt = _d.get('pt_change', 0)
        _pt_fmt = f"{abs(_pt):,.2f}" if _name in ('美元台幣', '台積電ADR', '聯電ADR') else f"{abs(_pt):,.0f}"
        _arrow = "▲" if _pt > 0 else ("▼" if _pt < 0 else "▬")
        _macro_chips.append(f"<span style='margin-right:14px;'><b>{_name}</b> {_val_fmt} "
                            f"<span style='color:{_mc};'>({_arrow}{_pt_fmt} | {_d['pct']:+.2f}%)</span></span>")
    else:
        _note = _d.get('note', '連線中')
        _macro_chips.append(f"<span style='margin-right:14px; color:#9fb3c8;'><b>{_name}</b> {_note}</span>")
_gate_status, _gate_reason = evaluate_overnight_gate(_macro)
_gate_color = "#00c853" if _gate_status == 'normal' else "#ff4d4d"
# 【V160 簡化】日期只在標題後面標一次（美股系列共用同一個收盤日，不用每個指標各標一次，
# 避免畫面太擁擠）；不再另外顯示「查看時間」，手機本身就有時鐘不需要重複。
_macro_date = _macro.get('那斯達克', {}).get('data_date', '')
# 【V160 修復】原本 #666 在深色背景上幾乎看不見（總指揮官回報「文字不明顯、顏色太淺」），
# 提亮到 #9fb3c8 並加大一級字，仍維持次要資訊的視覺層級、不搶主指標的注意力。
_date_tag = f"<span style='color:#ffd479; font-size:13px; font-weight:600;'>（美股 {_macro_date} 收盤）</span>" if _macro_date else ""
st.markdown(f"""<div class='hud-box' style='margin-top:-4px;'>
    <div style='color:#7ab8ff; font-size:14px; font-weight:bold; margin-bottom:4px;'>🌙 隔夜總經 {_date_tag} <span style='color:{_gate_color}; font-size:12px;'>｜開盤前閘門：{_gate_reason}</span></div>
    <div style='color:#ddd; font-size:13px;'>{''.join(_macro_chips)}</div>
</div>""", unsafe_allow_html=True)

# 【V160 B#11】速覽模式開關（放在標題正下方最顯眼處）
st.checkbox("⚡ 速覽模式：所有標的（持倉+雷達+觀察）攤平成一張總表，5秒掃完全部",
            value=st.session_state.get('quick_overview_mode', False), key="quick_overview_mode")

with st.expander("🤖 系統自主選股模擬倉（做多 vs 做空 勝率PK）", expanded=False):
    st.caption("系統每天自動全市場選股、自動進出場，同時跑做多和做空兩個模擬倉。你不用干預，"
               "只看它選了哪些、報酬如何。與你手動選股對照，看誰的勝率高。")

    # 資金設定（可調，存 system_config）
    _sys_cap = get_system_capital()
    _new_cap = st.number_input("每日系統選股總額（元，依當天入選檔數平分）", min_value=10000,
                               max_value=10000000, value=_sys_cap, step=50000, key="sys_capital_input")
    if _new_cap != _sys_cap:
        if st.button("💾 更新總額設定", key="save_sys_cap"):
            if sb_set_config('system_pick_daily_capital', int(_new_cap), '系統自主選股每日投入總額'):
                st.success(f"✅ 已更新為 {_new_cap:,} 元")
                time.sleep(0.5)
                st.rerun()
            else:
                st.warning("更新失敗（Supabase 未連線？）")

    # 【V160 延伸4】ATR 移動停利設定
    _tc = get_trail_config()
    with st.expander("📈 ATR 移動停利設定（提高賺賠比，預設關閉）", expanded=False):
        st.caption("原本的出場規則是「固定停利點，一碰到就出場」，這會在大波段行情裡提早下車。"
                   "移動停利改成：價格往有利方向走時，停損線跟著抬高，只有回檔超過 N×ATR 才出場。"
                   "⚠️ 誠實說明：這提高的是**賺賠比**，不是勝率——它甚至可能小幅降低勝率"
                   "（部分原本會碰到固定停利的單，改成回檔出場時價格較低）。所以預設關閉，"
                   "建議你開啟後跑一個月，用上方績效表跟現在的數字對照，自己決定要不要留。")
        _t_on = st.checkbox("啟用 ATR 移動停利", value=_tc['enabled'], key="trail_on_cb")
        _t_mult = st.slider("回檔幾倍 ATR 出場（越大抱越久、回吐越多）", 1.0, 4.0,
                            _tc['mult'], 0.5, key="trail_mult_sld")
        _t_act = st.slider("獲利幾倍 ATR 才啟動（太小會被正常波動洗掉）", 0.5, 3.0,
                           _tc['activate_mult'], 0.5, key="trail_act_sld")
        if st.button("💾 儲存移動停利設定", key="save_trail_cfg", use_container_width=True):
            sb_set_config('trail_stop_enabled', '1' if _t_on else '0', 'ATR移動停利開關')
            sb_set_config('trail_stop_mult', str(_t_mult), 'ATR移動停利回檔倍數')
            sb_set_config('trail_stop_activate_mult', str(_t_act), 'ATR移動停利啟動門檻倍數')
            st.success("✅ 已儲存")
            time.sleep(0.5)
            st.rerun()
        if _tc['enabled']:
            st.info(f"目前啟用中：獲利超過 {_tc['activate_mult']}×ATR 後啟動，"
                    f"回檔 {_tc['mult']}×ATR 出場（出場原因會標記為 trail_stop，"
                    f"可在績效細節表裡跟 stop_loss／take_profit 分開比較）。")

    # 【V160】總指揮官確認排程已正常自動運作，移除手動測試選股按鈕
    # （原本只是給排程上線前測試用；system_select_candidates/system_build_entries
    #  這兩個核心函式排程仍在用，只是拿掉這顆網頁手動觸發鈕）。


    # 檢查出場
    if st.button("🔄 檢查並執行自動出場（出場規則B）", key="check_sys_exits", use_container_width=True):
        with st.spinner("檢查所有持倉是否觸發停損/停利..."):
            _exits = system_check_exits(config_payload)
            if _exits:
                system_apply_exits(_exits)
                st.success(f"✅ {len(_exits)} 檔觸發出場：" +
                           "、".join(f"{e['symbol']}({_exit_reason_zh(e['exit_reason'])},{e['realized_roi']:+.1f}%)" for e in _exits))
            else:
                st.info("目前沒有持倉觸發出場條件。")
        time.sleep(1)
        st.rerun()

    # 【V160 新功能】檢查並執行加碼/攤平（依訊號判斷，每檔各上限一次）
    if st.button("➕➖ 檢查並執行加碼/攤平", key="check_add_reduce", use_container_width=True):
        with st.spinner("檢查所有持倉是否符合加碼/攤平條件..."):
            _acts = system_check_add_reduce(config_payload)
            if _acts:
                system_apply_add_reduce(_acts)
                _add_list = [f"{a['symbol']}(加碼{a['add_shares']}張)" for a in _acts if a['action'] == 'add']
                _red_list = [f"{a['symbol']}(攤平{a['add_shares']}張)" for a in _acts if a['action'] == 'reduce']
                _msg = "✅ "
                if _add_list:
                    _msg += "順勢加碼：" + "、".join(_add_list) + "　"
                if _red_list:
                    _msg += "逆勢攤平：" + "、".join(_red_list)
                st.success(_msg)
            else:
                st.info("目前沒有持倉符合加碼/攤平條件（或都已達各自上限一次）。")
        time.sleep(1)
        st.rerun()

    st.divider()
    # 績效統計
    _stats = get_system_portfolio_stats()
    st.markdown("**📊 系統模擬倉績效（已實現）**")
    _perf_df = pd.DataFrame([
        {'方向': '🔴 做多', **_stats['long_closed']},
        {'方向': '🔵 做空', **_stats['short_closed']},
    ])
    st.dataframe(_style_pnl_columns(_perf_df, ['平均報酬%', '總損益']),
                 use_container_width=True, hide_index=True)

    # 【V160 新增】總指揮官回報：績效摘要只有多空兩列總計，看不到細節操作
    # （哪幾檔、什麼時候進出、賺賠多少）。加一個可展開的明細表。
    _closed_list = _stats.get('closed', [])
    if _closed_list:
        with st.expander(f"🔎 查看已實現績效細節（共 {len(_closed_list)} 筆已結算）", expanded=False):
            _side_filter = st.radio("篩選方向", ["全部", "🔴做多", "🔵做空"],
                                    horizontal=True, key="perf_detail_side_filter")
            _rows = _closed_list
            if _side_filter == "🔴做多":
                _rows = [r for r in _rows if r.get('side') == 'long']
            elif _side_filter == "🔵做空":
                _rows = [r for r in _rows if r.get('side') == 'short']
            _detail_df = pd.DataFrame([{
                '方向': '🔴做多' if r.get('side') == 'long' else '🔵做空',
                '代號': r.get('symbol'),
                '名稱': (TW_STOCK_NAMES.get(r.get('symbol'))
                        or (r.get('name') if r.get('name') != r.get('symbol') else None)
                        or r.get('symbol')),
                '來源': '🧪手動' if (r.get('trigger_source') or 'manual') == 'manual' else '🤖排程',
                '進場日': r.get('entry_date'), '進場價': r.get('entry_price'),
                '出場日': r.get('exit_date'), '出場價': r.get('exit_price'),
                '損益': r.get('realized_pnl'), '報酬%': r.get('realized_roi'),
                '出場原因': _exit_reason_zh(r.get('exit_reason')),
            } for r in sorted(_rows, key=lambda r: r.get('exit_date') or '', reverse=True)])
            st.dataframe(_style_pnl_columns(_detail_df, ['損益', '報酬%']),
                        use_container_width=True, hide_index=True)
    st.caption(f"目前持倉中：{_stats['holding_count']} 檔")
    if _stats['holding']:
        # 【V160 修復】方向欄位改用顏色圖示（🔴做多／🔵做空），跟上面績效摘要表用同一套視覺語言，
        # 一眼掃色就能分辨多空，不用每行重複讀「多」「空」文字。
        _hold_df = pd.DataFrame([{
            '方向': '🔴' if h.get('side') == 'long' else '🔵',
            '代號': h.get('symbol'),
            # 【V160 修復】建倉當下若 TaiwanStockInfo 名稱表沒抓到，name 會退回成代號，
            # 畫面就變成「代號、名稱」兩欄都是數字。這裡在顯示時用最新的名稱表回填，
            # 名稱表也沒有才顯示代號。
            '名稱': (TW_STOCK_NAMES.get(h.get('symbol'))
                     or (h.get('name') if h.get('name') != h.get('symbol') else None)
                     or h.get('symbol')),
            # 【V160 新增】來源：分辨這筆是你手動測試建的，還是 GitHub Actions 排程自動建的。
            '來源': '🧪手動' if (h.get('trigger_source') or 'manual') == 'manual' else '🤖排程',
            '進場日': h.get('entry_date'), '進場價': h.get('entry_price'),
            '張數': h.get('shares'), '防守線': h.get('def_line'), '停利點': h.get('take_profit'),
            '選股理由': h.get('select_reason', '—'),
        } for h in _stats['holding']])
        st.dataframe(_hold_df, use_container_width=True, hide_index=True)
        st.caption("🔴=做多／🔵=做空｜🧪手動=你按測試鈕建的，🤖排程=GitHub Actions 自動建的。"
                  "選股理由記錄了每檔當初為什麼被系統選中，"
                  "之後某檔勝率高，就能回頭分析它的共同特徵，優化選股邏輯。")

        # 【V160 新增】單檔績效查詢：看某一檔在模擬倉裡的完整進出與累計成績
        with st.expander("🔍 單檔績效查詢（某一檔幫我賺多少／賠多少）", expanded=False):
            # 【V160 修復】原本要手動打代號才能查，但根本不知道有哪幾檔交易過可以查。
            # 改成列出「所有有交易紀錄的標的」讓你直接選，仍保留輸入框給知道代號的情況。
            _traded = get_all_traded_symbols()
            if _traded:
                _sym_opts = ["—"] + [f"{s} {n}（{c}筆）" for s, n, c in _traded]
                _sym_map = {f"{s} {n}（{c}筆）": s for s, n, c in _traded}
                _sym_pick = st.selectbox(f"選擇標的（共 {len(_traded)} 檔有交易紀錄）",
                                         _sym_opts, key="sym_perf_pick")
                _sym_q = _sym_map.get(_sym_pick, "")
            else:
                st.caption("目前系統模擬倉沒有任何交易紀錄。")
                _sym_q = ""
            _sym_manual = st.text_input("或直接輸入代號查詢", key="sym_perf_q", placeholder="例如 2409")
            if _sym_manual.strip():
                _sym_q = _sym_manual.strip()
            if _sym_q:
                _cl, _hd, _stt = get_symbol_performance(_sym_q.strip())
                if not _cl and not _hd:
                    st.info(f"{_sym_q.strip()} 在系統模擬倉沒有任何紀錄。")
                else:
                    q1, q2, q3, q4 = st.columns(4)
                    q1.metric("已結算筆數", _stt['closed_count'])
                    q2.metric("持倉中", _stt['holding_count'])
                    q3.metric("勝率%", _stt['win_rate'] if _stt['win_rate'] is not None else "—")
                    q4.metric("累計損益", f"{_stt['total_pnl']:,.0f}"
                              if _stt['closed_count'] else "—")
                    if _stt['avg_roi'] is not None:
                        st.caption(f"平均報酬率 {_stt['avg_roi']:+.2f}%")
                    if _cl:
                        st.markdown("**已結算紀錄**")
                        _cl_df = pd.DataFrame([{
                            '方向': '🔴做多' if r.get('side') == 'long' else '🔵做空',
                            '來源': '🧪手動' if (r.get('trigger_source') or 'manual') == 'manual' else '🤖排程',
                            '進場日': r.get('entry_date'), '進場價': r.get('entry_price'),
                            '出場日': r.get('exit_date'), '出場價': r.get('exit_price'),
                            '損益': r.get('realized_pnl'), '報酬%': r.get('realized_roi'),
                            '出場原因': _exit_reason_zh(r.get('exit_reason')),
                        } for r in _cl])
                        st.dataframe(_style_pnl_columns(_cl_df, ['損益', '報酬%']),
                                    use_container_width=True, hide_index=True)
                    if _hd:
                        st.markdown("**持倉中**")
                        st.dataframe(pd.DataFrame([{
                            '方向': '🔴做多' if r.get('side') == 'long' else '🔵做空',
                            '來源': '🧪手動' if (r.get('trigger_source') or 'manual') == 'manual' else '🤖排程',
                            '進場日': r.get('entry_date'), '進場價': r.get('entry_price'),
                            '張數': r.get('shares'), '狀態': r.get('status'),
                        } for r in _hd]), use_container_width=True, hide_index=True)

        # 【V160 新功能】手動平倉／刪除：之前完全沒有手動介入的方式，只能等自動出場條件觸發。
        st.markdown("**🛠️ 手動平倉／刪除持倉**")

        # 【V160 新增】批次刪除手動測試持倉。總指揮官回報：一筆一筆刪太慢——
        # 手動測試按鈕經常一次建好幾筆（如截圖 5 筆），逐一選單挑選刪除很沒效率。
        # 只鎖定 trigger_source='manual' 的持倉，避免手滑連排程真實持倉一起刪掉。
        _manual_holds = [h for h in _stats['holding']
                         if (h.get('trigger_source') or 'manual') == 'manual']
        if _manual_holds:
            with st.expander(f"🧹 批次刪除手動測試持倉（共 {len(_manual_holds)} 筆）", expanded=False):
                st.caption("只列出來源＝🧪手動的持倉；🤖排程建立的不會出現在這裡，避免誤刪真實紀錄。")
                _batch_opts = {
                    f"#{h['id']} {'🔴' if h.get('side')=='long' else '🔵'}{h.get('symbol')} "
                    f"進場{h.get('entry_price')} {h.get('shares')}張 ({h.get('entry_date')})": h['id']
                    for h in _manual_holds
                }
                _batch_picked = st.multiselect("勾選要刪除的持倉（可多選）",
                                               list(_batch_opts.keys()), key="batch_del_manual")
                if _batch_picked and st.button(
                        f"🗑️ 確認刪除選中的 {len(_batch_picked)} 筆（不留紀錄，不計入勝率）",
                        key="batch_del_manual_btn", use_container_width=True):
                    _ids_to_del = [_batch_opts[k] for k in _batch_picked]
                    def _do_batch_delete():
                        return (SUPABASE_CONN.table("system_portfolio")
                                .delete().in_("id", _ids_to_del).execute())
                    ok, _ = _sb_safe(_do_batch_delete)
                    if ok:
                        st.success(f"✅ 已刪除 {len(_ids_to_del)} 筆手動測試持倉")
                    else:
                        st.warning("批次刪除失敗，請稍後再試。")
                    time.sleep(1)
                    st.rerun()

        _hold_labels = {
            f"#{h['id']} {'🔴' if h.get('side')=='long' else '🔵'}{h.get('symbol')} "
            f"進場{h.get('entry_price')} {h.get('shares')}張 ({h.get('entry_date')})": h
            for h in _stats['holding']
        }
        _picked_label = st.selectbox("選擇要操作的持倉", ["—"] + list(_hold_labels.keys()),
                                     key="manual_holding_pick")
        if _picked_label != "—":
            _picked_h = _hold_labels[_picked_label]
            mc1, mc2 = st.columns(2)
            if mc1.button("✅ 手動平倉（用現價結算損益，計入勝率統計）", key="manual_close_btn",
                          use_container_width=True):
                _cc = calculate_signals_worker(_picked_h['symbol'], config_payload)
                _cur = float(_cc.get('price', 0) or 0) if _cc and not _cc.get('error') else 0.0
                if _cur <= 0:
                    st.warning("抓不到現價，無法結算，請稍後再試。")
                else:
                    _entry = float(_picked_h.get('entry_price', 0) or 0)
                    _sh = int(_picked_h.get('shares', 0) or 0)
                    if _picked_h.get('side') == 'long':
                        _pnl = (_cur - _entry) * _sh * 1000
                    else:
                        _pnl = (_entry - _cur) * _sh * 1000
                    _roi = (_pnl / (_entry * _sh * 1000) * 100) if _entry > 0 and _sh > 0 else 0.0
                    system_apply_exits([{**_picked_h, 'exit_price': _cur, 'exit_reason': 'manual',
                                         'realized_pnl': round(_pnl, 0), 'realized_roi': round(_roi, 2)}])
                    st.success(f"✅ {_picked_h['symbol']} 已手動平倉，損益 {_pnl:+,.0f} 元 ({_roi:+.1f}%)，計入勝率統計")
                    time.sleep(1)
                    st.rerun()
            if mc2.button("🗑️ 直接刪除（不留紀錄，不計入勝率）", key="manual_delete_btn",
                          use_container_width=True):
                def _do_delete():
                    return SUPABASE_CONN.table("system_portfolio").delete().eq("id", _picked_h['id']).execute()
                ok, _ = _sb_safe(_do_delete)
                if ok:
                    st.success(f"✅ {_picked_h['symbol']} 已刪除")
                else:
                    st.warning("刪除失敗，請稍後再試。")
                time.sleep(1)
                st.rerun()
            st.caption("💡 手動平倉：用現價結算損益，跟自動出場一樣計入勝率統計（適合你想主動了結一筆）。"
                      "直接刪除：整筆紀錄消失、不計入任何統計（適合測試資料想清掉重來）。")

with st.expander("🏭 族群輪動熱力圖（找出資金正在流入哪個產業）", expanded=False):
    st.caption("個股會漲通常是因為整個族群在動。先確認族群趨勢再選個股，等於多一層過濾，"
               "能降低「選對股但選錯時機」的虧損。這項功能完全使用既有的免費資料"
               "（產業分類 + 股價），不需要付費 API。")
    _rot_n = st.slider("掃描檔數（越多越完整，但耗時越久）", 50, 400, 150, 50, key="rot_scan_n")
    if st.button("🔄 計算族群輪動", key="rot_calc_btn", use_container_width=True):
        _s2i, _i2s = fetch_industry_map()
        if not _s2i:
            st.warning("產業分類資料抓取失敗（FinMind TaiwanStockInfo 未回應），無法計算。")
        else:
            with st.spinner(f"掃描 {_rot_n} 檔股票、彙整產業強弱中..."):
                _rot_rows = compute_industry_rotation(
                    get_scan_pool_ordered()[0][:_rot_n], _s2i, max_scan=_rot_n)
            st.session_state['rotation_rows'] = _rot_rows

    _rot_rows = st.session_state.get('rotation_rows')
    if _rot_rows:
        _rot_df = pd.DataFrame(_rot_rows)
        # 用背景色階呈現強弱（紅=強、綠=弱，符合台股習慣）
        try:
            _styled = _rot_df.style.background_gradient(
                subset=['5日%'], cmap='RdYlGn_r').format(precision=2)
            st.dataframe(_styled, use_container_width=True, hide_index=True)
        except Exception:
            # styler 需要 matplotlib，沒有就退回普通表格，不讓功能整個掛掉
            st.dataframe(_rot_df, use_container_width=True, hide_index=True)
        st.markdown("#### 🧭 輪動判讀")
        for _line in build_rotation_advice(_rot_rows):
            st.markdown(_line)
    elif _rot_rows == []:
        st.info("沒有產業達到最低檔數門檻（每個產業至少3檔），試著加大掃描檔數。")

with st.expander("📊 情報來源準確度 & 選股勝率PK (V160)", expanded=False):
    pk_tab1, pk_tab2 = st.tabs(["📰 情報來源準確度", "👤vs🤖 選股勝率PK"])

    with pk_tab1:
        st.caption("追蹤每個情報來源／標籤，情報發布後 3/10/20 日的實際報酬與勝率。無未來函數，未到期的自動略過。")
        _custom_d = st.number_input("自訂回顧天數（選填，例如看 60 日後）", min_value=0, max_value=120, value=0, step=5,
                                    key="intel_custom_days")
        if st.button("🔍 計算情報準確度", key="calc_intel_acc", use_container_width=True):
            with st.spinner("補算各情報的歷史報酬中..."):
                src_df, tag_df = get_intel_accuracy_summary(custom_days=_custom_d if _custom_d > 0 else None)
            if src_df.empty:
                st.info("尚無情報紀錄，或 Supabase 未連線。先去情報注入面板存幾筆情報，過幾天再回來看。")
            else:
                st.markdown("**依來源**")
                st.dataframe(src_df, use_container_width=True, hide_index=True)
                if not tag_df.empty:
                    st.markdown("**依標籤**")
                    st.dataframe(tag_df, use_container_width=True, hide_index=True)

    with pk_tab2:
        st.caption("比較「你手動加入」vs「系統查詢加入」的標的，從加入日到今天的報酬率與勝率，看誰的選股比較準。")
        if st.button("⚔️ 計算勝率PK", key="calc_pk", use_container_width=True):
            with st.spinner("比對兩種選股方式的歷史績效..."):
                pk_df = get_manual_vs_system_pk()
            if pk_df.empty:
                st.info("尚無加入紀錄，或 Supabase 未連線。之後每次加入雷達會記錄加入日，累積一段時間再回來看。")
            else:
                st.dataframe(pk_df, use_container_width=True, hide_index=True)
                st.caption("樣本數太少時參考價值有限，建議累積 1-2 週的加入紀錄再看。")

with st.expander("🧪 訊號命中率回測實驗室 (V158/V159)", expanded=False):
    bt_tab1, bt_tab2 = st.tabs(["📈 技術訊號回測", "🎯 查1~查12 完整濾網回測"])

    with bt_tab1:
        st.caption("驗證範圍：價量＋均線＋大盤位階技術訊號。不含法人籌碼／基本面成分，"
                   "無未來函數——用當天收盤產生訊號，量測 3 日／10 日後的實際報酬。")

        bt_default_pool = sorted(set(list(st.session_state.get('pinned_stocks', {}).keys())
                                     + list(st.session_state.get('portfolio', {}).keys())))
        bt_stock_input = st.text_input(
            "回測股票池（逗號分隔，預設帶入你的雷達+持倉清單）",
            value=",".join(bt_default_pool) if bt_default_pool else "2330,2303,2317",
            key="bt_stock_input"
        )
        bt_c1, bt_c2, bt_c3 = st.columns(3)
        bt_years = bt_c1.slider("回測年數", 1, 5, 2, key="bt_years")
        bt_atr_mults_raw = bt_c2.text_input("ATR倍數(可多組,逗號分隔)", value="0.5,1.0,1.5",
                                            key="bt_atr_mults", help="會分別跑一次，方便比較哪個倍數的防守線比較合理")
        bt_doomsday = bt_c3.checkbox("納入末日熔斷", value=False, key="bt_doomsday")
        bt_market_regime = st.checkbox("納入大盤20MA位階濾網", value=True, key="bt_market_regime")

        if st.button("🚀 執行回測", key="bt_run_btn", use_container_width=True):
            bt_codes = [s.strip() for s in bt_stock_input.split(',') if s.strip()]
            try:
                bt_mults = [float(x.strip()) for x in bt_atr_mults_raw.split(',') if x.strip()]
            except ValueError:
                bt_mults = [0.5]
                st.warning("ATR倍數格式有誤，改用預設值 0.5")

            if not bt_codes or not bt_mults:
                st.warning("請至少輸入一檔股票代號與一組 ATR 倍數。")
            else:
                for mult in bt_mults:
                    st.markdown(f"#### ATR 倍數 = {mult}")
                    bt_progress = st.progress(0)
                    bt_status = st.empty()

                    def _bt_progress_cb(done, total, code):
                        bt_status.caption(f"回測進度：{done}/{total}（{code}）")
                        bt_progress.progress(done / total)

                    all_rows, summary_df = run_signal_backtest(
                        bt_codes, bt_years, mult, bt_doomsday, bt_market_regime,
                        progress_callback=_bt_progress_cb
                    )
                    bt_progress.empty()
                    bt_status.empty()

                    if summary_df.empty:
                        st.warning(f"ATR={mult}：沒有產出任何有效樣本，請確認股票代號或資料區間。")
                        continue

                    st.dataframe(summary_df, use_container_width=True, hide_index=True)
                    run_id = save_backtest_run(bt_codes, bt_years, mult, bt_doomsday, bt_market_regime, all_rows)
                    st.caption(f"已寫入 SQLite（run_id={run_id}），下方「歷史回測紀錄」可隨時回顧。")

                st.markdown("""
**戰略判讀提示**
- 勝率低於50%但平均報酬為正 → 該訊號屬於「大賺小賠」型，不代表訊號不好。
- 偏多訊號的10日防守擊穿率若明顯偏高 → 代表這組ATR倍數對這批股票太緊，容易被正常洗盤掃出場，可以調高倍數再測一次比較。
- 這裡測的是技術面單獨的表現；正式版訊號還會疊加法人籌碼與地雷警告，實際勝率可能與此不同。
                """)

        st.divider()
        st.markdown("##### 📜 歷史回測紀錄")
        bt_runs_df = list_backtest_runs(mode='technical')
        if bt_runs_df.empty:
            st.caption("尚無回測紀錄。")
        else:
            st.dataframe(bt_runs_df, use_container_width=True, hide_index=True)
            bt_pick_id = st.selectbox("選一筆 run_id 回顧摘要", bt_runs_df['run_id'].tolist(), key="bt_pick_run")
            if bt_pick_id:
                bt_hist_summary = load_backtest_summary(bt_pick_id)
                # 【V160】8.1 回測完要有「所以我該怎麼做」的總結，不能只丟一張表
                _bt_advice = build_backtest_advice(bt_hist_summary)
                if not bt_hist_summary.empty:
                    st.dataframe(bt_hist_summary, use_container_width=True, hide_index=True)
                    # 【V160】8.1 表格下方直接給結論，不用自己解讀數字
                    st.markdown("#### 🧭 總結建議")
                    for _line in _bt_advice:
                        st.markdown(_line)

    with bt_tab2:
        st.caption("【V159】驗證範圍：✅ 完整點對點回測（含正確揭露時序）：查1/2/4/5/6/8/9/10/12 "
                   "｜ ⚠️ 簡化版：查11（用現在股利資料回推，非逐年精確股利） "
                   "｜ ❌ 不支援：查3（需要逐日精確EPS+估值百分位歷史，另排）、情報雷達/黃金交叉（無歷史時間戳）")

        fb_default_pool = sorted(set(list(st.session_state.get('pinned_stocks', {}).keys())
                                     + list(st.session_state.get('portfolio', {}).keys())))
        fb_stock_input = st.text_input(
            "回測股票池（逗號分隔，預設帶入你的雷達+持倉清單，樣本較少較快；可自行改成更大的清單）",
            value=",".join(fb_default_pool) if fb_default_pool else "2330,2303,2317",
            key="fb_stock_input"
        )
        fb_years = st.slider("回測年數", 1, 5, 2, key="fb_years")
        fb_available_cmds = ["查1.主升段突擊", "查2.魚頭慢伏支撐", "查4.投信作帳集團股",
                             "查5.籌碼外資霸王色", "查6.營收雙增爆發突破", "查8.昨日強勢動能延續",
                             "查9.均線糾結爆量突破", "查10.籌碼沉澱量縮潛伏",
                             "查11.除權息尋寶雷達 (簡化版)", "查12.K線型態尋寶型"]
        fb_selected = st.multiselect("要回測的濾網條件（可多選，每個會分開統計各自的命中率）",
                                     fb_available_cmds, default=["查6.營收雙增爆發突破", "查9.均線糾結爆量突破"],
                                     key="fb_selected_cmds")
        fb_k_patterns = []
        if any("查12" in c for c in fb_selected):
            fb_k_patterns = st.multiselect("查12 要測哪些K線型態", ["長紅", "紅三兵", "長黑", "黑三兵"],
                                           default=["長紅"], key="fb_k_patterns")
        fb_market_regime = st.checkbox("納入大盤20MA位階濾網（破20MA的日子不納入樣本）",
                                       value=True, key="fb_market_regime")

        if st.button("🚀 執行完整濾網回測", key="fb_run_btn", use_container_width=True):
            fb_codes = [s.strip() for s in fb_stock_input.split(',') if s.strip()]
            fb_cmds_clean = [c.replace(" (簡化版)", "") for c in fb_selected]
            if not fb_codes or not fb_cmds_clean:
                st.warning("請至少輸入一檔股票代號，並選擇至少一個濾網條件。")
            else:
                fb_progress = st.progress(0)
                fb_status = st.empty()

                def _fb_progress_cb(done, total, code):
                    fb_status.caption(f"回測進度：{done}/{total}（{code}，含法人/營收歷史API拉取，較慢屬正常）")
                    fb_progress.progress(done / total)

                fb_rows, fb_summary = run_filter_backtest(
                    fb_codes, fb_years, fb_cmds_clean, fb_k_patterns, fb_market_regime,
                    progress_callback=_fb_progress_cb
                )
                fb_progress.empty()
                fb_status.empty()

                if fb_summary.empty:
                    st.warning("沒有產出任何有效樣本，請確認股票代號、資料區間或濾網條件是否過於嚴格。")
                else:
                    st.dataframe(fb_summary, use_container_width=True, hide_index=True)
                    fb_run_id = save_filter_backtest_run(fb_codes, fb_years, fb_rows)
                    st.caption(f"已寫入 SQLite（run_id={fb_run_id}）。")
                    st.markdown("""
**戰略判讀提示**
- 樣本數太少（例如個位數）的濾網，命中率參考價值有限，建議擴大股票池或拉長年數再看一次。
- 同一個濾網在不同年數（1年 vs 3年）下命中率差異很大，代表這個條件對市況（多頭/空頭年）敏感，不是穩定訊號。
                    """)

        st.divider()
        st.markdown("##### 📜 歷史回測紀錄")
        fb_runs_df = list_backtest_runs(mode='filter')
        if fb_runs_df.empty:
            st.caption("尚無回測紀錄。")
        else:
            st.dataframe(fb_runs_df, use_container_width=True, hide_index=True)
            fb_pick_id = st.selectbox("選一筆 run_id 回顧摘要", fb_runs_df['run_id'].tolist(), key="fb_pick_run")
            if fb_pick_id:
                fb_hist_summary = load_filter_backtest_summary(fb_pick_id)
                if not fb_hist_summary.empty:
                    st.dataframe(fb_hist_summary, use_container_width=True, hide_index=True)

with st.expander("📋 情報注入面板", expanded=False):
    intel_source = st.selectbox("來源", ["股癌", "財經新聞", "法說會", "券商報告", "其他"], key="intel_source")
    intel_tag = st.text_input("標籤", key="intel_tag", placeholder="例如：財報公布、法人動向")
    intel_content = st.text_area("貼上報告內容（系統會自動偵測4碼代號，不用手打格式）", key="intel_content", height=150)

    # 【V160 B#12】自動偵測代號：抓內文所有4碼數字 + 比對已知股名，列出候選讓使用者確認
    _auto_codes = []
    if intel_content.strip():
        _digit_hits = set(re.findall(r'\b(\d{4})\b', intel_content))
        _digit_hits |= set(re.findall(r"\[標的代號:\s*(\d{4})\]", intel_content))  # 舊格式也相容
        # 名稱比對：內文出現的股名也抓出來
        for _c, _n in TW_STOCK_NAMES.items():
            if _n and _n in intel_content:
                _digit_hits.add(_c)
        _auto_codes = sorted([c for c in _digit_hits if c in TW_STOCK_NAMES])

    # 【V160 關鍵修復】總指揮官回報：整篇文章貼進去，很多偵測到的標的內文根本沒真的
    # 在講——例如文章用「U型海灣」形容線型走勢，結果因為剛好有一檔股票叫「海灣」，
    # 名稱比對就誤判成這篇在講海灣這檔股票。根因是原本的偵測邏輯太寬鬆：
    #   1. 4碼數字比對——日期(2024/07/15)、瀏覽數這類數字，只要剛好落在合法代號
    #      範圍內，就會被誤認成標的（TW股票代號涵蓋約1900檔，隨便一個4碼數字
    #      命中的機率其實不低）
    #   2. 股名比對——只要公司名稱「以子字串形式」出現在內文任何地方就算命中，
    #      但很多公司名稱本身就是常見詞彙（世界、地球、全家、安心、數字……），
    #      文章只是剛好用到這些詞，不代表真的在講那檔股票
    # 這兩個問題本質上都無法用更聰明的規則完全避免（沒有規則能區分「文章真的在講
    # 這檔股票」跟「剛好打到同名字」），所以正確做法不是把偵測做得更聰明，
    # 而是讓偵測結果變成「建議候選」，儲存前一定要你自己勾選確認——
    # 這樣任何誤判在存進實體大腦之前，你都有機會把它踢掉。
    if intel_content.strip():
        if _auto_codes:
            st.caption(f"🎯 自動偵測到 {len(_auto_codes)} 檔候選，請確認要綁定哪些"
                       f"（誤判的請取消勾選，例如文章用詞剛好跟股名撞名）：")
            _confirmed_codes = st.multiselect(
                "確認要綁定的標的", options=_auto_codes,
                default=_auto_codes,
                format_func=lambda c: f"{c}（{TW_STOCK_NAMES.get(c, '')}）",
                key="intel_confirm_codes")
        else:
            _confirmed_codes = []
            st.caption("⚠️ 內文中沒有偵測到可辨識的4碼代號或已知股名")
    else:
        _confirmed_codes = []

    if st.button("💾 儲存情報", key="intel_save_btn"):
        if intel_content.strip():
            if _confirmed_codes:
                for ticker in _confirmed_codes:
                    st.session_state.intelligence_pool.setdefault(ticker, {"sources": [], "history": []})
                    if intel_source not in st.session_state.intelligence_pool[ticker]["sources"]:
                        st.session_state.intelligence_pool[ticker]["sources"].append(intel_source)
                    st.session_state.intelligence_pool[ticker]["history"].append({
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "tag": intel_tag, "content": intel_content})
                    # 【V160 B#13】情報準確度追蹤：記錄基準價供之後算報酬
                    log_intel_performance(ticker, intel_source, intel_tag)
                save_local_db_isolated()
                st.success(f"已綁定 {len(_confirmed_codes)} 檔標的並寫入實體大腦！")
            else:
                st.warning("未勾選任何標的，無法綁定。請在上方候選清單中確認至少一檔。")
        else:
            st.warning("內容不能為空")

    # 【V160 新增】已綁定標的管理——總指揮官回報匯入錯誤需要一次移除。
    # 原本完全沒有刪除機制，只能一路疊加，錯了沒辦法收拾。
    _bound = st.session_state.get('intelligence_pool', {})
    if _bound:
        st.divider()
        st.markdown(f"**🗂️ 已綁定標的管理（目前共 {len(_bound)} 檔）**")
        _bound_list = sorted(_bound.keys())
        _to_remove = st.multiselect(
            "選擇要移除的標的（可多選）", options=_bound_list,
            format_func=lambda c: f"{c}（{TW_STOCK_NAMES.get(c, c)}）｜{len(_bound.get(c, {}).get('history', []))}則情報",
            key="intel_remove_select")
        _rm_col1, _rm_col2 = st.columns(2)
        with _rm_col1:
            if st.button("🗑️ 移除勾選的標的", key="intel_remove_btn",
                        disabled=not _to_remove, use_container_width=True):
                for c in _to_remove:
                    st.session_state.intelligence_pool.pop(c, None)
                save_local_db_isolated()
                st.success(f"已移除 {len(_to_remove)} 檔標的")
                st.rerun()
        with _rm_col2:
            if st.button("🧹 一次清空全部", key="intel_clear_all_btn", use_container_width=True):
                st.session_state['intel_clear_confirm'] = True
        if st.session_state.get('intel_clear_confirm'):
            st.warning(f"⚠️ 確定要清空全部 {len(_bound)} 檔已綁定標的嗎？這個動作無法復原。")
            _cc1, _cc2 = st.columns(2)
            with _cc1:
                if st.button("✅ 確定清空", key="intel_clear_confirm_btn", use_container_width=True):
                    st.session_state.intelligence_pool = {}
                    save_local_db_isolated()
                    st.session_state['intel_clear_confirm'] = False
                    st.success("已清空全部已綁定標的")
                    st.rerun()
            with _cc2:
                if st.button("取消", key="intel_clear_cancel_btn", use_container_width=True):
                    st.session_state['intel_clear_confirm'] = False
                    st.rerun()

def resolve_input_to_codes(raw):
    """
    【V160】把使用者輸入（可含多個代號/名稱，逗號或空白分隔）解析成股票代號清單。
    回傳 (codes, ambiguous_msgs)。ambiguous_msgs 是模糊比對到多筆時的提示。
    """
    codes, ambiguous = [], []
    tokens = re.split(r'[,\s，、]+', raw.strip())
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        digit_codes = re.findall(r'\b\d{4}\b', tok)
        if digit_codes:
            codes.extend(digit_codes)
            continue
        # 名稱精確比對
        exact = [code for code, name in TW_STOCK_NAMES.items() if name == tok]
        if exact:
            codes.append(exact[0])
            continue
        # 名稱模糊比對
        fuzzy = [code for code, name in TW_STOCK_NAMES.items() if tok in name]
        if len(fuzzy) == 1:
            codes.append(fuzzy[0])
        elif len(fuzzy) > 1:
            ambiguous.append(f"「{tok}」模糊比對到多筆：" + ', '.join(f'{m}({TW_STOCK_NAMES[m]})' for m in fuzzy[:5]))
        else:
            ambiguous.append(f"「{tok}」找不到對應代號")
    # 去重保序
    seen, uniq = set(), []
    for c in codes:
        if c not in seen:
            seen.add(c); uniq.append(c)
    return uniq, ambiguous


def _add_codes_to(target_key, codes, label):
    """把 codes 加進 target_key（pinned_stocks 或 observe_stocks），加入前驗證報價。
    【V160】新加入的股票排在最前面（看盤時新標的一眼可見）。"""
    added, failed = [], []
    for code in codes:
        hist_check, _ = get_real_stock_data_yfinance(code)
        if hist_check is None or len(hist_check) < 21:
            failed.append(code)
        else:
            added.append(code)
            log_watchlist_entry(code, "manual")   # 【V160 B#14】記錄手動加入
    if added:
        # 新加入的排最前面：新 codes 先放，再接原本的（去除重複）
        old = st.session_state.get(target_key, {})
        new_dict = {}
        for c in added:
            new_dict[c] = "手動加入"
        for c, v in old.items():
            if c not in new_dict:
                new_dict[c] = v
        st.session_state[target_key] = new_dict
        save_local_db_isolated()
        st.success(f"✅ 已加入{label}（排最前）：{', '.join(added)}")
        time.sleep(0.6)
        st.rerun()
    if failed:
        st.error(f"⚠️ 這些代號抓不到有效報價（興櫃/冷門/剛下市/資料源暫缺），已略過：{', '.join(failed)}")


search_input = st.text_input("🔍 手動股票代號/名稱輸入框（可一次多檔，用逗號分隔，如：2330,2303,聯電）", "")
_add_c1, _add_c2 = st.columns(2)
with _add_c1:
    add_observe_clicked = st.button("👁️ 加入觀察區", use_container_width=True,
                                    help="先丟著看幾天的候選，不列入長期追蹤。之後覺得可以再升級到常態雷達。")
with _add_c2:
    add_radar_clicked = st.button("🎯 直接加入常態雷達", use_container_width=True,
                                  help="確定要長期盯盤的核心標的。")

if add_observe_clicked or add_radar_clicked:
    q = search_input.strip()
    if not q:
        st.warning("請先輸入至少一個代號或名稱。")
    else:
        codes, ambiguous = resolve_input_to_codes(q)
        for msg in ambiguous:
            st.warning("⚠️ " + msg)
        if codes:
            if add_observe_clicked:
                _add_codes_to('observe_stocks', codes, "觀察區")
            else:
                _add_codes_to('pinned_stocks', codes, "常態雷達")
        elif not ambiguous:
            st.error("⚠️ 找不到任何有效代號。提示：中文名只認得證交所清單裡的股票，冷門股請直接輸入4碼代號。")


def render_action_buttons(card, code, is_portfolio, section_key='pinned_stocks'):
    btn_suffix = "_port" if is_portfolio else ("_obs" if section_key == 'observe_stocks' else "_pin")
    st.session_state.analysis_history.setdefault(code, {'nv_history': [], 'gm_history': [], 'cl_history': []})

    # 【V160】5.8 K線圖搬到卡面最外層。原本藏在「⚙️資料校正、人工覆寫與AI推演」展開區裡，
    # 總指揮官回報「找不到」——K線是最常看的東西，不該要展開兩層才點得到。
    # 展開區裡那顆保留（同一個 session_state 開關，兩邊點都同步），不影響既有習慣。
    if st.button("📈 K線圖（含MA5/20/60＋成交量＋MACD）",
                 key=f"kline_face_{code}{btn_suffix}", use_container_width=True):
        st.session_state[f'show_kline_{code}'] = not st.session_state.get(f'show_kline_{code}', False)
    if st.session_state.get(f'show_kline_{code}'):
        # 【V160 修復】render_kline_chart(symbol, hist) 需要兩個參數，
        # 先前只傳 code 導致 TypeError。跟展開區內那顆用同一套取資料方式。
        with st.spinner("繪製K線圖中..."):
            _khist_face, _ = get_real_stock_data_yfinance(code)
            render_kline_chart(code, _khist_face)

    with st.expander("🏭 同產業族群強弱（簡化版，非供應鏈圖譜）", expanded=False):
        stock_to_ind, ind_to_stocks = fetch_industry_map()
        ind = stock_to_ind.get(code)
        if not ind:
            st.caption("查無此股票的產業分類資料（FinMind TaiwanStockInfo 未提供）。")
        else:
            st.caption(f"產業分類：{ind}｜這是「同產業分類」不是真正的上下游供應鏈關聯，"
                       f"用來快速看同族群個股今日強弱、抓輪動股。")
            peers = [s for s in ind_to_stocks.get(ind, []) if s != code and s in TW_STOCK_NAMES][:15]
            peer_rows = []
            for p in peers:
                hp, _ = get_real_stock_data_yfinance(p)
                if hp is not None and len(hp) >= 2:
                    _pc = float(hp['Close'].iloc[-1])
                    pg = (_pc - float(hp['Close'].iloc[-2])) / float(hp['Close'].iloc[-2]) * 100
                    peer_rows.append({'代號': p, '名稱': TW_STOCK_NAMES.get(p, p),
                                      '現價': round(_pc, 2), '漲跌%': round(pg, 2)})
            if peer_rows:
                peer_df = pd.DataFrame(peer_rows).sort_values('漲跌%', ascending=False).reset_index(drop=True)
                st.dataframe(peer_df, use_container_width=True, hide_index=True)
            else:
                st.caption("同產業標的目前沒有可用的即時資料。")

    with st.expander("⚙️ 資料校正、人工覆寫與 AI 推演", expanded=False):
        if st.button("🚀 執行單檔精準同步 (籌碼+融資+大戶)", key=f"btn_sync_single_{code}{btn_suffix}",
                     use_container_width=True):
            with st.spinner(f"正在獨立同步 {code} 最新籌碼..."):
                success, msg = sync_single_stock_finmind(code)
                if success:
                    st.success(f"✅ {code} {msg}！")
                    # 【V160】同步後自動重整，免得還要手動按重新整理才看到最新資料
                    st.rerun()
                else:
                    st.warning(f"⚠️ {code} {msg}")
                time.sleep(1.5)
                st.rerun()

        # 【V160 延伸2 校正機制】總指揮官提出的構想：把「猜測」變成「有已知誤差範圍的估計」
        st.markdown("<div style='font-size:13px; font-weight:bold; color:#f1c40f; margin-top:10px;'>"
                    "📐 主力成本校正（輸入籌碼K線前五大券商買均價，系統自動取平均並比較誰更準）</div>",
                    unsafe_allow_html=True)
        _mf = card.get('mf_cost') or {}
        _our_est = _mf.get('heavy_vwap') or _mf.get('vwap20')
        if _our_est:
            st.caption(f"我們的估計（爆量均價優先，其次VWAP20）：**{_our_est}** 元。"
                       f"到籌碼K線「買方Top15」查前五大券商的買均價，連同券商名稱一起填進來，"
                       f"系統會自動算五家均值、記錄每家的誤差，累積後還能比較「哪家券商的數字"
                       f"跟我們的估計比較一致」。")
            st.caption("⚠️ 誠實說明：這比較的是「哪家券商數字比較貼近我們的估計」，"
                      "不是絕對客觀的準確度——我們沒有標準答案可以核對，只能互相參照。")

            # 【V160】3組擴為5組——同一檔股票的前五大買方，不是全台前五大券商
            # （後者對特定股票不見得相關，見說明文字）。5家平均能再降低雜訊，
            # 邊際效益超過5家後遞減，所以停在5不繼續往上加。
            _b_cols = st.columns(5)
            _brokers = []
            for _i in range(5):
                with _b_cols[_i]:
                    # 【V160 新增】券商名稱改用下拉選單，避免手打錯字（總指揮官回報的需求）。
                    # 清單外的分點選「其他（手動輸入）」，下面會多跳出一個輸入框，
                    # 不會因為不在清單裡就選不了。
                    _bpick = st.selectbox(f"券商{_i+1}", ["（未選擇）"] + COMMON_BROKER_BRANCHES
                                          + ["✏️ 其他（手動輸入）"],
                                          key=f"cal_bpick_{_i}_{code}{btn_suffix}")
                    if _bpick == "✏️ 其他（手動輸入）":
                        _bname = st.text_input("輸入券商/分點名稱", key=f"cal_bname_{_i}_{code}{btn_suffix}",
                                               placeholder="例如 凱基-台中")
                    elif _bpick == "（未選擇）":
                        _bname = ""
                    else:
                        _bname = _bpick
                    _bprice = st.number_input(f"買均價", min_value=0.0, step=0.1, format="%.2f",
                                              key=f"cal_bprice_{_i}_{code}{btn_suffix}")
                    if _bname.strip() and _bprice > 0:
                        _brokers.append((_bname.strip(), _bprice))

            if st.button("💾 記錄校正（自動算均值＋逐家分開記錄）",
                         key=f"cal_save_{code}{btn_suffix}", use_container_width=True):
                if len(_brokers) >= 1:
                    _avg = round(sum(p for _, p in _brokers) / len(_brokers), 2)
                    _ok_all = True
                    for _bname, _bprice in _brokers:
                        _ok_all = sb_log_cost_calibration(
                            code, _our_est, _bprice, "券商個別", _bname) and _ok_all
                    _ok_all = sb_log_cost_calibration(
                        code, _our_est, _avg, "五家均值", "五家均值") and _ok_all
                    if _ok_all:
                        _err = (_our_est - _avg) / _avg * 100 if _avg else 0
                        st.success(f"✅ 已記錄 {len(_brokers)} 家券商＋均值：我們 {_our_est} "
                                  f"vs 均值 {_avg}，誤差 {_err:+.1f}%")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.warning("部分寫入失敗（Supabase 未連線？或尚未執行 supabase_migration_extensions.sql "
                                  "新增 broker_name 欄位）")
                else:
                    st.warning("請至少填一組「券商名稱＋買均價」。")

            _cal_rows = sb_get_cost_calibration(code)
            _cal_sum = summarize_calibration(_cal_rows)
            if _cal_sum:
                st.caption(f"📊 這檔已校正 {_cal_sum['count']} 筆｜平均絕對誤差 "
                           f"**{_cal_sum['mean_abs_err']}%**｜誤差≤10%的比例 "
                           f"{_cal_sum['within_10pct']}%｜{_cal_sum['bias']}")
                _by_broker = summarize_calibration_by_broker(_cal_rows)
                if len(_by_broker) > 1:
                    st.markdown("**券商準確度排行（越前面跟我們的估計越接近）**")
                    st.dataframe(pd.DataFrame([
                        {'券商': b, '筆數': s['count'], '平均絕對誤差%': s['mean_abs_err'],
                         '誤差≤10%比例': s['within_10pct'], '偏差方向': s['bias']}
                        for b, s in _by_broker.items()
                    ]), use_container_width=True, hide_index=True)
        else:
            st.caption("目前這檔的主力成本估計不可用（股價資料不足），無法校正。")

        # 【V160 新增】深度財報分析（毛利率/ROE/現金流品質），按需查詢不進批次掃描
        st.markdown("<div style='font-size:13px; font-weight:bold; color:#00c853; margin-top:10px;'>"
                    "📊 深度財報分析（毛利率／ROE／現金流品質）</div>", unsafe_allow_html=True)
        st.caption("這三個指標定位是「30秒判斷要不要繼續看」的快篩，不是要取代財報狗的完整"
                   "多年度趨勢分析——真的要做投資決策，仍建議去財報狗查完整資料再確認。")
        if st.button("📊 查詢深度財報", key=f"fin_health_btn_{code}{btn_suffix}",
                     use_container_width=True):
            with st.spinner("查詢綜合損益表／資產負債表／現金流量表中..."):
                _fh = fetch_financial_health_cached(code, get_active_fm_token())
            st.session_state[f'fin_health_{code}'] = _fh

        _fh = st.session_state.get(f'fin_health_{code}')
        if _fh:
            _fh_c1, _fh_c2, _fh_c3 = st.columns(3)
            _fh_c1.metric("毛利率", f"{_fh['gross_margin']}%" if _fh['gross_margin'] is not None else "—")
            _fh_c2.metric("ROE(年化估計)", f"{_fh['roe']}%" if _fh['roe'] is not None else "—")
            _fh_c3.metric("營業現金流/淨利", f"{_fh['cash_quality']}x" if _fh['cash_quality'] is not None else "—")
            if _fh.get('quarter_date'):
                st.caption(f"資料季度：{_fh['quarter_date']}")
            if _fh.get('cash_quality_note'):
                st.caption(_fh['cash_quality_note'])
        elif f'fin_health_{code}' in st.session_state:
            st.caption("查無財報資料（可能是興櫃股或資料尚未公佈）。")

        st.markdown("<div style='font-size:13px; font-weight:bold; color:#00d2ff; margin-top:10px;'>✏️ 人工覆寫 (7日後自動過期恢復)</div>",
                    unsafe_allow_html=True)
        m_cols = st.columns([1, 1, 1])
        m_month = m_cols[0].text_input("月份", value="06月", key=f"my_mo_{code}{btn_suffix}")
        _cur_yoy = card.get('rev_yoy')
        m_y = m_cols[1].number_input("營收年增(%)", -100.0, 1000.0,
                                     float(_cur_yoy) if _cur_yoy is not None else 0.0, 0.1,
                                     key=f"my_y_{code}{btn_suffix}")

        b_cols = st.columns([2, 1])
        _cur_bh = card.get('big_holder')
        b_ratio = b_cols[0].number_input("大戶比例(%)", 0.0, 100.0,
                                         float(_cur_bh) if isinstance(_cur_bh, (int, float)) else 0.0, 0.1,
                                         key=f"my_bh_{code}{btn_suffix}")
        b_date = b_cols[1].text_input("大戶日期", value=datetime.now().strftime("%m/%d"),
                                      key=f"my_b_date_{code}{btn_suffix}")

        b1, b2 = st.columns(2)
        if b1.button("✅ 寫入覆寫", key=f"btn_override_{code}{btn_suffix}", use_container_width=True):
            now_ts = datetime.now().timestamp()
            st.session_state.revenue_override[code] = {
                'yoy': m_y, 'mom': card.get('rev_mom') if card.get('rev_mom') is not None else 0.0,
                'month': m_month, 'ts': now_ts}
            if b_ratio > 0:
                st.session_state.bigholder_override[code] = {'ratio': b_ratio, 'date': b_date, 'ts': now_ts}
                safe_upsert_big_holder(code, f"{datetime.now().year}-{b_date.replace('/', '-')}", b_ratio)
            save_local_db_isolated()
            st.success("資料鎖定成功！")
            time.sleep(0.5)
            st.rerun()
        if b2.button("🗑️ 解除鎖定", key=f"btn_clear_ov_{code}{btn_suffix}", use_container_width=True):
            st.session_state.revenue_override.pop(code, None)
            st.session_state.bigholder_override.pop(code, None)
            save_local_db_isolated()
            st.success("已解除人工資料，恢復 API 模式！")
            time.sleep(0.5)
            st.rerun()

        if st.button("🤖 解鎖 NVIDIA 戰略推演", key=f"ai_single_{code}{btn_suffix}", use_container_width=True):
            st.session_state.single_ai_trigger = code
            with st.spinner("NVIDIA 輪替陣列推演中..."):
                rep = execute_single_stock_ai(card)
                st.session_state.single_ai_report[code] = rep
                # 【V160 修復】只有「成功的推演」才存進歷史時光膠囊。失敗訊息（模型下架/連線逾時
                # 等）不存，否則歷史區會被一堆「三個模型都無法使用」的錯誤訊息塞滿、變得雜亂。
                _is_error = ('無法使用' in rep or '模型不存在' in rep or 'Error code' in rep
                             or rep.strip().startswith('⚠️'))
                if not _is_error:
                    st.session_state.analysis_history[code]['nv_history'].append(
                        {"time": datetime.now().strftime("%Y-%m-%d %H:%M"), "report": rep})
                    save_local_db_isolated()
            st.info(rep)

        # 【V160 B#12】戰卡一鍵匯出純文字（可複製貼到外部 Gemini/Claude/NVIDIA 網頁版）
        if st.button("📋 匯出戰卡純文字（供外部AI分析）", key=f"export_txt_{code}{btn_suffix}", use_container_width=True):
            st.session_state[f'card_text_{code}'] = build_card_text_report(card)
        if st.session_state.get(f'card_text_{code}'):
            st.text_area("複製以下全文，貼到外部AI分析：", value=st.session_state[f'card_text_{code}'],
                         height=200, key=f"card_text_area_{code}{btn_suffix}")

        # 【V160 新功能】互動式K線圖（純用yfinance股價，不需付費資料源）
        # 【V160】K線圖按鈕已搬到戰卡最外層（卡面最上方），這裡不再重複放一顆，
        # 兩處原本共用同一個 session_state 開關，現在只留卡面那顆入口。

    with st.expander("📥 貼上外部網頁版情報與裁決 (三方會審區)", expanded=False):
        c1, c2 = st.columns(2)
        nv_val = c1.text_area("📝 NVIDIA (DeepSeek)", height=80, key=f"nv_txt_{code}{btn_suffix}")
        gm_val = c2.text_area("📝 Gemini 分析", height=80, key=f"gm_txt_{code}{btn_suffix}")
        cl_val = st.text_area("👑 Claude 總裁決 (將存入歷史)", height=80, key=f"cl_txt_{code}{btn_suffix}")

        # 【V160 B#12】三方會審一鍵總結：把三份外部分析+原始戰卡數據，用NVIDIA整合成最終結論
        if st.button("⚖️ NVIDIA 三方會審總結", key=f"synth_{code}{btn_suffix}", use_container_width=True):
            if nv_val or gm_val or cl_val:
                with st.spinner("整合三方分析中..."):
                    _ctext = build_card_text_report(card)
                    _summary = synthesize_three_way_review(_ctext, nv_val or "（無）", gm_val or "（無）", cl_val or "（無）")
                st.session_state[f'synth_result_{code}'] = _summary
            else:
                st.warning("請至少貼上一份外部分析再產生總結。")
        if st.session_state.get(f'synth_result_{code}'):
            st.success("【三方會審總結】")
            st.info(st.session_state[f'synth_result_{code}'])

        if st.button("💾 儲存 Claude 裁決至時光膠囊", key=f"save_cl_{code}{btn_suffix}", use_container_width=True):
            if cl_val:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M")
                st.session_state.analysis_history[code]['cl_history'].append({
                    "time": ts, "report": cl_val,
                    "snapshot": f"收盤:{card.get('price'):.2f} | 外資5日:{card.get('f_5d'):.0f}張 | 爆量:{card.get('vol_ratio'):.1f}x | 價值分:{card.get('value_score')}"
                })
                if gm_val:
                    st.session_state.analysis_history[code]['gm_history'].append({"time": ts, "report": gm_val})
                save_local_db_isolated()
                st.success("✅ 已寫入時光膠囊！")
                time.sleep(0.5)
                st.rerun()
            else:
                st.warning("請先輸入 Claude 裁決報告！")

    hist_pack = st.session_state.analysis_history[code]
    if hist_pack['nv_history'] or hist_pack['cl_history'] or hist_pack['gm_history']:
        with st.expander("🗂️ 歷史時光膠囊覆盤區", expanded=False):
            # 【V160 修復】顯示時也過濾掉舊的錯誤訊息（之前版本存進去的「模型無法使用」等），
            # 讓畫面乾淨；並提供清空按鈕，讓使用者能一鍵清掉累積的雜亂紀錄。
            def _clean_hist(items):
                out = []
                for h in items:
                    r = h.get('report', '')
                    if ('無法使用' in r or '模型不存在' in r or 'Error code' in r
                            or r.strip().startswith('⚠️')):
                        continue
                    out.append(h)
                return out
            _nv = _clean_hist(hist_pack['nv_history'])
            _gm = _clean_hist(hist_pack['gm_history'])
            _cl = _clean_hist(hist_pack['cl_history'])
            if st.button("🧹 清空這檔的歷史紀錄", key=f"clear_hist_{code}{btn_suffix}"):
                st.session_state.analysis_history[code] = {'nv_history': [], 'gm_history': [], 'cl_history': []}
                save_local_db_isolated()
                st.rerun()
            h1, h2, h3 = st.tabs(["NVIDIA", "Gemini", "Claude"])
            with h1:
                if _nv:
                    for h in reversed(_nv[-5:]):
                        st.info(f"**{h['time']}**\n\n{h['report']}")
                else:
                    st.caption("尚無成功的推演紀錄。")
            with h2:
                if _gm:
                    for h in reversed(_gm[-5:]):
                        st.info(f"**{h['time']}**\n\n{h['report']}")
                else:
                    st.caption("尚無紀錄。")
            with h3:
                if _cl:
                    for h in reversed(_cl[-10:]):
                        st.success(f"**{h['time']}**\n\n{h['report']}")
                else:
                    st.caption("尚無紀錄。")

    m_cols = st.columns(2)
    if is_portfolio:
        if m_cols[0].button("從持倉移除", key=f"del_port_{code}{btn_suffix}", use_container_width=True):
            st.session_state.portfolio.pop(code, None)
            save_local_db_isolated()
            st.rerun()
    else:
        # 【V160】依所在區塊決定「移除」要從哪個清單刪（觀察區 vs 常態雷達）
        this_section = section_key or 'pinned_stocks'
        remove_label = "移出觀察區" if this_section == 'observe_stocks' else "移出雷達"
        if m_cols[0].button("轉移至持倉", key=f"mov_pin_{code}{btn_suffix}", use_container_width=True):
            st.session_state.portfolio[code] = {"entry_price": card.get('price', 0.0), "qty": 1}
            st.session_state[this_section].pop(code, None)
            save_local_db_isolated()
            st.rerun()
        if m_cols[1].button(remove_label, key=f"del_pin_{code}{btn_suffix}", use_container_width=True):
            st.session_state[this_section].pop(code, None)
            save_local_db_isolated()
            st.rerun()


# ==============================================================================
# 十一之二、清單管理區塊（V160：觀察區/常態雷達 共用，含搜尋/篩選/批次勾選刪除/快取）
# ==============================================================================
# 決策判定分類（供篩選下拉；對應 determine_signal 的五種輸出）
VERDICT_OPTIONS = ["🔥 偏多攻擊", "🟡 觀察偏多", "⚖️ 中立震盪", "⚠️ 轉弱謹慎", "🔵 偏空防守"]


def compute_cards_cached(codes, config_payload, cache_token):
    """
    算出一組 codes 的卡片，並用 session_state 快取。cache_token 改變才重算，
    否則直接用快取——這樣使用者勾選/搜尋/篩選時不會每次都重算 yfinance（避免頓）。
    回傳 {code: card_dict}（只含成功算出的）。

    【V160 修復】總指揮官回報開機/重整要等5分鐘。這裡原本重算時是序列迴圈
    （一檔算完才算下一檔），改用跟「全市場掃描」引擎完全相同、已經驗證過的
    ThreadPoolExecutor 平行處理模式——8檔同時算，理論上能把這段時間縮到
    約1/8。搭配 get_real_stock_data_yfinance 新增的 st.cache_data 快取
    （見該函式註解），這是這輪對開機速度影響最大的兩個修復。
    """
    cache = st.session_state.get('card_cache', {})
    if st.session_state.get('card_cache_token', '') == cache_token and cache:
        return {c: cache[c] for c in codes if c in cache}
    # token 變了或無快取 → 重算全部（平行處理）
    # 【V160】加上 0-100% 進度條（總指揮官要求取代 spinner）：平行處理時
    # 用 as_completed 逐一回報完成數量，所以百分比是真實進度不是估計值。
    result = {}
    ctx = get_script_run_ctx()
    _total = len(codes)
    _prog = st.progress(0.0, text=f"⚙️ 計算戰卡中 0/{_total}") if _total else None
    _done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_to_code = {executor.submit(calculate_signals_worker, code, config_payload, ctx): code
                          for code in codes}
        for future in concurrent.futures.as_completed(future_to_code):
            code = future_to_code[future]
            _done += 1
            if _prog is not None:
                _pct = _done / _total
                _prog.progress(_pct, text=f"⚙️ 計算戰卡中 {_done}/{_total}（{_pct*100:.0f}%）")
            try:
                c = future.result()
            except Exception:
                continue
            if c and not c.get('error'):
                result[code] = c
    if _prog is not None:
        _prog.empty()
    st.session_state['card_cache'] = result
    st.session_state['card_cache_token'] = cache_token
    return result


def render_list_section(section_key, title, config_payload, is_observe=False):
    """
    渲染一個清單區塊（觀察區 or 常態雷達），含控制列：
    搜尋框 + 決策判定篩選 + 批次勾選刪除。兩區共用這個函數。
    回傳這區成功算出的卡片 list（供盤中異常偵測收集）。
    """
    stocks_dict = st.session_state.get(section_key, {})
    if not stocks_dict:
        return []

    codes = list(stocks_dict.keys())
    # 快取 token：用「這區的代號集合 + 手動重整旗標」當 key，代號沒變就吃快取不重算
    cache_token = f"{section_key}:{','.join(sorted(codes))}:{st.session_state.get('last_refresh', 0)}"
    cards_map = compute_cards_cached(codes, config_payload, cache_token)

    with st.expander(title, expanded=True):
        # ---- 控制列 ----
        ctrl1, ctrl2, ctrl3 = st.columns([2, 2, 1.3])
        kw = ctrl1.text_input("🔍 搜尋", key=f"search_{section_key}", placeholder="代號或名稱",
                              label_visibility="collapsed")
        verdict_filter = ctrl2.selectbox("決策判定篩選", ["全部"] + VERDICT_OPTIONS,
                                         key=f"vfilter_{section_key}", label_visibility="collapsed")
        del_clicked = ctrl3.button("🗑️ 刪除勾選", key=f"delsel_{section_key}", use_container_width=True)

        # 【V160 新增】評分範圍篩選（跟決策判定、關鍵字搜尋三者疊加生效）
        score_range = st.slider("📊 評分範圍篩選（只顯示評分落在此區間的標的）", -10, 10, (-10, 10),
                                key=f"scorerange_{section_key}")

        # 【V160 新增】快速批次刪除：總指揮官回報逐張卡片勾選太慢（尤其標的一多，
        # 要滑過整排卡片才找得到checkbox）。改用下拉多選清單，不用捲動看卡片就能選。
        # 下面卡片旁的勾選框仍保留（習慣邊看卡片邊勾的人可以繼續用），兩者共用同一個
        # session_state 選取集合，彼此同步。
        _quick_opts = [f"{c} {TW_STOCK_NAMES.get(c, '')}" for c in codes]
        _quick_map = {f"{c} {TW_STOCK_NAMES.get(c, '')}": c for c in codes}
        with st.expander(f"⚡ 快速批次刪除（不用捲動找卡片，共 {len(codes)} 檔）", expanded=False):
            _quick_picked = st.multiselect("勾選要刪除的標的（可搜尋，可多選）",
                                           _quick_opts, key=f"quick_del_{section_key}")
            if _quick_picked and st.button(f"🗑️ 確認刪除選中的 {len(_quick_picked)} 檔",
                                           key=f"quick_del_btn_{section_key}",
                                           use_container_width=True):
                _to_del_quick = {_quick_map[k] for k in _quick_picked}
                for c in _to_del_quick:
                    st.session_state[section_key].pop(c, None)
                save_local_db_isolated()
                st.success(f"🗑️ 已刪除 {len(_to_del_quick)} 檔")
                time.sleep(0.5)
                st.rerun()

        # ---- 過濾（搜尋 + 決策判定 + 評分範圍 疊加生效）----
        kw = (kw or "").strip()
        filtered = []
        for code in codes:
            c = cards_map.get(code)
            if not c:
                continue
            if kw:
                name = TW_STOCK_NAMES.get(code, "")
                if kw not in code and kw not in name:
                    continue
            if verdict_filter != "全部" and c.get('signal_text', '') != verdict_filter:
                continue
            _sc = c.get('score', 0)
            if not (score_range[0] <= _sc <= score_range[1]):
                continue
            filtered.append(code)

        # ---- 批次刪除：收集勾選 ----
        sel_key = f"selected_{section_key}"
        if sel_key not in st.session_state:
            st.session_state[sel_key] = set()

        if del_clicked:
            to_del = set(st.session_state[sel_key])
            if to_del:
                for c in to_del:
                    st.session_state[section_key].pop(c, None)
                st.session_state[sel_key] = set()
                save_local_db_isolated()
                st.success(f"🗑️ 已刪除 {len(to_del)} 檔")
                time.sleep(0.5)
                st.rerun()
            else:
                st.warning("尚未勾選任何標的。")

        if not filtered:
            st.caption("（沒有符合搜尋/篩選條件的標的）")
            return list(cards_map.values())

        st.caption(f"顯示 {len(filtered)} / 共 {len(codes)} 檔"
                   + (f"｜勾選 {len(st.session_state[sel_key])} 檔待刪" if st.session_state[sel_key] else ""))

        # ---- 卡片渲染（雙欄）----
        cols, idx = st.columns(2), 0
        for code in filtered:
            c = cards_map[code]
            with cols[idx % 2]:
                # 右上角勾選框（批次刪除用）
                checked = st.checkbox(f"勾選刪除 {code} {TW_STOCK_NAMES.get(code, '')}",
                                      key=f"chk_{section_key}_{code}",
                                      value=(code in st.session_state[sel_key]))
                if checked:
                    st.session_state[sel_key].add(code)
                else:
                    st.session_state[sel_key].discard(code)

                st.markdown(render_stock_card_ui(c), unsafe_allow_html=True)

                # 觀察區專屬：升級到常態雷達
                if is_observe:
                    if st.button("⬆️ 升級到常態雷達", key=f"promote_{code}", use_container_width=True):
                        # 【V160 修復】保留原始來源血統；升級後排最前面
                        _orig = st.session_state.observe_stocks.get(code, "手動加入")
                        _new_pin = {code: f"{_orig}→經觀察區"}
                        for _c, _v in st.session_state.pinned_stocks.items():
                            if _c != code:
                                _new_pin[_c] = _v
                        st.session_state.pinned_stocks = _new_pin
                        st.session_state.observe_stocks.pop(code, None)
                        st.session_state[sel_key].discard(code)
                        save_local_db_isolated()
                        st.success(f"⬆️ {code} 已升級到常態雷達")
                        time.sleep(0.5)
                        st.rerun()
                render_action_buttons(c, code, False, section_key=section_key)
            idx += 1

        return list(cards_map.values())


def render_quick_overview(all_codes_with_source, config_payload):
    """
    【V160 B#11】戰情室速覽模式：把持倉/雷達/觀察區所有股票攤平成一張精簡總表，
    一眼掃完所有標的的決策判定，不用一張張滑卡片。
    all_codes_with_source: list of (code, source_label)

    【V160 關鍵修復】原本這裡是序列迴圈，而且呼叫端還會為了「盤中異常偵測」
    把同一批股票的 calculate_signals_worker 再重算一次——等於同樣的資料
    算兩遍。改成平行運算 + 回傳算好的結果給呼叫端直接重複使用，不用重算。
    回傳 {code: card_dict}（只含成功算出的），呼叫端可以直接拿來用。
    """
    codes = [code for code, _ in all_codes_with_source]
    source_map = dict(all_codes_with_source)
    results = {}
    if codes:
        _qo_ctx = get_script_run_ctx()
        _qo_prog = st.progress(0.0, text=f"⚙️ 速覽計算中 0/{len(codes)}")
        _qo_done = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(calculate_signals_worker, code, config_payload, _qo_ctx): code
                      for code in codes}
            for future in concurrent.futures.as_completed(futures):
                code = futures[future]
                _qo_done += 1
                _qo_prog.progress(_qo_done / len(codes),
                                  text=f"⚙️ 速覽計算中 {_qo_done}/{len(codes)}（{_qo_done/len(codes)*100:.0f}%）")
                try:
                    c = future.result()
                    if c and not c.get('error'):
                        results[code] = c
                except Exception:
                    continue
        _qo_prog.empty()

    rows = []
    for code, c in results.items():
        source = source_map.get(code, '')
        sig = c.get('signal_text', '')
        if '偏多攻擊' in sig: verdict = "🔥進攻"
        elif '觀察偏多' in sig: verdict = "🟡觀望"
        elif '偏空防守' in sig: verdict = "🔵撤退"
        elif '轉弱謹慎' in sig: verdict = "⚠️警戒"
        else: verdict = "⚖️中性"
        rows.append({
            '判定': verdict, '代號': code, '名稱': TW_STOCK_NAMES.get(code, code),
            '現價': round(float(c.get('price', 0) or 0), 2),
            '漲跌%': round(float(c.get('gain', 0) or 0), 2),
            # 【V160 新增】今日開/高/低，速覽模式一眼看出當日振幅與現價在區間的位置
            '開': c.get('open_today'),
            '高': c.get('high_today'),
            '低': c.get('low_today'),
            '評分': c.get('score', 0),
            # 【V160】總指揮官回報：只有外資5日不夠判斷，法人動能要看多天期才知道是
            # 「單日突襲」還是「持續買盤」。四個欄位一起看：若 5日≈10日，代表買盤集中在
            # 最近幾天（動能新鮮）；若 5日遠小於10日，代表買盤在更早之前、近期已停手。
            '外資5日': int(c.get('f_5d', 0) or 0),
            '外資10日': int(c.get('f_10d', 0) or 0),
            '投信5日': int(c.get('t_5d', 0) or 0),
            '投信10日': int(c.get('t_10d', 0) or 0),
            '爆量比': round(float(c.get('vol_ratio', 0) or 0), 1),
            '防守線': c.get('def_line', 0),
            '來源': source,
        })
    if not rows:
        st.caption("目前清單為空，或都抓不到報價。")
        return results
    df = pd.DataFrame(rows).sort_values('評分', ascending=False).reset_index(drop=True)
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption(f"共 {len(df)} 檔｜🔥進攻 {sum('進攻' in r['判定'] for r in rows)} 檔"
               f"｜🔵撤退 {sum('撤退' in r['判定'] for r in rows)} 檔｜依評分高→低排序")
    return results


_monitor_cards = []   # 【V159】收集雷達+持倉這輪算出來的卡片，供盤中異常偵測使用

# 【V160 B#11】速覽模式：開關已移到標題正下方，這裡只讀取狀態
_quick_mode = st.session_state.get('quick_overview_mode', False)

if _quick_mode:
    # 速覽：把持倉+雷達+觀察區全部攤平成一張表
    _all_codes = ([(c, "持倉") for c in st.session_state.get('portfolio', {}).keys()]
                  + [(c, "雷達") for c in st.session_state.get('pinned_stocks', {}).keys()]
                  + [(c, "觀察") for c in st.session_state.get('observe_stocks', {}).keys()])
    st.markdown("### ⚡ 戰情速覽")
    # 【V160 修復】原本這裡在 render_quick_overview 算完之後，又用序列迴圈把
    # 同一批股票重算一次給 monitor_cards 用——現在改成直接複用回傳結果，
    # 不重算，這是速覽模式「明明有平行處理過但還是慢」的另一半原因。
    _qo_results = render_quick_overview(_all_codes, config_payload)
    _monitor_cards.extend(_qo_results.values())
else:
    if st.session_state.get('portfolio', {}):
        with st.expander("💼 總指揮常態持倉模擬倉", expanded=True):
            # 【V160 關鍵修復】這裡是「開機/重整卡在只跑出1-2檔」的真正根因——
            # 持倉清單最先渲染，但原本是逐檔序列迴圈（一檔算完才算下一檔），
            # round 23 平行化了雷達/觀察區，唯獨漏掉這段，持倉檔數一多就會
            # 卡在這裡動彈不得，讓你以為後面雷達/觀察都沒在跑（其實是還沒輪到）。
            # 改用跟雷達/觀察區同一套 ThreadPoolExecutor，先平行算完全部持倉的
            # 資料，再照原本順序渲染卡片（渲染本身很快，真正慢的是抓資料）。
            _pf_items = list(st.session_state.portfolio.items())
            _pf_codes = [code for code, _ in _pf_items]
            _pf_ctx = get_script_run_ctx()
            _pf_results = {}
            if _pf_codes:
                _pf_prog = st.progress(0.0, text=f"⚙️ 計算持倉中 0/{len(_pf_codes)}")
                _pf_done = 0
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                    _pf_futures = {executor.submit(calculate_signals_worker, code, config_payload, _pf_ctx): code
                                   for code in _pf_codes}
                    for future in concurrent.futures.as_completed(_pf_futures):
                        code = _pf_futures[future]
                        _pf_done += 1
                        _pf_prog.progress(_pf_done / len(_pf_codes),
                                          text=f"⚙️ 計算持倉中 {_pf_done}/{len(_pf_codes)}（{_pf_done/len(_pf_codes)*100:.0f}%）")
                        try:
                            _pf_results[code] = future.result()
                        except Exception:
                            _pf_results[code] = None
                _pf_prog.empty()

            cols, idx = st.columns(2), 0
            for code, p_data in _pf_items:
                c = _pf_results.get(code)
                if c and not c.get('error'):
                    _monitor_cards.append(c)
                    ent_p = safe_float(p_data.get('entry_price', c.get('price')))
                    profit, roi = calc_real_profit(ent_p, float(c.get('price', 0.0)), safe_float(p_data.get('qty', 1)))
                    with cols[idx % 2]:
                        st.markdown(render_stock_card_ui(c, True, profit, roi, ent_p), unsafe_allow_html=True)
                        render_action_buttons(c, code, True)
                    idx += 1

    # 【V160】觀察區（先丟著看的候選，不列入長期追蹤）
    _obs_cards = render_list_section('observe_stocks', "👁️ 觀察區（候選標的，尚未列入長期追蹤）",
                                     config_payload, is_observe=True)
    _monitor_cards.extend(_obs_cards)

    # 【V160】常態觀測雷達（確定長期盯盤的核心清單）
    _radar_cards = render_list_section('pinned_stocks', "🎯 總指揮常態觀測雷達防線",
                                       config_payload, is_observe=False)
    _monitor_cards.extend(_radar_cards)

# 【V159】盤中異常偵測：陽春版，只在網頁內顯示，不推播
if _monitor_cards:
    _new_alerts = detect_intraday_anomalies(_monitor_cards)
    if _new_alerts:
        st.markdown(
            "<div style='background:#7a1010; border:2px solid #ff4d4d; border-radius:6px; "
            "padding:12px; margin-bottom:15px;'>"
            "<div style='background:#ff4d4d; color:#ffffff; font-weight:bold; font-size:14px; "
            "padding:4px 10px; border-radius:4px; display:inline-block; margin-bottom:8px;'>🚨 盤中異常偵測（這次輪詢新出現）</div>"
            "<div style='color:#ffffff; font-size:13px; line-height:1.8;'>"
            + "<br>".join(_new_alerts) + "</div></div>", unsafe_allow_html=True)
if st.session_state.get('anomaly_log'):
    with st.expander(f"📜 異常偵測紀錄（本次瀏覽階段，共 {len(st.session_state['anomaly_log'])} 則）", expanded=False):
        for _log_line in st.session_state['anomaly_log']:
            st.caption(_log_line)



# ------------------------------------------------------------------
# 掃描引擎
# ------------------------------------------------------------------
if st.session_state.get('trigger_scan', False):
    st.session_state.trigger_scan = False
    st.session_state.scan_results = []

    intel_pool = st.session_state.get('intelligence_pool', {})
    intel_cmds = [c for c in selected_cmds if "情報雷達：" in c or "情報黃金交叉" in c]

    if intel_cmds:
        target_pool = [c for c in intel_pool.keys() if c in TW_STOCK_NAMES] or list(intel_pool.keys())
    else:
        # 【V160】掃描池改依當日成交值排序，取「最值得看的N檔」而非「代碼最小的N檔」
        _pool_ordered, _pool_by_value = get_scan_pool_ordered()
        target_pool = _pool_ordered[:scan_pool_size]
        if not _pool_by_value:
            st.caption("ℹ️ 成交值排行暫時取不到（假日或端點異常），本次掃描池退回代碼順序。")

    results = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    ctx = get_script_run_ctx()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_to_code = {executor.submit(calculate_signals_worker, code, config_payload, ctx): code
                          for code in target_pool}
        total = max(1, len(target_pool))
        for i, future in enumerate(concurrent.futures.as_completed(future_to_code)):
            status_text.markdown(
                f"<div style='color:#00d2ff; font-size:13px; font-weight:bold;'>📡 並行高速掃描進度: "
                f"{i+1}/{total} ({int((i+1)/total*100)}%)</div>", unsafe_allow_html=True)
            progress_bar.progress((i + 1) / total)

            try:
                card = future.result()
            except Exception:
                continue
            if not card or card.get('error', False):
                continue

            code = card.get('code', '')
            c_vol = float(card.get('vol', 0) or 0)
            if c_vol < min_volume_filter:
                continue

            c_sources = set(intel_pool.get(code, {}).get('sources', []))
            _score_range = st.session_state.get('scan_score_range', (-10, 10))
            _c_score = card.get('score', 0)
            if not (_score_range[0] <= _c_score <= _score_range[1]):
                continue
            if evaluate_scan_conditions(selected_cmds, card, c_sources, selected_k_patterns):
                results.append(card)

    progress_bar.empty()
    status_text.empty()
    results.sort(key=lambda x: x.get('score', 0), reverse=True)
    st.session_state.scan_results = results
    st.session_state.scan_mode = " + ".join([cmd.split('.')[0] for cmd in selected_cmds])

if st.session_state.get('scan_results', []):
    st.markdown(f"### ⚡ 【{st.session_state.scan_mode}】交叉篩選戰果 ({len(st.session_state.scan_results)} 檔符合)")
    if st.button("➕ 批次部署並強制寫入常態追蹤雷達", use_container_width=True):
        for card in st.session_state.scan_results:
            _ccode = card.get('code', '')
            st.session_state.pinned_stocks[_ccode] = st.session_state.scan_mode
            log_watchlist_entry(_ccode, st.session_state.scan_mode)   # 【V160 B#14】記錄系統查詢加入
        save_local_db_isolated()
        st.success("✅ 成功綁定血統並永久存檔。")
        time.sleep(0.5)
        st.rerun()

    cols = st.columns(2)
    for idx, card in enumerate(st.session_state.scan_results):
        with cols[idx % 2]:
            st.markdown(render_stock_card_ui(card), unsafe_allow_html=True)

# ==============================================================================
# CHANGELOG V155 → V156
# ------------------------------------------------------------------------------
# [BUG-1] safe_float 會刪掉負號 → 證交所 CSV 的「賣超」全部被寫成「買超」。已修復。
# [BUG-2] calculate_signals_worker 內 fetch_finmind_revenue(symbol, fm_token) → token。
# [BUG-3] process_twse_csv 的自營商欄位比對會先命中「買進股數」而非「買賣超股數」。已修復。
# [BUG-4] is_first_red / is_yesterday_strong 從未被計算 → 查1、查8 永遠掃不到。已補上。
# [BUG-5] margin 從未被寫入 → 查5、查10 永遠空手而回。已加 FinMind 融資同步。
# [BUG-6] 查3、查10、情報雷達、黃金交叉沒有實作濾網。已補齊。
# [BUG-7] 子執行緒缺 ScriptRunContext → st.cache_data 在掃描時失效。已注入 ctx。
# [BUG-8] 搜尋框 matches 變數可能未定義。已初始化。
# [BUG-9] 總量列只印 vol_change_str[0]（單一個 emoji）。已改為完整字串。
# [BUG-10] NVIDIA 模型 ID 不存在，AI 推演必定失敗。已換成 NIM 上真實可用的模型。
# [BUG-11] 掃描結果卡片只顯示名稱與爆量比。已改用完整卡片渲染。
# [NEW-1] 法人連續買賣超真實成本 VWAP（外資 / 投信）。
# [NEW-2] 估價模型：PE 合理價 / 樂觀價 / 殖利率防守價 / 價值分數 / 💀 基本面地雷警告。
# [NEW-3] 大盤位階風控濾網（TWII 20MA），多方訊號強制降級。
# [NEW-4] 動態移動停利（近20高 − 1.5×ATR）+ 布林上軌。
# [NEW-5] API 錯誤透明化：[⛔ API限流] / [📭 官方未公佈] / [🔌 連線失敗]，不再用 0.0 帶過。
# ==============================================================================
# CHANGELOG V158 → V159
# ------------------------------------------------------------------------------
# [NEW-8] A項：PE百分位極端值警示（⚡ 估值遠離歷史常態）。跟💀基本面地雷警告不同，
#   不要求營收衰退或法人賣超，純粹標示「現在的估值已經遠離自己3年歷史常態」，
#   常見於重大題材重估（用聯電2026年因英特爾12奈米合作題材從PE~15重估到PE~38的
#   真實案例驗證過：pe_extreme=True 且 landmine=False，兩個標籤是獨立判斷）。
# [NEW-9] B項：查1~查12 完整濾網回測（含法人籌碼與營收），新增分頁「🎯 查1~查12
#   完整濾網回測」。核心改動：
#   - 把即時掃描裡的條件判斷邏輯抽成 evaluate_single_condition()/evaluate_scan_
#     conditions()共用函式，正式掃描跟回測都呼叫同一份規則，用3600種隨機組合驗證
#     過重構前後行為100%一致，不會兩邊寫兩份、之後改一邊忘了改另一邊。
#   - 新增 fetch_institutional_history()：抓歷史三大法人+融資融券，各1支API call
#     涵蓋整個回測區間（不是一天一call）。FinMind額度確認足夠（免費300/hr+兩組
#     註冊帳號600/hr=約1500/hr），這個顧慮已解除。
#   - 新增 fetch_revenue_history_lagged()：處理月營收「揭露延遲」，用當月最後一天
#     +10天緩衝當作「可用日」，訊號產生當下只看得到已公告的最新一期營收，避免
#     未來函數。已用單元測試驗證：6月營收在7/9查詢查不到，7/10（公告日）才查得到。
#   - SQLite schema 用 ALTER TABLE 做遷移安全升級（mode/filter_name欄位），已驗證
#     V158建出來的舊資料庫可以無痛升級，舊回測紀錄不會遺失。
#   - 範圍聲明：完整驗證 查1/2/4/5/6/8/9/10/12；查11(殖利率)用現在股利資料簡化
#     套用；查3(價值分數)因需要逐日精確EPS歷史，本輪不支援，UI上不列入可選清單；
#     情報雷達/黃金交叉無歷史時間戳，不支援回測。
# [NEW-10] C+D項：陽春版盤中自動輪詢 + 異常偵測（不推播）。部署環境確認是
#   Streamlit Cloud免費版，沒有背景執行能力，改用 streamlit-autorefresh 在網頁
#   分頁開著時定時重新整理；detect_intraday_anomalies() 比較「這次輪詢」與
#   「上次輪詢」的快照，只在指標新突破門檻（爆量比2.0x / 漲跌±5%）時才提醒，
#   已用單元測試驗證不會對同一個已觸發過的異常重複騷擾。異常只顯示在網頁頂部
#   banner，沒有Line/Telegram/Email推播（使用者選擇暫不做）。
# [NEW-11] E項：簡化版產業鏈（同產業分類）。用 FinMind TaiwanStockInfo 一次性
#   批次拉取產業分類（不是逐檔拉，成本低），在個股操作面板新增「🏭 同產業族群
#   強弱」，列出同產業其他個股今日漲跌排序。明確聲明這不是真正的上下游供應鏈
#   關聯圖譜，只是同產業分類的簡化替代方案。
# [CORRECTION] 上一輪誤判「券商分點資料需要FinMind企業版」，經查證是錯的——
#   FinMind的TaiwanStockTradingDailyReport（分點進出）、TaiwanSecuritiesTraderInfo
#   （券商代碼對照）都在免費開放資料集內，資料回溯至2001年。這輪尚未實作（F項，
#   待與總指揮官確認是否本輪一併排入），僅在此記錄修正過的正確資訊。
# ------------------------------------------------------------------------------
# CHANGELOG V157 → V158
# ------------------------------------------------------------------------------
# [NEW-7] 命中率回測實驗室（改編自總指揮官提供的獨立回測腳本）：
#   - 核心「無未來函數」骨架保留：第 i 天收盤產生訊號，量測 i+3/i+10 天後的實際報酬。
#   - 【修復】腳本原本的 is_open_high_close_low = (curr_price < open_price) 其實是
#     「單純收黑K」，跟正式版「開盤高於昨收、收盤低於今開」的開高走低定義不一致，
#     會把大量正常黑K誤判成轉弱訊號。已改用正式版定義（實測：新定義判定次數確實
#     比舊定義少，是舊定義的子集合，行為符合預期）。
#   - 大盤位階（TWII 20MA）一併納入回測，只需多抓一次大盤歷史，不增加額外API負擔。
#   - 明確排除法人籌碼與地雷警告成分（foreign_buy固定0、landmine固定False），因為
#     要驗證那塊需要對每天每檔額外拉歷史籌碼/營收 API，運算與API負荷會暴增，這裡
#     誠實標註「只測技術面」而不是假裝驗證了完整訊號。
#   - 結果寫入新增的 SQLite 表 backtest_runs / backtest_signals，永久保存，不會重開
#     網頁就砍掉重測；支援一次輸入多組 ATR 倍數比較，並可回顧歷史 run。
#   - CLI (input/print) 改寫成 Streamlit 側邊欄面板，並用 ThreadPoolExecutor 並行抓取
#     多檔歷史資料（沿用既有掃描功能的並行模式）。
# [REFACTOR-1] def_line 的 ATR 倍數改用具名常數 DEF_LINE_ATR_MULT，正式版與回測共用
#   同一個預設值，未來要調整防守線鬆緊只需要改一個地方。
# ------------------------------------------------------------------------------
# 本輪仍未處理（下一輪視需要再排）：
#   - 查1~查12 濾網本身的回測（含法人/基本面條件），需要額外大量歷史API調用。
#   - 背景排程 + 主動推播（需先完成 FastAPI 化）。
#   - 盤中籌碼/價量異常即時偵測通知。
# ==============================================================================
# CHANGELOG V156 → V157
# ------------------------------------------------------------------------------
# [FIX-1] 總量增縮列（用整日的昨量比對盤中未走完的今量）跟爆量比（有做時間校正）
#         基準不一致，導致同一張卡片一邊顯示「量縮」一邊顯示「爆量5.5x」互相矛盾。
#         現在兩者共用 get_intraday_projection() 同一套「今日推估全天量」，盤中會
#         加註「(今日累計推估至收盤，尚未定案)」；開盤剛過幾分鐘估算值不穩時另外加註
#         ⚠️ 提醒，並將 time_ratio 下限鎖在 0.05 避免除以趨近 0 的值讓數字暴衝失真。
# [FIX-2] 估價模型改用「歷史 PE 百分位」(fetch_pe_history + FinMind TaiwanStockPER)，
#         取代 V156 寫死的 PE×15/PE×20。半導體股跟傳產股的合理本益比天差地遠，套同一把
#         尺會系統性誤判；改用個股自己近3年的估值分布位置更合理，概念上等同財報狗的
#         本益比河流圖。歷史樣本不足（新股等）時會自動退回舊版固定倍數並在 UI 標註
#         「樣本不足，退回估算」，不會假裝有精確依據。同時新增便宜價(P25)欄位。
# [FIX-3] 估價模型從「一個 tooltip 講四個數字」拆成 PE／便宜價／合理價／樂觀價／
#         殖利率防守價各自獨立的 tooltip，點哪個看哪個的說明，不再混在一起。
# [FIX-4] tooltip 溢出修正：CSS 由「置中展開 (left:50%+translateX(-50%))」改為
#         「左錨定展開 (left:0)」並用 max-width:min(220px,78vw) 限制寬度、加上自動
#         換行。觸發文字靠近卡片左緣時不會再被裁掉一半、疊住下面的文字。
# [FIX-5] 「進攻參考」更名為「短線滿足價」，並在 tooltip 明講這是「價格可能達到的
#         上緣壓力參考」，不是建議買入價，避免與防守停損（真正的操作參考線）混淆。
# [NEW-6] 簡化版處置/注意股風險提示 calc_disposal_risk_proxy()：用「6個營業日累計
#         漲跌 + 成交量異常倍增」做代理指標。這不是證交所官方判定模型（官方規則涉及
#         近百項法規細節、依股價級距與上市/上櫃分別調整），UI 上明確標註「簡化版」
#         並在 tooltip 聲明非官方模型，避免使用者誤以為是精算結果。
# ------------------------------------------------------------------------------
# 本輪未處理（下一輪獨立開發）：
#   - 命中率/回測追蹤模組：把「查1~查12」濾網的歷史命中率量化出來，目前所有門檻
#     （爆量比0.6/1.5/2.0、六日累計漲跌門檻等）都還沒有被驗證過。
#   - 背景排程 + 主動推播：現行 Streamlit 單檔架構下無法背景執行，需等 FastAPI 化。
#   - 盤中籌碼/價量異常即時偵測通知。
# ==============================================================================
