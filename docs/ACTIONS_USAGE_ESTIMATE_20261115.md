# GitHub Actions 每月用量估算（2026-11-15）

> 性質：**靜態估算**，不是計費紀錄。原因見「二、實際用量查詢結果」。
> 範圍：`.github/workflows/*.yml` 共 24 個檔案，以及 Cloudflare Worker `warroom_monitor_worker.js`（派發 `system_scheduler.yml`）。

## 一、結論

- 估計中位數約 **10,200 分鐘／月**（區間約 2,400～19,000）。
- **超過 2,000 分鐘上限**。即使最低情境（每次 run 只計 2 分鐘）也約 2,400，仍超過。
- 只有在 repo 為**公開倉**時才免費（標準 ubuntu runner 對公開倉免費）。目前無法確認可見性，見下文。
- 最大的三個耗用來源：
  1. Worker 派發的 `system_scheduler.yml`（約 5,980 分鐘，佔一半以上）
  2. `intraday_hist.yml`（每日兩次 cron＋每週一次，約 3,000 分鐘）
  3. 12 個 `backtest_*.yml` 手動回測（假設每月各跑一次，約 765 分鐘）

## 二、實際用量查詢結果

- `gh auth status` 顯示 `GH_TOKEN` 無效，無法登入。
- `gh api repos/{owner}/{repo}` 回傳 403：此 session 未啟用對本 repo 的 GitHub 存取。
- 因此無法取得 `/actions/billing` 或實際 run 時長。下列數字全部是估計。
- 也無法確認 repo 是公開或私有。結論需依此再次確認。

## 三、每個 workflow 的觸發方式與 runner

全部 24 個 workflow 都使用 `runs-on: ubuntu-latest`（倍率 1x），沒有 windows 或 macos job。

| workflow | 觸發 | timeout-minutes | 備註 |
|---|---|---|---|
| system_scheduler.yml | 原生 cron 僅保留每月 1 號一次；workflow_dispatch（Worker 派發） | 90 或 340 | Worker 每分鐘檢查 SCHEDULE 並派發 |
| intraday_hist.yml | cron 每日 `40 2`、每日 `30 18`、每週六 `30 19` UTC；workflow_dispatch | 330 | 原生 cron 仍在，與 system_scheduler 註解不一致 |
| broker_backfill.yml | 僅 workflow_dispatch | 330 | 預設預算 270 分鐘 |
| broker_flows_scheduler.yml | 僅 workflow_dispatch | 120 | 原生 cron 已移除 |
| backtest_b1b3 / cmd_rules / oldscore / posts / revenue / rules / winrate50 / winrate_tuning | 僅 workflow_dispatch | 120 | 各 120 |
| backtest_entry_final / otc | 僅 workflow_dispatch | 90 | 各 90 |
| backtest_regime | 僅 workflow_dispatch | 240 | |
| backtest_sector | 僅 workflow_dispatch | 150 | |
| cleanup_actions_runs | 僅 workflow_dispatch | 180 | 清 run 紀錄，本身很短 |
| cleanup_reports_branches | 僅 workflow_dispatch | 5 | |
| etf_holdings_sync | 僅 workflow_dispatch（每日排程已停用） | 40 | |
| ui_admin_test / ui_browser_test / ui_selftest | 僅 workflow_dispatch | 120 | 瀏覽器測試 |
| unit_tests | push 到 main（符合 .py 路徑）、pull_request、workflow_dispatch | 40 | 觸發次數依 push/PR 量而定 |
| validate_etf_sources | 僅 workflow_dispatch | 40 | |

## 四、Worker 派發次數估算

依 `warroom_monitor_worker.js` 的 `SCHEDULE` 陣列（25 筆平日項目、另有週日、週二與週五、週六項目）。
假設：平日約 21.7 個交易日／月，假日扣除未計入（會略為高估）。盤中 `intraday_kbar` 以 `intraday_mode=shadow`（程式預設，約 50 分鐘輪詢）計算，`fast` 模式約 5 分鐘。

