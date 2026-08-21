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
import yfinance as yf
import threading
import time
import re
import io
import concurrent.futures
from datetime import datetime, timedelta, timezone

# 【R97修復，時區鐵律全面修復，見開發歷程.md】這個共用核心模組原本沒有自己的
# 時區常數，warroom_v160.py／system_scheduler.py 各自定義了一份 TAIPEI_TZ，
# 但 core.py 裡的 datetime.now(TAIPEI_TZ) 呼叫（例如 determine_active_intraday_gate()
# 的 now=None 預設值）完全沒有時區保護，兩邊的修法沒有涵蓋到共用模組本身。
# 這裡補上同一個常數，讓 core.py 也能自己時區安全，不用依賴呼叫端記得傳值。
TAIPEI_TZ = timezone(timedelta(hours=8))

# 【R95新增，設計鐵律的唯一例外】get_threshold()需要讀session_state才能讓
# 網頁版門檻覆寫生效，用try/except安全隔離，排程環境沒裝streamlit時st=None、
# 安全退回預設值，不影響排程正常運作。
try:
    import streamlit as st
except ImportError:
    st = None


# 【R60新增】共用模組版本號——warroom_v160.py匯入後檢查這個數字，版本對不上
# 就在啟動當下明講「版本不同步」並停住，不要等深藏的呼叫炸出TypeError。
# 每次幫這個共用模組加新東西，這個數字要+1。
CORE_VERSION = 103


# ==============================================================================
# 一、共用 HTTP session（跟 fetch_twse_mis_batch 一起搬過來，兩邊共用同一組
#     重試設定，不再各自建一份可能設定不一致的 session）
# ==============================================================================
GOV_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*"
}


def get_safe_session():
    """
    【R97重大修復，見開發歷程.md「連線池架構改善」章節】原本全App共用
    一個_SESSION、一組連線池設定，總指揮官實測發現：pool_maxsize調到100
    時整個速覽模式完全卡死(0/20超過3分鐘)，調到30反而秒級完成——這種
    「跨過門檻就從正常變斷崖式卡死」的表現，不像單純的漸進式流量限速，
    更可能是撞到某種硬性上限（遠端伺服器防護機制、或Streamlit Cloud
    容器本身的連線數/file descriptor上限，兩者都有可能，這個沙盒環境
    沒辦法直接驗證是哪一個）。

    不管根因是哪一個，同一個解法都有幫助：改成「每個外部網域各自獨立
    的連線池」，不要讓所有請求擠同一組連線池——這樣任何單一網域(尤其
    是被打最兇的mis.twse.com.tw)出狀況，不會拖累其他網域，也不會讓
    單一網域一次承受過大並行量觸發防護機制。

    這個做法完全不用改任何呼叫端程式碼——requests.Session本身就支援
    對不同網址前綴掛不同的HTTPAdapter，session.get(url)會自動比對最長
    符合的前綴，沒對應到的網域才會退回下面的通用設定。

    各網域池子大小依實際使用強度設定，不是隨便給同一個數字：
    - mis.twse.com.tw：戰情速覽/候選池即時報價最常打的端點，給20
    - api.finmindtrade.com：大部分籌碼/營收/PE等資料的主要來源，給20
    - api.web.finmindtrade.com：只有額度查詢(已知常失敗)會打，給5就夠
    - openapi.twse.com.tw：產業分類/重大訊息/股利等，中等頻率，給10
    - www.tpex.org.tw：上櫃相關查詢，中等頻率，給10
    - 其他沒特別列出的網域：退回通用設定，給10
    """
    session = requests.Session()
    session.headers.update(GOV_HEADERS)
    retry = Retry(
        total=3, backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )

    def _mount_pool(prefix, pool_size):
        _adapter = HTTPAdapter(max_retries=retry, pool_connections=pool_size, pool_maxsize=pool_size)
        session.mount(prefix, _adapter)

    _mount_pool("https://mis.twse.com.tw", 20)
    _mount_pool("https://api.finmindtrade.com", 20)
    _mount_pool("https://api.web.finmindtrade.com", 5)
    _mount_pool("https://openapi.twse.com.tw", 10)
    _mount_pool("https://www.tpex.org.tw", 10)
    # 通用備援設定——沒被上面任何一條前綴比對到的網域，退回這組。
    _mount_pool("https://", 10)
    _mount_pool("http://", 10)
    return session


_SESSION = get_safe_session()


# ==============================================================================
# 一之二、FinMind 多帳號輪替 + 額度用量追蹤（R47從v160搬過來共用，網頁版
# 跟排程版共用同一套輪替/錯誤分類邏輯，只維護一份）
# ------------------------------------------------------------------------------
class FinMindAPIError(Exception):
    def __init__(self, reason, detail=""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


_FM_TOKENS = []            # 由呼叫端(網頁版/排程版)各自呼叫 set_finmind_tokens() 設定

# 【R97續10新增，總指揮官要求：不要用猜的，要有明確診斷】這幾個全域計數器
# 記錄snapshot快取命中/沒命中(退回FinMind)的次數，供stage_build_intraday_pool
# 執行完印出來，一眼就能看出Stage0b/Stage2慢是不是因為snapshot沒生效、
# 還在逐檔打FinMind——不用再靠人工比對log行數猜測。呼叫端(system_scheduler.py)
# 每次執行開頭可以呼叫reset_snapshot_cache_counters()歸零，執行完讀值。
_SNAPSHOT_CACHE_STATS = {"price_value_hit": 0, "price_value_miss": 0,
                         "shares_hit": 0, "shares_miss": 0, "shares_backoff": 0,
                         "institutional_hit": 0, "institutional_miss": 0,
                         "pe_hit": 0, "pe_miss": 0,
                         "revenue_hit": 0, "revenue_miss": 0}


def reset_snapshot_cache_counters():
    for k in _SNAPSHOT_CACHE_STATS:
        _SNAPSHOT_CACHE_STATS[k] = 0


def get_snapshot_cache_counters():
    return dict(_SNAPSHOT_CACHE_STATS)

_FM_KEY_LOCK = threading.Lock()
_FM_KEY_INDEX = 0          # 目前用到第幾組 token
_FM_KEY_EXHAUSTED = {}     # {token: 何時被判定額度用盡的 timestamp}
_FM_COOLDOWN_SEC = 900     # 被判定用盡後，15 分鐘內不再優先使用

# 用量計數——FinMind沒有官方即時查額度端點，用「送出過幾次請求」的
# 滾動1小時窗口回推估計，不論成功失敗都算一次（都會消耗配額）。
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


def is_finmind_likely_exhausted():
    """
    【R97續17新增，總指揮官實測：smart_money_scan/backfill跑完後，戰情速覽
    從幾秒變4分鐘】根因：FinMind額度是整個系統共用的資源（排程端跟網頁版
    用同一組token/同一個FinMind帳號），排程端(smart_money_scan全市場1078
    檔股本查詢)短時間內burst大量請求，把額度打到見底後，_fm_token_chain()
    雖然會把「冷卻中」的token排到後面，但仍然會照樣嘗試（只是順序調整），
    不會真的跳過——網頁版這邊完全不知道剛剛排程端才用掉額度，每一檔股票
    還是傻傻照樣對FinMind走一輪重試，白白燒掉時間才退回yfinance。

    這裡給一個快速判斷：如果目前設定的token「加上訪客額度」全部都在
    冷卻中（15分鐘內曾經被判定用盡），代表這次幾乎確定會全部一樣失敗，
    直接回傳True。

    【R97續18修正，總指揮官指出「改一個地方其他地方沒改」】原本只在
    calculate_signals_worker單一呼叫點使用這個判斷，現在改成在全站
    唯一的_finmind_get()共用入口統一套用，涵蓋所有呼叫端（法人買賣超/
    融資融券/月營收/大戶持股/股價K棒...全部一次到位），不用逐一補丁、
    以後新增呼叫端也不會漏掉。

    【R97續18修正】原本只檢查_FM_TOKENS(已註冊憑證)，沒把訪客額度('')
    納入——這會導致「已註冊憑證全部冷卻中，但訪客額度其實還沒被試過/
    還沒用盡」的情況被誤判成「已知會失敗」而略過，錯失訪客額度原本
    可能成功的機會。現在把訪客額度也算進「要全部都冷卻中」的判斷組合，
    只有連訪客額度都最近失敗過，才代表這次真的已知會全部失敗。

    只在「有設定token」時才有意義判斷——完全沒token(只能猜guest額度)
    的情況，沒有歷史紀錄可以判斷，保守回傳False(照原本邏輯試一次)。
    """
    tokens = list(_FM_TOKENS)
    if not tokens:
        return False
    now = time.time()
    _all_keys = tokens + [""]
    return all(now - _FM_KEY_EXHAUSTED.get(t, 0) <= _FM_COOLDOWN_SEC for t in _all_keys)


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
    """
    標記某組 token 額度用盡，並把輪替索引推到下一組。

    【R97續18修正】原本`if token:`會讓訪客額度(空字串'')完全不會被記錄
    進_FM_KEY_EXHAUSTED——導致is_finmind_likely_exhausted()檢查訪客額度
    時永遠讀到「從沒失敗過」，誤判成訪客額度隨時可用，讓快速失敗判斷
    形同虛設(永遠不會觸發)。這裡拆成兩段：不論是不是訪客額度，只要
    被標記用盡就記錄時間；只有「這是已註冊token」時才需要推進輪替索引
    (訪客額度不在_FM_TOKENS清單裡，沒有索引可推)。
    """
    global _FM_KEY_INDEX
    tokens = list(_FM_TOKENS)
    with _FM_KEY_LOCK:
        _FM_KEY_EXHAUSTED[token] = time.time()   # 空字串(訪客)也要記錄
        if token and tokens and token in tokens:
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
    # 【R55新增】兩組帳號用量數字一模一樣，最常見原因是secrets裡兩組
    # token其實是同一個字串（複製貼上貼重複了），這裡直接檢查並提醒。
    if len(tokens) >= 2 and len(set(tokens)) < len(tokens):
        rows.append("⚠️ 偵測到有兩組（或以上）token字串完全相同——這代表你設定的其實是"
                     "同一組帳號被算成兩組，不是真的兩組獨立額度。請去Streamlit secrets"
                     "確認每組token是不是不小心貼重複了。")
    rows.append("＊以上是本工具自己記錄「送出過幾次請求」回推的估計值，"
                 "不是FinMind伺服器端的真實數字；每小時是滾動窗口，不是整點統一重置；"
                 "且process重啟（Streamlit Cloud重新部署/休眠、或GitHub Actions每次執行都是全新環境，"
                 "彼此的用量記錄也互不相通）會讓這份記錄歸零，不代表額度真的滿了。"
                 "如需真實數字，見get_fm_real_quota_status()。")
    return rows


# ==============================================================================
# 【R97新增，見開發歷程.md「NVIDIA AI推演重新設計」章節】AI戰略推演共用核心
# ------------------------------------------------------------------------------
# 原本的NVIDIA推演邏輯只在warroom_v160.py，這次要接進排程端(system_scheduler.py)
# 一起用，兩邊各寫一份是這幾輪一直在踩的「同一套邏輯分散維護」問題的翻版，
# 這次直接把prompt組裝跟平行呼叫邏輯搬進共用模組，兩邊都從這裡import，只
# 維護一份。openai套件本身不依賴streamlit，可以安全放進core.py。
#
# 這裡刻意不做「用哪個NVIDIA_API_KEY」「用哪個模型清單」這兩件事——那些
# 網頁版讀st.secrets、排程端讀os.environ，來源不一樣，由呼叫端各自準備好
# 再傳進來，這個函式只管「怎麼問AI、怎麼平行問多個模型取最快的」。
# ==============================================================================
NIM_FALLBACK_MODELS = [
    "deepseek-ai/deepseek-v3.2",
    "meta/llama-3.3-70b-instruct",
    "moonshotai/kimi-k2.5-instruct",
    "zai/glm-5.1",
    "qwen/qwen3-coder-480b",
]


def build_ai_strategy_prompt(card_data, direction='long', gate_result=None):
    """
    【R97新增，總指揮官確認：推演內容要包含系統A評分/三關判斷結果，不只
    戰卡表面數字】組裝丟給AI的prompt文字。

    card_data：戰卡資料dict，至少要有name/code/price/gain這些基本欄位；
    有的話會一併帶入score/signal_text/reasons(系統A評分結果)、
    rev_yoy/pe/value_score/landmine(基本面)、f_5d/big_holder(籌碼)、
    macd_str/def_line/trail_stop(技術面)。

    direction：'long'(多方)或'short'(空方)——決定prompt的語氣跟AI該
    側重回答的風險面向。空方版本會特別要求AI評估「軋空/反彈」風險，
    這是多方版本不需要考慮的空方特有風險（放空的下檔利潤有限、上檔
    風險理論上無限，AI推演不該用同一套多方口吻硬套在空方標的上）。

    gate_result：選填，5分K三關(查15)的判斷結果dict（overall_verdict/
    overall_label/gate1/gate2/gate3），候選池/排程自動流程產生的AI推演
    會帶這個，網頁版單檔手動推演如果查得到當日三關結果也會帶。沒有
    三關資料時（例如非交易時段、或這檔沒進候選池）就不提這段，不要
    编造一個不存在的三關結果給AI。

    回傳 (system_prompt, user_prompt) 兩個字串。
    """
    name, code = card_data.get('name', ''), card_data.get('code', '')
    price, gain = card_data.get('price', 0), card_data.get('gain', 0)

    # 系統A評分結果——這是這次新增的核心，不再只給AI看表面數字，要讓AI
    # 知道系統自己怎麼判斷這檔股票，才能在AI推演裡對系統的判斷提出補充
    # 或質疑，而不是重新算一次一樣的東西。
    score = card_data.get('score')
    signal_text = card_data.get('signal_text', '')
    reasons = card_data.get('reasons', [])
    system_a_str = (f"系統A評分:{score}分（{signal_text}），判斷依據：{' / '.join(reasons)}"
                    if score is not None else "系統A評分：無資料")

    # 三關(查15)判斷結果——只在真的有資料時才提，不編造
    gate_str = "5分K三關：今日未進候選池或非交易時段，無三關資料"
    if gate_result:
        _g1 = gate_result.get('gate1_verdict', '—')
        _g2 = gate_result.get('gate2_verdict', '—')
        _g3 = gate_result.get('gate3_verdict', '—')
        gate_str = (f"5分K三關(查15)：{gate_result.get('overall_verdict', '—')}"
                   f"（{gate_result.get('overall_label', '')}），"
                   f"第一關{_g1}／第二關{_g2}／第三關{_g3}")

    bh = card_data.get('big_holder', 0)
    bh_str = f"{bh}%" if isinstance(bh, (int, float)) else str(bh)
    fv = card_data.get('f_vwap')
    fv_str = f"外資連續{fv['side']}{fv['days']}日，成本{fv['vwap']}元" if fv else "外資連續買賣超成本：無資料"
    yoy = card_data.get('rev_yoy')
    yoy_str = f"{yoy:.1f}%" if yoy is not None else "官方未公佈"

    if direction == 'short':
        role = "請以首席戰略幕僚身分，對這檔股票進行冷血的「空方」推演——這是候選池篩選出來的空方(做空)候選標的。"
        extra_ask = ("特別注意：放空的下檔利潤有限、上檔軋空風險理論上無限，"
                     "請務必評估「反彈/軋空風險」（例如是否已跌深、族群是否可能止跌），"
                     "不要只用多方那套「還能不能漲」的邏輯硬套在空方標的上。")
        summary_label = "【總指揮空方戰略總結（含軋空風險評估）】"
    else:
        role = "請以首席戰略幕僚身分，對這檔股票進行冷血的「多方」推演。"
        extra_ask = ""
        summary_label = "【總指揮明日戰略總結】"

    user_prompt = (
        f"{role}標的：{name} ({code})。"
        f"現價:{price:.2f} | 漲跌:{gain:.2f}% | {system_a_str} | {gate_str} | "
        f"營收YoY:{yoy_str} | PE:{card_data.get('pe')} | 價值分數:{card_data.get('value_score')} | "
        f"地雷:{'是' if card_data.get('landmine') else '否'} | "
        f"外資5日:{card_data.get('f_5d', 0):.0f}張 | {fv_str} | 大戶比例:{bh_str} | "
        f"MACD:{card_data.get('macd_str', '')} | 防守線:{card_data.get('def_line')} | "
        f"移動停利:{card_data.get('trail_stop')}。{extra_ask}"
        f"請分四段繁體輸出：【第一戰區財報估價小結】、【第二戰區技術面小結】、"
        f"【第三戰區籌碼成本小結】、{summary_label}——"
        f"總結段務必明確提及是否同意系統A的判斷，同意或不同意都要說明理由。"
    )
    system_prompt = "你是一位冷血的台灣股市操盤幕僚。所有輸出嚴格使用繁體中文，並使用台灣金融專有名詞。直擊核心。"
    return system_prompt, user_prompt


def call_ai_models_parallel(system_prompt, user_prompt, api_key, models=None, timeout=30, max_tokens=1200):
    """
    【R97新增，見開發歷程.md「NVIDIA推演變慢排查」章節】平行送給多個NVIDIA
    NIM模型，哪個先成功回應就用哪個——不是依序嘗試（那樣正常情況也會被
    排在前面卡住的模型拖累，總指揮官這輪明確指出這個問題）。

    回傳 (成功與否bool, 結果或錯誤說明str)。
    """
    if not api_key:
        return False, "未配置 NVIDIA API 金鑰"
    try:
        from openai import OpenAI
    except ImportError:
        return False, "openai套件未安裝"

    client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=api_key)
    models_to_try = models or NIM_FALLBACK_MODELS

    def _try_one_model(model_id):
        completion = client.chat.completions.create(
            model=model_id,
            messages=[{"role": "system", "content": system_prompt},
                      {"role": "user", "content": user_prompt}],
            temperature=0.2, max_tokens=max_tokens, timeout=timeout
        )
        return f"【{model_id.split('/')[-1]} 提供分析】\n\n{completion.choices[0].message.content}"

    errors = []
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=len(models_to_try))
    future_to_model = {executor.submit(_try_one_model, m): m for m in models_to_try}
    try:
        for future in concurrent.futures.as_completed(future_to_model):
            model_id = future_to_model[future]
            short = model_id.split('/')[-1]
            try:
                result = future.result()
                executor.shutdown(wait=False, cancel_futures=True)
                return True, result
            except Exception as e:
                emsg = str(e).lower()
                if '401' in emsg or 'unauthorized' in emsg or 'invalid api key' in emsg:
                    errors.append(f"{short}: API金鑰無效或未授權")
                elif '404' in emsg or 'not found' in emsg or 'does not exist' in emsg:
                    errors.append(f"{short}: 模型不存在(已下架)")
                elif '429' in emsg or 'rate' in emsg or 'quota' in emsg:
                    errors.append(f"{short}: 限流/額度不足")
                elif 'timeout' in emsg or 'timed out' in emsg:
                    errors.append(f"{short}: 連線逾時({timeout}s)")
                else:
                    errors.append(f"{short}: {str(e)[:40]}")
                continue
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    return False, ("全部模型都無法使用，逐一狀態：\n- " + "\n- ".join(errors))


def get_fm_real_quota_status():
    """
    【R97新增，見開發歷程.md「候選池rate_limited排查」章節】上面
    get_fm_quota_status()的估計值原本文件寫「FinMind沒有官方即時查額度
    端點」——這是過時的認知，查證後FinMind其實有官方端點：
    GET https://api.web.finmindtrade.com/v2/user_info
    回傳 user_count(真實已用次數) 跟 api_request_limit(真實上限)，是
    FinMind伺服器端的真實數字，不是本工具自己回推的估計值。

    【R97續1修復，總指揮官實測回報】第一版只用Authorization: Bearer
    header帶token，結果全部回傳{'msg': 'Token 違法.', 'status': 400}——
    這個查詢本身失敗，卻被誤判成「剩餘額度=0」，直接把整個候選池砍到0檔，
    比沒有這個安全機制還糟（本來只是Stage2會失敗，現在Stage0a都不跑了）。

    【R97續3新發現，總指揮官盤中實測回報】改用_SESSION(帶正常瀏覽器UA)
    之後，這個查詢在排程端(GitHub Actions)還是一樣回'Token 違法'——這
    代表上一版「User-Agent」的診斷是錯的，不是這個原因。新的懷疑方向：
    GitHub Actions執行環境的對外IP是知名雲端機房IP段，FinMind這個帳號
    查詢端點(api.web.finmindtrade.com)很可能對雲端機房IP有額外的風控/
    封鎖規則，跟一般的資料查詢端點(api.finmindtrade.com，排程一直用
    這個都正常)是不同等級的防護。總指揮官的瀏覽器測試是從一般家用/
    公司網路IP發出，不會被同一套規則擋到。這個假設同樣沒辦法在這個
    沙盒環境驗證（打不到該網域），但已經是第二次不同排查方向都指向
    「排程端這個特定端點被擋，其他端點都正常」這個共同現象。

    這裡修兩個問題：
    1. 認證方式：token當查詢字串參數（params={'token': token}），不是
       Authorization header，這是全專案目前唯一驗證過真的能用的認證
       方式，不再自己另外猜一種。header方式先留著當備援嘗試，兩種都
       失敗才真的判定查不到。
    2. 【最關鍵】查詢失敗時回傳total_remaining=None（不是0），代表「不知道，
       不是真的沒額度」——呼叫端看到None要退回原本的容量設定正常跑，
       不能把「安全機制本身故障」跟「額度真的用完」混為一談。安全機制
       壞掉的正確處理方式是「退回沒有這層防護之前的行為」，不是「假設
       最壞情況把整個流程砍光」。
    """
    tokens = list(_FM_TOKENS)
    result = {"tokens": [], "total_remaining": None}
    _any_success = False
    for t in tokens:
        used = limit = None
        _last_note = ""
        # 【R97續2修復，總指揮官這輪抓到根本原因】前兩版失敗的真正原因
        # 找到了，跟認證方式（query param vs Bearer header）完全無關——
        # 是這裡用了最原始的requests.get()，沒有帶正常瀏覽器身分
        # (User-Agent)。全專案其他所有FinMind請求都是用_SESSION發送
        # （_SESSION = get_safe_session()，帶GOV_HEADERS這組瀏覽器UA），
        # 只有這個函式當初漏掉，直接用裸的requests.get()——FinMind這個
        # 帳號查詢端點很可能把「看起來像程式自動發送、沒有正常UA」的
        # 請求擋下來，回傳一個誤導性的「Token 違法」，讓人誤以為是token
        # 或認證方式的問題，實際上是被當機器人擋掉。總指揮官用瀏覽器測試
        # 會成功，正是因為瀏覽器本來就有正常UA。改用_SESSION後跟其他
        # 所有FinMind呼叫用同一套身分，理論上就能拿到真實額度數字。
        for _mode, _kwargs in [
            ("query_param", {"params": {"token": t}}),
            ("bearer_header", {"headers": {"Authorization": f"Bearer {t}"}}),
        ]:
            try:
                resp = _SESSION.get("https://api.web.finmindtrade.com/v2/user_info",
                                    timeout=6, **_kwargs)
                data = resp.json()
                used = data.get("user_count")
                limit = data.get("api_request_limit")
                if used is not None and limit is not None:
                    break   # 這個模式成功了，不用再試下一種
                _last_note = f"{_mode}回應格式異常：{data}"
            except Exception as e:
                _last_note = f"{_mode}查詢失敗：{type(e).__name__}: {e}"
        if used is not None and limit is not None:
            remaining = max(0, limit - used)
            result["tokens"].append({"used": used, "limit": limit, "remaining": remaining})
            result["total_remaining"] = (result["total_remaining"] or 0) + remaining
            _any_success = True
        else:
            result["tokens"].append({"used": None, "limit": None, "remaining": None,
                                     "note": _last_note})
    if not _any_success:
        print(f"[FinMind真實額度查詢] 所有token都查詢失敗，這個安全機制本身暫時失效"
              f"（不代表額度真的用完）。詳情：{[t.get('note') for t in result['tokens']]}")
    return result


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
            if res.status_code in (401, 403):
                # 【R95/R56新增】401/403代表這組憑證被拒絕，直接標記
                # permission_denied不重試，讓外層立刻換下一組；非200時
                # 解析回應內容比對付費限定關鍵字，同樣歸類成permission_denied。
                _body_text = res.text or ''
                _body_preview = _body_text[:200].replace('\n', ' ')
                try:
                    _err_payload = res.json()
                    _err_msg = str(_err_payload.get('msg', ''))
                    _err_m = _err_msg.lower()
                    if ('sponsor' in _err_m or 'backer' in _err_m or 'permission' in _err_m
                            or 'not allow' in _err_m or 'upgrade' in _err_m
                            or 'level is free' in _err_m or '權限' in _err_msg):
                        raise FinMindAPIError('permission_denied', f"HTTP {res.status_code}：{_err_msg}")
                except (ValueError, KeyError):
                    pass   # body不是預期的JSON格式，退回下面的通用http_error處理
                last_reason, last_detail = "http_error", f"HTTP {res.status_code}：{_body_preview}"
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

    【R49 修復】原本只有「額度用盡」和「權限不足」才換下一組，「逾時/連線
    失敗/HTTP錯誤」被歸類成「跟帳號無關的問題，換帳號也一樣」直接放棄——
    這個假設是錯的。實測發現：TaiwanStockInfo這種大型批次端點，某一組
    憑證的那次請求逾時，不代表FinMind伺服器本身掛了，換一組憑證（等於
    重新建立一次連線）常常就通了。原本的設計等於「帳號1一逾時就整組放棄，
    帳號2跟訪客額度形同虛設、永遠不會被試到」——這正是族群輪動熱力圖
    一直顯示「TaiwanStockInfo 未回應」、但額度狀態卻只看到帳號1有用量、
    帳號2跟訪客始終0次的根本原因。現在逾時/連線問題也會換下一組再試，
    只有「查無資料」（empty_data，資料本身就是不存在，換帳號不會生出資料）
    維持原樣直接回報，不浪費額度重試。

    【R97續18修復，總指揮官指出「改一個地方其他地方沒改」——這裡是根本
    修法】上一輪只在calculate_signals_worker的股價K棒這一個FinMind呼叫點
    加了「額度已知用盡就跳過」的判斷，但一支戰卡背後還有法人買賣超/融資
    融券/月營收/大戶持股等至少5、6個獨立的FinMind呼叫，全部沒補到——
    這正是總指揮官擔心的那種「東修西漏」。與其在每個呼叫端各自加一次
    判斷（未來新增呼叫端還是會漏），這裡直接在全站唯一的FinMind請求
    入口統一擋——is_finmind_likely_exhausted()判斷「目前設定的所有token
    最近都被標記過額度用盡」時，代表接下來不管是這裡的哪一組憑證，重試
    也幾乎確定會一樣失敗（這不是猜測，是根據這個process自己剛剛真實
    觀察到的結果），直接快速失敗，不再對已知會失敗的請求跑完整輪N組
    憑證×max_retries次重試——不管呼叫端是哪個功能、要不要"正確性優先"，
    對「已知會失敗」的請求硬等都沒有意義，唯一的差別只是等更久還是
    等更短，所以在這個共用入口統一處理是安全的，也不用要求以後每個
    新的呼叫端自己記得加判斷。
    """
    if is_finmind_likely_exhausted():
        raise FinMindAPIError('rate_limited',
                              '所有已設定的FinMind憑證最近都被判定額度用盡（15分鐘冷卻中），'
                              '直接快速失敗，不浪費時間對已知會失敗的請求跑完整輪重試。')
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
                # 【R56新增】權限不足分兩種：token本身失效(標記冷卻)，
                # vs 這個資料集要更高付費方案(不冷卻，其他資料集可能還通)。
                _detail_lower = (e.detail or '').lower()
                # 【R95新增】401/403同樣屬於「這組憑證本身壞了」，一併
                # 標記冷卻，避免壞憑證之後每次呼叫都重試一次白白等待。
                if 'illegal' in _detail_lower or 'invalid' in _detail_lower or 'http 401' in _detail_lower or 'http 403' in _detail_lower:
                    _fm_mark_exhausted(cred)
                last_exc = e
                continue
            if e.reason in ('timeout', 'connection_error', 'http_error'):
                # 【R49】這組憑證這次連線逾時/失敗，不代表換一組也會一樣——
                # 換下一組再試一次，真的所有憑證都連不上才放棄。
                last_exc = e
                continue
            raise                          # empty_data：資料本身不存在，換帳號無意義
    raise last_exc if last_exc else FinMindAPIError('unknown', '所有憑證皆無法取得資料')


# ==============================================================================
# 二、核心常數（單一來源，網頁版與排程版都從這裡讀）
# ==============================================================================
# 防守線=MA5-此倍數×ATR，維持0.5（1.5已明確否決，見開發歷程.md）。
DEF_LINE_ATR_MULT = 0.5

# 【R97續14新增】股本快取有效期（天）——股本只有增資/減資才會變，180天
# 已涵蓋絕大多數股票一年最多1-2次增減資的頻率，不需要像價量資料一樣頻繁更新。
SHARES_CACHE_TTL_DAYS = 180
# 【R97續14新增】股本抓取失敗後的退避天數——最近試過失敗的symbol，這段
# 期間內不再重打FinMind，避免額度被同一批已知抓不到的股票反覆浪費，
# 留給backfill_shares_outstanding階段慢慢補（那個階段沒有這層退避限制）。
SHARES_ATTEMPT_BACKOFF_DAYS = 3

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

# 【V160 R41 新增】社群長期追蹤的隔日沖分點名單（最後更新2026-07-24，
# 需定期維護）。同一分點底下客戶眾多，設計成警示降級而非一票否決。
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
def safe_float(val):
    """
    【R97搬進共用模組】原本只在warroom_v160.py，候選池篩選(排程端)算
    週轉率/成交值排行時也需要用同一份，搬進來共用，避免兩邊各自維護
    一份同名函式又不小心長歪（這正是本檔案開頭module docstring警告過的
    「同一套邏輯分散維護」問題）。

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


def fetch_shares_outstanding(symbol, token=None, sb=None, ignore_backoff=False):
    """
    【R97搬進共用模組，原本在warroom_v160.py】取得發行股數，供區間週轉率
    計算用（週轉率 = 區間成交金額 ÷ 市值，市值 = 股價 × 發行股數）。

    用 FinMind `TaiwanStockShareholding`（外資持股表）的
    `NumberOfSharesIssued`（發行股數）欄位——這個資料集是單檔查詢免費
    （跟月營收表同等級，只有「一次拿全市場」才需要付費）。

    回傳最新一筆的發行股數（int）或 None（抓不到時誠實回報，不編造）。

    【R97續8新增，R97續14延長+加失敗退避】sb不為None時，優先查
    stock_shares_outstanding快取表——股本幾乎不會變（只有增資/減資才會動），
    SHARES_CACHE_TTL_DAYS(180天)內的快取直接用，不重打FinMind。

    【R97續14新增，見對話紀錄「smart_money_scan全市場1078檔股本快取風暴」】
    總指揮官實測回報：stage_smart_money_scan掃全市場時，還沒快取過的symbol
    每天都重打一次FinMind，連續撞額度上限，單次執行拖到35分鐘以上。這裡
    加上「失敗退避」——查快取表時，如果shares是空的但last_attempt_at在
    SHARES_ATTEMPT_BACKOFF_DAYS(3天)以內，代表最近才試過失敗，直接回傳
    None跳過這次FinMind呼叫，留給backfill_shares_outstanding階段慢慢補，
    不在smart_money_scan/build_intraday_pool這種常態掃描裡反覆浪費額度。
    不論這次成功或失敗，只要有打FinMind，都會更新last_attempt_at。
    """
    if sb is not None:
        try:
            res = (sb.table("stock_shares_outstanding")
                  .select("shares,updated_at,last_attempt_at").eq("symbol", symbol).execute())
            rows = res.data or []
            if rows:
                _updated = rows[0].get("updated_at", "")
                _age_days = 9999
                try:
                    _updated_dt = datetime.fromisoformat(_updated.replace("Z", "+00:00"))
                    _age_days = (datetime.now(timezone.utc) - _updated_dt).days
                except (ValueError, TypeError):
                    pass
                if _age_days <= SHARES_CACHE_TTL_DAYS and rows[0].get("shares"):
                    _SNAPSHOT_CACHE_STATS["shares_hit"] += 1
                    return int(rows[0]["shares"])
                # 【R97續14】股本是空的，但最近才試過失敗——退避，不重打FinMind
                # （ignore_backoff=True時跳過，供backfill_shares_outstanding階段
                # 強制重試，那個階段的目的就是清掉這些長期缺快取的symbol）
                if not rows[0].get("shares") and not ignore_backoff:
                    _last_attempt = rows[0].get("last_attempt_at", "")
                    _attempt_age_days = 9999
                    try:
                        _attempt_dt = datetime.fromisoformat(_last_attempt.replace("Z", "+00:00"))
                        _attempt_age_days = (datetime.now(timezone.utc) - _attempt_dt).days
                    except (ValueError, TypeError):
                        pass
                    if _attempt_age_days <= SHARES_ATTEMPT_BACKOFF_DAYS:
                        _SNAPSHOT_CACHE_STATS["shares_backoff"] += 1
                        return None
        except Exception as e:
            print(f"[fetch_shares_outstanding] {symbol} 查快取表失敗，"
                  f"退回FinMind：{type(e).__name__}: {e}")
    _SNAPSHOT_CACHE_STATS["shares_miss"] += 1

    def _touch_attempt():
        # 【R97續14】不論成敗都記錄「試過的時間」，供下次退避判斷用。
        if sb is None:
            return
        try:
            sb.table("stock_shares_outstanding").upsert(
                {"symbol": symbol, "last_attempt_at": datetime.now(timezone.utc).isoformat()},
                on_conflict="symbol").execute()
        except Exception as e:
            print(f"[fetch_shares_outstanding] {symbol} 記錄嘗試時間失敗（不影響本次計算）："
                  f"{type(e).__name__}: {e}")

    url = 'https://api.finmindtrade.com/api/v4/data'
    params = {'dataset': 'TaiwanStockShareholding', 'data_id': symbol,
              'start_date': (datetime.now(TAIPEI_TZ) - timedelta(days=30)).strftime('%Y-%m-%d')}
    if token:
        params['token'] = token
    try:
        payload = _finmind_get(url, params, max_retries=2, timeout=8)
        df = pd.DataFrame(payload.get('data', []))
        if df.empty or 'NumberOfSharesIssued' not in df.columns:
            _touch_attempt()
            return None
        df = df.sort_values('date')
        latest = pd.to_numeric(df['NumberOfSharesIssued'], errors='coerce').dropna()
        shares = int(latest.iloc[-1]) if len(latest) else None
        if shares and sb is not None:
            try:
                sb.table("stock_shares_outstanding").upsert(
                    {"symbol": symbol, "shares": shares,
                     "last_attempt_at": datetime.now(timezone.utc).isoformat()},
                    on_conflict="symbol").execute()
            except Exception as e:
                print(f"[fetch_shares_outstanding] {symbol} 寫回快取表失敗（不影響本次計算）："
                      f"{type(e).__name__}: {e}")
        elif not shares:
            _touch_attempt()
        return shares
    except FinMindAPIError as _e:
        print(f"[fetch_shares_outstanding-診斷] {symbol} FinMind抓股本失敗：{type(_e).__name__}: {_e}")
        _touch_attempt()
        return None
    except Exception as _e:
        print(f"[fetch_shares_outstanding-診斷] {symbol} 非預期例外：{type(_e).__name__}: {_e}")
        _touch_attempt()
        return None


