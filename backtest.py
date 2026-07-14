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
ERR_RATE_LIMIT = "[⛔ API限流]"
ERR_NO_DATA    = "[📭 官方未公佈]"
ERR_CONN       = "[🔌 連線失敗]"

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
            sample_count INTEGER
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS backtest_signals (
            run_id INTEGER, stock TEXT, date TEXT, signal TEXT,
            future_3d_ret REAL, future_10d_ret REAL, is_breached INTEGER
        )
    ''')
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
    with DB_LOCK:
        try:
            SQLITE_CONN.execute("""
                INSERT INTO big_holder_history (code, date, percent) VALUES (?, ?, ?)
                ON CONFLICT(code, date) DO UPDATE SET percent = excluded.percent
            """, (code, date_str, percent_value))
            SQLITE_CONN.commit()
            return True
        except Exception:
            return False


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


def init_session_state():
    defaults = {
        'db_loaded': False, 'pinned_stocks': {"2303": "手動強制加入", "5871": "手動強制加入"},
        'portfolio': {}, 'revenue_override': {}, 'dividend_override': {},
        'bigholder_override': {}, 'scan_results': [], 'scan_mode': "",
        'active_key_index': 0, 'single_ai_trigger': "", 'single_ai_report': {},
        'intelligence_pool': {}, 'analysis_history': {}, 'last_refresh': time.time(),
        'last_uploaded_csv': None, 'trigger_scan': False
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
        "portfolio": st.session_state.get('portfolio', {}),
        "revenue_override": st.session_state.get('revenue_override', {}),
        "dividend_override": st.session_state.get('dividend_override', {}),
        "bigholder_override": st.session_state.get('bigholder_override', {}),
        "intelligence_pool": st.session_state.get('intelligence_pool', {}),
        "analysis_history": st.session_state.get('analysis_history', {})
    }
    safe_json_write(USER_DB_FILE, payload)


load_and_isolate_db()

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


def _finmind_get(url, params, max_retries=3, timeout=6):
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
                # FinMind 的額度用盡有時是 200 + msg，不是 429
                if 'limit' in msg.lower() or '402' in msg or 'upgrade' in msg.lower():
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


def _reason_to_label(reason):
    if reason == 'rate_limited':
        return ERR_RATE_LIMIT
    if reason in ('timeout', 'connection_error', 'http_error'):
        return ERR_CONN
    return ERR_NO_DATA


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_finmind_revenue(symbol, token, max_lookback=400):
    url = 'https://api.finmindtrade.com/api/v4/data'
    lookback = 120
    df = None
    last_err = "empty_data"
    while df is None and lookback <= max_lookback:
        start_date = (datetime.now() - timedelta(days=lookback)).strftime('%Y-%m-%d')
        params = {'dataset': 'TaiwanStockMonthRevenue', 'data_id': symbol, 'start_date': start_date}
        if token:
            params['token'] = token
        try:
            payload = _finmind_get(url, params)
            df = pd.DataFrame(payload.get('data', []))
        except FinMindAPIError as e:
            last_err = e.reason
            if last_err == 'rate_limited':
                break                       # 限流就不要再打，直接回報
            lookback *= 2

    if df is not None and not df.empty:
        df = df.sort_values('date')
        for _, row in df[::-1].iterrows():
            yoy_raw, mom_raw = row.get('revenue_YearOnYearRatio'), row.get('revenue_MonthOverMonthRatio')
            if pd.isna(yoy_raw) or pd.isna(mom_raw):
                continue
            try:
                yoy, mom = float(yoy_raw), float(mom_raw)
                m_label = f"{int(row.get('revenue_month', 0)):02d}月"
                result = {'yoy': yoy, 'mom': mom, 'month': m_label, 'stale': False, 'ok': True}
                with _LAST_GOOD_LOCK:
                    _LAST_GOOD_REVENUE[symbol] = result
                return result
            except Exception:
                continue

    with _LAST_GOOD_LOCK:
        last_good = _LAST_GOOD_REVENUE.get(symbol)
    if last_good:
        stale = dict(last_good)
        stale['stale'] = True
        return stale

    # 【任務一】不再用 0.0 混過去，明確標示失敗原因
    return {'yoy': None, 'mom': None, 'month': _reason_to_label(last_err), 'stale': False, 'ok': False}


@st.cache_data(ttl=14400, show_spinner=False)
def fetch_big_holder_with_recursion(code, token, target_date, initial_lookback=20, max_lookback=180):
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
                df = df[df['HoldingSharesLevel'] >= 15]
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


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_twse_dividends():
    divs = {}
    try:
        res = _SESSION.get("https://openapi.twse.com.tw/v1/exchangeReport/TWT48U", timeout=5)
        if res.status_code == 200:
            for item in res.json():
                c = str(item.get('股票代號', '')).strip()
                if len(c) == 4:
                    cash_div = safe_float(item.get('現金股利', 0))
                    stock_div = safe_float(item.get('盈餘轉增資配股股數', 0)) / 100
                    if stock_div <= 0:
                        stock_div = safe_float(item.get('資本公積轉增資配股股數', 0)) / 100
                    divs[c] = {'date': str(item.get('除權息日期', '')).strip(),
                               'cash': cash_div, 'stock': stock_div}
    except Exception:
        pass
    return divs


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_stock_names():
    names = {}
    for url in ["https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL",
                "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"]:
        try:
            res = _SESSION.get(url, timeout=5)
            if res.status_code == 200:
                for item in res.json():
                    c = str(item.get('Code', item.get('SecuritiesCompanyCode', ''))).strip()
                    n = str(item.get('Name', item.get('CompanyName', ''))).strip()
                    if len(c) == 4 and c.isdigit() and n:
                        names[c] = n
        except Exception:
            pass
    for k, v in {"2330": "台積電", "2303": "聯電", "2317": "鴻海", "2308": "台達電",
                 "5871": "中租-KY", "3481": "群創", "2454": "聯發科"}.items():
        names.setdefault(k, v)
    return names


TW_STOCK_NAMES = fetch_stock_names()
DIVIDEND_DB = fetch_twse_dividends()
GLOBAL_MARKET_CODES = list(TW_STOCK_NAMES.keys())


def _yf_ticker(sym):
    """新版 yfinance 對 requests.Session 有相容性問題，做雙軌降級。"""
    try:
        return yf.Ticker(sym, session=_SESSION)
    except Exception:
        return yf.Ticker(sym)


@st.cache_data(ttl=60, show_spinner=False)
def get_market_weather_real():
    try:
        tk = _yf_ticker("^TWII")
        hist = tk.history(period="10d")
        if not hist.empty and len(hist) >= 2:
            c_idx, prev_idx = float(hist['Close'].iloc[-1]), float(hist['Close'].iloc[-2])
            change_pt = round(c_idx - prev_idx, 2)
            change_pct = round((change_pt / prev_idx) * 100, 2) if prev_idx else 0.0
            arrow = "▲" if change_pt > 0 else ("▼" if change_pt < 0 else "▬")
            color = "#ff4d4d" if change_pt > 0 else ("#00c853" if change_pt < 0 else "#999")
            return f"{c_idx:,.0f} ({arrow} {abs(change_pt):,.0f}點 | {change_pct:+.2f}%)", color, change_pct
    except Exception:
        pass
    return "大盤連線中...", "#888", 0.0


@st.cache_data(ttl=300, show_spinner=False)
def get_market_regime():
    """【任務二】大盤位階風控濾網：TWII 收盤 vs 20MA。"""
    try:
        tk = _yf_ticker("^TWII")
        hist = tk.history(period="3mo")
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


@st.cache_data(ttl=120, show_spinner=False)
def get_real_stock_data_yfinance(symbol):
    for ext in [".TW", ".TWO"]:
        for use_session in (True, False):
            try:
                tk = yf.Ticker(symbol + ext, session=_SESSION) if use_session else yf.Ticker(symbol + ext)
                # auto_adjust=False → 保留實際成交價，與券商報價一致
                hist = tk.history(period="6mo", auto_adjust=False).dropna(subset=['Close'])
                hist = hist[hist['Volume'] > 0]
                if hist.empty or len(hist) <= 20:
                    continue
                hist = hist.copy()
                hist['Volume'] = hist['Volume'] / 1000.0   # 股 → 張
                try:
                    info = tk.info
                except Exception:
                    info = {}
                return hist.tail(120), info
            except Exception:
                continue
    return None, {}


# ==============================================================================
# 四、 動態技術指標與 ATR 交易邏輯
# ==============================================================================
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

    if (c0 > o0) and is_significant:
        if (c1 < o1) and c0 > o1 and o0 < c1:
            patterns.append({"text": "長紅吞噬", "class": "tag-red"})
        else:
            patterns.append({"text": "低檔長紅", "class": "tag-red"})
    if (c0 > o0) and (c1 > o1) and (c2 > o2) and (c0 > c1 > c2):
        patterns.append({"text": "紅三兵", "class": "tag-red"})
    if (c0 < o0) and is_significant:
        if (c1 > o1) and c0 < o1 and o0 > c1:
            patterns.append({"text": "長黑吞噬", "class": "tag-green"})
        else:
            patterns.append({"text": "高檔長黑", "class": "tag-green"})
    if (c0 < o0) and (c1 < o1) and (c2 < o2) and (c0 < c1 < c2):
        patterns.append({"text": "黑三兵", "class": "tag-green"})
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

    if f_5d > 0:   score += 10
    elif f_5d < 0: score -= 8

    score = int(max(0, min(100, score)))

    is_expensive = (percentile is not None and percentile >= 80) or (percentile is None and eps > 0 and pe > PE_LANDMINE)
    landmine = bool(is_expensive and (rev_yoy is not None and rev_yoy < 0) and f_5d < 0)

    return {'eps': round(eps, 2), 'pe': pe, 'pe_percentile': percentile,
            'pe_p25': pe_p25, 'pe_p50': pe_p50, 'pe_p75': pe_p75, 'pe_hist_ok': pe_hist_ok,
            'fair_price': fair_price, 'dream_price': dream_price, 'cheap_price': cheap_price,
            'def_price': def_price, 'value_score': score, 'landmine': landmine, 'div_y': round(div_y, 2)}


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
                     market_bull=True, landmine=False):
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
            div_display = (f"{div_date_str} | 息 {cash_div}元 + 權 {d_stock}元"
                           if d_stock > 0 else f"{div_date_str} | 息 {cash_div}元")
        else:
            cash_div = safe_float(info.get('dividendRate', 0.0)) if info else 0.0
            div_yield = (cash_div / curr_price) * 100 if curr_price > 0 else 0.0
            div_display = f"無日期 | 息 {cash_div}元" if cash_div > 0 else "無近期資訊"

    # ---- 估價模型（V157：優先用歷史 PE 百分位，樣本不足才退回固定倍數） ----
    pe_hist_df = fetch_pe_history(symbol, token)
    val = build_valuation(info, curr_price, rev_yoy if rev_ok else None, f_5d, cash_div, pe_hist_df)

    zones = build_trade_zones(curr_price, ma5, ma20, atr_val, hist)
    signal_text, color_border, score, reasons = determine_signal(
        curr_price, ma5, ma20, f_single, vol_ratio, is_open_high_close_low, zones['buffer_pct'],
        gain=gain, enable_doomsday=enable_doomsday,
        market_bull=market_bull, landmine=val['landmine']
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
        "bb_upper": zones['bb_upper'], "high_20": zones['high_20'],
        "rev_yoy": rev_yoy, "rev_mom": rev_mom, "rev_month": rev_month, "rev_ok": rev_ok,
        "div_display": div_display, "div_yield": div_yield, "manual_div_mode": manual_div_mode,
        "eps": val['eps'], "pe": val['pe'], "fair_price": val['fair_price'],
        "dream_price": val['dream_price'], "cheap_price": val['cheap_price'], "def_price": val['def_price'],
        "pe_percentile": val['pe_percentile'], "pe_p25": val['pe_p25'], "pe_p50": val['pe_p50'],
        "pe_p75": val['pe_p75'], "pe_hist_ok": val['pe_hist_ok'],
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
def _fmt_vwap(c, key, label, color):
    """把 VWAP 區塊壓成單行 HTML；無資料時明確顯示原因，不用 0 帶過。"""
    v = c.get(key)
    price = float(c.get('price', 0) or 0)
    tip = ("<span class='m-tooltiptext'>回推法人「連續同方向買/賣超」區間，以每日典型價(H+L+C)/3"
           "對法人張數加權，估算其真實平均成本。現價低於買超成本＝法人套牢，反彈易遇解套賣壓；"
           "現價高於買超成本＝法人有浮額獲利，拉抬意願較高。</span>")
    if not v:
        return (f"<div style='font-size:12px; color:#777;'>{label}: <span class='m-tooltip'>"
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

    k_patterns = c.get('detected_patterns', [])
    k_text = (f"{'📉' if '黑' in k_patterns[0].get('text', '') else '🔥'} {k_patterns[0].get('text')}"
              if k_patterns else "⚖️ 壓縮盤整")
    k_tags = f"<span class='k-tag'>{k_text}</span>"
    if c.get('landmine'):
        k_tags += ("<span class='m-tooltip k-tag' style='background:#5a1010; color:#ff8080;'>💀 基本面地雷警告"
                   "<span class='m-tooltiptext'>同時滿足：估值落在自身歷史最貴區間（或PE>30）、最新月營收年減、外資近5日賣超。"
                   "高估值 + 基本面轉差 + 籌碼失守，屬於典型的高處不勝寒結構。</span></span>")

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
                   f"<hr style='margin:4px 0; border-color:#666;'>{sig_tip}</span>")

    vs = int(c.get('value_score', 0))
    vs_color = "#00c853" if vs >= 60 else ("#f1c40f" if vs >= 40 else "#ff4d4d")
    tooltip_vs = ("<span class='m-tooltiptext'>戰情室綜合價值分數(0~100)：由本益比、營收年增、殖利率、"
                  "外資5日籌碼與現價/合理價位階加權而成。>=60 偏價值面有利，<40 偏貴或體質轉差。</span>")

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
        f"""<span style="font-size:13px; color:#f1c40f; white-space:nowrap;">{c.get('blood_line', '')}</span></div>""",
        f"""<div style="display:flex; justify-content:space-between; align-items:flex-end; margin:10px 0;">""",
        f"""<div style="display:flex; align-items:center;"><span style="font-size:32px; font-weight:bold; color:#ffffff;">{float(c.get('price', 0)):.2f}</span><span style="font-size:15px; color:{gain_c}; background:{gain_b}; padding:3px 8px; border-radius:4px; margin-left:10px; font-weight:bold;">{gain_v:+.2f}%</span></div>""",
        f"""<div style="font-size:14px; display:flex; align-items:center; color:#ccc;">近7日: {c.get('sparkline_html')}</div></div>""",
        f"""<div style="background:#0e1117; padding:8px; border-radius:4px; margin-bottom:10px;">""",
        f"""<div style="font-size:13px; margin-bottom:4px;">總量: {c.get('vol'):,.0f}張 | {c.get('vol_change_str')}</div>""",
        tags_html,
        f"""</div>""",

        f"""<div class="zone-box"><div class="zone-title">❤️ 第一戰區：基本、財報與估價</div>""",
        f"""<div style="font-size:13px; margin-bottom:4px;">營收 年增 <strong style="color:#ffffff;">({c.get('rev_month')})</strong>: <strong style="color:{yoy_color};">{yoy_txt}</strong> | 月增: <strong style="color:{mom_color};">{mom_txt}</strong></div>""",
        f"""<div style="font-size:13px; margin-bottom:4px;">除權息資訊: <strong style="color:#d200ff;">{c.get('div_display')} (殖利率: {float(c.get('div_yield', 0)):.1f}%)</strong></div>""",
        f"""<div style="font-size:13px; margin-bottom:4px;">{pe_html} | <span class='m-tooltip'>便宜價{tooltip_cheap}</span> <strong style="color:#00e676;">{cheap_txt}</strong> | <span class='m-tooltip'>合理價{tooltip_fair}</span> <strong style="color:#00c853;">{fair_txt}</strong> | <span class='m-tooltip'>樂觀價{tooltip_dream}</span> <strong style="color:#ff4d4d;">{dream_txt}</strong></div>""",
        f"""<div style="font-size:13px; margin-bottom:4px;"><span class='m-tooltip'>殖利率防守價{tooltip_defp}</span>: <strong style="color:#00d2ff;">{defp_txt}</strong></div>""",
        f"""<div style="font-size:13px;"><span class='m-tooltip'>戰情室價值分數{tooltip_vs}</span>: <strong style="color:{vs_color}; font-size:15px;">{vs} 分</strong></div></div>""",

        f"""<div class="zone-box"><div class="zone-title">⚔️ 第二戰區：技術、防守與移動停利</div>""",
        f"""<div style="font-size:13px; margin-bottom:4px; display:flex; justify-content:space-between;">""",
        f"""<span>5MA: <b style="color:#ffffff;">{float(c.get('ma5', 0)):.1f}</b></span><span>20MA: <b style="color:#ffffff;">{float(c.get('ma20', 0)):.1f}</b></span><span>60MA: <b style="color:#ffffff;">{float(c.get('ma60', 0)):.1f}</b></span></div>""",
        f"""<div style="font-size:13px; margin-bottom:4px; line-height:2.2;">MACD 動能: <strong style="color:{c.get('macd_color')}; margin-right:15px;">{c.get('macd_str')}</strong>{rsi_html} <span style="margin-left:15px;">{bias_html}</span></div>""",
        f"""<div style="font-size:12px; color:#aaa; margin-top:6px; border-top:1px dashed #444; padding-top:4px;">""",
        f"""<span class='m-tooltip' style='color:#ff4d4d;'>短線滿足價:<span class='m-tooltiptext'>現價加上1倍ATR，是價格「可能達到」的上緣壓力參考，用來評估波段滿足點或分批停利，不是建議買入價。真正要進場，仍應以訊號與防守線為準。</span></span> {c.get('atk_zone')} | <span class='m-tooltip' style='color:#00FF00;'>防守停損:<span class='m-tooltiptext'>MA5扣除0.5倍ATR波動緩衝，避開隨機洗盤。跌破代表短多結構破壞。</span></span> {c.get('def_line')} (緩衝 {c.get('buffer_pct')}%, <span class='m-tooltip'>ATR={float(c.get('atr_val', 0)):.2f}<span class='m-tooltiptext'>真實波動幅度，衡量近14日日均震幅。ATR越大代表洗盤越兇，停損需拉寬。</span></span>)</div>""",
        f"""<div style="font-size:12px; color:#aaa; margin-top:4px;"><span class='m-tooltip' style='color:#f1c40f;'>動態移動停利{tooltip_trail}</span>: <strong style="color:#f1c40f;">{trail_txt}</strong> ({trail_state}, 近20高 {c.get('high_20')}) | <span class='m-tooltip' style='color:#d200ff;'>布林上軌{tooltip_bb}</span>: <strong style="color:#d200ff;">{bb_txt}</strong></div></div>""",

        f"""<div class="zone-box"><div class="shadow-box"><div class="zone-title">📊 第三戰區：三大法人、真實成本與主力籌碼</div>""",
        f"""<div style="font-size:13px; margin-bottom:4px; display:flex; flex-wrap:wrap; gap:6px;"><b>[外資]</b> 單日<span style="color:#f1c40f;">({display_date}{warn_icon})</span>: <strong style="color:#ff4d4d;">{int(c.get('f_buy', 0)):+,}張 ({float(c.get('f_pct', 0)):+.2f}%)</strong> | 5日: <strong>{int(c.get('f_5d', 0)):+,}張 ({float(c.get('f_5d_pct', 0)):+.2f}%)</strong> | 10日: <strong>{int(c.get('f_10d', 0)):+,}張 ({float(c.get('f_10d_pct', 0)):+.2f}%)</strong></div>""",
        _fmt_vwap(c, 'f_vwap', '外資連續買賣超成本', '#ff4d4d'),
        f"""<div style="font-size:13px; margin:6px 0 4px 0; display:flex; flex-wrap:wrap; gap:6px;"><b>[投信]</b> 單日<span style="color:#f1c40f;">({display_date}{warn_icon})</span>: <strong style="color:#ff4d4d;">{int(c.get('t_buy', 0)):+,}張 ({float(c.get('t_pct', 0)):+.2f}%)</strong> | 5日: <strong>{int(c.get('t_5d', 0)):+,}張 ({float(c.get('t_5d_pct', 0)):+.2f}%)</strong> | 10日: <strong>{int(c.get('t_10d', 0)):+,}張 ({float(c.get('t_10d_pct', 0)):+.2f}%)</strong></div>""",
        _fmt_vwap(c, 't_vwap', '投信連續買賣超成本', '#f1c40f'),
        f"""<div style="font-size:12px; border-top:1px dashed #444; padding-top:6px; margin-top:6px; display:flex; justify-content:space-between; color:#aaa;"><span>千張大戶({c.get('big_holder_date') or ERR_NO_DATA}): <strong style="color:#00d2ff;">{bh_display}</strong></span><span>自營商: {int(c.get('d_buy', 0)):+,}張 | 融資增減: {int(c.get('margin_diff', 0)):+,}張{'' if c.get('has_margin') else ' (未同步)'}</span></div></div></div>""",

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

        try:
            params = {'dataset': 'TaiwanStockInstitutionalInvestorsBuySell',
                      'data_id': code, 'start_date': target_date}
            if token:
                params['token'] = token
            payload = _finmind_get(url, params)
            df = pd.DataFrame(payload.get('data', []))
            df['net'] = (pd.to_numeric(df['buy'], errors='coerce').fillna(0)
                         - pd.to_numeric(df['sell'], errors='coerce').fillna(0))
            piv = df.pivot_table(index='date', columns='name', values='net', aggfunc='sum')
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
                SQLITE_CONN.commit()

            parts = ["籌碼"]
            if margin_val is not None:
                parts.append("融資")
            if bh_success:
                parts.append("大戶")
            msg = f"同步完成 ({'+'.join(parts)})"
            if not bh_success:
                msg += "，⏳大戶無資料"
            return True, msg

        error_map = {'rate_limited': ERR_RATE_LIMIT, 'timeout': "⏱️ 連線逾時",
                     'connection_error': ERR_CONN, 'empty_data': ERR_NO_DATA}
        return False, error_map.get(inst_err_reason, f"❓ 同步失敗 ({inst_err_reason})")
    except Exception as e:
        return False, f"連線異常 ({e})"


# ==============================================================================
# 九、 NVIDIA NIM 引擎
# ==============================================================================
# 註：V155 寫的 deepseek-v4-pro / nemotron-3-ultra 在 NVIDIA NIM 上並不存在，
#     故 for 迴圈每次都會全數失敗。以下換成實際可用的模型 ID。
NIM_MODELS = [
    "deepseek-ai/deepseek-r1",
    "meta/llama-3.3-70b-instruct",
    "qwen/qwen2.5-72b-instruct",
]


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
    for model_id in NIM_MODELS:
        try:
            completion = client.chat.completions.create(
                model=model_id,
                messages=[{"role": "system", "content": "你是一位冷血的台灣股市操盤幕僚。所有輸出嚴格使用繁體中文，並使用台灣金融專有名詞。直擊核心。"},
                          {"role": "user", "content": prompt}],
                temperature=0.2, max_tokens=1200, timeout=30
            )
            return f"【{model_id.split('/')[-1]} 提供分析】\n\n{completion.choices[0].message.content}"
        except Exception:
            continue
    return "⚠️ NVIDIA API 全面癱瘓或限流。"


# ==============================================================================
# 九之二、命中率回測引擎 (V158 新增)
# ------------------------------------------------------------------------------
# 改編自總指揮官提供的獨立回測腳本，核心「無未來函數」骨架保留：
# 用第 i 天收盤產生訊號，量測第 i+3 / i+10 天的未來報酬，rolling 均線/ATR
# 都只用到當天為止的資料，不偷看未來。
#
# 範圍聲明（誠實告知，不假裝做了沒做的事）：
# 這裡驗證的是「價量 + 均線 + 大盤位階」這段技術面訊號的歷史命中率。
# 不含法人籌碼與基本面（landmine）成分——因為要驗證那塊，需要對每一天、每一檔
# 額外打 FinMind 歷史籌碼/營收 API，運算與 API 負荷會暴增好幾倍，這裡先不做，
# 留給下一輪如果你要擴充再加。foreign_buy 固定傳 0（中性）、landmine 固定 False。
# 大盤位階（TWII 20MA）則有納入，因為只需要多抓一次 TWII 歷史，成本很低。
# ==============================================================================
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
                enable_doomsday, use_market_regime, sample_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
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


def list_backtest_runs(limit=20):
    with DB_LOCK:
        try:
            return pd.read_sql(
                'SELECT run_id, run_time, stock_list, years, atr_multiplier, enable_doomsday, '
                'use_market_regime, sample_count FROM backtest_runs ORDER BY run_id DESC LIMIT ?',
                SQLITE_CONN, params=(limit,))
        except Exception:
            return pd.DataFrame()


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
# 十、 CSS 與 UI 側邊欄
# ==============================================================================
st.markdown("""<style>
div[data-testid="stSidebar"] { background-color: #12141a !important; border-right: 1px solid #333 !important; }
div[data-testid="stButton"] > button { background-color: #1e1e24 !important; border: 1px solid #444 !important; }
div[data-testid="stButton"] > button p { color: #00d2ff !important; font-weight: bold !important; font-size: 14px !important; }
.hud-box { background: linear-gradient(135deg, #1a1c23 0%, #0d1117 100%); border-radius: 10px; padding: 15px; border-left: 5px solid #ff4d4d; margin-bottom: 20px;}
.zone-box { background: #11141c; border: 1px solid #2c3e50; border-radius: 6px; padding: 10px; margin-bottom: 8px; color:#eeeeee;}
.zone-title { color: #00d2ff; font-weight: bold; font-size: 13px; margin-bottom: 6px; border-bottom: 1px dashed #333; padding-bottom: 3px; }
.k-tag { font-size:13px; background:#2c3e50; padding:3px 8px; border-radius:5px; color:#f1c40f; white-space: nowrap; display: inline-block; margin-left:8px; }
/* V157 修復：原本 left:50%+translateX(-50%) 置中展開，觸發文字靠近卡片左緣時
   tooltip 左半部會直接衝出邊界被裁切。改為左錨定（貼齊觸發文字左緣向右展開）
   並用 min(...) 限制最大寬度不超過視窗可視範圍，同時保留自動換行避免溢出。 */
.m-tooltip { position: relative; display: inline-block; border-bottom: 1px dotted #888; cursor: help; }
.m-tooltip .m-tooltiptext { visibility: hidden; width: max-content; max-width: min(220px, 78vw); background-color: #333; color: #fff; text-align: left; border-radius: 6px; padding: 10px; position: absolute; z-index: 999; bottom: 125%; left: 0; transform: translateX(0); opacity: 0; transition: opacity 0.3s; font-size: 12px; font-weight: normal; line-height:1.6; overflow-wrap: break-word; word-break: break-word;}
.m-tooltip:hover .m-tooltiptext { visibility: visible; opacity: 1; }
</style>""", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("<h2 style='color:#f1c40f; text-align:center;'>⚙️ 戰略控制台</h2>", unsafe_allow_html=True)
    if st.button("🔄 強制重整畫面", use_container_width=True):
        st.session_state.last_refresh = time.time()
        st.rerun()

    with st.expander("📥 [主攻] 官方 CSV 籌碼強填中樞", expanded=False):
        uploaded_csvs = st.file_uploader("拖曳證交所三大法人 CSV (T86)", type=['csv'],
                                         accept_multiple_files=True, key="csv_up_v3")
        if uploaded_csvs and st.button("🚀 批次強制解析回填至 SQLite", use_container_width=True):
            process_twse_csv(uploaded_csvs)

    with st.expander("📊 資料庫完整度與備份還原", expanded=False):
        db_days, db_details = get_db_stats()
        if db_days == 0:
            st.warning("⚠️ 目前大腦無籌碼資料")
        else:
            st.write(f"當前儲存天數共: {db_days} 天")
            with st.container(height=150):
                for detail in db_details:
                    st.caption(f"📅 {detail[0]}: 已存 {detail[1]} 檔籌碼")

        st.divider()
        st.markdown("### 💾 實體資料庫備份還原")
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
        st.markdown("### 📤 上傳備份覆蓋大腦")
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
        base_idx += 1
    if existing_sources:
        commands_list.append(f"查{base_idx}. 🏆 情報黃金交叉")

    selected_cmds = st.multiselect("🎯 戰略掃描條件 (可複選交集)", commands_list, default=[])
    selected_k_patterns = []
    if any("查12" in cmd for cmd in selected_cmds):
        with st.container(border=True):
            if st.checkbox("🔥 長紅吞噬 / 低檔長紅"): selected_k_patterns.append("長紅")
            if st.checkbox("🔥 紅三兵強勢推推"): selected_k_patterns.append("紅三兵")
            if st.checkbox("💀 長黑吞噬頂部出貨"): selected_k_patterns.append("長黑")
            if st.checkbox("💀 黑三兵弱勢跌破"): selected_k_patterns.append("黑三兵")

    if st.button("🚀 執行全市場並行高速掃描", use_container_width=True, type="primary"):
        if not selected_cmds:
            st.warning("請先選擇至少一項戰略條件。")
        else:
            st.session_state.trigger_scan = True

    with st.expander("📖 統籌戰術解密說明書", expanded=False):
        st.markdown("""<div style="font-size:13px; color:#ffffff; background:#1e1e24; padding:15px; border-radius:8px;">
        <b style='color:#f1c40f;'>🛡️ V156 戰情室濾網大公開</b><br>
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

    st.divider()
    st.markdown("<div style='font-size:12px; font-weight:bold; margin-bottom:5px;'>📡 系統連線狀態</div>",
                unsafe_allow_html=True)
    st.markdown(f"<div style='font-size:11px;'>{'🟢' if API_READY else '🔴'} NVIDIA NIM<br>"
                f"{'🟢' if FINMIND_READY else '🔴'} FinMind 線路</div>", unsafe_allow_html=True)


# ==============================================================================
# 十一、 主畫面
# ==============================================================================
st.title("🚀 54088 戰情室 V156 量化擴張版")

_regime_badge = ("<span style='color:#00c853;'>站上20MA</span>" if MARKET_REGIME['bull']
                 else "<span style='color:#ff4d4d;'>跌破20MA·多方訊號降級</span>") if MARKET_REGIME['known'] else "<span style='color:#888;'>計算中</span>"
st.markdown(f"""<div class='hud-box'>
    <div style='color:#f1c40f; font-size:16px; font-weight:bold; margin-bottom:4px;'>📊 大將軍智慧 HUD 總覽</div>
    <div style='color:#ddd; font-size:14px;'><b>大盤氣象：</b> <span style='color:{weather_color}; font-weight:bold;'>上市大盤 {weather_str}</span> | <b>位階濾網：</b> {_regime_badge}</div>
</div>""", unsafe_allow_html=True)

with st.expander("🧪 訊號命中率回測實驗室 (V158 新增)", expanded=False):
    st.caption("驗證範圍：價量＋均線＋大盤位階技術訊號。不含法人籌碼／基本面成分（原因見下方說明），"
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
    bt_runs_df = list_backtest_runs()
    if bt_runs_df.empty:
        st.caption("尚無回測紀錄。")
    else:
        st.dataframe(bt_runs_df, use_container_width=True, hide_index=True)
        bt_pick_id = st.selectbox("選一筆 run_id 回顧摘要", bt_runs_df['run_id'].tolist(), key="bt_pick_run")
        if bt_pick_id:
            bt_hist_summary = load_backtest_summary(bt_pick_id)
            if not bt_hist_summary.empty:
                st.dataframe(bt_hist_summary, use_container_width=True, hide_index=True)

with st.expander("📋 情報注入面板", expanded=False):
    intel_source = st.selectbox("來源", ["股癌", "財經新聞", "法說會", "券商報告", "其他"], key="intel_source")
    intel_tag = st.text_input("標籤", key="intel_tag", placeholder="例如：財報公布、法人動向")
    intel_content = st.text_area("貼上報告內容 (需含 [標的代號: XXXX])", key="intel_content", height=150)

    if st.button("💾 儲存情報", key="intel_save_btn"):
        if intel_content.strip():
            tickers_found = re.findall(r"\[標的代號:\s*(\d{4})\]", intel_content)
            if tickers_found:
                for ticker in tickers_found:
                    st.session_state.intelligence_pool.setdefault(ticker, {"sources": [], "history": []})
                    if intel_source not in st.session_state.intelligence_pool[ticker]["sources"]:
                        st.session_state.intelligence_pool[ticker]["sources"].append(intel_source)
                    st.session_state.intelligence_pool[ticker]["history"].append({
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "tag": intel_tag, "content": intel_content})
                save_local_db_isolated()
                st.success(f"已綁定 {len(tickers_found)} 檔標的並寫入實體大腦！")
            else:
                st.warning("未偵測到 [標的代號: XXXX]，無法綁定血統。")
        else:
            st.warning("內容不能為空")

search_input = st.text_input("🔍 手動股票代號/名稱輸入框 (如: 2330 或 聯電)", "")
if st.button("➕ 強制加入常態觀測雷達", use_container_width=True):
    q = search_input.strip()
    if q:
        found_codes = re.findall(r'\b\d{4}\b', q)
        matches = []                                   # 【修復】先初始化，避免 NameError
        if not found_codes:
            for code, name in TW_STOCK_NAMES.items():
                if name == q:
                    found_codes.append(code)
                    break
        if not found_codes:
            matches = [code for code, name in TW_STOCK_NAMES.items() if q in name]
            if len(matches) == 1:
                found_codes.append(matches[0])
            elif len(matches) > 1:
                st.warning("⚠️ 模糊偵測到多筆標的，請輸入精確代號："
                           + ', '.join([f'{m}({TW_STOCK_NAMES[m]})' for m in matches[:5]]))

        if found_codes:
            for code in found_codes:
                st.session_state.pinned_stocks[code] = "手動強制加入"
            save_local_db_isolated()
            st.rerun()
        elif not matches:
            st.error("⚠️ 找不到對應的股票代號或名稱，請重新輸入。")


def render_action_buttons(card, code, is_portfolio):
    btn_suffix = "_port" if is_portfolio else "_pin"
    st.session_state.analysis_history.setdefault(code, {'nv_history': [], 'gm_history': [], 'cl_history': []})

    with st.expander("⚙️ 資料校正、人工覆寫與 AI 推演", expanded=False):
        if st.button("🚀 執行單檔精準同步 (籌碼+融資+大戶)", key=f"btn_sync_single_{code}{btn_suffix}",
                     use_container_width=True):
            with st.spinner(f"正在獨立同步 {code} 最新籌碼..."):
                success, msg = sync_single_stock_finmind(code)
                if success:
                    st.success(f"✅ {code} {msg}！")
                else:
                    st.warning(f"⚠️ {code} {msg}")
                time.sleep(1.5)
                st.rerun()

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
                st.session_state.analysis_history[code]['nv_history'].append(
                    {"time": datetime.now().strftime("%Y-%m-%d %H:%M"), "report": rep})
                save_local_db_isolated()
            st.info(rep)

    with st.expander("📥 貼上外部網頁版情報與裁決 (三方會審區)", expanded=False):
        c1, c2 = st.columns(2)
        nv_val = c1.text_area("📝 NVIDIA (DeepSeek)", height=80, key=f"nv_txt_{code}{btn_suffix}")
        gm_val = c2.text_area("📝 Gemini 分析", height=80, key=f"gm_txt_{code}{btn_suffix}")
        cl_val = st.text_area("👑 Claude 總裁決 (將存入歷史)", height=80, key=f"cl_txt_{code}{btn_suffix}")
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
            h1, h2, h3 = st.tabs(["NVIDIA", "Gemini", "Claude"])
            with h1:
                for h in reversed(hist_pack['nv_history'][-5:]):
                    st.info(f"**{h['time']}**\n\n{h['report']}")
            with h2:
                for h in reversed(hist_pack['gm_history'][-5:]):
                    st.info(f"**{h['time']}**\n\n{h['report']}")
            with h3:
                for h in reversed(hist_pack['cl_history'][-10:]):
                    st.success(f"**{h['time']}**\n\n{h['report']}")

    m_cols = st.columns(2)
    if is_portfolio:
        if m_cols[0].button("從持倉移除", key=f"del_port_{code}{btn_suffix}", use_container_width=True):
            st.session_state.portfolio.pop(code, None)
            save_local_db_isolated()
            st.rerun()
    else:
        if m_cols[0].button("轉移至持倉", key=f"mov_pin_{code}{btn_suffix}", use_container_width=True):
            st.session_state.portfolio[code] = {"entry_price": card.get('price', 0.0), "qty": 1}
            st.session_state.pinned_stocks.pop(code, None)
            save_local_db_isolated()
            st.rerun()
        if m_cols[1].button("移出雷達", key=f"del_pin_{code}{btn_suffix}", use_container_width=True):
            st.session_state.pinned_stocks.pop(code, None)
            save_local_db_isolated()
            st.rerun()


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

if st.session_state.get('portfolio', {}):
    with st.expander("💼 總指揮常態持倉模擬倉", expanded=True):
        cols, idx = st.columns(2), 0
        for code, p_data in list(st.session_state.portfolio.items()):
            c = calculate_signals_worker(code, config_payload)
            if c and not c.get('error'):
                ent_p = safe_float(p_data.get('entry_price', c.get('price')))
                profit, roi = calc_real_profit(ent_p, float(c.get('price', 0.0)), safe_float(p_data.get('qty', 1)))
                with cols[idx % 2]:
                    st.markdown(render_stock_card_ui(c, True, profit, roi, ent_p), unsafe_allow_html=True)
                    render_action_buttons(c, code, True)
                idx += 1

if st.session_state.get('pinned_stocks', {}):
    with st.expander("🎯 總指揮常態觀測雷達防線", expanded=True):
        cols, idx = st.columns(2), 0
        for code in list(st.session_state.pinned_stocks.keys()):
            c = calculate_signals_worker(code, config_payload)
            if c and not c.get('error'):
                with cols[idx % 2]:
                    st.markdown(render_stock_card_ui(c), unsafe_allow_html=True)
                    render_action_buttons(c, code, False)
                idx += 1

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
        target_pool = GLOBAL_MARKET_CODES[:scan_pool_size]

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

            c_price = float(card.get('price', 0) or 0)
            c_ma60 = float(card.get('ma60', 0) or 0)
            c_vol_ratio = float(card.get('vol_ratio', 0) or 0)
            c_tbuy = float(card.get('t_buy', 0) or 0)
            c_fbuy = float(card.get('f_buy', 0) or 0)
            c_margin = float(card.get('margin_diff', 0) or 0)
            c_has_margin = bool(card.get('has_margin'))
            c_rev_yoy = card.get('rev_yoy')
            c_kdj = str(card.get('kdj_str', ''))
            c_sources = set(intel_pool.get(code, {}).get('sources', []))
            margin_shrink = (c_margin < 0) if c_has_margin else True   # 未同步融資者不硬性排除

            meets_all = True
            for cmd in selected_cmds:
                if "情報雷達：" in cmd:
                    src = cmd.split("情報雷達：")[-1].strip()
                    if src not in c_sources: meets_all = False
                elif "情報黃金交叉" in cmd:
                    if len(c_sources) < 2: meets_all = False
                elif "查1." in cmd:
                    if not (card.get('is_first_red') and c_vol_ratio >= 2.0 and "金叉" in c_kdj): meets_all = False
                elif "查2." in cmd:
                    if not (c_price > c_ma60 and c_vol_ratio >= 1.2): meets_all = False
                elif "查3." in cmd:
                    if not (int(card.get('value_score', 0)) >= 60 and not card.get('landmine')): meets_all = False
                elif "查4." in cmd:
                    if not (c_tbuy > 0): meets_all = False
                elif "查5." in cmd:
                    if not (c_fbuy > 0 and margin_shrink): meets_all = False
                elif "查6." in cmd:
                    if not (c_rev_yoy is not None and c_rev_yoy > 20): meets_all = False
                elif "查8." in cmd:
                    if not card.get('is_yesterday_strong'): meets_all = False
                elif "查9." in cmd:
                    if not (c_vol_ratio >= 2.0): meets_all = False
                elif "查10." in cmd:
                    if not (0 < c_vol_ratio <= 0.6 and margin_shrink): meets_all = False
                elif "查11." in cmd:
                    if not (float(card.get('div_yield', 0)) >= 4.5): meets_all = False
                elif "查12." in cmd:
                    hit = [x.get('text') for x in card.get('detected_patterns', [])]
                    if not (selected_k_patterns and any(p in t for t in hit for p in selected_k_patterns)):
                        meets_all = False
                if not meets_all:
                    break

            if meets_all:
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
            st.session_state.pinned_stocks[card.get('code', '')] = st.session_state.scan_mode
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
