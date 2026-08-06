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
from datetime import datetime, timedelta

# 【R95新增，設計鐵律的唯一例外，須說明清楚】上面的鐵律是「絕對不能import
# streamlit」，這裡刻意違反、但用try/except把風險徹底隔離：get_threshold()
# 需要讀st.session_state才能讓網頁版側欄「🎛️門檻參數調整」面板的即時覆寫
# 生效——如果完全不import streamlit，這個函式搬進來後，即使在真正的網頁版
# 環境執行，也永遠讀不到session_state（Python的名稱查找是看函式「定義」在
# 哪個模組，不是看呼叫者），使用者調整門檻會變成看得到介面、但完全不影響
# 判斷邏輯的假功能，比不搬移更糟。
# 用try/except包住import，st裝不到（例如GitHub Actions排程環境）時st=None，
# get_threshold本身的except Exception會接住st.session_state的AttributeError、
# 安全退回預設值——排程環境不會因為這個import而壞掉，這條鐵律的精神（排程
# 不能因為Streamlit而炸掉）完全沒被違反，只是換一種寫法達成。
try:
    import streamlit as st
except ImportError:
    st = None


# 【R60新增】共用模組版本號——warroom_v160.py匯入後會檢查這個數字，版本對不上
# 就在啟動當下直接明講「這兩個檔案版本不同步」並停住，不要等到某個深藏在
# ThreadPoolExecutor worker裡的呼叫因為缺參數炸出TypeError，才回頭猜半天。
# 這個bug已經真實發生兩次（一次ImportError、一次determine_signal()缺
# foreign_buy_streak3參數），都是同一個根因：warroom_v160.py換了新版，
# warroom_core.py忘記跟著換。每次幫這個共用模組加新東西，這個數字要+1。
CORE_VERSION = 101


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
            if res.status_code in (401, 403):
                # 【R95新增】401/403是「這組憑證本身被拒絕」的明確訊號（不是伺服器
                # 暫時性問題），過去被歸類成跟500/連線逾時一樣的http_error，在
                # _finmind_get_once這層重試3次、又在_finmind_get外層被當成「換一組
                # 再試」但從不標記冷卻——結果是一個持續回401/403的壞憑證，會在
                # 「每一次」呼叫（單檔同步的每個子查詢、深度財報的每張表）都被完整
                # 重試一輪才被跳過，這是總指揮官回報「單檔同步/深度財報要等5分鐘
                # 以上」的根因之一：好幾個查詢各自都在同一組壞憑證上重複繳同樣的
                # 時間成本。現在直接歸類成permission_denied、不重試，讓外層立刻
                # 標記冷卻、換下一組——同一組壞憑證這個session之後就不會再被排到
                # 前面浪費時間。
                _body_preview = (res.text or '')[:200].replace('\n', ' ')
                raise FinMindAPIError('permission_denied', f"HTTP {res.status_code}：{_body_preview}")
            if res.status_code != 200:
                # 【R56新增】原本只記狀態碼(如"HTTP 400")，完全看不出FinMind那邊
                # 實際回了什麼——排程端的資料源異常警報曾經只顯示「http_error:
                # HTTP 400」，沒有辦法判斷是我們的請求參數有問題、還是FinMind
                # 那次剛好回應異常。這裡補上回應內容片段（截斷避免log爆量），
                # 下次再發生同樣狀況，才看得出真正原因。
                #
                # 【R95續18新增】總指揮官這輪實測TaiwanStockKBar，拿到的正是
                # 這個分支——HTTP 400，body裡卻明明白白寫著
                # "Your level is free. Please update your user level."，是
                # 跟401/403一樣清楚的「這個資料集需要付費方案」訊號，但這個
                # 分支原本完全不解析body、直接歸類成含糊的http_error然後重試
                # 3次——跟401/403那組修復是同一種病根：權限類錯誤被誤判成
                # 暫時性錯誤，浪費重試次數，而且錯誤標籤沒講清楚真正原因。
                # 這裡在非200的分支裡也試著解析JSON、比對同一組permission
                # 關鍵字，符合的話一樣歸類成permission_denied、不重試；解析
                # 失敗或關鍵字對不上，才照原本方式退回http_error。
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
                # 【R95新增】HTTP 401/403（見_finmind_get_once）現在也走這條路徑，
                # 同樣屬於「這組憑證本身壞了」，一併標記冷卻，理由跟illegal/invalid
                # 完全一樣：不冷卻的話，同一個壞憑證會在之後每一次呼叫都被重新
                # 排到前面重試一次，白白疊加等待時間。
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
        # 【R95追加修復】pd.read_csv預設會把「證券代號」這種看起來像純數字的欄位
        # 推斷成int64，讓001xxx這類前面帶0的代號（部分興櫃/月月配債券代碼）
        # 被截斷成1xxx，跟真正的股票代號對不起來、永遠比對不到。強制用字串讀取
        # 這個特定欄位，不讓pandas自作主張推斷型別。這裡先用寬鬆的dtype=str整張
        # 表讀入（這份CSV欄位都能安全用字串處理，數值欄位下面還是會轉numeric），
        # 比起等rename完才知道哪欄是symbol再回頭補救更單純可靠。
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
    # 【R95續17修復】原本寫死只看tables[0]，假設分點表格永遠是頁面上第一個
    # <table>。總指揮官這輪回報「HTTP 200、內容長度74571字元、表格關鍵字
    # 全部找得到，但還是取得0家分點」——追查後直接web_fetch了2330的真實
    # 現況頁面比對，發現分點表格本身的欄位結構(券商名稱/買張/賣張/賣超/均價
    # 這一組)其實還在、還是對的，問題出在HiStock頁面上可能還有其他<table>
    # （廣告、相關個股、導覽之類），只要新增或調整了其中一個表格的順序，
    # tables[0]就不再保證是分點資料那張——這比「網站真的改版分點表格本身」
    # 更常見，也更難用「內容長度/關鍵字都正常」這種粗略診斷分辨出來。
    # 改成掃描pd.read_html()回傳的「每一個」表格，挑第一個欄位結構符合
    # 預期的，不再假設一定是第一張——這樣不管HiStock在分點表格前面加了
    # 幾個新表格，都不影響解析。
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
        # 【R94新增】明確標示這是「部署環境缺套件」，不是「連線失敗」或
        # 「網站結構問題」——總指揮官實測發現本地電腦沒裝lxml時會拋這個
        # 例外，而且跟其他失敗混在一起長期造成誤判(懷疑IP被擋、懷疑網站
        # 改版，一輪一輪排查都排查錯方向)。這裡印出清楚可辨識的訊息，
        # 讓log/健康度診斷能一眼看出是這個原因，不用再靠診斷腳本一輪
        # 一輪排查。
        print(f"[券商分點] ❌缺少解析套件(lxml或html5lib)：{stock_code} {e}"
              f"——請確認requirements.txt有列出lxml，這不是網站或連線問題。")
        return None
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