def fetch_market_turnover_ranking_with_value():
    """
    【R97搬進共用模組並強化，原本在warroom_v160.py】抓全市場「當日成交值」
    排行。搬進core.py讓候選池篩選(排程端stage_build_intraday_pool)也能
    共用，跟原本fetch_market_turnover_ranking()同一套抓法。

    【強化】原本只回傳代碼清單，這裡改成回傳(code, value)元組清單，讓
    呼叫端可以直接用成交值本身做門檻判斷（例如只留成交值>=某個金額的），
    不用另外再查一次。warroom_v160.py的fetch_market_turnover_ranking()
    改成呼叫這個再取代碼，保持向下相容，不影響既有呼叫端。

    做法：兩支免費官方端點各一次呼叫，各自涵蓋上市/上櫃全部個股：
      上市：TWSE STOCK_DAY_ALL（個股日成交資訊，含成交金額）
      上櫃：TPEx tpex_mainboard_daily_close_quotes（上櫃日收盤行情）
    依成交值由大到小排序回傳。任一邊失敗就只用另一邊，兩邊都失敗回空list。
    """
    ranked = []

    try:
        res = _SESSION.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=8)
        if res.status_code == 200:
            for item in res.json():
                code = str(item.get('Code', '')).strip()
                if len(code) != 4 or not code.isdigit():
                    continue
                val = safe_float(item.get('TradeValue', 0))
                if val > 0:
                    ranked.append((code, val, 'twse'))
    except Exception as e:
        print(f"[成交值排行] 上市端點失敗：{e}")

    try:
        res = _SESSION.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
                           timeout=8)
        if res.status_code == 200:
            for item in res.json():
                code = str(item.get('SecuritiesCompanyCode', item.get('Code', ''))).strip()
                if len(code) != 4 or not code.isdigit():
                    continue
                raw = item.get('TradingAmount', item.get('TradeValue', 0))
                val = safe_float(str(raw).replace(',', ''))
                if val > 0:
                    ranked.append((code, val, 'otc'))
    except Exception as e:
        print(f"[成交值排行] 上櫃端點失敗：{e}")

    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked


def fetch_stock_price_and_value_history(symbol, days_back, token=None, sb=None):
    """
    【R97新增，設計時就整合成一次API call】抓單一個股過去N天的收盤價+
    每日成交金額（Trading_money），一次FinMind TaiwanStockPrice呼叫同時
    拿到兩者——避免呼叫端另外再打一次抓「最新股價」的請求，區間週轉率
    計算(現價×股本=市值)跟成交金額加總可以共用同一份資料，省一半API量。

    跟fetch_finmind_stock_price()是同一個資料集，那個函式當初只留OHLCV
    給K棒用、把Trading_money捨棄了，這裡另外做一份保留成交金額+收盤價
    的版本，不去動原本那個函式（避免影響它原本的既有用途）。

    回傳：DataFrame[close, trading_money]，以日期排序（新到舊，方便
    直接取.iloc[0]當最新收盤價），或None（抓不到時）。

    【R97續9新增，真實資料驗證過】sb不為None時，優先查twse_market_
    snapshot表（官方MI_INDEX批次端點每日同步累積的資料）。表剛開始
    累積時筆數不足days_back，週轉率算出來會偏保守（因為累積天數不夠、
    分母天數少），但這是漸進式過渡，累積夠天數後自動變準，不需要改
    任何程式碼。查表失敗或查無資料才退回FinMind，門檻跟其他三支
    （法人/PE/營收）一致：有資料就用，不要求一定要滿days_back天。
    """
    if sb is not None:
        snap_df = _load_price_value_from_snapshot(sb, symbol, days_back)
        if snap_df is not None and len(snap_df) >= 1:
            _SNAPSHOT_CACHE_STATS["price_value_hit"] += 1
            return snap_df
    _SNAPSHOT_CACHE_STATS["price_value_miss"] += 1

    try:
        # _finmind_get()自己會做多帳號額度輪替，這裡傳進去的token值本身
        # 不影響輪替結果，留token參數只是保持跟其他函式一致的呼叫介面。
        url = 'https://api.finmindtrade.com/api/v4/data'
        start_date = (datetime.now(TAIPEI_TZ) - timedelta(days=days_back + 10)).strftime('%Y-%m-%d')
        params = {'dataset': 'TaiwanStockPrice', 'data_id': symbol, 'start_date': start_date}
        if token:
            params['token'] = token
        payload = _finmind_get(url, params, max_retries=2, timeout=8)
        df = pd.DataFrame(payload.get('data', []))
        if df.empty or 'Trading_money' not in df.columns or 'close' not in df.columns:
            return None
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date').sort_index()
        out = pd.DataFrame({
            'close': pd.to_numeric(df['close'], errors='coerce'),
            'trading_money': pd.to_numeric(df['Trading_money'], errors='coerce'),
        }).dropna()
        if out.empty:
            return None
        return out.tail(days_back + 5).sort_index(ascending=False)
    except FinMindAPIError as _e:
        print(f"[fetch_stock_price_and_value_history-診斷] {symbol} 抓價量歷史失敗："
              f"{type(_e).__name__}: {_e}")
        return None
    except Exception as _e:
        print(f"[fetch_stock_price_and_value_history-診斷] {symbol} 非預期例外："
              f"{type(_e).__name__}: {_e}")
        return None


def compute_interval_turnover(symbol, days=10, token=None, overheated_threshold=50.0, sb=None):
    """
    【R97新增，總指揮官依實戰資料規劃：區間週轉率 = Σ近N天成交金額 ÷ 市值】

    公式與門檻依總指揮官提供的實戰經驗：
      區間週轉率 = 近N天成交金額加總 ÷ 個股市值
      >50% 視為過度炒作警訊（這裡刻意做成「標記」而非「排除」——排除會
      把最熱的動能股踢出候選池，跟找當沖標的的初衷矛盾，見這輪討論結論）。

    現價跟成交金額歷史用同一次fetch_stock_price_and_value_history()呼叫
    拿到，只多打一次fetch_shares_outstanding()查股本，總共每檔2次
    API call（不是3次）。

    回傳 dict：{turnover_pct, overheated, market_cap, sum_trading_value, note}
    任何一段資料抓不到都誠實回傳 None/False，不用0假裝，呼叫端可以用
    turnover_pct is None 判斷「這檔沒算出來，不納入排序」。
    """
    pv = fetch_stock_price_and_value_history(symbol, days, token=token, sb=sb)
    if pv is None or pv.empty:
        return {"turnover_pct": None, "overheated": False, "market_cap": None,
                "sum_trading_value": None, "note": "價量歷史缺資料，無法計算"}
    current_price = float(pv['close'].iloc[0])

    shares = fetch_shares_outstanding(symbol, token=token, sb=sb)
    if not shares or shares <= 0 or current_price <= 0:
        return {"turnover_pct": None, "overheated": False, "market_cap": None,
                "sum_trading_value": None, "note": "股本缺資料，無法計算"}
    market_cap = shares * current_price

    sum_value = float(pv['trading_money'].sum())
    turnover_pct = round(sum_value / market_cap * 100, 2)
    overheated = turnover_pct > overheated_threshold
    return {"turnover_pct": turnover_pct, "overheated": overheated, "market_cap": market_cap,
            "sum_trading_value": sum_value, "current_price": current_price,
            "note": f"近{len(pv)}天週轉率{turnover_pct}%" + ("（⚠️過熱）" if overheated else "")}


def detect_smart_money_patterns(sb, symbol, trade_date=None):
    """
    【R97續10新增，取材自CMoney「週轉率高的熱門股/週轉率異常/週轉率高的
    反轉股」三篇文章的選股邏輯，加上總指揮官提出的第四維度】

    對單一股票判斷四個主力偵測維度，同一檔可能同時符合多個（不是互斥）：

      ①週轉率絕對水位高：60天週轉率>50%——找持續高度活躍的熱門股
      ②冷門股突然爆量：當日成交量>5日均量3倍 且 60天週轉率<50%——
        專找「平常冷清、今天突然爆量」的股票，這種最可能是主力剛開始
        進場的第一根訊號
      ③週轉率高的反轉股：60天週轉率<50% 且 均線(5/20/60)糾結——量縮
        蓄勢，抓變盤前的沉寂點
      ④週轉率逐步墊高：最近5天的週轉率序列持續遞增——代表資金是「有
        計畫、持續」在進場，不是單日消息面衝量，比單日爆量更可信

    全部從twse_market_snapshot的累積歷史計算，不逐檔額外打任何API——
    複用compute_interval_turnover()（已經是snapshot-first）跟這裡直接
    查表算MA/5日均量，累積夠天數後這個函式完全零FinMind呼叫。

    【資料深度限制，優雅降級，不是bug】
    - ①②需要60天成交量/成交金額+股本，累積不足60天時turnover_pct會
      用「目前累積到的天數」算，數字偏保守，不是錯——README已經在
      compute_interval_turnover的note欄位講清楚實際用了幾天
    - ③需要60天收盤價算MA60，不足60天這個維度直接不判斷（回傳False，
      不用不足的資料硬猜）
    - ④需要至少5天資料，不足5天這個維度不判斷

    回傳：{symbol, patterns:[...], turnover_pct, vol_ratio_5d, note,
           以及R97續15新增的enrich欄位：trading_value, inst_net_5d,
           foreign_streak, trust_streak, shares, above_ma20, above_ma60,
           broke_20d_high, rev_yoy}
    patterns是符合的維度中文標籤list，空list代表四個維度都沒中。

    【R97續15新增，主力偵測收斂】原本只回傳四維度判斷結果，231檔全部平鋪。
    這裡順手把「籌碼/型態/流動性/基本面」確認資料一起算出來回傳，讓
    stage_smart_money_scan寫進smart_money_candidates的enrich欄位，網頁UI
    就能純讀表做「指令組合器」收斂，不用現場逐檔運算（避免登入卡頁）。
    這些enrich資料絕大多數從本函式「已經載入的hist_df」直接算，只多一次
    法人快照讀取(_load_institutional_from_snapshot)，零額外FinMind成本。
    """
    result = {"symbol": symbol, "patterns": [], "turnover_pct": None,
              "vol_ratio_5d": None, "note": "",
              # R97續15 enrich欄位（預設None/False，算得到才填）
              "trading_value": None, "inst_net_5d": None,
              "foreign_streak": 0, "trust_streak": 0, "shares": None,
              "above_ma20": None, "above_ma60": None, "broke_20d_high": None,
              "rev_yoy": None}

    turnover_info = compute_interval_turnover(symbol, days=60, sb=sb)
    turnover_pct = turnover_info.get("turnover_pct")
    result["turnover_pct"] = turnover_pct
    # 股本從市值反推（compute_interval_turnover已經算過，不重抓）
    _mcap = turnover_info.get("market_cap")
    _cprice = turnover_info.get("current_price")
    if _mcap and _cprice and _cprice > 0:
        result["shares"] = round(_mcap / _cprice)

    if turnover_pct is not None:
        if turnover_pct > 50.0:
            result["patterns"].append("週轉率高的熱門股")

    # 查60天完整的價量歷史，供②③④共用（一次查表，三個維度一起判斷）
    hist_df = _load_price_value_from_snapshot(sb, symbol, days_back=60)
    if hist_df is None or hist_df.empty:
        result["note"] = "價量歷史缺資料，②③④維度無法判斷"
        return result

    hist_df = hist_df.sort_index(ascending=False)   # 新到舊
    volumes = hist_df.get("trading_money")   # 注意：這裡沿用_load_price_value_from_snapshot
    # 【重要】_load_price_value_from_snapshot回傳的是trading_money(成交金額)，
    # 不是成交股數。②的「成交量>5日均量3倍」CMoney原文用的是「量」(股數)，
    # 這裡用成交金額當代理指標——同樣能反映「資金活躍度暴增」這件事，
    # 且已經有的資料就能算，不用另外多查一次trading_volume欄位，兩者
    # 在判斷「暴量」這件事上實務意義相近，這是刻意的簡化，不是疏漏。
    if volumes is not None and len(volumes) >= 6:
        today_value = float(volumes.iloc[0])
        avg5 = float(volumes.iloc[1:6].mean())
        vol_ratio = round(today_value / avg5, 2) if avg5 > 0 else None
        result["vol_ratio_5d"] = vol_ratio
        result["trading_value"] = round(today_value)   # R97續15：今日成交金額(流動性硬地板用)
        if vol_ratio is not None and vol_ratio > 3.0 and turnover_pct is not None and turnover_pct < 50.0:
            result["patterns"].append("週轉率異常(主力關注)")
    elif volumes is not None and len(volumes) >= 1:
        # 資料不足6天算不了量比，但成交金額至少填今日的，供流動性地板判斷
        result["trading_value"] = round(float(volumes.iloc[0]))

    closes = hist_df.get("close")
    # 【R97續15 enrich】站上MA20/MA60 + 突破近20日高——全部從已載入的
    # closes直接算，不多查任何資料。curr_price用最近一筆收盤（iloc[0]）。
    if closes is not None and len(closes) >= 1:
        _curr = float(closes.iloc[0])
        if len(closes) >= 20:
            _ma20 = float(closes.iloc[0:20].mean())
            result["above_ma20"] = bool(_curr > _ma20)
            # 突破近20日高：今日收盤 >= 前20天（不含今日）的最高收盤
            _prior20_high = float(closes.iloc[1:21].max()) if len(closes) >= 21 else float(closes.iloc[1:20].max())
            result["broke_20d_high"] = bool(_curr >= _prior20_high)
        if len(closes) >= 60:
            _ma60 = float(closes.iloc[0:60].mean())
            result["above_ma60"] = bool(_curr > _ma60)

    if closes is not None and len(closes) >= 60:
        ma5 = float(closes.iloc[0:5].mean())
        ma20 = float(closes.iloc[0:20].mean())
        ma60 = float(closes.iloc[0:60].mean())
        if min(ma5, ma20, ma60) > 0:
            compression = (max(ma5, ma20, ma60) - min(ma5, ma20, ma60)) / min(ma5, ma20, ma60)
            if compression < 0.05 and turnover_pct is not None and turnover_pct < 50.0:
                result["patterns"].append("週轉率高的反轉股(均線糾結)")

    # ④週轉率逐步墊高：用近5天「單日成交金額」的走勢當代理（不用重算
    # 每天各自的週轉率百分比——那樣要對每一天都重抓一次股本市值，
    # 划不來；用單日成交金額的斜率本身就能反映「資金是否持續加溫」）
    if volumes is not None and len(volumes) >= 5:
        recent5 = volumes.iloc[0:5].iloc[::-1].tolist()   # 轉成舊到新
        increasing_days = sum(1 for i in range(1, len(recent5)) if recent5[i] > recent5[i-1])
        if increasing_days >= 3:   # 5天裡至少3次遞增，不要求嚴格單調(容忍偶爾小回檔)
            result["patterns"].append("週轉率逐步墊高")

    # 【R97續15 enrich】三大法人近5日淨買超 + 外資/投信連買天數（一次快照讀取）
    _inst = _load_institutional_from_snapshot(sb, symbol, days_back=5)
    result["inst_net_5d"] = _inst.get("inst_net_5d")
    result["foreign_streak"] = _inst.get("foreign_streak", 0)
    result["trust_streak"] = _inst.get("trust_streak", 0)

    # 【R97續15 enrich】月營收年增——直接讀當日snapshot的rev_yoy欄位
    try:
        _rev_res = (sb.table("twse_market_snapshot").select("rev_yoy")
                   .eq("symbol", symbol).not_.is_("rev_yoy", "null")
                   .order("trade_date", desc=True).limit(1).execute())
        if _rev_res.data:
            result["rev_yoy"] = _rev_res.data[0].get("rev_yoy")
    except Exception:
        pass

    result["note"] = f"符合{len(result['patterns'])}個維度" if result["patterns"] else "四維度均未符合"
    return result


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
# 三之一、收盤強弱代查（R96新增，Step 1）——收盤價落在當日高低區間的
# 百分位。純代查用途，不掛進既有評分引擎，門檻沿用圖上原始25%/75%。
# ==============================================================================
def evaluate_closing_strength(open_price, high, low, close):
    """
    收盤強弱代查：依當日 O/H/L/C，判斷收盤價落在當日高低區間的百分位。

    規則（依策略框架圖「波段續抱資格三關·第三關」）：
      pct >= 75%（收在區間前25%高檔）→ strong，「明天有戲」
      pct <= 25%（收在區間後25%低檔）→ weak，「今天該走」
      其餘 → neutral，中段區，不強不弱

    另外偵測長上影線：上影線長度 >= 實體2倍，且收盤落在下半段（pct < 50%），
    代表盤中曾經衝更高、但賣壓把漲幅吃回去——即使還沒跌破25%門檻，也額外
    標記提醒（圖上「長上影線」示警的精神，量化成可判斷的條件）。

    high/low 相等（例如一字鎖停）時，pct 定義為 50%（無法判斷強弱，中性處理，
    不假裝有意義的百分位）。

    回傳 dict：{pct, verdict, label, detail, has_long_upper_shadow}
    verdict 固定是 'strong' / 'weak' / 'neutral' 三選一，方便呼叫端直接比對，
    不用重新解析 detail 文字。
    """
    day_range = high - low
    pct = ((close - low) / day_range) if day_range > 0 else 0.5

    body = abs(close - open_price)
    upper_shadow = max(0.0, high - max(close, open_price))
    has_long_upper_shadow = bool(day_range > 0 and pct < 0.5 and (
        (body > 0 and upper_shadow >= body * 2) or
        (body == 0 and upper_shadow / day_range >= 0.5)
    ))

    if pct >= 0.75:
        verdict, label = "strong", "收高檔"
        # 【修復】原本誤用(1-pct)*100，跟badge顯示的pct方向相反，統一
        # 改成直接用pct。
        detail = f"收盤來到當日區間的{round(pct * 100)}%位置（高檔區），明天有戲。"
    elif pct <= 0.25:
        verdict, label = "weak", "收低檔"
        detail = f"收盤落在當日區間後{round(pct * 100)}%（低檔區），今天該走。"
    else:
        verdict, label = "neutral", "收中段"
        detail = f"收盤落在當日區間中段（{round(pct * 100)}%），不強不弱，續觀察。"

    if has_long_upper_shadow:
        detail += " ⚠️ 長上影線，盤中衝更高但賣壓把漲幅吃回去。"

    return {
        "pct": round(pct * 100, 1),
        "verdict": verdict,
        "label": label,
        "detail": detail,
        "has_long_upper_shadow": has_long_upper_shadow,
    }


# ==============================================================================
# 三之二、攻擊K棒辨識 + 量能達標代查（R96新增，Step 2）——find_attack_
# bar是共用元件，Step 3拉回體檢也用同一個函式，避免兩關各寫一套。
# ==============================================================================
def find_attack_bar(hist, lookback=20, vol_ratio_threshold=1.5):
    """
    找出「攻擊K棒」——策略框架圖裡「第一波攻擊」／「攻擊K棒」的概念，供
    量能達標代查（新B-2）與之後的拉回體檢（新B-1／新A-1）共用，避免兩關
    各自寫一套「攻擊起漲點」的判定邏輯，日後校準只需要改這一處。

    定義：從最近的K棒往回找（最多looback根），找「最近一根」同時符合
    「爆量」(成交量 >= 這根之前5根均量的vol_ratio_threshold倍，預設1.5倍，
    跟現有ma_compression_breakout因子用的爆量門檻一致，不另外發明新標準)
    且「收紅」(收盤>開盤) 的K棒，視為這一波攻擊的起漲點。

    hist: 需要 Open/High/Low/Close/Volume 欄位的 DataFrame（yfinance/FinMind
    格式皆可，跟系統其他地方吃的hist格式一致），由新到舊或舊到新皆可，
    這裡一律用.iloc位置索引，不假設索引本身的排序意義。

    回傳 dict：{position(在hist裡的iloc位置), date, open, high, low, close,
    volume} 或 None（近期找不到符合條件的攻擊K棒時，誠實回傳None，不用
    猜測硬湊一個答案）。
    """
    if hist is None or len(hist) < 10:
        return None
    n = min(lookback, len(hist) - 5)
    for i in range(len(hist) - 1, len(hist) - 1 - n, -1):
        if i < 5:
            break
        vol = float(hist['Volume'].iloc[i])
        prev5_vol = hist['Volume'].iloc[i - 5:i]
        avg5 = float(prev5_vol.mean()) if len(prev5_vol) > 0 else 0.0
        is_bullish = float(hist['Close'].iloc[i]) > float(hist['Open'].iloc[i])
        if avg5 > 0 and vol >= avg5 * vol_ratio_threshold and is_bullish:
            return {
                "position": i,
                "date": hist.index[i],
                "open": float(hist['Open'].iloc[i]),
                "high": float(hist['High'].iloc[i]),
                "low": float(hist['Low'].iloc[i]),
                "close": float(hist['Close'].iloc[i]),
                "volume": vol,
            }
    return None


def evaluate_volume_followthrough(hist, attack_bar=None, new_high_window=20):
    """
    量能達標代查（依策略框架圖「波段續抱資格三關·第二關」）：股價創新高時，
    成交量是否跟得上，判斷是不是有新資金進場、還是沒人願意高檔承接。

    規則：
      創新高 且 今日量 >= 攻擊量的80% → strong，「量能達標」，有新資金
      進場，趨勢健康，續抱
      創新高 且 今日量 < 攻擊量的50% → weak，「量能不足」，沒人願意
      高檔承接，隨時可能拉回，該走就走
      創新高 但今日量介於50%-80%之間 → neutral，續觀察
      沒有創新高 → neutral，這一關的判斷前提是「創新高」，非創高時
      這一關先不適用（不是「不合格」，是「還沒輪到這一關判斷」）

    「創新高」的定義：今日收盤 >= 最近 new_high_window(預設20)根K棒的最高價
    （用High欄位，不是只比收盤價，跟系統既有build_trade_zones()裡high_20
    的算法一致，沿用同一套「近期新高」定義，不另外發明新標準）。

    attack_bar: find_attack_bar()的回傳值，未提供時這裡會自動呼叫一次
    找一次。找不到攻擊K棒時（近期都沒有符合條件的爆量起漲點），量能比較
    沒有基準可用，回傳verdict='unknown'，誠實講清楚原因，不假裝有答案。

    回傳 dict：{verdict, label, is_new_high, ratio_pct, attack_volume,
    today_volume, detail}
    """
    if hist is None or len(hist) < 6:
        return None
    if attack_bar is None:
        # 自動尋找攻擊K棒要排除「今天」，避免今天自己被誤判成攻擊K棒
        # （自己跟自己比恆等於100%，失去比較意義）。
        attack_bar = find_attack_bar(hist.iloc[:-1])

    today_close = float(hist['Close'].iloc[-1])
    today_vol = float(hist['Volume'].iloc[-1])
    # 【修復】「近期新高」比較基準必須是「今天以前」，不能把今天自己也
    # 算進窗口，否則今天的High永遠是窗口最大值，比較沒有意義。
    _prior_window = hist['High'].iloc[-(new_high_window + 1):-1]
    recent_high = float(_prior_window.max()) if len(_prior_window) > 0 else today_close
    is_new_high = today_close >= recent_high - 1e-9

    if not attack_bar or not attack_bar.get('volume'):
        return {
            "verdict": "unknown", "label": "無攻擊基準",
            "is_new_high": is_new_high, "ratio_pct": None,
            "attack_volume": None, "today_volume": today_vol,
            "detail": "近20個交易日內找不到符合條件的攻擊起漲點（爆量+收紅），量能比較沒有基準可用。",
        }

    attack_vol = attack_bar['volume']
    ratio_pct = round((today_vol / attack_vol) * 100, 1) if attack_vol > 0 else None
    # 【R96新增，可驗證性】把攻擊K棒的日期附進detail——總指揮官反映需要能
    # 對照K線圖親眼確認「找的攻擊K棒對不對」，光看百分比數字沒辦法驗證，
    # 附上日期後可以直接翻K線圖那一天核對。
    _ab_date = attack_bar.get('date')
    _ab_date_str = _ab_date.strftime('%m/%d') if hasattr(_ab_date, 'strftime') else str(_ab_date or '')

    if not is_new_high:
        return {
            "verdict": "neutral", "label": "非創新高",
            "is_new_high": False, "ratio_pct": ratio_pct,
            "attack_volume": attack_vol, "today_volume": today_vol,
            "attack_bar_date": _ab_date_str,
            "detail": f"今天沒有創近{new_high_window}個交易日新高，這一關的判斷前提是創新高，先不適用。"
                      f"（比對基準：{_ab_date_str}攻擊K棒）",
        }

    if ratio_pct is not None and ratio_pct >= 80:
        verdict, label = "strong", "量能達標"
        detail = f"創新高，成交量達攻擊量的{ratio_pct}%（≥80%），有新資金進場，趨勢健康。（攻擊K棒：{_ab_date_str}）"
    elif ratio_pct is not None and ratio_pct < 50:
        verdict, label = "weak", "量能不足"
        detail = f"創新高，但成交量只有攻擊量的{ratio_pct}%（<50%），沒人願意高檔承接，隨時可能拉回。（攻擊K棒：{_ab_date_str}）"
    else:
        verdict, label = "neutral", "量能中段"
        detail = f"創新高，成交量為攻擊量的{ratio_pct}%，介於50%-80%之間，續觀察。（攻擊K棒：{_ab_date_str}）"

    return {
        "verdict": verdict, "label": label, "is_new_high": is_new_high,
        "ratio_pct": ratio_pct, "attack_volume": attack_vol, "today_volume": today_vol,
        "attack_bar_date": _ab_date_str,
        "detail": detail,
    }


# ==============================================================================
# 三之三、拉回體檢母關（R96新增——策略框架圖整合 Step 3，合併新A-1盤中版
# 與新B-1波段版，用 mode 參數切換，不重複寫兩套邏輯）
# ==============================================================================
def evaluate_pullback_health(hist, attack_bar=None, mode='swing'):
    """
    拉回體檢母關：合併策略框架圖裡兩個原本分開的關卡——

    mode='swing'（依新B-1「攻擊後的拉回位置」，日線/波段適用）：
      拉回守在攻擊K棒(High-Low範圍)一半以上位置 → 合格續抱
      拉回跌破攻擊K棒範圍的三分之一，甚至跌破起漲點 → 不合格出場

    mode='intraday'（依新A-1「早盤第一波攻擊的續航力」，盤中/5分K適用，
    這裡先用日線資料的粗略版本；等5分K第二階段整合後，可以直接把5分K的
    hist餵進來，函式邏輯不用改）：
      拉回量縮到攻擊量的三分之一以下 + 不破起漲點 → 合格續抱
      拉回量增到攻擊量以上，或跌破起漲點 → 不合格出場

    兩種模式共用同一套「起漲點」定義：攻擊K棒本身的最低點（find_attack_bar
    找到的那根K棒的low），這是這一波攻擊真正發動的位置，跌破它代表這一波
    攻擊已經完全被收復、行情假設不成立。

    attack_bar: find_attack_bar()的回傳值，未提供時這裡會自動找一次（排除
    今天自己，理由跟evaluate_volume_followthrough一致——攻擊必須是「之前」
    發生的事，不能是今天自己）。找不到攻擊K棒，或攻擊K棒本身就是最新一根
    （代表還沒有任何拉回可以體檢），都誠實回傳verdict='unknown'，不硬湊
    答案。

    回傳 dict：{verdict, label, mode, price_pct, vol_ratio_pct, breaks_start,
    attack_volume, detail}
    """
    if hist is None or len(hist) < 6:
        return None
    if attack_bar is None:
        attack_bar = find_attack_bar(hist.iloc[:-1])
    if not attack_bar:
        return {
            "verdict": "unknown", "label": "無攻擊基準", "mode": mode,
            "price_pct": None, "vol_ratio_pct": None, "breaks_start": None,
            "attack_volume": None,
            "detail": "近20個交易日內找不到符合條件的攻擊起漲點（爆量+收紅），拉回體檢沒有基準可用。",
        }

    pos = attack_bar['position']
    pullback_bars = hist.iloc[pos + 1:]
    if pullback_bars.empty:
        return {
            "verdict": "unknown", "label": "尚無拉回", "mode": mode,
            "price_pct": None, "vol_ratio_pct": None, "breaks_start": None,
            "attack_volume": attack_bar['volume'],
            "detail": "攻擊K棒就是最新一根K棒，還沒有拉回可以體檢，之後再看。",
        }

    today_close = float(hist['Close'].iloc[-1])
    attack_vol = attack_bar['volume']
    attack_low = attack_bar['low']     # 起漲點的代理：攻擊K棒本身的最低點
    attack_high = attack_bar['high']
    attack_range = attack_high - attack_low
    # 【R96新增，可驗證性】攻擊K棒日期，理由跟evaluate_volume_followthrough
    # 一致——附上日期才能對照K線圖親眼驗證找的攻擊K棒對不對。
    _ab_date = attack_bar.get('date')
    _ab_date_str = _ab_date.strftime('%m/%d') if hasattr(_ab_date, 'strftime') else str(_ab_date or '')

    pullback_avg_vol = float(pullback_bars['Volume'].mean())
    vol_ratio_pct = round((pullback_avg_vol / attack_vol) * 100, 1) if attack_vol > 0 else None
    breaks_start = bool(today_close < attack_low)
    price_pct = round((today_close - attack_low) / attack_range * 100, 1) if attack_range > 0 else None

    if mode == 'intraday':
        vol_healthy = (vol_ratio_pct is not None and vol_ratio_pct < 33.3)
        vol_fail = (vol_ratio_pct is not None and vol_ratio_pct >= 100)
        if vol_healthy and not breaks_start:
            verdict, label = "strong", "續抱合格"
            detail = f"攻擊後拉回量縮至攻擊量的{vol_ratio_pct}%（<三分之一），且未跌破起漲點，健康。（攻擊K棒：{_ab_date_str}）"
        elif vol_fail or breaks_start:
            verdict, label = "weak", "出場訊號"
            _reasons = []
            if vol_fail:
                _reasons.append(f"拉回量增至攻擊量的{vol_ratio_pct}%（≥100%）")
            if breaks_start:
                _reasons.append("跌破起漲點")
            detail = "、".join(_reasons) + f"，不合格，該走就走。（攻擊K棒：{_ab_date_str}）"
        else:
            verdict, label = "neutral", "中段觀察"
            _vt = f"{vol_ratio_pct}%" if vol_ratio_pct is not None else "—"
            detail = f"拉回量為攻擊量的{_vt}，介於健康與警戒之間，續觀察。（攻擊K棒：{_ab_date_str}）"
    else:
        if price_pct is None:
            return {
                "verdict": "unknown", "label": "無法判斷", "mode": mode,
                "price_pct": None, "vol_ratio_pct": vol_ratio_pct, "breaks_start": breaks_start,
                "attack_volume": attack_vol, "attack_bar_date": _ab_date_str,
                "detail": "攻擊K棒高低相等，無法計算拉回位置。",
            }
        if price_pct >= 50 and not breaks_start:
            verdict, label = "strong", "續抱合格"
            detail = f"拉回守在攻擊K棒{price_pct}%位置（≥一半），續抱。（攻擊K棒：{_ab_date_str}）"
        elif price_pct < 33.3 or breaks_start:
            verdict, label = "weak", "出場訊號"
            _reason = "跌破起漲點" if breaks_start else f"拉回跌到攻擊K棒{price_pct}%位置（跌破三分之一）"
            detail = f"{_reason}，不合格，該走就走。（攻擊K棒：{_ab_date_str}）"
        else:
            verdict, label = "neutral", "中段觀察"
            detail = f"拉回在攻擊K棒{price_pct}%位置，介於三分之一到一半之間，續觀察。（攻擊K棒：{_ab_date_str}）"

    return {
        "verdict": verdict, "label": label, "mode": mode,
        "price_pct": price_pct, "vol_ratio_pct": vol_ratio_pct, "breaks_start": breaks_start,
        "attack_volume": attack_vol, "attack_bar_date": _ab_date_str, "detail": detail,
    }


