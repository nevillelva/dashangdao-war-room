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

# 【R60新增】共用模組版本號——warroom_v160.py匯入後會檢查這個數字，版本對不上
# 就在啟動當下直接明講「這兩個檔案版本不同步」並停住，不要等到某個深藏在
# ThreadPoolExecutor worker裡的呼叫因為缺參數炸出TypeError，才回頭猜半天。
# 這個bug已經真實發生兩次（一次ImportError、一次determine_signal()缺
# foreign_buy_streak3參數），都是同一個根因：warroom_v160.py換了新版，
# warroom_core.py忘記跟著換。每次幫這個共用模組加新東西，這個數字要+1。
CORE_VERSION = 87


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
    # 【R55新增】兩組帳號的用量數字一模一樣時，最常見的原因不是巧合，是
    # Streamlit secrets裡兩組token其實是同一個字串（複製貼上時貼重複了）。
    # 用量記錄是用token字串本身當key（_FM_USAGE_LOG[cred]），如果兩組token
    # 字串完全相同，它們讀到的其實是同一筆記錄，數字當然會完全一樣，不是
    # 「輪替剛好平均分配」這麼單純的巧合。這裡直接檢查並提醒，不用猜。
    if len(tokens) >= 2 and len(set(tokens)) < len(tokens):
        rows.append("⚠️ 偵測到有兩組（或以上）token字串完全相同——這代表你設定的其實是"
                     "同一組帳號被算成兩組，不是真的兩組獨立額度。請去Streamlit secrets"
                     "確認每組token是不是不小心貼重複了。")
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
                # 【R56新增】原本只記狀態碼(如"HTTP 400")，完全看不出FinMind那邊
                # 實際回了什麼——排程端的資料源異常警報曾經只顯示「http_error:
                # HTTP 400」，沒有辦法判斷是我們的請求參數有問題、還是FinMind
                # 那次剛好回應異常。這裡補上回應內容片段（截斷避免log爆量），
                # 下次再發生同樣狀況，才看得出真正原因。
                _body_preview = (res.text or '')[:200].replace('\n', ' ')
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
                # 【R56新增】「權限不足」有兩種完全不同的情況，過去分類成同一個
                # permission_denied，處理方式卻應該不一樣：
                #   (a) token本身失效/格式錯誤（訊息含illegal/invalid）——這是
                #       整個session都不會好的問題，每次都重試等於白白浪費一次
                #       完整的重試+逾時等待時間。
                #   (b) token有效，但這個特定資料集要更高付費方案（訊息含
                #       sponsor/backer/permission/upgrade）——這只是「這個資料集
                #       不行」，不代表這組token整個報廢，其他資料集可能還是通的，
                #       不該連坐標記冷卻。
                # 這裡只把(a)標記冷卻，(b)維持原樣每次都再試一次（因為呼叫端
                # 每次要的資料集可能不一樣）。標記冷卻後，同一組壞掉的token
                # 15分鐘內不會再被排到第一順位重試，明顯減少「每一次呼叫都先
                # 在同一組壞token上重試+逾時等待才換下一組」這種白白浪費的時間。
                _detail_lower = (e.detail or '').lower()
                if 'illegal' in _detail_lower or 'invalid' in _detail_lower:
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
                     rev_mom=None, rev_yoy=None, day_trader_alert=False,
                     foreign_buy_streak3=None):
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

    【R58新增】foreign_buy_streak3：法人持續性因子的精確版信號（連續3天外資
    買超與否，True/False/None）。同樣預設None、向下相容——沒傳就是「不知道」，
    因子函式會自動退回舊版的5日/10日方向代理，不會報錯。
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
                # 【R62修復】原本這裡「z(最近成交) → o(今日開盤) → y(昨收)」依序
                # 找第一個有值的當作即時價——總指揮官回報：聯電鎖跌停好幾小時，
                # 「即時」欄位卻會不定期跳回109附近，但當天真正的成交價一直
                # 鎖在102.5左右。查出根因：109其實是聯電「今日開盤價」，不是
                # 即時成交價。z欄位（最近成交）在鎖跌停、暫時沒有新成交時可能
                # 短暫回傳空值，這時候原本的邏輯會誤把「今日開盤」甚至「昨收」
                # 這種完全不同的參考價，冒充成「即時」顯示出來——這正是這個
                # 函式自己的docstring說好的「查不到的股票不會出現在結果裡」
                # 這個承諾被違反的地方。即時報價的意義就是「現在成交在哪」，
                # 沒有成交價寧可誠實顯示沒資料("—")，也不該顯示一個看起來像
                # 即時、實際上是好幾小時前(甚至前一天)的舊參考價。
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
                    "time": item.get("t", ""), "date": item.get("d", ""),
                    "ok": True,
                }
        except Exception as e:
            print(f"[即時報價] 批次抓取失敗：{e}")
            continue
    return results