# ==============================================================================
# 十、回測引擎共用資料層——R89新增（查1~查12+情報雷達自動化重構第一步）
# ------------------------------------------------------------------------------
# 【背景】總指揮官要求把查1~查12完整濾網回測自動化排程，架構要能繼續擴充
# （不是硬寫死12個），並且情報雷達（我們自己的情報匯入功能，R88已經補上
# 補登日期，解除了原本「沒有歷史時間戳無法回測」的限制）也要納入。
#
# 這批函式(fetch_pe_history/fetch_institutional_history/
# fetch_revenue_history_lagged/_lookup_lagged_revenue)原本在warroom_v160.py，
# 是回測引擎抓歷史資料的共用層，本身沒有任何Streamlit UI依賴（純資料抓取+
# 整理），適合搬進共用模組讓網頁版跟排程版共用同一份邏輯，不用各自維護。
# 這是完整重構的第一步：資料層先搬，下一步才是把_filter_backtest_one_stock
# 本身(依賴DIVIDEND_DB、K線型態辨識這些網頁版專屬的部分)也處理掉。
# ==============================================================================
def fetch_pe_history(symbol, token, years=3):
    """
    【V157新增，R89搬進共用模組】抓取 FinMind 每日本益比／股價淨值比／殖利率
    歷史序列。取代「PE×15合理、PE×20樂觀」的固定倍數——固定倍數對電子股
    （常態PE 25~35）跟傳產股（常態PE 10~15）套同一把尺，會系統性誤判。
    改用「現在的PE落在這檔股票自己歷史分布的第幾百分位」。
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


def fetch_institutional_history(stock_code, years, token):
    """
    【V159新增，R89搬進共用模組】歷史三大法人買賣超+融資融券，各一支API
    call涵蓋整個回測區間（不是一天一call）。三大法人與融資融券資料是證交所
    收盤後當天公告，用在「當天收盤產生訊號」沒有未來函數問題。
    回傳以日期為index的DataFrame，欄位：f_buy, t_buy, d_buy, margin_diff
    （單位：張）。
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
    # 【R95修復】原本return out.fillna(0.0)把f_buy/t_buy/d_buy/margin_diff全部
    # 一視同仁地把NaN(沒抓到資料)填成0.0——margin_diff因此永遠無法分辨「這天
    # 真的融資沒變化」跟「這天根本沒有融資資料」，跟即時戰卡那邊已經修過的
    # has_margin bug是同一個病根(見_filter_backtest_one_stock)。f_buy/t_buy/
    # d_buy維持補0是合理的(下游用加總邏輯，0是安全值)，只有margin_diff需要
    # 保留NaN讓呼叫端能用pd.notna()判斷「有沒有資料」。
    for _c in ('f_buy', 't_buy', 'd_buy'):
        if _c in out.columns:
            out[_c] = out[_c].fillna(0.0)
    return out