# ==============================================================================
# 三之四、時段自動選關（R96新增，Step 4／新框架A組：當日續抱時間軸，
# 跟5分K獨立的另一條軸線）
# ==============================================================================
def determine_active_intraday_gate(now=None):
    """
    時段自動選關：依台灣現在時間，判斷現在該顯示「當日續抱時間軸」（策略
    框架圖新框架A組）裡的哪一關。

    時段劃分（台灣時間，交易日 09:00-13:30）：
      09:00-09:15  pre_first_wave  早盤試搓期，還沒到第一關判斷時間
      09:15-09:30  first_wave      第一關：早盤第一波攻擊的續航力（新A-1）
      09:30-10:00  between_1_2     第一關已過、第二關還沒到
      10:00-10:15  second_confirm  第二關：10:00二次表態（新A-2）
      10:15-13:00  intraday        盤中即時：五檔掛單節奏（新A-3，對應Step 5）
      13:00-13:30  pre_close       收盤前30分：收盤強弱（對應Step 1，已可用）
      其餘（非交易時段）  closed

    【重要，誠實標注現況】available=True代表這一關的判斷邏輯現在真的能跑；
    False代表「現在是該顯示這一關的時間點」，但底層判斷邏輯還沒接上——
    目前只有pre_close（收盤強弱，Step 1）是真正可用的，first_wave／
    second_confirm需要盤中5分K的判斷邏輯（5分K第二階段，還沒開始寫），
    intraday需要五檔/內外盤逐筆資料（Step 5，還沒接資料源）。這裡不假裝
    這些關卡已經做好，讓UI端可以誠實顯示「這關的時間到了，但功能還沒
    接上」，而不是靜默顯示錯誤或空白。

    now: 呼叫端可以自行傳進「台灣時間」的datetime；留None時預設用
    datetime.now(TAIPEI_TZ)，明確取得正確時區的當下時間，不管執行環境
    本身時鐘是UTC還是別的時區都不受影響。

    【R97修復，見開發歷程.md時區bug章節】原本這裡留None時用不帶時區的
    datetime.now(TAIPEI_TZ)，Streamlit Cloud系統時鐘是UTC，導致這一關的時間軸判斷
    在真正的台灣盤中時間（09:00-13:30，對應UTC 01:00-05:30）永遠誤判成
    「非交易時段」——這是R96新增的功能，新增時漏掉了同一輪本該套用的
    時區修法，這輪一併補上。

    回傳 dict：{gate, label, available, note}
    """
    if now is None:
        now = datetime.now(TAIPEI_TZ)
    t = now.time()
    from datetime import time as _time

    if t < _time(9, 0) or t >= _time(13, 30):
        return {"gate": "closed", "label": "非交易時段", "available": False,
                "note": "現在不是台股交易時間（09:00-13:30），當日續抱時間軸不適用。"}
    if t < _time(9, 15):
        return {"gate": "pre_first_wave", "label": "早盤試搓期", "available": False,
                "note": "09:00-09:15是試搓階段，還沒到第一關（09:15-09:30續航力）判斷時間。"}
    if t < _time(9, 30):
        return {"gate": "first_wave", "label": "第一關：早盤第一波攻擊續航力", "available": False,
                "note": "依策略框架圖新A-1：拉回量縮<攻擊量三分之一、不破起漲點→合格續抱；"
                        "拉回量增≥攻擊量、跌破起漲點→不合格出場。這一關需要盤中5分K資料，"
                        "5分K第二階段（判斷邏輯）還沒接上，暫時只顯示時段提示。"}
    if t < _time(10, 0):
        return {"gate": "between_1_2", "label": "第一關已過，等待第二關", "available": False,
                "note": "09:30-10:00之間，第一關時間已過、第二關（10:00二次表態）還沒到。"}
    if t < _time(10, 15):
        return {"gate": "second_confirm", "label": "第二關：10:00二次表態", "available": False,
                "note": "依策略框架圖新A-2：10:00盤中二次確認是否守住、量縮。這一關同樣需要"
                        "盤中5分K資料，判斷邏輯還沒接上。"}
    if t < _time(13, 0):
        # 【R97修復，見開發歷程.md「狀態文字過時排查」章節】這段原本寫
        # available=False+「資料源尚未接上」，但查證後evaluate_order_book_
        # pressure()(Step 5)其實R96就已經接上真實五檔掛單+內外盤成交
        # 資料在算了(warroom_v160.py的attach_live_quotes()裡，c['order_book']
        # = evaluate_order_book_pressure(_bids, _asks, ...)，用的是
        # fetch_twse_mis_batch()真實回傳的bids/asks，不是假資料)。這是
        # 「功能做好了、但這裡的狀態說明文字沒有跟著更新」的同一種疏漏，
        # 這次一併修正，不再誤導總指揮官以為這一關還沒做。
        return {"gate": "intraday", "label": "盤中即時：五檔掛單節奏", "available": True,
                "note": "依策略框架圖新A-3：買盤墊高+外盤成交=真買；買盤厚但內盤大單=偷出貨。"
                        "這一關已經接上真實五檔/內外盤資料，請到個股戰卡查看「五檔買盤結構」"
                        "區塊的即時判斷，這裡的時間軸只是提示現在該看哪一關，不是重複顯示判斷結果。"}
    return {"gate": "pre_close", "label": "收盤前30分：收盤強弱", "available": True,
            "note": "這一關已經可用——見戰卡上的「收盤強弱」區塊（Step 1）。"}


# ==============================================================================
# 三之五、五檔買盤結構判斷（R96新增，Step 5／附件38）——fetch_twse_
# mis_batch()本身就免費附帶五檔資料，這輪把它解析出來。
# ==============================================================================
def evaluate_order_book_pressure(bids, asks, prev_bids=None, outer_volume=None, inner_volume=None):
    """
    五檔買盤結構判斷（依策略框架圖新A-3／附件38：外盤內盤的買盤結構）。

    【R96更新——內外盤成交比率已接上，補完框架規則的完整判斷】原本這裡
    只能判斷「買盤掛單厚不厚」，現在加上outer_volume/inner_volume（來自
    aggregate_intraday_snapshots_to_bars()用tick rule逐筆分類累加的外盤/
    內盤成交量，跟5分K同一套輪詢基礎建設，不多打API），可以完整判斷
    框架規則的兩種關鍵情境：
      買盤掛單墊高 + 外盤成交為主 → 真買，主力真的在買
      買盤掛單雖厚 + 內盤成交為主 → 偷出貨，主力掛買單撐盤面、實際卻在
      倒貨（俗稱「假買盤真出貨」）
    outer_volume/inner_volume留None時（呼叫端還沒接上這層資料，或是
    Step5獨立呼叫的舊路徑），退回原本只看掛單厚度的判斷，data_completeness
    標記'partial'誠實區分；兩者都有提供且加總>0時，data_completeness
    升級成'full'，判斷結論也會反映外內盤成交的確認結果。

    bids/asks: fetch_twse_mis_batch()回傳的bids/asks，[(price, volume), ...]，
    最多5筆，volume單位跟該端點原始欄位一致（張）。
    prev_bids: 上一次快照的bids（選填）。有提供才能額外判斷「買盤是不是在
    墊高」（這次委買總量是否比上次明顯增加）；不提供就只看這一次快照的
    靜態厚度，不判斷趨勢。
    outer_volume/inner_volume: 累計外盤/內盤成交量（張），通常是今天累計
    到目前為止的加總，不是單一根K棒的量——呼叫端自行決定要傳累計還是
    近期一段時間的加總。

    回傳 dict：{verdict, label, bid_depth, ask_depth, depth_ratio,
    is_thickening, outer_inner_ratio, data_completeness, detail}
    data_completeness：'none'（完全沒五檔資料）／'partial'（只有掛單厚度）／
    'full'（掛單厚度+外內盤成交比率都有）。
    """
    if not bids or not asks:
        return {
            "verdict": "unknown", "label": "無五檔資料",
            "bid_depth": None, "ask_depth": None, "depth_ratio": None,
            "is_thickening": None, "outer_inner_ratio": None, "data_completeness": "none",
            "detail": "這次快照沒有取得五檔委買/委賣資料，可能是非交易時段或該檔暫無掛單。",
        }

    bid_depth = round(sum(v for _, v in bids), 1)
    ask_depth = round(sum(v for _, v in asks), 1)
    depth_ratio = round(bid_depth / ask_depth, 2) if ask_depth > 0 else None

    is_thickening = None
    if prev_bids:
        prev_depth = sum(v for _, v in prev_bids)
        if prev_depth > 0:
            # 厚度要明顯增加(>5%)才算「墊高」，避免正常報價跳動的雜訊被
            # 誤判成有意義的趨勢變化。
            is_thickening = bool(bid_depth > prev_depth * 1.05)

    has_flow_data = bool(outer_volume is not None and inner_volume is not None
                         and (outer_volume + inner_volume) > 0)
    outer_inner_ratio = None
    is_outer_led = None
    if has_flow_data:
        _total_flow = outer_volume + inner_volume
        outer_inner_ratio = round(outer_volume / inner_volume, 2) if inner_volume > 0 else None
        # 外盤成交佔比>=55%算「外盤為主」，<=45%算「內盤為主」，中間算
        # 均衡——門檻不用跟depth_ratio的1.5倍一樣嚴，因為這裡是連續累計量
        # 的佔比，天然就會比單次五檔厚度快照更平滑穩定。
        _outer_pct = outer_volume / _total_flow
        is_outer_led = True if _outer_pct >= 0.55 else (False if _outer_pct <= 0.45 else None)

    depth_thick = depth_ratio is not None and depth_ratio >= 1.5
    depth_thin = depth_ratio is not None and depth_ratio <= 0.67

    if has_flow_data and depth_thick and is_outer_led is True:
        verdict, label = "strong", "買盤墊高+外盤主買，真買"
        detail = (f"五檔委買是委賣的{depth_ratio}倍，且累計外盤成交佔{_outer_pct*100:.0f}%，"
                  f"掛單墊高有真實買盤成交確認，主力真的在買，可信度高。")
    elif has_flow_data and depth_thick and is_outer_led is False:
        verdict, label = "weak", "買盤雖厚但內盤主導，疑似偷出貨"
        detail = (f"五檔委買是委賣的{depth_ratio}倍看似買盤強，但累計成交卻有"
                  f"{(1-_outer_pct)*100:.0f}%是打在買價成交（內盤主導）——這是主力掛買單"
                  f"撐盤面、實際卻在倒貨的典型型態，掛單厚度不能盡信，留意風險。")
    elif depth_thick:
        verdict, label = "strong", "買盤掛單墊高"
        detail = f"五檔委買總量是委賣的{depth_ratio}倍，買盤結構偏厚。"
    elif depth_thin:
        verdict, label = "weak", "賣盤掛單較重"
        detail = f"五檔委買總量只有委賣的{depth_ratio}倍，賣盤結構偏重。"
    else:
        verdict, label = "neutral", "買賣掛單均衡"
        detail = f"五檔委買/委賣量大致均衡（比例{depth_ratio}）。"

    if has_flow_data:
        if not (depth_thick and is_outer_led is not None):
            detail += (f" 累計外盤成交佔{_outer_pct*100:.0f}%"
                      f"（{'外盤主導' if is_outer_led is True else ('內盤主導' if is_outer_led is False else '均衡')}）。")
    else:
        detail += ("⚠️ 這個判斷只涵蓋「掛單厚度」，還沒涵蓋「成交是打在買價還是"
                   "賣價」（外盤/內盤成交比率）——那部分需要連續追蹤報價，這次"
                   "呼叫沒有提供這層資料，判斷還不完整，僅供參考，不要單獨"
                   "依賴這個判斷做進出場決定。")

    return {
        "verdict": verdict, "label": label,
        "bid_depth": bid_depth, "ask_depth": ask_depth, "depth_ratio": depth_ratio,
        "is_thickening": is_thickening, "outer_inner_ratio": outer_inner_ratio,
        "data_completeness": "full" if has_flow_data else "partial",
        "detail": detail,
    }


# ==============================================================================
# 三之五之一、趨勢/趨勢中休息/盤整三態分類 + RSI雙版本判斷（R96新增，
# 詳見開發歷程.md）
# ==============================================================================
def classify_trend_regime(ma5, ma20, ma60, hist=None, lookback=120,
                           advance_threshold_pct=15.0, max_retracement_pct=50.0):
    """
    三態趨勢/盤整分類（完整版）。

    【第一層】短期均線糾結判斷——沿用「查9.均線糾結爆量突破」已經校準過
    的糾結門檻定義：MA5/20/60三線 (最高-最低)/最低 < 5% 視為「糾結」。
    這裡刻意跟ma_compression_breakout用同一個5%門檻，不另外發明新標準，
    避免「均線糾結」這個概念在系統裡有兩套定義互相打架（之後查9的門檻
    如果校準出更好的數字，這裡也要跟著調整，兩處保持一致）。
    不糾結 → 直接回傳'trending'，不需要看長期結構。

    【第二層，只在短期糾結時才需要】用hist(近lookback天的OHLC)判斷這次
    糾結，是「真正沒方向的橫盤」還是「大趨勢中的健康休息」：
    在lookback天(預設120天，約半年)的視窗裡，算「視窗內最高價 vs 最低價」
    的漲幅(advance_pct)，代表這段期間有沒有走出過一段像樣的趨勢；再算
    「這波漲幅本身被回吃了多少」(retracement_pct，用費波納契回撤算法：
    回檔金額 ÷ 這波漲幅本身，不是除以最高價本身，避免高基期股票的
    回檔比例失真)，代表這段漲幅
    有沒有被大部分吃回去（吃回去太多，代表趨勢可能已經真的反轉，不是
    單純休息）。
      advance_pct >= advance_threshold_pct(預設15%)
      且 retracement_pct <= max_retracement_pct(預設50%，還沒吃回去超過
      一半的漲幅) → 'trend_resting'（趨勢中休息：短期均線糾結，但長期
      還在大趨勢的休息階段，不建議套用均值回歸邏輯去搶短期超賣，該當成
      「等重新表態」）
      其餘情況（沒有出現過像樣漲幅，或漲幅已經被吃回大半）→ 'ranging'
      （真正的橫盤，均值回歸邏輯適用對象）

    hist=None，或資料不足(len(hist)<20)時，糾結但無法判斷第二層長期結構，
    保守回傳'ranging'——沒有證據支持「這是趨勢中休息」，寧可當一般盤整
    處理，不假設它有更好的大趨勢背景（缺資料時不該給比較寬鬆的判定）。

    回傳 'trending' / 'trend_resting' / 'ranging' 三選一；任一均線缺值或
    非正值時回傳None，呼叫端要自行決定缺值時的預設行為（不硬猜）。
    """
    if ma5 is None or ma20 is None or ma60 is None:
        return None
    if ma5 <= 0 or ma20 <= 0 or ma60 <= 0:
        return None
    vals = [ma5, ma20, ma60]
    compression = (max(vals) - min(vals)) / min(vals)
    if compression >= 0.05:
        return 'trending'

    # 短期均線糾結，進一步判斷是「真盤整」還是「趨勢中休息」
    if hist is None or len(hist) < 20 or 'High' not in hist.columns or 'Low' not in hist.columns:
        return 'ranging'

    _window = hist.tail(min(lookback, len(hist)))
    window_high = float(_window['High'].max())
    window_low = float(_window['Low'].min())
    if window_low <= 0 or window_high <= 0:
        return 'ranging'

    advance_pct = (window_high - window_low) / window_low * 100
    curr_price = float(hist['Close'].iloc[-1])
    # 回檔幅度用「這波漲幅本身」當分母(費波納契回撤算法)，不是除以最高價
    # 本身——後者在高基期股票上會失真，見開發歷程.md的詳細案例說明。
    _advance_amount = window_high - window_low
    retracement_pct = ((window_high - curr_price) / _advance_amount * 100) if _advance_amount > 0 else 100

    if advance_pct >= advance_threshold_pct and retracement_pct <= max_retracement_pct:
        return 'trend_resting'
    return 'ranging'


def evaluate_rsi_dual_mode(rsi_val, rsi_prev=None, regime=None):
    """
    RSI雙版本判斷——依classify_trend_regime()判斷出的regime，切換兩套完全
    不同（甚至相反）的RSI判斷哲學：

    regime='trending'（動能追蹤版，依附件06）：
      RSI>50 且比前一日高（上升）→ strong，多頭力道增強
      RSI<50，或比前一日低（下降）→ weak，空頭佔優
      其餘（=50或持平、缺前一日資料）→ neutral

    regime='trend_resting'（第三態，總指揮官這輪要求新增：趨勢中休息，
    介於動能追蹤版跟均值回歸版之間，刻意不套用兩邊任一邊的極端判斷）：
      本質是趨勢股，只是短期在整理消化——這時RSI偏低不代表「超賣可以
      搶反彈」（均值回歸版會誤導你搶短期買點，但真正的大浪還沒回來，
      容易套在半山腰），RSI偏高也不代表「動能已經確認」（trending版
      會太早給strong訊號，實際上還在整理、隨時可能再次拉回測試）。
      RSI>50 且上升 → strong，但標註「休息後重新表態」（比trending版
      更保守的說法，因為還沒脫離整理區）
      RSI<40（拉回明顯加深，比trending版的<50門檻更寬容，允許整理期
      RSI合理地低於50而不觸發警訊）→ weak，標註「拉回加深，留意是否
      真的轉弱」（這是唯一該提高警覺的情況——如果休息期RSI持續破底，
      可能代表這次不是單純休息，是真的要轉弱了）
      其餘（40~50之間，或50以上但沒有上升）→ neutral，「整理中，等
      表態」——這是這個regime裡最常見的狀態，誠實反映「現在還看不出
      答案，該觀望」，不勉強給方向。

    regime='ranging'（均值回歸版，我們原本的邏輯，維持不變）：
      RSI>70 → weak，過熱，有回檔風險
      RSI<30 → strong，超賣，有反彈機會
      其餘（30~70中性區） → neutral

    regime=None或無法判斷（例如均線缺值）：兩套都不套用，誠實回傳
    verdict='neutral'、label='無法判斷'，不假裝有答案。

    rsi_prev：前一日RSI值，選填——只有trending/trend_resting模式判斷
    「上升/下降」時需要，ranging模式不需要這個參數。

    回傳 dict：{verdict, label, regime, detail}
    """
    if rsi_val is None:
        return None
    rsi_val = float(rsi_val)

    if regime == 'trending':
        is_rising = (rsi_prev is not None and rsi_val > float(rsi_prev))
        is_falling = (rsi_prev is not None and rsi_val < float(rsi_prev))
        if rsi_val > 50 and is_rising:
            return {"verdict": "strong", "label": "多頭力道增強", "regime": regime,
                    "detail": f"RSI {rsi_val:.0f}（動能追蹤版）：>50且比前一日上升，多頭力道增強。"}
        if rsi_val < 50 or is_falling:
            return {"verdict": "weak", "label": "空頭佔優", "regime": regime,
                    "detail": f"RSI {rsi_val:.0f}（動能追蹤版）：<50或比前一日下降，空頭佔優。"}
        return {"verdict": "neutral", "label": "中性", "regime": regime,
                "detail": f"RSI {rsi_val:.0f}（動能追蹤版）：方向不明確，續觀察。"}

    if regime == 'trend_resting':
        is_rising = (rsi_prev is not None and rsi_val > float(rsi_prev))
        if rsi_val > 50 and is_rising:
            return {"verdict": "strong", "label": "休息後重新表態", "regime": regime,
                    "detail": f"RSI {rsi_val:.0f}（趨勢休息版）：>50且上升，可能結束整理、重新表態。"}
        if rsi_val < 40:
            return {"verdict": "weak", "label": "拉回加深，留意轉弱", "regime": regime,
                    "detail": f"RSI {rsi_val:.0f}（趨勢休息版）：<40，拉回比正常整理更深，"
                              f"留意這次是不是真的要轉弱，不只是單純休息。"}
        return {"verdict": "neutral", "label": "整理中，等表態", "regime": regime,
                "detail": f"RSI {rsi_val:.0f}（趨勢休息版）：仍在整理消化，方向還沒確認，觀望為主。"}

    if regime == 'ranging':
        if rsi_val > 70:
            return {"verdict": "weak", "label": "過熱警示", "regime": regime,
                    "detail": f"RSI {rsi_val:.0f}（均值回歸版）：>70過熱，有回檔風險。"}
        if rsi_val < 30:
            return {"verdict": "strong", "label": "超賣機會", "regime": regime,
                    "detail": f"RSI {rsi_val:.0f}（均值回歸版）：<30超賣，有反彈機會。"}
        return {"verdict": "neutral", "label": "中性", "regime": regime,
                "detail": f"RSI {rsi_val:.0f}（均值回歸版）：落在30-70中性區間。"}

    return {"verdict": "neutral", "label": "無法判斷", "regime": None,
            "detail": "均線資料不足，無法判斷目前是趨勢股還是盤整股，RSI暫不判斷。"}


# ==============================================================================
# 三之六、產業分類/固定龍頭對照表（R96新增）——從warroom_v160.py搬出核心
# 版本，供不能有Streamlit依賴的system_scheduler.py共用，單一事實來源。
# ==============================================================================
FIXED_INDUSTRY_LEADERS = {
    "半導體業": ("2330", "台積電"),
    "電腦及週邊設備業": ("2382", "廣達"),
    "光電業": ("3008", "大立光"),
    "通信網路業": ("2412", "中華電"),
    "電子零組件業": ("2308", "台達電"),
    "電子通路業": ("3702", "大聯大"),
    "其他電子業": ("2317", "鴻海"),
    "金融保險業": ("2881", "富邦金"),
    "塑膠工業": ("1301", "台塑"),
    "鋼鐵工業": ("2002", "中鋼"),
    "汽車工業": ("2207", "和泰車"),
    "航運業": ("2603", "長榮"),
    "食品工業": ("1216", "統一"),
    "水泥工業": ("1101", "台泥"),
    "貿易百貨": ("2912", "統一超"),
    "橡膠工業": ("2105", "正新"),
    "電器電纜": ("1605", "華新"),
}


def fetch_industry_map_raw():
    """
    【R96搬移自warroom_v160.py的fetch_industry_map】用FinMind TaiwanStockInfo
    一次性批次拉取產業分類——這裡是「不含Streamlit快取裝飾器」的核心版本，
    warroom_v160.py的fetch_industry_map()改成薄薄一層@st.cache_data包裝這裡，
    system_scheduler.py（排程端）直接呼叫這個版本，兩邊共用同一份抓取邏輯，
    不重複維護。

    回傳 (stock_to_industry, industry_to_stocks) 兩個字典；查詢失敗回傳
    ({}, {})，不編造資料。
    """
    url = 'https://api.finmindtrade.com/api/v4/data'
    try:
        payload = _finmind_get(url, {'dataset': 'TaiwanStockInfo'}, max_retries=2, timeout=20)
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
    except Exception as e:
        print(f"[fetch_industry_map_raw-診斷] 抓產業分類失敗：{type(e).__name__}: {e}")
        return {}, {}


def get_industry_leader_for_symbol(symbol, stock_to_ind=None):
    """
    【R96新增】給一個股票代號，查它的固定龍頭代號——5分K三關第二關要用，
    system_scheduler.py排程端專用的輕量版（只查FIXED_INDUSTRY_LEADERS這份
    固定表，不像warroom_v160.py的get_industry_leader_proxy()還有「查不到
    固定表就動態算成交值最高同業」那層退回邏輯——排程端要的是穩定、
    低成本、不製造額外API爆量請求的版本，查不到固定表就誠實回傳None，
    不動態延伸查詢）。

    stock_to_ind: fetch_industry_map_raw()回傳的stock_to_industry字典，
    留None時這裡會自己呼叫一次（多一次API成本，呼叫端如果已經有這份
    對照表，建議傳進來重複使用）。

    回傳 (leader_code, leader_name) 或 (None, None)。自己就是龍頭時
    （symbol本身剛好是它產業的固定龍頭）也回傳(None, None)，跟
    get_industry_leader_proxy()的exclude_code邏輯一致——「自己是不是
    自己的龍頭」沒有意義。
    """
    if stock_to_ind is None:
        stock_to_ind, _ = fetch_industry_map_raw()
    ind = stock_to_ind.get(symbol)
    if not ind or ind not in FIXED_INDUSTRY_LEADERS:
        return None, None
    leader_code, leader_name = FIXED_INDUSTRY_LEADERS[ind]
    if leader_code == symbol:
        return None, None
    return leader_code, leader_name


# ==============================================================================
# 三之七、5分K三關（查15，Step 3，R96新增）——第一關9:30量價配合、第二關
# 族群內個股強弱、第三關拉回量價，循序判斷，第三關複用swing版拉回體檢。
# ==============================================================================
def bars_to_hist_df(bars):
    """
    把intraday_5min_bars資料表查出來的一批列（dict或Row，需要有bar_time/
    open/high/low/close/volume欄位）轉成find_attack_bar()/
    evaluate_pullback_health()吃的DataFrame格式（Open/High/Low/Close/Volume
    欄位，依bar_time排序，索引是bar_time字串）。

    bars: list of dict，例如Supabase查詢回來的.data。缺值的列（open為None，
    代表那根K棒輪詢期間完全沒抓到樣本）會被跳過，不強行塞入NaN造成後續
    計算出錯。

    回傳pandas.DataFrame，沒有任何有效列時回傳空DataFrame（呼叫端既有的
    「len(hist)<6直接回None」防呆會自然接住這個情況）。
    """
    rows = []
    for b in sorted(bars, key=lambda x: x.get('bar_time', '')):
        o, h, l, c, v = b.get('open'), b.get('high'), b.get('low'), b.get('close'), b.get('volume')
        if o is None or h is None or l is None or c is None:
            continue
        rows.append({'bar_time': b.get('bar_time'), 'Open': o, 'High': h, 'Low': l,
                     'Close': c, 'Volume': v if v is not None else 0.0})
    if not rows:
        return pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume'])
    df = pd.DataFrame(rows).set_index('bar_time')
    return df


def evaluate_930_gate1(bars):
    """
    5分K三關·第一關：9:30量價配合——今天方向出來了沒有？（依附件17完整版，
    累積清單第3項：升級成5態輸出，取代原本pass/fail二分）

    【R96升級】原本只有pass/fail二分，依附件20/21簡化版設計。這輪重新
    核對附件17（同一關的完整版）才發現原作者實際上分5種狀態，不是二分：
      強勢多方：實體長紅，量≥前一根1.5倍 → 續抱/加碼
      弱勢多方：小紅或帶上影線，量放大或持平 → 觀察/不追高
      弱勢空方：小黑或帶下影線，量放大或持平 → 減碼/觀望
      強勢空方：實體長黑，量≥前一根1.5倍 → 出場/停損
      多空不明：十字線或極小K，縮量或不穩定 → 觀望為主
    二分版會把「弱勢多方(觀察)」跟「多空不明(觀望)」都判成同一個fail，
    但原作者明確區分這兩種處置不一樣——這裡改成5態，不再壓縮成二分。

    實體大小判斷：body_ratio = |收盤-開盤| / (最高-最低)，>=60%算「長」，
    否則算「小」；range本身極小或body_ratio<15%算「十字線/不明」。
    量能：vol_ratio_pct(這根量相對前一根的%) >=150%算「強」，>=80%算
    「放大或持平」，<50%算「縮量不穩」。
    跌破開盤低點時，不管K棒本身長相，直接判定至少是空方（強弱依量能
    決定），因為跌破低點是原始2關版就強調的關鍵確認，不因為升級成5態
    就丟掉這個判斷。

    bars: bars_to_hist_df()整理過的DataFrame，或原始list of dict皆可。

    回傳 dict：{verdict, label, action, vol_ratio_pct, detail}
    verdict固定是 'strong_bull'/'weak_bull'/'weak_bear'/'strong_bear'/
    'unclear' 五選一（找不到資料時是'unknown'，這是第六種，代表資料
    不足，跟'unclear'的「方向不明」意義不同，不要混用）。
    """
    df = bars if isinstance(bars, pd.DataFrame) else bars_to_hist_df(bars)
    if '09:25' not in df.index or '09:30' not in df.index:
        return {"verdict": "unknown", "label": "資料不足", "action": "等待資料",
                "vol_ratio_pct": None,
                "detail": "09:25或09:30這兩根5分K還沒收集到有效資料，無法判斷第一關。"}

    b25, b30 = df.loc['09:25'], df.loc['09:30']
    body = abs(b30['Close'] - b30['Open'])
    day_range = b30['High'] - b30['Low']
    body_ratio = (body / day_range) if day_range > 0 else 0.0
    is_red = bool(b30['Close'] > b30['Open'])
    is_black = bool(b30['Close'] < b30['Open'])
    is_long_body = bool(body_ratio >= 0.6)
    is_doji = bool(day_range <= 0 or body_ratio < 0.15)

    vol_ratio_pct = round((b30['Volume'] / b25['Volume']) * 100, 1) if b25['Volume'] > 0 else None
    vol_strong = bool(vol_ratio_pct is not None and vol_ratio_pct >= 150)
    vol_ok = bool(vol_ratio_pct is not None and vol_ratio_pct >= 80)
    vol_thin = bool(vol_ratio_pct is not None and vol_ratio_pct < 50)

    open_low_proxy = min(b25['Low'], b30['Low'])
    breaks_open_low = bool(b30['Close'] < open_low_proxy)

    _vt = f"{vol_ratio_pct}%" if vol_ratio_pct is not None else "—"

    if is_doji or vol_thin:
        return {"verdict": "unclear", "label": "多空不明", "action": "觀望為主",
                "vol_ratio_pct": vol_ratio_pct,
                "detail": f"9:30十字線或極小K棒（實體佔比{body_ratio*100:.0f}%），"
                          f"或量能縮到{_vt}明顯不穩定，方向未明，觀望為主。"}

    if breaks_open_low or is_black:
        if is_long_body and vol_strong and not is_red:
            return {"verdict": "strong_bear", "label": "強勢空方", "action": "出場/停損",
                    "vol_ratio_pct": vol_ratio_pct,
                    "detail": f"9:30實體長黑，量能達前一根的{_vt}（≥150%），空方控盤，"
                              f"今天方向偏空，手上的多單該走了。"}
        return {"verdict": "weak_bear", "label": "弱勢空方", "action": "減碼/觀望",
                "vol_ratio_pct": vol_ratio_pct,
                "detail": f"9:30小黑或跌破開盤低點，量能{_vt}放大或持平，空方有壓力但力道不強，"
                          f"減碼觀望，不建議續抱。"}

    if is_red:
        if is_long_body and vol_strong:
            return {"verdict": "strong_bull", "label": "強勢多方", "action": "續抱/加碼",
                    "vol_ratio_pct": vol_ratio_pct,
                    "detail": f"9:30實體長紅，量能達前一根的{_vt}（≥150%），多方控盤，"
                              f"今天方向偏多，手上的單子可以抱。"}
        if vol_ok:
            return {"verdict": "weak_bull", "label": "弱勢多方", "action": "觀察/不追高",
                    "vol_ratio_pct": vol_ratio_pct,
                    "detail": f"9:30小紅或帶上影線，量能{_vt}放大或持平，多頭有撐但力道不強，"
                              f"觀察為主，不建議追高。"}

    return {"verdict": "unclear", "label": "多空不明", "action": "觀望為主",
            "vol_ratio_pct": vol_ratio_pct,
            "detail": f"9:30量能{_vt}，型態跟量能組合不明確，觀望為主。"}


def evaluate_gate2_leader_deviation(stock_gain_pct, leader_gain_pct, ratio_threshold=1.5):
    """
    5分K三關·第二關：族群內個股強弱——你手上的是龍頭還是跟風？（依附件22）

    規則（依附件22範例回推：龍頭+4.2%、跟風+6.5%被判「領先龍頭過多」，
    6.5/4.2≈1.55，取整數1.5倍當門檻——見這輪討論確認過的建議值）：
      龍頭有在動（漲幅>0）+ 你的股跟漲，但漲幅沒有超過龍頭的1.5倍
      → 合格續抱，資金健康擴散
      龍頭沒動（漲幅<=0）你卻大漲，或你的漲幅超過龍頭1.5倍以上
      → 不合格，小鬼當家，主力拉高出貨機率高

    stock_gain_pct/leader_gain_pct：盤中漲幅（%），例如4.2代表+4.2%。

    回傳 dict：{verdict, label, detail, deviation_ratio}
    """
    if leader_gain_pct is None or stock_gain_pct is None:
        return {"verdict": "unknown", "label": "資料不足", "deviation_ratio": None,
                "detail": "缺少龍頭或個股的盤中漲幅資料，無法判斷第二關。"}

    if leader_gain_pct <= 0:
        return {"verdict": "fail", "label": "龍頭沒動，小鬼當家", "deviation_ratio": None,
                "detail": f"族群龍頭漲幅{leader_gain_pct}%（沒有在動），你的股卻自己漲，"
                          f"跟風股領漲不健康，主力拉高出貨機率高。"}

    deviation_ratio = round(stock_gain_pct / leader_gain_pct, 2)
    if stock_gain_pct > 0 and deviation_ratio <= ratio_threshold:
        return {"verdict": "pass", "label": "跟龍頭同步，健康擴散", "deviation_ratio": deviation_ratio,
                "detail": f"你的股漲幅是龍頭的{deviation_ratio}倍（≤{ratio_threshold}倍），"
                          f"漲幅跟龍頭接近，資金是健康的擴散，可以續抱。"}
    return {"verdict": "fail", "label": "領先龍頭過多", "deviation_ratio": deviation_ratio,
            "detail": f"你的股漲幅是龍頭的{deviation_ratio}倍（>{ratio_threshold}倍），"
                      f"漲幅遠超龍頭、乖離過大，小鬼當家，主力拉高出貨機率高。"}


