# MIS_ALLOWED = False 的原因調查（2026-11-15）

範圍：`warroom_core.py` 的 `MIS_ALLOWED`（第 5350 行）。本次只釐清與建議，沒有改成 True，也沒有改備援順序。

## 結論

- `MIS_ALLOWED = False` 是政策性停用，不是臨時除錯開關，也不是遺漏。
- 原因是證交所網站使用條款第 6 條明文禁止以自動化方式下載網站資料，而 `mis.twse.com.tw` 不在 OpenAPI／政府資料開放平臺內。
- 這個決定符合 `CLAUDE.md` 開發死規則 5（2026-10-16 更正版）：條款「明確逐條禁止」者，先找合規備案，找到了就停用。備案已經存在（永豐金 Shioaji）。
- 建議維持 False。要改回 True 的前提只有「取得證交所同意」或「簽約資訊廠商」，不是改一行常數就能做的事。

## 證據

| 來源 | 內容 |
|---|---|
| `warroom_core.py` 5345–5350 行註解 | 標註「2026-10-12 死規則5／6」。以瀏覽器實際渲染 mis.twse.com.tw，頁尾「使用條款」連到 `https://www.twse.com.tw/zh/terms/use.html`，第 6 條禁止以自動化裝置、指令碼、自動程式等方式下載網站軟體或資料；MIS 也不在 OpenAPI／政府資料開放平臺。結論：停用程式自動存取，即時報價改由永豐金 Shioaji（券商 API，依帳戶契約授權，只查詢不下單）提供，含加權指數 t00。註解最後寫明「若日後取得證交所同意或簽約資訊廠商，才可改回 True」。 |
| `CLAUDE.md` 死規則 5 | 2026-10-16 老闆更正：條款只是一般性聲明、或對方提供公開 API 者視為可用；條款「明確逐條禁止自動化／爬蟲者」先找合規備案，仍無法才停用。同一條已列「證交所 ETF e添富（使用條款第 6 條）」為已知明確禁止，與 MIS 引用的是同一份條款。 |
| `test_mis_terms_switch.py` | 把停用寫成測試：預設 False、關閉時完全不發 HTTP 請求、改由 Shioaji 補上（含 t00 漲跌點數）、盤後不打永豐金。同一檔也涵蓋 `T86_ALLOWED=False`、`BROKER_SCRAPE_ALLOWED=False`（HiStock）。 |
| `system_scheduler.py` `stage_diag_live_quotes` | 2026-10-12 加的診斷階段，專門實測「MIS 停用、永豐金取代」整條路徑，報告內含 `mis_allowed`。 |
| `warroom_core.py` 5546 行起的 R98續R7 註解（斷路器） | 停用之前，MIS 在 production 的盤中成功率已只有 7.5%（27/358，跨 4 個交易日），Shioaji 備援實際扛下全部流量，所以 MIS 的嘗試與逾時本來就是浪費時間。 |

git 歷史的限制：這份 repo 的歷史只有 88 個 commit，最早的是 2026-10-21 的快照（`5d25aa1`，310 個檔案一次加入）。`git log -S MIS_ALLOWED` 與 `git blame` 都只會指到這個快照，查不到 10 月 12 日當天的原始變更與討論。上表的日期與理由來自程式註解、`CLAUDE.md` 與測試，不是來自 commit 訊息。

## 目前停用後的程式路徑（檢查結果）

- `fetch_twse_mis_batch`：`MIS_ALLOWED=False` 時在最前面返回，不發請求；`return_diagnostics=True` 時所有代號列入 `truly_missing_syms`，並帶 `mis_disabled_terms=True`。
- `fetch_live_quotes_resilient`：`MIS_ALLOWED=False` 時把 `_mis_circuit_open` 強制設為 True，所有代號視為「MIS 沒查到」，直接進永豐金補查；之後的「MIS 最後補漏重試」被 `and MIS_ALLOWED` 擋掉。盤後（`is_twse_trading_session_now()` 為 False）跳過永豐金，沿用呼叫端快取。
- 專案內只有 `system_scheduler.py` 12179 行（診斷）直接呼叫 `fetch_twse_mis_batch`，其餘都走 `fetch_live_quotes_resilient`，所以停用是全面生效的。
- 斷路器狀態更新區塊有 `not _mis_circuit_open` 條件，停用期間不會誤累計失敗次數。

## 備援順序檢查

目前順序是：永豐金（盤中）→ 呼叫端快取（盤後或查不到）。MIS 重試被擋掉。我沒有找到明顯錯誤，所以沒有修改。逐項確認：

1. 停用後不會有「先等 MIS 逾時、再打永豐金」的浪費：MIS 路徑在發請求前就結束。
2. 加權指數 t00 由 `_shioaji_taiex_contract` 處理（新版 `IX0001` 優先、舊版 `Indexs.TSE['001']` 備援），`test_mis_terms_switch.py` 已覆蓋。
3. 沒有設定 Shioaji 金鑰時，函式回傳空結果與診斷，呼叫端走快取，不會拋例外。

## 建議

1. 維持 `MIS_ALLOWED = False`，不要為了「報價看起來少」而改回 True。真要恢復，需要書面同意或簽約資訊廠商，並在 `CLAUDE.md`／本文件記錄來源。
2. 停用期間 MIS 專屬的程式碼（斷路器、反向交易所重試、拆批重試）在 production 是死碼，可以留著當開關後的備用，但不要再在上面投入維護。若要精簡，另開一輪，並保留 `test_mis_terms_switch.py`。
3. 所有盤中報價現在集中在永豐金一個來源。`fetch_shioaji_snapshot` 用 `api.snapshots()`，官方文件把高頻輪詢 snapshots 列為最常見的誤用。目前 Streamlit 端有連線快取（`_get_cached_shioaji_connection`），排程端每次完整 login→snapshot→logout；實際呼叫頻率我這次沒有統計，需要持續觀察 perf_log 的 `live_quotes` 與 Shioaji 回應，避免帳戶被限流。若日後要加大頻率，應改成訂閱式（`subscribe`）而不是增加輪詢。
4. 單一來源沒有第二備援。可評估的合規候選是證交所 OpenAPI／櫃買 OpenAPI 的收盤後資料（只能補收盤價，不是即時），不能取代盤中報價。
5. 這份結論依據的是程式內的紀錄。條款原文我這次沒有重新連網確認；若要核對，依死規則 6 用瀏覽器渲染 `https://www.twse.com.tw/zh/terms/use.html` 第 6 條。