# ==============================================================================
# 五、千張大戶（TDCC集保股權分散表）共用解析邏輯——R70新增
# ------------------------------------------------------------------------------
# 【重大更正】R69當時查證TDCC的opendata端點，測試的是smart.tdcc.com.tw這個
# 子網域，得到「robots.txt明確禁止自動化存取」的結果，因此判定只能走CSV
# 人工上傳。R70回頭查證才發現：官方文件跟社群實際使用的網址其實是
# opendata.tdcc.com.tw（不是smart.tdcc.com.tw，兩個是不同子網域），這個網域
# 根本沒有robots.txt檔案（測試回傳404），而且有真實的VBA/Excel自動化案例
# 長期穩定使用同一個URL。R69的CSV上傳結論是建立在測錯網域的前提上，這裡
# 更正：千張大戶現在可以由排程自動抓取，不用再靠人工上傳。
#
# 這三個函式(_parse_holding_level_lower/parse_tdcc_holding_csv/
# compute_big_holder_ratios)搬進共用模組，是因為現在網頁版跟排程版都要用
# 同一套解析邏輯——網頁版的CSV上傳UI繼續保留當備援（例如哪天官方網址又
# 改版擋掉了，還有手動路徑可以撐著），排程版則是新的自動化路徑，兩邊不該
# 各自維護一份解析邏輯。
# ==============================================================================
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

    level_lower重用_parse_holding_level_lower，這個級距字串格式TDCC跟
    FinMind是同一個來源，解析邏輯不用重寫。
    """
    try:
        text = raw_bytes.decode('utf-8', errors='ignore')
        if '證券代號' not in text[:2000] and '股票代號' not in text[:2000]:
            text = raw_bytes.decode('big5', errors='ignore')  # 保險：不同時期版本編碼可能不同
    except Exception:
        return None
    try:
        df = pd.read_csv(io.StringIO(text))
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
    df['level_lower'] = df['level'].apply(_parse_holding_level_lower)
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
# 六、券商分點——HiStock免費資料源（R72新增）
# ------------------------------------------------------------------------------
# 【背景】券商分點原本只能靠TWSE的bsr.twse.com.tw（有reCAPTCHA v2保護）走
# 人工CSV上傳。經過多輪查證（TWSE官方OpenAPI確認不含分點資料、玩股網的
# 頁面資料是JS動態載入+背後API被Cloudflare擋下），最後在HiStock
# (histock.tw)找到一個真正乾淨的路徑：
#   https://histock.tw/stock/branch.aspx?no={股票代號}
# 這是傳統ASP.NET WebForms架構（有__doPostBack痕跡），表格是伺服器端直接
# 渲染，不需要登入、不需要瀏覽器執行JavaScript、沒有反爬蟲防護——用plain
# requests + pandas.read_html就能正常讀取，已經實測驗證過表格結構。
#
# 這不是繞過任何安全機制——單純是這個公開頁面本身就沒有設反自動化的防護，
# 跟我們拒絕的CAPTCHA破解、Cloudflare指紋偽裝是完全不同性質的事情。
# ==============================================================================
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

    回傳DataFrame[broker_name, buy_shares, sell_shares, net_shares]
    （單位：張），或None（表格結構跟預期不符、可能是網站改版了）。
    """
    try:
        tables = pd.read_html(io.StringIO(html_text))
    except Exception:
        return None
    if not tables:
        return None
    t = tables[0]
    # 【R72修復】原本以為右半的「買超」欄位跟左半的「賣超」一樣會被pandas
    # 加上.1後綴，實測後發現pandas只對「真的重複」的欄位名稱加後綴——
    # 「券商名稱」左右都叫這個名字所以有.1，但「賣超」「買超」本來就是
    # 兩個不同的字串，不會被當成重複，所以「買超」沒有.1後綴。
    _expected = {'券商名稱', '買張', '賣張', '賣超',
                 '券商名稱.1', '買張.1', '賣張.1', '買超'}
    if not _expected.issubset(set(t.columns)):
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
    except Exception as e:
        print(f"[券商分點] HiStock連線失敗：{stock_code} {e}")
        return None