def evaluate_gate2_leader_deviation_short(stock_decline_pct, leader_decline_pct, ratio_threshold=1.5):
    """
    【R97新增，空方版第二關，見開發歷程.md「空方gate2規劃」章節】跟多方版
    (evaluate_gate2_leader_deviation)結構對稱，但依市場結構特性調整，
    不是機械式符號鏡射：

    跟多方版的關鍵差異：
    1. 不檢查量能——實戰經驗指出「做空不一定要有量，無量下跌對量的要求
       沒那麼高」（多方需要買盤才能推升，空方只要沒人接就會跌），加量能
       門檻會誤殺健康的無量陰跌。
    2. 「小鬼當家」的判定方向相反：多方是「領先龍頭過多=主力出貨」，
       空方是「領先龍頭過多下跌=個股自身利空，非族群性弱勢，急殺後容易
       利空出盡反彈」，跟族群性的健康補跌是不同性質。

    規則：
      龍頭有在跌（跌幅>0，這裡跌幅用正數表示，例如3.5代表跌3.5%）+
      你的股跟跌，跌幅沒有超過龍頭的1.5倍
      → 合格，族群資金整體撤退，健康的空方擴散
      龍頭沒跌（跌幅<=0）你卻重挫，或你的跌幅超過龍頭1.5倍以上
      → 不合格，個股自身利空非族群性，急殺急拉風險高，當沖空風險高

    stock_decline_pct/leader_decline_pct：盤中跌幅（%，用正數表示，
    例如3.5代表跌3.5%，不是-3.5）——呼叫端計算時記得轉成正數再傳進來，
    避免跟多方版的正負號語意搞混。

    回傳 dict：{verdict, label, detail, deviation_ratio}，格式跟多方版一致
    方便呼叫端統一處理。
    """
    if leader_decline_pct is None or stock_decline_pct is None:
        return {"verdict": "unknown", "label": "資料不足", "deviation_ratio": None,
                "detail": "缺少龍頭或個股的盤中跌幅資料，無法判斷空方第二關。"}

    if leader_decline_pct <= 0:
        return {"verdict": "fail", "label": "龍頭沒跌，個股單獨走弱", "deviation_ratio": None,
                "detail": f"族群龍頭跌幅{leader_decline_pct}%（沒有在跌），你的股卻自己重挫，"
                          f"不是族群性弱勢，缺乏持續下殺的族群動能支撐，當沖空風險較高。"}

    deviation_ratio = round(stock_decline_pct / leader_decline_pct, 2)
    if stock_decline_pct > 0 and deviation_ratio <= ratio_threshold:
        return {"verdict": "pass", "label": "跟龍頭同步下跌，健康補跌", "deviation_ratio": deviation_ratio,
                "detail": f"你的股跌幅是龍頭的{deviation_ratio}倍（≤{ratio_threshold}倍），"
                          f"跌幅跟龍頭接近，族群資金整體撤退，是健康的空方擴散。"}
    return {"verdict": "fail", "label": "領跌過多，個股自身利空", "deviation_ratio": deviation_ratio,
            "detail": f"你的股跌幅是龍頭的{deviation_ratio}倍（>{ratio_threshold}倍），"
                      f"跌得比族群兇太多，多半是個股自身利空而非族群性弱勢，"
                      f"急殺後容易利空出盡反彈，當沖空風險高。"}


def evaluate_short_position_precheck(hist, lookback_days=20, max_decline_from_high_pct=20.0):
    """
    【R97新增，空方防接刀機制，見開發歷程.md】依實戰經驗「高檔剛轉弱才空，
    不追殺已經跌深的股票」——放空的價值在於「風險有限、利潤空間大」，
    如果股票已經跌了一大段，繼續空的下檔空間有限，但軋空/反彈風險無限，
    這是空方特有、多方沒有對稱情況的風控（多方沒有「追高買在阿呆谷」這種
    對稱的位置風險，因為多方買進的下檔風險本來就有限，是股價歸零；空方
    下檔"利潤"有限但上檔"風險"無限，位置检查更重要）。

    做法：檢查目前股價相對過去lookback_days天內最高價，跌幅是否已經超過
    max_decline_from_high_pct——超過就代表「已經跌深」，不建議再新建空單
    （不是不能追蹤，是不建議「新建」部位，避免接刀）。

    hist: 日K DataFrame（需有High/Close欄位），通常是fetch_price_hist()
    或fetch_finmind_stock_price()的回傳值。

    回傳 dict：{verdict, label, detail, decline_from_high_pct}
    verdict: 'ok'（位置健康，可以考慮新建空單）／'too_deep'（已經跌深，
    不建議新建空單，避免接刀）／'unknown'（資料不足）。
    """
    if hist is None or hist.empty or 'High' not in hist.columns or 'Close' not in hist.columns:
        return {"verdict": "unknown", "label": "資料不足", "decline_from_high_pct": None,
                "detail": "缺少日K資料，無法判斷是否已經跌深。"}
    recent = hist.tail(lookback_days)
    if recent.empty:
        return {"verdict": "unknown", "label": "資料不足", "decline_from_high_pct": None,
                "detail": "近期日K資料不足，無法判斷是否已經跌深。"}
    recent_high = float(recent['High'].max())
    cur_close = float(hist['Close'].iloc[-1])
    if recent_high <= 0:
        return {"verdict": "unknown", "label": "資料異常", "decline_from_high_pct": None,
                "detail": "近期最高價異常，無法判斷。"}
    decline_pct = round((recent_high - cur_close) / recent_high * 100, 2)
    if decline_pct > max_decline_from_high_pct:
        return {"verdict": "too_deep", "label": "已跌深，不建議新建空單",
                "decline_from_high_pct": decline_pct,
                "detail": f"目前價格已經比近{lookback_days}天高點回落{decline_pct}%"
                          f"（超過{max_decline_from_high_pct}%），下檔空間有限但反彈/軋空風險"
                          f"無限，不建議在這個位置新建空單，只適合觀望或考慮回補既有空單。"}
    return {"verdict": "ok", "label": "位置健康，高檔剛轉弱", "decline_from_high_pct": decline_pct,
            "detail": f"目前價格僅比近{lookback_days}天高點回落{decline_pct}%"
                      f"（未超過{max_decline_from_high_pct}%），位置在相對高檔，"
                      f"符合「高檔剛轉弱」的放空條件。"}


def evaluate_930_three_gate(stock_bars, leader_bars=None, direction='long', daily_hist=None):
    """
    5分K三關（查15）整合判斷——第一關過不了就停，過了才繼續第二關，
    第二關過了才繼續追蹤第三關（複用Step 3的evaluate_pullback_health）。

    stock_bars: 該股票的5分K列表（list of dict，intraday_5min_bars格式）
    leader_bars: 該股票所屬產業龍頭的5分K列表，格式相同，選填——沒有
    提供時第二關無法判斷（verdict='unknown'，不是fail，誠實區分「資料
    不足」跟「條件不合格」這兩種不同情況）。

    【R97新增direction參數，見開發歷程.md「空方gate2規劃」】'long'（預設，
    原本行為不變）或'short'。direction='short'時：
    - gate1：strong_bull/weak_bull才算「這個方向不合格，停止追蹤」（多方
      訊號代表空方論點被推翻），strong_bear/weak_bear/unclear繼續往下看
      （跟多方相反）。
    - gate2：改用evaluate_gate2_leader_deviation_short()，語意是「跟龍頭
      同步下跌才健康」而不是同步上漲。
    - 【誠實的技術限制】gate3(拉回體檢)目前只有多方版邏輯
      (evaluate_pullback_health)，空方對稱的「反彈健康度」還沒有設計，
      direction='short'時gate3固定回傳None、overall_verdict最高只到
      'pass'但標籤會註明「gate3空方版尚未支援」，不會假裝算出一個不存在
      的第三關結果。

    daily_hist：日K DataFrame（選填，direction='short'時用來跑
    evaluate_short_position_precheck()防接刀檢查——沒有跌深過的股票才
    允許pass。多方沒有這個檢查，只有空方需要，因為空方「下檔利潤有限、
    上檔風險無限」的不對稱風險結構跟多方不同，見該函式docstring。

    回傳 dict：{gate1, gate2, gate3, position_precheck, direction,
    overall_verdict, overall_label}
    overall_verdict：'pass'（該方向的關卡都過，或還在等後續資料但目前都
    沒fail）／'fail'（任一關明確fail）／'pending'（資料還不夠判斷）。
    """
    stock_df = bars_to_hist_df(stock_bars)
    gate1 = evaluate_930_gate1(stock_df)

    result = {"gate1": gate1, "gate2": None, "gate3": None, "position_precheck": None,
              "direction": direction, "overall_verdict": "pending", "overall_label": "等待資料"}

    if gate1["verdict"] == "unknown":
        result["overall_label"] = "等待9:30資料"
        return result

    if direction == "short":
        # 空方：多方訊號代表空方論點被推翻，直接停止追蹤
        if gate1["verdict"] in ("strong_bull", "weak_bull"):
            result["overall_verdict"] = "fail"
            result["overall_label"] = f"第一關{gate1['label']}(偏多)，空方論點不成立，停止追蹤"
            return result
        # 空方防接刀：位置已經跌深，不建議新建空單
        if daily_hist is not None:
            precheck = evaluate_short_position_precheck(daily_hist)
            result["position_precheck"] = precheck
            if precheck["verdict"] == "too_deep":
                result["overall_verdict"] = "fail"
                result["overall_label"] = f"位置已跌深({precheck['decline_from_high_pct']}%)，不建議新建空單"
                return result
    else:
        # 多方：原本行為，strong_bear/weak_bear才算不合格停止追蹤，
        # unclear(多空不明)只是觀望，不強制停止。
        if gate1["verdict"] in ("strong_bear", "weak_bear"):
            result["overall_verdict"] = "fail"
            result["overall_label"] = f"第一關{gate1['label']}，停止追蹤"
            return result

    # 第一關這個方向過了，繼續第二關——先算個股跟龍頭的盤中漲跌幅
    stock_gain_pct = None
    if '09:30' in stock_df.index and not stock_df.empty:
        _first_bar = stock_df.iloc[0]
        _last_close = stock_df.loc['09:30', 'Close']
        if _first_bar['Open'] > 0:
            stock_gain_pct = round((_last_close - _first_bar['Open']) / _first_bar['Open'] * 100, 2)

    leader_gain_pct = None
    if leader_bars:
        leader_df = bars_to_hist_df(leader_bars)
        if '09:30' in leader_df.index and not leader_df.empty:
            _l_first = leader_df.iloc[0]
            _l_last_close = leader_df.loc['09:30', 'Close']
            if _l_first['Open'] > 0:
                leader_gain_pct = round((_l_last_close - _l_first['Open']) / _l_first['Open'] * 100, 2)

    if direction == "short":
        # 空方版gate2要用跌幅(正數)，不是漲幅——這裡轉換，並且沒有股價
        # 資料時保持None不硬轉。
        stock_decline_pct = -stock_gain_pct if stock_gain_pct is not None else None
        leader_decline_pct = -leader_gain_pct if leader_gain_pct is not None else None
        gate2 = evaluate_gate2_leader_deviation_short(stock_decline_pct, leader_decline_pct)
    else:
        gate2 = evaluate_gate2_leader_deviation(stock_gain_pct, leader_gain_pct)
    result["gate2"] = gate2

    if gate2["verdict"] == "fail":
        result["overall_verdict"] = "fail"
        result["overall_label"] = "第二關不合格，停止追蹤"
        return result
    if gate2["verdict"] == "unknown":
        result["overall_verdict"] = "pass"   # 第一關已過，第二關只是缺資料不是fail
        result["overall_label"] = "第一關合格，第二關缺龍頭資料"
        return result

    if direction == "short":
        # 【誠實標註】空方版第三關(反彈健康度)還沒設計，不假裝算出結果，
        # 前兩關過就先給pass，標籤註明第三關暫不支援。
        result["overall_verdict"] = "pass"
        result["overall_label"] = "空方前兩關合格(第三關空方版尚未支援，人工複核拉回/反彈狀況)"
        return result

    # 第一、二關都過，繼續追蹤第三關（拉回體檢，複用Step 3，只有多方支援）
    gate3 = evaluate_pullback_health(stock_df, mode='intraday') if len(stock_df) >= 6 else None
    result["gate3"] = gate3

    if gate3 and gate3.get("verdict") == "weak":
        result["overall_verdict"] = "fail"
        result["overall_label"] = "第三關拉回不合格，出場"
    elif gate3 and gate3.get("verdict") == "strong":
        result["overall_verdict"] = "pass"
        result["overall_label"] = "三關全過，續抱合格"
    else:
        result["overall_verdict"] = "pass"
        result["overall_label"] = "前兩關合格，第三關資料還不夠（拉回尚未發生或資料不足）"

    return result


# ==============================================================================
# 三之八、趨勢資格硬閘門（R96新增，累積清單第1+2項——月線連續3天未站回
# 時無條件出場，一票否決不被其他因子分數蓋掉，依批次一分析附件11/14）
# ==============================================================================
def evaluate_trend_qualification_gate(hist):
    """
    趨勢資格硬閘門：股價連續3天收在20日均線(月線)下方 → 無條件判定「趨勢
    資格不符」，不管加權總分多高，都該出場——這是「一票否決」，不是
    再加一個評分因子。

    設計理由（批次一分析）：現有加權評分系統會讓「基本面90分、籌碼80分」
    這種高分因子蓋掉「月線已經跌破3天」這個事實，導致系統顯示續抱，但
    照這套框架的真實邏輯，月線破裂就該無條件出場，跟其他因子多漂亮
    無關。這個函式回傳的triggered=True，呼叫端應該用它覆蓋掉原本的
    加權評分結論，不是跟其他因子加總。

    判斷邏輯：用20日均線(不是外部傳入的ma20單一數值，是完整算出來的
    MA20序列)，檢查最近3個交易日，是否每一天的收盤都在當天的MA20之下。
    這裡刻意用「當天的MA20」而不是「今天的MA20」去比較過去3天的收盤，
    因為均線本身每天都在變動，用今天的均線去評判3天前的收盤，時空
    不對齊，會失真。

    hist: 需要至少23根K棒（20天算MA20 + 3天檢查窗）的OHLC DataFrame。
    資料不足時回傳triggered=False、reason說明資料不足，不假裝有答案。

    回傳 dict：{triggered, reason, days_below, ma20_now}
    """
    if hist is None or len(hist) < 23:
        return {"triggered": False, "reason": "資料不足（需要至少23個交易日）",
                "days_below": None, "ma20_now": None}

    ma20_series = hist['Close'].rolling(20).mean()
    last3_close = hist['Close'].tail(3)
    last3_ma20 = ma20_series.tail(3)

    if last3_ma20.isna().any():
        return {"triggered": False, "reason": "均線資料不足，無法判斷",
                "days_below": None, "ma20_now": None}

    below_flags = (last3_close.values < last3_ma20.values)
    days_below = int(below_flags.sum())
    ma20_now = round(float(ma20_series.iloc[-1]), 2)

    if bool(below_flags.all()):
        return {"triggered": True,
                "reason": f"股價連續3天收在月線(MA20={ma20_now})下方，趨勢資格不符，"
                          f"無條件出場，不論其他評分因子多高。",
                "days_below": days_below, "ma20_now": ma20_now}
    return {"triggered": False,
            "reason": f"近3天有{days_below}天收在月線下方，還沒連續3天，暫不觸發硬閘門。",
            "days_below": days_below, "ma20_now": ma20_now}


# ==============================================================================
# 三之九、盤中無量下跌反彈健康度（R96新增，累積清單第6項）——判斷點在
# 「反彈階段」的量，不是下跌當下的量。複用find_attack_bar反向邏輯找急殺K棒。
# ==============================================================================
def find_panic_drop_bar(hist, lookback=20, vol_ratio_threshold=1.5):
    """
    找「急殺K棒」——find_attack_bar()的反向版本：找最近一根同時符合
    「爆量」(成交量>=之前5根均量的1.5倍) 且「收黑」(收盤<開盤) 的K棒，
    視為這波下跌的急殺起點。跟find_attack_bar共用同一套「爆量」門檻，
    只是方向相反（找收黑不是收紅），不重新發明標準。

    回傳格式跟find_attack_bar完全一致：{position, date, open, high, low,
    close, volume} 或 None。
    """
    if hist is None or len(hist) < 10:
        return None
    n = min(lookback, len(hist) - 5)
    for i in range(len(hist) - 1, len(hist) - 1 - n, -1):
        if i < 5:
            break
        vol = float(hist['Volume'].iloc[i])
        prev5_vol = hist['Volume'].iloc[i - 5:i]
        avg5 = float(prev5_vol.mean()) if len(prev5_vol) > 0 else 0.0
        is_bearish = float(hist['Close'].iloc[i]) < float(hist['Open'].iloc[i])
        if avg5 > 0 and vol >= avg5 * vol_ratio_threshold and is_bearish:
            return {
                "position": i, "date": hist.index[i],
                "open": float(hist['Open'].iloc[i]), "high": float(hist['High'].iloc[i]),
                "low": float(hist['Low'].iloc[i]), "close": float(hist['Close'].iloc[i]),
                "volume": vol,
            }
    return None


def evaluate_rebound_health(hist, panic_bar=None):
    """
    盤中無量下跌反彈健康度（依附件28修正版分析）：急殺當下量大是正常
    生理反應，不是判斷重點；真正的照妖鏡在「反彈階段」的量——反彈量縮
    (賣壓減輕，短線客在跑、主力沒動) = 虛跌，可以等；反彈量增但彈不
    回去(有人趁反彈倒貨) = 賣壓沒減輕，必須走。

    跟evaluate_pullback_health(拉回體檢)結構類似但方向相反：拉回體檢
    看「多頭攻擊後的拉回」，這個看「空頭急殺後的反彈」，兩者是對稱的
    一組。

    panic_bar: find_panic_drop_bar()的回傳值，未提供時這裡會自動找一次
    （排除今天自己，理由同其他關卡——急殺必須是「之前」發生的事）。

    回傳 dict：{verdict, label, vol_ratio_pct, detail}
    """
    if hist is None or len(hist) < 6:
        return None
    if panic_bar is None:
        panic_bar = find_panic_drop_bar(hist.iloc[:-1])
    if not panic_bar:
        return {"verdict": "unknown", "label": "無急殺基準", "vol_ratio_pct": None,
                "detail": "近20個交易日內找不到符合條件的急殺起點（爆量收黑），"
                          "反彈健康度沒有基準可用。"}

    pos = panic_bar['position']
    rebound_bars = hist.iloc[pos + 1:]
    if rebound_bars.empty:
        return {"verdict": "unknown", "label": "尚無反彈", "vol_ratio_pct": None,
                "detail": "急殺K棒就是最新一根，還沒有反彈可以體檢。"}

    panic_vol = panic_bar['volume']
    rebound_avg_vol = float(rebound_bars['Volume'].mean())
    vol_ratio_pct = round((rebound_avg_vol / panic_vol) * 100, 1) if panic_vol > 0 else None
    _ab_date_str = panic_bar['date'].strftime('%m/%d') if hasattr(panic_bar['date'], 'strftime') else str(panic_bar['date'])

    today_close = float(hist['Close'].iloc[-1])
    has_recovered = bool(today_close > panic_bar['close'])

    # 【修復】strong(虛跌)條件原本多加了「not has_recovered」，但止穩
    # 回升本來就是健康確認訊號，不該否定虛跌判斷，已拿掉這個多餘條件。
    if vol_ratio_pct is not None and vol_ratio_pct < 70:
        return {"verdict": "strong", "label": "虛跌，賣壓減輕", "vol_ratio_pct": vol_ratio_pct,
                "detail": f"反彈量縮至急殺量的{vol_ratio_pct}%，賣壓在減輕，主力還在，"
                          f"這種下跌是虛跌，可以等止穩。（急殺K棒：{_ab_date_str}）"}
    if vol_ratio_pct is not None and vol_ratio_pct >= 100 and not has_recovered:
        return {"verdict": "weak", "label": "賣壓未減，續破風險高", "vol_ratio_pct": vol_ratio_pct,
                "detail": f"反彈量增至急殺量的{vol_ratio_pct}%，但股價彈不回去，賣壓沒有減輕，"
                          f"有人利用反彈倒貨，續破風險高，該走就走。（急殺K棒：{_ab_date_str}）"}
    return {"verdict": "neutral", "label": "中段觀察", "vol_ratio_pct": vol_ratio_pct,
            "detail": f"反彈量為急殺量的{vol_ratio_pct if vol_ratio_pct is not None else '—'}%，"
                      f"介於健康與警戒之間，續觀察。（急殺K棒：{_ab_date_str}）"}


# ==============================================================================
# 三之十、投信季底作帳警示（R96新增，累積清單第8項）——投信連續買超若
# 發生在季底前，可能是作帳行情不是真的看好。
# ==============================================================================
def check_institutional_season_end_warning(trade_date, buy_streak_days=0):
    """
    投信季底作帳警示：投信連續買超，如果發生在季底前10個交易日內，
    標註「可能是作帳，季底後留意倒貨」，不是單純的看好訊號。

    trade_date：要檢查的日期（datetime或date物件，通常是今天）。
    buy_streak_days：投信連續買超天數，呼叫端傳入（通常從法人買賣超
    歷史資料算出，這個函式本身不重新查資料，只做日期邏輯判斷）。

    季底定義：3/31、6/30、9/30、12/31，往前推10個「日曆天」(不是嚴格的
    交易日，用日曆天簡化計算，10個交易日約對應14個日曆天左右，這裡取
    保守值10天寧可提早一點警示，不要抓太窄漏掉)。

    回傳 dict：{warning, reason}；buy_streak_days<3時不觸發（連續買超
    未滿3天本身還不構成一般意義下的「連續買超」訊號，這個警示只在
    「已經構成買超訊號」的前提下才有意義去額外提醒季底風險）。
    """
    if buy_streak_days < 3:
        return {"warning": False, "reason": None}

    import datetime as _dt
    if isinstance(trade_date, str):
        trade_date = _dt.datetime.strptime(trade_date, "%Y-%m-%d").date()
    elif hasattr(trade_date, 'date'):
        trade_date = trade_date.date()

    year = trade_date.year
    quarter_ends = [
        _dt.date(year, 3, 31), _dt.date(year, 6, 30),
        _dt.date(year, 9, 30), _dt.date(year, 12, 31),
    ]
    for q_end in quarter_ends:
        days_to_q_end = (q_end - trade_date).days
        if 0 <= days_to_q_end <= 14:
            return {"warning": True,
                    "reason": f"投信連續買超{buy_streak_days}天，且距離季底({q_end})只剩"
                              f"{days_to_q_end}天，可能是作帳行情，季底後留意投信倒貨，"
                              f"不要單純當成看好訊號。"}
    return {"warning": False, "reason": None}


# ==============================================================================
# 三之十一、今日流動性過濾器（R96新增，累積清單第9項）——用開盤後真實
# 成交比對近期均量，取代附件36的試撮量估計（可掛假單撤銷，可信度低）。
# ==============================================================================
def evaluate_today_liquidity_by_avg(cum_volume_now, avg_vol, recent_days=5,
                                    adequate_pct=60.0, thin_pct=30.0):
    """
    今日流動性過濾器（核心邏輯，直接吃已經算好的均量）——見
    evaluate_today_liquidity()的完整說明；這個版本給「呼叫端已經有
    現成的近N日均量、沒有完整hist資料」的情境用（例如attach_live_quotes
    只有戰卡裡已經算好的vol_5d_mean，沒有完整OHLC歷史，重新查一次不但
    浪費、也違反不重複查詢的原則）。evaluate_today_liquidity(hist版)
    內部也是呼叫這個函式，兩邊共用同一套判斷邏輯，不重複維護兩份。

    avg_vol：近recent_days日平均成交量（張），呼叫端自行算好傳入。

    回傳格式跟evaluate_today_liquidity完全一致。
    """
    if cum_volume_now is None or avg_vol is None or avg_vol <= 0:
        return {"verdict": "unknown", "label": "資料不足", "pct_of_avg": None,
                "detail": "缺少即時累計量或近期均量資料，無法判斷今日流動性。"}

    pct_of_avg = round((cum_volume_now / avg_vol) * 100, 1)

    if pct_of_avg >= adequate_pct:
        return {"verdict": "adequate", "label": "流動性充足", "pct_of_avg": pct_of_avg,
                "detail": f"今天累計量已達近{recent_days}日均量的{pct_of_avg}%（≥{adequate_pct}%），"
                          f"有波動空間，可積極找標的。"}
    if pct_of_avg <= thin_pct:
        return {"verdict": "thin", "label": "量能清淡", "pct_of_avg": pct_of_avg,
                "detail": f"今天累計量只有近{recent_days}日均量的{pct_of_avg}%（≤{thin_pct}%），"
                          f"沒人氣、波動小、滑價大，這種盤進場容易被磨損，建議觀望。"}
    return {"verdict": "moderate", "label": "量能中等", "pct_of_avg": pct_of_avg,
            "detail": f"今天累計量為近{recent_days}日均量的{pct_of_avg}%，介於清淡與充足之間，續觀察。"}


def evaluate_today_liquidity(cum_volume_now, hist, recent_days=5, adequate_pct=60.0, thin_pct=30.0):
    """
    今日流動性過濾器：用「今天累計到目前為止的真實成交量」對比「近N個
    交易日平均量」，估算今天的流動性夠不夠、值不值得進場——避免在冷清盤
    買到爛價位（量不夠時滑價通常較大）。

    跟附件36的差異：附件36用「昨日總量」當基準、且只用試撮量（可以掛假
    單撤銷，可信度低）；這裡改用「近5日均量」當基準（避免昨天剛好是
    異常爆量或地量日，用單一天當基準會失真），且用開盤後的真實成交量
    （不是試撮委託量），更貼近真相。

    門檻不是照搬附件36的40%/30%（那是「試撮量 vs 全天預估」的比例，
    跟這裡「盤中累計 vs 近期日均量」的比例，量測的東西不完全一樣，
    照搬數字沒有意義）——這裡用60%/30%當一組合理的起始值：盤中累計量
    達近5日均量的60%以上，代表照這個速度全天量能大概率會超過均量，
    流動性充足；30%以下代表明顯清淡。這組門檻之後可以用回測資料校準，
    不是憑感覺定案的最終答案。

    cum_volume_now：今天累計到目前為止的成交量（張），來自即時報價的
    累計量欄位。
    hist：日K歷史資料，用來算近N日均量（不含今天，避免今天自己還在
    累積中的量拉低/影響基準）。

    回傳 dict：{verdict, label, pct_of_avg, detail}
    verdict：'adequate'(流動性充足)／'thin'(清淡)／'moderate'(中等，
    觀望)／'unknown'(資料不足)
    """
    if cum_volume_now is None or hist is None or len(hist) < recent_days + 1:
        return {"verdict": "unknown", "label": "資料不足", "pct_of_avg": None,
                "detail": "缺少即時累計量或近期日K資料，無法判斷今日流動性。"}

    recent_avg_vol = float(hist['Volume'].iloc[-(recent_days + 1):-1].mean())
    return evaluate_today_liquidity_by_avg(cum_volume_now, recent_avg_vol, recent_days,
                                           adequate_pct, thin_pct)


# ==============================================================================
# 三之十二、漲幅榜族群性市場regime閘門（R96新增，累積清單第4項，依附件18：
# 漲幅榜前10名是否集中同一族群，判斷今天適不適合抱波段）
# ==============================================================================
def evaluate_market_gainer_concentration(gainers_with_industry, top_n=10, concentration_threshold=6):
    """
    漲幅榜族群性：漲幅榜前N名(預設10)裡，如果有≥concentration_threshold
    (預設6)檔屬於同一個產業，代表資金集中、主流明確，今天適合抱波段；
    分散在多個族群，代表資金沒有共識、行情走不遠，續抱訊號的可信度該
    打折扣。

    gainers_with_industry：list of (code, gain_pct, industry)，呼叫端已經
    抓好漲跌幅排行+對照過產業分類；這個函式只負責統計判斷，不做任何
    資料抓取（資料抓取放在v160，因為要打外部端點；這裡刻意保持
    warroom_core.py的純函式風格，方便獨立測試）。

    回傳 dict：{verdict, label, dominant_industry, dominant_count, detail}
    verdict：'concentrated'(有主流)／'dispersed'(資金分散)／'unknown'
    (資料不足，例如gainers_with_industry筆數不到top_n)
    """
    if not gainers_with_industry or len(gainers_with_industry) < top_n:
        return {"verdict": "unknown", "label": "資料不足", "dominant_industry": None,
                "dominant_count": None,
                "detail": f"漲幅榜資料不足{top_n}檔，無法判斷族群集中度。"}

    top_list = sorted(gainers_with_industry, key=lambda x: x[1], reverse=True)[:top_n]
    industry_counts = {}
    for _code, _gain, industry in top_list:
        if not industry:
            continue
        industry_counts[industry] = industry_counts.get(industry, 0) + 1

    if not industry_counts:
        return {"verdict": "unknown", "label": "資料不足", "dominant_industry": None,
                "dominant_count": None, "detail": "漲幅榜前幾名都缺產業分類資料，無法判斷。"}

    dominant_industry, dominant_count = max(industry_counts.items(), key=lambda x: x[1])

    if dominant_count >= concentration_threshold:
        return {"verdict": "concentrated", "label": "資金集中，有主流", "dominant_industry": dominant_industry,
                "dominant_count": dominant_count,
                "detail": f"漲幅榜前{top_n}名裡有{dominant_count}檔屬於「{dominant_industry}」，"
                          f"資金集中、主流明確，今天適合抱波段，續抱訊號可信度較高。"}
    return {"verdict": "dispersed", "label": "資金分散，沒有主流", "dominant_industry": dominant_industry,
            "dominant_count": dominant_count,
            "detail": f"漲幅榜前{top_n}名裡最多同族群只有{dominant_count}檔（未達{concentration_threshold}檔），"
                      f"資金分散、沒有明確主流，今天的行情可能走不遠，有賺就跑，續抱訊號可信度打折扣。"}


