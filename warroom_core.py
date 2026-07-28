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
  4. R47：warroom_v160.py 的 FinMind 多帳號輪替 + illegal-token 判斷（R46修復）
     從沒被搬進這個共用模組——system_scheduler.py 一直有自己另一份完全獨立、
     更原始的 FinMind 抓取（只取token第一組、無輪替、無illegal判斷），R46/R47
     在網頁版修好的東西對排程端一直沒有生效。這輪把整套邏輯搬進本檔案共用。

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
import threading
import time


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
# 一之二、FinMind 多帳號輪替 + 額度用量追蹤（R47 從 warroom_v160.py 搬過來共用）
# ------------------------------------------------------------------------------
# 【為什麼要搬】system_scheduler.py 原本有自己一份完全獨立、極簡的 FinMind 抓取
# （只取 token 第一組、沒有輪替、沒有「Token is illegal.」判斷），R46 在網頁版
# 修好的 illegal-token 分類、R47 修好的相關快取問題，對排程端完全沒有生效——
# 這正是本檔案開頭教訓1/2點名的那種「改一邊不會改到另一邊」問題。
# 現在兩邊都呼叫 set_finmind_tokens() 設定各自讀到的token清單（網頁版讀
# st.secrets、排程版讀os.environ，來源不同沒關係），之後都呼叫 _finmind_get()
# 取資料，同一套輪替/錯誤分類/額度追蹤邏輯只維護一份。
# ==============================================================================
class FinMindAPIError(Exception):
    def __init__(self, reason, detail=""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


_FM_TOKENS = []            # 由呼叫端(網頁版/排程版)各自呼叫 set_finmind_tokens() 設定
_FM_KEY_LOCK = threading.Lock()
_FM_KEY_INDEX = 0          # 目前用到第幾組 token
_FM_KEY_EXHAUSTED = {}     # {token: 何時被判定額度用盡的 timestamp}
_FM_COOLDOWN_SEC = 900     # 被判定用盡後，15 分鐘內不再優先使用

# 用量計數——單純的「冷卻中/可用」看不出「是不是快用完了但還沒被判定
# exhausted」，這裡用「我們自己送出過幾次請求」的滾動1小時窗口回推估計值
# （FinMind沒有官方即時查額度端點）——不論該次請求成功或失敗都算一次，
# 因為送出請求本身就會消耗當次配額，不是只有成功才算。
_FM_USAGE_LOCK = threading.Lock()
_FM_USAGE_LOG = {}         # {token或''(訪客): [這次請求的timestamp, ...]}
_FM_USAGE_WINDOW_SEC = 3600


def set_finmind_tokens(tokens):
    """
    呼叫端在啟動時呼叫一次，設定這個process要依序嘗試的token清單。
    網頁版從 st.secrets.radar_secrets.finmind_token 讀（逗號分隔）、
    排程版從 os.environ['FINMIND_TOKEN'] 讀（同樣逗號分隔——R47之前排程
    這裡有個獨立小bug：只取 split(',')[0]，等於多組token形同虛設，永遠
    只用第一組；這次一起修掉，排程也能真正輪替多組token了）。
    """
    global _FM_TOKENS
    _FM_TOKENS = [t.strip() for t in tokens if t and t.strip()]


def _fm_log_usage(cred):
    """記錄一次真正送出的FinMind請求（在_finmind_get_once每次真正打API時呼叫）。"""
    now = time.time()
    cutoff = now - _FM_USAGE_WINDOW_SEC
    with _FM_USAGE_LOCK:
        log = _FM_USAGE_LOG.setdefault(cred, [])
        log.append(now)
        _FM_USAGE_LOG[cred] = [t for t in log if t > cutoff]


def _fm_usage_status(cred):
    """回傳(這組憑證過去1小時內已送出的請求數, 最舊一筆還有幾秒過期)。"""
    now = time.time()
    cutoff = now - _FM_USAGE_WINDOW_SEC
    with _FM_USAGE_LOCK:
        log = sorted(t for t in _FM_USAGE_LOG.get(cred, []) if t > cutoff)
    count = len(log)
    expire_in = (log[0] + _FM_USAGE_WINDOW_SEC - now) if log else 0
    return count, max(0, expire_in)


def _fm_token_chain():
    """回傳這次請求要依序嘗試的憑證清單：目前索引起算的所有 token，最後補上訪客額度('')。"""
    tokens = list(_FM_TOKENS)
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
    tokens = list(_FM_TOKENS)
    if not tokens:
        return
    with _FM_KEY_LOCK:
        if token:
            _FM_KEY_EXHAUSTED[token] = time.time()
            if token in tokens:
                _FM_KEY_INDEX = (tokens.index(token) + 1) % len(tokens)


def get_fm_quota_status():
    """給側邊欄/排程log顯示用：目前用第幾組、哪些在冷卻、這一小時內各自已經打了幾次。"""
    tokens = list(_FM_TOKENS)
    with _FM_KEY_LOCK:
        idx = _FM_KEY_INDEX % max(1, len(tokens)) if tokens else 0
    now = time.time()
    rows = []
    for i, t in enumerate(tokens):
        left = _FM_COOLDOWN_SEC - (now - _FM_KEY_EXHAUSTED.get(t, 0))
        state = f"冷卻中({int(left/60)}分)" if left > 0 else "可用"
        used, expire_in = _fm_usage_status(t)
        usage_txt = f"本小時已打 {used}/600 次"
        if used >= 600:
            usage_txt += "　⚠️已達上限"
        elif expire_in > 0:
            usage_txt += f"（最舊一筆 {int(expire_in/60)} 分後過期回補）"
        rows.append(f"帳號{i + 1}：{state}｜{usage_txt}" + ("　◀ 目前使用" if i == idx else ""))
    _g_used, _g_expire = _fm_usage_status("")
    _g_txt = f"本小時已打 {_g_used}/300 次"
    if _g_used >= 300:
        _g_txt += "　⚠️已達上限"
    elif _g_expire > 0:
        _g_txt += f"（最舊一筆 {int(_g_expire/60)} 分後過期回補）"
    rows.append(f"訪客額度：最後備援｜{_g_txt}")
    rows.append("＊以上是本工具自己記錄「送出過幾次請求」回推的估計值（FinMind沒有官方即時查額度端點），"
                 "不是FinMind伺服器端的真實數字；每小時是滾動窗口，不是整點統一重置；"
                 "且process重啟（Streamlit Cloud重新部署/休眠、或GitHub Actions每次執行都是全新環境，"
                 "彼此的用量記錄也互不相通）會讓這份記錄歸零，不代表額度真的滿了。")
    return rows


def _finmind_get_once(url, params, max_retries=3, timeout=6):
    """單一憑證的請求（含重試）。憑證輪替由 _finmind_get 負責。"""
    last_reason, last_detail = "unknown", ""
    for attempt in range(max_retries):
        try:
            _fm_log_usage(params.get('token', ''))   # 不論成敗，送出就算一次額度
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
                # 先判斷「方案權限不足」，再判斷「額度用盡」。兩者都可能回
                # 200＋msg，但意義完全不同：權限不足再等也沒用。
                if ('sponsor' in _m or 'backer' in _m or 'permission' in _m
                        or 'not allow' in _m or 'upgrade' in _m or '權限' in msg):
                    raise FinMindAPIError('permission_denied', msg)
                # "Token is illegal."（token本身失效/格式錯誤/被撤銷）跟
                # permission_denied用同一個分類，因為處理方式一樣：換一組
                # 憑證可能就通了，不用在原地重試。
                if 'illegal' in _m or 'invalid' in _m:
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
    FinMind 請求入口——真正把「多帳號額度輪替」接上。呼叫端傳進來的 token
    一律忽略，由這裡依序試 token1 → token2 → ... → 訪客額度（不帶 token），
    任一組被判定額度用盡就標記冷卻並自動換下一組。
    只有「額度用盡」和「權限不足」才換下一組；「查無資料」是資料本身的問題，
    換帳號也一樣，直接回報不浪費額度。
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

# 【V160 R41 新增】社群長期追蹤的常見隔日沖分點名單。
#
# 【最後更新：2026-07-24（本輪對話查證時的網路搜尋結果）】這份名單需要定期
# 維護——分點的隔日沖活躍程度每年都會變動（例如國泰敦北過去活躍、現在較常
# 出現的是國泰敦南），總指揮官之後可以直接改這個清單，不用等重新對話。
#
# 【重要限制，務必記得】同一個分點底下有上千個客戶，出現在買超榜不代表
# 那筆交易就是隔日沖——這正是這個因子設計成「警示降級」而非「一票否決」
# 的原因。分點名稱比對用「包含」邏輯（例如買方顯示「凱基-台北忠孝」也算
# 命中「凱基-台北」），因為分點的完整名稱格式各券商不盡相同。
DAY_TRADER_BROKERS = [
    "凱基-台北", "凱基-松山", "凱基-信義", "凱基-板橋", "凱基-城中",
    "元大-土城永寧",
    "群益金鼎-大安",
    "國泰-敦南",
    "康和-永和",
    "美林", "港商野村", "台灣摩根大通",
]


def check_day_trader_alert(top_buyer_broker):
    """
    檢查買超第一名的分點是不是已知隔日沖名單裡的分點。

    top_buyer_broker：買超金額/張數最高的那個分點名稱（字串）。這個資料
    目前只能來自手動輸入的5大券商，或分點CSV上傳解析（尚未實作），
    批次全市場掃描沒有這個資料，所以這個因子目前只在你自己手動查證某檔
    股票、或未來CSV解析接上後才會真正發揮作用。

    回傳 True/False。沒有提供分點名稱時（None或空字串）回 False——
    沒有資料就不觸發警示，不會亂猜。
    """
    if not top_buyer_broker:
        return False
    return any(known in top_buyer_broker for known in DAY_TRADER_BROKERS)


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
# ==============================================================================
# 四之一、因子註冊表（R40 新架構）
# ==============================================================================
# 【V160 R40 新增】把「多因子共振評分」拆成一個個獨立、可註冊的因子函式，
# 取代原本 determine_signal 內部一長串 if/elif 全部寫死在一起的做法。
#
# 為什麼要拆：規劃中的 R41 要再加入均線糾結+爆量、法人共振、法人持續性、
# 千張大戶趨勢、營收動能、隔日沖警示等一批新因子——如果繼續往同一個函式裡
# 塞 if/elif，這個函式會變得又長又難改，每加一個新因子都要重新讀懂整段舊
# 邏輯才敢動手。拆成註冊表之後，加因子＝寫一個新函式＋一行register，
# 不用碰任何舊因子的程式碼。
#
# 【這一輪(R40)的承諾：只搬架構，不改分數】ADDITIVE_FACTORS 這份清單裡的
# 每一個因子，都是把 determine_signal 原本內部的判斷「原封不動」搬過來，
# 加分/扣分數字、觸發條件、文字說明全部逐字保留。搬完後有做過大量隨機組合
# 測試（見下方 verify_factor_registry_equivalence），確認新舊算出來的分數、
# 判定、理由清單三者完全一致，才正式讓 determine_signal 改用這套新架構。
#
# 因子函式簽名：fn(ctx: dict) -> (delta:int, reason:str|None)
# ctx 是一個字典，包含這個因子判斷需要的所有欄位——不是整張戰卡，只給
# 真正需要的欄位，避免因子函式跟戰卡的內部結構耦合。
ADDITIVE_FACTORS = []


def register_factor(name):
    """裝飾器：把因子函式加進 ADDITIVE_FACTORS 清單，同時保留函式本身可獨立測試。"""
    def _deco(fn):
        ADDITIVE_FACTORS.append((name, fn))
        return fn
    return _deco


@register_factor("ma_position")
def _factor_ma_position(ctx):
    """均線位置：站穩多頭排列 +2／僅站上5MA +1／跌破5MA -2。"""
    price, ma5, ma20 = ctx["price"], ctx["ma5"], ctx["ma20"]
    if price > ma5 > ma20:
        return 2, "站穩多頭"
    elif price > ma5:
        return 1, "站上5MA"
    elif price < ma5:
        return -2, "跌破5MA"
    return 0, None


@register_factor("foreign_buy")
def _factor_foreign_buy(ctx):
    """外資單日買賣超：買超 +1／賣超 -1。"""
    fb = ctx["foreign_buy"]
    if fb > 0:
        return 1, f"外買{fb:,.0f}"
    elif fb < 0:
        return -1, f"外賣{abs(fb):,.0f}"
    return 0, None


@register_factor("volume_ratio")
def _factor_volume_ratio(ctx):
    """量能：量縮力竭(<0.6倍) -1／爆量(>2.0倍) +1。"""
    vr = ctx["vol_ratio"]
    if vr < 0.6:
        return -1, "量縮力竭"
    elif vr > 2.0:
        return 1, "爆量"
    return 0, None


@register_factor("open_high_close_low")
def _factor_ohcl(ctx):
    """開高走低轉弱：-2。"""
    if ctx["is_ohcl"]:
        return -2, "開高走低轉弱"
    return 0, None


@register_factor("buffer_pct")
def _factor_buffer(ctx):
    """防守線緩衝不足(<1%)：-1。"""
    bp = ctx["buffer_pct"]
    if bp < 1.0:
        return -1, f"緩衝僅{bp:.1f}%"
    return 0, None


@register_factor("landmine")
def _factor_landmine(ctx):
    """基本面地雷：-2。"""
    if ctx["landmine"]:
        return -2, "💀 基本面地雷"
    return 0, None


@register_factor("ma_compression_breakout")
def _factor_compression_breakout(ctx):
    """
    【R41 新增】均線糾結+爆量突破：MA5/20/60三線糾結（(最高-最低)/最低 < 5%）
    且當日爆量(1.5~2.5倍均量)：+2，代表盤整後帶量突破，是相對乾淨的起漲訊號。
    上漲但量縮(<0.8倍均量)：-1，代表上漲沒有量能支撐，是常見的誘多假突破。

    ma60/vol_ratio 任一缺值時這個因子不觸發（None-safe），不會因為缺資料
    而誤判——這個因子需要的資料在批次掃描時本來就都算好了，缺值多半代表
    上市時間太短(不足60日均線)，這種情況下不判斷比亂猜安全。
    """
    ma5, ma20, ma60 = ctx.get("ma5"), ctx.get("ma20"), ctx.get("ma60")
    vr = ctx.get("vol_ratio")
    gain = ctx.get("gain")
    if ma5 is None or ma20 is None or ma60 is None or vr is None or gain is None:
        return 0, None
    if ma5 <= 0 or ma20 <= 0 or ma60 <= 0:
        return 0, None
    vals = [ma5, ma20, ma60]
    compression = (max(vals) - min(vals)) / min(vals) if min(vals) > 0 else 1.0
    is_compressed = compression < 0.05
    if is_compressed and 1.5 <= vr <= 2.5:
        return 2, "均線糾結+爆量突破"
    if gain > 0 and vr < 0.8:
        return -1, "上漲量縮(誘多疑慮)"
    return 0, None


@register_factor("institutional_resonance")
def _factor_institutional_resonance(ctx):
    """
    【R41 新增】法人共振：外資與投信「同一天」都是買超（土洋合作），+2。
    只看同向同買，不看賣超方向（賣超已經由 foreign_buy 這個既有因子涵蓋），
    避免同一件事被算兩次分數。
    """
    fb, tb = ctx.get("foreign_buy"), ctx.get("trust_buy")
    if fb is None or tb is None:
        return 0, None
    if fb > 0 and tb > 0:
        return 2, "法人共振(外資+投信同買)"
    return 0, None


@register_factor("institutional_persistence")
def _factor_institutional_persistence(ctx):
    """
    【R41 新增】法人持續性：外資10日買超方向跟5日一致（同向），代表不是單日
    突襲、是持續性買超，+2。跟第三戰區籌碼小結論用的是同一個判斷邏輯
    （10日同向續買/續賣），這裡沿用同一套，避免同一件事有兩套標準。

    【已知限制】這是用「5日/10日買超方向是否一致」當「連續3日買超」的代理，
    不是真正逐日檢查連續3天的買超記錄——這個資料在calculate_signals_worker
    層級目前只有5日/10日彙總值，沒有逐日明細可以精確判斷「恰好連續3天」。
    這個代理在大多數情況下能達到類似效果(持續買超 vs 單日爆量的方向感)，
    但不是規格書原本設想的精確版本，未來若要做精確版需要多抓逐日法人明細。
    """
    f5, f10 = ctx.get("foreign_buy_5d"), ctx.get("foreign_buy_10d")
    if f5 is None or f10 is None:
        return 0, None
    if f5 > 0 and f10 > 0:
        return 2, "法人持續性(10日同向續買)"
    return 0, None


@register_factor("revenue_momentum")
def _factor_revenue_momentum(ctx):
    """【R41 新增】營收動能：最新月營收 MoM>0 且 YoY>0（雙增），+1。"""
    mom, yoy = ctx.get("rev_mom"), ctx.get("rev_yoy")
    if mom is None or yoy is None:
        return 0, None
    if mom > 0 and yoy > 0:
        return 1, "營收雙增"
    return 0, None


def run_additive_factors(ctx):
    """依序執行 ADDITIVE_FACTORS 清單裡全部的因子，回傳 (總分, 理由清單)。"""
    score = 0
    reasons = []
    for _name, fn in ADDITIVE_FACTORS:
        delta, reason = fn(ctx)
        if delta:
            score += delta
        if reason:
            reasons.append(reason)
    return score, reasons


def apply_override_rules(score, reasons, market_bull, is_volume_dump, enable_doomsday, gain, buffer_pct,
                         day_trader_alert=False):
    """
    套用「一票否決／強制調整」類規則——這些不是簡單加減分，是在因子加總完成
    後，依照特定條件覆蓋或壓制總分。順序跟原本 determine_signal 完全一致：
    大盤位階降級 → 爆量下殺強制偏空 → 末日熔斷 → 隔日沖警示。

    【R41 更新】新增因子後滿分擴大到約±10，門檻同步等比例放大（起始值，
    R42會用回測資料重新校準，這裡先用這組協商過的起始值）：
    大盤破20MA時，偏多攻擊門檻從6提高到8——原本6~7分會觸發🔥的，
    現在會被壓到剛好卡在門檻之下(5分，落入🟡觀察偏多區間)，不再是舊版的
    「score>=3就砍成2」這種綁死在舊滿分±5的寫法。

    【R41 新增】隔日沖警示：買超第一名分點若命中已知隔日沖名單，扣3分
    （不是一票否決，是降級）。理由見 check_day_trader_alert 的說明——
    同一分點底下客戶眾多，不該直接判死刑，扣分讓它「比較難但不是不可能」
    衝上偏多攻擊門檻，同時新增的爆量突破+2分因子正好是隔日沖第一天的典型
    盤面特徵，這個警示是那個新因子的必要配套。
    """
    if not market_bull:
        if 6 <= score < 8:
            score = 5; reasons.append("🌧️ 大盤破20MA·降級(門檻提高至8)")

    if is_volume_dump:
        score = min(score, -3); reasons.append("🚨 爆量下殺·主力出貨")

    if enable_doomsday and (gain <= -7.0 or buffer_pct < 0):
        score = min(score, -3); reasons.append("💀 末日熔斷觸發")

    if day_trader_alert:
        score -= 3; reasons.append("⚠️ 買超第一名疑似隔日沖分點(降級)")

    return score, reasons


def classify_score(score):
    """
    把最終分數對應到判定文字/顏色。

    【R41 更新】新增因子後滿分擴大到約±10，門檻等比例放大（起始值，
    R42會用3年回測資料重新校準，這裡先用協商過的起始值，不是隨便訂的）：
    🔥偏多攻擊≥6（原3）／🟡觀察偏多≥2（原1）／
    🔵偏空防守≤-6（原-3）／⚠️轉弱謹慎≤-2（原-1）
    """
    if score >= 6:   return "🔥 偏多攻擊", "#ff4d4d"
    elif score >= 2: return "🟡 觀察偏多", "#ffab00"
    elif score <= -6: return "🔵 偏空防守", "#2979ff"
    elif score <= -2: return "⚠️ 轉弱謹慎", "#ff9100"
    else:            return "⚖️ 中立震盪", "#888"


def determine_signal(current_price, ma5, ma20, foreign_buy, vol_ratio, is_open_high_close_low,
                     buffer_pct, gain=0.0, enable_doomsday=False,
                     market_bull=True, landmine=False, is_volume_dump=False,
                     ma60=None, trust_buy=None, foreign_buy_5d=None, foreign_buy_10d=None,
                     rev_mom=None, rev_yoy=None, day_trader_alert=False):
    """
    多因子共振評分引擎（R40起改用因子註冊表架構，見上方 ADDITIVE_FACTORS；
    R41新增均線糾結+爆量/法人共振/法人持續性/營收動能四個因子+隔日沖警示）。

    R41新增的參數全部預設 None/False——呼叫端沒有提供這些資料時（例如排程端
    目前還沒有籌碼/基本面資料管線，規劃在R41的排程資料抓取一起補上前），
    對應的新因子就是「因為缺資料而不觸發」，不會報錯也不會亂猜，行為等同
    R41之前的舊版。這是刻意設計成向下相容，讓網頁版跟排程端可以分階段
    採用新因子，不用同一輪一次全部改完。

    day_trader_alert：見 check_day_trader_alert 的說明，目前只有手動查證
    某檔股票、有分點資料時才有意義（批次全市場掃描沒有分點資料）。
    """
    ctx = {"price": current_price, "ma5": ma5, "ma20": ma20, "ma60": ma60,
           "foreign_buy": foreign_buy, "trust_buy": trust_buy,
           "foreign_buy_5d": foreign_buy_5d, "foreign_buy_10d": foreign_buy_10d,
           "vol_ratio": vol_ratio, "is_ohcl": is_open_high_close_low,
           "buffer_pct": buffer_pct, "landmine": landmine, "gain": gain,
           "rev_mom": rev_mom, "rev_yoy": rev_yoy}
    score, reasons = run_additive_factors(ctx)
    score, reasons = apply_override_rules(score, reasons, market_bull, is_volume_dump,
                                          enable_doomsday, gain, buffer_pct,
                                          day_trader_alert=day_trader_alert)
    badge, color = classify_score(score)
    return badge, color, score, reasons


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