# ==============================================================================
# 七、處置股/注意股預警——R79新增（已驗證的官方端點）
# ------------------------------------------------------------------------------
# 【查證結果】三個候選端點，兩個直接可用，用真實回應確認過欄位名稱：
#   - TWSE(上市)注意股：openapi.twse.com.tw/v1/announcement/notice
#     欄位：Number/Code/Name/NumberOfAnnouncement/TradingInfoForAttention/
#           Date/ClosingPrice/PE
#   - TWSE(上市)處置股：openapi.twse.com.tw/v1/announcement/punish
#     欄位：Number/Date/Code/Name/NumberOfAnnouncement/ReasonsOfDisposition/
#           DispositionPeriod/DispositionMeasures/Detail/LinkInformation
#   - TPEx(上櫃)處置股：www.tpex.org.tw/openapi/v1/tpex_disposal_information
#     欄位：Date/SecuritiesCompanyCode/CompanyName/DispositionPeriod/
#           DispositionReasons/DisposalCondition
#   - TPEx(上櫃)注意股：測試失敗（回傳HTML不是JSON），端點名稱可能不對，
#     這裡先不做上櫃注意股，只做上市注意股+兩邊的處置股，缺口誠實標註。
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
# 八、自結財報/重大訊息掃描——R79新增（已驗證的官方端點）
# ------------------------------------------------------------------------------
# 【查證結果】openapi.twse.com.tw/v1/opendata/t187ap04_L 已驗證可用，
# 真實回應確認欄位為繁體中文：出表日期/發言日期/發言時間/公司代號/
# 公司名稱/主旨/符合條款/事實發生日/說明。這是TWSE官方重大訊息公告，
# 涵蓋範圍比「自結財報」廣（改名、業績說明會等都算重大訊息），要篩出
# 自結財報相關的，用「主旨」欄位關鍵字比對。
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


# ==============================================================================
# 九、命中率自動化驗證——門檻敏感度掃描（R87新增）
# ------------------------------------------------------------------------------
# 【範圍聲明，誠實標註】這不是把「查1~查12完整濾網回測」整套搬過來——那套
# 邏輯目前深度依賴warroom_v160.py裡的其他函式(DIVIDEND_DB、K線型態辨識等)，
# 要整套搬進共用模組是一次大重構，這裡先聚焦在總指揮官具體點名的「爆量比
# 門檻」跟「六日累計漲跌門檻」這兩個，用獨立、輕量的方式驗證敏感度——
# 這兩個門檻本身的邏輯不複雜(單一數值比較)，不需要完整回測引擎的複雜度
# 就能驗證。完整12濾網的自動化排程列為之後的延伸項目，不在這輪範圍內。
# ==============================================================================
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