# ==============================================================================
# 三之十五、當沖操作建議整合層（R96新增，把Step1-9綜合成單一進場建議，
# 跟determine_signal()波段評分分開設計、分開顯示，不合併分數）
# ==============================================================================
def evaluate_daytrade_recommendation(signals):
    """
    當沖操作建議——把當沖相關的多項獨立判斷綜合成一句可以直接看懂的建議，
    不用自己一項一項比對數字。跟determine_signal()（波段導向的加權評分
    引擎）是兩條平行邏輯，故意不合併：波段看的是中期趨勢/基本面/籌碼，
    當沖看的是當下盤中的量價/籌碼情緒/流動性，混在同一個分數裡會互相
    稀釋、失去各自的意義。

    signals: dict，鍵是訊號名稱、值是該訊號的判斷結果dict（跟這個系統
    其他evaluate_*函式的回傳格式一致，至少要有'verdict'欄位）。可以直接
    餵戰卡的card dict部分欄位進來，例如：
    {
        'trend_gate': c.get('trend_gate'),              # 硬性否決①
        'intraday_gate': c.get('intraday_gate'),          # 硬性否決②(9:30三關)
        'pullback_health': c.get('pullback_health'),      # 硬性否決③(拉回體檢)
        'closing_strength': c.get('closing_strength'),
        'volume_followthrough': c.get('volume_followthrough'),
        'rebound_health': c.get('rebound_health'),
        'day_trader_ratio': c.get('day_trader_ratio'),
        'margin_regime': c.get('margin_regime'),
        'vwap_position': c.get('vwap_position'),
        'order_book': c.get('order_book'),
        'rsi_dual': c.get('rsi_dual'),
        'liquidity': c.get('liquidity'),
    }
    沒有的鍵可以省略，這個函式會自動跳過缺值的項目，不強行湊分數。

    【硬性否決，一票否決，不管其他項目分數多高】：
      trend_gate觸發（連續3天破月線）→ 無條件不建議進場
      intraday_gate明確fail（9:30三關不合格）→ 無條件不建議進場
      pullback_health=weak（拉回跌破起漲點，出場訊號）→ 無條件不建議進場
    這三項是這套框架裡明確標注「不合格出場，絕對不抱」的硬性規則，不能
    被其他項目的高分蓋掉——這是批次一分析時定案的「一票否決」設計原則，
    當沖建議層沿用同一個哲學。

    【其餘項目加權】：每項strong算+1分，weak算-1分，neutral/unknown不計分，
    但neutral會記錄在detail裡讓使用者知道「這項有查但沒有明確方向」。
    liquidity跟一般三態verdict命名不同（adequate/thin/moderate），這裡
    額外對應：adequate算+1，thin算-1。

    回傳 dict：{verdict, label, score, positive_items, negative_items,
    neutral_items, veto_reason, detail}
    verdict：'veto'(硬性否決)／'aggressive'(積極進攻)／'watch_positive'
    (觀望偏多)／'neutral'(中性觀望)／'watch_negative'(觀望偏空)／
    'avoid'(不建議進場，加權分數判定)／'unknown'(完全沒有足夠資料判斷)

    【R96新增，訊號衝突自動降級】總指揮官明確指出：這個整合層存在的目的
    就是不要讓使用者自己讀完每一項細節才能發現矛盾——如果「5項技術面
    偏多、1項籌碼面明確偏空」單純加總就報「積極進攻」，等於把「判斷」
    這件事又丟回給使用者，整合層就沒有意義了。所以當五檔盤口有內外盤
    成交比率完整驗證（data_completeness=='full'，不是只看掛單厚度這種
    容易被瞬間大單影響的單一指標）且明確偏空時，會自動把結論封頂在
    watch_positive，不管加總分數多高，並把衝突原因寫進detail最前面——
    這是AI該做的判斷，不是留給使用者自己比對數字。
    """
    # 硬性否決檢查，優先於所有加權計分
    trend_gate = signals.get('trend_gate')
    if trend_gate and trend_gate.get('triggered'):
        return {"verdict": "veto", "label": "不建議進場（硬性否決）", "score": None,
                "positive_items": [], "negative_items": [], "neutral_items": [],
                "veto_reason": "趨勢資格不符", "detail": trend_gate.get('reason', '連續3天收在月線下方')}

    intraday_gate = signals.get('intraday_gate')
    if intraday_gate and intraday_gate.get('overall_verdict') == 'fail':
        return {"verdict": "veto", "label": "不建議進場（硬性否決）", "score": None,
                "positive_items": [], "negative_items": [], "neutral_items": [],
                "veto_reason": "9:30三關不合格",
                "detail": intraday_gate.get('overall_label', '三關判斷不合格')}

    pullback_health = signals.get('pullback_health')
    if pullback_health and pullback_health.get('verdict') == 'weak':
        return {"verdict": "veto", "label": "不建議進場（硬性否決）", "score": None,
                "positive_items": [], "negative_items": [], "neutral_items": [],
                "veto_reason": "拉回體檢出場訊號",
                "detail": pullback_health.get('detail', '跌破起漲點，不合格')}

    # 加權計分——沒有觸發硬性否決，才進到這一段
    _weighted_keys = {
        'closing_strength': '收盤強弱', 'volume_followthrough': '量能達標',
        'rebound_health': '反彈健康度', 'day_trader_ratio': '當沖佔比',
        'margin_regime': '融資水位', 'vwap_position': 'VWAP位置',
        'order_book': '五檔盤口', 'rsi_dual': 'RSI動能',
    }
    score = 0
    positive_items, negative_items, neutral_items = [], [], []
    for key, label in _weighted_keys.items():
        v = signals.get(key)
        if not v:
            continue
        verdict = v.get('verdict')
        if verdict == 'strong':
            score += 1
            positive_items.append(label)
        elif verdict == 'weak':
            score -= 1
            negative_items.append(label)
        elif verdict == 'neutral':
            neutral_items.append(label)

    # liquidity命名跟一般三態不同，額外對應
    liq = signals.get('liquidity')
    if liq:
        if liq.get('verdict') == 'adequate':
            score += 1
            positive_items.append('流動性')
        elif liq.get('verdict') == 'thin':
            score -= 1
            negative_items.append('流動性')
        elif liq.get('verdict') == 'moderate':
            neutral_items.append('流動性')

    total_counted = len(positive_items) + len(negative_items) + len(neutral_items)
    if total_counted == 0:
        return {"verdict": "unknown", "label": "資料不足，無法給建議", "score": None,
                "positive_items": [], "negative_items": [], "neutral_items": [],
                "veto_reason": None, "detail": "目前沒有足夠的當沖相關資料可以綜合判斷。"}

    if score >= 3:
        verdict, label = "aggressive", "積極進攻"
    elif score >= 1:
        verdict, label = "watch_positive", "觀望偏多"
    elif score <= -3:
        verdict, label = "avoid", "不建議進場"
    elif score <= -1:
        verdict, label = "watch_negative", "觀望偏空"
    else:
        verdict, label = "neutral", "中性觀望"

    # 【R96新增——總指揮官明確指出的設計缺陷：不能一邊說「整合層幫你省下
    # 比對數字的時間」，一邊又要求使用者自己發現矛盾點才知道該多想一步。
    # 這正是仁寶案例暴露的問題：5項技術面偏多、只有1項籌碼面(五檔盤口)
    # 偏空，單純加總分數算出「積極進攻」，但那1項偏空恰好是當下最直接的
    # 真實買賣力道訊號——這種訊號的可信度跟即時性，不該被其他4-5項技術
    # 面因子的加總分數稀釋掉，該由這裡自動偵測、自動降級，不是留給使用者
    # 自己讀完每一項細節才能發現。
    #
    # 【R96再擴充，總指揮官要求「務必做到最完整」】原本只處理五檔盤口
    # 這一種組合，這裡系統性審查過全部8個加權因子後，擴充成通用的
    # 「高可信度即時訊號」框架，不是只補這一個洞：
    #   - 五檔盤口（data_completeness=='full'時）：即時委買委賣掛單+內外盤
    #     成交比率交叉驗證，直接反映「錢實際上在往哪邊流」，不是技術指標
    #     的間接推論。
    #   - 反彈健康度：直接觀察「急殺後的反彈階段，賣壓有沒有真的減輕」，
    #     同樣是觀察實際參與者行為（成交量的真實變化），不是統計出來的
    #     技術指標。
    # 這兩項的共同特徵是「直接觀察實際發生的買賣行為」，跟收盤強弱、RSI
    # 動能、量能達標這種「從價格/量的統計規律推論」的技術指標，可信度跟
    # 即時性不對等，不該用同樣的+1/-1加總去稀釋。其餘6個因子（收盤強弱、
    # 量能達標、當沖佔比、融資水位、VWAP位置、RSI動能、流動性）審查後
    # 判斷都屬於「統計規律推論」或「結構性慢變量」，繼續留在一般加權
    # 計分，不特別封頂——不是每個因子都要有封頂機制，只有「直接觀察真實
    # 參與者行為」這個特徵的訊號才夠格。
    #
    # 雙向設計：不是只有「偏空訊號被多數偏多蓋過」該封頂，「偏多訊號被
    # 多數偏空蓋過」同樣該有一個下限（floor）——這兩個高可信度訊號如果
    # 明確偏多，就算其餘技術面因子加總偏空，結論也不該悲觀到avoid，至少
    # 停在watch_negative，同樣把衝突原因講清楚，不是留給使用者自己發現。
    _caution_notes = []
    _verdict_severity = {"avoid": 0, "watch_negative": 1, "neutral": 2,
                         "watch_positive": 3, "aggressive": 4}
    _cap_to = None      # 封頂（結論太樂觀時往下限制）
    _floor_to = None    # 保底（結論太悲觀時往上限制）

    _high_reliability_signals = [
        ('order_book', '五檔盤口', lambda v: v.get('data_completeness') == 'full'),
        ('rebound_health', '反彈健康度', lambda v: True),   # 沒有額外資料完整度門檻，本身就是直接觀察
    ]
    for _key, _cn_label, _gate_fn in _high_reliability_signals:
        _v = signals.get(_key)
        if not _v or not _gate_fn(_v):
            continue
        if _v.get('verdict') == 'weak':
            _caution_notes.append(f"⚠️ {_cn_label}明確偏空（{_v.get('label')}），跟其他技術面因子的"
                                  f"加總結論衝突，這是直接觀察實際買賣行為的訊號，優先度較高")
            if _cap_to is None or _verdict_severity[_cap_to] > _verdict_severity['watch_positive']:
                _cap_to = "watch_positive"
        elif _v.get('verdict') == 'strong':
            _caution_notes.append(f"✅ {_cn_label}明確偏多（{_v.get('label')}），跟其他技術面因子的"
                                  f"加總結論衝突，這是直接觀察實際買賣行為的訊號，優先度較高")
            if _floor_to is None or _verdict_severity[_floor_to] < _verdict_severity['watch_negative']:
                _floor_to = "watch_negative"

    if _cap_to and _verdict_severity.get(verdict, 0) > _verdict_severity.get(_cap_to, 0):
        verdict, label = _cap_to, {"watch_positive": "觀望偏多", "neutral": "中性觀望",
                                   "watch_negative": "觀望偏空", "avoid": "不建議進場"}[_cap_to]
    elif _floor_to and _verdict_severity.get(verdict, 0) < _verdict_severity.get(_floor_to, 0):
        verdict, label = _floor_to, {"watch_positive": "觀望偏多", "neutral": "中性觀望",
                                     "watch_negative": "觀望偏空", "avoid": "不建議進場"}[_floor_to]

    _detail_parts = []
    if positive_items:
        _detail_parts.append(f"{len(positive_items)}項偏多（{'、'.join(positive_items)}）")
    if negative_items:
        _detail_parts.append(f"{len(negative_items)}項偏空（{'、'.join(negative_items)}）")
    if neutral_items:
        _detail_parts.append(f"{len(neutral_items)}項中性（{'、'.join(neutral_items)}）")
    _stats_line = "、".join(_detail_parts) + f"，綜合分數{score:+d}。"
    # 【R96新增】警語跟統計數字分開成兩句，警語擺最前面單獨一句——這是
    # 使用者一眼就該看到的「有訊號衝突」提示，不該跟後面的統計數字黏在
    # 一起變成一整句難讀的run-on句子。
    detail = ("　".join(_caution_notes) + "　" + _stats_line) if _caution_notes else _stats_line

    return {"verdict": verdict, "label": label, "score": score,
            "positive_items": positive_items, "negative_items": negative_items,
            "neutral_items": neutral_items, "veto_reason": None, "detail": detail}


# ==============================================================================
# 三之十三、當沖佔比 + 融資餘額籌碼濾網（R96新增，累積清單第5項，依附件26）
# ==============================================================================
def evaluate_day_trader_ratio(day_trade_volume, total_volume, cold_threshold=30.0, hot_threshold=40.0):
    """
    當沖佔比：當沖成交量 ÷ 當日總成交量。依附件26：<30%=情緒偏冷，續抱
    空間還在；>40%=投機氣氛過重，主力容易出貨；其餘中性。

    day_trade_volume：FinMind TaiwanStockDayTrading資料集的Volume欄位
    （這是當沖成交量，資料集本來就有，只是原本的fetch_day_trading_info
    只取了BuyAfterSale沒取這個欄位）。
    total_volume：當日總成交量，跟day_trade_volume要用同一個單位
    （FinMind的Volume是「股」，跟系統其他地方習慣用「張」不同，呼叫端
    要自己統一單位再傳進來，這裡不做單位轉換，避免呼叫端搞不清楚
    這個函式期待哪種單位）。

    回傳 dict：{verdict, label, ratio_pct, detail}
    """
    if day_trade_volume is None or total_volume is None or total_volume <= 0:
        return {"verdict": "unknown", "label": "資料不足", "ratio_pct": None,
                "detail": "缺少當沖成交量或當日總成交量資料，無法判斷。"}

    ratio_pct = round((day_trade_volume / total_volume) * 100, 1)

    if ratio_pct < cold_threshold:
        return {"verdict": "strong", "label": "情緒偏冷", "ratio_pct": ratio_pct,
                "detail": f"當沖佔比{ratio_pct}%（<{cold_threshold}%），投機氣氛不重，續抱空間還在。"}
    if ratio_pct > hot_threshold:
        return {"verdict": "weak", "label": "投機過熱", "ratio_pct": ratio_pct,
                "detail": f"當沖佔比{ratio_pct}%（>{hot_threshold}%），投機氣氛過重，主力容易利用當沖"
                          f"熱度出貨，風險升高。"}
    return {"verdict": "neutral", "label": "中性", "ratio_pct": ratio_pct,
            "detail": f"當沖佔比{ratio_pct}%，介於冷熱之間，續觀察。"}


def fetch_day_trading_info(symbol):
    """
    【R97搬進共用模組，原本在warroom_v160.py】查詢個股「現股當沖」資格——
    用FinMind的TaiwanStockDayTrading資料集，這是交易所官方認定的當沖標的
    名單。搬進core.py讓候選池篩選(system_scheduler.py的
    stage_build_intraday_pool)也能用同一份，供標記「當沖比過熱」用
    （總指揮官依實戰經驗提供：當沖比>50~60%代表短線客在對作，波動大）。

    【誠實的限制】查不到資料時回傳None，可能是「這檔真的不能當沖」，也
    可能是「這幾天剛好都沒有當沖成交量」，兩者從API本身無法100%區分，
    呼叫端不該把「查無資料」講成「確定不能當沖」。

    回傳 dict {'eligible': True, 'buy_after_sale': str, 'date': str,
    'day_trade_volume': float或None} 或 None。
    """
    try:
        _start = (datetime.now(TAIPEI_TZ) - timedelta(days=10)).strftime('%Y-%m-%d')
        payload = _finmind_get('https://api.finmindtrade.com/api/v4/data',
                               {'dataset': 'TaiwanStockDayTrading', 'data_id': symbol,
                                'start_date': _start}, max_retries=2, timeout=10)
        rows = payload.get('data', [])
        if not rows:
            return None
        # 【R97修復，總指揮官用真實資料抓到的bug】原本直接取rows[-1](最新
        # 一筆)，但FinMind當天這筆本來就是Volume=0的佔位資料——當沖量要
        # 等當天收盤後才會定案，查詢當下(尤其是盤中)最新一筆幾乎必然是0，
        # 天真地把這個0當成真實數字用，會讓當沖比計算永遠算出0.0%，不是
        # 市場真的沒有當沖量。改成從最新一筆往前找，跳過「今天」這筆
        # (不管有沒有值都跳過，因為今天的本來就不可信)，用最近一個
        # 「不是今天」的真實定案值。
        _today_str = datetime.now(TAIPEI_TZ).strftime('%Y-%m-%d')
        latest = None
        for _row in reversed(rows):
            if _row.get('date') == _today_str:
                continue   # 今天這筆是佔位資料，不可信，跳過
            latest = _row
            break
        if latest is None:
            return None   # 扣掉今天之後完全沒有其他資料，誠實回報查無資料
        return {'eligible': True, 'buy_after_sale': str(latest.get('BuyAfterSale', '') or ''),
                'date': latest.get('date', ''),
                'day_trade_volume': safe_float(latest.get('Volume')) if latest.get('Volume') is not None else None}
    except Exception as _e:
        print(f"[fetch_day_trading_info-診斷] {symbol} 抓當沖資格失敗：{type(_e).__name__}: {_e}")
        return None


def evaluate_margin_balance_regime(current_balance, balance_history, near_high_pct=95.0, near_low_pct=105.0):
    """
    融資餘額水位：依附件26——融資餘額在低檔或下降，代表散戶還沒大量進場
    接盤，行情還有空間，續抱風險較低；融資餘額持續創高，代表散戶已經
    大量進場，主力容易趁高檔出貨給散戶接。

    current_balance：今天的融資餘額（張或股，跟balance_history單位一致
    即可，這裡只做相對比較，不涉及絕對單位換算）。
    balance_history：近N天的融資餘額歷史（不含今天），list of float。

    判斷邏輯：今天餘額 >= 近期最高值的near_high_pct%(預設95%，接近或
    創新高) → weak(過熱警示)；今天餘額 <= 近期最低值的near_low_pct%
    (預設105%，接近或創新低) → strong(低檔，續抱空間還在)；其餘neutral。

    回傳 dict：{verdict, label, pct_vs_recent_high, detail}
    """
    if current_balance is None or not balance_history:
        return {"verdict": "unknown", "label": "資料不足", "pct_vs_recent_high": None,
                "detail": "缺少融資餘額歷史資料，無法判斷水位。"}

    recent_high = max(balance_history)
    recent_low = min(balance_history)
    if recent_high <= 0:
        return {"verdict": "unknown", "label": "資料不足", "pct_vs_recent_high": None,
                "detail": "融資餘額歷史資料異常，無法判斷水位。"}

    pct_vs_recent_high = round((current_balance / recent_high) * 100, 1)

    if current_balance >= recent_high * (near_high_pct / 100.0):
        return {"verdict": "weak", "label": "融資餘額創高，散戶大量進場", "pct_vs_recent_high": pct_vs_recent_high,
                "detail": f"今天融資餘額達近期最高的{pct_vs_recent_high}%，接近或創新高，"
                          f"散戶已大量進場接盤，主力容易趁高檔出貨，留意風險。"}
    if recent_low > 0 and current_balance <= recent_low * (near_low_pct / 100.0):
        return {"verdict": "strong", "label": "融資餘額低檔，散戶還沒進場", "pct_vs_recent_high": pct_vs_recent_high,
                "detail": f"今天融資餘額接近近期低點，散戶還沒大量進場，主力還有操作空間，續抱風險較低。"}
    return {"verdict": "neutral", "label": "中段", "pct_vs_recent_high": pct_vs_recent_high,
            "detail": f"今天融資餘額為近期最高的{pct_vs_recent_high}%，介於高低檔之間，續觀察。"}


# ==============================================================================
# 三之十四、Step 1 VWAP升級（R96新增，累積清單第7項）——用5分K反推近似
# VWAP(Typical Price加權平均法)，比原本的高低區間百分位更貼近機構執行基準。
# ==============================================================================
def calc_intraday_vwap_from_bars(bars):
    """
    用5分K bars反推近似VWAP（Typical Price加權平均法）：
    VWAP ≈ Σ(典型價_i × 量_i) / Σ(量_i)，典型價 = (High+Low+Close)/3。

    這不是精確VWAP（精確版需要逐筆成交價量，我們沒有那麼細的資料），是
    業界公認、資料不夠精細時的標準近似算法，跟只用日K高低區間百分位比，
    更貼近「大部分成交量發生在哪個價位」。

    bars: list of dict（intraday_5min_bars格式，需要High/Low/Close/Volume
    或high/low/close/volume欄位皆可，這裡兩種鍵名都會嘗試）。

    回傳 float（近似VWAP）或None（沒有有效K棒/總量為0時，不假裝有答案）。
    """
    if not bars:
        return None
    total_pv, total_v = 0.0, 0.0
    for b in bars:
        h = b.get('High', b.get('high'))
        l = b.get('Low', b.get('low'))
        c = b.get('Close', b.get('close'))
        v = b.get('Volume', b.get('volume'))
        if h is None or l is None or c is None or v is None or v <= 0:
            continue
        typical = (h + l + c) / 3.0
        total_pv += typical * v
        total_v += v
    if total_v <= 0:
        return None
    return round(total_pv / total_v, 2)


def evaluate_vwap_position(curr_price, vwap):
    """
    站不站得上VWAP——依附件29：尾盤站回均價線之上=多方守住，明天有機會
    延續；跌破均價線=空方壓境，今天該清掉。這裡只做「現價 vs VWAP」的
    單純位置判斷，不含附件29額外要求的「尾盤時段」跟「量能配合」——那些
    留給呼叫端自行決定要不要疊加（例如只在收盤前30分鐘才呼叫這個判斷，
    搭配Step4時段選關使用）。

    回傳 dict：{verdict, label, vwap, deviation_pct, detail}
    """
    if curr_price is None or vwap is None or vwap <= 0:
        return {"verdict": "unknown", "label": "資料不足", "vwap": vwap, "deviation_pct": None,
                "detail": "缺少VWAP或現價資料，無法判斷。"}

    deviation_pct = round((curr_price - vwap) / vwap * 100, 2)
    if curr_price >= vwap:
        return {"verdict": "strong", "label": "站上均價線", "vwap": vwap, "deviation_pct": deviation_pct,
                "detail": f"現價{curr_price}站上VWAP({vwap})，乖離{deviation_pct:+.2f}%，多方守住。"}
    return {"verdict": "weak", "label": "跌破均價線", "vwap": vwap, "deviation_pct": deviation_pct,
            "detail": f"現價{curr_price}跌破VWAP({vwap})，乖離{deviation_pct:+.2f}%，空方壓境。"}


