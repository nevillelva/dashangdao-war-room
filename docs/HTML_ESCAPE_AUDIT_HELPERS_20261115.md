# HTML 跳脫審計：dashangdao_helpers.py 的 `_fmt_*`、`_note_cell`、`_price_cell`（2026-11-15）

規則：凡是外部或資料庫來源的字串進入 `unsafe_allow_html=True` 的 HTML，用 `safe_html.esc` 只包「變數值」；程式寫死的標籤、樣式、emoji 不跳脫（否則版面會壞）。純數字經 `:.2f`／`:+d`／`int()`／`float()` 格式化者，格式化本身已排除標籤字元，不另包。

背景：戰卡 `c` 的內容有一部分來自快取（`warcard_cache`）與資料庫（`broker_style_daily.detail`、`intraday_gate.overall_label`、`mops_events` 等），不能假設都是程式當下算出來的，所以評分函式回傳的 `label`／`detail` 也一併視為不可信。

## 判定表

「已處理」欄：本次修改＝這次加了 `esc`；原本已處理＝審計前就有跳脫；無需＝無外部變數。

### dashangdao_helpers.py

| 函式 | 進入 HTML 的變數 | 來源 | 判定 | 已處理 |
|---|---|---|---|---|
| `_fmt_closing_strength` | `label`、`pct`、`detail` | 戰卡快取／評分函式 | 需跳脫 | 本次修改 |
| `_fmt_volume_followthrough` | `label`、`ratio_pct`、`detail` | 同上 | 需跳脫 | 本次修改 |
| `_fmt_pullback_health` | `label`、`price_pct`、`detail` | 同上 | 需跳脫 | 本次修改 |
| `_fmt_rebound_health` | `label`、`vol_ratio_pct`、`detail` | 同上 | 需跳脫 | 本次修改 |
| `_fmt_trend_regime_tag` | 只用 `regime` 查寫死表 | 外部字串僅作 key，不輸出；未知值回空字串 | 無需 | 無需（測試鎖定） |
| `_fmt_order_book_pressure` | `label`、`depth_ratio`、`detail` | 戰卡（含即時報價衍生） | 需跳脫 | 本次修改 |
| `_fmt_today_liquidity` | `label`、`pct_of_avg`、`detail` | 同上 | 需跳脫 | 本次修改 |
| `_fmt_day_trader_and_margin` | `dtr.label`、`dtr.ratio_pct`、`mgr.label` | 戰卡 | 需跳脫 | 本次修改 |
| `_fmt_official_open_data` | 警示文字 `txt`、資料日期 | 證交所／櫃買 OpenAPI 經 `official_open_data` | 需跳脫 | 原本已處理（`esc(txt)`、`esc(m.get("date"))`）；數值走 `:.1f`／`int()` |
| `_fmt_vwap_position` | `label`、`vwap`；`deviation_pct` 走 `:+.2f` | 戰卡 | 需跳脫 | 本次修改（`label`、`vwap`） |
| `_fmt_daytrade_verdict_banner` | `label`、`veto_reason`、`detail`；`score` 走 `:+d` | 戰卡 | 需跳脫 | 本次修改 |
| `_fmt_events_block` | `flags`、`date`、`time`、`category`、`subject` | `mops_events`（公開資訊觀測站重大訊息） | 需跳脫 | 原本已用 `html.escape`；本次改成 `esc`（行為相同，且 None 不再拋例外） |
| `_fmt_broker_style_block` | `x["b"]`（分點名） | `broker_style_daily.detail` | 需跳脫 | 原本已處理 |
| 同上 | `x["why"]`、`x["k"]`、`detail.window`、`detail.cnt.*`、`log_date`（含近 N 日趨勢的 `h["log_date"]`） | 同一份 JSON／資料庫列 | 需跳脫 | 本次修改 |
| 同上 | `emoji`、`vtext`、顏色表 | `broker_style.VERDICT_LABELS` 寫死常數 | 無需 | 無需 |
| 同上 | `flip_pct`／`build_pct`／`foreign_pct`／`*_buy`／`*_sell` | 經 `float()`／`int()` 轉型 | 無需 | 無需（非數字會拋錯而不會輸出標籤） |
| `_fmt_main_force_cost` | `val`（均價）、`heavy_days` | 戰卡計算結果 | 需跳脫（防舊快取） | 本次修改；`dev` 走 `:+.1f` |
| `_fmt_vwap` | `v['side']`、`v['days']`、`label`；`lots`、`vwap` 走格式化 | `calc_inst_streak_vwap` 結果、呼叫端字面值 | 需跳脫 | 本次修改 |
| `_fmt_daytrade_summary` | `intraday_gate.overall_label`；`ob`/`vp`/`rh`/`liq` 的 `label`、`depth_ratio` | `intraday_gate` 來自 Supabase；其餘戰卡 | 需跳脫 | 本次修改 |
| `_format_live_date_human` | 回傳日期字串 | 只進 DataFrame 的「即時日期」欄，走 `st.dataframe`，不是 HTML | 不在 HTML 路徑 | 無需 |

### dashangdao.py（`_note_cell`／`_price_cell` 是 `dashangdao.py` 裡的巢狀函式，不在 helpers）

| 函式 | 進入 HTML 的變數 | 來源 | 判定 | 已處理 |
|---|---|---|---|---|
| `_price_cell` | `symbol`、`_t1`、`rule`、`detail`、AI 推演各欄、`funnel`、`brief`、價位文字 | 排程寫入的主戰清單／尾盤確認／AI 推演（含 LLM 輸出） | 需跳脫 | 原本已處理：全部經 `_wc._esc(...)`；顏色來自寫死字典 |
| `_note_cell` | `tv_rank`（成交值排名）、`up_days`（連漲天數） | 排程盤前算好的清單 JSON | 需跳脫（防舊清單／手動寫入） | 本次修改（`esc(_r)`、`esc(_u)`）；`flip_pct` 走 `:.0f`；其餘文字寫死 |

## 本次未處理（範圍外，另列）

- `warroom_core._fmt_zone_summary(badge, color, reason)`：三個戰區小結論，`badge`／`reason` 由 `score_zone1/2/3_*` 程式組字串，`color` 寫死。不在 helpers 內，這次沒動；若要一致，可在同函式內對 `badge`、`reason` 加 `esc`。要先確認 `reason` 是否刻意含 `<b>` 之類標籤，不能直接全包。
- `render_stock_card_ui` 的其他區塊（股票名稱、K 線標籤、新聞摘要等）不在這份審計範圍；`safe_html` 在 `dashangdao.py` 已有其他使用點。

## 驗證

`test_html_escape_helpers.py`：每個函式餵入 `<script>alert(1)</script>`、`<img src=x onerror=alert(2)>`，確認輸出沒有原始標籤、payload 以跳脫形式保留、寫死的版面標籤（`<div style=…>`、`<strong>`、橫幅樣式、分點長條圖）仍在。`_note_cell` 以 AST 從 `dashangdao.py` 取出原始碼單獨執行。審計前版本（HEAD）跑同一份測試有 35 項失敗，修改後全數通過。