| 群組 | 項目數 | 每日分鐘（合計） | 每月次數 | 每月分鐘 |
|---|---|---|---|---|
| 盤中 kbar（shadow 輪詢） | 1 | 50 | 21.7 | 1,085 |
| 盤中其他（gate、snap、execute、tp_snap、tail、force_exit 等） | 16 | 61 | 347 | 1,324 |
| 收盤後與夜間（bundle、signal、overnight、bt_nightly、broker_flows×2 等） | 8 | 113 | 174 | 2,452 |
| 早盤情報 premarket_brief（週日～四 UTC） | 1 | 20 | 21.7 | 434 |
| 週二、週五：financial_health、mops_financial | 2 | 30（每天 15 分鐘×2） | 17.4 | 261 |
| 週六：big_holder | 1 | 15 | 4.35 | 65 |
| 週日：filter_backtest、dealer_stats、health_report | 3 | 75（60＋10＋5） | 13.05 | 326 |
| 每月 1 號 cron（系統排程 `0 3 1 * *`） | 1 | 30 | 1 | 30 |
| **Worker 派發合計** | | | **約 600** | **約 5,977** |

若 `intraday_mode=fast`，kbar 每月約 110 分鐘，Worker 合計約 5,000。

## 五、各 workflow 估算彙總（每月）

| workflow | 每次分鐘（估計） | 每月次數（假設） | 每月分鐘（中位） | 區間 |
|---|---|---|---|---|
| system_scheduler.yml（Worker 派發＋月 cron） | 見第四節 | 約 600 | 5,977 | 1,200～9,000 |
| intraday_hist.yml（每日 2 次＋週日 1 次） | 約 45（區間 10～120） | 約 61（每日 2 次）＋4.35（週） | 2,997 | 690～7,970 |
| backtest_*（12 個，各 1 次／月，取 timeout 的 50%） | 依 timeout 估 | 12 | 765 | 380～1,530 |
| broker_backfill.yml（預算上限） | 270（區間 60～330） | 1 | 270 | 60～330 |
| ui_admin_test / ui_browser_test / ui_selftest | 30～40 | 各 1 | 100 | 50～200 |
| unit_tests.yml | 約 4 | 約 10（假設） | 40 | 20～80 |
| etf_holdings_sync / validate_etf_sources / cleanup_* / broker_flows_scheduler | 1～10 | 各約 1 | 約 63 | 30～100 |
| **合計** | | | **約 10,200** | **約 2,400～19,000** |

說明：
- 每次分鐘數依 timeout、程式階段說明與 Worker 的 grace／deadline 推估，沒有實際 run 紀錄。
- Worker 每個 run 至少含 checkout、setup-python 與 `pip install`（約 1.5 分鐘），所以 600 次 run 的下限就已接近 1,200 分鐘。
- `intraday_hist` 的每次時長取決於配額。程式註解提到「額度尚未重置」，額度用完時 run 可能很快結束，因此區間很寬。
- 手動 workflow 的次數是假設值，實際請以 GitHub 的 Actions 用量頁面為準。

## 六、比較結論

- **若為私有倉**：中位約 10,200 分鐘，超過 2,000 分鐘上限約 5 倍。最低情境仍約 2,400，超過。需要大幅降低用量（例如 kbar 改 fast、intraday_hist 減頻或改由 Worker 派發、Worker 與原生 cron 去重）。
- **若為公開倉**：標準 ubuntu runner 免費，本估算不適用。
- 可見性未確認，這是最需要先確認的前提。

## 七、發現與建議（僅供參考，未修改任何 workflow）

1. `system_scheduler.yml` 註解寫「≤2000 分鐘/月」，但 Worker 的 SCHEDULE 每月約 600 次派發，與該目標不一致。
2. `intraday_hist.yml` 仍有原生 cron（每日兩次），與其他已移除原生 cron 的 workflow 不一致，建議確認是否刻意保留。
3. `intraday_mode` 的實際值（shadow／fast）會使 Worker 用量差約 1,000 分鐘／月，應以實際設定為準。
4. 若要精確數字，需要：`gh` 重新授權，或從 GitHub 網頁 Settings → Billing 讀取上月實際分鐘數，再用 run 紀錄校正各 stage 的時長。