# 四、核心評分邏輯（多因子共振評分引擎，R40起改用因子註冊表架構）
# ==============================================================================
# 四之一、因子註冊表——加因子＝寫新函式+一行register，不動舊因子。
# 因子函式簽名：fn(ctx:dict) -> (delta:int, reason)。
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
    """
    外資單日買賣超：買超 +1／賣超 -1。

    【R97修復，見開發歷程.md】原本沒有None防護——網頁版呼叫端一律預設
    foreign_buy=0.0（不是None），從沒撞到這個問題；system_scheduler.py
    新增的compute_full_signal_for在籌碼抓取失敗時傳None進來，直接讓這裡
    的fb>0比較拋出TypeError，連帶讓候選池篩選整批股票評分失敗。補上跟
    其他籌碼/基本面因子一致的None防護（缺資料=不觸發，不報錯）。
    """
    fb = ctx["foreign_buy"]
    if fb is None:
        return 0, None
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
    【R41 新增，R58精確化】法人持續性：外資連續買超，代表不是單日突襲、
    是持續性買超，+2。

    【R58】原本這裡只有「5日/10日買超方向是否一致」這個代理判斷，理由是
    calculate_signals_worker當時只彙總到5日/10日總量，沒有逐日明細可以
    精確判斷「恰好連續3天」。後來發現逐日明細(inst_df)其實本來就已經抓
    好、只是沒有傳到這一層——R58把它接上，foreign_buy_streak3現在是
    「最新3天是否每天都外資買超」的精確結果，不再是方向代理。

    向下相容：foreign_buy_streak3為None時（呼叫端沒有逐日資料，例如
    system_scheduler.py目前的精簡版訊號計算），自動退回舊版5日/10日
    方向代理，不會報錯也不會漏判。
    """
    streak3 = ctx.get("foreign_buy_streak3")
    if streak3 is not None:
        if streak3:
            return 2, "法人持續性(連續3日買超)"
        return 0, None

    f5, f10 = ctx.get("foreign_buy_5d"), ctx.get("foreign_buy_10d")
    if f5 is None or f10 is None:
        return 0, None
    if f5 > 0 and f10 > 0:
        return 2, "法人持續性(10日同向續買·代理判斷)"
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
                         day_trader_alert=False, trend_gate_triggered=False):
    """
    套用「一票否決／強制調整」類規則——這些不是簡單加減分，是在因子加總完成
    後，依照特定條件覆蓋或壓制總分。順序跟原本 determine_signal 完全一致：
    大盤位階降級 → 爆量下殺強制偏空 → 趨勢資格硬閘門 → 末日熔斷 → 隔日沖警示。

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

    【R96新增，累積清單第1+2項】趨勢資格硬閘門：股價連續3天收在月線
    (MA20)下方時觸發（見evaluate_trend_qualification_gate），強制把分數
    壓到-6以下——比爆量下殺(-3)更嚴格，直接確保落入classify_score的
    「🔵偏空防守」區間，不是「⚠️轉弱謹慎」。這是刻意的設計：批次一分析
    這是整套框架信心最高、最不可退讓的核心規則（同一條規則被原作者用
    兩張不同的圖重複強調），該是「一票否決」而不是溫和降級——不能讓
    基本面/籌碼分數再高，也蓋不過「月線已經破3天」這個事實。
    trend_gate_triggered預設False，向下相容——呼叫端沒有算這個閘門時
    （例如還沒把evaluate_trend_qualification_gate接進呼叫端），行為
    等同這次新增之前，不會報錯也不會誤觸發。
    """
    if not market_bull:
        if 6 <= score < 8:
            score = 5; reasons.append("🌧️ 大盤破20MA·降級(門檻提高至8)")

    if is_volume_dump:
        score = min(score, -3); reasons.append("🚨 爆量下殺·主力出貨")

    if trend_gate_triggered:
        score = min(score, -7); reasons.append("⛔ 趨勢資格不符(連續3天破月線)·無條件出場")

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


def compute_landmine_flag(symbol, curr_price, rev_yoy, f_5d, token=None, pe_years=3, sb=None):
    """
    【R97補做，總指揮官確認：地雷警訊要接上排程】跟網頁版
    calculate_signals_worker的is_expensive/landmine公式完全對齊：
    估值百分位>=80(或抓不到百分位、退回PE>PE_LANDMINE的固定倍數備援)
    + 營收年增衰退 + 外資5日賣超，三者同時成立才判定地雷。

    rev_yoy/f_5d由呼叫端傳入（compute_full_signal_for已經算過這兩個值，
    不在這裡重算，避免同一份資料抓兩次）。

    EPS用「用最新PER反推」的方式取得（curr_price / 最新PER），跟網頁版
    trailingEps抓不到時的備援路徑一致——這裡刻意不額外呼叫yfinance
    Ticker.info多抓一次trailingEps，直接用反推法，跟fetch_pe_history
    共用同一次FinMind呼叫取得的資料，不多花一次API成本。

    回傳 bool。任何一段資料抓不到，保守回傳False（不誤判成地雷，也不假裝
    有地雷警訊），不中斷呼叫端的整體評分流程。
    """
    try:
        pe_hist_df = fetch_pe_history(symbol, token, years=pe_years, sb=sb)
        if pe_hist_df is None or pe_hist_df.empty or 'PER' not in pe_hist_df.columns:
            return False
        valid_pe = pe_hist_df['PER'].dropna()
        valid_pe = valid_pe[valid_pe > 0]
        if valid_pe.empty or curr_price <= 0:
            return False

        latest_per = float(valid_pe.iloc[-1])
        if latest_per <= 0:
            return False
        eps = round(curr_price / latest_per, 2)
        pe = round(curr_price / eps, 1) if eps > 0 else 0.0

        percentile = None
        if len(valid_pe) >= 60 and pe > 0:
            percentile = round(float((valid_pe < pe).mean() * 100), 1)

        is_expensive = ((percentile is not None and percentile >= 80)
                        or (percentile is None and eps > 0 and pe > PE_LANDMINE))
        return bool(is_expensive and (rev_yoy is not None and rev_yoy < 0)
                    and (f_5d is not None and f_5d < 0))
    except Exception as e:
        print(f"[compute_landmine_flag] {symbol} 計算失敗，保守回傳False："
              f"{type(e).__name__}: {e}")
        return False


def determine_signal(current_price, ma5, ma20, foreign_buy, vol_ratio, is_open_high_close_low,
                     buffer_pct, gain=0.0, enable_doomsday=False,
                     market_bull=True, landmine=False, is_volume_dump=False,
                     ma60=None, trust_buy=None, foreign_buy_5d=None, foreign_buy_10d=None,
                     rev_mom=None, rev_yoy=None, day_trader_alert=False,
                     foreign_buy_streak3=None, trend_gate_triggered=False):
    """
    ⚠️⚠️⚠️【R97強制規定，見開發歷程.md「評分邏輯稽核」章節】⚠️⚠️⚠️
    這個函式的參數清單，就是這個系統所有風控/加分機制的完整清單。
    只要你「新增/刪除/改名這個函式的任何參數」，或「修改任何一個呼叫端
    （determine_signal(...)的呼叫處，目前有v160.py跟system_scheduler.py
    兩處）」，動手改之前跟改完之後，都必須執行一次：
        python3 audit_scoring_wiring.py
    這支腳本會自動比對「這個函式支援哪些參數」vs「每個呼叫端實際傳了
    哪些參數」，抓出「支援但從沒被任何呼叫端傳遞過」的參數——這正是
    R97就任by這種方式抓到is_volume_dump/trend_gate_triggered/
    market_bull/landmine四個被靜默漏接的真實案例，不是假設性的預防措施。

    多因子共振評分引擎（R40起改用因子註冊表架構，見上方 ADDITIVE_FACTORS；
    R41新增均線糾結+爆量/法人共振/法人持續性/營收動能四個因子+隔日沖警示）。

    R41新增的參數全部預設 None/False——呼叫端沒有提供這些資料時（例如排程端
    目前還沒有籌碼/基本面資料管線，規劃在R41的排程資料抓取一起補上前），
    對應的新因子就是「因為缺資料而不觸發」，不會報錯也不會亂猜，行為等同
    R41之前的舊版。這是刻意設計成向下相容，讓網頁版跟排程端可以分階段
    採用新因子，不用同一輪一次全部改完。

    day_trader_alert：見 check_day_trader_alert 的說明，目前只有手動查證
    某檔股票、有分點資料時才有意義（批次全市場掃描沒有分點資料）。

    【R58新增】foreign_buy_streak3：法人持續性因子的精確版信號（連續3天外資
    買超與否，True/False/None）。同樣預設None、向下相容——沒傳就是「不知道」，
    因子函式會自動退回舊版的5日/10日方向代理，不會報錯。

    【R96新增】trend_gate_triggered：趨勢資格硬閘門是否觸發（呼叫端用
    evaluate_trend_qualification_gate(hist)算出來後傳進來）。預設False，
    向下相容——沒傳就是「沒有這個資訊」，不會誤觸發強制出場。
    """
    ctx = {"price": current_price, "ma5": ma5, "ma20": ma20, "ma60": ma60,
           "foreign_buy": foreign_buy, "trust_buy": trust_buy,
           "foreign_buy_5d": foreign_buy_5d, "foreign_buy_10d": foreign_buy_10d,
           "foreign_buy_streak3": foreign_buy_streak3,
           "vol_ratio": vol_ratio, "is_ohcl": is_open_high_close_low,
           "buffer_pct": buffer_pct, "landmine": landmine, "gain": gain,
           "rev_mom": rev_mom, "rev_yoy": rev_yoy}
    score, reasons = run_additive_factors(ctx)
    score, reasons = apply_override_rules(score, reasons, market_bull, is_volume_dump,
                                          enable_doomsday, gain, buffer_pct,
                                          day_trader_alert=day_trader_alert,
                                          trend_gate_triggered=trend_gate_triggered)
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


def _parse_mis_book(price_str, vol_str):
    """
    【R96新增，Step 5五檔節奏】解析MIS端點的五檔委買/委賣字串——價格跟張數
    各自是用底線分隔的字串（例如 "607.0_606.0_605.0_604.0_603.0"），兩條字串
    要一一對應配對。空字串、"-"、配對數量對不上時，回傳空list，不硬湊資料
    （對不上代表這次快照本身就不完整，寧可讓呼叫端知道「這次沒有五檔資料」，
    不要用錯位配對出一組看起來像資料、實際上是亂湊的五檔）。

    回傳 [(price, volume), ...]，最多5筆，價格/張數皆為float。
    """
    if not price_str or not vol_str or price_str == "-" or vol_str == "-":
        return []
    try:
        prices = [float(p) for p in price_str.strip('_').split('_') if p]
        vols = [float(v) for v in vol_str.strip('_').split('_') if v]
    except (ValueError, TypeError):
        return []
    if len(prices) != len(vols) or not prices:
        return []
    return list(zip(prices, vols))[:5]


def _fetch_and_parse_mis_chunk(chunk):
    """
    【R97抽出，供fetch_twse_mis_batch()主迴圈+拆批次重試共用同一份解析
    邏輯，不要兩處各自維護一份】對單一chunk(最多100組(symbol,ex)配對)
    發一次請求並解析，回傳 (results_dict, missing_pairs_list)。
    失敗時直接raise，呼叫端自行決定要不要重試/拆批次。
    """
    ex_ch = "|".join(f"{ex}_{sym}.tw" for sym, ex in chunk)
    results = {}
    missing_pairs = []
    resp = _SESSION.get("https://mis.twse.com.tw/stock/api/getStockInfo.jsp",
                        params={"ex_ch": ex_ch, "json": "1", "delay": "0"}, timeout=6)
    data = resp.json()
    if data.get("rtcode") != "0000":
        # 【R97續16新增，診斷】原本這裡靜默return，查不出「這次到底是
        # 沒查到資料還是被限流」。TWSE MIS已知有「5秒內最多3次請求，
        # 超過暫時鎖IP」的限制——rtcode不是"0000"時印出實際值，被鎖時
        # 通常會是別的rtcode或rtmessage帶錯誤訊息，方便事後從log判斷。
        print(f"[即時報價-診斷] rtcode非0000（可能被TWSE MIS限流/鎖IP）："
              f"rtcode={data.get('rtcode')!r}, rtmessage={data.get('rtmessage')!r}，"
              f"這批{len(chunk)}組全部視為missing。")
        return results, missing_pairs
    _returned_syms = set()
    for item in data.get("msgArray", []):
        sym = str(item.get("c", "")).strip()
        if not sym:
            continue
        _returned_syms.add(sym)
        # 【R62修復】原本z(最近成交)查不到時會依序退回o(開盤)/y(昨收)
        # 冒充即時價顯示——查無成交價寧可誠實顯示「—」，不假裝有資料。
        _z = item.get("z", "-")
        try:
            _price = float(_z) if _z and _z != "-" else None
        except (ValueError, TypeError):
            _price = None
        if _price is None:
            # 【R96新增，診斷用】原本直接continue、沒留線索。加這行
            # 診斷log，方便分辨是真的沒新成交、還是exchange判斷錯誤。
            print(f"[即時報價-診斷] {sym}：z欄位原始值={item.get('z')!r}，"
                  f"無法轉成價格，這次跳過（其餘欄位可能仍有效）。")
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
            "volume_cum": _safe_mis_float(item.get("v")),
            "time": item.get("t", ""), "date": item.get("d", ""),
            # 【R96新增，Step 5五檔】mis.twse.com.tw本來就有回傳五檔
            # 委買/委賣資料，b/a是價格字串(底線分隔)，g/f是對應張數。
            "bids": _parse_mis_book(item.get("b"), item.get("g")),
            "asks": _parse_mis_book(item.get("a"), item.get("f")),
            "ok": True,
        }
    # 【R96新增，診斷用】區分「有回應但z是空的」跟「這個組合根本沒被
    # 端點回應」（後者通常是tse/otc判斷錯誤），方便定位查不到的原因。
    _requested_syms = {sym for sym, _ex in chunk}
    _missing = _requested_syms - _returned_syms
    if _missing:
        print(f"[即時報價-診斷] 這批查詢完全沒有回應（可能是tse/otc"
              f"判斷錯誤，或該代號當下沒有掛在這個組合下）：{sorted(_missing)}")
        for _sym, _ex in chunk:
            if _sym in _missing:
                missing_pairs.append((_sym, _ex))
    return results, missing_pairs


def _fetch_twse_mis_chunk_with_split(chunk, min_size=25):
    """
    【R97新增，總指揮官要求：502這種整批失敗要有備案，不能只靠單一端點
    硬扛】對一個chunk嘗試直接抓，失敗就對半拆成兩份各自遞迴重試——
    如果失敗只是「這個時間點端點對這麼大量的請求不穩」，縮小請求量
    仍有機會部分成功，不用整批全部放棄。拆到min_size(預設25組)以下
    就不再拆，直接放棄那一小段（避免過度拆分導致請求次數暴增，25組
    大約還能接受）。

    回傳 results_dict（拆到底還是失敗的部分，那些symbol不會出現在
    結果裡，呼叫端會自然把它們當成「這次沒抓到」處理，不會假裝有資料）。
    """
    try:
        results, _missing = _fetch_and_parse_mis_chunk(chunk)
        return results
    except Exception as e:
        if len(chunk) <= min_size:
            print(f"[即時報價] 拆到{len(chunk)}組仍失敗，這一小段放棄："
                  f"{[s for s, _ex in chunk]}，錯誤：{e}")
            return {}
        mid = len(chunk) // 2
        left_results = _fetch_twse_mis_chunk_with_split(chunk[:mid], min_size=min_size)
        right_results = _fetch_twse_mis_chunk_with_split(chunk[mid:], min_size=min_size)
        return {**left_results, **right_results}


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
    _all_missing_pairs = []   # 【R96新增】累積所有批次裡完全沒回應的(sym, ex)，供結束後用反向交易所重試
    BATCH = 100
    for i in range(0, len(symbol_ex_pairs), BATCH):
        chunk = symbol_ex_pairs[i:i + BATCH]
        try:
            _chunk_results, _chunk_missing = _fetch_and_parse_mis_chunk(chunk)
            results.update(_chunk_results)
            _all_missing_pairs.extend(_chunk_missing)
        except Exception as e:
            print(f"[即時報價] 批次抓取失敗：{e}——這批{len(chunk)}組，改成拆成小批次重試"
                  f"（總指揮官要求：整批失敗不該直接放棄整批，拆小批次至少搶救部分資料）。")
            # 【R97新增，總指揮官要求：502這種整批失敗要有備案，不能只靠
            # 單一端點硬扛】拆成小批次重試——如果是「這個時間點端點短暫
            # 不穩」，縮小請求量、分開送出，仍有機會部分成功，不用整批
            # 全部放棄。遞迴對半拆，最小拆到25組就不再拆（避免過度拆分
            # 導致請求次數暴增），拆到底仍失敗的部分才真的放棄。
            _sub_results = _fetch_twse_mis_chunk_with_split(chunk, min_size=25)
            results.update(_sub_results)
            continue

    # 【R96新增，總指揮官反映特定股票長期即時報價空白】完全沒回應的代號，
    # 很可能是tse/otc交易所判斷錯誤——這裡不用等外部呼叫端下次重新猜，
    # 直接在這裡用「相反的交易所」重試一次，一次到位解決持續性的判斷
    # 錯誤，不會因為連續猜錯而讓某檔股票長期顯示空白。只在真的有缺漏
    # 時才多打這次請求，正常情況（大多數代號都有回應）完全不受影響。
    if _all_missing_pairs:
        _retry_pairs = [(sym, "otc" if ex == "tse" else "tse") for sym, ex in _all_missing_pairs]
        print(f"[即時報價-診斷] 對{len(_retry_pairs)}檔完全沒回應的代號，"
              f"用相反的交易所重試一次：{_retry_pairs}")
        try:
            _retry_ex_ch = "|".join(f"{ex}_{sym}.tw" for sym, ex in _retry_pairs)
            _retry_resp = _SESSION.get("https://mis.twse.com.tw/stock/api/getStockInfo.jsp",
                                       params={"ex_ch": _retry_ex_ch, "json": "1", "delay": "0"}, timeout=6)
            _retry_data = _retry_resp.json()
            if _retry_data.get("rtcode") == "0000":
                _retry_recovered = []
                for item in _retry_data.get("msgArray", []):
                    sym = str(item.get("c", "")).strip()
                    if not sym or sym in results:
                        continue
                    _z = item.get("z", "-")
                    try:
                        _price = float(_z) if _z and _z != "-" else None
                    except (ValueError, TypeError):
                        _price = None
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
                        "volume_cum": _safe_mis_float(item.get("v")),
                        "time": item.get("t", ""), "date": item.get("d", ""),
                        "bids": _parse_mis_book(item.get("b"), item.get("g")),
                        "asks": _parse_mis_book(item.get("a"), item.get("f")),
                        "ok": True,
                    }
                    _retry_recovered.append(sym)
                if _retry_recovered:
                    print(f"[即時報價-診斷] 相反交易所重試後恢復{len(_retry_recovered)}檔："
                          f"{_retry_recovered}——這證實原本的tse/otc判斷確實錯了。")
        except Exception as e:
            print(f"[即時報價-診斷] 相反交易所重試本身失敗：{e}")
    return results


def classify_trade_side(price, bids, asks):
    """
    【R96新增，累積清單「內外盤成交比率」】Tick rule分類——這筆成交價
    (price)相對當下五檔的位置，判斷是主動買(外盤)還是主動賣(內盤)：
      price >= 最佳委賣價(asks[0][0]) → 'outer'（外盤：買方主動貼著賣價
      成交，願意付更高價格買，代表買盤積極）
      price <= 最佳委買價(bids[0][0]) → 'inner'（內盤：賣方主動貼著買價
      成交，願意賠本賣出，代表賣壓積極）
      介於買賣價之間（少見，通常是跳動點成交或流動性極佳時的價格改善）
      → 'mid'，無法明確歸類，呼叫端通常對半分配

    bids/asks格式跟fetch_twse_mis_batch/_parse_mis_book一致：
    [(price, volume), ...]，bids由高到低、asks由低到高排列，[0]就是
    最佳買賣價。price或bids/asks缺值時回傳None，不假裝能判斷。
    """
    if price is None or not bids or not asks:
        return None
    try:
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
    except (IndexError, TypeError, ValueError):
        return None
    if price >= best_ask:
        return 'outer'
    if price <= best_bid:
        return 'inner'
    return 'mid'


def aggregate_intraday_snapshots_to_bars(snapshots, bar_minutes=5):
    """
    【R95續28新增】自建5分K的核心聚合邏輯——把一串「輪詢即時報價得到的原始
    快照」，組裝成5分鐘OHLCV K棒。故意抽成獨立、不碰網路/Supabase的純函式，
    這樣可以完全不用真的在盤中執行就先測試邏輯對不對（用假的快照資料）。

    snapshots: list of dict，每筆至少要有：
      - symbol: 股票代號
      - poll_time: 'HH:MM:SS' 格式的輪詢時間（本地/台灣時間）
      - price: 那一刻的成交價（可能是None，代表那次輪詢剛好沒抓到）
      - volume_cum: 當天累計到那一刻的成交量（股），也可能是None

    【R96新增，累積清單「內外盤成交比率」】snapshots額外可以帶bids/asks
    （fetch_twse_mis_batch順手回傳的五檔，跟五檔買盤結構Step5共用同一批
    資料，不多打API）——有帶的話，這裡會用tick rule把「這一段時間的
    成交量」分類成外盤(主動買)/內盤(主動賣)/中間(無法明確歸類，對半分)，
    累加進每根K棒的outer_volume/inner_volume。沒帶bids/asks時，這兩個
    欄位是0，不影響其餘OHLCV計算，向下相容舊的呼叫端。

    回傳 {symbol: [bar_dict, ...]}，每個bar_dict：
      {bar_time, open, high, low, close, volume, sample_count,
       outer_volume, inner_volume}
    bar_time是這根K棒的「起始時間」（HH:MM，分鐘捨去到bar_minutes的整數倍，
    例如09:27捨去成09:25）。

    設計決策：
    - 一根K棒內完全沒有任何有效價格樣本時，直接跳過、不硬湊一根假K棒——
      跟這個專案一路以來「沒有真實資料寧可誠實缺席，不要冒充」的原則一致。
    - volume是「這根K棒結束時的累計量」減「上一根有效K棒結束時的累計量」，
      不是這根K棒內樣本的加總（那樣會重複計算，因為v本身就是累計值）。
    - 內外盤分類改成「逐筆快照」計算delta量，不是整根K棒才算一次——這樣
      同一根K棒內如果先有外盤成交、後有內盤成交，才不會被平均掉、能保留
      盤中真正的買賣力道變化細節。這裡改成單一迴圈依時間順序逐筆處理
      （不再是先分桶、後統一算量），跨K棒邊界的delta量也能正確歸屬到
      正確的那一根K棒。
    - sample_count保留下來當資料品質指標——之後如果某根K棒的sample_count
      異常低（例如輪詢間隔中斷過），使用者/後續分析可以自己決定要不要
      信任那根K棒。
    """
    from collections import defaultdict
    by_symbol = defaultdict(list)
    for s in snapshots:
        by_symbol[s['symbol']].append(s)

    def _bucket_start(t_str):
        parts = t_str.split(':')
        h, m = int(parts[0]), int(parts[1])
        bucket_m = (m // bar_minutes) * bar_minutes
        return f"{h:02d}:{bucket_m:02d}"

    result = {}
    for sym, items in by_symbol.items():
        items = sorted(items, key=lambda x: x['poll_time'])

        # 【R96新增】逐筆依時間順序處理，同時算OHLC樣本跟外盤/內盤delta量，
        # 一次迴圈完成，不用先分桶再回頭算——delta量本來就要跟「前一筆
        # 快照」比較，逐筆處理比先分桶更自然，也才能正確跨越K棒邊界歸屬。
        bar_data = defaultdict(lambda: {'prices': [], 'sample_count': 0,
                                        'outer_volume': 0.0, 'inner_volume': 0.0})
        _prev_cum_vol = None
        _prev_bar_time_for_vol = {}   # bar_time -> 該K棒結束時的累計量（給OHLC的volume用）
        for it in items:
            bt = _bucket_start(it['poll_time'])
            price = it.get('price')
            if price is not None:
                bar_data[bt]['prices'].append(price)
            bar_data[bt]['sample_count'] += 1

            cum_vol = it.get('volume_cum')
            if cum_vol is not None:
                _prev_bar_time_for_vol[bt] = cum_vol
                if _prev_cum_vol is not None:
                    _delta = max(0.0, cum_vol - _prev_cum_vol)
                    if _delta > 0:
                        _side = classify_trade_side(price, it.get('bids'), it.get('asks'))
                        if _side == 'outer':
                            bar_data[bt]['outer_volume'] += _delta
                        elif _side == 'inner':
                            bar_data[bt]['inner_volume'] += _delta
                        elif _side == 'mid':
                            bar_data[bt]['outer_volume'] += _delta / 2
                            bar_data[bt]['inner_volume'] += _delta / 2
                        # _side是None（缺bids/asks）時，這筆delta量不分類，
                        # 不硬塞進外盤或內盤，維持外盤+內盤加總可能小於
                        # 總量的誠實狀態，而不是亂猜一個歸屬。
                _prev_cum_vol = cum_vol

        bars = []
        _prev_vol_for_ohlc = None
        for bar_time in sorted(bar_data.keys()):
            d = bar_data[bar_time]
            if not d['prices']:
                continue
            _last_vol = _prev_bar_time_for_vol.get(bar_time)
            volume = None
            if _last_vol is not None and _prev_vol_for_ohlc is not None:
                volume = max(0, _last_vol - _prev_vol_for_ohlc)
            bars.append({
                'bar_time': bar_time,
                'open': d['prices'][0], 'high': max(d['prices']), 'low': min(d['prices']),
                'close': d['prices'][-1], 'volume': volume, 'sample_count': d['sample_count'],
                'outer_volume': round(d['outer_volume'], 1), 'inner_volume': round(d['inner_volume'], 1),
            })
            if _last_vol is not None:
                _prev_vol_for_ohlc = _last_vol
        result[sym] = bars
    return result


def validate_intraday_bars_vs_daily(bars, daily_open, daily_high, daily_low, tolerance_pct=1.0):
    """
    【R95續29新增】自建5分K的回溯驗證——總指揮官提出：與其只能被動等資料
    慢慢累積、日後才發現組裝邏輯有問題，不如拿已經很可靠的日K資料（開盤價/
    當日最高/最低）當基準交叉比對，及早抓出系統性錯誤（例如交易所tse/otc
    判斷錯、抓錯股票、單位換算錯這類會讓整批資料都不對勁的問題）。

    純函式，不碰網路/Supabase，方便獨立測試——呼叫端(排程/網頁版之後想用
    都可以)自己準備好bars(aggregate_intraday_snapshots_to_bars的輸出格式)
    跟當天的日K資料，這裡只負責比對邏輯。

    檢查兩件事：
    1. 09:25那根K棒的開盤價，應該要跟當天真正的開盤價很接近（照理說9:25
       已經是開盤後第一分鐘，價格不會跟開盤價差太多）。
    2. 收集到的所有K棒的最高/最低，理論上不可能超出「當天全天」的最高/
       最低範圍——9:25-9:50只是全天的一部分，全天的高低點涵蓋這段時間的
       高低點是數學上一定成立的關係，如果違反了，代表資料本身有問題
       （抓錯股票、單位算錯之類）。

    tolerance_pct：容許的誤差百分比，預設1%——抓的是「明顯不對勁」，不是
    要求分毫不差（分點資料抓取的時間點跟官方日K收盤定案的時間點本來就
    不會完全一致，允許一點誤差是合理的）。

    回傳 {'ok': bool, 'issues': [str,...]}——ok=True代表沒發現異常，
    issues是空list或問題描述list。呼叫端可以自己決定要印log、發Telegram、
    或存進資料庫當品質紀錄，這裡只負責判斷本身。
    """
    issues = []
    if not bars:
        return {'ok': False, 'issues': ['沒有收集到任何K棒，無法驗證']}
    if daily_open is None or daily_high is None or daily_low is None:
        return {'ok': False, 'issues': ['沒有可用的日K基準資料，無法驗證']}

    _tol = daily_high * (tolerance_pct / 100.0) if daily_high else 0

    _first_bar = sorted(bars, key=lambda b: b['bar_time'])[0]
    if _first_bar.get('open') is not None:
        _open_diff_pct = abs(_first_bar['open'] - daily_open) / daily_open * 100 if daily_open else None
        if _open_diff_pct is not None and _open_diff_pct > tolerance_pct * 3:
            # 開盤第一根K棒的容許誤差稍微放寬(3倍)——9:25已經是開盤後第一
            # 分鐘，可能已經有一些價格變動，不像「當下這一刻」要求那麼嚴格。
            issues.append(f"09:25開盤價({_first_bar['open']})跟官方日K開盤價({daily_open})"
                          f"差距{_open_diff_pct:.1f}%，超出容許範圍，可能抓錯股票或交易所判斷錯")

    _collected_highs = [b['high'] for b in bars if b.get('high') is not None]
    _collected_lows = [b['low'] for b in bars if b.get('low') is not None]
    if _collected_highs and max(_collected_highs) > daily_high + _tol:
        issues.append(f"收集到的最高價({max(_collected_highs)})超過官方日K當天最高價"
                      f"({daily_high})，數學上不該發生，資料本身有問題")
    if _collected_lows and min(_collected_lows) < daily_low - _tol:
        issues.append(f"收集到的最低價({min(_collected_lows)})低於官方日K當天最低價"
                      f"({daily_low})，數學上不該發生，資料本身有問題")

    return {'ok': len(issues) == 0, 'issues': issues}


# ==============================================================================
# 五、千張大戶（TDCC集保股權分散表）共用解析邏輯——R70新增，正確網域是
# opendata.tdcc.com.tw，可排程自動抓取，CSV上傳保留當備援。
# ------------------------------------------------------------------------------
def _parse_holding_level_lower(level):
    """
    解析 FinMind／TDCC 股東持股分級表的級距字串，回傳該級距的「下界股數」。

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


def parse_tdcc_holding_csv(raw_bytes):
    """
    【R69新增，R70搬進共用模組】解析TDCC集保戶股權分散表CSV（全市場，
    每週更新一次）。原始byte內容進來，回傳DataFrame[symbol, level_lower,
    shares]，或None（格式不對、不是這份CSV）。

    【R95修復——重大根因】原本這裡直接把「持股分級」欄位丟給
    _parse_holding_level_lower()解析，該函式是為FinMind的文字級距格式
    （如'1-999'、'1,000,001以上'）設計的。但總指揮官提供的實際
    opendata.tdcc.com.tw即時資料（親自fetch驗證過）證實：TDCC這份CSV的
    「持股分級」欄位根本不是文字級距，是純數字代碼1~17（例如友達那類
    股票的第15碼才是「1,000,001以上」，16是差異調整列，17是合計列）。
    用_parse_holding_level_lower解析「17」這種代碼字串，regex抓到的是
    代碼本身（17），跟千張大戶判斷用的門檻「level_lower>=1,000,000」
    差了五個數量級，導致：
      1) 大戶(千張以上)加總「永遠」是0——這不是2409特有的問題，是這個
         功能自R69/R70建立以來、對「每一檔股票、每一週」都成立的系統性
         bug，總指揮官這次查到2409的0.00%只是第一個被抓到的樣本。
      2) 合計列(代碼17)的股數又被當成一般級距重複加進total，導致總股數
         被算成兩倍（若同時遇到情況1，這個放大誤差不影響大戶比例，但
         會讓散戶比例被拉低）。
    修復：新增TDCC官方17級代碼→實際股數下界的對照表，代碼1~15對應真正
    的股數門檻，代碼16(差異)/17(合計)不是真實級距、直接排除，避免雙重
    計算。下游compute_big_holder_ratios/compute_small_holder_ratios完全
    不用改，因為它們是根據level_lower數值做判斷，這裡把level_lower填對
    之後，下游邏輯自動就對了。
    """
    try:
        text = raw_bytes.decode('utf-8', errors='ignore')
        if '證券代號' not in text[:2000] and '股票代號' not in text[:2000]:
            text = raw_bytes.decode('big5', errors='ignore')  # 保險：不同時期版本編碼可能不同
    except Exception:
        return None
    try:
        # 【R95追加修復】強制用字串讀取證券代號欄位，避免pandas把001xxx這種
        # 帶前導0的代號推斷成int64截斷掉0。
        df = pd.read_csv(io.StringIO(text), dtype=str)
    except Exception:
        return None
    df.columns = [str(c).strip() for c in df.columns]
    _rename = {}
    for c in df.columns:
        if '證券代號' in c or '股票代號' in c:
            _rename[c] = 'symbol'
        elif '持股分級' in c:
            _rename[c] = 'level'
        elif c == '股數' or ('股數' in c and '比例' not in c and '人數' not in c):
            _rename[c] = 'shares'
    df = df.rename(columns=_rename)
    if not {'symbol', 'level', 'shares'}.issubset(df.columns):
        return None
    df['symbol'] = df['symbol'].astype(str).str.strip()
    df['shares'] = pd.to_numeric(df['shares'], errors='coerce')
    # 【R95】TDCC官方17級代碼實際股數下界（代碼1~15），16=差異、17=合計，
    # 兩者都不是真實級距、直接排除，不併入level_lower映射，靠dropna濾掉。
    _tdcc_level_lower = {
        1: 1, 2: 1000, 3: 5001, 4: 10001, 5: 15001, 6: 20001, 7: 30001,
        8: 40001, 9: 50001, 10: 100001, 11: 200001, 12: 400001,
        13: 600001, 14: 800001, 15: 1000001,
    }
    _level_code = pd.to_numeric(df['level'], errors='coerce')
    df['level_lower'] = _level_code.map(_tdcc_level_lower)
    df = df.dropna(subset=['shares', 'level_lower'])
    return df[['symbol', 'level_lower', 'shares']] if not df.empty else None


def compute_big_holder_ratios(df):
    """
    【R69新增，R70搬進共用模組】把parse_tdcc_holding_csv解析出來的全市場
    明細，彙總成每檔股票的千張大戶比例（level_lower>=1,000,000股的加總 /
    該股票總股數×100）。千張＝1000張＝1,000,000股，跟FinMind千張大戶判斷
    用同一個門檻，兩者定義一致。

    回傳 dict {symbol: ratio_pct}。
    """
    out = {}
    for sym, grp in df.groupby('symbol'):
        total = grp['shares'].sum()
        if total <= 0:
            continue
        big = grp.loc[grp['level_lower'] >= 1_000_000, 'shares'].sum()
        out[str(sym)] = round(float(big) / float(total) * 100, 2)
    return out


def compute_small_holder_ratios(df):
    """
    【R90新增】散戶（十張以下）持股比例——總指揮官指出集保戶股權分散表
    同時能判斷「大戶增減」跟「散戶增減」，原本只做了大戶端(千張以上)，
    散戶端一直沒做。這份資料本身就是全級距明細(parse_tdcc_holding_csv
    解析出來的df本來就含所有級距，不是只有千張以上那幾筆)，不用多打
    任何API，跟千張大戶用同一份df算出來的第二個指標。

    十張以下＝持股未達10,000股(level_lower < 10,000)，跟千張大戶的定義
    對稱：千張大戶看「level_lower>=1,000,000」（下界達標），散戶看
    「level_lower<10,000」（下界未達一個交易單位10張）。

    回傳 dict {symbol: ratio_pct}——這個比例通常會占多數（台股散戶結構性
    持有很大比例籌碼是常態），數字本身不是重點，重點是「跟自己歷史比」
    的趨勢方向（用法比照get_big_holder_trend，是不是要新增
    get_small_holder_trend由你決定，這裡先把基礎資料算出來）。
    """
    out = {}
    for sym, grp in df.groupby('symbol'):
        total = grp['shares'].sum()
        if total <= 0:
            continue
        small = grp.loc[grp['level_lower'] < 10_000, 'shares'].sum()
        out[str(sym)] = round(float(small) / float(total) * 100, 2)
    return out


def fetch_tdcc_holding_csv_direct(timeout=30):
    """
    【R70新增】直接向TDCC官方opendata端點要當週集保戶股權分散表——
    這是自動化的核心。網址是opendata.tdcc.com.tw（不是smart.tdcc.com.tw），
    已查證這個網域沒有robots.txt限制，且有社群長期穩定使用同一個URL做
    自動化的先例。

    回傳原始bytes內容，或None（連線失敗）。呼叫端接著用
    parse_tdcc_holding_csv解析。刻意不在這裡重試太多次或用太短的timeout——
    這份CSV涵蓋全市場，檔案不小，給30秒預設值。
    """
    try:
        r = _SESSION.get("https://opendata.tdcc.com.tw/getOD.ashx?id=1-5", timeout=timeout)
        if r.status_code == 200 and r.content:
            return r.content
        print(f"[千張大戶] TDCC回應異常：HTTP {r.status_code}")
        return None
    except Exception as e:
        print(f"[千張大戶] TDCC連線失敗：{e}")
        return None


# ==============================================================================
# 六、券商分點——HiStock免費資料源（R72新增，見開發歷程.md查證過程）
# ------------------------------------------------------------------------------
def parse_histock_branch_html(html_text):
    """
    解析HiStock「券商分點買賣日報」頁面（histock.tw/stock/branch.aspx?no=X）。

    已用真實頁面驗證過表格結構：單一表格，15列x10欄，左半是「賣超排行」
    (券商名稱/買張/賣張/賣超/均價)，右半是「買超排行」(券商名稱.1/買張.1/
    賣張.1/買超.1/均價.1)，左右各15家分點、合計30家（當日買賣超前15大）。

    net_shares直接用來源網站自己算好的「賣超」/「買超」欄位，不自己用
    買張-賣張重算——測試時發現來源網站顯示的淨額偶爾跟買張-賣張手動相減
    差1（應該是原始資料本身的小數捨入），直接沿用來源的數字，避免我們
    自己算出一個跟網站顯示對不上、讓人搞混的版本。

    【R94修復】總指揮官實測發現本地電腦沒裝lxml時，pd.read_html()會拋
    ImportError——這個原本被前面的`except Exception: return None`一起
    吞掉，跟「表格結構真的跟預期不符」看起來一模一樣，都是回傳None、
    健康度檢查都顯示「0家分點」。這導致連續好幾輪都在懷疑IP被擋、網站
    改版，卻沒人想到可能只是**部署環境沒裝這個套件**這麼單純的原因。
    這裡把ImportError單獨接住、往上拋出去，不再跟其他錯誤混在一起，
    讓呼叫端能明確分辨「缺套件」跟「其他問題」，不用再靠診斷腳本一輪
    一輪排查。

    回傳DataFrame[broker_name, buy_shares, sell_shares, net_shares]
    （單位：張），或None（表格結構跟預期不符、可能是網站改版了）。
    ImportError（缺lxml/html5lib套件）不吞掉，直接往上拋，讓呼叫端能
    明確分辨這跟「網站結構問題」是不同種類的失敗。
    """
    try:
        tables = pd.read_html(io.StringIO(html_text))
    except ImportError:
        raise   # 缺套件不是「這次抓不到資料」，是環境設定問題，不能裝作沒事回傳None
    except Exception:
        return None
    if not tables:
        return None
    # 【R95續17修復】原本寫死只看tables[0]，但HiStock頁面可能有其他<table>
    # 導致分點表格不一定是第一個。改成掃描每個表格、挑欄位結構符合的那個。
    _expected = {'券商名稱', '買張', '賣張', '賣超',
                 '券商名稱.1', '買張.1', '賣張.1', '買超'}
    t = None
    for _candidate in tables:
        if _expected.issubset(set(_candidate.columns)):
            t = _candidate
            break
    if t is None:
        return None

    left = t[['券商名稱', '買張', '賣張', '賣超']].copy()
    left.columns = ['broker_name', 'buy_shares', 'sell_shares', 'net_shares']
    right = t[['券商名稱.1', '買張.1', '賣張.1', '買超']].copy()
    right.columns = ['broker_name', 'buy_shares', 'sell_shares', 'net_shares']

    combined = pd.concat([left, right], ignore_index=True)
    combined = combined.dropna(subset=['broker_name'])
    combined['broker_name'] = combined['broker_name'].astype(str).str.strip()
    combined = combined[combined['broker_name'] != '']
    for col in ('buy_shares', 'sell_shares', 'net_shares'):
        combined[col] = pd.to_numeric(combined[col], errors='coerce').fillna(0)
    return combined if not combined.empty else None