def fetch_revenue_history_lagged(stock_code, years, token, disclosure_buffer_days=10):
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
    """
    url = 'https://api.finmindtrade.com/api/v4/data'
    start_date = (datetime.now() - timedelta(days=int(365 * years) + 400)).strftime('%Y-%m-%d')
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
    except FinMindAPIError:
        return None
    except Exception:
        return None


def _lookup_lagged_revenue(rev_hist_df, signal_date_ts):
    """
    【R89搬進共用模組】用merge_asof概念手動查表：找出在signal_date當下，
    「已經公告」的最新一筆營收年增率/月增率。回傳(yoy, mom)，兩者都可能
    是None（該筆基期湊不出來時）。
    """
    if rev_hist_df is None or rev_hist_df.empty:
        return None, None
    eligible = rev_hist_df[rev_hist_df['available_date'] <= signal_date_ts]
    if eligible.empty:
        return None, None
    latest = eligible.iloc[-1]
    yoy = float(latest['yoy']) if pd.notna(latest.get('yoy')) else None
    mom = float(latest['mom']) if pd.notna(latest.get('mom')) else None
    return yoy, mom


# ==============================================================================
# 十一、查1~查14+情報雷達 回測引擎本體——R95搬進共用模組（重構第二步，接續
# R89的資料層搬移）
# ------------------------------------------------------------------------------
# 【背景】R89已經把資料層(fetch_pe_history/fetch_institutional_history/
# fetch_revenue_history_lagged/_lookup_lagged_revenue)搬過來，並在註解裡
# 明確留下「下一步才是把_filter_backtest_one_stock本身(依賴DIVIDEND_DB、
# K線型態辨識這些網頁版專屬的部分)也處理掉」——這裡就是那一步。
#
# 這批函式(get_threshold/evaluate_single_condition/evaluate_scan_conditions/
# detect_k_line_patterns_v152/fetch_twii_regime_history/
# _filter_backtest_one_stock/run_filter_backtest/summarize_filter_backtest/
# summarize_filter_backtest_walkforward)原本在warroom_v160.py。搬過來之前
# 逐一檢查過每個的Streamlit依賴，處理方式：
#   - get_threshold：唯一真正需要st的地方，用檔案開頭那個try/except過的
#     st（可能是None）搭配既有的except Exception防呆，兩邊環境都安全。
#   - DIVIDEND_DB(股利資料)、FinMind token：這兩個原本是_filter_backtest_
#     one_stock/run_filter_backtest內部直接讀的網頁版全域變數/函式，現在
#     改成外部傳入的參數(dividend_db/token)，呼叫端(網頁版或未來排程)自己
#     決定要傳什麼進來，不再寫死依賴v160.py的全域狀態。
#   - fetch_twii_regime_history原本掛@st.cache_data，這裡拿掉，改用一個
#     模組層級的簡單dict做記憶體快取(同一個process生命週期內有效，跟
#     st.cache_data的效果對這個用途來說已經足夠——這份資料同一次回測/
#     排程執行中最多用到一次，不需要跨session持久化)。
#
# 這樣一來，網頁版UI照樣呼叫這些函式(只是改成從這裡import、多傳兩個參數)，
# 而未來如果要讓GitHub Actions排程也能跑自動化回測/校準（例如定期重新驗證
# 查1~14各濾網的命中率是否還成立），system_scheduler.py現在也能直接
# import這整套邏輯，不用再複製一份。這輪只做到「搬移＋讓兩邊都能用」，
# 沒有新增排程stage去實際呼叫它——排成什麼頻率、產出結果要怎麼處理(寫
# 回Supabase？Telegram通知？)是後續要另外規劃的部分，不在這輪範圍內。
# ==============================================================================

# 【R88新增，R95搬移】門檻集中管理——側欄「🎛️門檻參數調整」面板的即時覆寫
# 透過get_threshold()讀取，沒調整過就用這裡的預設值。
# 【R95搬進共用模組，因為_filter_backtest_one_stock現在也要用到】
# 地雷觸發本益比門檻——v160.py的估價模型(build_valuation)跟這裡的回測引擎
# 用同一個數字，不要各自定義以免將來漂移不同步。
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
    margin_shrink = (c_margin < 0) if c_has_margin else True

    if "情報雷達：" in cmd:
        return cmd.split("情報雷達：")[-1].strip() in c_sources
    if "情報黃金交叉" in cmd:
        return len(c_sources) >= 2
    if "查1." in cmd:
        return bool(card.get('is_first_red') and c_vol_ratio >= get_threshold('vol_ratio_surge') and "金叉" in c_kdj)
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
    except Exception:
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
            'rev_yoy': rev_yoy, 'kdj_str': kdj_str, 'value_score': value_score_hist, 'landmine': landmine_hist,
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
                # 【R95續7修復】原本這裡是except Exception: continue——完全靜默吞掉
                # 例外，總指揮官回報「60檔股票明明價格資料都堪用，技術面回測還是
                # 0筆樣本」，但排程/網頁版log裡沒有任何線索可以查，因為
                # _filter_backtest_one_stock在價格抓取之外的部分(指標計算/濾網
                # 判斷迴圈)完全沒有try/except保護，一旦某處拋例外，會被這裡
                # 整個吞掉、连是哪一檔股票、什麼錯誤都不知道。現在至少印出來，
                # 排程端會進GitHub Actions log、網頁版會進Streamlit Cloud的log
                # 主控台——不會解決根因，但下次再發生「技術面0筆」時，終於有
                # 線索可以查，不用再靠猜的。
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
# 十二、情報雷達回測 + GitHub Actions 排程自動化——R95續，接續本輪的「情報雷達
# 回測支援」，現在讓排程版也能用同一套邏輯
# ------------------------------------------------------------------------------
# compute_forward_return跟run_intel_radar_backtest原本在warroom_v160.py，這裡
# 搬過來讓system_scheduler.py能直接呼叫，做每週自動排程回測校準。搬移時把
# run_intel_radar_backtest原本內部直接呼叫的_sb_fetch_all(v160.py專屬的
# Supabase包裝)拿掉，改成呼叫端先查好rows(list of dict)再傳進來——網頁版
# 傳SUPABASE_CONN查到的、排程版傳sb.table(...)查到的，兩邊Supabase client
# 物件介面一致(supabase-py)，只是變數名字不同，這樣不用在core.py裡重新
# 定義一套Supabase包裝。
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