def fetch_histock_branch_data(stock_code, timeout=15):
    """
    向HiStock要指定股票的當日券商分點買賣資料，回傳parse_histock_branch_html
    處理過的DataFrame，或None（連線失敗/格式不符）。

    刻意帶一個像真實瀏覽器的User-Agent（不是偽裝身分繞過防護——這個頁面
    本來就沒有反自動化機制，帶正常UA純粹是禮貌，避免被當成明顯異常流量）。
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "zh-TW,zh;q=0.9",
        }
        r = _SESSION.get(f"https://histock.tw/stock/branch.aspx?no={stock_code}",
                         headers=headers, timeout=timeout)
        if r.status_code != 200:
            print(f"[券商分點] HiStock回應異常：{stock_code} HTTP {r.status_code}")
            return None
        return parse_histock_branch_html(r.text)
    except ImportError as e:
        # 【R94新增】明確標示這是「部署環境缺套件」，不是連線/網站問題——
        # 缺lxml時會拋這個例外，過去長期跟其他失敗混淆誤判方向。
        print(f"[券商分點] ❌缺少解析套件(lxml或html5lib)：{stock_code} {e}"
              f"——請確認requirements.txt有列出lxml，這不是網站或連線問題。")
        return None
    except Exception as e:
        print(f"[券商分點] HiStock連線失敗：{stock_code} {e}")
        return None


def fetch_finmind_branch_data(stock_code, target_date):
    """
    【R96新增，R96再修復】FinMind版本的券商分點資料——用官方的「台股分點
    資料表」(TaiwanStockTradingDailyReport)，涵蓋上市/上櫃/興櫃全市場，
    跟HiStock網頁爬蟲提供的是同一種資訊(單一券商當日買賣張數)，但走正式
    API，不用擔心網站改版、反爬蟲、GitHub Actions這組IP被擋這些爬蟲固有
    的風險。

    【R96實測結果，已確認，不再是「未確認事項」】查證FinMind官方完整
    資料集文件(https://finmind.github.io/llms-full.txt)確認兩件事：
    ①這個資料集是Sponsor付費方案專屬，免費/註冊會員都不能用——如果目前
    帳號等級不是Sponsor，這裡會失敗，呼叫端(fetch_branch_data_with_
    fallback)會自動退回HiStock爬蟲，不會讓既有功能因為這次改動而變差。
    ②【重大bug修復】原本這裡的API呼叫方式整個是錯的——這個資料集不走
    一般的/api/v4/data端點，是獨立的專屬端點，而且參數是單一date（一次
    只能查一天），不是start_date/end_date區間查詢。原本的寫法就算帳號
    是Sponsor也會失敗，不是「權限不足」那種失敗，是「端點跟參數用錯」
    的失敗，這裡改成官方文件確認過的正確用法。

    回傳格式跟fetch_histock_branch_data完全一致，方便呼叫端無縫替換：
    DataFrame[broker_name, buy_shares, sell_shares, net_shares]（單位：張），
    或None。FinMind原始欄位單位是「股」，這裡除以1000統一成「張」，
    跟系統其他地方的單位慣例一致，不會讓呼叫端要另外處理單位轉換。
    """
    url = 'https://api.finmindtrade.com/api/v4/taiwan_stock_trading_daily_report'
    params = {'data_id': stock_code, 'date': target_date}
    try:
        payload = _finmind_get(url, params, max_retries=2, timeout=15)
        rows = payload.get('data', [])
        if not rows:
            return None
        df = pd.DataFrame(rows)
        if 'securities_trader' not in df.columns or 'buy' not in df.columns:
            return None
        # 同一券商同一天可能有多筆（不同成交價位各一筆），先依券商加總
        agg = df.groupby('securities_trader').agg(
            buy_shares=('buy', 'sum'), sell_shares=('sell', 'sum')).reset_index()
        agg['buy_shares'] = agg['buy_shares'] / 1000.0
        agg['sell_shares'] = agg['sell_shares'] / 1000.0
        agg['net_shares'] = agg['buy_shares'] - agg['sell_shares']
        agg = agg.rename(columns={'securities_trader': 'broker_name'})
        agg = agg[agg['broker_name'].astype(str).str.strip() != '']
        return agg[['broker_name', 'buy_shares', 'sell_shares', 'net_shares']] if not agg.empty else None
    except Exception as e:
        print(f"[券商分點] FinMind抓取失敗：{stock_code} {e}")
        return None


def fetch_branch_data_with_fallback(stock_code, target_date, timeout=15):
    """
    【R96新增】券商分點資料——FinMind優先，失敗才退回HiStock爬蟲。這是
    總指揮官這輪確認的方向：FinMind走正式API，比爬蟲穩定；只有FinMind
    這個資料集在目前帳號等級用不了（可能是付費限定，實測前無法確認）時，
    才退回原本已經在用、已知會偶爾連不上的HiStock爬蟲當備援——不會讓
    既有功能因為這次改動而變得更差，只會更好或至少一樣。

    呼叫端原本直接呼叫fetch_histock_branch_data(code)的地方，改呼叫這個
    函式即可，回傳格式完全一致，不用改動任何下游處理邏輯。
    """
    df = fetch_finmind_branch_data(stock_code, target_date)
    if df is not None and not df.empty:
        return df
    return fetch_histock_branch_data(stock_code, timeout=timeout)


# ==============================================================================
# 七、處置股/注意股預警——R79新增，已驗證的官方端點（TWSE注意股/處置股、
# TPEx處置股），TPEx注意股端點測試失敗，缺口誠實標註不做。
# ==============================================================================
def fetch_twse_attention_stocks(timeout=15):
    """
    【R79新增】TWSE(上市)注意股清單——已驗證端點，回傳原始list of dict，
    欄位為英文(Code/Name/Date/TradingInfoForAttention等)。連線失敗回傳None。
    """
    try:
        r = _SESSION.get("https://openapi.twse.com.tw/v1/announcement/notice", timeout=timeout)
        if r.status_code != 200:
            return None
        data = r.json()
        return data if isinstance(data, list) else None
    except Exception as e:
        print(f"[注意股] TWSE連線失敗：{e}")
        return None


def fetch_twse_disposal_stocks(timeout=15):
    """
    【R79新增】TWSE(上市)處置股清單——已驗證端點。連線失敗回傳None。
    """
    try:
        r = _SESSION.get("https://openapi.twse.com.tw/v1/announcement/punish", timeout=timeout)
        if r.status_code != 200:
            return None
        data = r.json()
        return data if isinstance(data, list) else None
    except Exception as e:
        print(f"[處置股] TWSE連線失敗：{e}")
        return None


def fetch_tpex_disposal_stocks(timeout=15):
    """
    【R79新增】TPEx(上櫃)處置股清單——已驗證端點。連線失敗回傳None。
    """
    try:
        r = _SESSION.get("https://www.tpex.org.tw/openapi/v1/tpex_disposal_information", timeout=timeout)
        if r.status_code != 200:
            return None
        data = r.json()
        return data if isinstance(data, list) else None
    except Exception as e:
        print(f"[處置股] TPEx連線失敗：{e}")
        return None


def check_disposal_attention_status(symbol, attention_list=None, disposal_twse_list=None,
                                     disposal_tpex_list=None):
    """
    【R79新增】給一檔股票代號，比對三份官方清單，回傳這檔目前的注意/處置
    狀態。三份清單可以先抓好傳進來（避免每檔股票都重新打一次API，掃描
    多檔時只要抓一次清單，逐檔比對即可）。

    回傳dict：{'attention': bool, 'disposal': bool, 'detail': str}
    detail欄位放實際的公告內容摘要（原因/期間），查無資料時是空字串。
    三份清單都是None時（表示連線失敗），回傳{'attention': None, ...}用
    None表示「無法確認」，不是False（False代表「確認過、沒有」，兩者
    意義不同，不能混為一談）。
    """
    if attention_list is None and disposal_twse_list is None and disposal_tpex_list is None:
        return {'attention': None, 'disposal': None, 'detail': '（三份官方清單都抓不到，無法確認狀態）'}

    _sym = str(symbol)
    result = {'attention': False, 'disposal': False, 'detail': ''}

    if attention_list:
        for item in attention_list:
            if str(item.get('Code', '')).strip() == _sym:
                result['attention'] = True
                result['detail'] += f"⚠️注意股：{item.get('TradingInfoForAttention', '')}　"
                break

    if disposal_twse_list:
        for item in disposal_twse_list:
            if str(item.get('Code', '')).strip() == _sym:
                result['disposal'] = True
                result['detail'] += (f"🚨處置股(上市)：{item.get('ReasonsOfDisposition', '')}，"
                                     f"期間{item.get('DispositionPeriod', '')}　")
                break

    if disposal_tpex_list:
        for item in disposal_tpex_list:
            if str(item.get('SecuritiesCompanyCode', '')).strip() == _sym:
                result['disposal'] = True
                result['detail'] += (f"🚨處置股(上櫃)：{item.get('DispositionReasons', '')}，"
                                     f"期間{item.get('DispositionPeriod', '')}　")
                break

    return result


# ==============================================================================
# 八、自結財報/重大訊息掃描——R79新增，用官方端點openapi.twse.com.tw/v1/
# opendata/t187ap04_L，篩「主旨」欄位關鍵字比對自結財報相關公告。
# ==============================================================================
def fetch_twse_material_announcements(timeout=15):
    """
    【R79新增】TWSE官方重大訊息公告——已驗證端點，回傳原始list of dict，
    欄位為繁體中文(公司代號/公司名稱/主旨/事實發生日/說明等)。
    連線失敗回傳None。這個端點是滾動快照(最近幾天內的公告)，不是全歷史，
    要天天排程掃描才不會漏掉。
    """
    try:
        r = _SESSION.get("https://openapi.twse.com.tw/v1/opendata/t187ap04_L", timeout=timeout)
        if r.status_code != 200:
            return None
        data = r.json()
        return data if isinstance(data, list) else None
    except Exception as e:
        print(f"[重大訊息] TWSE連線失敗：{e}")
        return None


def filter_self_compiled_announcements(announcements, tracked_symbols=None):
    """
    【R79新增】從重大訊息清單裡篩出「自結財報」相關的公告——用「主旨」欄位
    關鍵字比對，不是所有重大訊息都跟自結財報有關（改名、業績說明會等這類
    公告要濾掉，不然會員推播訊息大部分都是噪音）。

    tracked_symbols：只想關注自己持倉/雷達清單的話，傳這個進來做篩選；
    不傳就回傳全部符合關鍵字的公告（不限股票代號）。

    關鍵字：自結、自結損益、自結財報、自結數——這些都是台股公告裡常見
    描述自結財務數字的用詞，用「或」的方式比對「主旨」欄位。

    回傳篩選後的list，可能是空list（代表沒有符合條件的公告，不代表出錯）。
    """
    if not announcements:
        return []
    keywords = ('自結損益', '自結財報', '自結數', '自結')
    out = []
    for item in announcements:
        subject = str(item.get('主旨', ''))
        if not any(kw in subject for kw in keywords):
            continue
        code = str(item.get('公司代號', '')).strip()
        if tracked_symbols is not None and code not in tracked_symbols:
            continue
        out.append(item)
    return out


# 【R97新增，見開發歷程.md「事件驅動評分系統」章節】總指揮官提供的十大
# 會影響股價的事件，用關鍵字分類TWSE重大訊息公告。只能用關鍵字比對
# （免費資料源沒有NLP語意分類，只有「主旨」文字欄位），精準度有限，
# 會有漏抓/誤抓，這是資料源本身的限制，不是分類邏輯的問題。
#
# 標記+否決並用（總指揮官這輪確認）：
# - VETO（一票否決，直接排除候選池）：不確定性通常大到蓋過任何技術面
#   訊號，比照現有disposal_watch(處置股/注意股)的精神。
# - TAG（只標記，不排除）：屬於「知道就好」的資訊性事件，本身可能正面
#   也可能負面（例如財報公佈本身不代表好壞），不該自動排除。
MATERIAL_EVENT_CATEGORIES = {
    # 序號跟總指揮官提供的清單一一對應
    "1_股東會": {"keywords": ("股東常會", "股東臨時會", "股東會"), "action": "tag"},
    "2_法說會": {"keywords": ("法人說明會", "法說會", "投資人說明會"), "action": "tag"},
    "3_股利政策": {"keywords": ("股利", "盈餘分配", "配息", "配股"), "action": "tag"},
    "4_增資減資": {"keywords": ("現金增資", "減資", "增資"), "action": "veto"},
    "5_募資計劃": {"keywords": ("私募", "發行特別股", "海外存託憑證", "ADR", "發行公司債",
                              "募資"), "action": "veto"},
    "6_除權息時間": {"keywords": ("除權", "除息", "停止過戶"), "action": "tag"},
    "7_每月業績公佈": {"keywords": ("自結營收", "月營收", "營業收入"), "action": "tag"},
    "8_每季財報公佈": {"keywords": ("自結損益", "自結財報", "季報", "財務報告", "合併財報"),
                     "action": "tag"},
    "9_經營權之爭或併購": {"keywords": ("經營權", "併購", "合併", "收購", "股權轉讓",
                                   "委託書"), "action": "veto"},
    "10_內部人買賣自家股": {"keywords": ("董事", "監察人", "經理人", "內部人", "轉讓持股",
                                    "取得或處分本公司股份"), "action": "veto"},
}


def classify_material_announcements(announcements, tracked_symbols=None, today_only=True,
                                    reference_date=None):
    """
    【R97新增】對TWSE重大訊息公告做十類事件分類，回傳依股票代號分組的
    結果：{code: {"veto": [事件說明,...], "tag": [事件說明,...]}}。

    today_only=True時只保留「事實發生日」是今天(或reference_date)的公告，
    避免把好幾天前的舊公告一直重複標記——這個資料源本身是滾動快照
    （最近幾天內），不是每天全新的，不篩日期會一直誤判成「今天發生」。

    一則公告可能同時符合多個分類（例如同時提到「股東會」跟「股利政策」），
    這裡全部列出，不強迫只歸一類。

    回傳誠實反映資料本身的限制：關鍵字比對，不是語意理解，有漏抓/誤抓
    是預期中的行為，不是bug。
    """
    if not announcements:
        return {}
    ref_date = reference_date or datetime.now(TAIPEI_TZ).strftime('%Y-%m-%d')
    result = {}
    for item in announcements:
        code = str(item.get('公司代號', '')).strip()
        if not code:
            continue
        if tracked_symbols is not None and code not in tracked_symbols:
            continue
        if today_only:
            _event_date = str(item.get('事實發生日', '')).strip()
            if _event_date and _event_date != ref_date:
                continue
        subject = str(item.get('主旨', ''))
        matched = []
        for cat_name, cat_info in MATERIAL_EVENT_CATEGORIES.items():
            if any(kw in subject for kw in cat_info["keywords"]):
                matched.append((cat_name, cat_info["action"], subject))
        if not matched:
            continue
        bucket = result.setdefault(code, {"veto": [], "tag": []})
        for cat_name, action, subject in matched:
            entry = f"{cat_name}：{subject}"
            bucket[action].append(entry)
    return result


# ==============================================================================
# 九、命中率自動化驗證——門檻敏感度掃描（R87新增，範圍限定爆量比/六日累計
# 漲跌門檻，完整12濾網回測引擎是之後的延伸項目）
# ------------------------------------------------------------------------------
def scan_volume_ratio_sensitivity(symbols, candidates=(0.8, 1.0, 1.2, 1.5, 1.8, 2.0, 2.5, 3.0),
                                   years=2, forward_days=3):
    """
    【R87新增】爆量比門檻敏感度掃描——對每個候選門檻值，統計「爆量比超過
    這個門檻」的那些交易日，未來N天的平均報酬跟正報酬機率，這樣才能回答
    「0.6/1.5/2.0這些數字，哪個門檻其實比較有鑑別力」。

    做法：對每檔股票抓歷史日K，算vol_ratio(=當日量/5日均量)，對每個候選
    門檻值，收集「vol_ratio超過門檻」那些日子的未來N日報酬，彙總算命中率。
    不需要完整的12濾網回測引擎——這個門檻本身只是單一數值比較，用這個
    輕量做法就能得到有意義的敏感度數據。

    回傳 dict {threshold: {'sample': n, 'win_rate': %, 'avg_ret': %}}。
    """
    _buckets = {c: [] for c in candidates}
    for code in symbols:
        try:
            tk = yf.Ticker(f"{code}.TW")
            df = tk.history(period=f"{years}y", auto_adjust=False, timeout=10)
            if df.empty:
                tk = yf.Ticker(f"{code}.TWO")
                df = tk.history(period=f"{years}y", auto_adjust=False, timeout=10)
            df = df.dropna(subset=['Close'])
            if df.empty or len(df) < 30:
                continue
            df['Vol5MA'] = df['Volume'].rolling(5).mean()
            df['VolRatio'] = df['Volume'] / df['Vol5MA']
            closes = df['Close'].values
            for i in range(10, len(df) - forward_days):
                vr = df['VolRatio'].iloc[i]
                if pd.isna(vr) or vr <= 0:
                    continue
                fwd_ret = (closes[i + forward_days] - closes[i]) / closes[i] * 100
                for c in candidates:
                    if vr >= c:
                        _buckets[c].append(fwd_ret)
        except Exception:
            continue

    out = {}
    for c, rets in _buckets.items():
        if not rets:
            out[c] = {'sample': 0, 'win_rate': None, 'avg_ret': None}
        else:
            out[c] = {
                'sample': len(rets),
                'win_rate': round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
                'avg_ret': round(sum(rets) / len(rets), 2),
            }
    return out


def scan_six_day_gain_sensitivity(symbols, candidates=(10, 15, 20, 25, 30, 35),
                                   years=2, forward_days=5):
    """
    【R87新增】六日累計漲跌門檻敏感度掃描——calc_disposal_risk_proxy()用的
    「近6個營業日累計漲跌」門檻，跟爆量比同樣邏輯：測不同候選門檻下，
    觸發後未來N日的表現分布，用來判斷目前寫死的門檻是否合理。

    回傳格式同scan_volume_ratio_sensitivity。
    """
    _buckets = {c: [] for c in candidates}
    for code in symbols:
        try:
            tk = yf.Ticker(f"{code}.TW")
            df = tk.history(period=f"{years}y", auto_adjust=False, timeout=10)
            if df.empty:
                tk = yf.Ticker(f"{code}.TWO")
                df = tk.history(period=f"{years}y", auto_adjust=False, timeout=10)
            df = df.dropna(subset=['Close'])
            if df.empty or len(df) < 30:
                continue
            closes = df['Close'].values
            for i in range(6, len(df) - forward_days):
                six_day_gain = (closes[i] - closes[i - 6]) / closes[i - 6] * 100
                fwd_ret = (closes[i + forward_days] - closes[i]) / closes[i] * 100
                for c in candidates:
                    if six_day_gain >= c:
                        _buckets[c].append(fwd_ret)
        except Exception:
            continue

    out = {}
    for c, rets in _buckets.items():
        if not rets:
            out[c] = {'sample': 0, 'win_rate': None, 'avg_ret': None}
        else:
            out[c] = {
                'sample': len(rets),
                'win_rate': round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
                'avg_ret': round(sum(rets) / len(rets), 2),
            }
    return out


# ==============================================================================
# 十、回測引擎共用資料層——R89新增，見開發歷程.md背景說明
# ------------------------------------------------------------------------------
# ==============================================================================
# R97續5新增：TWSE官方批次端點（取代FinMind逐檔迴圈的主力資料來源）
# ==============================================================================
_TWSE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept": "application/json",
}


def _is_plain_stock_code(code):
    """
    【R97續5，真實資料驗證過的過濾規則】T86回傳全市場所有有價證券
    (含ETF/權證/受益憑證等)，不是只有普通股票。用「4碼純數字、
    不以00開頭」篩出真正的股票——00開頭幾乎都是ETF，一般股票代碼
    不會這樣命名。實測套用後15,211筆過濾成1,078筆，跟現有1074檔
    掃描池量級吻合。
    """
    if not code or not isinstance(code, str):
        return False
    code = code.strip()
    return len(code) == 4 and code.isdigit() and not code.startswith("00")


def fetch_twse_t86_snapshot(trade_date_yyyymmdd):
    """
    【R97續5新增，R97續13強化欄位比對彈性】三大法人買賣超日報，全市場
    一次回傳，取代逐檔打FinMind。
    來源唯一：www.twse.com.tw/rwd/zh/fund/T86（openapi.twse.com.tw目錄
    裡查證過沒有這份資料，不用再找替代路徑）。
    回傳：{symbol: {'f_buy':.., 't_buy':.., 'd_buy':..}}（單位：張，
    跟fetch_institutional_history舊版FinMind路徑的單位換算一致，
    原始股數/1000）。查無資料（非交易日/尚未公告）回傳空dict，不是例外，
    呼叫端要能處理空字典。

    【R97續13新增，總指揮官實測抓到】原本欄位名稱寫死單一字串比對，
    2026-08-20實測發現TWSE某些交易日回傳的T86欄位名稱會有差異（推測
    是外資分類方式在不同日期有細微調整），造成exact match失敗、整段
    法人資料當天完全抓不到、全部退回FinMind逐檔查詢（拖慢整個選股
    流程的主因）。這裡改成每個欄位準備多個候選名稱，依序嘗試，且失敗
    時把當天實際欄位清單完整印出來，方便下次再發生時直接比對，不用
    再靠人工截圖來回確認。
    """
    url = "https://www.twse.com.tw/rwd/zh/fund/T86"
    params = {"date": trade_date_yyyymmdd, "selectType": "ALL", "response": "json"}
    out = {}
    try:
        r = requests.get(url, params=params, headers=_TWSE_HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
        if data.get("stat") != "OK":
            return out
        fields = data.get("fields", [])
        rows = data.get("data", [])

        def _find_field(candidates):
            for c in candidates:
                if c in fields:
                    return fields.index(c)
            return None

        idx_code = _find_field(["證券代號"])
        idx_foreign_net = _find_field(["外資買賣超股數", "外資及陸資買賣超股數",
                                       "外資買賣超股數(不含外資自營商)"])
        idx_trust_net = _find_field(["投信買賣超股數"])
        idx_dealer_self_net = _find_field(["自營商買賣超股數(自行買賣)", "自營商買進股數(自行買賣)"])
        idx_dealer_hedge_net = _find_field(["自營商買賣超股數(避險)", "自營商買進股數(避險)"])

        if None in (idx_code, idx_foreign_net, idx_trust_net, idx_dealer_self_net, idx_dealer_hedge_net):
            print(f"[fetch_twse_t86_snapshot] 欄位對應失敗，TWSE今天({trade_date_yyyymmdd})"
                  f"回傳的實際欄位清單：{fields}")
            return out
        for row in rows:
            code = str(row[idx_code]).strip()
            if not _is_plain_stock_code(code):
                continue

            def _num(s):
                try:
                    return float(str(s).replace(",", "") or 0)
                except (ValueError, TypeError):
                    return 0.0
            f_buy = _num(row[idx_foreign_net]) / 1000.0
            t_buy = _num(row[idx_trust_net]) / 1000.0
            d_buy = (_num(row[idx_dealer_self_net]) + _num(row[idx_dealer_hedge_net])) / 1000.0
            out[code] = {"f_buy": f_buy, "t_buy": t_buy, "d_buy": d_buy}
    except Exception as e:
        print(f"[fetch_twse_t86_snapshot] 抓取失敗：{type(e).__name__}: {e}")
    return out


def fetch_twse_margin_snapshot():
    """
    【R97續5新增】融資餘額全市場一次回傳。用openapi版（1294筆逐檔），
    不是rwd版（rwd版是全市場加總統計表，只有3筆，不是我們要的逐檔資料，
    這是真實測試中發現的重要區別，混用會整批資料錯誤）。
    回傳：{symbol: margin_diff}（今日餘額-前日餘額，單位：張）。
    這支端點只有「最新一個交易日」，沒有date參數可以回溯——回溯需求
    由這張表本身逐日累積達成，不是這支API的責任。
    """
    url = "https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN"
    out = {}
    try:
        r = requests.get(url, headers=_TWSE_HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            print("[fetch_twse_margin_snapshot] 回傳格式非預期(不是list)，略過")
            return out
        for row in data:
            code = str(row.get("股票代號", "")).strip()
            if not _is_plain_stock_code(code):
                continue
            try:
                today_bal = float(str(row.get("融資今日餘額", "0")).replace(",", "") or 0)
                prev_bal = float(str(row.get("融資前日餘額", "0")).replace(",", "") or 0)
            except (ValueError, TypeError):
                continue
            out[code] = today_bal - prev_bal
    except Exception as e:
        print(f"[fetch_twse_margin_snapshot] 抓取失敗：{type(e).__name__}: {e}")
    return out


def fetch_twse_pe_snapshot():
    """
    【R97續5新增】本益比/殖利率/淨值比全市場一次回傳。用openapi版
    BWIBBU_ALL（乾淨dict格式，1083筆）。只有最新交易日，沒有date參數。
    回傳：{symbol: {'pe':.., 'dividend_yield':.., 'pb_ratio':..}}
    """
    url = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
    out = {}
    try:
        r = requests.get(url, headers=_TWSE_HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            print("[fetch_twse_pe_snapshot] 回傳格式非預期(不是list)，略過")
            return out
        for row in data:
            code = str(row.get("Code", "")).strip()
            if not _is_plain_stock_code(code):
                continue

            def _f(v):
                try:
                    return float(v) if v not in (None, "", "-") else None
                except (ValueError, TypeError):
                    return None
            out[code] = {"pe": _f(row.get("PEratio")),
                         "dividend_yield": _f(row.get("DividendYield")),
                         "pb_ratio": _f(row.get("PBratio"))}
    except Exception as e:
        print(f"[fetch_twse_pe_snapshot] 抓取失敗：{type(e).__name__}: {e}")
    return out


def sync_twse_market_snapshot(sb, trade_date=None):
    """
    【R97續5新增，R97續6擴充月營收，R97續9擴充每日價量】排程端每個交易日
    呼叫一次（stage_signal迴圈開始前），把T86+MI_MARGN+BWIBBU_ALL+
    t187ap05_L(月營收)+MI_INDEX(每日價量)五支批次端點的資料合併、批次
    upsert進twse_market_snapshot表。全市場總共5次API呼叫，取代原本
    1074檔×5次=5000+次FinMind呼叫。

    trade_date：'YYYY-MM-DD'格式，預設今天（台北時區）。T86/MI_INDEX
    需要'YYYYMMDD'格式的date參數，這裡自動轉換。

    回傳：實際寫入的股票數量（int）。任何一支端點失敗，該部分資料留空，
    不會讓整個同步失敗——法人抓不到，融資/PE/營收/價量還是可以正常
    寫入，呼叫端的fetch_institutional_history/fetch_pe_history/
    fetch_revenue_history_lagged/fetch_stock_price_and_value_history
    各自對None值有處理。
    """
    if trade_date is None:
        trade_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    date_yyyymmdd = trade_date.replace("-", "")

    t86 = fetch_twse_t86_snapshot(date_yyyymmdd)
    margin = fetch_twse_margin_snapshot()
    pe = fetch_twse_pe_snapshot()
    revenue = fetch_twse_monthly_revenue_snapshot()
    price_value = fetch_twse_daily_price_value_snapshot(date_yyyymmdd)

    all_symbols = (set(t86.keys()) | set(margin.keys()) | set(pe.keys())
                   | set(revenue.keys()) | set(price_value.keys()))
    if not all_symbols:
        print(f"[sync_twse_market_snapshot] {trade_date} 五支端點都沒抓到資料"
              f"（可能非交易日），本次不寫入。")
        return 0

    rows = []
    for sym in all_symbols:
        t86_row = t86.get(sym, {})
        pe_row = pe.get(sym, {})
        rev_row = revenue.get(sym, {})
        pv_row = price_value.get(sym, {})
        rows.append({
            "trade_date": trade_date,
            "symbol": sym,
            "f_buy": t86_row.get("f_buy"),
            "t_buy": t86_row.get("t_buy"),
            "d_buy": t86_row.get("d_buy"),
            "margin_diff": margin.get(sym),
            "pe": pe_row.get("pe"),
            "dividend_yield": pe_row.get("dividend_yield"),
            "pb_ratio": pe_row.get("pb_ratio"),
            "rev_yoy": rev_row.get("rev_yoy"),
            "rev_mom": rev_row.get("rev_mom"),
            "revenue_ym": rev_row.get("revenue_ym"),
            "close_price": pv_row.get("close"),
            "trading_value": pv_row.get("trading_value"),
            "trading_volume": pv_row.get("trading_volume"),
            "source": "twse_official",
        })

    # 分批寫入，避免單次upsert payload過大
    CHUNK = 500
    written = 0
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        try:
            sb.table("twse_market_snapshot").upsert(
                chunk, on_conflict="trade_date,symbol").execute()
            written += len(chunk)
        except Exception as e:
            print(f"[sync_twse_market_snapshot] 第{i}-{i+len(chunk)}筆寫入失敗："
                  f"{type(e).__name__}: {e}")
    print(f"[sync_twse_market_snapshot] {trade_date} 全市場快照同步完成，"
          f"共{written}/{len(rows)}檔（法人{len(t86)}檔／融資{len(margin)}檔／"
          f"本益比{len(pe)}檔／營收{len(revenue)}檔／價量{len(price_value)}檔）")
    return written


def _load_institutional_from_snapshot(sb, stock_code, years):
    """
    【R97續5新增】fetch_institutional_history()的第一層資料來源——先查
    twse_market_snapshot這張表，查得到就不用打FinMind。回傳格式跟舊版
    FinMind路徑完全一致（index=date的DataFrame，欄位f_buy/t_buy/d_buy/
    margin_diff），呼叫端(_derive_institutional_features等)不用改。

    表剛建立、資料還沒累積夠天數時，這裡查到的筆數會不足，呼叫端
    (fetch_institutional_history)偵測到筆數太少會自動退回FinMind補齊，
    不會讓功能整段掛掉，是漸進式取代，不是一次到位的硬切換。
    """
    if sb is None:
        return None
    start_date = (datetime.now(TAIPEI_TZ) - timedelta(days=int(365 * years))).strftime("%Y-%m-%d")
    try:
        res = (sb.table("twse_market_snapshot")
              .select("trade_date,f_buy,t_buy,d_buy,margin_diff")
              .eq("symbol", stock_code)
              .gte("trade_date", start_date)
              .order("trade_date", desc=True)
              .limit(30)
              .execute())
        rows = res.data or []
        if not rows:
            return None
        df = pd.DataFrame(rows).set_index("trade_date")
        return df
    except Exception as e:
        print(f"[_load_institutional_from_snapshot] {stock_code} 查表失敗，"
              f"退回FinMind：{type(e).__name__}: {e}")
        return None


def fetch_pe_history(symbol, token, years=3, sb=None):
    """
    【V157新增，R89搬進共用模組】抓取每日本益比／股價淨值比／殖利率
    歷史序列。取代「PE×15合理、PE×20樂觀」的固定倍數——固定倍數對電子股
    （常態PE 25~35）跟傳產股（常態PE 10~15）套同一把尺，會系統性誤判。
    改用「現在的PE落在這檔股票自己歷史分布的第幾百分位」。
    抓不到或樣本不足時，呼叫端會自動退回舊版固定倍數，不會整段功能掛掉。

    【R97續5新增，R97續7修正門檻】sb不為None時，優先查twse_market_snapshot
    表（官方BWIBBU_ALL批次端點每日同步的資料）。門檻從原本≥5改成≥1——
    總指揮官實測抓到：table剛累積1天資料時，≥5這個門檻永遠不滿足，
    每次還是整段退回FinMind，完全沒有加速到，這是原本設計的失誤（percentile
    百分位計算本來就需要≥60筆才會真的算，資料不夠percentile自然是None、
    退回PE_LANDMINE固定倍數備援，這個「優雅降級」下游本來就有，不需要
    在這裡疊加一層「筆數不夠乾脆全部不用」的門檻，那樣反而讓table永遠
    等不到第一次真正被使用的機會）。
    """
    if sb is not None:
        try:
            start_date = (datetime.now(TAIPEI_TZ) - timedelta(days=int(365 * years))).strftime("%Y-%m-%d")
            res = (sb.table("twse_market_snapshot")
                  .select("trade_date,pe,pb_ratio,dividend_yield")
                  .eq("symbol", symbol)
                  .gte("trade_date", start_date)
                  .order("trade_date", desc=True)
                  .limit(1000)
                  .execute())
            rows = res.data or []
            if len(rows) >= 1:
                df = pd.DataFrame(rows)
                df = df.rename(columns={"pe": "PER", "pb_ratio": "PBR"})
                for col in ("PER", "PBR", "dividend_yield"):
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                _SNAPSHOT_CACHE_STATS["pe_hit"] += 1
                return df
        except Exception as e:
            print(f"[fetch_pe_history] {symbol} 查twse_market_snapshot失敗，"
                  f"退回FinMind：{type(e).__name__}: {e}")
    _SNAPSHOT_CACHE_STATS["pe_miss"] += 1

    url = 'https://api.finmindtrade.com/api/v4/data'
    start_date = (datetime.now(TAIPEI_TZ) - timedelta(days=int(365 * years))).strftime('%Y-%m-%d')
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
    except FinMindAPIError as e:
        print(f"[fetch_pe_history-診斷] FinMind抓PE歷史失敗：{type(e).__name__}: {e}")
        return None


def fetch_institutional_history(stock_code, years, token, sb=None):
    """
    【V159新增，R89搬進共用模組】歷史三大法人買賣超+融資融券，各一支API
    call涵蓋整個回測區間（不是一天一call）。三大法人與融資融券資料是證交所
    收盤後當天公告，用在「當天收盤產生訊號」沒有未來函數問題。
    回傳以日期為index的DataFrame，欄位：f_buy, t_buy, d_buy, margin_diff
    （單位：張）。

    【R97續5新增，R97續7修正門檻】sb不為None時，優先查twse_market_snapshot表
    （官方T86+MI_MARGN批次端點每日同步累積的資料）。表剛建立時每天只累積
    1筆，f_5d/f_10d/foreign_buy_streak3這類需要5-10天歷史的特徵會因為
    筆數不足自動留None（_derive_institutional_features本來就對None容忍，
    不會報錯），但f_single/t_single/margin_diff這些「當日」特徵從第一天
    就能用。累積約10個交易日後，這張表就能完全取代FinMind，不用再
    改任何程式碼——純粹隨時間自然過渡。門檻從原本≥3改成≥1——總指揮官
    實測抓到：表只累積1天資料時，≥3這個門檻永遠不滿足，每次還是整段
    退回FinMind，完全沒有加速到，這是原本設計的失誤，混淆了「當日特徵」
    跟「多日特徵」需要的資料深度不一樣這件事，改成「有多少用多少」，
    不是「不夠就整段放棄」。查表失敗才退回原本FinMind路徑。

    【R97獨立排查，見開發歷程.md】總指揮官回報filter_backtest手動測試log
    出現「FinMindAPIError: empty_data: API 回傳成功但 data 為空」，追查
    _finmind_get()的分類邏輯（見該函式docstring）：empty_data代表「該次
    請求HTTP 200+msg=success，但data陣列是空的」，_finmind_get刻意設計
    成empty_data不跨帳號重試（理由寫在_finmind_get docstring：「資料本身
    不存在，換帳號無意義」）。

    這次追查因為log沒有印出是哪一檔股票觸發的，已經先補上stock_code
    （見下面except區塊），下次再發生時可以直接定位。目前無法排除的
    另一種可能：某些FinMind帳號方案對TaiwanStockInstitutionalInvestors
    BuySell這個資料集的歷史回溯深度可能有差異（訪客/低階帳號回溯較短），
    如果剛好第一個嘗試的帳號回溯深度不夠、query的start_date在其可用範圍
    之外，也會呈現「200+空data」而不是明確的權限錯誤，這種情況目前的
    「empty_data不換帳號」設計會誤判成「真的沒資料」而提早放棄。這個
    假設還沒有辦法在這個環境驗證（FinMind API不在這個沙盒的網路白名單
    內），建議下次log出現時，把印出來的stock_code拿去FinMind官網或
    另一組帳號手動查一次，確認是「真的沒資料」還是「這組帳號查不到」。
    """
    if sb is not None:
        snap_df = _load_institutional_from_snapshot(sb, stock_code, years)
        if snap_df is not None and len(snap_df) >= 1:
            _SNAPSHOT_CACHE_STATS["institutional_hit"] += 1
            return snap_df
    _SNAPSHOT_CACHE_STATS["institutional_miss"] += 1

    url = 'https://api.finmindtrade.com/api/v4/data'
    start_date = (datetime.now(TAIPEI_TZ) - timedelta(days=int(365 * years))).strftime('%Y-%m-%d')
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
    except FinMindAPIError as e:
        print(f"[fetch_institutional_history-診斷] {stock_code} 抓法人買賣超失敗(部分或全部)："
              f"{type(e).__name__}: {e}")
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
    except FinMindAPIError as e:
        print(f"[fetch_institutional_history-診斷] {stock_code} 抓融資增減失敗："
              f"{type(e).__name__}: {e}")
        pass

    if out.empty:
        return None
    # 【R95修復】原本fillna(0.0)把margin_diff的NaN(沒抓到資料)也填成0，
    # 導致無法分辨「真的沒變化」跟「根本沒資料」。margin_diff保留NaN
    # 供呼叫端用pd.notna()判斷，f_buy/t_buy/d_buy維持補0(下游加總安全值)。
    for _c in ('f_buy', 't_buy', 'd_buy'):
        if _c in out.columns:
            out[_c] = out[_c].fillna(0.0)
    return out


def fetch_twse_daily_price_value_snapshot(trade_date_yyyymmdd):
    """
    【R97續9新增，真實資料驗證過】全市場每日收盤價+成交金額，一次回傳，
    取代逐檔打FinMind TaiwanStockPrice（fetch_stock_price_and_value_
    history舊路徑）。

    來源：www.twse.com.tw/exchangeReport/MI_INDEX（不是openapi.twse.
    com.tw，那邊沒有這份逐檔明細；也不是/rwd/zh/afterTrading/這個路徑，
    那個路徑不存在，之前驗證時踩過這個坑）。

    回傳結構是tables陣列（不是T86那種扁平data/fields），個股逐檔明細
    在其中一個子表裡（實測是32,669筆，同一天有多個子表，指數統計/
    報酬指數等在前面，個股明細筆數最多，這裡用筆數最多來判斷，不寫死
    索引避免TWSE調整子表順序又對不上）。32,669筆裡混了大量ETF/權證，
    用_is_plain_stock_code()過濾（4碼純數字、不00開頭），跟T86同一套
    規則直接沿用。

    【R97續10新增】多抓「成交股數」欄位——CMoney主力偵測法的「成交量>
    5日均量3倍」這個維度需要每日成交量(股數)才能算，之前只留成交金額
    (用在週轉率的分子)，這裡補齊。

    回傳：{symbol: {'close': .., 'trading_value': .., 'trading_volume': ..}}
    """
    url = "https://www.twse.com.tw/exchangeReport/MI_INDEX"
    params = {"date": trade_date_yyyymmdd, "type": "ALL", "response": "json"}
    out = {}
    try:
        r = requests.get(url, params=params, headers=_TWSE_HEADERS, timeout=45)
        r.raise_for_status()
        data = r.json()
        if data.get("stat") != "OK":
            return out
        tables = data.get("tables", [])
        if not tables:
            return out
        # 抓筆數最多的子表當個股逐檔明細
        best = max(tables, key=lambda t: len(t.get("data", [])))
        fields = best.get("fields", [])
        rows = best.get("data", [])
        try:
            idx_code = fields.index("證券代號")
            idx_value = fields.index("成交金額")
            idx_close = fields.index("收盤價")
            idx_volume = fields.index("成交股數")
        except ValueError as e:
            print(f"[fetch_twse_daily_price_value_snapshot] 欄位對應失敗，"
                  f"TWSE可能改版：{e}（實際欄位：{fields}）")
            return out

        def _num(s):
            # 收盤價欄位有時會帶HTML片段（例如<p style='color:...'>），
            # 這裡先去HTML標籤再轉數字，避免整批被當成無效值丟棄。
            s = re.sub(r"<[^>]+>", "", str(s))
            try:
                return float(s.replace(",", "") or 0)
            except (ValueError, TypeError):
                return None

        for row in rows:
            code = str(row[idx_code]).strip()
            if not _is_plain_stock_code(code):
                continue
            close = _num(row[idx_close])
            value = _num(row[idx_value])
            volume = _num(row[idx_volume])
            if close is None or close <= 0:
                continue
            out[code] = {"close": close, "trading_value": value or 0.0,
                        "trading_volume": volume or 0.0}
    except Exception as e:
        print(f"[fetch_twse_daily_price_value_snapshot] 抓取失敗：{type(e).__name__}: {e}")
    return out


def _load_price_value_from_snapshot(sb, stock_code, days_back):
    """
    【R97續9新增】fetch_stock_price_and_value_history()的第一層資料
    來源——查twse_market_snapshot表裡累積的close/trading_value欄位。
    表剛開始同步時每天只累積1筆，近10天週轉率這類需要多天加總的計算
    會因為筆數不足而不準，這是跟法人/PE/營收同一種漸進式過渡——累積
    夠天數後自動變準，不用改任何程式碼。

    回傳DataFrame[close, trading_money]（欄位名沿用舊版FinMind路徑的
    命名，呼叫端compute_interval_turnover不用改），按日期新到舊排序，
    或None（查無資料/查詢失敗）。
    """
    if sb is None:
        return None
    start_date = (datetime.now(TAIPEI_TZ) - timedelta(days=days_back + 10)).strftime("%Y-%m-%d")
    try:
        res = (sb.table("twse_market_snapshot")
              .select("trade_date,close_price,trading_value")
              .eq("symbol", stock_code)
              .gte("trade_date", start_date)
              .not_.is_("close_price", "null")
              .order("trade_date", desc=True)
              .limit(days_back + 5)
              .execute())
        rows = res.data or []
        if not rows:
            return None
        df = pd.DataFrame(rows)
        df = df.rename(columns={"close_price": "close", "trading_value": "trading_money"})
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["trading_money"] = pd.to_numeric(df["trading_money"], errors="coerce")
        df = df.dropna(subset=["close"])
        if df.empty:
            return None
        return df[["trade_date", "close", "trading_money"]].set_index("trade_date")
    except Exception as e:
        print(f"[_load_price_value_from_snapshot] {stock_code} 查表失敗，"
              f"退回FinMind：{type(e).__name__}: {e}")
        return None


def _load_institutional_from_snapshot(sb, stock_code, days_back=5):
    """
    【R97續15新增，主力偵測收斂用】從twse_market_snapshot讀近N日三大法人
    買賣超(f_buy外資/t_buy投信/d_buy自營)，供主力偵測面板的「籌碼確認」
    濾網使用。跟_load_price_value_from_snapshot同一張表、同一種漸進式累積
    模式，零額外FinMind成本。

    回傳 dict：{
      "inst_net_5d": 近N日三大法人淨買超合計(張，可正可負),
      "foreign_streak": 外資從最近一天往回算連續買超天數,
      "trust_streak": 投信連續買超天數,
    }
    任何一段查不到就給None/0，呼叫端自己判斷（不編造）。
    """
    out = {"inst_net_5d": None, "foreign_streak": 0, "trust_streak": 0}
    if sb is None:
        return out
    start_date = (datetime.now(TAIPEI_TZ) - timedelta(days=days_back + 12)).strftime("%Y-%m-%d")
    try:
        res = (sb.table("twse_market_snapshot")
              .select("trade_date,f_buy,t_buy,d_buy")
              .eq("symbol", stock_code)
              .gte("trade_date", start_date)
              .order("trade_date", desc=True)
              .limit(days_back + 5)
              .execute())
        rows = res.data or []
        if not rows:
            return out
        # 新到舊（query已經desc，但保險起見再排一次）
        rows = sorted(rows, key=lambda r: r.get("trade_date", ""), reverse=True)

        def _num(v):
            try:
                return float(v) if v is not None else 0.0
            except (ValueError, TypeError):
                return 0.0

        recent = rows[:days_back]
        inst_net = sum(_num(r.get("f_buy")) + _num(r.get("t_buy")) + _num(r.get("d_buy"))
                       for r in recent)
        out["inst_net_5d"] = round(inst_net, 1)

        # 連買天數：從最近一天往回，f_buy(或t_buy)連續為正的天數
        f_streak = 0
        for r in rows:
            if _num(r.get("f_buy")) > 0:
                f_streak += 1
            else:
                break
        t_streak = 0
        for r in rows:
            if _num(r.get("t_buy")) > 0:
                t_streak += 1
            else:
                break
        out["foreign_streak"] = f_streak
        out["trust_streak"] = t_streak
        return out
    except Exception as e:
        print(f"[_load_institutional_from_snapshot] {stock_code} 查表失敗：{type(e).__name__}: {e}")
        return out


def fetch_twse_monthly_revenue_snapshot():
    """
    【R97續6新增】上市公司月營收全市場一次回傳，取代逐檔打FinMind
    TaiwanStockMonthRevenue。來源：openapi.twse.com.tw/v1/opendata/
    t187ap05_L，真實測試約1000+筆（上市公司範圍，跟現有1074檔掃描池
    量級吻合，注意不是t187ap05_P——那支是「公開發行公司」範圍更廣，
    含非上市公司，不是我們要的）。

    這支端點只回「最新一期已公告」的快照，沒有date參數可以回溯——歷史
    深度一樣由twse_market_snapshot表逐日累積達成，跟法人/融資/PE三支
    是同一個模式。

    另外這支端點很佛心，年增率/月增率官方已經算好，不用像FinMind路徑
    那樣自己抓兩期營收再手算年增/月增。

    回傳：{symbol: {'rev_yoy':.., 'rev_mom':.., 'revenue_ym':..}}
    revenue_ym是'YYYYMM'（西元），供比對用；rev_yoy/rev_mom是百分比數字。
    """
    url = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
    out = {}
    try:
        r = requests.get(url, headers=_TWSE_HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            print("[fetch_twse_monthly_revenue_snapshot] 回傳格式非預期(不是list)，略過")
            return out
        for row in data:
            code = str(row.get("公司代號", "")).strip()
            if not _is_plain_stock_code(code):
                continue

            def _f(v):
                try:
                    s = str(v).strip()
                    if s in ("", "-", "－"):
                        return None
                    return float(s.replace(",", ""))
                except (ValueError, TypeError):
                    return None
            rev_yoy = _f(row.get("營業收入-去年同月增減(%)"))
            rev_mom = _f(row.get("營業收入-上月比較增減(%)"))
            if rev_yoy is None and rev_mom is None:
                continue
            # 「資料年月」是民國年格式（如11506=民國115年6月），轉西元
            roc_ym = str(row.get("資料年月", "")).strip()
            revenue_ym = None
            if len(roc_ym) >= 5:
                try:
                    roc_year = int(roc_ym[:-2])
                    month = int(roc_ym[-2:])
                    revenue_ym = f"{roc_year + 1911}{month:02d}"
                except ValueError:
                    pass
            out[code] = {"rev_yoy": rev_yoy, "rev_mom": rev_mom, "revenue_ym": revenue_ym}
    except Exception as e:
        print(f"[fetch_twse_monthly_revenue_snapshot] 抓取失敗：{type(e).__name__}: {e}")
    return out


def _load_revenue_from_snapshot(sb, stock_code, years):
    """
    【R97續6新增】fetch_revenue_history_lagged()的第一層資料來源——查
    twse_market_snapshot表裡累積的rev_yoy/rev_mom。這裡不需要重算揭露
    延遲（disclosure_buffer_days那套邏輯）：因為這張表是「排程當天同步
    當時TWSE官方已經公告的最新一期」，寫入這張表的當下就代表這筆資料
    在trade_date這天已經是公開資訊，trade_date本身就是安全的available_
    date，不會有偷看未來的問題——這跟FinMind那條路徑「抓到整段歷史，
    要自己往後推N天才能用」是不同性質的安全機制，但保護的是同一件事。

    回傳DataFrame[available_date, yoy, mom]，跟原本FinMind路徑格式一致。
    """
    if sb is None:
        return None
    start_date = (datetime.now(TAIPEI_TZ) - timedelta(days=int(365 * years))).strftime("%Y-%m-%d")
    try:
        res = (sb.table("twse_market_snapshot")
              .select("trade_date,rev_yoy,rev_mom")
              .eq("symbol", stock_code)
              .gte("trade_date", start_date)
              .not_.is_("rev_yoy", "null")
              .order("trade_date", desc=True)
              .limit(60)
              .execute())
        rows = res.data or []
        if not rows:
            return None
        df = pd.DataFrame(rows)
        df["available_date"] = pd.to_datetime(df["trade_date"])
        df = df.rename(columns={"rev_yoy": "yoy", "rev_mom": "mom"})
        return df[["available_date", "yoy", "mom"]]
    except Exception as e:
        print(f"[_load_revenue_from_snapshot] {stock_code} 查表失敗，"
              f"退回FinMind：{type(e).__name__}: {e}")
        return None


def fetch_revenue_history_lagged(stock_code, years, token, disclosure_buffer_days=10, sb=None):
    """
    【R89搬進共用模組，原本有@st.cache_data(ttl=21600)，搬進來後拿掉這個
    裝飾器——core.py沒有streamlit可用，快取交給呼叫端自己決定要不要包】
    歷史月營收年增率+月增率，處理揭露延遲避免未來函數。台灣上市櫃公司
    月營收依規定要在次月10日前公告，把每一期營收的「可用日」設定為
    revenue_month最後一天 + disclosure_buffer_days（預設10天）的保守估計，
    在那天之前，回測時該股票的rev_yoy/rev_mom一律視為None（未公佈），
    不會偷看未來。

    回傳：DataFrame[available_date, yoy, mom]，用merge_asof對齊到訊號日期
    使用（見_lookup_lagged_revenue）。

    【R97續6新增】sb不為None時，優先查twse_market_snapshot表（官方
    t187ap05_L批次端點每日同步累積的資料，這裡不需要另外算揭露延遲，
    見_load_revenue_from_snapshot的說明）。查到足夠筆數(≥1)才用；
    查不到或sb為None則退回原本的FinMind路徑，行為完全不變。
    """
    if sb is not None:
        snap_df = _load_revenue_from_snapshot(sb, stock_code, years)
        if snap_df is not None and len(snap_df) >= 1:
            _SNAPSHOT_CACHE_STATS["revenue_hit"] += 1
            return snap_df
    _SNAPSHOT_CACHE_STATS["revenue_miss"] += 1

    url = 'https://api.finmindtrade.com/api/v4/data'
    start_date = (datetime.now(TAIPEI_TZ) - timedelta(days=int(365 * years) + 400)).strftime('%Y-%m-%d')
    try:
        params = {'dataset': 'TaiwanStockMonthRevenue', 'data_id': stock_code, 'start_date': start_date}
        if token:
            params['token'] = token
        payload = _finmind_get(url, params, max_retries=2, timeout=10)
        df = pd.DataFrame(payload.get('data', []))
        if df.empty or 'revenue' not in df.columns:
            return None
        df['revenue'] = pd.to_numeric(df['revenue'], errors='coerce')
        df['revenue_year'] = pd.to_numeric(df.get('revenue_year'), errors='coerce')
        df['revenue_month'] = pd.to_numeric(df.get('revenue_month'), errors='coerce')
        df = df.dropna(subset=['revenue', 'revenue_year', 'revenue_month'])
        if df.empty:
            return None
        df = df.sort_values(['revenue_year', 'revenue_month'])
        df = df.drop_duplicates(subset=['revenue_year', 'revenue_month'], keep='last')

        by_ym = {(int(r['revenue_year']), int(r['revenue_month'])): float(r['revenue'])
                 for _, r in df.iterrows()}

        rows = []
        for _, r in df.iterrows():
            y, m, cur = int(r['revenue_year']), int(r['revenue_month']), float(r['revenue'])
            prev_y, prev_m = (y - 1, 12) if m == 1 else (y, m - 1)
            prev_rev = by_ym.get((prev_y, prev_m))
            last_year_rev = by_ym.get((y - 1, m))
            mom = (cur - prev_rev) / prev_rev * 100 if prev_rev else None
            yoy = (cur - last_year_rev) / last_year_rev * 100 if last_year_rev else None
            if mom is None and yoy is None:
                continue
            rows.append({'revenue_year': y, 'revenue_month': m, 'yoy': yoy, 'mom': mom})

        if not rows:
            return None
        out = pd.DataFrame(rows)
        out['period_end'] = pd.to_datetime(
            out['revenue_year'].astype(int).astype(str) + '-' + out['revenue_month'].astype(int).astype(str) + '-01'
        ) + pd.offsets.MonthEnd(0)
        out['available_date'] = out['period_end'] + pd.Timedelta(days=disclosure_buffer_days)
        out = out.sort_values('available_date')[['available_date', 'yoy', 'mom']].reset_index(drop=True)
        return out
    except FinMindAPIError as e:
        print(f"[fetch_revenue_history_lagged-診斷] FinMind抓營收歷史失敗：{type(e).__name__}: {e}")
        return None
    except Exception as e:
        print(f"[fetch_revenue_history_lagged-診斷] 非預期例外：{type(e).__name__}: {e}")
        return None


def _lookup_lagged_revenue(rev_hist_df, signal_date_ts):
    """
    【R89搬進共用模組】用merge_asof概念手動查表：找出在signal_date當下，
    「已經公告」的最新一筆營收年增率/月增率。回傳(yoy, mom)，兩者都可能
    是None（該筆基期湊不出來時）。
    """
    if rev_hist_df is None or rev_hist_df.empty:
        return None, None
    # 【R96三修，真正根因見開發歷程.md】signal_date_ts(來自yfinance，帶時區
    # Asia/Taipei) vs available_date(來自FinMind轉換，無時區)，pandas不允許
    # .astype在有時區/無時區間硬轉。改用tz_localize(None)拿掉時區再比較。
    try:
        if signal_date_ts.tzinfo is not None:
            signal_date_ts = signal_date_ts.tz_localize(None)
        eligible = rev_hist_df[rev_hist_df['available_date'] <= signal_date_ts]
    except Exception as e:
        print(f"[_lookup_lagged_revenue-診斷] 日期比較仍然失敗：{type(e).__name__}: {e}｜"
              f"signal_date_ts={signal_date_ts!r}({type(signal_date_ts)})｜"
              f"available_date.dtype={rev_hist_df['available_date'].dtype}")
        return None, None
    if eligible.empty:
        return None, None
    latest = eligible.iloc[-1]
    yoy = float(latest['yoy']) if pd.notna(latest.get('yoy')) else None
    mom = float(latest['mom']) if pd.notna(latest.get('mom')) else None
    return yoy, mom


# ==============================================================================
# 十一、查1~查14+情報雷達 回測引擎本體（R95搬進共用模組，見開發歷程.md）
# ==============================================================================

# 【R88新增，R95搬移】門檻集中管理——側欄面板即時覆寫透過get_threshold()讀取。
PE_LANDMINE = 30.0

DEFAULT_THRESHOLDS = {
    'vol_ratio_low': 0.6,      # 量縮沉澱門檻（查10「量縮+融資減少」用）
    'vol_ratio_surge': 2.0,    # 爆量門檻（查1/查4主升段/is_volume_dump用）
    'six_day_gain_watch': 20,  # 六日累計漲跌｜watch等級門檻
    'six_day_gain_high': 32,   # 六日累計漲跌｜high等級門檻
}


def get_threshold(key):
    """
    統一的門檻讀取入口——優先讀st.session_state裡使用者透過側欄調整的
    override值，沒調整過（或st不可用，例如排程環境）就退回DEFAULT_THRESHOLDS
    的預設值。見檔案開頭「唯一例外」的說明，這裡的try/except是刻意設計、
    不是隨手防呆。
    """
    try:
        return st.session_state.get(f'threshold_override_{key}', DEFAULT_THRESHOLDS[key])
    except Exception:
        return DEFAULT_THRESHOLDS[key]


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
    c_k_val = card.get('k_val')
    margin_shrink = (c_margin < 0) if c_has_margin else True

    if "情報雷達：" in cmd:
        return cmd.split("情報雷達：")[-1].strip() in c_sources
    if "情報黃金交叉" in cmd:
        return len(c_sources) >= 2
    if "查1." in cmd:
        # 【R96修復，見開發歷程.md附件06】原本只檢查"金叉" in c_kdj，
        # 沒檢查K值50以上/以下——50以下只是跌深反彈，50以上才是真轉強。
        # 加上c_k_val>50條件，缺值時保守判不通過。
        return bool(card.get('is_first_red') and c_vol_ratio >= get_threshold('vol_ratio_surge')
                    and "金叉" in c_kdj and c_k_val is not None and c_k_val > 50)
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
        return bool(c_vol_ratio >= get_threshold('vol_ratio_surge'))
    if "查10." in cmd:
        return bool(0 < c_vol_ratio <= get_threshold('vol_ratio_low') and margin_shrink)
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


def detect_k_line_patterns_v152(df, atr_val):
    patterns = []
    if len(df) < 5:
        return patterns
    if pd.isna(atr_val) or atr_val == 0:
        atr_val = df['Close'].iloc[-1] * 0.02

    c0, c1, c2 = float(df['Close'].iloc[-1]), float(df['Close'].iloc[-2]), float(df['Close'].iloc[-3])
    o0, o1, o2 = float(df['Open'].iloc[-1]), float(df['Open'].iloc[-2]), float(df['Open'].iloc[-3])
    is_significant = abs(c0 - o0) > atr_val * 0.5

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

    if not patterns and len(df) >= 20:
        recent5_range = float(df['High'].tail(5).max() - df['Low'].tail(5).min())
        avg20_daily_range = float((df['High'].tail(20) - df['Low'].tail(20)).mean())
        if avg20_daily_range > 0 and recent5_range < avg20_daily_range * 2.2:
            patterns.append({"text": "壓縮盤整", "class": "tag-neutral"})
    return patterns


# 【R95】原本掛@st.cache_data(ttl=21600)，搬進core.py後拿掉這個裝飾器
# （排程環境沒有streamlit runtime），改用模組層級dict做同一process內的
# 簡單記憶體快取——同一次回測/排程執行最多真正抓一次大盤歷史，效果足夠。
_TWII_REGIME_CACHE = {}


def fetch_twii_regime_history(years):
    """抓 TWII 歷史，算出每一天的 20MA 位階，回測時用日期查表，不用每檔股票各抓一次大盤。"""
    if years in _TWII_REGIME_CACHE:
        return _TWII_REGIME_CACHE[years]
    try:
        twii = yf.Ticker("^TWII", session=_SESSION)
        df = twii.history(period=f"{years}y", auto_adjust=False, timeout=10)
        if df.empty:
            _TWII_REGIME_CACHE[years] = None
            return None
        df['MA20'] = df['Close'].rolling(20).mean()
        regime = (df['Close'] > df['MA20'])
        regime.index = df.index.strftime('%Y-%m-%d')
        _TWII_REGIME_CACHE[years] = regime
        return regime
    except Exception as e:
        print(f"[fetch_twii_regime_history-診斷] 抓大盤TWII歷史失敗：{type(e).__name__}: {e}")
        _TWII_REGIME_CACHE[years] = None
        return None


def probe_price_data_availability(symbols, years=2):
    """
    【R95續5新增】診斷用途——total_sample_count=0時，光靠單一檔(2330)探測
    只能知道「yfinance整體連不連得通」，沒辦法回答「這批symbols裡到底有
    幾檔真的抓得到堪用的價格資料」。這裡用跟_filter_backtest_one_stock
    完全相同的抓價邏輯(.TW失敗才退.TWO、len<40視為不堪用)單獨跑一次，
    只做這一步、不算任何濾網條件，成本遠低於完整回測，適合在「技術面0筆」
    時額外呼叫一次來源分解，而不是繼續猜測。

    回傳dict：{'usable': N, 'empty_or_short': N, 'total': N}。
    """
    usable, bad = 0, 0
    for stock_code in symbols:
        try:
            tk_obj = yf.Ticker(f"{stock_code}.TW", session=_SESSION)
            df = tk_obj.history(period=f"{years}y", auto_adjust=False, timeout=10)
            if df.empty:
                tk_obj = yf.Ticker(f"{stock_code}.TWO", session=_SESSION)
                df = tk_obj.history(period=f"{years}y", auto_adjust=False, timeout=10)
            df = df.dropna(subset=['Close'])
            if df.empty or len(df) < 40:
                bad += 1
            else:
                usable += 1
        except Exception:
            bad += 1
    return {'usable': usable, 'empty_or_short': bad, 'total': len(symbols)}


def _filter_backtest_one_stock(stock_code, years, selected_cmds, selected_k_patterns,
                                token, twii_regime, market_bull_filter, dividend_db=None):
    """
    【R95：dividend_db改為外部傳入】原本直接讀v160.py的模組全域DIVIDEND_DB，
    搬進共用模組後不能再這樣做（core.py不能反向依賴v160.py，否則循環
    import）。呼叫端（網頁版傳DIVIDEND_DB、排程版可以傳自己抓到的股利dict
    或直接傳None——查11殖利率條件在沒有股利資料時就是「不符合」，不會出錯，
    只是那個濾網樣本會變少，不影響其他濾網的回測）。
    """
    rows = []
    dividend_db = dividend_db or {}
    try:
        tk_obj = yf.Ticker(f"{stock_code}.TW", session=_SESSION)
        df = tk_obj.history(period=f"{years}y", auto_adjust=False, timeout=10)
        if df.empty:
            tk_obj = yf.Ticker(f"{stock_code}.TWO", session=_SESSION)
            df = tk_obj.history(period=f"{years}y", auto_adjust=False, timeout=10)
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

    need_inst = any(("查4." in c or "查5." in c or "查10." in c or "查3." in c) for c in selected_cmds)
    need_kline = any("查12." in c for c in selected_cmds)
    need_pe = any("查3." in c for c in selected_cmds)

    inst_hist = fetch_institutional_history(stock_code, years, token) if need_inst else None
    rev_hist = fetch_revenue_history_lagged(stock_code, years, token) if any(
        ("查6." in c or "查3." in c) for c in selected_cmds) else None
    div_info = dividend_db.get(stock_code)
    cash_div = div_info.get('cash', 0.0) if div_info else 0.0

    pe_hist = None
    if need_pe:
        _pe_hist_df = fetch_pe_history(stock_code, token, years=years + 3)
        if _pe_hist_df is not None and not _pe_hist_df.empty and 'PER' in _pe_hist_df.columns:
            _s = _pe_hist_df.dropna(subset=['PER']).set_index('date')['PER']
            _s = _s[_s > 0].sort_index()
            pe_hist = _s if not _s.empty else None

    for i in range(20, len(df) - 10):
        d = date_strs[i]
        # 【R96新增，強化防護】原本這段迴圈完全沒有try/except，單一天
        # 出錯會讓整檔股票近2年回測資料全部作廢（一個角落的型別問題能讓
        # 60檔全部0樣本的根因）。加上後單一天出錯只跳過那天，不拖累整批。
        try:
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
                _raw_margin = row.get('margin_diff')
                has_margin = pd.notna(_raw_margin)
                margin_diff = float(_raw_margin) if has_margin else 0.0

            rev_yoy, rev_mom = _lookup_lagged_revenue(rev_hist, df.index[i]) if rev_hist is not None else (None, None)
            div_yield = (cash_div / curr_price * 100) if curr_price > 0 else 0.0

            value_score_hist, landmine_hist = 0, False
            if need_pe:
                pe_percentile_h, pe_raw_h = None, None
                if pe_hist is not None and d in pe_hist.index:
                    _cur_pe_h = pe_hist.loc[d]
                    if isinstance(_cur_pe_h, pd.Series):
                        _cur_pe_h = _cur_pe_h.iloc[-1]
                    pe_raw_h = float(_cur_pe_h)
                    _window_h = pe_hist[pe_hist.index < d]
                    if len(_window_h) >= 60:
                        pe_percentile_h = round(float((_window_h < pe_raw_h).mean() * 100), 1)

                _score = 40
                if pe_percentile_h is not None:
                    if pe_percentile_h <= 20:   _score += 30
                    elif pe_percentile_h <= 40: _score += 18
                    elif pe_percentile_h <= 60: _score += 5
                    elif pe_percentile_h <= 80: _score -= 10
                    else:                       _score -= 20
                elif pe_raw_h is not None:
                    if pe_raw_h <= 12:   _score += 20
                    elif pe_raw_h <= 18: _score += 10
                    elif pe_raw_h > PE_LANDMINE: _score -= 12
                else:
                    _score -= 15

                if rev_yoy is not None:
                    if rev_yoy > 20:    _score += 22
                    elif rev_yoy > 0:   _score += 12
                    elif rev_yoy < -10: _score -= 18
                    elif rev_yoy < 0:   _score -= 10

                if div_yield >= 4.5:  _score += 15
                elif div_yield >= 3.0: _score += 8

                value_score_hist = int(max(0, min(100, _score)))
                _is_expensive_h = ((pe_percentile_h is not None and pe_percentile_h >= 80)
                                   or (pe_percentile_h is None and pe_raw_h is not None and pe_raw_h > PE_LANDMINE))
                _f_5d_h = 0.0
                if inst_hist is not None and not inst_hist.empty:
                    _window_dates_h = date_strs[max(0, i - 4): i + 1]
                    _avail_h = inst_hist.reindex(_window_dates_h)['f_buy'].fillna(0.0)
                    if len(_avail_h) > 0:
                        _f_5d_h = float(_avail_h.sum())
                landmine_hist = bool(_is_expensive_h and (rev_yoy is not None and rev_yoy < 0) and _f_5d_h < 0)

            market_bull = True
            if market_bull_filter and twii_regime is not None and d in twii_regime.index:
                market_bull = bool(twii_regime.loc[d])
            if market_bull_filter and not market_bull:
                continue

            card = {
                'price': curr_price, 'ma60': ma60, 'vol_ratio': vol_ratio,
                't_buy': t_buy, 'f_buy': f_buy, 'margin_diff': margin_diff, 'has_margin': has_margin,
                'rev_yoy': rev_yoy, 'kdj_str': kdj_str, 'k_val': round(k_v, 1), 'd_val': round(d_v, 1),
                'value_score': value_score_hist, 'landmine': landmine_hist,
                'is_first_red': is_first_red, 'is_yesterday_strong': is_yesterday_strong,
                'div_yield': div_yield, 'detected_patterns': detected_patterns,
            }

            future_3d_ret = (float(df['Close'].iloc[i + 3]) - curr_price) / curr_price * 100 if curr_price > 0 else 0.0
            future_10d_ret = (float(df['Close'].iloc[i + 10]) - curr_price) / curr_price * 100 if curr_price > 0 else 0.0

            for cmd in selected_cmds:
                if evaluate_single_condition(cmd, card, None, selected_k_patterns):
                    rows.append({'stock': stock_code, 'date': d, 'filter': cmd,
                                'future_3d_ret': round(future_3d_ret, 2), 'future_10d_ret': round(future_10d_ret, 2)})
        except Exception as _e:
            print(f"[_filter_backtest_one_stock-診斷] {stock_code} {d} 這天判斷失敗，"
                  f"跳過這一天繼續：{type(_e).__name__}: {_e}")
            continue
    return rows


def run_filter_backtest(stock_list, years, selected_cmds, selected_k_patterns, use_market_regime,
                        token, dividend_db=None, progress_callback=None, max_workers=6):
    """
    多執行緒跑完整濾網回測。max_workers 刻意比技術面回測(8)低一點——這裡每個任務
    多打了法人籌碼/營收兩種歷史API，即使額度夠，也不必要對FinMind太密集併發。

    【R95】token/dividend_db改為必要／可選參數，呼叫端（網頁版get_active_
    fm_token()+DIVIDEND_DB，或未來排程自己的等價物）自己決定要傳什麼，
    這個函式本身不再依賴任何v160.py專屬的全域狀態。
    """
    twii_regime = fetch_twii_regime_history(years) if use_market_regime else None
    all_rows = []
    total = max(1, len(stock_list))

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_filter_backtest_one_stock, code, years, selected_cmds,
                                   selected_k_patterns, token, twii_regime, use_market_regime,
                                   dividend_db): code
                  for code in stock_list}
        for i, future in enumerate(concurrent.futures.as_completed(futures)):
            if progress_callback:
                progress_callback(i + 1, total, futures[future])
            try:
                all_rows.extend(future.result())
            except Exception as e:
                # 【R95續7修復】原本except Exception: continue完全靜默吞掉
                # 例外，導致「60檔0筆樣本」查不出線索。現在印出來，排程端
                # 進GitHub Actions log、網頁版進Streamlit Cloud log，下次
                # 再發生有線索可查。
                print(f"[run_filter_backtest] {futures[future]} 回測失敗：{type(e).__name__}: {e}")
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


def summarize_filter_backtest_walkforward(all_rows, window_months=6):
    """
    【R77新增】回測引擎滾動驗證(Walk-Forward)——不要用整個回測區間算出單一
    固定的命中率，因為台股資金輪動快，某個濾網可能只在特定市場氣氛下有效，
    全區間平均會把「只在牛市有效」跟「任何時候都有效」混在一起，看不出差異。

    做法：把run_filter_backtest已經算好的all_rows按時間切成連續的滾動窗口
    (預設每6個月一個)，每個窗口各自算一次命中率。

    回傳DataFrame[濾網條件, 窗口, 樣本數, 3日勝率%, 3日平均報酬%]，依濾網、
    窗口時間排序；樣本數<5的窗口直接跳過，避免單筆極端值主導判讀。
    """
    if not all_rows:
        return pd.DataFrame()
    df = pd.DataFrame(all_rows)
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date')

    rows_out = []
    for f in sorted(df['filter'].unique()):
        subset = df[df['filter'] == f]
        if subset.empty:
            continue
        window_start = subset['date'].min()
        end = subset['date'].max()
        while window_start <= end:
            window_end = window_start + pd.DateOffset(months=window_months)
            win = subset[(subset['date'] >= window_start) & (subset['date'] < window_end)]
            if len(win) >= 5:
                rows_out.append({
                    '濾網條件': f,
                    '窗口': f"{window_start.strftime('%Y-%m')}~"
                            f"{(window_end - pd.DateOffset(days=1)).strftime('%Y-%m')}",
                    '樣本數': len(win),
                    '3日勝率%': round((win['future_3d_ret'] > 0).mean() * 100, 1),
                    '3日平均報酬%': round(win['future_3d_ret'].mean(), 2),
                })
            window_start = window_end
    return pd.DataFrame(rows_out)


# ==============================================================================
# 十二、情報雷達回測 + GitHub Actions排程自動化——R95續，讓排程版也能
# 用同一套邏輯。compute_forward_return/run_intel_radar_backtest原本在
# v160.py，搬過來時改成呼叫端先查好rows再傳進來（網頁版/排程版各自
# 用自己的Supabase client物件查，介面一致，不用core.py重新定義一套）。
# ==============================================================================
def compute_forward_return(symbol, base_price, intel_date_str, trading_days):
    """
    算某檔股票從 intel_date 起算、trading_days 個交易日後的報酬率。
    無未來函數：用歷史股價，若未到期（資料不足）回 None。
    base_price 為 0 時（儲存當下沒抓），從歷史補抓 intel_date 當天收盤當基準。
    """
    try:
        try:
            tk = yf.Ticker(f"{symbol}.TW", session=_SESSION)
        except Exception:
            tk = yf.Ticker(f"{symbol}.TW")
        hist = tk.history(period="6mo", timeout=8)
        if hist.empty:
            try:
                tk = yf.Ticker(f"{symbol}.TWO", session=_SESSION)
            except Exception:
                tk = yf.Ticker(f"{symbol}.TWO")
            hist = tk.history(period="6mo", timeout=8)
        hist = hist.dropna(subset=['Close'])
        if hist.empty:
            return None
        hist.index = hist.index.strftime('%Y-%m-%d')
        dates = list(hist.index)
        after = [d for d in dates if d >= intel_date_str]
        if not after:
            return None
        if not base_price or base_price <= 0:
            base_price = float(hist.loc[after[0], 'Close'])
        if base_price <= 0 or len(after) <= trading_days:
            return None
        target_price = float(hist.loc[after[trading_days], 'Close'])
        return round((target_price - base_price) / base_price * 100, 2)
    except Exception:
        return None


def run_intel_radar_backtest(rows, selected_intel_cmds, cross_window_days=7):
    """
    情報雷達／情報黃金交叉 回測支援。rows是呼叫端已經查好的intel_performance
    全部紀錄(list[dict]，欄位symbol/source/tag/intel_date/base_price)——
    這裡不自己查Supabase，由呼叫端決定資料怎麼來（網頁版/排程版用法見本節
    開頭說明）。

    - 「情報雷達：X」單一來源：intel_performance裡source==X的每一筆都是一個
      訊號樣本，直接算forward return。
    - 「情報黃金交叉」：同一檔股票在cross_window_days天內被2個以上不同來源
      提及才算一次訊號——用「這個窗口內第一次湊到第2個不同來源」的那一天
      當訊號日，之後同一群消息不會被重複計入，避免同一波消息的樣本數被灌水。

    回傳all_rows（list[dict]，格式跟run_filter_backtest的all_rows一致）。
    """
    if not rows:
        return []

    single_source_cmds = {}
    want_cross = False
    for cmd in selected_intel_cmds:
        if "情報雷達：" in cmd:
            single_source_cmds[cmd.split("情報雷達：")[-1].strip()] = cmd
        elif "情報黃金交叉" in cmd:
            want_cross = True

    all_rows = []

    if single_source_cmds:
        for r in rows:
            src = r.get('source', '未知')
            if src not in single_source_cmds:
                continue
            sym, idate, bp = r.get('symbol'), r.get('intel_date'), r.get('base_price')
            if not sym or not idate:
                continue
            ret3 = compute_forward_return(sym, bp, idate, 3)
            ret10 = compute_forward_return(sym, bp, idate, 10)
            if ret3 is None and ret10 is None:
                continue
            all_rows.append({
                'stock': sym, 'date': idate, 'filter': single_source_cmds[src],
                'future_3d_ret': ret3 if ret3 is not None else 0.0,
                'future_10d_ret': ret10 if ret10 is not None else 0.0,
            })

    if want_cross:
        by_symbol = {}
        for r in rows:
            sym = r.get('symbol')
            if sym and r.get('intel_date'):
                by_symbol.setdefault(sym, []).append(r)

        for sym, recs in by_symbol.items():
            recs_sorted = sorted(recs, key=lambda x: x['intel_date'])
            cluster_sources = set()
            last_date = None
            for r in recs_sorted:
                idate, src = r['intel_date'], r.get('source', '未知')
                try:
                    d_this = datetime.strptime(idate, '%Y-%m-%d')
                except Exception:
                    continue
                if last_date is not None and (d_this - last_date).days > cross_window_days:
                    cluster_sources = set()
                cluster_sources.add(src)
                last_date = d_this
                if len(cluster_sources) == 2:
                    ret3 = compute_forward_return(sym, r.get('base_price'), idate, 3)
                    ret10 = compute_forward_return(sym, r.get('base_price'), idate, 10)
                    if ret3 is None and ret10 is None:
                        continue
                    all_rows.append({
                        'stock': sym, 'date': idate, 'filter': "🏆 情報黃金交叉（多個情報來源同時指向）",
                        'future_3d_ret': ret3 if ret3 is not None else 0.0,
                        'future_10d_ret': ret10 if ret10 is not None else 0.0,
                    })

    return all_rows
